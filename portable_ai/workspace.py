"""Authorized workspace IO; not an OS sandbox against hostile concurrent processes."""
from __future__ import annotations
import hashlib
import os
import tempfile
import uuid
from pathlib import Path
from .hybrid_sync import safe_path, is_reparse, atomic_json, load_json, HostLock, digest


class Workspace:
    def __init__(self, user_selected_root: Path, data_root: Path):
        raw = Path(user_selected_root).absolute()
        if any(is_reparse(p) for p in (raw, *raw.parents)):
            raise PermissionError('工作区根目录不可包含符号链接/重解析点')
        self.root = raw.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('工作区不是目录')
        self.backup = Path(data_root) / 'backups'
        # Scope receipts to this absolute workspace; never replay against another root.
        scope = hashlib.sha256(str(self.root).encode()).hexdigest()[:24]
        self.transactions = self.backup / 'file-transactions' / scope
        self.transactions.mkdir(parents=True, exist_ok=True)

    def resolve(self, rel: str) -> Path:
        if rel == '.':
            return self.root
        if not isinstance(rel, str):
            raise ValueError('相对路径必须是字符串')
        return safe_path(self.root, rel.replace('\\', '/'))

    def list_files(self, rel: str = '.'):
        directory = self.resolve(rel)
        if not directory.is_dir():
            raise NotADirectoryError(rel)
        entries = []
        for p in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
            try:
                relative = p.relative_to(self.root).as_posix()
                self.resolve(relative)
            except (ValueError, PermissionError):
                continue
            entries.append({'path': relative, 'directory': p.is_dir()})
            if len(entries) >= 100:
                break
        return entries

    def read(self, rel: str):
        p = self.resolve(rel)
        if not p.is_file():
            raise FileNotFoundError(rel)
        if p.stat().st_size > 300_000:
            raise ValueError('读取上限 300KB')
        return p.read_text(encoding='utf-8-sig')[:24000]

    def fingerprint(self, rel):
        p = self.resolve(rel)
        return digest(p) if p.is_file() else None

    def _replace(self, p, data, expected):
        p.parent.mkdir(parents=True, exist_ok=True)
        self.resolve(p.relative_to(self.root).as_posix())
        name = None
        try:
            with tempfile.NamedTemporaryFile('wb', dir=p.parent, prefix='.portableai-tmp-', delete=False) as f:
                name = f.name
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            if digest(Path(name)) != hashlib.sha256(data).hexdigest():
                raise IOError('临时文件校验失败')
            self.resolve(p.relative_to(self.root).as_posix())
            if (digest(p) if p.exists() else None) != expected:
                raise RuntimeError('文件已被其他程序修改，拒绝覆盖')
            os.replace(name, p)
        finally:
            if name and os.path.exists(name):
                os.unlink(name)

    def write(self, rel: str, content: str, expected_sha='capture'):
        data = content.encode('utf-8')
        if len(data) > 200_000:
            raise ValueError('单次写入上限200KB')
        if any(r.get('status') == 'unresolved' for r in self.recover()):
            raise RuntimeError('存在未解决文件事务；人工核对后再写入')
        with HostLock(self.transactions / 'write.lock'):
            p = self.resolve(rel)
            if p == self.root or (p.exists() and not p.is_file()):
                raise ValueError('目标必须是普通文件')
            before = digest(p) if p.exists() else None
            if expected_sha != 'capture' and before != expected_sha:
                raise RuntimeError('批准预览后文件已变化，拒绝覆盖')
            tx = uuid.uuid4().hex
            receipt = self.transactions / (tx + '.json')
            snapshot = self.transactions / (tx + '.snapshot')
            record = {'id': tx, 'path': p.relative_to(self.root).as_posix(), 'before': before,
                      'after': hashlib.sha256(data).hexdigest(), 'status': 'intent'}
            atomic_json(receipt, record)
            if before is not None:
                with snapshot.open('xb') as f:
                    f.write(p.read_bytes()); f.flush(); os.fsync(f.fileno())
                if digest(snapshot) != before:
                    raise RuntimeError('快照期间文件变化')
            record['status'] = 'prepared'
            atomic_json(receipt, record)
            self._replace(p, data, before)
            record['status'] = 'committed'
            atomic_json(receipt, record)
            return {'path': record['path'], 'backup': str(snapshot) if before else None,
                    'bytes': len(data), 'transaction': tx, 'sha256': record['after']}

    def recover(self):
        results = []
        with HostLock(self.transactions / 'write.lock'):
            for f in self.transactions.glob('*.json'):
                r = load_json(f, {})
                if r.get('status') == 'unresolved':
                    results.append(r)
                    continue
                if r.get('status') not in ('intent', 'prepared'):
                    continue
                actual = self.fingerprint(r['path'])
                r['status'] = 'committed' if actual == r['after'] else 'aborted' if actual == r['before'] else 'unresolved'
                atomic_json(f, r); results.append(r)
        return results

    def rollback(self, transaction: str):
        if not isinstance(transaction, str) or len(transaction) != 32 or any(c not in '0123456789abcdef' for c in transaction):
            raise ValueError('无效事务 ID')
        with HostLock(self.transactions / 'write.lock'):
            receipt = self.transactions / (transaction + '.json')
            r = load_json(receipt, {})
            if r.get('status') != 'committed':
                raise ValueError('事务不可回滚')
            p = self.resolve(r['path'])
            if self.fingerprint(r['path']) != r['after']:
                raise RuntimeError('用户已修改文件，拒绝回滚覆盖')
            if r['before'] is None:
                p.unlink()
            else:
                snapshot = self.transactions / (transaction + '.snapshot')
                if digest(snapshot) != r['before']:
                    raise RuntimeError('恢复快照损坏')
                self._replace(p, snapshot.read_bytes(), r['after'])
            r['status'] = 'rolled_back'; atomic_json(receipt, r)
            return {'transaction': transaction, 'status': 'rolled_back'}
