"""Encrypted local mail cache: instant start, offline reading and local search.

SQLite holds only AES-GCM ciphertext; the per-account key is wrapped with the
Windows current-user DPAPI. Plaintext summaries live in memory for searching.
"""
import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BODY_BUDGET = 300 * 1024 * 1024


def date_ts(text):
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(text)[:19], fmt).timestamp()
        except ValueError:
            continue
    return 0.0


class MailCache:
    def __init__(self, path, protector):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key_path = self.path.with_suffix('.key')
        if key_path.exists():
            key = protector.unprotect(key_path.read_bytes())
        else:
            key = AESGCM.generate_key(256)
            key_path.write_bytes(protector.protect(key))
        self._aead = AESGCM(key)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._db.execute('PRAGMA journal_mode=WAL')
        for ddl in ('CREATE TABLE IF NOT EXISTS messages(mid TEXT PRIMARY KEY, fid TEXT, ts REAL, blob BLOB)',
                    'CREATE TABLE IF NOT EXISTS bodies(mid TEXT PRIMARY KEY, size INTEGER, at REAL, blob BLOB)',
                    'CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, blob BLOB)',
                    'CREATE INDEX IF NOT EXISTS messages_fid ON messages(fid, ts)'):
            self._db.execute(ddl)
        self._rows = {}
        for mid, blob in self._db.execute('SELECT mid, blob FROM messages'):
            try:
                self._rows[mid] = self._open(blob, mid)
            except Exception:
                continue  # A corrupt row is refetched on the next sync.

    def _seal(self, value, aad):
        nonce = os.urandom(12)
        data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
        return nonce + self._aead.encrypt(nonce, data, aad.encode('utf-8'))

    def _open(self, blob, aad, raw=False):
        data = self._aead.decrypt(blob[:12], blob[12:], aad.encode('utf-8'))
        return data if raw else json.loads(data.decode('utf-8'))

    # Summaries -----------------------------------------------------------
    def put_summaries(self, fid, rows, window_complete=False):
        """Store a server page. A complete first page also drops rows the server
        no longer has in the newest part of the folder (moved or deleted)."""
        with self._lock:
            ids = {row['id'] for row in rows}
            if window_complete:
                oldest = min((row['ts'] for row in rows), default=None)
                stale = [mid for mid, row in self._rows.items() if row['fid'] == fid and mid not in ids
                         and (oldest is None or row['ts'] >= oldest)]
                for mid in stale:
                    self.remove(mid)
            self._db.execute('BEGIN')
            try:
                for row in rows:
                    self._rows[row['id']] = row
                    self._db.execute('INSERT OR REPLACE INTO messages VALUES (?,?,?,?)',
                                     (row['id'], fid, row['ts'], self._seal(row, row['id'])))
                self._db.execute('COMMIT')
            except Exception:
                self._db.execute('ROLLBACK')
                raise

    def summaries(self, fid, offset=0, limit=50, unread_only=False):
        with self._lock:
            rows = [r for r in self._rows.values() if r['fid'] == fid and (not unread_only or not r['flags'].get('read'))]
        rows.sort(key=lambda r: r['ts'], reverse=True)
        return rows[offset:offset + limit], len(rows)

    def summary(self, mid):
        with self._lock:
            return self._rows.get(mid)

    def known_ids(self, fid):
        with self._lock:
            return {mid for mid, r in self._rows.items() if r['fid'] == fid}

    def update(self, mid, **changes):
        with self._lock:
            row = self._rows.get(mid)
            if row is None:
                return
            row = {**row, **changes}
            if 'flags' in changes:
                row['flags'] = {**self._rows[mid]['flags'], **changes['flags']}
            self._rows[mid] = row
            self._db.execute('INSERT OR REPLACE INTO messages VALUES (?,?,?,?)', (mid, row['fid'], row['ts'], self._seal(row, mid)))

    def remove(self, mid):
        with self._lock:
            self._rows.pop(mid, None)
            self._db.execute('DELETE FROM messages WHERE mid=?', (mid,))

    def search(self, query, limit=200):
        terms = [t for t in query.casefold().split() if t]
        if not terms:
            return []
        with self._lock:
            rows = list(self._rows.values())
        hits = []
        for row in rows:
            hay = ' '.join((row['subject'], row['from'], row.get('to', ''), row.get('preview', ''))).casefold()
            if all(t in hay for t in terms):
                hits.append(row)
        hits.sort(key=lambda r: r['ts'], reverse=True)
        return hits[:limit]

    def addresses(self):
        """Every From/To string seen, for local recipient suggestions."""
        with self._lock:
            return [(row['from'], row.get('to', ''), row['fid'], row['ts']) for row in self._rows.values()]

    # Bodies --------------------------------------------------------------
    def get_body(self, mid):
        with self._lock:
            found = self._db.execute('SELECT blob FROM bodies WHERE mid=?', (mid,)).fetchone()
            if not found:
                return None
            try:
                data = self._open(found[0], 'body:' + mid, raw=True)
            except Exception:
                self._db.execute('DELETE FROM bodies WHERE mid=?', (mid,))
                return None
            self._db.execute('UPDATE bodies SET at=? WHERE mid=?', (time.time(), mid))
            return data

    def put_body(self, mid, raw):
        with self._lock:
            self._db.execute('INSERT OR REPLACE INTO bodies VALUES (?,?,?,?)',
                             (mid, len(raw), time.time(), self._seal(bytes(raw), 'body:' + mid)))
            total = self._db.execute('SELECT COALESCE(SUM(size),0) FROM bodies').fetchone()[0]
            if total > BODY_BUDGET:
                for old_mid, size in self._db.execute('SELECT mid, size FROM bodies ORDER BY at').fetchall():
                    if total <= BODY_BUDGET * 0.8:
                        break
                    self._db.execute('DELETE FROM bodies WHERE mid=?', (old_mid,))
                    total -= size

    # Small encrypted settings -------------------------------------------
    def get(self, key, default=None):
        with self._lock:
            found = self._db.execute('SELECT blob FROM kv WHERE k=?', (key,)).fetchone()
        if not found:
            return default
        try:
            return self._open(found[0], 'kv:' + key)
        except Exception:
            return default

    def set(self, key, value):
        with self._lock:
            self._db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)', (key, self._seal(value, 'kv:' + key)))

    def close(self):
        with self._lock:
            self._db.close()
