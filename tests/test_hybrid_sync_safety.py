"""Adversarial offline sync tests; deliberately inject user edits and interruptions."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portable_ai.hybrid_sync import HybridSync, HostLock, digest, eligible


class SyncSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.usb = self.root / 'usb'
        self.work = self.usb / 'workspace'
        self.work.mkdir(parents=True)
        self.host = self.root / 'host'
        self.sync = HybridSync(self.usb, self.root / 'state', reserve_bytes=0)
        self.sync.bind(self.host)

    def seed(self):
        (self.work / 'a.txt').write_text('base')
        self.assertTrue(self.sync.apply()['ok'])

    def before_target(self, action):
        count = 0
        def hook(stage):
            nonlocal count
            if stage == 'before_replace':
                count += 1
                if count == 2:
                    action()
        self.sync.failpoint = hook

    def test_edit_during_copy_is_not_overwritten(self):
        self.seed()
        (self.work / 'a.txt').write_text('new')
        self.before_target(lambda: (self.host / 'a.txt').write_text('user edit'))
        result = self.sync.apply()
        self.assertFalse(result['ok'])
        self.assertEqual((self.host / 'a.txt').read_text(), 'user edit')
        self.assertTrue(self.sync.recover()['unresolved'])

    def test_new_target_appearing_during_copy_is_not_overwritten(self):
        (self.work / 'a.txt').write_text('new')
        def hook(stage):
            if stage == 'before_replace':
                (self.host / 'a.txt').write_text('user created')
        self.sync.failpoint = hook
        self.assertFalse(self.sync.apply()['ok'])
        self.assertEqual((self.host / 'a.txt').read_text(), 'user created')

    def test_edit_during_rollback_is_not_overwritten(self):
        self.seed()
        (self.work / 'a.txt').write_text('new')
        tx = self.sync.apply()['transaction']
        self.before_target(lambda: (self.host / 'a.txt').write_text('user edit'))
        self.assertTrue(self.sync.rollback(tx)['rejected'])
        self.assertEqual((self.host / 'a.txt').read_text(), 'user edit')

    def test_edit_during_delete_snapshot_is_not_deleted(self):
        self.seed()
        (self.work / 'a.txt').unlink()
        def hook(stage):
            if stage == 'after_replace':
                (self.host / 'a.txt').write_text('user edit')
        self.sync.failpoint = hook
        self.assertFalse(self.sync.apply(allow_deletions=True)['ok'])
        self.assertEqual((self.host / 'a.txt').read_text(), 'user edit')

    def test_pending_transaction_requires_recovery_before_new_apply(self):
        (self.work / 'a.txt').write_text('new')
        def fail(stage):
            if stage == 'after_replace':
                raise RuntimeError('power loss')
        self.sync.failpoint = fail
        self.assertFalse(self.sync.apply()['ok'])
        self.sync.failpoint = None
        with self.assertRaises(RuntimeError):
            self.sync.apply()
        self.assertEqual(self.sync.recover()['recovered'], ['a.txt'])
        self.assertTrue(self.sync.apply()['ok'])

    def test_root_replaced_by_symlink_is_denied(self):
        self.host.rmdir()
        outside = self.root / 'outside'
        outside.mkdir()
        self.host.symlink_to(outside, target_is_directory=True)
        (self.work / 'a.txt').write_text('new')
        with self.assertRaises(PermissionError):
            self.sync.plan()
        self.assertFalse((outside / 'a.txt').exists())

    def test_root_parent_replaced_by_symlink_is_denied(self):
        other = self.root / 'other'
        self.usb.rename(other)
        self.usb.symlink_to(other, target_is_directory=True)
        with self.assertRaises(PermissionError):
            self.sync.plan()

    def test_scan_error_never_becomes_deletion(self):
        self.seed()
        import os
        original_walk = os.walk
        def bad_walk(root, **kwargs):
            if root == self.work:
                if kwargs.get('onerror'):
                    kwargs['onerror'](PermissionError('cannot scan'))
                return iter(())
            return original_walk(root, **kwargs)
        with patch('portable_ai.hybrid_sync.os.walk', side_effect=bad_walk):
            with self.assertRaises(PermissionError):
                self.sync.apply(allow_deletions=True)
        self.assertEqual((self.host / 'a.txt').read_text(), 'base')

    def test_same_host_concurrent_engine_lock(self):
        with HostLock(self.sync.lock_path):
            with self.assertRaises(RuntimeError):
                self.sync.apply()

    def test_delete_pending_retains_common_baseline(self):
        self.seed()
        base = self.sync._read_base()
        (self.work / 'a.txt').unlink()
        for _ in range(2):
            self.assertEqual(self.sync.apply()['deletions_waiting'], 1)
            self.assertEqual(self.sync._read_base(), base)
        (self.host / 'a.txt').write_text('post deletion edit')
        self.assertEqual(self.sync.plan().counts()['conflict'], 1)

    def test_conflict_does_not_advance_baseline(self):
        self.seed()
        base = self.sync._read_base()
        (self.work / 'a.txt').write_text('usb edit')
        (self.host / 'a.txt').write_text('host edit')
        self.assertFalse(self.sync.apply()['ok'])
        self.assertEqual(self.sync._read_base(), base)

    def test_credentials_are_excluded_on_both_sides(self):
        for rel in ('.env.local', '.aws/credentials', '.ssh/id_rsa', 'a/private.pem',
                    'secrets.json', '.kube/config', '.git/config', '.npmrc', '.aws'):
            self.assertFalse(eligible(rel), rel)
        (self.host / '.env').write_text('SECRET')
        (self.work / '.npmrc').write_text('TOKEN')
        self.assertTrue(self.sync.apply()['ok'])
        self.assertFalse((self.work / '.env').exists())
        self.assertFalse((self.host / '.npmrc').exists())

    def test_directory_in_place_of_deleted_target_not_absence(self):
        self.seed()
        (self.work / 'a.txt').unlink()
        result = self.sync.apply(allow_deletions=True)
        (self.host / 'a.txt').mkdir()
        undo = self.sync.rollback(result['transaction'])
        self.assertTrue(undo['rejected'])
        self.assertTrue((self.host / 'a.txt').is_dir())

    def test_malformed_baseline_hash_is_rejected(self):
        self.seed()
        data = json.loads(self.sync.state_path.read_text())
        data['base']['a.txt'] = 'z' * 64
        self.sync.state_path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.sync.plan()

    def test_snapshot_corruption_prevents_rollback(self):
        self.seed()
        (self.work / 'a.txt').write_text('new')
        result = self.sync.apply()
        receipt = self.sync.latest_transaction()
        (self.sync.state_dir / receipt['ops'][0]['snapshot']).write_text('corrupt')
        self.assertTrue(self.sync.rollback(result['transaction'])['rejected'])
        self.assertEqual((self.host / 'a.txt').read_text(), 'new')

    def test_transaction_id_path_traversal_rejected(self):
        for tx in ('../binding', '../../outside', 'A' * 32, 'x' * 32):
            with self.assertRaises(ValueError):
                self.sync.rollback(tx)

    def test_rollback_interruption_after_replace_is_resumable(self):
        self.seed()
        (self.work / 'a.txt').write_text('new')
        tx = self.sync.apply()['transaction']
        count = 0
        def fail(stage):
            nonlocal count
            if stage == 'after_replace':
                count += 1
                if count == 2:
                    raise RuntimeError('power loss after rollback replace')
        self.sync.failpoint = fail
        self.assertTrue(self.sync.rollback(tx)['rejected'])
        self.assertEqual((self.host / 'a.txt').read_text(), 'base')
        self.assertFalse(self.sync.auto_enabled)
        self.sync.failpoint = None
        self.assertEqual(self.sync.rollback(tx)['undone'], ['a.txt'])

    def test_rollback_interruption_before_replace_is_resumable(self):
        self.seed()
        (self.work / 'a.txt').write_text('new')
        tx = self.sync.apply()['transaction']
        def fail():
            raise RuntimeError('power loss before rollback replace')
        self.before_target(fail)
        self.assertTrue(self.sync.rollback(tx)['rejected'])
        self.assertEqual((self.host / 'a.txt').read_text(), 'new')
        self.sync.failpoint = None
        self.assertEqual(self.sync.rollback(tx)['undone'], ['a.txt'])
        self.assertEqual((self.host / 'a.txt').read_text(), 'base')

    def test_control_file_symlink_is_rejected(self):
        outside = self.root / 'outside.json'
        outside.write_text('{}')
        self.sync.state_path.symlink_to(outside)
        with self.assertRaises(PermissionError):
            self.sync.plan()
        self.assertEqual(outside.read_text(), '{}')
