"""Portable AI Agent: conservative USB <-> Windows-host workspace synchronizer.

Hash-based three-way synchronization, with a PER-HOST common ancestor. Only
an explicitly bound worktree is touched. Copy uses same-directory staging and
os.replace. There is no unconditional recursive delete or blind mtime merge.

This is a practical MVP, not a hostile-machine sandbox or a replacement for
an independent backup. Crash recovery is per-file: rerun inventory+plan.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

VERSION = 1
DEFAULT_RESERVE_BYTES = 8 * 1024 ** 3
DEFAULT_FILE_LIMIT = 128 * 1024 ** 2
DEFAULT_BATCH_LIMIT = 1024 ** 3
DEFAULT_SCAN_LIMIT = 30_000
EXCLUDED_DIRS = frozenset({
    '.git', '.svn', '.hg', '.vs', '.idea', '.vscode', '.venv', 'venv',
    '.ssh', '.aws', '.config', '.docker', '.kube',
    'node_modules', '__pycache__', 'bin', 'obj', 'build', 'dist',
    '.gradle', '.m2', '.next', '.nuxt', '.angular', 'target',
    '.pytest_cache', '.mypy_cache', '.ruff_cache', '.tox',
    '__portableai_build__', '.portableai', '.portableai-cache',
})
EXCLUDED_NAMES = frozenset({
    '.env', '.env.local', '.env.production', '.npmrc', '.pypirc',
    'credentials.json', 'secrets.json', 'id_rsa', 'id_ed25519',
    'known_hosts', 'thumbs.db', '.ds_store',
})
EXCLUDED_SUFFIXES = ('.pem', '.pfx', '.p12', '.key', '.pyc', '.pyo')
EXCLUDED_GLOBS = ('.env.*', '*.secret', '*.sqlite3-wal', '*.sqlite3-shm',
                  '.portableai-part-*', '.portableai-tmp-*')
WIN_BAD = set('<>:"|?*')
WIN_RESERVED = {'con', 'prn', 'aux', 'nul'} | {f'com{i}' for i in range(1, 10)} | {f'lpt{i}' for i in range(1, 10)}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def is_reparse(path: Path) -> bool:
    """Reject symlinks, junctions and other Windows reparse points."""
    try:
        s = path.lstat()
    except FileNotFoundError:
        return False
    return bool(stat.S_ISLNK(s.st_mode) or (getattr(s, 'st_file_attributes', 0) & 0x400))


def valid_rel(rel: str) -> bool:
    if not isinstance(rel, str) or not rel or '\x00' in rel or '\\' in rel:
        return False
    p = PurePosixPath(rel)
    if p.is_absolute() or any(x in ('', '.', '..') for x in rel.split('/')):
        return False
    for part in p.parts:
        if len(part) > 255 or part.endswith((' ', '.')) or any(c in WIN_BAD or ord(c) < 32 for c in part):
            return False
        if part.split('.')[0].lower() in WIN_RESERVED:
            return False
    return True


def eligible(rel: str) -> bool:
    if not valid_rel(rel):
        return False
    parts = PurePosixPath(rel).parts
    if any(part.lower() in EXCLUDED_DIRS or part.lower() in EXCLUDED_NAMES for part in parts[:-1]):
        return False
    name = parts[-1].lower()
    return (name not in EXCLUDED_DIRS and name not in EXCLUDED_NAMES
            and not name.endswith(EXCLUDED_SUFFIXES)
            and not any(fnmatch.fnmatchcase(name, mask) for mask in EXCLUDED_GLOBS))


def safe_path(root: Path, rel: str) -> Path:
    if not eligible(rel):
        raise ValueError('不安全或已排除的相对路径: ' + rel)
    root = Path(root).absolute()
    assert_plain_path(root)
    root = root.resolve(strict=True)
    result = root
    for part in PurePosixPath(rel).parts:
        result = result / part
        if is_reparse(result):
            raise PermissionError('检测到符号链接/Windows 重解析点: ' + rel)
    if result.resolve(strict=False) != root and root not in result.resolve(strict=False).parents:
        raise PermissionError('相对路径逃逸了授权目录')
    return result


def assert_plain_path(path: Path) -> None:
    """Check before resolve: resolving first would hide a replaced root link."""
    if any(is_reparse(p) for p in (path, *path.parents)):
        raise PermissionError('路径包含符号链接或 Windows 重解析点')


def file_sha(path: Path) -> str | None:
    assert_plain_path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise PermissionError('同步目标不是普通文件')
    return digest(path)


def atomic_json(path: Path, value: dict) -> None:
    assert_plain_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         prefix='.portableai-tmp-', suffix='.json', delete=False) as f:
            json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
            temp = f.name
        os.replace(temp, path)
        temp = None
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)


def load_json(path: Path, default: dict) -> dict:
    assert_plain_path(path)
    if not path.is_file():
        return default.copy()
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('配置文件不是 JSON 对象: ' + str(path))
    return data


def local_state_home() -> Path:
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA') or (Path.home() / 'AppData' / 'Local')) / 'PortableAI'
    return Path.home() / '.local' / 'share' / 'PortableAI'


@dataclass(frozen=True)
class Item:
    path: str
    action: str                 # copy | delete | conflict | blocked
    direction: str              # usb_to_host | host_to_usb | ''
    usb_sha: str | None
    host_sha: str | None
    base_sha: str | None
    bytes: int = 0
    reason: str = ''


@dataclass
class Plan:
    items: list[Item]
    agreement: dict[str, str | None]  # identical pairs (including both deleted)
    warnings: list[str] = field(default_factory=list)

    def counts(self):
        return {action: sum(i.action == action for i in self.items)
                for action in ('copy', 'delete', 'conflict', 'blocked')}

    def auto_safe(self) -> bool:
        return not any(item.action != 'copy' for item in self.items) and not self.warnings


@dataclass
class Inventory:
    hashes: dict[str, str]
    sizes: dict[str, int]
    blocked: dict[str, str]


class HostLock:
    """Cross-process lock held during plan+apply, no stale PID lock files."""
    def __init__(self, file: Path):
        self.file = file
        self.handle = None

    def __enter__(self):
        assert_plain_path(self.file)
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.file.open('a+b')
        self.handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            if self.handle.seek(0, os.SEEK_END) == 0:
                self.handle.write(b'0')
                self.handle.flush()
            self.handle.seek(0)
            try:
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.handle.close()
                raise RuntimeError('此工作区正在被另一个进程同步') from None
        else:
            import fcntl
            try:
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.handle.close()
                raise RuntimeError('此工作区正在被另一个进程同步') from None
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.handle:
            if os.name == 'nt':
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


class HybridSync:
    def __init__(self, usb_root: Path, state_home: Path | None = None,
                 reserve_bytes: int = DEFAULT_RESERVE_BYTES,
                 max_file_bytes: int = DEFAULT_FILE_LIMIT,
                 max_batch_bytes: int = DEFAULT_BATCH_LIMIT,
                 max_files: int = DEFAULT_SCAN_LIMIT,
                 failpoint: Callable[[str], None] | None = None):
        assert_plain_path(Path(usb_root).absolute())
        self.usb_root = Path(usb_root).resolve(strict=True)
        self.usb = self.usb_root / 'workspace'
        if is_reparse(self.usb) or not self.usb.is_dir():
            raise ValueError('USB workspace 必须是已有的普通目录')
        self.cfg_path = self.usb_root / 'config' / 'hybrid_sync.json'
        cfg = load_json(self.cfg_path, {})
        if cfg and (cfg.get('mode') != 'smart_hybrid' or cfg.get('version') != VERSION or not isinstance(cfg.get('project_id'), str)):
            raise ValueError('不兼容的混合工作区配置；拒绝自动迁移')
        if not cfg:
            cfg = {'version': VERSION, 'mode': 'smart_hybrid', 'project_id': uuid.uuid4().hex}
            atomic_json(self.cfg_path, cfg)
        self.project_id = cfg['project_id']
        if not (len(self.project_id) == 32 and all(c in '0123456789abcdef' for c in self.project_id)):
            raise ValueError('项目 ID 非法')
        state_candidate = Path(state_home or local_state_home()).expanduser().absolute()
        assert_plain_path(state_candidate)
        self.state_home = state_candidate.resolve()
        self.state_dir = self.state_home / 'sync' / self.project_id
        self.binding_path = self.state_dir / 'binding.json'
        self.state_path = self.state_dir / 'state.json'
        self.lock_path = self.state_dir / 'sync.lock'
        self.transactions = self.state_dir / 'transactions'
        self.snapshots = self.state_dir / 'snapshots'
        self.reserve_bytes = reserve_bytes
        self.max_file_bytes = max_file_bytes
        self.max_batch_bytes = max_batch_bytes
        self.max_files = max_files
        self.failpoint = failpoint
        self.host: Path | None = None
        binding = load_json(self.binding_path, {})
        if binding:
            if binding.get('project_id') != self.project_id:
                raise ValueError('主机绑定指向其他项目')
            p = Path(binding['host_root'])
            if p.is_dir():
                assert_plain_path(p)
                self.host = p.resolve(strict=True)
                self._assert_separate(self.host)

    @property
    def bound(self) -> bool:
        return self.host is not None

    @property
    def auto_enabled(self) -> bool:
        b = load_json(self.binding_path, {}) if self.bound else {}
        return self.bound and bool(b.get('auto_sync', False) and b.get('initial_reviewed', False))

    @property
    def initial_reviewed(self) -> bool:
        return self.bound and bool(load_json(self.binding_path, {}).get('initial_reviewed', False))

    def _mark_initial_reviewed(self):
        binding = load_json(self.binding_path, {})
        if binding and not binding.get('initial_reviewed'):
            binding['initial_reviewed'] = True
            binding['initial_reviewed_at'] = now_utc()
            atomic_json(self.binding_path, binding)

    def default_host_path(self) -> Path:
        return self.state_home / 'workspaces' / self.project_id

    def _assert_separate(self, host: Path):
        if host == self.usb_root or self.usb_root in host.parents or host in self.usb_root.parents:
            raise ValueError('目标电脑工作区必须和 U 盘工作区分开，不能嵌套')
        if host.parent == host:
            raise ValueError('不能授权磁盘根目录')
        if is_reparse(host):
            raise ValueError('不能授权符号链接/重解析点目录')
        for x in (host, *host.parents):
            if x.exists() and is_reparse(x):
                raise ValueError('本地工作区路径包含重解析点')

    def bind(self, host_root: Path | None = None, auto_sync: bool = True) -> Path:
        """Caller MUST obtain explicit consent before binding or copying."""
        candidate = Path(host_root or self.default_host_path()).expanduser().absolute()
        self._assert_separate(candidate)
        candidate.mkdir(parents=True, exist_ok=True)
        host = candidate.resolve(strict=True)
        self._assert_separate(host)
        if self.host is not None:
            if self.host != host:
                raise ValueError('已绑定其他目录；为了保护共同版本，不可直接改绑定目录')
            return host  # Idempotent: never reset the first-sync approval.
        prior = load_json(self.binding_path, {})
        if prior and prior.get('host_root') != str(host):
            raise ValueError('原绑定目录暂不可用，禁止创建不同目录覆盖原绑定，请先恢复或迁移')
        if (self.state_path.exists() and not self.binding_path.exists()):
            raise ValueError('检测到孤立的基线状态；请手工恢复绑定，禁止重新初始化')
        self.state_dir.mkdir(parents=True, exist_ok=True)
        atomic_json(self.binding_path, {'version': VERSION, 'project_id': self.project_id,
                                        'host_root': str(host), 'auto_sync': bool(auto_sync),
                                        'initial_reviewed': bool(prior.get('initial_reviewed',False)),
                                        'authorized_at': prior.get('authorized_at',now_utc())})
        self.host = host
        return host

    def set_auto(self, enabled: bool):
        if not self.bound:
            raise ValueError('未绑定目标电脑工作区')
        binding = load_json(self.binding_path, {})
        binding['auto_sync'] = bool(enabled)
        atomic_json(self.binding_path, binding)

    def _read_base(self) -> dict[str, str]:
        state = load_json(self.state_path, {})
        if not state:
            return {}
        if state.get('project_id') != self.project_id or state.get('host_root') != str(self.host):
            raise ValueError('同步基线与项目或绑定目录不匹配')
        hashes = state.get('base')
        if not isinstance(hashes, dict) or any(not eligible(k) or not isinstance(v, str) or len(v)!=64 or any(c not in '0123456789abcdef' for c in v) for k, v in hashes.items()):
            raise ValueError('同步基线已损坏，拒绝继续写入')
        return hashes

    def _save_base(self, base: dict[str, str]):
        atomic_json(self.state_path, {'version': VERSION, 'project_id': self.project_id,
                                      'host_root': str(self.host), 'base': base,
                                      'updated_utc': now_utc()})

    def _scan(self, root: Path) -> Inventory:
        assert_plain_path(root)
        hashes, sizes, blocked = {}, {}, {}
        count = 0
        def scan_error(error):
            raise error  # Incomplete inventory must never be interpreted as deletion.
        for dirpath, dirs, files in os.walk(root, topdown=True, followlinks=False, onerror=scan_error):
            parent = Path(dirpath)
            for name in list(dirs):
                path = parent / name
                rel = path.relative_to(root).as_posix()
                if name.lower() in EXCLUDED_DIRS or not eligible(rel + '/placeholder.txt'):
                    dirs.remove(name)
                elif is_reparse(path):
                    dirs.remove(name)
                    blocked[rel] = 'symlink_or_junction_directory'
            for name in files:
                path = parent / name
                rel = path.relative_to(root).as_posix()
                if not eligible(rel):
                    continue
                count += 1
                if count > self.max_files:
                    raise RuntimeError(f'可同步文件超过 {self.max_files} 项；为避免不完整扫描，本轮拒绝同步')
                if is_reparse(path):
                    blocked[rel] = 'symlink_or_reparse_file'
                    continue
                try:
                    info = path.lstat()
                    if not stat.S_ISREG(info.st_mode):
                        blocked[rel] = 'non_regular_file'
                    elif info.st_size > self.max_file_bytes:
                        blocked[rel] = 'file_too_large'
                    else:
                        hashes[rel] = digest(path)
                        sizes[rel] = info.st_size
                except OSError as e:
                    blocked[rel] = type(e).__name__
        # Detect casefold collisions even when scanning on a case-sensitive host.
        collision = {}
        for rel in hashes.keys() | blocked.keys():
            collision.setdefault(rel.casefold(), []).append(rel)
        for paths in collision.values():
            if len(paths) > 1:
                for rel in paths:
                    blocked[rel] = 'case_insensitive_name_collision'
                    hashes.pop(rel, None)
                    sizes.pop(rel, None)
        return Inventory(hashes, sizes, blocked)

    def _plan(self, base: dict[str, str]) -> Plan:
        if not self.bound:
            raise ValueError('请先明确授权并绑定目标电脑工作区')
        if not self.usb.is_dir() or not self.host.is_dir():
            raise FileNotFoundError('U 盘或目标工作区已经断开连接')
        usb = self._scan(self.usb)
        host = self._scan(self.host)
        all_paths = sorted(set(base) | set(usb.hashes) | set(host.hashes) | set(usb.blocked) | set(host.blocked))
        # Windows paths are normally case-insensitive. Prevent two different
        # spellings on opposite sides from silently aliasing the same file.
        cross_case = {}
        for rel in all_paths:
            cross_case.setdefault(rel.casefold(), []).append(rel)
        case_conflicts = {rel for spellings in cross_case.values() if len(spellings)>1 for rel in spellings}
        items, agreement = [], {}
        for rel in all_paths:
            u, h, b = usb.hashes.get(rel), host.hashes.get(rel), base.get(rel)
            if rel in case_conflicts:
                items.append(Item(rel, 'blocked', '', u, h, b, reason='USB/主机路径大小写不一致或重名'))
                continue
            if any(rel == blocked or rel.startswith(blocked + '/')
                   for blocked in usb.blocked.keys() | host.blocked.keys()):
                items.append(Item(rel, 'blocked', '', u, h, b,
                                  reason='USB: ' + usb.blocked.get(rel, '-') + ', host: ' + host.blocked.get(rel, '-')))
                continue
            # A missing tracked file inside an ignored/excluded folder is not present in all_paths.
            if u == h:
                agreement[rel] = u
                continue
            if b is None:
                if u is None:
                    direction, size = 'host_to_usb', host.sizes[rel]
                elif h is None:
                    direction, size = 'usb_to_host', usb.sizes[rel]
                else:
                    items.append(Item(rel, 'conflict', '', u, h, b, reason='双方已有不同文件，未建立共同版本'))
                    continue
                items.append(Item(rel, 'copy', direction, u, h, b, bytes=size))
            elif u == b:
                action = 'delete' if h is None else 'copy'
                items.append(Item(rel, action, 'host_to_usb', u, h, b, bytes=host.sizes.get(rel, 0)))
            elif h == b:
                action = 'delete' if u is None else 'copy'
                items.append(Item(rel, action, 'usb_to_host', u, h, b, bytes=usb.sizes.get(rel, 0)))
            else:
                items.append(Item(rel, 'conflict', '', u, h, b, reason='USB 和电脑自共同版本后均已变化'))
        warnings = []
        if sum(i.bytes for i in items if i.action == 'copy') > self.max_batch_bytes:
            warnings.append('本次复制字节数超过单批上限；需要缩小工作区或分批处理')
        return Plan(items, agreement, warnings)

    def plan(self) -> Plan:
        # Independent read-only preview, checked AGAIN under lock before apply.
        return self._plan(self._read_base())

    def _journal(self, tx: str, event: str, **extra):
        assert_plain_path(self.transactions / (tx + '.jsonl'))
        self.transactions.mkdir(parents=True, exist_ok=True)
        with (self.transactions / (tx + '.jsonl')).open('a', encoding='utf-8') as f:
            f.write(json.dumps({'utc': now_utc(), 'event': event, **extra}, ensure_ascii=False) + '\n')
            f.flush()
            os.fsync(f.fileno())

    def unfinished(self) -> list[str]:
        if not self.transactions.exists():
            return []
        pending = []
        for p in sorted(self.transactions.glob('*.jsonl')):
            try:
                events = [json.loads(line)['event'] for line in p.read_text(encoding='utf-8').splitlines() if line]
            except (OSError, ValueError, KeyError):
                pending.append(p.stem)
                continue
            receipt = load_json(self.transactions / (p.stem + '.json'), {})
            if (receipt.get('pending') or (events and events[0] == 'start'
                and events[-1] not in ('completed', 'partial', 'rolled_back'))):
                pending.append(p.stem)
        return pending

    def _copy_file(self, src: Path, dst: Path, expected: str, before: str | None = None):
        assert_plain_path(src)
        assert_plain_path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if is_reparse(dst) or is_reparse(dst.parent):
            raise PermissionError('复制目标包含重解析点')
        tmp = None
        try:
            with tempfile.NamedTemporaryFile('wb', dir=dst.parent, prefix='.portableai-part-', delete=False) as f:
                tmp = Path(f.name)
                h = hashlib.sha256()
                with src.open('rb') as stream:
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        h.update(chunk)
                f.flush()
                os.fsync(f.fileno())
            if h.hexdigest() != expected or digest(src) != expected:
                raise RuntimeError('复制期间源文件发生变化；已取消覆盖')
            if self.failpoint:
                self.failpoint('before_replace')
            assert_plain_path(src)
            if file_sha(dst) != before:
                raise RuntimeError('替换前发现目标并发修改，拒绝覆盖')
            os.replace(tmp, dst)
            tmp = None
            if digest(dst) != expected:
                raise RuntimeError('目标端校验失败；请利用快照恢复')
            if self.failpoint:
                self.failpoint('after_replace')
        finally:
            if tmp and tmp.exists():
                tmp.unlink()

    def _snapshot(self, dst: Path, rel: str, tx: str, side: str, expected: str | None) -> str | None:
        if expected is None:
            return None
        target = self.snapshots / tx / side / Path(rel)
        assert_plain_path(target)
        if target.exists():
            raise FileExistsError('快照目标已经存在，拒绝覆盖')
        target.parent.mkdir(parents=True, exist_ok=True)
        self._copy_file(dst, target, expected)
        return str(target.relative_to(self.state_dir).as_posix())

    def _available(self, side: str, bytes_to_write: int, backup_bytes: int):
        volume = self.usb if side == 'usb' else self.host
        threshold = self.reserve_bytes if side == 'usb' else 1024 ** 3
        if shutil.disk_usage(volume).free < threshold + bytes_to_write:
            raise OSError(f'{side} 空间不足，保留空间或文件大小要求不满足')
        if shutil.disk_usage(self.state_dir).free < backup_bytes + 100 * 1024 ** 2:
            raise OSError('目标电脑上的恢复快照空间不足')

    def apply(self, plan: Plan | None = None, allow_deletions: bool = False) -> dict:
        """Caller must approve actions (or have previously authorized safe auto sync).

        Invalid/conflicting entries never execute. An outdated preview cannot execute.
        Deletions require a separate affirmative allow_deletions=True.
        """
        if not self.bound:
            raise ValueError('尚未授权本地工作区')
        with HostLock(self.lock_path):
            if self.unfinished():
                raise RuntimeError('存在中断事务，请先执行同步恢复')
            base = self._read_base()
            current = self._plan(base)
            if plan is not None and (current.items != plan.items or current.agreement != plan.agreement):
                raise RuntimeError('文件在预览后又发生变化，请重新预览')
            if current.warnings:
                raise RuntimeError('; '.join(current.warnings))
            blocked = [i for i in current.items if i.action in ('conflict', 'blocked')]
            if blocked:
                # Fail closed: don't partially copy while unresolved conflicts exist.
                return {'ok': False, 'reason': '存在冲突或不安全文件，已暂停整个批次',
                        'counts': current.counts(), 'issues': [asdict(i) for i in blocked[:50]]}
            actions = [i for i in current.items if i.action == 'copy' or (i.action == 'delete' and allow_deletions)]
            updated = base.copy()
            for rel, sha in current.agreement.items():
                if sha is None:
                    updated.pop(rel, None)
                else:
                    updated[rel] = sha
            deletion_waiting = len([i for i in current.items if i.action == 'delete']) - len(
                [i for i in actions if i.action == 'delete'])
            if not actions:
                # An already-equal pair is a valid new common ancestor, even
                # though no content copying was necessary.
                self._save_base(updated)
                self._mark_initial_reviewed()
                return {'ok': True, 'transaction': None, 'copied': 0, 'deleted': 0,
                        'deletions_waiting': deletion_waiting, 'bytes': 0, 'errors': []}
            tx = uuid.uuid4().hex
            txfile = self.transactions / (tx + '.json')
            receipt = {'version': VERSION, 'id': tx, 'utc': now_utc(), 'project_id': self.project_id,
                       'status': 'started', 'ops': [], 'pending': None}
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self._journal(tx, 'start', copies=sum(i.action == 'copy' for i in actions),
                          deletes=sum(i.action == 'delete' for i in actions))
            atomic_json(txfile, receipt)
            # Before I/O, persist only paths already identical on both sides.
            self._save_base(updated)
            result = {'ok': True, 'transaction': tx, 'copied': 0, 'deleted': 0,
                      'deletions_waiting': deletion_waiting, 'bytes': 0, 'errors': []}
            for i in actions:
                src_side, dst_side = ('usb', 'host') if i.direction == 'usb_to_host' else ('host', 'usb')
                src_root = self.usb if src_side == 'usb' else self.host
                dst_root = self.usb if dst_side == 'usb' else self.host
                expected = i.usb_sha if src_side == 'usb' else i.host_sha
                before = i.host_sha if dst_side == 'host' else i.usb_sha
                try:
                    target = safe_path(dst_root, i.path)
                    source = safe_path(src_root, i.path)
                    current_target = file_sha(target)
                    current_source = file_sha(source)
                    if before != current_target or expected != current_source:
                        raise RuntimeError('同步前再次校验发现并发修改，停止批次')
                    if i.action == 'delete' and current_target is None:
                        raise RuntimeError('删除目标不再存在')
                    self._available(dst_side, i.bytes if i.action == 'copy' else 0,
                                    target.stat().st_size if before else 0)
                    snapshot = self._snapshot(target, i.path, tx, dst_side, before)
                    op = {'path': i.path, 'direction': i.direction, 'action': i.action,
                          'target': dst_side, 'before_sha': before,
                          'after_sha': expected if i.action == 'copy' else None, 'snapshot': snapshot}
                    # WAL: backup and undo metadata are durable BEFORE target replacement.
                    receipt['pending'] = op
                    atomic_json(txfile, receipt)
                    self._journal(tx, 'prepared', path=i.path)
                    if i.action == 'copy':
                        self._copy_file(source, target, expected, before)
                        result['copied'] += 1
                        result['bytes'] += i.bytes
                        updated[i.path] = expected
                    else:
                        if file_sha(target) != before or file_sha(source) is not None:
                            raise RuntimeError('删除前发现并发修改，拒绝删除')
                        target.unlink()
                        result['deleted'] += 1
                        updated.pop(i.path, None)
                    receipt['ops'].append(op)
                    receipt['pending'] = None
                    atomic_json(txfile, receipt)
                    self._save_base(updated)
                    self._journal(tx, 'applied', path=i.path, action=i.action)
                except Exception as exc:
                    result['ok'] = False
                    result['errors'].append({'path': i.path, 'error': str(exc)})
                    self._journal(tx, 'error', path=i.path, error=type(exc).__name__)
                    break
            receipt['status'] = 'completed' if result['ok'] else 'partial'
            atomic_json(txfile, receipt)
            self._journal(tx, 'completed' if result['ok'] else 'partial')
            if result['ok']:
                self._mark_initial_reviewed()
            return result

    def recover(self) -> dict:
        """Reconcile prepared file operations after crash/unplug. No new file copies.

        A fully verified replacement is appended to the receipt and becomes
        rollback-eligible; an untouched destination remains pending for replan.
        Unknown destination content is NEVER overwritten by recovery.
        """
        if not self.bound:
            return {'recovered': [], 'unresolved': []}
        recovered, unresolved = [], []
        with HostLock(self.lock_path):
            for tx in self.unfinished():
                path = self.transactions / (tx + '.json')
                receipt = load_json(path, {})
                op = receipt.get('pending')
                if not op:
                    # Last journal line missing but there was no in-flight file.
                    self._journal(tx, 'partial')
                    continue
                try:
                    rel = op['path']
                    if op['target'] not in ('usb', 'host'):
                        raise ValueError('事务目标无效')
                    root = self.usb if op['target'] == 'usb' else self.host
                    target = safe_path(root, rel)
                    current = file_sha(target)
                    if current == op['after_sha'] and current != op['before_sha']:
                        base = self._read_base()
                        if current is None:
                            base.pop(rel, None)
                        else:
                            base[rel] = current
                        receipt['ops'].append(op)
                        receipt['pending'] = None
                        receipt['status'] = 'partial'
                        atomic_json(path, receipt)
                        self._save_base(base)
                        self._journal(tx, 'recovered', path=rel)
                        self._journal(tx, 'partial')
                        recovered.append(rel)
                    elif current == op['before_sha']:
                        receipt['pending'] = None
                        receipt['status'] = 'partial'
                        atomic_json(path, receipt)
                        self._journal(tx, 'prepared_not_applied', path=rel)
                        self._journal(tx, 'partial')
                        recovered.append(rel + ' (未覆盖)')
                    else:
                        unresolved.append({'path': rel, 'reason': '目标与新旧版本都不一致，需人工处理'})
                except Exception as exc:
                    unresolved.append({'transaction': tx, 'reason': str(exc)})
        return {'recovered': recovered, 'unresolved': unresolved}

    def latest_transaction(self) -> dict | None:
        if not self.transactions.is_dir():
            return None
        files = sorted(self.transactions.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files:
            value = load_json(p, {})
            if value.get('ops') and not value.get('rolled_back'):
                return value
        return None

    def rollback(self, transaction_id: str) -> dict:
        """Restore only sync targets that still match that transaction's output.

        This changes only the destination side. It pauses automatic sync so
        this deliberate reversal cannot immediately re-propagate.
        """
        if not self.bound:
            raise ValueError('尚未绑定工作区')
        if len(transaction_id) != 32 or any(c not in '0123456789abcdef' for c in transaction_id):
            raise ValueError('非法事务 ID')
        with HostLock(self.lock_path):
            file = self.transactions / (transaction_id + '.json')
            data = load_json(file, {})
            if not data or data.get('project_id') != self.project_id or data.get('rolled_back'):
                raise ValueError('未找到有效、尚未回滚的事务')
            if data.get('pending'):
                raise RuntimeError('此事务含有中断写入；请先执行同步恢复')
            # Pause before the first destructive operation, including crash paths.
            self.set_auto(False)
            undone, rejected = [], []
            already = set(data.get('undone_paths', []))
            for op in reversed(data['ops']):
                if op['path'] in already:
                    continue
                rel = op['path']
                if op.get('target') not in ('usb', 'host'):
                    rejected.append({'path': rel, 'reason': '事务目标无效'}); continue
                dst_root = self.usb if op['target'] == 'usb' else self.host
                try:
                    dst = safe_path(dst_root, rel)
                    current = file_sha(dst)
                    if data.get('rollback_pending') == rel and current == op['before_sha']:
                        # Replacement completed before the previous receipt update.
                        already.add(rel)
                        undone.append(rel)
                        data['undone_paths'] = sorted(already)
                        data['rollback_pending'] = None
                        atomic_json(file, data)
                        continue
                    if current != op['after_sha']:
                        rejected.append({'path': rel, 'reason': '同步后文件又被修改，拒绝覆盖'})
                        break
                    original_sha = op['before_sha']
                    # A rollback is also a destructive change: save the version
                    # being undone before replacing or deleting anything.
                    if current is not None:
                        rollback_snapshot = self.snapshots / ('rollback-' + transaction_id) / op['target'] / Path(rel)
                        if rollback_snapshot.exists():
                            if file_sha(rollback_snapshot) != current:
                                raise RuntimeError('回滚快照校验失败，拒绝覆盖')
                        else:
                            self._snapshot(dst, rel, 'rollback-' + transaction_id, op['target'], current)
                    data['rollback_pending'] = rel
                    atomic_json(file, data)
                    if original_sha is None:
                        if current is None:
                            rejected.append({'path': rel, 'reason': '目标已不存在'})
                            continue
                        if file_sha(dst) != current:
                            raise RuntimeError('回滚前发现并发修改，拒绝删除')
                        dst.unlink()
                    else:
                        snap = self.snapshots / transaction_id / op['target'] / Path(rel)
                        if str(snap.relative_to(self.state_dir).as_posix()) != op['snapshot']:
                            raise RuntimeError('恢复快照路径和事务不匹配')
                        if not snap.is_file() or any(is_reparse(x) for x in (snap, *snap.parents)) or digest(snap) != original_sha:
                            raise RuntimeError('恢复快照缺失或校验失败')
                        self._copy_file(snap, dst, original_sha, current)
                    undone.append(rel)
                    already.add(rel)
                    data['undone_paths'] = sorted(already)
                    data['rollback_pending'] = None
                    atomic_json(file, data)
                except Exception as exc:
                    rejected.append({'path': rel, 'reason': str(exc)})
                    break  # Preserve the single pending rollback operation for retry.
            data['rolled_back'] = len(already) == len(data['ops']) and not rejected and not data.get('pending')
            if data['rolled_back']:
                data['rolled_back_at'] = now_utc()
            atomic_json(file, data)
            self.set_auto(False)
            self._journal(transaction_id, 'rolled_back' if data['rolled_back'] else 'rollback_partial',
                          restored=len(undone), rejected=len(rejected))
            return {'undone': undone, 'rejected': rejected, 'auto_sync_paused': True}
