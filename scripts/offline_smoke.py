"""No network, no user files: end-to-end MVP exercise in a temporary directory."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
import tempfile
from portable_ai.hybrid_sync import HybridSync
from portable_ai.workspace import Workspace
from portable_ai.storage import MemoryStore, Audit
from portable_ai.tools import ToolRouter
from portable_ai.agent import Agent


def main():
    with tempfile.TemporaryDirectory(prefix='portableai-smoke-') as temp:
        root=Path(temp); usb=root/'usb'; usb.mkdir(); (usb/'workspace').mkdir()
        (usb/'workspace'/'hello.txt').write_text('portable source',encoding='utf-8')
        sync=HybridSync(usb,state_home=root/'hoststate',reserve_bytes=0)
        sync.bind(root/'host',auto_sync=False)
        initial=sync.apply(sync.plan())
        assert initial['ok']
        ws=Workspace(sync.host,usb); memory=MemoryStore(usb)
        router=ToolRouter(usb,ws,memory,Audit(usb))
        class OfflineModel:
            def __init__(self,proxy): self.proxy=proxy; self.count=0
            def post(self,url,key,body):
                self.count+=1
                if self.count==1:
                    return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'1','type':'function','function':{'name':'write_file','arguments':json.dumps({'path':'hello.txt','content':'verified agent result'})}}]}}]}
                return {'choices':[{'message':{'role':'assistant','content':'Offline task completed.'}}]}
        answer=Agent(router,http_factory=OfflineModel).run('Edit demo','DeepSeek','fake-not-real','offline',lambda *_:True)
        memory.add('project','Smoke project verified',project='smoke')
        sync_result=sync.apply(sync.plan())
        assert sync_result['ok'] and (usb/'workspace'/'hello.txt').read_text()=='verified agent result'
        reopened=MemoryStore(usb)
        assert reopened.search('verified',project='smoke')
        receipt=next(ws.transactions.glob('*.json'))
        ws.rollback(receipt.stem)
        assert ws.read('hello.txt')=='portable source'
        print(json.dumps({'ok':True,'answer':answer,'checks':['authorized first sync','fake API tool loop','approved transactional write','host-to-portable hash sync','persistent FTS memory','guarded rollback'], 'network_calls':0},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
