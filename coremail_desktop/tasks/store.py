"""Encrypted task store: the formal record of the task list (unlike the mail cache it is
never thrown away or rebuilt from the server).

Same scheme as app/cache.py: SQLite holds AES-GCM ciphertext only, with the row's identity
as associated data, under a 256-bit key wrapped by the Windows current-user DPAPI. All
tasks are kept decrypted in memory (a few hundred rows). Every change is one transaction
that is read back before it commits.
"""
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from coremail_desktop.errors import AppError

SCHEMA = ('CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, rev INTEGER NOT NULL, blob BLOB NOT NULL)',
          'CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, eid TEXT NOT NULL, task_id INTEGER NOT NULL,'
          ' at REAL NOT NULL, review TEXT NOT NULL, blob BLOB NOT NULL)',
          'CREATE TABLE IF NOT EXISTS links(id INTEGER PRIMARY KEY AUTOINCREMENT, lid TEXT NOT NULL, task_id INTEGER NOT NULL,'
          ' review TEXT NOT NULL, blob BLOB NOT NULL)',
          'CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, blob BLOB)',
          'CREATE INDEX IF NOT EXISTS events_task ON events(task_id, seq)',
          'CREATE INDEX IF NOT EXISTS links_task ON links(task_id)')
REVIEWS = ('confirmed', 'pending', 'rejected', 'accepted')  # accepted: an AI suggestion that was run.


class TaskStore:
    def __init__(self, path, protector):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key_path = self.path.with_suffix('.key')
        try:
            if key_path.exists():
                key = protector.unprotect(key_path.read_bytes())
            elif self.path.exists() and self.path.stat().st_size:
                raise AppError('TASK_STORE_LOCKED')  # A store without its key: never start a new one over it.
            else:
                key = AESGCM.generate_key(256)
                key_path.write_bytes(protector.protect(key))
        except AppError:
            raise
        except Exception:
            raise AppError('TASK_STORE_LOCKED') from None
        self._aead = AESGCM(key)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('PRAGMA synchronous=FULL')
        for ddl in SCHEMA:
            self._db.execute(ddl)
        self.tasks, self.revs = {}, {}
        try:
            for task_id, rev, blob in self._db.execute('SELECT id, rev, blob FROM tasks'):
                self.tasks[task_id] = self._open(blob, f'task:{task_id}')
                self.revs[task_id] = rev
        except Exception:
            self._db.close()
            raise AppError('TASK_STORE_LOCKED') from None  # The formal record is never silently skipped.

    def _seal(self, value, aad):
        nonce = os.urandom(12)
        return nonce + self._aead.encrypt(nonce, json.dumps(value, ensure_ascii=False).encode('utf-8'), aad.encode('utf-8'))

    def _open(self, blob, aad):
        return json.loads(self._aead.decrypt(blob[:12], blob[12:], aad.encode('utf-8')).decode('utf-8'))

    # Reading -------------------------------------------------------------
    def events(self, task_id=None, review=None):
        """[(seq, review, event)] oldest first, of one task or all; review narrows by state."""
        sql, args = 'SELECT seq, eid, task_id, review, blob FROM events', []
        where = []
        if task_id is not None:
            where.append('task_id=?'); args.append(int(task_id))
        if review:
            where.append('review=?'); args.append(review)
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        with self._lock:
            rows = self._db.execute(sql + ' ORDER BY seq', args).fetchall()
        return [(seq, state, {**self._open(blob, f'event:{tid}:{eid}'), 'task': tid}) for seq, eid, tid, state, blob in rows]

    def event(self, seq):
        found = [e for e in self.events_by_seq((seq,))]
        return found[0] if found else None

    def events_by_seq(self, seqs):
        with self._lock:
            rows = [self._db.execute('SELECT seq, eid, task_id, review, blob FROM events WHERE seq=?', (int(s),)).fetchone() for s in seqs]
        return [(seq, state, {**self._open(blob, f'event:{tid}:{eid}'), 'task': tid}) for seq, eid, tid, state, blob in filter(None, rows)]

    def pending_count(self):
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM events WHERE review='pending'").fetchone()[0]

    def links(self, task_id=None):
        sql, args = 'SELECT id, lid, task_id, review, blob FROM links', ()
        if task_id is not None:
            sql, args = sql + ' WHERE task_id=?', (int(task_id),)
        with self._lock:
            rows = self._db.execute(sql + ' ORDER BY id', args).fetchall()
        return [{**self._open(blob, f'link:{tid}:{lid}'), 'id': lid, 'task': tid, 'review': state} for _, lid, tid, state, blob in rows]

    def get(self, key, default=None):
        with self._lock:
            found = self._db.execute('SELECT blob FROM kv WHERE k=?', (key,)).fetchone()
        return self._open(found[0], 'kv:' + key) if found else default

    # Writing -------------------------------------------------------------
    def commit(self, tasks=(), events=(), reviews=(), links=(), unlink=(), kv=None, expect=None, replace=False):
        """One transaction. tasks: full task dicts to write; events: (task_id, review, event);
        reviews: (seq, review); links: link dicts; unlink: link ids; kv: {key: value};
        expect: {task_id: rev} the caller read (else TASK_CONFLICT); replace: start from empty
        (an import or a restore). Written rows are read back and compared before COMMIT."""
        tasks, events = list(tasks), list(events)
        with self._lock:
            for task_id, rev in (expect or {}).items():
                if self.revs.get(task_id) != rev:
                    raise AppError('TASK_CONFLICT')
            revs = {} if replace else dict(self.revs)
            self._db.execute('BEGIN IMMEDIATE')
            try:
                if replace:
                    for table in ('tasks', 'events', 'links'):
                        self._db.execute(f'DELETE FROM {table}')
                written = {}
                for task in tasks:
                    task_id = int(task['id'])
                    revs[task_id] = revs.get(task_id, 0) + 1
                    self._db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?,?)', (task_id, revs[task_id], self._seal(task, f'task:{task_id}')))
                    written[task_id] = task
                at = time.time()
                for task_id, review, event in events:
                    eid = uuid.uuid4().hex
                    event = {k: v for k, v in event.items() if k != 'task'}
                    self._db.execute('INSERT INTO events(eid, task_id, at, review, blob) VALUES (?,?,?,?,?)',
                                     (eid, int(task_id), event.get('at') or at, review, self._seal({'at': at, **event}, f'event:{int(task_id)}:{eid}')))
                for seq, review in reviews:
                    if review not in REVIEWS:
                        raise AppError('TASK_INVALID')
                    self._db.execute('UPDATE events SET review=? WHERE seq=?', (review, int(seq)))
                for link in links:
                    lid = link.get('id') or uuid.uuid4().hex
                    task_id = int(link['task'])
                    body = {k: v for k, v in link.items() if k not in ('id', 'task', 'review')}
                    self._db.execute('INSERT INTO links(lid, task_id, review, blob) VALUES (?,?,?,?)',
                                     (lid, task_id, link.get('review') or 'confirmed', self._seal(body, f'link:{task_id}:{lid}')))
                for lid in unlink:
                    self._db.execute('DELETE FROM links WHERE lid=?', (lid,))
                for key, value in (kv or {}).items():
                    self._db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)', (key, self._seal(value, 'kv:' + key)))
                for task_id, task in written.items():  # Read back what this transaction wrote.
                    rev, blob = self._db.execute('SELECT rev, blob FROM tasks WHERE id=?', (task_id,)).fetchone()
                    if rev != revs[task_id] or self._open(blob, f'task:{task_id}') != json.loads(json.dumps(task, ensure_ascii=False)):
                        raise AppError('TASK_VERIFY_FAILED')
                self._db.execute('COMMIT')
            except BaseException:
                self._db.execute('ROLLBACK')
                raise
            if replace:
                self.tasks = {}
            for task_id, task in written.items():
                self.tasks[task_id] = json.loads(json.dumps(task, ensure_ascii=False))
            self.revs = revs

    def set(self, key, value):
        self.commit(kv={key: value})

    def on_disk(self):
        """Every task as a new connection reads it from the file (the check after a save)."""
        db = sqlite3.connect(str(self.path))
        try:
            return {task_id: (rev, self._open(blob, f'task:{task_id}')) for task_id, rev, blob in db.execute('SELECT id, rev, blob FROM tasks')}
        finally:
            db.close()

    def backup(self, target):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            dest = sqlite3.connect(str(target))
            try:
                self._db.backup(dest)
            finally:
                dest.close()
        key = self.path.with_suffix('.key')
        target.with_suffix('.key').write_bytes(key.read_bytes())

    def close(self):
        with self._lock:
            self._db.close()
