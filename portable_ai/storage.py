"""Portable SQLite memory and privacy-conscious metadata audit log."""
from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class MemoryStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.dbfile = self.root / 'memory' / 'memory.sqlite3'
        self.dbfile.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL
            )''')

        with self.connect() as con:
            columns = {r[1] for r in con.execute('PRAGMA table_info(memories)')}
            for name, definition in [('project', "TEXT NOT NULL DEFAULT ''"), ('importance', 'INTEGER NOT NULL DEFAULT 3'), ('version', 'INTEGER NOT NULL DEFAULT 1')]:
                if name not in columns:
                    con.execute(f'ALTER TABLE memories ADD COLUMN {name} {definition}')
            con.execute('CREATE TABLE IF NOT EXISTS memory_versions (memory_id INTEGER, version INTEGER, content TEXT, category TEXT, project TEXT, importance INTEGER, updated_at TEXT, PRIMARY KEY(memory_id,version))')
            con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(content, category, project, content='memories', content_rowid='id')")
            con.executescript("""
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
              INSERT INTO memory_fts(rowid,content,category,project) VALUES(new.id,new.content,new.category,new.project);
            END;
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
              INSERT INTO memory_fts(memory_fts,rowid,content,category,project) VALUES('delete',old.id,old.content,old.category,old.project);
              INSERT INTO memory_fts(rowid,content,category,project) VALUES(new.id,new.content,new.category,new.project);
            END;
            """)
            con.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")

    @contextmanager
    def connect(self):
        con = sqlite3.connect(str(self.dbfile), timeout=5)
        try:
            # DELETE journal mode is easier to move/remount than persistent WAL files.
            con.execute('PRAGMA journal_mode=DELETE')
            con.execute('PRAGMA synchronous=FULL')
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def add(self, category: str, content: str, source='assistant_confirmed', project='', importance=3):
        if not content.strip() or len(content) > 4000:
            raise ValueError('记忆内容不能为空，且不得超过4000字。')
        if category not in {'preference', 'project', 'fact', 'decision', 'todo'}:
            raise ValueError('不支持的记忆类别')
        if not isinstance(project, str) or len(project) > 100 or type(importance) is not int or not 1 <= importance <= 5:
            raise ValueError('项目/重要性无效')
        with self.connect() as con:
            existing = con.execute('SELECT id FROM memories WHERE content=? AND category=? AND project=?', (content.strip(), category, project)).fetchone()
            if existing:
                return existing[0]
            cur = con.execute(
                'INSERT INTO memories (created_at,category,content,source,project,importance) VALUES (?,?,?,?,?,?)',
                (utc_now(), category, content.strip(), source, project, importance))
            new_id=cur.lastrowid
        self.backup()
        return new_id

    def backup(self):
        folder=self.root/'memory'/'backups'
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/('memory-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
        source=sqlite3.connect(str(self.dbfile))
        target=sqlite3.connect(str(path))
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        for old in sorted(folder.glob('memory-*.sqlite3'))[:-30]:
            old.unlink(missing_ok=True)
        return path

    def update(self, memory_id, content, expected_version):
        if not isinstance(content, str) or not content.strip() or len(content) > 4000:
            raise ValueError('记忆内容无效')
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            r = con.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
            if not r or r['version'] != expected_version:
                raise ValueError('记忆版本冲突')
            con.execute('INSERT INTO memory_versions VALUES(?,?,?,?,?,?,?)',
                        (r['id'], r['version'], r['content'], r['category'], r['project'], r['importance'], utc_now()))
            con.execute('UPDATE memories SET content=?,version=version+1 WHERE id=?', (content.strip(), memory_id))
        self.backup()
        return expected_version + 1

    def versions(self, memory_id):
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute('SELECT * FROM memory_versions WHERE memory_id=? ORDER BY version', (memory_id,))]

    def search(self, query: str, limit: int = 6, project=None) -> list[dict]:
        query = query.strip()[:100]
        limit = max(1, min(int(limit), 100))
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            where, args = [], []
            if project is not None:
                where.append('m.project=?'); args.append(project)
            if query:
                # Literal quoted FTS phrase prevents FTS query syntax injection.
                # LIKE fallback also supports substrings and unsegmented CJK text.
                phrase = '"' + query.replace('"', '""') + '"'
                escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                where.append("(m.id IN (SELECT rowid FROM memory_fts WHERE memory_fts MATCH ?) OR m.content LIKE ? ESCAPE '\\')")
                args.extend([phrase, '%' + escaped + '%'])
            sql = 'SELECT m.* FROM memories m' + (' WHERE ' + ' AND '.join(where) if where else '')
            sql += ' ORDER BY importance DESC,id DESC LIMIT ?'
            return [dict(row) for row in con.execute(sql, [*args, limit])]


class Audit:
    def __init__(self, root: Path):
        self.file = Path(root) / 'logs' / 'actions.jsonl'
        self.file.parent.mkdir(parents=True, exist_ok=True)

    def record(self, operation: str, status: str, summary: str = ''):
        # No API keys, file contents, prompts or proxy passwords are persisted.
        entry = {'utc': utc_now(), 'operation': operation, 'status': status,
                 'summary': summary[:300]}
        with self.file.open('a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            f.flush()
            os.fsync(f.fileno())
