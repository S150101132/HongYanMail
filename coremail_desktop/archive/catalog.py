"""Encrypted catalog of the local archive: one row per archived message with its
envelope, attachment names and plain text for searching, and where its record lives.

Like the mail cache, SQLite holds only AES-GCM ciphertext under a DPAPI-wrapped key and
the rows are kept in memory for searching (a few thousand messages). Originals are never
copied: reading one goes back to its pack (reader.read_record). A pack whose size and
modification time are unchanged is skipped on the next scan; a changed one is redone.
"""
import base64
import binascii
import hashlib
import json
import os
import quopri
import re
import sqlite3
import threading
from datetime import datetime
from email import policy
from email.parser import BytesHeaderParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from coremail_desktop.archive import reader
from coremail_desktop.errors import AppError
from coremail_desktop.mime.parser import MAX_DEPTH, _raw_body, _raw_children

TEXT_CHARS = 12000
PREVIEW_CHARS = 200
PACK_NAME = re.compile(r'^[0-9A-Za-z_-]{1,40}\.cmpack$')
SMIME_TYPES = ('application/pkcs7-mime', 'application/x-pkcs7-mime')
SIGNATURE_TYPES = ('application/pkcs7-signature', 'application/x-pkcs7-signature')


def archive_dir(folder):
    """The account folder the user chose, or its archmails folder itself."""
    folder = Path(folder)
    return folder if folder.name.lower() == 'archmails' else folder / 'archmails'


def _header(head, name):
    return ', '.join(str(v) for v in head.get_all(name) or ())


def _date(text):
    try:
        value = parsedate_to_datetime(text)
        if value.tzinfo is not None:
            value = value.astimezone()  # Local time, as server rows show it.
        return value.strftime('%Y-%m-%d %H:%M:%S'), value.timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        return '', 0.0


def _headers(part):
    """The headers of a MIME part, parsed from its header block only (the standard
    parser would also read every line of a large attachment)."""
    found = re.search(rb'\r\n\r\n|\n\n|\r\r', part)
    block = part[:found.start()] if found else part
    return BytesHeaderParser(policy=policy.default).parsebytes(block + b'\r\n\r\n')


def _parts(part, depth=0):
    """(headers, raw part) of each leaf, split on boundaries without decoding bodies."""
    head = _headers(part)
    kind = head.get_content_type()
    if kind.startswith('multipart/') and depth < MAX_DEPTH:
        for child in _raw_children(part, head.get_boundary()):
            yield from _parts(child, depth + 1)
    else:
        yield head, part


def _payload(head, part):
    body = _raw_body(part)
    encoding = str(head.get('content-transfer-encoding') or '').strip().lower()
    try:
        if encoding == 'base64':
            data = re.sub(rb'[^A-Za-z0-9+/]', b'', body)
            body = base64.b64decode(data[:len(data) - len(data) % 4])
        elif encoding == 'quoted-printable':
            body = quopri.decodestring(body)
    except (binascii.Error, ValueError):
        return ''
    try:
        return body.decode(head.get_content_charset() or 'utf-8', 'replace')
    except LookupError:
        return body.decode('utf-8', 'replace')


def describe(raw, mid):
    """Envelope, attachment names and plain text of one original. S/MIME-encrypted mail
    is described by its envelope only (its text is not decrypted for the catalog)."""
    # A light walk of the parts, not the reader's parse_mime: that also sanitizes the
    # HTML for display, and the standard parser reads attachments line by line; together
    # about 1.5 s a message on the real archive.
    from coremail_desktop.ai.summary import _tidy, visible_text
    from coremail_desktop.crypto.models import CryptoError
    from coremail_desktop.crypto.signature import unwrap_signed
    head = _headers(raw)
    date, ts = _date(_header(head, 'date'))
    sender = getaddresses([_header(head, 'from')])
    row = {'id': mid, 'subject': _header(head, 'subject'), 'from': _header(head, 'from'), 'to': _header(head, 'to'),
           'cc': _header(head, 'cc'), 'date': date, 'ts': ts, 'messageId': _header(head, 'message-id').strip('<> '),
           'size': len(raw), 'encrypted': False, 'attachments': [], 'text': '', 'preview': ''}
    content = raw
    if head.get_content_type() in SMIME_TYPES:  # Opaque-signed or encrypted.
        try:
            signed = unwrap_signed(raw, sender[0][1] if sender else '')
            if signed is not None and signed[0]:
                content = signed[0]
        except CryptoError:
            pass
        # Judged from the headers: detect_envelope_format parses the whole message and
        # refuses very large ones (8 archived mails of 28-69 MB, 2026-10-20).
        if content is raw or _headers(content).get_content_type() in SMIME_TYPES + ('multipart/encrypted',):
            row['encrypted'] = True
            return row
    plain = html = ''
    names = []
    for part_head, part in _parts(content):
        kind, name = part_head.get_content_type(), part_head.get_filename()
        if kind in SIGNATURE_TYPES:
            continue
        if name or part_head.get_content_disposition() == 'attachment' or not kind.startswith('text/'):
            names.append(str(name or ''))
        elif kind == 'text/plain' and not plain:
            plain = _payload(part_head, part)
        elif kind == 'text/html' and not html:
            html = _payload(part_head, part)
    text = _tidy(plain or visible_text(html))
    row.update(attachments=[n for n in names if n][:50], text=text[:TEXT_CHARS],
               preview=re.sub(r'\s+', ' ', text[:PREVIEW_CHARS * 2]).strip()[:PREVIEW_CHARS])
    return row


class ArchiveCatalog:
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
        for ddl in ('CREATE TABLE IF NOT EXISTS packs(name TEXT PRIMARY KEY, blob BLOB)',
                    'CREATE TABLE IF NOT EXISTS mails(mid TEXT PRIMARY KEY, pack TEXT, blob BLOB)',
                    'CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, blob BLOB)'):
            self._db.execute(ddl)
        self.rows, self.packs = {}, {}
        for name, blob in self._db.execute('SELECT name, blob FROM packs'):
            try:
                self.packs[name] = self._open(blob, 'pack:' + name)
            except Exception:
                continue  # Rescanned next time.
        for mid, blob in self._db.execute('SELECT mid, blob FROM mails'):
            try:
                self.rows[mid] = self._open(blob, mid)
            except Exception:
                self.packs.pop(self._db.execute('SELECT pack FROM mails WHERE mid=?', (mid,)).fetchone()[0], None)

    def _seal(self, value, aad):
        nonce = os.urandom(12)
        return nonce + self._aead.encrypt(nonce, json.dumps(value, ensure_ascii=False).encode('utf-8'), aad.encode('utf-8'))

    def _open(self, blob, aad):
        return json.loads(self._aead.decrypt(blob[:12], blob[12:], aad.encode('utf-8')).decode('utf-8'))

    def get(self, key, default=None):
        with self._lock:
            found = self._db.execute('SELECT blob FROM kv WHERE k=?', (key,)).fetchone()
        try:
            return self._open(found[0], 'kv:' + key) if found else default
        except Exception:
            return default

    def set(self, key, value):
        with self._lock:
            self._db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)', (key, self._seal(value, 'kv:' + key)))

    def close(self):
        with self._lock:
            self._db.close()

    # Scanning ----------------------------------------------------------------
    def _replace_pack(self, name, info, rows):
        with self._lock:
            self._db.execute('BEGIN')
            try:
                self._db.execute('DELETE FROM mails WHERE pack=?', (name,))
                if info is None:
                    self._db.execute('DELETE FROM packs WHERE name=?', (name,))
                else:
                    for row in rows:
                        self._db.execute('INSERT OR REPLACE INTO mails VALUES (?,?,?)', (row['id'], name, self._seal(row, row['id'])))
                    self._db.execute('INSERT OR REPLACE INTO packs VALUES (?,?)', (name, self._seal(info, 'pack:' + name)))
                self._db.execute('COMMIT')
            except Exception:
                self._db.execute('ROLLBACK')
                raise
            for mid in [m for m, r in self.rows.items() if r['pack'] == name]:
                del self.rows[mid]
            if info is None:
                self.packs.pop(name, None)
            else:
                self.packs[name] = info
                for row in rows:
                    self.rows.setdefault(row['id'], row)  # The same original in two packs: the first stays.

    def scan(self, folder, progress=lambda done, total: None, cancelled=lambda: False):
        """Bring the catalog up to date with folder's archmails. -> counts and failures
        (pack and record names with a fixed reason, never content)."""
        directory = archive_dir(folder)
        if not directory.is_dir():
            raise AppError('ARCHIVE_NOT_FOUND')
        names = sorted(p.name for p in directory.iterdir() if PACK_NAME.match(p.name) and p.is_file())
        for gone in set(self.packs) - set(names):
            self._replace_pack(gone, None, [])
        result = {'packs': len(names), 'scanned': 0, 'skipped': 0, 'failed': []}
        for index, name in enumerate(names):
            if cancelled():
                break
            progress(index, len(names))
            path = directory / name
            try:
                stat = path.stat()
                stamp = [stat.st_size, stat.st_mtime_ns]
                known = self.packs.get(name)
                # Unchanged: skipped, its unreadable records too (they would fail the same way).
                if known and known['stamp'] == stamp:
                    result['skipped'] += 1
                    continue
                data, stamp = reader.read_pack(path)
                rows, failed = [], []
                for entry in reader.entries(data):
                    if 'error' in entry:
                        failed.append({'pack': name, 'record': entry['name'], 'reason': 'directory'})
                        continue
                    try:
                        raw = reader.decode(data[entry['offset']:entry['offset'] + entry['length']])
                        mid = 'arc:' + hashlib.sha256(raw).hexdigest()[:24]
                        row = describe(raw, mid)
                        row.update(pack=name, offset=entry['offset'], length=entry['length'],
                                   sha256=hashlib.sha256(raw).hexdigest(), stamp=list(stamp))
                        rows.append(row)
                    except AppError as error:
                        failed.append({'pack': name, 'record': entry['name'], 'reason': error.code})
                    except Exception:
                        failed.append({'pack': name, 'record': entry['name'], 'reason': 'parse'})
                self._replace_pack(name, {'stamp': list(stamp), 'count': len(rows), 'failed': len(failed)}, rows)
                result['failed'] += failed
                result['scanned'] += 1
            except AppError as error:
                result['failed'].append({'pack': name, 'record': '', 'reason': error.code})
            except OSError:
                result['failed'].append({'pack': name, 'record': '', 'reason': 'ARCHIVE_NOT_FOUND'})
        progress(len(names), len(names))
        result['messages'] = len(self.rows)
        result['at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
        # Records still unreadable in skipped packs count too, not only this run's.
        stale = sum(info.get('failed', 0) for name, info in self.packs.items() if name not in {f['pack'] for f in result['failed']})
        self.set('lastScan', {k: v for k, v in result.items() if k != 'failed'} | {'failedCount': len(result['failed']) + stale})
        return result

    # Reading -------------------------------------------------------------------
    def raw(self, folder, mid):
        """The original, read again from its pack and checked against the catalog."""
        row = self.rows.get(mid)
        if row is None:
            raise AppError('MESSAGE_UNAVAILABLE')
        raw = reader.read_record(archive_dir(folder) / row['pack'], row['offset'], row['length'], row['stamp'])
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise AppError('ARCHIVE_CHANGED')
        return raw

    def summary(self, mid):
        """The row as the mail list shows a message (no text)."""
        row = self.rows.get(mid)
        if row is None:
            return None
        name, addr = (getaddresses([row['from']]) or [('', '')])[0]
        return {'id': mid, 'fid': 'archive', 'subject': row['subject'], 'from': row['from'], 'fromName': name or addr,
                'fromEmail': addr, 'to': row['to'], 'date': row['date'], 'ts': row['ts'], 'preview': row['preview'],
                'size': row['size'], 'flags': {'read': True, 'attached': bool(row['attachments'])}, 'archived': True,
                'encrypted': row['encrypted'], 'scheduleAt': None, 'tags': []}

    def listing(self, offset=0, limit=50):
        with self._lock:
            rows = sorted(self.rows.values(), key=lambda r: r['ts'], reverse=True)
        return [self.summary(r['id']) for r in rows[offset:offset + limit]], len(rows)

    def search(self, terms, sender='', since=None, until=None, subject_only=False, limit=200):
        """Rows whose subject, people, attachment names or text contain every term."""
        terms = [t.casefold() for t in terms if t]
        sender = sender.casefold()
        with self._lock:
            rows = list(self.rows.values())
        found = []
        for row in rows:
            if since is not None and row['ts'] < since or until is not None and row['ts'] >= until:
                continue
            if sender and sender not in row['from'].casefold():
                continue
            hay = row['subject'].casefold() if subject_only else '\n'.join(
                (row['subject'], row['from'], row['to'], row['cc'], ' '.join(row['attachments']), row['text'])).casefold()
            if all(t in hay for t in terms):
                found.append(row)
        # Most relevant first: terms in the subject, then in attachment names; then newest.
        # (Common words such as a system name match dozens of weekly reports.)
        def score(row):
            subject, names = row['subject'].casefold(), ' '.join(row['attachments']).casefold()
            return sum(3 if t in subject else 2 if t in names else 0 for t in terms)
        found.sort(key=lambda r: (score(r), r['ts']), reverse=True)
        return [self.summary(r['id']) for r in found[:limit]], len(found)
