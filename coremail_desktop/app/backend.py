"""Qt-free application backend for the daily-use client.

Every public method takes and returns JSON-compatible values so the web UI can
call it through the bridge. Calls are made from one worker thread, in order.
"""
import base64
import functools
import hashlib
import html
import json
import re
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr
from pathlib import Path

from coremail_desktop.app.cache import MailCache, date_ts
from coremail_desktop.compose.attachments import validate_attachments
from coremail_desktop.compose.inline import extract_inline
from coremail_desktop.compose.service import build_reply
from coremail_desktop.errors import AppError
from coremail_desktop.models import AccountConfig, DraftAttachment, DraftDocument
from coremail_desktop.mime.parser import parse_mime

DEFAULT_PREFS = {'theme': 'system', 'signature': '', 'pollSeconds': 60, 'notify': True,
                 'density': 'comfortable', 'markReadOnOpen': True, 'readingPane': 'right', 'threads': False,
                 'autostart': False, 'sidebarWidth': 232, 'listWidth': 400, 'undoSeconds': 10}
UNDO_CHOICES = (0, 5, 10, 20, 30)  # 撤销发送 delay in seconds; 0 sends at once.
NETWORK_CODES = ('NETWORK_ERROR', 'TLS_ERROR')
MAX_EML_BYTES = 40 * 1024 * 1024
# Column widths the page may store (px); the page enforces the same bounds while dragging.
WIDTH_LIMITS = {'sidebarWidth': (180, 400), 'listWidth': (280, 900)}
TEMPLATE_LIMIT = 100
# Coremail system folder ids. Used only for icons and ordering; the name shown is
# always the server's own folder name.
SYSTEM_ROLES = {'1': 'inbox', '2': 'drafts', '3': 'sent', '4': 'trash', '5': 'spam', '6': 'virus'}
REAUTH_CODES = ('SESSION_EXPIRED', 'NOT_AUTHENTICATED')
FOLDER_ORDER = {'inbox': 0, 'drafts': 2, 'sent': 3, 'trash': 8, 'spam': 7, 'virus': 9}
MAX_FOLDER_DEPTH = 5  # Nesting levels the server allows (index.jsp maxFolderNestLevel).
SENDER_LIST_LIMIT = 500
# Attachment preview: only these, recognised by their bytes, never by name alone.
# SVG and HTML are not images here (they can carry script); Office files open externally.
PREVIEW_IMAGE_LIMIT = 20 * 1024 * 1024
PREVIEW_PDF_LIMIT = 50 * 1024 * 1024
PREVIEW_TEXT_LIMIT = 2 * 1024 * 1024
PREVIEW_TEXT = ('.txt', '.log', '.csv', '.tsv', '.md', '.json', '.xml', '.ini', '.cfg', '.conf', '.yaml', '.yml', '.eml')


def is_attached_mail(name, media_type=''):
    return str(media_type or '').lower() == 'message/rfc822' or str(name or '').lower().endswith('.eml')


def preview_kind(name, data):
    """'image/png' ... for a raster image, 'pdf', 'text' or None, from the content."""
    head = bytes(data[:16])
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if head.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    if head.startswith(b'%PDF-'):
        return 'pdf'
    if str(name or '').lower().endswith(PREVIEW_TEXT) and b'\x00' not in bytes(data[:8192]):
        return 'text'
    return None
SENDER_RE = re.compile(r'(?:[^\s<>@,;*]+|\*)@[^\s<>@,;*]+\.[^\s<>@,;*]+')


def _schedule_iso(value):
    """Epoch milliseconds from the page -> UTC 'YYYY-MM-DDTHH:MM:SSZ'; None means send now."""
    if value in (None, '', 0):
        return None
    if type(value) not in (int, float):
        raise AppError('INVALID_SCHEDULE')
    try:
        moment = datetime.fromtimestamp(value / 1000, timezone.utc).replace(microsecond=0)
    except (OverflowError, OSError, ValueError):
        raise AppError('INVALID_SCHEDULE') from None
    return moment.strftime('%Y-%m-%dT%H:%M:%SZ')


def _schedule_ms(iso):
    return int(datetime.strptime(iso, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp() * 1000) if iso else None


def _image_uri(raw, limit=256 * 1024):
    """A head image as a data: URI, only for real PNG/JPEG/GIF bytes of sane size."""
    if not isinstance(raw, (bytes, bytearray)) or not raw or len(raw) > limit:
        return ''
    kind = 'png' if raw.startswith(b'\x89PNG\r\n') else 'jpeg' if raw.startswith(b'\xff\xd8\xff') else 'gif' if raw.startswith((b'GIF87a', b'GIF89a')) else ''
    return f'data:image/{kind};base64,' + base64.b64encode(bytes(raw)).decode('ascii') if kind else ''


def _people(value):
    result = []
    for name, addr in getaddresses([value.replace(';', ',')] if isinstance(value, str) else [str(v) for v in value]):
        if addr or name:
            result.append({'name': name.strip('"\' ') or '', 'email': addr})
    return result


def _summary_dict(item):
    name, addr = parseaddr(item.sender)
    return {'id': item.remote_id, 'fid': item.folder_id, 'subject': item.subject, 'from': item.sender,
            'fromName': name or addr or item.sender, 'fromEmail': addr, 'to': item.recipients,
            'date': item.date_text, 'ts': date_ts(item.date_text), 'preview': item.preview,
            'size': item.size, 'flags': {k: bool(v) for k, v in dict(item.flags).items()},
            'scheduleAt': _schedule_ms(getattr(item, 'schedule_at', None)), 'tags': list(getattr(item, 'tags', ()) or ())}


SEARCH_TEXT = ('from', 'to', 'with')  # Address substrings; 'with' is either side (往来邮件).
SEARCH_FLAGS = ('attachment', 'unread', 'flagged')


def search_filters(value):
    """Advanced-search filters from the page, validated: unknown keys are dropped."""
    value = dict(value or {})
    result = {}
    for key in SEARCH_TEXT:
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            if len(text) > 200:
                raise AppError('INVALID_SEARCH')
            result[key] = text.strip()
    if value.get('scope') == 'subject':
        result['scope'] = 'subject'
    for key in ('since', 'until'):
        text = value.get(key)
        if text:
            try:
                datetime.strptime(str(text), '%Y-%m-%d')
            except ValueError:
                raise AppError('INVALID_SEARCH') from None
            result[key] = str(text)
    if result.get('since') and result.get('until') and result['since'] > result['until']:
        raise AppError('INVALID_SEARCH')
    if value.get('folder') not in (None, ''):
        result['folder'] = str(value['folder'])
    for key in SEARCH_FLAGS:
        if value.get(key) is True:
            result[key] = True
    return result


def filter_match(filters, query=''):
    """Row predicate for the filters a server did not apply (and for the local cache)."""
    terms = [t for t in str(query).casefold().split() if t]
    since = date_ts(filters['since']) if filters.get('since') else None
    until = date_ts(filters['until']) + 86400 if filters.get('until') else None

    def match(row):
        flags = row.get('flags') or {}
        sender, rcpt = str(row.get('from', '')).casefold(), str(row.get('to', '')).casefold()
        return ((not filters.get('from') or filters['from'].casefold() in sender)
                and (not filters.get('to') or filters['to'].casefold() in rcpt)
                and (not filters.get('with') or filters['with'].casefold() in sender + ' ' + rcpt)
                and (filters.get('scope') != 'subject' or all(t in str(row.get('subject', '')).casefold() for t in terms))
                and (since is None or (row.get('ts') or 0) >= since) and (until is None or (row.get('ts') or 0) < until)
                and (not filters.get('folder') or str(row.get('fid')) == filters['folder'])
                and (not filters.get('attachment') or bool(flags.get('attached')))
                and (not filters.get('unread') or not flags.get('read'))
                and (not filters.get('flagged') or bool(flags.get('flagged'))))
    return match


def _cid_to_data(document, body_html):
    """cid: images the message carries, as data: URIs (reader, quotes, draft editing)."""
    def cid(match):
        found = document.cid_resources.get(match.group(2))
        if not found:
            return match.group(0)
        return f'src={match.group(1)}data:{found[0]};base64,{base64.b64encode(found[1]).decode("ascii")}{match.group(1)}'
    return re.sub(r'src=(["\'])cid:([^"\']+)\1', cid, body_html)


def _quote_html(document):
    header = lambda key: ', '.join(document.headers.get(key, ()))
    rows = ''.join(f'<div><b>{label}：</b>{html.escape(header(key))}</div>'
                   for label, key in (('发件人', 'from'), ('发送时间', 'date'), ('收件人', 'to'), ('抄送', 'cc'), ('主题', 'subject'))
                   if header(key))
    # Pictures in the original stay in the quote; sending carries them as inline parts.
    original = (_cid_to_data(document, document.html) if document.html else None) or '<div style="white-space:pre-wrap">' + html.escape(document.text) + '</div>'
    return ('<br><div class="hy-quote" style="border-top:1px solid #d0d5dd;padding-top:8px;margin-top:12px;color:#475467">'
            '<div style="font-size:12px;margin-bottom:8px">------------------ 原始邮件 ------------------' + rows + '</div>'
            '<blockquote style="margin:0 0 0 4px;padding-left:12px;border-left:3px solid #d0d5dd">' + original + '</blockquote></div>')


class MailBackend:
    def __init__(self, data_dir, adapter_factory=None, protector=None, crypto=None, services_factory=None):
        from coremail_desktop.storage.dpapi import DataProtector
        if adapter_factory is None:
            from coremail_desktop.protocol.coremail import CoremailAdapter as adapter_factory
        if services_factory is None:
            from coremail_desktop.storage.services import local_services as services_factory
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.protector = protector or DataProtector()
        self.crypto = crypto
        self.adapter = adapter_factory()
        self._services_factory = services_factory
        self.services = None
        self.cache = None
        self.account = None
        self.identities = []
        self.display_name = ''
        self._password = None  # Kept in memory only when the user chose to stay signed in.
        self._signed_in = False
        self._folders = []
        self._shared = {}  # Shared mailbox id -> {id, email, name, send, rw, folders, unread}
        self._contact_index = None
        self._contact_index_size = -1
        self._docs = {}
        self._signer_directory = {}  # (sender, signer fingerprint) -> matches the company directory
        self._rules = {}  # Rule id -> the rule as the server last listed it.
        self.autostart = None  # Set by the app (not in demo mode): reads and writes the Windows Run key.
        # 撤销发送: drafts waiting out their undo delay, local id -> {revision, subject, until}.
        # Memory only on purpose: if the app ends during the delay nothing is sent and
        # the draft stays in 本机草稿 (abandon_held records which ones).
        self._held = {}
        self._held_lock = threading.Lock()
        self._imported = {}  # 'eml:<digest>' -> raw bytes of .eml files opened read-only.

    # Accounts --------------------------------------------------------------
    def _accounts_path(self):
        return self.data_dir / 'account.bin'

    def saved_login(self):
        try:
            value = json.loads(self.protector.unprotect(self._accounts_path().read_bytes()).decode('utf-8'))
        except Exception:
            return {'server': 'https://newmail.inspur.com/coremail/', 'username': '', 'remember': False}
        return {'server': value.get('server', ''), 'username': value.get('username', ''),
                'remember': bool(value.get('password'))}

    def _save_login(self, server, username, password, remember):
        value = {'server': server, 'username': username, 'password': password if remember else ''}
        path = self._accounts_path()
        path.write_bytes(self.protector.protect(json.dumps(value).encode('utf-8')))

    def login(self, server, username, password, remember=False):
        server = (server or '').strip() or 'https://newmail.inspur.com/coremail/'
        username = (username or '').strip()
        if not username or not password:
            raise AppError('AUTH_REJECTED')
        self.adapter.login(AccountConfig('primary', server, username), password)
        self._signed_in = True
        self._password = (server, username, password) if remember else None
        self._save_login(server, username, password, remember)
        self._open_account(server, username)
        return self.profile()

    def auto_login(self):
        try:
            value = json.loads(self.protector.unprotect(self._accounts_path().read_bytes()).decode('utf-8'))
        except Exception:
            raise AppError('NOT_AUTHENTICATED') from None
        if not value.get('password'):
            raise AppError('NOT_AUTHENTICATED')
        try:
            return self.login(value['server'], value['username'], value['password'], True)
        except AppError as error:
            if error.code not in ('NETWORK_ERROR', 'TLS_ERROR'):
                raise
        # Offline start: show the encrypted cache now and sign in on the next call.
        self._password = (value['server'], value['username'], value['password'])
        self._signed_in = False
        self._open_account(value['server'], value['username'], online=False)
        return {**self.profile(), 'offline': True}

    def logout(self, forget=False):
        self.adapter.close()
        self._password = None
        self._signed_in = False
        if forget:
            saved = self.saved_login()
            self._save_login(saved['server'], saved['username'], '', False)
        if self.cache:
            self.abandon_held()
            self.cache.close()
        self.cache = self.account = None
        self._shared = {}
        return True

    def _open_account(self, server, username, online=True):
        account_key = hashlib.sha256((server + '\n' + username.casefold()).encode('utf-8')).hexdigest()[:24]
        if self.cache is None or self.account != username:
            if self.cache:
                self.cache.close()
            self.cache = MailCache(self.data_dir / 'cache' / (account_key + '.db'), self.protector)
        self.account = username
        self.services = self._services_factory(self.adapter, self.data_dir, prepare_encrypted=self._prepare_encrypted)
        self._auto_unlock_certificate()
        identity = self.cache.get('identity', {})
        self.identities, self.display_name = identity.get('identities') or [username], identity.get('name', '')
        self._shared = {b['id']: b for b in self.cache.get('shared') or []}
        if online:
            try:
                self.identities = sorted(self.adapter.get_account_identities()) or [username]
            except AppError:
                pass
            try:
                self.display_name = self.adapter.display_name() if hasattr(self.adapter, 'display_name') else ''
            except AppError:
                pass
            self.cache.set('identity', {'identities': self.identities, 'name': self.display_name})
        if username not in self.identities and '@' in username:
            self.identities.insert(0, username)

    def internal_domains(self):
        """Colleagues are addresses in the account's own mail domains."""
        return sorted({i.rpartition('@')[2].casefold() for i in [self.account or '', *self.identities] if '@' in i})

    def profile(self):
        return {'account': self.account, 'name': self.display_name, 'identities': self.identities,
                'prefs': self.prefs(), 'internalDomains': self.internal_domains(),
                'certificate': self.certificate_status()}

    def _online(self, operation, *args, **kwargs):
        """Run an adapter call; on an expired session sign in again once if allowed."""
        if self.cache is None:
            raise AppError('NOT_AUTHENTICATED')
        if not self._signed_in and self._password:
            server, username, password = self._password
            self.adapter.login(AccountConfig('primary', server, username), password)
            self._signed_in = True
        try:
            return operation(*args, **kwargs)
        except AppError as error:
            if error.code not in REAUTH_CODES or not self._password:
                raise
        server, username, password = self._password
        self.adapter.login(AccountConfig('primary', server, username), password)
        return operation(*args, **kwargs)

    # Preferences -----------------------------------------------------------
    def prefs(self):
        stored = self.cache.get('prefs', {}) if self.cache else {}
        prefs = {**DEFAULT_PREFS, **{k: v for k, v in stored.items() if k in DEFAULT_PREFS}}
        if self.autostart is not None:
            prefs['autostart'] = self.autostart.is_enabled()  # The registry is the source of truth.
        return prefs

    def set_prefs(self, values):
        prefs = self.prefs()
        for key, value in dict(values).items():
            if key in DEFAULT_PREFS and type(value) is type(DEFAULT_PREFS[key]):
                prefs[key] = value
        prefs['pollSeconds'] = max(30, min(int(prefs['pollSeconds']), 3600))
        prefs['signature'] = prefs['signature'][:20000]
        if prefs['undoSeconds'] not in UNDO_CHOICES:
            prefs['undoSeconds'] = DEFAULT_PREFS['undoSeconds']
        for key, (low, high) in WIDTH_LIMITS.items():
            prefs[key] = max(low, min(int(prefs[key]), high))
        if self.autostart is not None and type(values.get('autostart')) is bool:
            prefs['autostart'] = self.autostart.set_enabled(values['autostart'])
        self.cache.set('prefs', prefs)
        return prefs

    # Folders and lists -----------------------------------------------------
    def _folder_dict(self, folder, box=None):
        counts = dict(folder.counts)
        role = folder.role or SYSTEM_ROLES.get(folder.remote_id)
        parent = getattr(folder, 'parent', None)
        result = {'id': folder.remote_id, 'name': folder.name, 'role': role, 'parent': parent,
                  'system': bool(getattr(folder, 'system', False) or role), 'depth': 0,
                  'total': counts.get('messageCount'), 'unread': counts.get('unreadMessageCount', 0)}
        if box:
            result.update(id=folder.remote_id + '@' + box, mailbox=box, parent=parent and parent + '@' + box)
        return result

    @staticmethod
    def _ordered(folders):
        """Server folders in their usual order, user folders as a tree: each sub-folder
        right after its parent, with its depth."""
        key = lambda f: (FOLDER_ORDER.get(f['role'], 5), f['role'] is None, f['name'])
        ids = {f['id'] for f in folders}
        children = {}
        for f in folders:
            children.setdefault(f['parent'] if f['parent'] in ids and f['parent'] != f['id'] else None, []).append(f)
        result, seen = [], set()

        def walk(parent, depth):
            for f in sorted(children.get(parent, []), key=key):
                if f['id'] not in seen:
                    seen.add(f['id'])
                    result.append({**f, 'depth': depth})
                    walk(f['id'], depth + 1)
        walk(None, 0)
        return result + [{**f, 'depth': 0} for f in folders if f['id'] not in seen]  # A parent loop never hides a folder.

    # Shared mailboxes --------------------------------------------------------
    # A shared mailbox's folders and messages carry ids qualified as 'fid@box' and
    # 'mid@box' (webmail's own folder notation), so its mail is cached under its own
    # keys and never mixes with the user's; the server gets the bare id plus mboxa.
    def _locate(self, value):
        value = str(value)
        for box in self._shared:
            if value.endswith('@' + box) and len(value) > len(box) + 1:
                return value[:-len(box) - 1], box
        return value, None

    def _box_args(self, box):
        return {'mboxa': '@' + box} if box else {}

    def _qualify(self, row, box):
        if box:
            row.update(id=row['id'] + '@' + box, fid=row['fid'] + '@' + box, mailbox=box)
        return row

    def _box_view(self, box):
        info = self._shared.get(box) if box else None
        return {k: info[k] for k in ('id', 'email', 'name', 'send', 'rw')} if info else None

    def shared_mailboxes(self, refresh=True):
        """The shared / department mailboxes this account may open, each with its folders."""
        if not hasattr(self.adapter, 'list_shared_mailboxes'):
            return []
        if refresh or not self._shared:
            try:
                boxes = self._online(self.adapter.list_shared_mailboxes)
            except AppError as error:
                if self._shared and error.code in ('NETWORK_ERROR', 'TLS_ERROR'):
                    return list(self._shared.values())
                raise
            result = {}
            for box in boxes:
                try:
                    folders = self._online(self.adapter.list_folders, mboxa='@' + box['id'])
                except AppError as error:
                    if error.code in REAUTH_CODES:
                        raise
                    old = self._shared.get(box['id'], {})  # Keep the last known folders for this one.
                    folders = None
                entry = {**box, 'folders': self._ordered([self._folder_dict(f, box['id']) for f in folders])
                         if folders is not None else old.get('folders', [])}
                result[box['id']] = entry
            self._shared = result
            self._shared_counts()
            # Mail of a mailbox no longer shared with the user leaves the cache.
            self.cache.drop_mailboxes(set(result))
        return list(self._shared.values())

    def _shared_counts(self):
        for box in self._shared.values():
            box['unread'] = sum(f['unread'] or 0 for f in box['folders'] if f['role'] not in ('sent', 'trash', 'drafts', 'spam', 'virus'))
        self.cache.set('shared', list(self._shared.values()))

    def _all_folders(self):
        return self._folders + [f for b in self._shared.values() for f in b['folders']]

    def folders(self, refresh=True):
        if refresh or not self._folders:
            try:
                folders = self._online(self.adapter.list_folders)
            except AppError as error:
                cached = self.cache.get('folders') if self.cache else None
                if cached and error.code in ('NETWORK_ERROR', 'TLS_ERROR'):
                    return cached
                raise
            self._folders = self._ordered([self._folder_dict(f) for f in folders])
            self.cache.set('folders', self._folders)
        return self._folders

    def cached_view(self, folder_id, unread_only=False, limit=50):
        """Instant first paint from the encrypted cache, before any network call."""
        rows, total = self.cache.summaries(str(folder_id), 0, limit, unread_only)
        return {'folders': self.cache.get('folders') or [], 'rows': rows, 'cachedTotal': total}

    def messages(self, folder_id, offset=0, limit=50, unread_only=False):
        folder_id = str(folder_id)
        fid, box = self._locate(folder_id)
        summaries = self._online(self.adapter.list_messages, fid, offset, limit, **self._box_args(box))
        rows = [self._qualify(_summary_dict(item), box) for item in summaries]
        self.cache.put_summaries(folder_id, rows, window_complete=(offset == 0))
        total = self.adapter.last_folder_total if self.adapter.last_folder_id == folder_id else None
        if unread_only:
            rows = [r for r in rows if not r['flags'].get('read')]
        return {'rows': rows, 'total': total, 'offset': offset, 'more': len(summaries) == limit}

    # Reading ---------------------------------------------------------------
    def _parse(self, raw, mid):
        from email import policy
        from email.parser import BytesHeaderParser
        from coremail_desktop.crypto.formats import detect_envelope_format
        from coremail_desktop.crypto.models import CryptoError
        from coremail_desktop.crypto.signature import unwrap_signed
        outer = BytesHeaderParser(policy=policy.default).parsebytes(raw)
        sender = parseaddr(str(outer.get('from', '')))[1]

        def inner(content, **changes):
            document = parse_mime(raw=content, message_id=mid, rich=True)
            headers = dict(document.headers)
            for name in ('from', 'to', 'cc', 'subject', 'reply-to', 'message-id', 'references', 'date'):
                if outer.get_all(name):
                    headers[name] = tuple(str(v) for v in outer.get_all(name))
            return replace(document, headers=headers, **changes)
        try:
            signed = unwrap_signed(raw, sender)
        except CryptoError:
            signed = None
        if signed is not None and signed[0]:
            return inner(signed[0], signature=signed[1])
        if detect_envelope_format(raw):
            if self.crypto is None or self.crypto.certificate_info is None:
                document = parse_mime(raw=raw, message_id=mid, rich=True)
                return replace(document, encryption_state='locked')
            result = self.crypto.decrypt(raw, sender)
            return inner(result.mime_bytes, encryption_state='decrypted', signature=result.signature)
        return parse_mime(raw=raw, message_id=mid, rich=True)

    def _signature_view(self, document):
        """The reader's signature badge. A signer certificate identical to the
        sender's company directory certificate is reported as extra assurance."""
        report = document.signature
        if report is None:
            return None
        view = report.to_dict()
        sender = parseaddr(', '.join(document.headers.get('from', ())))[1].casefold()
        key = (sender, report.fingerprint)
        if report.status == 'valid' and report.email_match and hasattr(self.adapter, 'public_certificates'):
            if key not in self._signer_directory:
                try:
                    found = self._online(self.adapter.public_certificates, [sender])
                    self._signer_directory[key] = found.get(sender) == report.certificate_der
                except AppError:
                    pass  # Offline: the badge simply leaves the directory out.
            view['directory'] = self._signer_directory.get(key)
        return view

    def _raw(self, mid):
        """The message exactly as the server stores it (still encrypted if it was)."""
        if mid in self._imported:
            return self._imported[mid]
        raw = self.cache.get_body(mid)
        if raw is None:
            remote, box = self._locate(mid)
            raw = self._online(self.adapter.fetch_mime, remote, **self._box_args(box))
            self.cache.put_body(mid, raw)
        return raw

    def _document(self, mid):
        if mid in self._docs:
            return self._docs[mid]
        document = self._parse(self._raw(mid), mid)
        if document.encryption_state != 'locked':
            self._docs = {mid: document, **dict(list(self._docs.items())[:7])}
        return document

    def open_message(self, mid, mark_read=None):
        document = self._document(mid)
        header = lambda key: ', '.join(document.headers.get(key, ()))
        body_html = document.html
        remote = False
        if body_html:
            body_html = _cid_to_data(document, body_html)
            remote = bool(re.search(r'<img[^>]+src=["\']https?://', body_html, re.I))
        summary = self.cache.summary(mid) or {}
        result = {'id': mid, 'fid': summary.get('fid'), 'subject': header('subject') or summary.get('subject', ''),
                  'from': _people(header('from')), 'to': _people(header('to')), 'cc': _people(header('cc')),
                  'replyTo': _people(header('reply-to')), 'date': summary.get('date') or header('date'),
                  'html': body_html, 'text': document.text, 'hasRemoteImages': remote,
                  'encryption': document.encryption_state, 'signature': self._signature_view(document),
                  'flags': summary.get('flags', {}),
                  'scheduled': bool(summary.get('flags', {}).get('scheduleDelivery')), 'scheduleAt': summary.get('scheduleAt'),
                  'attachments': [{'part': a.part_id, 'name': a.name, 'type': a.media_type, 'size': a.size}
                                  for a in document.attachments],
                  'mailbox': self._box_view(self._locate(mid)[1]), 'tags': list(summary.get('tags') or [])}
        if mid in self._imported:
            result['imported'] = True  # A local .eml file: read-only, not on the server.
        elif self._is_server_draft(summary):
            result['serverDraft'] = True
        invitation = self._invitation(mid, document)
        if invitation:
            result['invitation'] = {**invitation, 'answer': (self.cache.get('inviteAnswers') or {}).get(mid),
                                    'canAnswer': not result['mailbox'] and hasattr(self.adapter, 'respond_invitation')}
        if mark_read is None:
            mark_read = self.prefs()['markReadOnOpen']
        if result['mailbox'] and not result['mailbox']['rw']:
            mark_read = False  # Read-only access: opening must not change colleagues' state.
        if mark_read and summary and not summary.get('flags', {}).get('read'):
            try:
                self.set_flags(mid, read=True)
                result['flags'] = {**result['flags'], 'read': True}
                result['markedRead'] = True
            except AppError:
                pass
        return result

    def prefetch(self, ids, limit=12):
        """Download bodies ahead of time so opening is instant. Runs on a background
        thread beside user actions, so it never signs in and swallows every failure."""
        fetched = 0
        for mid in list(ids)[:limit]:
            if self.cache is None or not self._signed_in:
                break
            try:
                if self.cache.get_body(mid) is None:
                    remote, box = self._locate(mid)
                    self.cache.put_body(mid, self.adapter.fetch_mime(remote, **self._box_args(box)))
                    fetched += 1
            except Exception:
                continue
        return fetched

    def attachment(self, mid, part):
        document = self._document(mid)
        if part == 'invite.ics':  # The invitation's calendar part, saved as an .ics file.
            from coremail_desktop.mime.icalendar import calendar_part
            found = calendar_part(self.cache.get_body(mid) or b'') if document.encryption_state == 'none' else None
            if not found:
                raise AppError('MESSAGE_UNAVAILABLE')
            return (found[1] or 'invite.ics'), 'text/calendar', found[0]
        data = document.attachment_payloads.get(part)
        info = next((a for a in document.attachments if a.part_id == part), None)
        if data is None or info is None:
            raise AppError('MESSAGE_UNAVAILABLE')
        return info.name, info.media_type, data

    def open_attached_mail(self, mid, part):
        """A mail attached to a message (message/rfc822 or a .eml file) opened read-only
        like a local .eml: returns its 'eml:' id for the reader."""
        name, kind, data = self.attachment(mid, part)
        if not is_attached_mail(name, kind):
            raise AppError('PREVIEW_UNAVAILABLE')
        return self.import_eml(data)

    def attachment_preview(self, mid, part):
        """An attachment for the in-app viewer: a raster image as a data: URI, plain
        text, or for a PDF only its name (the shell renders its pages to images)."""
        name, _kind, data = self.attachment(mid, part)
        kind = preview_kind(name, data)
        result = {'name': name, 'size': len(data)}
        if kind == 'pdf' and len(data) <= PREVIEW_PDF_LIMIT:
            return {**result, 'kind': 'pdf'}
        if kind and kind.startswith('image/') and len(data) <= PREVIEW_IMAGE_LIMIT:
            return {**result, 'kind': 'image', 'data': f'data:{kind};base64,' + base64.b64encode(data).decode('ascii')}
        if kind == 'text':
            raw = bytes(data[:PREVIEW_TEXT_LIMIT])
            for encoding in ('utf-8-sig', 'gb18030'):
                try:
                    text = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                text = raw.decode('utf-8', errors='replace')
            return {**result, 'kind': 'text', 'text': text, 'truncated': len(data) > PREVIEW_TEXT_LIMIT}
        raise AppError('PREVIEW_UNAVAILABLE')

    def _certificate_path(self):
        return self.data_dir / 'certificate.bin'

    def unlock_certificate(self, path, password, remember=False):
        if self.crypto is None:
            raise AppError('OPERATION_NOT_ALLOWED')
        self.crypto.unlock(path, password)
        self._docs.clear()
        if remember:
            record = json.dumps({'path': str(path), 'password': password}).encode('utf-8')
            self._certificate_path().write_bytes(self.protector.protect(record))
        else:
            self._certificate_path().unlink(missing_ok=True)
        return self.certificate_status()

    def _auto_unlock_certificate(self):
        if self.crypto is None or self.crypto.certificate_info is not None:
            return
        try:
            record = json.loads(self.protector.unprotect(self._certificate_path().read_bytes()).decode('utf-8'))
            self.crypto.unlock(record['path'], record['password'])
        except Exception:
            pass  # A moved file or changed password simply asks again when needed.

    def forget_certificate(self):
        self._certificate_path().unlink(missing_ok=True)
        if self.crypto is not None:
            self.crypto.lock()
        self._docs.clear()
        return self.certificate_status()

    def certificate_status(self):
        info = self.crypto.certificate_info if self.crypto is not None else None
        return {'available': self.crypto is not None, 'unlocked': info is not None, 'emails': list(info.emails) if info else [],
                'subject': info.subject if info else '', 'expires': info.not_after.strftime('%Y-%m-%d') if info else '',
                'remembered': self._certificate_path().exists()}

    # Mail actions ----------------------------------------------------------
    # Each action takes one id or a list of ids. A list goes to the server in one
    # call where the adapter supports it; any id the server did not confirm is
    # retried on its own, so a partial batch never goes unnoticed.
    def _apply(self, ids, single, many, *args):
        done = []
        if len(ids) > 1 and getattr(self.adapter, 'batch_updates', False):
            try:
                done = list(self._online(many, ids, *args))
            except AppError as error:
                if error.code != 'MAIL_UPDATE_UNCONFIRMED':
                    raise
        failed, last = [], None
        for mid in ids:
            if mid in done:
                continue
            try:
                self._online(single, mid, *args)
                done.append(mid)
            except AppError as error:
                if len(ids) == 1:
                    raise
                failed.append(mid)
                last = error
        if not done and last:
            raise last
        return [m for m in ids if m in done], failed

    def _apply_in(self, ids, single, many, *args, folder=None):
        """_apply for ids of one mailbox. Shared mail goes to the server as bare ids with
        that mailbox's mboxa, and only where the user has write access."""
        located = [self._locate(m) for m in ids]
        boxes = {box for _, box in located}
        if len(boxes) > 1:
            raise AppError('OPERATION_NOT_ALLOWED')
        box = boxes.pop()
        if folder is not None and self._locate(folder)[1] != box:
            raise AppError('SHARED_MOVE_OUTSIDE')
        if box is None:
            return self._apply(ids, single, many, *args)
        if not self._shared[box].get('rw'):
            raise AppError('SHARED_READ_ONLY')
        args = (self._locate(folder)[0],) if folder is not None else args
        extra = self._box_args(box)
        done, failed = self._apply([r for r, _ in located], functools.partial(single, **extra),
                                   many and functools.partial(many, **extra), *args)
        return [d + '@' + box for d in done], [f + '@' + box for f in failed]

    def _saved_counts(self):
        if self._folders:
            self.cache.set('folders', self._folders)
        if self._shared:
            self._shared_counts()

    def _count(self, fid, delta_unread=0, delta_total=0):
        folder = next((f for f in self._all_folders() if f['id'] == fid), None)
        if folder:
            folder['unread'] = max(0, (folder['unread'] or 0) + delta_unread)
            if folder['total'] is not None:
                folder['total'] = max(0, folder['total'] + delta_total)

    def set_flags(self, mid, read=None, flagged=None):
        flags = {k: v for k, v in (('read', read), ('flagged', flagged)) if v is not None}
        ids = [mid] if isinstance(mid, str) else [str(m) for m in mid]
        before = {m: self.cache.summary(m) or {} for m in ids}
        done, failed = self._apply_in(ids, self.adapter.set_message_flags, getattr(self.adapter, 'set_flags_many', None), flags)
        for m in done:
            was = before[m].get('flags', {}).get('read')
            if 'read' in flags and was is not None and bool(was) != read:
                self._count(before[m].get('fid'), -1 if read else 1)
            self.cache.update(m, flags=flags)
        self._saved_counts()
        return flags if isinstance(mid, str) else {'flags': flags, 'done': done, 'failed': failed, 'folders': self._folders,
                                                   'shared': list(self._shared.values())}

    def move(self, mid, folder_id):
        folder_id = str(folder_id)
        ids = [mid] if isinstance(mid, str) else [str(m) for m in mid]
        before = {m: self.cache.summary(m) or {} for m in ids}
        done, failed = self._apply_in(ids, self.adapter.move_message, getattr(self.adapter, 'move_many', None), folder_id, folder=folder_id)
        for m in done:
            unread = 0 if before[m].get('flags', {}).get('read', True) else 1
            if before[m].get('fid') and before[m]['fid'] != folder_id:
                self._count(before[m]['fid'], -unread, -1)
                self._count(folder_id, unread, 1)
            self.cache.update(m, fid=folder_id)
        self._saved_counts()
        return True if isinstance(mid, str) else {'done': done, 'failed': failed, 'folders': self._folders,
                                                  'shared': list(self._shared.values())}

    def delete(self, mid):
        ids = [mid] if isinstance(mid, str) else [str(m) for m in mid]
        box = self._locate(ids[0])[1] if ids else None
        if box:
            trash = next((f['id'] for f in self._shared[box]['folders'] if f['role'] == 'trash'), '4@' + box)
        else:
            trash = next((f['id'] for f in self._folders if f['role'] == 'trash'), '4')
        # Delete moves to the trash; mail already there stays (彻底删除 is purge()).
        movable = [m for m in ids if (self.cache.summary(m) or {}).get('fid') != trash]
        if not movable:
            raise AppError('OPERATION_NOT_ALLOWED')
        if isinstance(mid, str):
            return self.move(mid, trash)
        return {**self.move(movable, trash), 'skipped': [m for m in ids if m not in movable]}

    # Folder management -------------------------------------------------------
    # Only the user's own folders: a shared mailbox's folders belong to its owner.
    def _own_folder(self, folder_id, refresh=False):
        folder_id = str(folder_id)
        if self._locate(folder_id)[1]:
            raise AppError('OPERATION_NOT_ALLOWED')
        folder = next((f for f in self.folders(refresh=refresh) if f['id'] == folder_id), None)
        if folder is None:
            raise AppError('INVALID_FOLDER')
        return folder

    def _user_folder(self, folder_id, refresh=False):
        folder = self._own_folder(folder_id, refresh)
        if folder['system'] or folder['role']:
            raise AppError('SYSTEM_FOLDER')
        return folder

    def _folder_name(self, name, parent, skip=None):
        from coremail_desktop.protocol.coremail import CoremailAdapter
        name = CoremailAdapter.folder_name(name)
        if any(f['name'].casefold() == name.casefold() and f['parent'] == parent and f['id'] != skip for f in self._folders):
            raise AppError('FOLDER_EXISTS')
        return name

    def _folders_result(self, **extra):
        return {**extra, 'folders': self.folders(refresh=True), 'shared': list(self._shared.values())}

    def create_folder(self, name, parent=None):
        """A new folder at the top level, or a sub-folder of one of the user's folders."""
        parent = str(parent) if parent not in (None, '') else None
        self.folders(refresh=True)
        if parent is not None:
            # depth counts from 0, so the new folder sits at nesting level depth + 2.
            if self._user_folder(parent)['depth'] + 2 > MAX_FOLDER_DEPTH:
                raise AppError('FOLDER_DEPTH_LIMIT')
        name = self._folder_name(name, parent)
        folder_id = self._online(self.adapter.create_folder, name, parent)
        return self._folders_result(id=folder_id)

    def rename_folder(self, folder_id, name):
        folder = self._user_folder(folder_id, refresh=True)
        name = self._folder_name(name, folder['parent'], skip=folder['id'])
        if name != folder['name']:
            self._online(self.adapter.rename_folder, folder['id'], name)
        return self._folders_result()

    def _folder_ids(self, folder_id, total):
        ids, offset = [], 0
        while offset < total + 100:
            page = self._online(self.adapter.list_messages, folder_id, offset, 100)
            ids += [s.remote_id for s in page if s.remote_id not in ids]
            if len(page) < 100:
                break
            offset += 100
        return ids

    def delete_folder(self, folder_id, mail='trash'):
        """Delete one of the user's folders. Mail still in it is first moved to 已删除
        (mail='trash'), or the folder is kept (mail='keep'); it is never deleted with
        the folder. Sub-folders have to go first."""
        folder = self._user_folder(folder_id, refresh=True)
        if any(f['parent'] == folder['id'] for f in self._folders):
            raise AppError('FOLDER_HAS_CHILDREN')
        moved = 0
        if folder['total']:
            if mail != 'trash':
                raise AppError('FOLDER_NOT_EMPTY')
            trash = next((f['id'] for f in self._folders if f['role'] == 'trash'), '4')
            ids = self._folder_ids(folder['id'], folder['total'])
            for start in range(0, len(ids), 100):
                result = self.move(ids[start:start + 100], trash)
                moved += len(result['done'])
            folder = self._user_folder(folder['id'], refresh=True)
            if folder['total']:
                raise AppError('FOLDER_NOT_EMPTY')  # Something stayed behind: keep the folder.
        self._online(self.adapter.delete_folder, folder['id'])
        for mid in self.cache.known_ids(folder['id']):
            self.cache.remove(mid)
        return self._folders_result(moved=moved)

    def mark_all_read(self, folder_id):
        """Every unread message of a folder marked read: the server lists the unread ids
        (mbox:listMessages filter), then multi-id updates of up to 100."""
        folder_id = str(folder_id)
        fid, box = self._locate(folder_id)
        if box and not self._shared[box].get('rw'):
            raise AppError('SHARED_READ_ONLY')
        if not any(f['id'] == folder_id for f in self._all_folders()):
            raise AppError('INVALID_FOLDER')
        ids = [m + '@' + box if box else m for m in self._online(self.adapter.unread_ids, fid, **self._box_args(box))]
        done, failed = [], []
        for start in range(0, len(ids), 100):
            result = self.set_flags(ids[start:start + 100], read=True)
            done += result['done']
            failed += result['failed']
        if box:
            self.shared_mailboxes(refresh=True)
        return self._folders_result(done=len(done), failed=len(failed))

    # Spam --------------------------------------------------------------------
    def _role_id(self, role, default):
        return next((f['id'] for f in self._folders if f['role'] == role), default)

    def report_spam(self, ids, spam=True, block=False):
        """举报垃圾邮件 (to 垃圾邮件) or 这不是垃圾邮件 (back to the inbox); the server
        learns from the report. block / allow also puts the senders on the blocklist
        (spam) or the allowlist (not spam)."""
        ids = [ids] if isinstance(ids, str) else [str(m) for m in ids]
        if not ids or any(self._locate(m)[1] for m in ids):
            raise AppError('SPAM_NOT_ALLOWED')  # Webmail offers it only in the user's own mailbox.
        target = self._role_id('spam' if spam else 'inbox', '5' if spam else '1')
        before = {m: self.cache.summary(m) or {} for m in ids}
        done = []
        for start in range(0, len(ids), 100):
            done += self._online(self.adapter.report_spam, ids[start:start + 100], bool(spam))
        for m in done:
            unread = 0 if before[m].get('flags', {}).get('read', True) else 1
            if before[m].get('fid') and before[m]['fid'] != target:
                self._count(before[m]['fid'], -unread, -1)
                self._count(target, unread, 1)
            self.cache.update(m, fid=target)
        self._saved_counts()
        result = {'done': done, 'failed': [m for m in ids if m not in done], 'folders': self._folders,
                  'shared': list(self._shared.values())}
        senders = sorted({(before[m].get('fromEmail') or '').casefold() for m in done} - {''})
        if block and senders:
            try:
                result['lists'] = self.add_senders('refuselist' if spam else 'safelist', senders)
            except AppError as error:  # The mail has moved either way; only the list step failed.
                if error.code in REAUTH_CODES:
                    raise
                result['listError'] = error.code
        return result

    # Permanent deletion ------------------------------------------------------
    # Offered only in 已删除 and 垃圾邮件, each time behind the page's double
    # confirmation. Mail anywhere else has to go to the trash first.
    def _purgeable(self):
        return {f['id'] for f in self._folders if f['role'] in ('trash', 'spam')}

    def purge(self, ids):
        ids = [ids] if isinstance(ids, str) else [str(m) for m in ids]
        before = {m: self.cache.summary(m) or {} for m in ids}
        if not ids or any(self._locate(m)[1] or before[m].get('fid') not in self._purgeable() for m in ids):
            raise AppError('PURGE_NOT_ALLOWED')
        done = []
        for start in range(0, len(ids), 100):
            done += self._online(self.adapter.delete_messages, ids[start:start + 100])
        for m in done:
            self._count(before[m]['fid'], 0 if before[m].get('flags', {}).get('read', True) else -1, -1)
            self.cache.remove(m)
            self._docs.pop(m, None)
        self._saved_counts()
        return {'done': done, 'failed': [m for m in ids if m not in done], 'folders': self._folders,
                'shared': list(self._shared.values())}

    def empty_folder(self, folder_id):
        """清空 已删除 or 垃圾邮件: everything in it is deleted for good."""
        folder = self._own_folder(folder_id, refresh=True)
        if folder['id'] not in self._purgeable():
            raise AppError('PURGE_NOT_ALLOWED')
        self._online(self.adapter.empty_folder, folder['id'])
        for mid in self.cache.known_ids(folder['id']):
            self.cache.remove(mid)
            self._docs.pop(mid, None)
        return self._folders_result(removed=folder['total'] or 0)

    # Sender blocklist / allowlist ----------------------------------------------
    def sender_lists(self):
        return self._online(self.adapter.sender_lists)

    def _sender(self, kind, value):
        value = str(value or '').strip().casefold()
        if value.startswith('@'):
            value = '*' + value
        if kind not in ('safelist', 'refuselist') or len(value) > 254 or not SENDER_RE.fullmatch(value):
            raise AppError('INVALID_SENDER')
        own = {i.casefold() for i in [self.account or '', *self.identities]}
        if kind == 'refuselist' and (value in own or value.startswith('*@') and value[2:] in self.internal_domains()):
            raise AppError('SENDER_IS_SELF')
        return value

    def add_senders(self, kind, values):
        """Put addresses (or *@domain) on one list; an entry on the other list moves over."""
        values = [values] if isinstance(values, str) else list(values)
        entries = list(dict.fromkeys(self._sender(kind, v) for v in values))
        lists = self.sender_lists()
        other = 'refuselist' if kind == 'safelist' else 'safelist'
        changed = {kind: lists[kind] + [e for e in entries if e not in lists[kind]]}
        if any(e in lists[other] for e in entries):
            changed[other] = [e for e in lists[other] if e not in entries]
        if len(changed[kind]) > SENDER_LIST_LIMIT:
            raise AppError('SENDER_LIST_LIMIT')
        if changed[kind] != lists[kind] or other in changed:
            self._online(self.adapter.set_sender_lists, changed)
        return {**lists, **changed}

    def remove_sender(self, kind, value):
        if kind not in ('safelist', 'refuselist'):
            raise AppError('INVALID_SENDER')
        lists = self.sender_lists()
        value = str(value or '').strip().casefold()
        kept = [e for e in lists[kind] if e.casefold() != value]
        if len(kept) != len(lists[kind]):
            self._online(self.adapter.set_sender_lists, {kind: kept})
        return {**lists, kind: kept}

    # Search ----------------------------------------------------------------
    def search_local(self, query, filters=None):
        # The user's own mail only: the cache skips shared-mailbox rows.
        filters = search_filters(filters)
        return self.cache.search(query or '', match=filter_match(filters, query) if filters else None)

    def search_server(self, query, offset=0, filters=None):
        """Server search. Filters the server applies go with the request; the rest
        narrow the returned rows here, and the reply says which were which."""
        query = (query or '').strip()
        filters = search_filters(filters)
        supported = getattr(self.adapter, 'SEARCH_FILTERS', frozenset())
        remote = {k: v for k, v in filters.items() if k in supported}
        local = {k: v for k, v in filters.items() if k not in supported}
        if not query and not remote:
            raise AppError('INVALID_SEARCH')  # Nothing the server could search for.
        if remote:
            result = self._online(self.adapter.search_messages, query, offset, 50, filters=remote)
        else:
            result = self._online(self.adapter.search_messages, query, offset, 50)
        rows = [_summary_dict(item) for item in result['messages']]
        if local:
            rows = [r for r in rows if filter_match(local, query)(r)]
        return {'rows': rows, 'total': result['total'], 'server': sorted(remote), 'local': sorted(local),
                'more': offset + len(result['messages']) < result['total']}

    def search_capabilities(self):
        return {'server': sorted(getattr(self.adapter, 'SEARCH_FILTERS', frozenset()))}

    # Directory (通讯录) ---------------------------------------------------------
    # Read-only. The tree and each department's members are kept in the encrypted
    # cache so the view opens instantly; kv keys for departments are hashed so no
    # org data sits in plaintext.
    def _stale(self, key, fetch, refresh):
        cached = self.cache.get(key)
        if cached is not None and not refresh:
            return {**cached, 'cached': True}
        try:
            value = {'data': self._online(fetch), 'at': int(datetime.now(timezone.utc).timestamp())}
        except AppError as error:
            if cached is not None and error.code in ('NETWORK_ERROR', 'TLS_ERROR'):
                return {**cached, 'cached': True}
            raise
        self.cache.set(key, value)
        return {**value, 'cached': False}

    def directory(self, refresh=False):
        if not hasattr(self.adapter, 'directory_tree'):
            return {'tree': [], 'at': None, 'cached': False}
        value = self._stale('directory', self.adapter.directory_tree, refresh)
        return {'tree': value['data'], 'at': value['at'], 'cached': value['cached']}

    # The server lists only a department's own people (oab:listEx) and offers no
    # subtree listing (contact.jsp oabSearch needs a keyword; oab:searchEx at the root
    # answers one entry), while many departments hold nobody directly (2026-10-19,
    # analysis/contacts-subtree-shape{,2}-2026-10-18.json). A department with
    # sub-departments is therefore shown with everyone beneath it, gathered here one
    # department at a time from the same cached listings; each row names its unit.
    # The root (the whole group, 168 departments) is not gathered: search covers it.
    SUBTREE_DEPARTMENTS = 25
    SUBTREE_PEOPLE = 3000

    def _department(self, dn, refresh):
        key = 'members:' + hashlib.sha256(dn.encode('utf-8')).hexdigest()[:24]
        return self._stale(key, lambda: self.adapter.directory_members(dn), refresh)

    def _subtree(self, dn):
        """[(node, unit path below dn)] for every department under dn, from the cached tree."""
        tree = (self.cache.get('directory') or {}).get('data') or []

        def find(nodes):
            for n in nodes:
                if n.get('id') == dn:
                    return n
                hit = find(n.get('children') or [])
                if hit:
                    return hit
            return None

        def below(node, path):
            for child in node.get('children') or []:
                unit = path + [child.get('name') or '']
                yield child, ' / '.join(unit)
                yield from below(child, unit)
        node = None if any(n.get('id') == dn for n in tree) else find(tree)
        return list(below(node, [])) if node else []

    def members(self, dn, refresh=False):
        if not isinstance(dn, str) or not dn or len(dn) > 500 or not hasattr(self.adapter, 'directory_members'):
            raise AppError('INVALID_CONTACT')
        own = self._department(dn, refresh)
        subs = self._subtree(dn)
        result = {**own['data'], 'direct': own['data']['total'], 'departments': len(subs), 'partial': False,
                  'at': own['at'], 'cached': own['cached']}
        if not subs:
            return result
        rows = list(own['data']['rows'])
        seen = {r['email'].casefold() for r in rows}
        total = own['data']['total']
        for node, unit in subs[:self.SUBTREE_DEPARTMENTS]:
            try:
                part = self._department(node['id'], refresh)
            except AppError as error:
                if error.code in ('SESSION_EXPIRED', 'NOT_AUTHENTICATED'):
                    raise
                result['partial'] = True  # The rest still shows; the page says some are missing.
                continue
            total += part['data']['total']
            result['partial'] |= part['data']['total'] > len(part['data']['rows'])
            result['cached'] &= part['cached']
            for r in part['data']['rows']:
                if len(rows) < self.SUBTREE_PEOPLE and r['email'].casefold() not in seen:
                    seen.add(r['email'].casefold())
                    rows.append({**r, 'unit': unit})
        result['partial'] |= len(subs) > self.SUBTREE_DEPARTMENTS
        return {**result, 'rows': rows, 'total': total}

    def personal_contacts(self, refresh=False):
        if not hasattr(self.adapter, 'personal_contacts'):
            return {'rows': [], 'at': None, 'cached': False}
        value = self._stale('pab', self.adapter.personal_contacts, refresh)
        return {'rows': value['data'], 'at': value['at'], 'cached': value['cached']}

    PERSON_MAX_AGE = 24 * 3600
    PERSON_FIELDS = ('name', 'email', 'dept', 'title', 'mobile', 'phone', 'fax', 'address', 'employeeNo', 'office', 'manager')

    def person(self, email, refresh=False):
        """A colleague's directory details (and head image) for the card and popover.

        Kept in the encrypted cache under a hashed key and refreshed once a day; never
        logged. People the directory does not know come back as {found: False}."""
        email = (email or '').strip()
        if not email or len(email) > 254 or '@' not in email:
            raise AppError('INVALID_CONTACT')
        if not hasattr(self.adapter, 'person_details'):
            return {'found': False, 'email': email, 'at': None, 'cached': False}
        key = 'person:' + hashlib.sha256(email.casefold().encode('utf-8')).hexdigest()[:24]
        cached = self.cache.get(key)
        fresh = cached is not None and int(datetime.now(timezone.utc).timestamp()) - cached.get('at', 0) < self.PERSON_MAX_AGE
        value = self._stale(key, lambda: self._fetch_person(email), refresh or not fresh)
        return {**value['data'], 'at': value['at'], 'cached': value['cached']}

    def _fetch_person(self, email):
        found = self.adapter.person_details(email)
        if not found:
            return {'found': False, 'email': email}
        result = {k: str(found.get(k) or '')[:500] for k in self.PERSON_FIELDS}
        result.update(found=True, email=result['email'] or email, avatar='')
        uid = found.get('uid')
        if uid and hasattr(self.adapter, 'head_image'):
            try:
                result['avatar'] = _image_uri(self.adapter.head_image(uid))
            except AppError:
                pass  # The coloured initials stand in.
        return result

    def find_people(self, query):
        """Company-wide directory matches for the contacts view search box."""
        query = (query or '').strip()
        if not query or not hasattr(self.adapter, 'match_contacts'):
            return []
        return self._online(self.adapter.match_contacts, query, 20)

    # Contacts --------------------------------------------------------------
    def _local_contacts(self):
        entries = self.cache.addresses()
        if self._contact_index is not None and self._contact_index_size == len(entries):
            return self._contact_index
        sent = {f['id'] for f in self._folders if f['role'] == 'sent'} or {'3'}
        index = {}
        for sender, recipients, fid, ts in entries:
            people = _people(recipients) if fid in sent else _people(sender)
            for person in people:
                key = person['email'].casefold()
                if not key or key in {i.casefold() for i in self.identities}:
                    continue
                entry = index.setdefault(key, {'name': '', 'email': person['email'], 'score': 0.0})
                entry['name'] = entry['name'] or person['name']
                entry['score'] += (3.0 if fid in sent else 1.0) + ts / 1e10
        self._contact_index, self._contact_index_size = index, len(entries)
        return index

    def suggest(self, query, limit=8):
        query = (query or '').strip()
        if not query:
            return []
        q = query.casefold()
        local = sorted((c for c in self._local_contacts().values()
                        if q in c['email'].casefold() or q in c['name'].casefold()),
                       key=lambda c: -c['score'])[:limit]
        results = [{'name': c['name'], 'email': c['email'], 'source': 'recent'} for c in local]
        seen = {c['email'].casefold() for c in results}
        if hasattr(self.adapter, 'match_contacts') and len(results) < limit:
            try:
                for person in self._online(self.adapter.match_contacts, query, limit):
                    if person['email'].casefold() not in seen:
                        seen.add(person['email'].casefold())
                        results.append({**person, 'source': 'directory'})
            except AppError:
                pass  # The directory is optional; local suggestions still work.
        return results[:limit]

    # Compose ---------------------------------------------------------------
    def _signature_html(self):
        signature = self.prefs()['signature']
        return f'<br><br><div class="hy-signature">{signature}</div>' if signature else ''

    def _shared_sender(self, address):
        """The shared mailbox whose address this is, or None for the user's own."""
        key = parseaddr(str(address))[1].casefold()
        return next((b for b in self._shared.values() if b['email'].casefold() == key), None)

    def _from_choices(self):
        """Addresses the user may send as: their own, plus shared mailboxes with send rights."""
        own = [i for i in self.identities if not self._shared_sender(i)] or [self.account or '']
        shared = [b['email'] for b in self._shared.values() if b.get('send')]
        return own + [a for a in shared if a.casefold() not in {o.casefold() for o in own}]

    def compose(self, mode='new', mid=None, mailbox=None):
        box = self._locate(mid)[1] if mid else (mailbox if mailbox in self._shared else None)
        info = self._shared.get(box) if box else None
        own = self._from_choices()[0]
        # Mail in a shared mailbox is answered as that mailbox only with send rights.
        identity = info['email'] if info and info.get('send') else own
        base = {'localId': uuid.uuid4().hex, 'revision': 0, 'mode': mode, 'from': identity,
                'to': [], 'cc': [], 'bcc': [], 'subject': '', 'html': '<p><br></p>' + self._signature_html(),
                'replySourceId': None, 'referenceHeaders': {}, 'attachments': [], 'encrypt': None, 'sign': None,
                'scheduleAt': None, 'receipt': False, 'fromChoices': self._from_choices()}
        if mode == 'new' or not mid:
            return base
        document = self._document(mid)
        if mode in ('reply', 'reply_all'):
            draft = build_reply(document, set(self.identities) | ({info['email']} if info else set()), mode == 'reply_all', identity)
            named = self._named_addresses(document)
            value = {**base, 'mode': draft.mode, 'to': named(draft.to), 'cc': named(draft.cc), 'subject': draft.subject,
                     'html': '<p><br></p>' + self._signature_html() + _quote_html(document),
                     'replySourceId': draft.reply_source_id, 'referenceHeaders': dict(draft.reference_headers),
                     'encrypt': True if document.encryption_state != 'none' else None}
            if mid in self._imported:
                # An imported .eml is not on the server, so the reply cannot be threaded
                # to it there: it goes out as a new message with the quote.
                value.update(mode='new', replySourceId=None, referenceHeaders={}, title=value['mode'])
            return value
        if mode == 'forward_attach':
            # 作为附件转发: the original exactly as stored (an encrypted one stays encrypted).
            subject = next(iter(document.headers.get('subject', ())), '')
            raw = self._raw(mid)
            return {**base, 'mode': 'forward', 'title': 'forward_attach',
                    'subject': ('' if subject.lower().startswith('fwd:') else 'Fwd: ') + subject,
                    'attachments': [{'name': self.file_stem(mid) + '.eml', 'type': 'message/rfc822', 'size': len(raw),
                                     'data': base64.b64encode(raw).decode('ascii')}],
                    'encryptedOriginal': document.encryption_state != 'none'}
        if mode == 'forward':
            subject = next(iter(document.headers.get('subject', ())), '')
            attachments = [{'name': a.name, 'type': a.media_type, 'size': len(document.attachment_payloads[a.part_id]),
                            'data': base64.b64encode(document.attachment_payloads[a.part_id]).decode('ascii')}
                           for a in document.attachments if a.part_id in document.attachment_payloads]
            return {**base, 'mode': 'forward', 'subject': ('' if subject.lower().startswith('fwd:') else 'Fwd: ') + subject,
                    'html': '<p><br></p>' + self._signature_html() + _quote_html(document), 'attachments': attachments,
                    'encrypt': True if document.encryption_state != 'none' else None}
        raise AppError('INVALID_MAIL_ACTION')

    def _named_addresses(self, document):
        """Recipients as '"Name" <addr>' so the composer shows who they are. Names come
        from the original message's headers, then from mail history; bare otherwise."""
        names = {}
        for key in ('reply-to', 'from', 'to', 'cc'):
            for name, addr in getaddresses([str(v) for v in document.headers.get(key, ())]):
                name = name.strip().strip('"\'')
                if name and addr and name.casefold() != addr.casefold():
                    names.setdefault(addr.casefold(), name)
        try:
            known = self._local_contacts()
        except Exception:
            known = {}

        def named(addresses):
            result = []
            for addr in addresses:
                name = names.get(addr.casefold()) or (known.get(addr.casefold()) or {}).get('name', '')
                name = name.replace('"', '').replace('<', '').replace('>', '').strip()
                result.append(f'"{name}" <{addr}>' if name else addr)
            return result
        return named

    def _draft(self, value):
        attachments = tuple(DraftAttachment(str(a['name']), str(a.get('type') or 'application/octet-stream'),
                                            base64.b64decode(a['data'], validate=True)) for a in value.get('attachments', ()))
        # Body images count toward the same size limits as attachments.
        validate_attachments(attachments + tuple(extract_inline(str(value.get('html', '')))[1]))
        server_draft = value.get('serverDraftId')
        if server_draft is not None and (not isinstance(server_draft, str) or len(server_draft) > 300):
            raise AppError('DRAFT_UNAVAILABLE')
        listify = lambda key: tuple(str(v).strip() for v in value.get(key, ()) if str(v).strip())
        mode = value.get('mode', 'new')
        source, box = self._locate(value['replySourceId']) if mode in ('reply', 'reply_all') and value.get('replySourceId') else (None, None)
        return DraftDocument(str(value['localId']), int(value.get('revision', 0)), mode,
                             str(value.get('from') or self.identities[0]), listify('to'), listify('cc'), listify('bcc'),
                             str(value.get('subject', '')), str(value.get('html', '')),
                             {str(k): str(v) for k, v in dict(value.get('referenceHeaders') or {}).items()},
                             value.get('encrypt') is True, source,
                             attachments, True, signature_required=value.get('sign') is True,
                             schedule_at=_schedule_iso(value.get('scheduleAt')), request_receipt=value.get('receipt') is True,
                             reply_mailbox='@' + box if box else '', server_draft_id=server_draft or '')

    def save_draft(self, value, server=False):
        """Save locally (encrypted). server=True, for a draft opened from the server's
        草稿箱, also writes it back there; once the server confirms, the server copy is
        the record and the local copy is removed (it returns with the next autosave)."""
        draft = self._draft(value)
        draft = replace(draft, revision=draft.revision + 1)
        self.services['save_draft'](draft=draft)
        saved = {'localId': draft.local_id, 'revision': draft.revision}
        if not server or not draft.server_draft_id:
            return saved
        # The local copy is saved either way; a server failure is reported, not raised,
        # so the composer keeps the revision it now has.
        from coremail_desktop.ui.messages import user_message
        try:
            if draft.encryption_required or draft.signature_required:
                raise AppError('DRAFT_ENCRYPTED_LOCAL')  # Never written to the server in plaintext.
            new_id = self._online(self.adapter.save_server_draft, draft)
        except AppError as error:
            if error.code in REAUTH_CODES:
                raise
            return {**saved, 'serverErrorCode': error.code, 'serverError': user_message(error.code)}
        self.delete_draft(draft.local_id)
        return {**saved, 'serverDraftId': new_id, 'local': False}

    # Server drafts (服务器草稿箱) -------------------------------------------------
    # Drafts saved by webmail, the official client or a phone live in the server's
    # drafts folder. Editing one makes a local working copy linked to it
    # (serverDraftId); 保存草稿 writes it back, sending consumes it. 本机草稿 stays
    # local-only: new mail and encrypted drafts never go to the server by themselves.
    def _is_server_draft(self, summary):
        fid = summary.get('fid')
        role = next((f['role'] for f in self._folders if f['id'] == fid), SYSTEM_ROLES.get(str(fid)))
        return role == 'drafts' and not summary.get('flags', {}).get('scheduleDelivery')

    def edit_server_draft(self, mid):
        if not isinstance(mid, str) or self._locate(mid)[1] or mid in self._imported:
            raise AppError('DRAFT_UNAVAILABLE')  # Shared mailboxes keep their drafts in webmail.
        summary = self.cache.summary(mid) or {}
        if summary.get('flags', {}).get('scheduleDelivery'):
            raise AppError('DRAFT_SCHEDULED')
        if not self._is_server_draft(summary) or not hasattr(self.adapter, 'save_server_draft'):
            raise AppError('DRAFT_UNAVAILABLE')
        document = self._document(mid)
        if document.encryption_state == 'locked':
            raise AppError('CERT_LOCKED')
        header = lambda key: [v for v in document.headers.get(key, ()) if v]
        addresses = lambda key: [f'"{n}" <{a}>' if n else a for n, a in getaddresses(header(key)) if a]
        body = _cid_to_data(document, document.html) if document.html else \
            '<div style="white-space:pre-wrap">' + html.escape(document.text) + '</div>'
        attachments = [{'name': a.name, 'type': a.media_type, 'size': len(document.attachment_payloads[a.part_id]),
                        'data': base64.b64encode(document.attachment_payloads[a.part_id]).decode('ascii')}
                       for a in document.attachments if a.part_id in document.attachment_payloads]
        return {**self.compose('new'), 'to': addresses('to'), 'cc': addresses('cc'), 'bcc': addresses('bcc'),
                'subject': ', '.join(header('subject')), 'html': body, 'attachments': attachments,
                'serverDraftId': mid, 'receipt': bool(header('disposition-notification-to'))}

    def list_drafts(self):
        return self.services['list_drafts']()

    def restore_draft(self, local_id):
        draft = self.services['restore_draft'](local_id=local_id)
        body = draft.body if draft.is_html else '<div style="white-space:pre-wrap">' + html.escape(draft.body) + '</div>'
        return {'localId': draft.local_id, 'revision': draft.revision, 'mode': draft.mode, 'from': draft.from_identity,
                'to': list(draft.to), 'cc': list(draft.cc), 'bcc': list(draft.bcc), 'subject': draft.subject,
                'html': body, 'replySourceId': self._reply_id(draft), 'referenceHeaders': dict(draft.reference_headers),
                'fromChoices': self._from_choices(),
                'encrypt': True if draft.encryption_required else None,
                # Signing follows encryption unless the user chose otherwise.
                'sign': True if draft.signature_required else False if draft.encryption_required else None,
                'scheduleAt': _schedule_ms(draft.schedule_at), 'receipt': draft.request_receipt,
                'serverDraftId': draft.server_draft_id or None,
                'attachments': [{'name': a.name, 'type': a.media_type, 'size': len(a.payload),
                                 'data': base64.b64encode(a.payload).decode('ascii')} for a in draft.attachments]}

    def _reply_id(self, draft):
        """The reply source as the interface knows it: qualified for a shared mailbox."""
        source, box = draft.reply_source_id, getattr(draft, 'reply_mailbox', '')[1:]
        return source + '@' + box if source and box else source

    def delete_draft(self, local_id):
        self.services['delete_draft'](local_id=local_id)
        return True

    # Templates: named subject + body (+ recipients), in the encrypted cache kv -----
    def templates(self):
        return sorted(self.cache.get('templates', []), key=lambda t: t['name'].casefold())

    def _template_name(self, name):
        name = str(name or '').strip()
        if not name or len(name) > 60 or '\n' in name:
            raise AppError('INVALID_TEMPLATE')
        return name

    def save_template(self, name, value, recipients=False):
        name = self._template_name(name)
        value = dict(value or {})
        listify = lambda key: [str(v).strip() for v in value.get(key, ()) if str(v).strip()][:100] if recipients else []
        body, subject = str(value.get('html', '')), str(value.get('subject', ''))
        if len(body) > 500_000 or len(subject) > 1000 or '\n' in subject:
            raise AppError('INVALID_TEMPLATE')
        stored = self.cache.get('templates', [])
        same = next((t for t in stored if t['name'].casefold() == name.casefold()), None)  # Same name replaces.
        if same is None and len(stored) >= TEMPLATE_LIMIT:
            raise AppError('TEMPLATE_LIMIT')
        template = {'id': same['id'] if same else uuid.uuid4().hex, 'name': name, 'subject': subject, 'html': body,
                    'to': listify('to'), 'cc': listify('cc'), 'bcc': listify('bcc'),
                    'modified': datetime.now().strftime('%Y-%m-%d %H:%M')}
        self.cache.set('templates', [t for t in stored if t is not same] + [template])
        return template

    def rename_template(self, template_id, name):
        name = self._template_name(name)
        stored = self.cache.get('templates', [])
        if not any(t['id'] == template_id for t in stored):
            raise AppError('INVALID_TEMPLATE')
        if any(t['name'].casefold() == name.casefold() and t['id'] != template_id for t in stored):
            raise AppError('TEMPLATE_EXISTS')
        self.cache.set('templates', [{**t, 'name': name} if t['id'] == template_id else t for t in stored])
        return self.templates()

    def delete_template(self, template_id):
        self.cache.set('templates', [t for t in self.cache.get('templates', []) if t['id'] != template_id])
        return self.templates()

    # Print and export --------------------------------------------------------
    def file_stem(self, mid):
        """A file name for saving the message: its subject without path characters."""
        subject = (self.cache.summary(mid) or {}).get('subject') or ', '.join(self._document(mid).headers.get('subject', ()))
        stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', subject).strip(' .')[:80]
        return stem or '邮件'

    def export_eml(self, mid, decrypted=False):
        """(file name, bytes) of the message. By default the original as stored on the
        server, so encrypted mail stays encrypted; decrypted=True gives the readable
        copy (outer headers + decrypted inner MIME) and needs the certificate."""
        raw = self._raw(mid)
        name = self.file_stem(mid) + '.eml'
        if not decrypted:
            return name, raw
        from email import policy
        from email.parser import BytesHeaderParser
        from coremail_desktop.crypto.formats import detect_envelope_format
        if not detect_envelope_format(raw):
            return name, raw  # Nothing to decrypt.
        if self.crypto is None or self.crypto.certificate_info is None:
            raise AppError('CERT_LOCKED')
        outer = BytesHeaderParser(policy=policy.default).parsebytes(raw)
        inner = self.crypto.decrypt(raw, parseaddr(str(outer.get('from', '')))[1]).mime_bytes
        head = re.split(rb'\r?\n\r?\n', inner, maxsplit=1)[0]
        own = {line.split(b':', 1)[0].strip().lower() for line in head.splitlines() if b':' in line and line[:1] not in b' \t'}
        keep = []
        for key in ('From', 'To', 'Cc', 'Subject', 'Date', 'Message-ID', 'In-Reply-To', 'References', 'Reply-To'):
            if key.lower().encode() not in own:
                keep += [f'{key}: {value}'.encode('utf-8') for value in outer.get_all(key) or ()]
        if b'mime-version' not in own:
            keep.append(b'MIME-Version: 1.0')
        return name, b'\r\n'.join(keep) + b'\r\n' + inner

    def import_eml(self, raw):
        """Open a local .eml file read-only: it is kept in memory under an 'eml:' id the
        reader can show (reply and forward work; nothing is stored or uploaded)."""
        from email import policy
        from email.parser import BytesHeaderParser
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            raise AppError('EML_INVALID')
        if len(raw) > MAX_EML_BYTES:
            raise AppError('EML_TOO_LARGE')
        raw = bytes(raw)
        try:
            head = BytesHeaderParser(policy=policy.default).parsebytes(raw)
            valid = bool(head.get('From') or head.get('Subject')) and any(head.get(k) for k in ('From', 'To', 'Subject', 'Date', 'Message-ID'))
        except Exception:
            valid = False
        if not valid:
            raise AppError('EML_INVALID')
        mid = 'eml:' + hashlib.sha256(raw).hexdigest()[:24]
        self._imported = {mid: raw, **dict(list(self._imported.items())[:15])}
        return mid

    def print_html(self, mid, remote_images=False):
        """A standalone document for printing or PDF export: header, attachment names
        and the sanitized body. Its policy refuses scripts; remote images load only
        when the user allowed them for this message."""
        view = self.open_message(mid, mark_read=False)
        esc = html.escape
        names = lambda people: '、'.join(f'{p["name"]} <{p["email"]}>' if p['name'] else p['email'] for p in people)
        rows = [(label, value) for label, value in (('发件人', names(view['from'])), ('收件人', names(view['to'])),
                                                    ('抄送', names(view['cc'])), ('时间', view['date'] or '')) if value]
        if view['attachments']:
            rows.append(('附件', '、'.join(a['name'] for a in view['attachments'])))
        body = view['html'] or '<div style="white-space:pre-wrap">' + esc(view['text'] or '') + '</div>'
        if view['encryption'] == 'locked':
            body = '<p>（加密邮件：解锁个人证书后才能打印正文）</p>'
        csp = "default-src 'none'; style-src 'unsafe-inline'; img-src data:" + (' https: http:' if remote_images else '') + '; font-src data:'
        return ('<!doctype html><html><head><meta charset="utf-8">'
                f'<meta http-equiv="Content-Security-Policy" content="{csp}"><title>{esc(view["subject"])}</title>'
                '<style>body{margin:0;color:#101828;font:13.5px/1.6 "Segoe UI","Microsoft YaHei UI","Microsoft YaHei",sans-serif}'
                'h1{font-size:19px;margin:0 0 10px}table.hy-head{border-collapse:collapse;margin-bottom:14px;width:100%}'
                'table.hy-head td{padding:2px 0;vertical-align:top;font-size:12.5px}'
                'table.hy-head td:first-child{color:#667085;width:4.5em;white-space:nowrap}'
                'hr{border:0;border-top:1px solid #d0d5dd;margin:0 0 14px}img{max-width:100%;height:auto}'
                '.hy-body{overflow-wrap:anywhere}blockquote{margin:0 0 0 4px;padding-left:12px;border-left:3px solid #d0d5dd;color:#475467}'
                '</style></head>'
                f'<body><h1>{esc(view["subject"] or "（无主题）")}</h1><table class="hy-head">'
                + ''.join(f'<tr><td>{label}</td><td>{esc(value)}</td></tr>' for label, value in rows)
                + f'</table><hr><div class="hy-body">{body}</div></body></html>')

    def send(self, value, hold=False):
        """Check, store and send a draft. hold=True (the composer's 发送) with 撤销发送 on
        and no schedule: the draft is only held here for the delay and comes back as
        state 'Held'; the app calls release_send when the delay is over, cancel_send undoes it."""
        draft = self._draft(value)
        draft = replace(draft, revision=draft.revision + 1)
        if not (draft.to or draft.cc or draft.bcc):
            raise AppError('RECIPIENT_REQUIRED')
        sender = self._shared_sender(draft.from_identity)
        if sender is not None and not sender.get('send'):
            raise AppError('SHARED_SEND_DENIED')  # Checked before anything is stored or sent.
        if draft.schedule_at:
            # The server sends it later; a time already past would go out at once.
            at = datetime.strptime(draft.schedule_at, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if not now + timedelta(minutes=1) <= at <= now + timedelta(days=366):
                raise AppError('INVALID_SCHEDULE')
            if draft.encryption_required and not getattr(self.adapter, 'supports_scheduled_encrypted', False):
                # Never fall back to plaintext: the user chooses to send now instead.
                raise AppError('SCHEDULE_ENCRYPTED_UNSUPPORTED')
        # A durable copy first: if sending fails the user loses nothing.
        self.services['save_draft'](draft=draft)
        if draft.signature_required and (self.crypto is None or self.crypto.certificate_info is None):
            # Never send unsigned when a signature was asked for: unlock, or turn signing off.
            return {'state': 'NeedsSigningKey', 'localId': draft.local_id, 'revision': draft.revision}
        if draft.encryption_required:
            try:
                missing = self.encryption_check(list(draft.to + draft.cc + draft.bcc))['missing']
            except AppError as error:
                if error.code not in NETWORK_CODES:
                    raise
                # Offline: the certificates are looked up again when the message is built,
                # and a missing one fails it there. It is never sent unencrypted.
                missing = []
            if missing:
                # The user decides: remove them, or send this one unencrypted.
                return {'state': 'NeedsCertificates', 'missing': missing, 'localId': draft.local_id, 'revision': draft.revision}
        delay = self.prefs()['undoSeconds'] if hold and not draft.schedule_at else 0
        if delay:
            with self._held_lock:
                self._held[draft.local_id] = {'revision': draft.revision, 'subject': draft.subject, 'until': time.time() + delay}
            return {'state': 'Held', 'localId': draft.local_id, 'revision': draft.revision, 'delayMs': delay * 1000}
        return self._dispatch(draft)

    def _dispatch(self, draft):
        """Hand a stored draft to the durable outbox. A network failure before anything
        was submitted puts it in the offline outbox (离线发件箱); an uncertain outcome
        (Unknown) is never retried by itself."""
        from coremail_desktop.ui.messages import user_message
        try:
            job = self._online(self.services['request_send'], draft=draft)
            state, failure = job.state, job.failure_code
        except AppError as error:
            if error.code not in NETWORK_CODES:
                raise
            state, failure = 'Failed', error.code  # Signing in failed: nothing reached the server.
        if state == 'Failed' and failure in NETWORK_CODES:
            self._queue(draft)
            state = 'Queued'
        else:
            self._unqueue(draft.local_id)
        if state == 'Accepted':
            self.delete_draft(draft.local_id)
            if draft.mode in ('reply', 'reply_all') and draft.reply_source_id:
                self.cache.update(self._reply_id(draft), flags={'replied': True})
        return {'state': state, 'failure': failure, 'localId': draft.local_id, 'revision': draft.revision,
                'subject': draft.subject, 'serverDraft': bool(draft.server_draft_id),
                'failureMessage': user_message(failure) if failure and state != 'Queued' else None,
                'scheduledAt': _schedule_ms(draft.schedule_at) if state == 'Accepted' else None}

    # 撤销发送 --------------------------------------------------------------------
    def release_send(self, local_id, revision):
        """The undo delay is over: send it, unless it was undone in the meantime."""
        with self._held_lock:
            held = self._held.get(local_id)
            if not held or held['revision'] != revision:
                return {'state': 'Cancelled', 'localId': local_id}
            del self._held[local_id]
        draft = self.services['restore_draft'](local_id=local_id)
        if draft.revision != revision:
            return {'state': 'Cancelled', 'localId': local_id}
        return self._dispatch(draft)

    def cancel_send(self, local_id):
        """Undo: nothing was sent; the draft comes back for the composer, intact."""
        with self._held_lock:
            if self._held.pop(local_id, None) is None:
                raise AppError('SEND_NOT_HELD')
        return self.restore_draft(local_id)

    def held_sends(self):
        with self._held_lock:
            return [{'localId': k, 'subject': v['subject'], 'until': v['until']} for k, v in self._held.items()]

    def abandon_held(self):
        """The app is ending during an undo delay: those drafts are not sent. They stay in
        本机草稿 and the next start says so."""
        with self._held_lock:
            held, self._held = self._held, {}
        if held and self.cache is not None:
            stored = self.cache.get('unsentHeld') or []
            self.cache.set('unsentHeld', stored + [{'localId': k, 'subject': v['subject']} for k, v in held.items()])
        return len(held)

    # 离线发件箱 -------------------------------------------------------------------
    # Drafts whose send failed for lack of network before anything was submitted. The
    # list (ids and subjects) is in the encrypted cache; the drafts in 本机草稿.
    def _queue(self, draft):
        queue = [e for e in self.cache.get('outbox') or [] if e['localId'] != draft.local_id]
        self.cache.set('outbox', queue + [{'localId': draft.local_id, 'subject': draft.subject,
                                           'queuedAt': datetime.now().strftime('%Y-%m-%d %H:%M')}])

    def _unqueue(self, local_id):
        queue = self.cache.get('outbox') or []
        if any(e['localId'] == local_id for e in queue):
            self.cache.set('outbox', [e for e in queue if e['localId'] != local_id])

    def outbox(self):
        """发件箱: queued drafts, sends whose outcome is unknown (check 已发送 before
        sending again), and drafts left unsent because the app ended during an undo delay."""
        review = []
        if 'review_jobs' in self.services:
            review = [{'id': job.local_id, 'localId': job.draft.local_id, 'subject': job.draft.subject,
                       'at': (job.submitted_at or '')[:16].replace('T', ' '), 'state': job.state}
                      for job in self.services['review_jobs']()]
        abandoned = self.cache.get('unsentHeld') or []
        if abandoned:
            self.cache.set('unsentHeld', [])
        return {'queued': list(self.cache.get('outbox') or []), 'review': review, 'abandoned': abandoned,
                'held': self.held_sends()}

    def outbox_remove(self, local_id):
        """Take a draft out of the queue (to edit it, or keep it as a draft only)."""
        self._unqueue(local_id)
        return self.outbox()

    def dismiss_review(self, job_id):
        """The user checked 已发送: the uncertain send leaves the list. Nothing is resent."""
        self.services['dismiss_job'](local_id=job_id)
        return self.outbox()

    def flush_outbox(self):
        """Connection is back: send the queued drafts, oldest first, stopping at the
        first one that is still offline."""
        results = []
        for entry in list(self.cache.get('outbox') or []):
            with self._held_lock:
                if entry['localId'] in self._held:
                    continue
            try:
                draft = self.services['restore_draft'](local_id=entry['localId'])
            except Exception:
                self._unqueue(entry['localId'])  # The draft was deleted meanwhile.
                continue
            result = self._dispatch(draft)
            results.append(result)
            if result['state'] == 'Queued':
                break
        return {'results': results, 'queued': len(self.cache.get('outbox') or [])}

    def encryption_check(self, recipients):
        """Which recipients have a company certificate. Bare addresses only."""
        from coremail_desktop.compose.recipients import normalize_recipients
        addresses = list(normalize_recipients(tuple(recipients)))
        found = self._online(self.adapter.public_certificates, addresses) if addresses else {}
        return {'missing': [a for a in addresses if a.casefold() not in found],
                'covered': [a for a in addresses if a.casefold() in found]}

    def _signer(self, draft):
        """The unlocked certificate and key for signing, or a fixed error: never unsigned."""
        from coremail_desktop.compose.recipients import normalize_recipients
        from coremail_desktop.crypto.models import CryptoError
        if self.crypto is None:
            raise AppError('CERT_LOCKED')
        try:
            signer = self.crypto.signing_identity()
        except CryptoError as error:
            raise AppError(error.code) from None
        emails = self.crypto.certificate_info.emails
        if emails and normalize_recipients((draft.from_identity,))[0].casefold() not in emails:
            raise AppError('SIGN_CERT_ADDRESS_MISMATCH')
        return signer

    def _prepare_encrypted(self, draft):
        """Outbox hook: build the S/MIME message (signed and/or encrypted for every
        recipient and the sender)."""
        from coremail_desktop.compose.recipients import normalize_recipients
        from coremail_desktop.compose.smime import build_encrypted_message, build_signed_message
        from coremail_desktop.crypto.backend import describe
        from cryptography import x509
        signer = self._signer(draft) if draft.signature_required else None
        if not draft.encryption_required:
            return build_signed_message(draft, signer, self.display_name)
        recipients = list(normalize_recipients(draft.to + draft.cc + draft.bcc))
        sender = normalize_recipients((draft.from_identity,))[0]
        found = self.adapter.public_certificates(recipients + [sender])
        if any(a.casefold() not in found for a in recipients):
            raise AppError('RECIPIENT_CERT_MISSING')
        if sender.casefold() not in found:
            # Without the sender's own key the sent copy could never be read again.
            raise AppError('OWN_CERT_MISSING')
        certificates = [describe(x509.load_der_x509_certificate(der), 'directory') for der in found.values()]
        return build_encrypted_message(draft, certificates, self.display_name, signer)

    # Sent mail: delivery status and recall ---------------------------------
    def delivery_status(self, mid):
        """Each recipient's delivery/read state, worded from the server's own codes only."""
        from coremail_desktop.compose.tracking import delivery_state
        if not hasattr(self.adapter, 'delivery_history'):
            raise AppError('OPERATION_NOT_ALLOWED')
        rows = []
        remote, box = self._locate(mid)
        for entry in self._online(self.adapter.delivery_history, remote, **self._box_args(box)):
            state = delivery_state(entry['result'], entry['recall'])
            rows.append({'email': entry['to'], 'name': entry['name'], 'text': state['text'], 'kind': state['kind'],
                         'time': entry['time'], 'recallable': entry['recallable']})
        counts = {}
        for row in rows:
            counts[row['text']] = counts.get(row['text'], 0) + 1
        return {'rows': rows, 'counts': counts, 'read': sum(r['kind'] == 'read' for r in rows),
                'recallable': any(r['recallable'] for r in rows)}

    def recall(self, mid):
        """Recall a sent message. Every row is the server's own per-recipient answer."""
        from coremail_desktop.compose.tracking import recall_state
        if not hasattr(self.adapter, 'recall_message') or self._locate(mid)[1]:
            raise AppError('OPERATION_NOT_ALLOWED')  # Shared mailboxes: recall stays in webmail.
        result = self._online(self.adapter.recall_message, str(mid))
        if result['cancelled']:
            return {'cancelled': True, 'rows': [], 'recalled': 0}
        rows = [{'email': email, **recall_state(code)} for email, code in sorted(result['states'].items())]
        return {'cancelled': False, 'rows': rows, 'recalled': sum(r['recalled'] for r in rows)}

    # Scheduled mail ----------------------------------------------------------
    def scheduled(self):
        if not hasattr(self.adapter, 'scheduled_messages'):
            return {'rows': []}
        rows = [_summary_dict(item) for item in self._online(self.adapter.scheduled_messages)]
        if rows:
            self.cache.put_summaries(rows[0]['fid'], rows)  # Lets the reader open them; never a complete window.
        return {'rows': sorted(rows, key=lambda r: r['scheduleAt'] or 0)}

    def unschedule(self, mid):
        self._online(self.adapter.unschedule, str(mid))
        self.cache.update(mid, flags={'scheduleDelivery': False}, scheduleAt=None)
        return True

    # Mail rules and holiday auto-reply (收信规则 / 休假自动回复) ----------------------
    # Both change how the server treats the user's mail, so every write is read back
    # and success is reported only from what the server then holds.
    def _need(self, name):
        if not hasattr(self.adapter, name):
            raise AppError('OPERATION_NOT_ALLOWED')

    def rules(self):
        from coremail_desktop.protocol.rules import rule_view
        self._need('mail_rules')
        raw = self._online(self.adapter.mail_rules)
        self._rules = {r['id']: r for r in raw}
        return {'rules': [rule_view(r) for r in raw]}

    def _rule_targets(self):
        folders = {f['id'] for f in (self._folders or self.folders())}
        tags = {t['id'] for t in self.tags()['tags']}
        return folders, tags

    def save_rule(self, rule):
        from coremail_desktop.protocol.rules import rule_server, rule_view
        self._need('mail_rules')
        rule = dict(rule or {})
        current = self.rules()['rules']
        rule_id = rule.get('id')
        if rule_id is not None:
            old = next((r for r in current if r['id'] == rule_id), None)
            if old is None or not old['editable']:
                raise AppError('RULE_NOT_EDITABLE')
        name = str(rule.get('name') or '').strip()
        if any(r['name'] == name and r['id'] != rule_id for r in current):
            raise AppError('RULE_NAME_EXISTS')  # Webmail: 分类规则名称已存在.
        server = rule_server(rule, *self._rule_targets(), rule_id=rule_id)
        self._online(self.adapter.update_mail_rule if rule_id is not None else self.adapter.add_mail_rule, server)
        result = self.rules()
        saved = next((r for r in result['rules'] if r['name'] == name), None)
        expected = rule_view(server)
        if saved is None or [saved[k] for k in ('conditions', 'actions', 'match', 'stop', 'enabled')] != \
                [expected[k] for k in ('conditions', 'actions', 'match', 'stop', 'enabled')]:
            raise AppError('RULE_UNCONFIRMED')
        return {**result, 'saved': saved['id']}

    def set_rule_enabled(self, rule_id, enabled):
        self._need('mail_rules')
        self.rules()
        raw = self._rules.get(rule_id)
        if raw is None or type(enabled) is not bool:
            raise AppError('INVALID_RULE')
        self._online(self.adapter.set_mail_rule_disabled, rule_id, not enabled)
        result = self.rules()
        if next((r['enabled'] for r in result['rules'] if r['id'] == rule_id), None) is not enabled:
            raise AppError('RULE_UNCONFIRMED')
        return result

    def delete_rule(self, rule_id):
        self._need('mail_rules')
        self.rules()
        if rule_id not in self._rules:
            raise AppError('INVALID_RULE')
        self._online(self.adapter.remove_mail_rule, rule_id)
        result = self.rules()
        if any(r['id'] == rule_id for r in result['rules']):
            raise AppError('RULE_UNCONFIRMED')
        return result

    def move_rule(self, rule_id, step):
        """Move a rule one place up (-1) or down (+1); rules run top to bottom."""
        self._need('mail_rules')
        order = [r['id'] for r in self.rules()['rules']]
        if rule_id not in order or step not in (-1, 1):
            raise AppError('INVALID_RULE')
        i = order.index(rule_id)
        j = i + step
        if not 0 <= j < len(order):
            return {'rules': self.rules()['rules']}
        self._online(self.adapter.move_mail_rule, rule_id, order[j])
        order[i], order[j] = order[j], order[i]
        result = self.rules()
        if [r['id'] for r in result['rules']] != order:
            raise AppError('RULE_UNCONFIRMED')
        return result

    def auto_reply(self):
        from coremail_desktop.protocol.rules import autoreply_view
        self._need('auto_reply')
        attrs = self._online(self.adapter.auto_reply)
        return {**autoreply_view(attrs), 'zone': str(attrs.get('time_zone') or '')}

    def set_auto_reply(self, values):
        from coremail_desktop.protocol.rules import autoreply_server, autoreply_view
        self._need('auto_reply')
        attrs = autoreply_server(values, self._online(self.adapter.account_now))
        self._online(self.adapter.set_auto_reply, attrs)
        now = self.auto_reply()
        expected = autoreply_view(attrs)
        if any(now[k] != expected[k] for k in expected):
            raise AppError('AUTOREPLY_UNCONFIRMED')
        return now

    # Tags (标签) ------------------------------------------------------------------
    # The user's own mailbox only; shared mailboxes keep their tags in webmail.
    def tags(self, refresh=True):
        from coremail_desktop.protocol.rules import TAG_LIMIT, tags_view
        if not hasattr(self.adapter, 'list_tags'):
            return {'tags': [], 'limit': 0}
        cached = self.cache.get('tags')
        if refresh or cached is None:
            try:
                cached = tags_view(self._online(self.adapter.list_tags))
                self.cache.set('tags', cached)
            except AppError as error:
                if cached is None or error.code not in ('NETWORK_ERROR', 'TLS_ERROR'):
                    raise
        return {'tags': cached, 'limit': TAG_LIMIT}

    def save_tag(self, name, color, tag_id=None):
        from coremail_desktop.protocol.rules import TAG_LIMIT, clean_tag_name, new_tag_id, stored_color
        self._need('manage_tags')
        name = clean_tag_name(name)
        current = self.tags()['tags']
        if any(t['name'] == name and t['id'] != tag_id for t in current):
            raise AppError('TAG_EXISTS')
        attrs = {'name': name, 'color': stored_color(color)}
        if tag_id is None:
            if len(current) >= TAG_LIMIT:
                raise AppError('TAG_LIMIT')
            tag_id = new_tag_id({t['id'] for t in current})
            self._online(self.adapter.manage_tags, 'add', [[tag_id, attrs]])
        else:
            if not any(t['id'] == tag_id for t in current):
                raise AppError('INVALID_TAG')
            self._online(self.adapter.manage_tags, 'update', [[tag_id, attrs]])
        result = self.tags()
        saved = next((t for t in result['tags'] if t['id'] == tag_id), None)
        if saved is None or saved['name'] != name:
            raise AppError('TAG_UNCONFIRMED')
        return {**result, 'saved': tag_id}

    def delete_tag(self, tag_id):
        self._need('manage_tags')
        if not any(t['id'] == tag_id for t in self.tags()['tags']):
            raise AppError('INVALID_TAG')
        self._online(self.adapter.manage_tags, 'delete', [tag_id])
        result = self.tags()
        if any(t['id'] == tag_id for t in result['tags']):
            raise AppError('TAG_UNCONFIRMED')
        return result  # Cached rows lose the tag on their next list refresh; the page hides unknown ids.

    def tag_messages(self, ids, add=None, remove=None):
        """Apply and/or remove tags on messages of the user's own mailbox."""
        self._need('update_message_tags')
        ids = [ids] if isinstance(ids, str) else [str(m) for m in ids or []]
        add, remove = [str(t) for t in add or []], [str(t) for t in remove or []]
        known = {t['id'] for t in self.tags(refresh=False)['tags']}
        if not ids or any(self._locate(m)[1] for m in ids):
            raise AppError('OPERATION_NOT_ALLOWED')
        if not (add or remove) or set(add + remove) - known or set(add) & set(remove):
            raise AppError('INVALID_TAG')
        self._online(self.adapter.update_message_tags, ids, add, remove)
        for mid in ids:
            row = self.cache.summary(mid)
            if row is not None:
                tags = [t for t in row.get('tags') or [] if t not in remove]
                self.cache.update(mid, tags=tags + [t for t in add if t not in tags])
        return {'ids': ids, 'add': add, 'remove': remove}

    def tag_view(self, tag_id, offset=0):
        """A tag's mail across the user's folders (trash excluded), newest first."""
        self._need('tagged_messages')
        result = self._online(self.adapter.tagged_messages, str(tag_id), offset, 50)
        rows = [_summary_dict(item) for item in result['messages']]
        for fid in {r['fid'] for r in rows}:
            self.cache.put_summaries(fid, [r for r in rows if r['fid'] == fid])  # Never a complete window.
        total = result['total']
        return {'rows': rows, 'total': total, 'more': offset + len(rows) < total if total is not None else len(rows) == 50}

    # Meeting invitations (会议邀请) ------------------------------------------------
    def _invitation(self, mid, document):
        from coremail_desktop.mime.icalendar import find_invitation
        if document.encryption_state != 'none':
            return None
        raw = self.cache.get_body(mid)
        return find_invitation(raw) if raw else None

    def respond_invitation(self, mid, answer):
        """Accept / tentatively accept / decline: the server informs the organizer."""
        self._need('respond_invitation')
        remote, box = self._locate(mid)
        if box:
            raise AppError('OPERATION_NOT_ALLOWED')  # Shared mailboxes: answer in webmail.
        card = self._invitation(mid, self._document(mid))
        if not card or card['method'] != 'REQUEST' or card['cancelled']:
            raise AppError('INVITE_UNAVAILABLE')
        result = self._online(self.adapter.respond_invitation, remote, answer)
        answers = self.cache.get('inviteAnswers') or {}
        answers[mid] = answer
        self.cache.set('inviteAnswers', dict(list(answers.items())[-500:]))
        return result

    # Background sync -------------------------------------------------------
    def poll(self):
        """Refresh folder counts and the newest inbox page; report new unread mail."""
        inbox = next((f['id'] for f in self._folders if f['role'] == 'inbox'), '1')
        known = self.cache.known_ids(inbox)
        first_sync = not known and not self.cache.get('inboxSynced')
        page = self.messages(inbox, 0, 50)
        self.cache.set('inboxSynced', True)
        fresh = [] if first_sync else [r for r in page['rows'] if r['id'] not in known and not r['flags'].get('read')]
        folders = self.folders(refresh=True)
        result = {'folders': folders, 'inbox': inbox, 'rows': page['rows'], 'total': page['total'], 'new': fresh}
        if self._shared:
            try:
                result['shared'] = self.shared_mailboxes(refresh=True)  # Counts only; no notifications.
            except AppError as error:
                if error.code in REAUTH_CODES:
                    raise
        return result
