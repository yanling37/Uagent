"""Explicit, auditable tool registry. Tool outputs are not trusted instructions."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from .devkit import DevKit
from .storage import Audit, MemoryStore
from .workspace import Workspace


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Callable
    risk: str = 'read'  # 'read' or 'approval'

    def openai(self):
        return {'type':'function','name':self.name,'description':self.description,
                'parameters':self.parameters}

    def deepseek(self):
        return {'type':'function','function':{'name':self.name,
                'description':self.description,'parameters':self.parameters}}


def schema(properties: dict, required: list[str] | None = None):
    return {'type':'object','properties':properties,
            'required':required or [], 'additionalProperties':False}


def prop(description):
    return {'type':'string','description':description}


class ToolRouter:
    def __init__(self, root: Path, workspace: Workspace, memory: MemoryStore, audit: Audit,
                 cache_root: Path | None = None):
        self.root=Path(root)
        self.workspace=workspace
        self.memory=memory
        self.audit=audit
        self.devkit=DevKit(root,cache_root=cache_root)
        self.proxy_port: int | None = None
        self.specs:dict[str,ToolSpec] = {}
        self._loaded_plugins = set()
        self.install_builtins()
        self.load_trusted_plugins()

    def register(self, spec:ToolSpec):
        if spec.name in self.specs or not spec.name.isidentifier():
            raise ValueError('工具名无效或冲突: '+spec.name)
        if spec.risk not in ('read','approval'):
            raise ValueError('工具必须声明风险类型')
        self.specs[spec.name]=spec

    def install_builtins(self):
        self.register(ToolSpec('list_files', 'List at most 100 files inside the approved workspace.',
            schema({'path':prop('Relative directory, e.g. . or src')},['path']),
            lambda path:self.workspace.list_files(path)))
        self.register(ToolSpec('read_file','Read at most 24000 Unicode characters of a workspace file. Sensitive names are denied.',
            schema({'path':prop('Relative path inside approved workspace')},['path']),
            lambda path:self.workspace.read(path)))
        self.register(ToolSpec('write_file','Transactionally write UTF-8 file in workspace with SHA256 snapshot and rollback. Requires human approval.',
            schema({'path':prop('Relative file path'), 'content':prop('New complete file content')},['path','content']),
            lambda path,content:self.workspace.write(path,content), 'approval'))
        self.register(ToolSpec('memory_search','Search portable SQLite user memory.',
            schema({'query':prop('Search term, or empty string for recent memories')},['query']),
            lambda query:self.memory.search(query)))
        self.register(ToolSpec('memory_save','Persist one confirmed, durable memory in USB SQLite. Requires human approval.',
            schema({'category':{'type':'string','enum':['preference','project','fact','decision','todo']},
                    'content':prop('Durable, accurate memory')},['category','content']),
            lambda category,content:{'id':self.memory.add(category,content)}, 'approval'))
        self.register(ToolSpec('rollback_file', 'Restore one committed file transaction only if unchanged since agent write. Requires approval.',
            schema({'transaction':prop('32-character transaction id returned by write_file')}, ['transaction']),
            lambda transaction:self.workspace.rollback(transaction), 'approval'))
        self.register(ToolSpec('memory_update', 'Update existing memory with version guard and retained history; requires approval.',
            schema({'id':{'type':'integer'}, 'content':prop('Corrected durable memory'), 'version':{'type':'integer'}}, ['id','content','version']),
            lambda id,content,version:{'version':self.memory.update(id,content,version)}, 'approval'))
        self.register(ToolSpec('toolchain_status','List available USB/host compilers and runtimes.',
            schema({}), lambda:self.devkit.status()))
        self.register(ToolSpec('proxy_status','Inspect local loopback ports commonly used by proxy clients; read-only.',
            schema({}), self.proxy_status))
        self.register(ToolSpec('set_local_proxy','Route this Agent API calls through a user-approved local HTTP proxy at 127.0.0.1:PORT; does not change Windows VPN settings.',
            schema({'port':{'type':'integer','minimum':1024,'maximum':65535}},['port']),
            self.set_local_proxy, 'approval'))
        self.register(ToolSpec('compile_source','Run a fixed compiler recipe (no shell) for a workspace file. Build systems might execute code; requires human approval.',
            schema({'language':{'type':'string','enum':['python','java','c','cpp','csharp']},
                    'source':prop('Relative path to source file (.py/.java/.c/.cpp/.csproj)')},['language','source']),
            lambda language,source:self.devkit.compile(language,source,self.workspace), 'approval'))

    def proxy_status(self):
        ports={}
        for port in (7890,7897,1080,9090):
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=0.15):
                    ports[str(port)]='open (protocol NOT verified)'
            except OSError:
                ports[str(port)]='closed'
        return {'local_ports':ports, 'agent_http_proxy':
                f'http://127.0.0.1:{self.proxy_port}' if self.proxy_port else 'system/default'}

    def set_local_proxy(self, port:int):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError('非法端口')
        self.proxy_port=port
        return {'ok':True,'scope':'this Agent process only',
                'http_proxy':f'http://127.0.0.1:{port}',
                'warning':'端口开放不证明它是 HTTP 代理。下一次网络调用将验证。'}

    def current_proxy(self) -> str | None:
        return f'http://127.0.0.1:{self.proxy_port}' if self.proxy_port else None

    def execute(self, name: str, arguments: dict, approve: Callable[[str,dict],bool]):
        spec=self.specs.get(name)
        if not spec:
            self.audit.record('unknown_tool', 'denied')
            return {'error':'未知或未经授权的工具: '+name}
        try:
            if not isinstance(arguments,dict):
                raise ValueError('工具参数必须是 JSON 对象')
            properties=spec.parameters.get('properties',{})
            required=spec.parameters.get('required',[])
            if any(k not in properties for k in arguments) or any(k not in arguments for k in required):
                raise ValueError('工具参数缺失或包含意外字段')
            for key, value in arguments.items():
                rule = properties[key]
                expected = rule.get('type')
                if expected == 'string' and not isinstance(value, str) or expected == 'integer' and type(value) is not int:
                    raise ValueError('工具参数类型不匹配: ' + key)
                if 'enum' in rule and value not in rule['enum']:
                    raise ValueError('工具参数不在允许范围: ' + key)
            before = self.workspace.fingerprint(arguments['path']) if name == 'write_file' else None
            self.audit.record(name, 'requested')
            if spec.risk=='approval' and not approve(name,arguments):
                self.audit.record(name,'denied',str(arguments.get('path') or arguments.get('source') or ''))
                return {'error':'用户拒绝执行此操作'}
            result = self.workspace.write(arguments['path'], arguments['content'], expected_sha=before) if name == 'write_file' else spec.handler(**arguments)
            self.audit.record(name,'ok',str(arguments.get('path') or arguments.get('source') or ''))
            return result
        except Exception as error:
            self.audit.record(name,'error',type(error).__name__)
            return {'error':str(error), 'exception':type(error).__name__}

    def _manifest(self, folder):
        path = folder / 'manifest.json'
        if path.is_symlink():
            raise ValueError('manifest 不可为符号链接')
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(manifest, dict) or not all(k in manifest for k in ('id', 'name', 'version', 'description', 'tools', 'dependencies')):
            raise ValueError('插件 manifest 缺少字段')
        if not isinstance(manifest['tools'], dict) or any(v not in ('read', 'approval') for v in manifest['tools'].values()):
            raise ValueError('插件权限无效')
        return manifest

    def revoke_plugin(self, name):
        # Loaded Python cannot be safely unloaded: restart is mandatory.
        trusted = self._read_trust()
        trusted.pop(name, None)
        from .hybrid_sync import atomic_json
        atomic_json(self._trusted_file(), trusted)
        return {'restart_required': True}

    def _trusted_file(self) -> Path:
        return self.root/'config'/'trusted_plugins.json'

    def list_plugins(self):
        root=self.root/'plugins'
        trusted=self._read_trust()
        results=[]
        if not root.exists():
            return results
        for dirpath in root.iterdir():
            code=dirpath/'plugin.py'
            if not dirpath.is_dir() or dirpath.is_symlink() or not code.is_file() or code.is_symlink():
                continue
            try:
                manifest = self._manifest(dirpath)
            except (ValueError, OSError):
                continue
            digest=hashlib.sha256(code.read_bytes() + (dirpath/'manifest.json').read_bytes()).hexdigest()
            results.append({'name':dirpath.name,'sha256':digest,
                            'trusted':trusted.get(dirpath.name)==digest, 'manifest':manifest})
        return results

    def _read_trust(self):
        f=self._trusted_file()
        if not f.exists():
            return {}
        try:
            data=json.loads(f.read_text(encoding='utf-8'))
            return data if isinstance(data,dict) else {}
        except (ValueError,OSError):
            return {}

    def trust_plugin(self, name:str):
        # This MUST only be called from a direct human interaction (not an LLM tool).
        if not name.isidentifier():
            raise ValueError('插件名只能使用字母、数字和下划线')
        code=self.root/'plugins'/name/'plugin.py'
        if not code.is_file() or code.is_symlink():
            raise FileNotFoundError('插件不存在')
        trusted=self._read_trust()
        self._manifest(code.parent)
        if code.parent.is_symlink():
            raise PermissionError('插件目录不可为符号链接')
        trusted[name]=hashlib.sha256(code.read_bytes() + (code.parent/'manifest.json').read_bytes()).hexdigest()
        f=self._trusted_file()
        f.parent.mkdir(parents=True,exist_ok=True)
        f.write_text(json.dumps(trusted,indent=2,ensure_ascii=False),encoding='utf-8')
        return trusted[name]

    def load_trusted_plugins(self):
        approved={p['name']:p for p in self.list_plugins() if p['trusted']}
        for name in sorted(approved):
            if name in self._loaded_plugins:
                continue
            code=self.root/'plugins'/name/'plugin.py'
            try:
                # Trusted plug-ins are unrestricted Python code, not sandboxed.
                spec=importlib.util.spec_from_file_location('_usbplugin_'+name,code)
                if not spec or not spec.loader:
                    continue
                module=importlib.util.module_from_spec(spec)
                sys.modules[spec.name]=module
                spec.loader.exec_module(module)
                declared = approved[name]['manifest']['tools']
                registered_tools = list(module.register())
                for registered in registered_tools:
                    if declared.get(registered.name) != registered.risk:
                        raise ValueError('插件工具权限与 manifest 不一致')
                    if registered.name in self.specs:
                        raise ValueError('插件工具名冲突')
                for registered in registered_tools:
                    self.register(registered)
                self._loaded_plugins.add(name)
            except Exception as error:
                self.audit.record('plugin_load:'+name,'error',str(error))
