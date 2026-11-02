import json
import tempfile
import unittest
from pathlib import Path

from portable_ai.storage import MemoryStore, Audit
from portable_ai.workspace import Workspace
from portable_ai.tools import ToolRouter
from portable_ai.providers import extract_openai_text


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        (self.root/'workspace').mkdir()
        self.ws=Workspace(self.root/'workspace', self.root)
        self.memory=MemoryStore(self.root)
        self.audit=Audit(self.root)
        self.router=ToolRouter(self.root,self.ws,self.memory,self.audit)

    def tearDown(self):
        self.temp.cleanup()

    def test_workspace_relative_path(self):
        self.assertEqual(self.ws.resolve('src/test.py'),self.ws.root/'src'/'test.py')
        for path in ['../outside.txt', '/etc/passwd', 'C:/Windows/win.ini', '.env', 'src/secrets.json']:
            with self.subTest(path=path),self.assertRaises((ValueError,PermissionError)):
                self.ws.resolve(path)

    def test_reject_symlink_escape(self):
        outside=self.root/'outside'
        outside.mkdir()
        link=self.ws.root/'bad'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError,NotImplementedError):
            self.skipTest('symlinks unavailable')
        with self.assertRaises(PermissionError):
            self.ws.resolve('bad/pwn.txt')

    def test_write_backup_and_read(self):
        x=self.router.execute('write_file',{'path':'notes/a.txt','content':'test1'},lambda name,args:True)
        self.assertEqual(x['path'],'notes/a.txt')
        self.assertIsNone(x['backup'])
        x2=self.router.execute('write_file',{'path':'notes/a.txt','content':'test2'},lambda name,args:True)
        self.assertEqual(self.ws.read('notes/a.txt'),'test2')
        self.assertEqual(Path(x2['backup']).read_text(),'test1')

    def test_write_requires_approval(self):
        denied=self.router.execute('write_file',{'path':'deny.txt','content':'hello'},lambda name,args:False)
        self.assertIn('error',denied)
        self.assertFalse((self.ws.root/'deny.txt').exists())

    def test_memory_requires_approval(self):
        deny=self.router.execute('memory_save',{'category':'project','content':'USB agent'},lambda n,a:False)
        self.assertIn('error',deny)
        allow=self.router.execute('memory_save',{'category':'project','content':'USB agent'},lambda n,a:True)
        self.assertGreater(allow['id'],0)
        results=self.memory.search('USB')
        self.assertEqual(results[0]['content'],'USB agent')

    def test_unknown_tool_not_executed(self):
        self.assertIn('error',self.router.execute('run_shell',{'command':'rm -rf /'},lambda *_:True))

    def test_proxy_does_not_modify_system(self):
        result=self.router.execute('set_local_proxy',{'port':7890},lambda *_:True)
        self.assertTrue(result['ok'])
        self.assertEqual(self.router.current_proxy(),'http://127.0.0.1:7890')

    def test_plugin_requires_explicit_trust(self):
        plugin_dir=self.root/'plugins'/'demo'
        plugin_dir.mkdir(parents=True)
        (plugin_dir/'plugin.py').write_text('''from portable_ai.tools import ToolSpec, schema\ndef register():\n    return [ToolSpec("plugin_demo","A small demo",schema({}),lambda:123)]\n''')
        (plugin_dir/'manifest.json').write_text(json.dumps({'id':'demo','name':'demo','version':'1','description':'test','tools':{'plugin_demo':'read'},'dependencies':[]}))
        self.router.load_trusted_plugins()
        self.assertNotIn('plugin_demo',self.router.specs)
        self.router.trust_plugin('demo')
        self.router.load_trusted_plugins()
        self.assertEqual(self.router.execute('plugin_demo',{},lambda *_:True),123)
        (plugin_dir/'plugin.py').write_text((plugin_dir/'plugin.py').read_text()+'\n#changed')
        fresh=ToolRouter(self.root,self.ws,self.memory,self.audit)
        self.assertNotIn('plugin_demo',fresh.specs)

    def test_openai_output_text(self):
        self.assertEqual(extract_openai_text([{'type':'message','content':[{'type':'output_text','text':'done'}]}]),'done')


if __name__=='__main__':
    unittest.main()
