import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from portable_ai.workspace import Workspace
from portable_ai.storage import MemoryStore, Audit
from portable_ai.tools import ToolRouter
from portable_ai.agent import Agent
from portable_ai.providers import HttpClient, ProviderError, NoRedirect


class HardenedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/'workspace').mkdir()
        self.ws = Workspace(self.root/'workspace', self.root)
        self.mem = MemoryStore(self.root)
        self.router = ToolRouter(self.root, self.ws, self.mem, Audit(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def test_windows_aliases_and_credentials_denied(self):
        for rel in ['a.txt:secret', 'CON.txt', 'nul', 'test.', 'test ', '.ENV.dev', '.AWS/credentials', '.ssh/id', '../a']:
            with self.subTest(rel=rel), self.assertRaises((ValueError, PermissionError)):
                self.ws.resolve(rel)

    def test_same_root_symlink_also_denied(self):
        (self.ws.root/'a').write_text('hello')
        try:
            (self.ws.root/'b').symlink_to(self.ws.root/'a')
        except OSError:
            self.skipTest('symlinks require platform privilege')
        with self.assertRaises(PermissionError):
            self.ws.read('b')

    def test_write_rollback_and_new_file_rollback(self):
        first = self.ws.write('a', 'old')
        second = self.ws.write('a', 'new')
        self.ws.rollback(second['transaction'])
        self.assertEqual(self.ws.read('a'), 'old')
        self.ws.rollback(first['transaction'])
        self.assertFalse((self.ws.root/'a').exists())

    def test_rollback_refuses_user_edits(self):
        result = self.ws.write('a', 'agent')
        (self.ws.root/'a').write_text('user')
        with self.assertRaises(RuntimeError):
            self.ws.rollback(result['transaction'])
        self.assertEqual(self.ws.read('a'), 'user')

    def test_write_approval_preview_stale(self):
        self.ws.write('a', 'original')
        def approve(*_):
            (self.ws.root/'a').write_text('user edit during prompt')
            return True
        result = self.router.execute('write_file', {'path':'a','content':'agent'}, approve)
        self.assertIn('error', result)
        self.assertEqual(self.ws.read('a'), 'user edit during prompt')

    def test_interrupted_receipt_recovery(self):
        result = self.ws.write('a', 'new')
        receipt = self.ws.transactions/(result['transaction']+'.json')
        r = json.loads(receipt.read_text()); r['status']='prepared'; receipt.write_text(json.dumps(r))
        self.assertEqual(self.ws.recover()[0]['status'], 'committed')

    def test_unresolved_file_transaction_blocks_new_write(self):
        result = self.ws.write('a', 'agent')
        receipt = self.ws.transactions/(result['transaction']+'.json')
        r = json.loads(receipt.read_text()); r['status']='prepared'; receipt.write_text(json.dumps(r))
        (self.ws.root/'a').write_text('external')
        with self.assertRaises(RuntimeError):
            self.ws.write('b', 'must not write')
        self.assertFalse((self.ws.root/'b').exists())
        self.assertEqual(self.ws.recover()[0]['status'], 'unresolved')

    def test_provider_bad_shape_reports_controlled_error(self):
        from portable_ai.providers import deepseek_completion, openai_responses
        class BadHTTP:
            def post(self, *args): return {'unexpected': 'shape'}
        with self.assertRaises(ProviderError):
            deepseek_completion(BadHTTP(), 'fake', 'model', [], [])
        with self.assertRaises(ProviderError):
            openai_responses(BadHTTP(), 'fake', 'model', [], [], '')

    def test_snapshot_corruption_does_not_restore(self):
        self.ws.write('a','old'); r = self.ws.write('a','new')
        Path(r['backup']).write_text('corrupted')
        with self.assertRaises(RuntimeError):
            self.ws.rollback(r['transaction'])
        self.assertEqual(self.ws.read('a'), 'new')

    def test_memory_fts_dedupe_version_project(self):
        i = self.mem.add('project','portable compiler design', project='p1', importance=5)
        self.assertEqual(i, self.mem.add('project','portable compiler design', project='p1'))
        self.assertEqual(self.mem.search('compiler', project='p1')[0]['id'], i)
        self.assertEqual(self.mem.search('compiler', project='p2'), [])
        self.assertEqual(self.mem.update(i, 'updated design', 1), 2)
        self.assertEqual(self.mem.versions(i)[0]['content'], 'portable compiler design')
        self.assertEqual(self.mem.search('compiler'), [])
        with self.assertRaises(ValueError):
            self.mem.update(i, 'stale', 1)
        with self.mem.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM memory_fts WHERE memory_fts MATCH 'updated'").fetchone()[0], 1)

    def test_memory_cjk_and_literal_query(self):
        self.mem.add('fact','本地记忆不会自动删除 100%')
        self.assertEqual(len(self.mem.search('记忆')),1)
        self.assertEqual(len(self.mem.search('100%')),1)
        self.assertEqual(self.mem.search('" OR *'),[])

    def test_parameter_type_validation(self):
        self.assertIn('error', self.router.execute('write_file', {'path':[], 'content':'x'}, lambda *_:True))
        self.assertIn('error', self.router.execute('set_local_proxy', {'port':True}, lambda *_:True))

    def test_missing_manifest_refuses_plugin(self):
        d=self.root/'plugins'/'bad'; d.mkdir(parents=True)
        (d/'plugin.py').write_text('raise Exception("never execute")')
        with self.assertRaises(OSError):
            self.router.trust_plugin('bad')
        self.assertEqual(self.router.list_plugins(),[])

    def test_fake_http_end_to_end_and_token_cap(self):
        calls=[]
        class FakeHTTP:
            def __init__(self, proxy): self.proxy=proxy
            def post(self, url, key, body):
                calls.append(body)
                if len(calls)==1:
                    return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'c1','type':'function','function':{'name':'write_file','arguments':'{"path":"hello.txt","content":"hello"}'}}]}}]}
                return {'choices':[{'message':{'role':'assistant','content':'done'}}]}
        a=Agent(self.router,http_factory=FakeHTTP)
        self.assertEqual(a.run('write','DeepSeek','fake','test',lambda *_:True),'done')
        self.assertEqual(self.ws.read('hello.txt'),'hello')
        self.assertEqual(calls[0]['max_tokens'],4096)
        self.assertEqual(calls[0]['thinking'],{'type':'disabled'})

    def test_bounded_tool_loop(self):
        calls=[]
        class FakeHTTP:
            def __init__(self, proxy): self.proxy=proxy
            def post(self,*_):
                calls.append(1)
                return {'choices':[{'message':{'role':'assistant','tool_calls':[{'id':str(len(calls)), 'function':{'name':'list_files','arguments':'{"path":"."}'}}]}}]}
        a=Agent(self.router,http_factory=FakeHTTP,max_steps=2)
        self.assertIn('上限', a.run('loop','DeepSeek','fake','test',lambda *_:False))
        self.assertEqual(len(calls),2)

    def test_redirect_does_not_forward_credentials(self):
        with self.assertRaises(ProviderError):
            NoRedirect().redirect_request(None,None,302,'redirect',{},'https://evil.invalid')

    def test_python_compile_uses_local_cache(self):
        import sys
        (self.ws.root/'demo.py').write_text('value = 123\n')
        with patch.object(self.router.devkit, 'find', return_value=(sys.executable, 'test')):
            result=self.router.execute('compile_source', {'language':'python','source':'demo.py'}, lambda *_:True)
        self.assertTrue(result.get('ok'), result)
        self.assertFalse((self.ws.root/'__pycache__').exists())
        self.assertTrue(list((self.root/'data'/'builds').glob('*/*.pyc')))

    def test_http_network_failure(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=OSError('offline')):
            with self.assertRaises(ProviderError):
                HttpClient().post('https://api.deepseek.com/chat/completions','fake',{})
