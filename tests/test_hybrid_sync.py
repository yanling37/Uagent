"""Offline sync acceptance tests; no cloud, USB, registry, or API side effects."""
from __future__ import annotations
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from portable_ai.hybrid_sync import HybridSync, eligible, valid_rel


class HybridSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.b = Path(self.tmp.name)
        self.usb = self.b / 'usb'
        (self.usb / 'workspace').mkdir(parents=True)
        self.local = self.b / 'local'
        self.state = self.b / 'state'
        self.sync = HybridSync(self.usb, self.state, reserve_bytes=0)
        self.sync.bind(self.local)

    def tearDown(self):
        self.tmp.cleanup()

    def write_usb(self, rel, content):
        p = self.usb / 'workspace' / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding='utf-8')
        return p

    def write_host(self, rel, content):
        p = self.local / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding='utf-8')
        return p

    def test_default_mode_and_initial_copy(self):
        self.write_usb('src/main.py', 'print(1)')
        self.write_usb('node_modules/a.js', 'cache')
        self.write_usb('.env', 'API_KEY=should_not_copy')
        self.write_usb('build/result.obj', 'temp')
        plan = self.sync.plan()
        self.assertEqual(plan.counts()['copy'], 1)
        self.assertTrue(plan.auto_safe())
        result = self.sync.apply(plan)
        self.assertTrue(result['ok'], result)
        self.assertEqual((self.local/'src/main.py').read_text(), 'print(1)')
        self.assertFalse((self.local/'.env').exists())
        self.assertFalse((self.local/'node_modules').exists())
        self.assertFalse((self.local/'build').exists())
        cfg=json.loads((self.usb/'config/hybrid_sync.json').read_text())
        self.assertEqual(cfg['mode'], 'smart_hybrid')
        self.assertTrue(self.sync.auto_enabled)
        self.assertEqual(self.sync.plan().counts()['copy'], 0)

    def test_bidirectional_updates_and_new_file(self):
        self.write_usb('src/a.txt', 'v1')
        self.sync.apply(self.sync.plan())
        self.write_host('src/a.txt', 'host-v2')
        self.write_host('src/extra.txt','new host file')
        plan = self.sync.plan()
        self.assertEqual(plan.counts()['copy'],2)
        self.assertTrue(all(x.direction=='host_to_usb' for x in plan.items))
        self.assertTrue(self.sync.apply(plan)['ok'])
        self.assertEqual((self.usb/'workspace/src/a.txt').read_text(), 'host-v2')
        self.write_usb('src/a.txt', 'usb-v3')
        self.assertEqual(self.sync.plan().items[0].direction,'usb_to_host')
        self.sync.apply(self.sync.plan())
        self.assertEqual((self.local/'src/a.txt').read_text(), 'usb-v3')

    def test_both_sides_changed_kept_and_auto_stopped(self):
        self.write_usb('file.txt','base')
        self.sync.apply(self.sync.plan())
        self.write_usb('file.txt','usb variant')
        self.write_host('file.txt','host variant')
        plan=self.sync.plan()
        self.assertEqual(plan.counts()['conflict'],1)
        self.assertFalse(plan.auto_safe())
        self.assertFalse(self.sync.apply(plan)['ok'])
        self.assertEqual((self.local/'file.txt').read_text(),'host variant')
        self.assertEqual((self.usb/'workspace/file.txt').read_text(),'usb variant')

    def test_no_partial_copy_when_conflict_exists(self):
        self.write_usb('a.txt','1')
        self.sync.apply(self.sync.plan())
        self.write_usb('a.txt','2')
        self.write_host('a.txt','3')
        self.write_usb('b.txt','new')
        result=self.sync.apply(self.sync.plan())
        self.assertFalse(result['ok'])
        self.assertFalse((self.local/'b.txt').exists())

    def test_deletes_never_automatic_but_explicit_supported_and_undoable(self):
        self.write_usb('old.txt','original')
        self.sync.apply(self.sync.plan())
        (self.local/'old.txt').unlink()
        plan=self.sync.plan()
        self.assertEqual(plan.counts()['delete'],1)
        self.assertFalse(plan.auto_safe())
        skipped=self.sync.apply(plan)
        self.assertTrue(skipped['ok'])
        self.assertEqual(skipped['deletions_waiting'],1)
        self.assertTrue((self.usb/'workspace/old.txt').is_file())
        erased=self.sync.apply(self.sync.plan(),allow_deletions=True)
        self.assertEqual(erased['deleted'],1)
        self.assertFalse((self.usb/'workspace/old.txt').exists())
        undo=self.sync.rollback(erased['transaction'])
        self.assertEqual(undo['undone'],['old.txt'])
        self.assertEqual((self.usb/'workspace/old.txt').read_text(),'original')
        self.assertFalse(self.sync.auto_enabled)

    def test_rollback_only_unchanged_target(self):
        self.write_usb('main.py','one')
        self.sync.apply(self.sync.plan())
        self.write_usb('main.py','two')
        done=self.sync.apply(self.sync.plan())
        self.assertEqual((self.local/'main.py').read_text(),'two')
        undo=self.sync.rollback(done['transaction'])
        self.assertEqual(undo['undone'],['main.py'])
        self.assertEqual((self.local/'main.py').read_text(),'one')
        self.assertFalse(self.sync.auto_enabled)
        # A separate transaction can never erase later edits.
        self.sync.set_auto(True)
        self.sync.apply(self.sync.plan())  # host changed -> copies back to USB
        next_tx=self.sync.latest_transaction()
        self.write_usb('main.py','edited after sync')
        undo2=self.sync.rollback(next_tx['id'])
        self.assertTrue(undo2['rejected'])
        self.assertEqual((self.usb/'workspace/main.py').read_text(),'edited after sync')

    def test_first_import_two_different_versions_is_conflict(self):
        self.write_usb('same.txt','on USB')
        self.write_host('same.txt','on PC')
        self.assertEqual(self.sync.plan().counts()['conflict'],1)

    def test_host_disconnected_then_restart_safe_incremental(self):
        self.write_usb('a.txt','data')
        self.sync.apply(self.sync.plan())
        self.write_host('a.txt','offline edit')
        # Recreating the engine simulates application restart on the same PC.
        sync2=HybridSync(self.usb,self.state,reserve_bytes=0)
        self.assertEqual(sync2.host,self.local.resolve())
        self.assertEqual(sync2.plan().items[0].direction,'host_to_usb')
        sync2.apply(sync2.plan())
        self.assertEqual((self.usb/'workspace/a.txt').read_text(),'offline edit')

    def test_moved_usb_path_does_not_break_local_binding(self):
        self.write_usb('a.txt','keep')
        self.sync.apply(self.sync.plan())
        new_usb=self.b/'renamed_usb'
        self.usb.rename(new_usb)
        engine=HybridSync(new_usb,self.state,reserve_bytes=0)
        self.assertEqual(engine.host,self.local.resolve())
        self.assertEqual(engine.plan().counts()['copy'],0)

    def test_multiple_machines_collision(self):
        self.write_usb('shared.txt','base')
        self.sync.apply(self.sync.plan())
        machine_b=HybridSync(self.usb,self.b/'other_computer',reserve_bytes=0)
        machine_b.bind(self.b/'other_local')
        machine_b.apply(machine_b.plan())
        self.write_host('shared.txt','edit A')
        (self.b/'other_local/shared.txt').write_text('edit B')
        machine_b.apply(machine_b.plan())
        self.assertEqual(self.sync.plan().counts()['conflict'],1)
        self.assertEqual((self.local/'shared.txt').read_text(),'edit A')

    def test_reparse_escape_or_bad_filenames_never_copied(self):
        outside=self.b/'outside.txt'; outside.write_text('SECRET')
        link=self.usb/'workspace/link.txt'
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('symlink unavailable')
        plan=self.sync.plan()
        self.assertEqual(plan.counts()['blocked'],1)
        self.assertFalse(self.sync.apply(plan)['ok'])
        self.assertFalse((self.local/'link.txt').exists())
        for rel in ['../x.txt','a/../b.txt','a/C:drive.txt','CON.txt','x\\y.txt']:
            self.assertFalse(valid_rel(rel),rel)
        self.assertFalse(eligible('src/private.key'))

    def test_post_replace_interruption_recovers_receipt_and_rollback(self):
        self.write_usb('a.txt','base')
        self.sync.apply(self.sync.plan())
        self.write_usb('a.txt','update')
        # The snapshot also uses _copy_file. Fail after destination replacement.
        calls=[]
        def failpoint(stage):
            if stage == 'after_replace':
                calls.append(stage)
                if len(calls)==2:
                    raise RuntimeError('simulate sudden halt after replace')
        self.sync.failpoint=failpoint
        result=self.sync.apply(self.sync.plan())
        self.assertFalse(result['ok'])
        self.assertEqual((self.local/'a.txt').read_text(),'update')
        self.assertTrue(self.sync.unfinished())
        self.sync.failpoint=None
        recovered=self.sync.recover()
        self.assertEqual(recovered['recovered'],['a.txt'])
        self.assertEqual(self.sync.unfinished(),[])
        latest=self.sync.latest_transaction()
        self.assertEqual(latest['ops'][0]['path'],'a.txt')
        rollback=self.sync.rollback(latest['id'])
        self.assertEqual(rollback['undone'],['a.txt'])
        self.assertEqual((self.local/'a.txt').read_text(),'base')

    def test_no_overwrite_on_stale_plan(self):
        self.write_usb('test.txt','1')
        plan=self.sync.plan()
        self.write_usb('test.txt','2')
        with self.assertRaises(RuntimeError):
            self.sync.apply(plan)
        self.assertFalse((self.local/'test.txt').exists())

    def test_casefold_collision_pauses_all(self):
        self.write_usb('X.txt','A')
        self.write_usb('x.txt','B')
        self.assertEqual(self.sync.plan().counts()['blocked'],2)
        self.assertFalse(self.sync.apply(self.sync.plan())['ok'])

    def test_denied_first_sync_never_autostarts_after_reopen(self):
        self.write_usb('a.py','do not automatically import')
        self.assertFalse(self.sync.initial_reviewed)
        self.assertFalse(self.sync.auto_enabled)
        restarted=HybridSync(self.usb,self.state,reserve_bytes=0)
        self.assertFalse(restarted.auto_enabled)
        self.assertEqual(restarted.plan().counts()['copy'],1)
        self.assertFalse((self.local/'a.py').exists())

    def test_identical_preexisting_files_commit_common_base(self):
        self.write_usb('same.py','shared content')
        self.write_host('same.py','shared content')
        self.assertEqual(self.sync.plan().items, [])
        self.sync.apply(self.sync.plan())
        self.write_host('same.py','changed only locally')
        plan=self.sync.plan()
        self.assertEqual(plan.counts()['copy'],1)
        self.assertEqual(plan.items[0].direction,'host_to_usb')

    def test_cross_side_casefold_collision_blocks_copy(self):
        self.write_usb('Readme.txt','usb data')
        self.write_host('README.txt','local data')
        plan=self.sync.plan()
        self.assertEqual(plan.counts()['blocked'],2)
        self.assertFalse(self.sync.apply(plan)['ok'])
        self.assertEqual((self.local/'README.txt').read_text(),'local data')

    def test_before_replace_interruption_leaves_old_file_intact(self):
        self.write_usb('reliable.txt','old version')
        self.sync.apply(self.sync.plan())
        self.write_usb('reliable.txt','new version')
        def fail_before(stage):
            # Ignore the snapshot's replace, interrupt only the target replace.
            if stage == 'before_replace':
                fail_before.count += 1
                if fail_before.count == 2:
                    raise RuntimeError('simulated disconnect before target replacement')
        fail_before.count = 0
        self.sync.failpoint=fail_before
        result=self.sync.apply(self.sync.plan())
        self.assertFalse(result['ok'])
        self.assertEqual((self.local/'reliable.txt').read_text(),'old version')
        self.sync.failpoint=None
        self.assertEqual(self.sync.recover()['recovered'],['reliable.txt (未覆盖)'])
        self.assertEqual(self.sync.plan().counts()['copy'],1)
        self.sync.apply(self.sync.plan())
        self.assertEqual((self.local/'reliable.txt').read_text(),'new version')

    def test_storage_guard_prevents_usb_overwrite(self):
        self.write_host('host.txt','host content')
        self.sync.reserve_bytes=10**18
        result=self.sync.apply(self.sync.plan())
        self.assertFalse(result['ok'])
        self.assertFalse((self.usb/'workspace/host.txt').exists())
        self.assertEqual((self.local/'host.txt').read_text(),'host content')

    def test_file_too_large_blocks_entire_sync(self):
        self.write_usb('too-big.dat','x'*16)
        self.write_usb('okay.py','print(1)')
        self.sync.max_file_bytes=10
        plan=self.sync.plan()
        self.assertEqual(plan.counts()['blocked'],1)
        self.assertFalse(self.sync.apply(plan)['ok'])
        self.assertFalse((self.local/'okay.py').exists())

    def test_refuse_usb_root_as_host_workspace(self):
        isolated=HybridSync(self.usb,self.b/'new-state',reserve_bytes=0)
        with self.assertRaises(ValueError):
            isolated.bind(self.usb/'workspace')
        with self.assertRaises(ValueError):
            isolated.bind(self.b)  # parent of USB folder

    def test_minimal_host_write_and_auto_toggle(self):
        self.assertFalse(self.sync.auto_enabled)  # requires an approved first synchronization
        self.assertFalse(self.sync.initial_reviewed)
        self.sync.apply(self.sync.plan())
        self.assertTrue(self.sync.auto_enabled)
        self.sync.set_auto(False)
        self.assertFalse(self.sync.auto_enabled)
        self.sync.set_auto(True)
        self.assertTrue(self.sync.auto_enabled)


if __name__ == '__main__':
    unittest.main()
