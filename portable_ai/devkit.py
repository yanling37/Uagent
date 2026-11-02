"""Detect USB toolchains and run narrow, confirmed compilation recipes."""
from __future__ import annotations
import os
import hashlib
import platform
import shutil
import subprocess
import sys
from pathlib import Path


class DevKit:
    def __init__(self, root: Path, cache_root: Path | None = None):
        self.root = Path(root)
        self.base = self.root / 'toolchains'
        # Keep high-churn pip, NuGet and npm caches on the host when bound.
        self.cache_root = Path(cache_root) if cache_root else self.root / 'data'

    def candidates(self, name: str) -> list[Path]:
        win = os.name == 'nt'
        e = '.exe' if win else ''
        targets = {
            'python': [f'python/python{e}', f'python/**/python{e}', f'python/**/python-*/python{e}'],
            'java': [f'java/bin/java{e}', f'java/*/bin/java{e}'],
            'javac': [f'java/bin/javac{e}', f'java/*/bin/javac{e}'],
            'dotnet': [f'dotnet/dotnet{e}'],
            'node': [f'node/node{e}', f'node/*/node{e}'],
            'git': [f'git/cmd/git{e}', f'git/bin/git{e}', f'git/{"mingw64/bin/git" if win else "bin/git"}'],
            'clang': [f'llvm-mingw/bin/clang{e}', f'llvm-mingw/*/bin/clang{e}'],
            'clang++': [f'llvm-mingw/bin/clang++{e}', f'llvm-mingw/*/bin/clang++{e}'],
            'gcc': [f'llvm-mingw/bin/gcc{e}', f'llvm-mingw/*/bin/gcc{e}'],
            'g++': [f'llvm-mingw/bin/g++{e}', f'llvm-mingw/*/bin/g++{e}'],
            'cmake': [f'utilities/cmake/bin/cmake{e}'],
            'ninja': [f'utilities/ninja{e}'],
            'ffmpeg': [f'utilities/ffmpeg/bin/ffmpeg{e}'],
            '7z': [f'utilities/7zip/7z{e}'],
        }
        found = []
        for pattern in targets.get(name, []):
            for result in self.base.glob(pattern):
                if result.is_file() and result not in found:
                    found.append(result)
        return found

    def find(self, name: str, portable_only: bool = False) -> tuple[str | None, str]:
        paths = self.candidates(name)
        if paths:
            return str(paths[0]), 'USB'
        if portable_only:
            return None, 'missing'
        host = shutil.which(name)
        return (host, 'computer') if host else (None, 'missing')

    def status(self) -> dict:
        names=['python','java','javac','dotnet','node','git','clang','clang++','gcc','g++','cmake','ninja','ffmpeg','7z']
        return {'platform': platform.platform(), 'toolchains': {
            name: {'path': self.find(name)[0], 'source': self.find(name)[1]} for name in names}}

    def session_env(self) -> dict:
        env = os.environ.copy()
        extra=[]
        for name in ['python','java','dotnet','node','git','clang','cmake','ninja','ffmpeg','7z']:
            exe,_ = self.find(name, portable_only=True)
            if exe:
                d=str(Path(exe).parent)
                if d not in extra:
                    extra.append(d)
                if name == 'java':
                    env['JAVA_HOME']=str(Path(exe).parent.parent)
                if name == 'dotnet':
                    env['DOTNET_ROOT']=d
        env['PATH']=os.pathsep.join(extra+[env.get('PATH','')])
        env['DOTNET_CLI_HOME']=str(self.cache_root / 'dotnet-home')
        env['NUGET_PACKAGES']=str(self.cache_root / 'nuget')
        env['npm_config_cache']=str(self.cache_root / 'npm-cache')
        env['PIP_CACHE_DIR']=str(self.cache_root / 'pip-cache')
        env['PYTHONDONTWRITEBYTECODE']='1'
        return env

    def compile(self, language: str, source: str, workspace) -> dict:
        opts = {
            'python': (['python'], {'.py'}),
            'java': (['javac'], {'.java'}),
            'c': (['clang','gcc'], {'.c'}),
            'cpp': (['clang++','g++'], {'.cpp','.cc','.cxx'}),
            'csharp': (['dotnet'], {'.csproj'}),
        }
        if language not in opts:
            raise ValueError('支持 python / java / c / cpp / csharp')
        path = workspace.resolve(source)
        if not path.is_file() or path.suffix.lower() not in opts[language][1]:
            raise ValueError('源文件不存在或扩展名与语言不匹配')
        if path.stat().st_size > 1_000_000:
            raise ValueError('源文件大于1MB')
        compiler=None
        for item in opts[language][0]:
            compiler=self.find(item)[0]
            if compiler:
                break
        if compiler is None:
            return {'ok':False, 'error':'对应编译器未安装。请先把工具链解压到 toolchains/。'}
        from .hybrid_sync import is_reparse
        scope=hashlib.sha256(str(workspace.root).encode()).hexdigest()[:24]
        builddir=self.cache_root/'builds'/scope
        if any(is_reparse(p) for p in (builddir,*builddir.parents)):
            raise PermissionError('构建缓存不可包含符号链接')
        builddir.mkdir(parents=True,exist_ok=True)
        if language=='python':
            argv=[compiler, '-c', 'import py_compile,sys; py_compile.compile(sys.argv[1],cfile=sys.argv[2],doraise=True)', str(path), str(builddir/(path.stem+'.pyc'))]
        elif language=='java':
            argv=[compiler, '-proc:none', '-d', str(builddir), str(path)]
        elif language in ('c','cpp'):
            ext='.exe' if os.name == 'nt' else '.bin'
            target=builddir/(path.stem+ext)
            argv=[compiler, str(path), '-o', str(target)]
        else:
            argv=[compiler, 'build', str(path), '--nologo', '--output', str(builddir)]
        # NOT a security sandbox: build systems/compilers may execute arbitrary project code.
        try:
            cp=subprocess.run(argv, cwd=str(workspace.root), env=self.session_env(),
                              capture_output=True, text=True, timeout=35, shell=False,
                              errors='replace')
            return {'ok':cp.returncode==0,'exit_code':cp.returncode,
                    'output':(cp.stdout+'\n'+cp.stderr)[-10000:]}
        except subprocess.TimeoutExpired:
            return {'ok':False, 'error':'编译超时（35秒）'}
