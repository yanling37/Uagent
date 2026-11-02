"""Simulate both vendors' tool-call protocols without real network/API spending."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from portable_ai.agent import Agent
from portable_ai.storage import Audit, MemoryStore
from portable_ai.workspace import Workspace
from portable_ai.tools import ToolRouter


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        (self.root/'workspace').mkdir()
        self.ws=Workspace(self.root/'workspace',self.root)
        (self.ws.root/'example.txt').write_text('hello')
        self.router=ToolRouter(self.root,self.ws,MemoryStore(self.root),Audit(self.root))
        self.agent=Agent(self.router)

    def tearDown(self):
        self.tmp.cleanup()

    def test_deepseek_tool_loop(self):
        turns=[
            {'role':'assistant','content':None,'tool_calls':[
                {'id':'call_1','type':'function','function':{'name':'read_file','arguments':'{"path":"example.txt"}'}}
            ]},
            {'role':'assistant','content':'文件内容是 hello。'},
        ]
        seen=[]
        def fake(http,key,model,messages,tools):
            seen.append(list(messages))
            self.assertEqual(model,'deepseek-flash')
            return turns[len(seen)-1]
        with patch('portable_ai.agent.deepseek_completion',fake):
            result=self.agent.run('读文件','DeepSeek','fake-secret','deepseek-flash',lambda *_:False)
        self.assertIn('hello',result)
        self.assertEqual(seen[1][-1]['role'],'tool')
        self.assertEqual(seen[1][-1]['content'],'"hello"')
        self.assertNotIn('fake-secret',(self.root/'logs'/'actions.jsonl').read_text())

    def test_openai_responses_loop_and_write_denial(self):
        responses=[
            {'output':[{'type':'function_call','call_id':'call_2','name':'write_file',
                        'arguments':'{"path":"foo.txt","content":"danger"}'}]},
            {'output':[{'type':'message','content':[{'type':'output_text','text':'已尊重用户拒绝，未修改文件。'}]}]},
        ]
        def fake(http,key,model,items,tools,instructions):
            return responses.pop(0)
        with patch('portable_ai.agent.openai_responses',fake):
            result=self.agent.run('试写文件','OpenAI','fake-secret','gpt-5-mini',lambda *_:False)
        self.assertIn('未修改',result)
        self.assertFalse((self.ws.root/'foo.txt').exists())

    def test_no_key_short_circuits(self):
        with self.assertRaises(Exception):
            self.agent.run('问候','DeepSeek','','deepseek-flash',lambda *_:False)


if __name__=='__main__':
    unittest.main()
