"""Encrypted store for 理解卡片, correction rules and 工作记忆 facts, one per account beside the mail cache.

Same scheme as app/cache.py: SQLite holds AES-GCM ciphertext only (the row's identity as
associated data) under a 256-bit key wrapped by the Windows current-user DPAPI. Cards are
kept decrypted in memory (a few thousand at most) so the list can look them up at once.
Unlike the mail cache it is not rebuilt from the server: a card cost an AI call. Facts have
two plain columns besides their id: an HMAC of the project name (pkey) and their state.
"""
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from coremail_desktop.errors import AppError

SCHEMA = ('CREATE TABLE IF NOT EXISTS cards(mid TEXT PRIMARY KEY, at REAL NOT NULL, blob BLOB NOT NULL)',
          'CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY, blob BLOB NOT NULL)',
          'CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, blob BLOB)',
          'CREATE TABLE IF NOT EXISTS facts(id TEXT PRIMARY KEY, pkey TEXT NOT NULL, state TEXT NOT NULL, at REAL NOT NULL, blob BLOB NOT NULL)')
CARD_DAYS = 30  # Cards of mail no longer in the cache are dropped after this long.


class MemoryStore:
    def __init__(self, path, protector):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key_path = self.path.with_suffix('.key')
        if not key_path.exists() and _has_rows(self.path):
            # Records without their key: never start over with a new key over them (V5).
            raise AppError('MEMORY_KEY_MISSING')
        try:
            if key_path.exists():
                key = protector.unprotect(key_path.read_bytes())
            else:
                key = AESGCM.generate_key(256)
                key_path.write_bytes(protector.protect(key))
            self._aead = AESGCM(key)
            self._pkey = hashlib.sha256(b'hongyan-memory-project' + key).digest()
            self._lock = threading.RLock()
            self._db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
            self._db.execute('PRAGMA journal_mode=WAL')
            for ddl in SCHEMA:
                self._db.execute(ddl)
        except Exception:
            raise AppError('MEMORY_UNAVAILABLE') from None
        self.cards, self.rules, self.facts = {}, {}, {}
        stored = sum(self._db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in ('facts', 'cards', 'rules'))
        for fact_id, blob in self._db.execute('SELECT id, blob FROM facts ORDER BY at'):
            try:
                self.facts[fact_id] = self._open(blob, 'fact:' + fact_id)
            except Exception:
                continue
        for mid, blob in self._db.execute('SELECT mid, blob FROM cards'):
            try:
                self.cards[mid] = self._open(blob, 'card:' + mid)
            except Exception:
                continue  # A corrupt card is simply made again.
        for rule_id, blob in self._db.execute('SELECT id, blob FROM rules'):
            try:
                self.rules[rule_id] = self._open(blob, 'rule:' + rule_id)
            except Exception:
                continue

        if stored and not (self.facts or self.cards or self.rules):
            # Not one record opens: the key is not theirs. Shown as an error, never as no memory (V5).
            self._db.close()
            raise AppError('MEMORY_KEY_MISSING')

    def _seal(self, value, aad):
        nonce = os.urandom(12)
        return nonce + self._aead.encrypt(nonce, json.dumps(value, ensure_ascii=False).encode('utf-8'), aad.encode('utf-8'))

    def _open(self, blob, aad):
        return json.loads(self._aead.decrypt(blob[:12], blob[12:], aad.encode('utf-8')).decode('utf-8'))

    # Cards ---------------------------------------------------------------
    def card(self, mid):
        with self._lock:
            return self.cards.get(mid)

    def put_card(self, mid, card):
        with self._lock:
            self.cards[mid] = card
            self._db.execute('INSERT OR REPLACE INTO cards VALUES (?,?,?)', (mid, time.time(), self._seal(card, 'card:' + mid)))

    def drop_cards(self, keep):
        """Forget cards whose mail is gone (keep(mid) is False) and that are older than CARD_DAYS."""
        cutoff = time.time() - CARD_DAYS * 86400
        with self._lock:
            old = [mid for mid, at in self._db.execute('SELECT mid, at FROM cards WHERE at < ?', (cutoff,)) if not keep(mid)]
            for mid in old:
                self.cards.pop(mid, None)
                self._db.execute('DELETE FROM cards WHERE mid=?', (mid,))
        return len(old)

    # Rules ---------------------------------------------------------------
    def put_rule(self, rule):
        with self._lock:
            self.rules[rule['id']] = rule
            self._db.execute('INSERT OR REPLACE INTO rules VALUES (?,?)', (rule['id'], self._seal(rule, 'rule:' + rule['id'])))

    def delete_rule(self, rule_id):
        with self._lock:
            self.rules.pop(rule_id, None)
            self._db.execute('DELETE FROM rules WHERE id=?', (rule_id,))

    # Facts (工作记忆) ---------------------------------------------------------
    def project_key(self, project):
        return hmac.new(self._pkey, project.casefold().encode('utf-8'), 'sha256').hexdigest()[:32]

    def put_fact(self, fact):
        with self._lock:
            self.facts[fact['id']] = fact
            self._db.execute('INSERT OR REPLACE INTO facts VALUES (?,?,?,?,?)', (fact['id'], self.project_key(fact['project']), fact['state'],
                                                                                time.time(), self._seal(fact, 'fact:' + fact['id'])))

    # Small settings --------------------------------------------------------
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

    def clear(self):
        """清除理解与记忆: every card, rule, fact and setting of this account."""
        with self._lock:
            for table in ('cards', 'rules', 'kv', 'facts'):
                self._db.execute(f'DELETE FROM {table}')
            self.cards, self.rules, self.facts = {}, {}, {}

    def close(self):
        with self._lock:
            self._db.close()


def _has_rows(path):
    """Whether a memory database already holds records (read-only; a missing file holds none)."""
    if not Path(path).exists():
        return False
    try:
        db = sqlite3.connect(f'file:{Path(path).as_posix()}?mode=ro', uri=True)
        try:
            return any(db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()
                       for table in ('facts', 'cards', 'rules')
                       if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())
        finally:
            db.close()
    except sqlite3.Error:
        return True  # Unreadable: keep it as it is rather than start over.
