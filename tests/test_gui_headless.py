"""Controller regressions using mocks: not a native GUI/Windows acceptance test."""
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import Application


class GuiControllerTests(unittest.TestCase):
    def app(self):
        a=Application.__new__(Application)
        a.busy=False
        a.syncing=False
        a._closed=False
        a.sync=SimpleNamespace(bound=False,initial_reviewed=False)
        a.notifications=queue.Queue()
        a.window=Mock()
        a._print=Mock()
        a.agent=Mock()
        a.provider=Mock()
        a._selected_provider='DeepSeek'
        return a

    @patch('app.messagebox.showwarning')
    def test_unbound_send_never_reads_prompt_or_starts_agent(self,warn):
        a=self.app()
        a._send()
        warn.assert_called_once()
        a.agent.run.assert_not_called()

    @patch('app.messagebox.showwarning')
    def test_unreviewed_send_denied(self,warn):
        a=self.app(); a.sync.bound=True
        a._send(); warn.assert_called_once()
        a.agent.run.assert_not_called()

    def test_approval_worker_uses_queue_not_tk(self):
        a=self.app(); answers=[]
        worker=threading.Thread(target=lambda:answers.append(a._approve('write_file',{})),daemon=True)
        worker.start()
        kind,body=a.notifications.get(timeout=2)
        self.assertEqual(kind,'approval')
        a.window.after.assert_not_called()
        _,event,result=body
        result['approved']=False; event.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive()); self.assertEqual(answers,[False])

    def test_broken_approval_dialog_releases_worker_and_keeps_polling(self):
        a=self.app(); event=threading.Event(); result={'approved':True}
        a.notifications.put(('approval',(Mock(side_effect=RuntimeError('failed')),event,result)))
        a._poll()
        self.assertTrue(event.is_set()); self.assertFalse(result['approved'])
        a.window.after.assert_called_once_with(100,a._poll)

    def test_closed_application_denies_queued_approval(self):
        a=self.app(); a._closed=True
        event=threading.Event(); dialog=Mock()
        a.notifications.put(('approval',(dialog,event,{'approved':False})))
        a._poll(); self.assertTrue(event.is_set())
        dialog.assert_not_called(); a.window.after.assert_not_called()

    def test_event_handler_failure_does_not_stop_poll(self):
        a=self.app(); a.status_text=Mock()
        a.status_text.set.side_effect=OSError('drive disconnected')
        a.notifications.put(('status','working'))
        a._poll(); a.window.after.assert_called_once_with(100,a._poll)

    @patch('app.messagebox.showinfo')
    def test_clear_proxy_plugins_blocked_while_busy(self,info):
        a=self.app(); a.busy=True
        a._clear_chat(); a._configure_proxy(); a._plugins()
        self.assertEqual(info.call_count,3); a.agent.clear.assert_not_called()

    def test_provider_change_blocked_during_task(self):
        a=self.app(); a.busy=True
        a._provider_changed(); a.agent.clear.assert_not_called()
        a.provider.set.assert_called_once_with('DeepSeek')

    def test_audit_view_is_read_only(self):
        import tempfile
        from pathlib import Path
        a=self.app()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'actions.jsonl'; p.write_text('{"operation":"write_file"}\n',encoding='utf-8')
            a.audit=SimpleNamespace(file=p)
            a._audit_log(); self.assertIn('write_file',a._print.call_args.args[1])
            self.assertEqual(p.read_text(),'{"operation":"write_file"}\n')

    @patch('app.messagebox.askyesno',return_value=False)
    @patch('app.simpledialog.askstring')
    def test_file_rollback_cancel_does_not_mutate(self,ask,confirm):
        import tempfile
        from pathlib import Path
        from portable_ai.workspace import Workspace
        a=self.app(); a.sync.bound=True; a.audit=Mock()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); work=root/'work'; work.mkdir()
            a.workspace=Workspace(work,root)
            receipt=a.workspace.write('test.txt','new')
            ask.return_value=receipt['transaction']
            a._rollback_file()
            self.assertEqual((work/'test.txt').read_text(),'new')
            a.audit.record.assert_called_once_with('file_rollback','denied')

    @patch('app.messagebox.askyesno',return_value=True)
    @patch('app.simpledialog.askstring')
    def test_file_rollback_approved_disables_auto_and_restores(self,ask,confirm):
        import tempfile
        from pathlib import Path
        from portable_ai.workspace import Workspace
        a=self.app(); a.sync.bound=True; a.sync.set_auto=Mock(); a.audit=Mock()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); work=root/'work'; work.mkdir()
            (work/'test.txt').write_text('old')
            a.workspace=Workspace(work,root)
            receipt=a.workspace.write('test.txt','new')
            ask.return_value=receipt['transaction']
            a._rollback_file()
            self.assertEqual((work/'test.txt').read_text(),'old')
            a.sync.set_auto.assert_called_once_with(False)

    @patch('app.messagebox.askyesno',return_value=True)
    @patch('app.simpledialog.askstring',return_value='updated')
    @patch('app.simpledialog.askinteger',return_value=1)
    def test_manual_memory_update_preserves_version(self,*mocks):
        import tempfile
        from pathlib import Path
        from portable_ai.storage import MemoryStore
        a=self.app(); a.audit=Mock()
        with tempfile.TemporaryDirectory() as d:
            a.memory=MemoryStore(Path(d)); a.memory.add('fact','original')
            a._edit_memory()
            self.assertEqual(a.memory.search('')[0]['version'],2)
            self.assertEqual(a.memory.versions(1)[0]['content'],'original')

    @patch('app.messagebox.askyesno',return_value=False)
    def test_cloud_transmission_declined_preserves_prompt(self,confirm):
        a=self.app(); a.sync.bound=True; a.sync.initial_reviewed=True
        a.prompt=Mock(); a.prompt.get.return_value='task'
        a.key=Mock(); a.key.get.return_value='ephemeral-key'
        a.model=Mock(); a.model.get.return_value='deepseek-flash'
        a.provider.get.return_value='DeepSeek'; a._cloud_consent=set()
        a._send()
        confirm.assert_called_once()
        a.prompt.delete.assert_not_called(); a.agent.run.assert_not_called()
        self.assertFalse(a.busy); self.assertEqual(a._cloud_consent,set())

    def test_build_script_never_installs_dependencies(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        script=(root/'Build-Windows.cmd').read_text()
        self.assertNotIn('pip install',script)
        self.assertIn('-m unittest discover',script)
        self.assertIn('--onefile --windowed',script)
        setup=(root/'Setup-Windows-Build.cmd').read_text()
        self.assertLess(setup.index('choice /C YN'), setup.index('pip install'))


if __name__=='__main__':
    unittest.main()
