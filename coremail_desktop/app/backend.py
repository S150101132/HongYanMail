"""Qt-free application backend for the daily-use client.

Every public method takes and returns JSON-compatible values so the web UI can
call it through the bridge. Calls are made from one worker thread, in order.
"""
import base64
import hashlib
import html
import json
import re
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr
from pathlib import Path

from coremail_desktop.app.cache import MailCache, date_ts
from coremail_desktop.compose.attachments import validate_attachments
from coremail_desktop.compose.service import build_reply
from coremail_desktop.errors import AppError
from coremail_desktop.models import AccountConfig, DraftAttachment, DraftDocument
from coremail_desktop.mime.parser import parse_mime

DEFAULT_PREFS = {'theme': 'system', 'signature': '', 'pollSeconds': 60, 'notify': True,
                 'density': 'comfortable', 'markReadOnOpen': True, 'readingPane': 'right', 'threads': False,
                 'autostart': False}
# Coremail system folder ids. Used only for icons and ordering; the name shown is
# always the server's own folder name.
SYSTEM_ROLES = {'1': 'inbox', '2': 'drafts', '3': 'sent', '4': 'trash', '5': 'spam', '6': 'virus'}
REAUTH_CODES = ('SESSION_EXPIRED', 'NOT_AUTHENTICATED')


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
            'scheduleAt': _schedule_ms(getattr(item, 'schedule_at', None))}


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


def _quote_html(document):
    header = lambda key: ', '.join(document.headers.get(key, ()))
    rows = ''.join(f'<div><b>{label}：</b>{html.escape(header(key))}</div>'
                   for label, key in (('发件人', 'from'), ('发送时间', 'date'), ('收件人', 'to'), ('抄送', 'cc'), ('主题', 'subject'))
                   if header(key))
    original = document.html or '<div style="white-space:pre-wrap">' + html.escape(document.text) + '</div>'
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
        self._contact_index = None
        self._contact_index_size = -1
        self._docs = {}
        self._signer_directory = {}  # (sender, signer fingerprint) -> matches the company directory
        self.autostart = None  # Set by the app (not in demo mode): reads and writes the Windows Run key.

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
            self.cache.close()
        self.cache = self.account = None
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
        if self.autostart is not None and type(values.get('autostart')) is bool:
            prefs['autostart'] = self.autostart.set_enabled(values['autostart'])
        self.cache.set('prefs', prefs)
        return prefs

    # Folders and lists -----------------------------------------------------
    def _folder_dict(self, folder):
        counts = dict(folder.counts)
        role = folder.role or SYSTEM_ROLES.get(folder.remote_id)
        return {'id': folder.remote_id, 'name': folder.name, 'role': role,
                'total': counts.get('messageCount'), 'unread': counts.get('unreadMessageCount', 0)}

    def folders(self, refresh=True):
        if refresh or not self._folders:
            try:
                folders = self._online(self.adapter.list_folders)
            except AppError as error:
                cached = self.cache.get('folders') if self.cache else None
                if cached and error.code in ('NETWORK_ERROR', 'TLS_ERROR'):
                    return cached
                raise
            order = {'inbox': 0, 'drafts': 2, 'sent': 3, 'trash': 8, 'spam': 7, 'virus': 9}
            self._folders = sorted((self._folder_dict(f) for f in folders),
                                   key=lambda f: (order.get(f['role'], 5), f['role'] is None, f['name']))
            self.cache.set('folders', self._folders)
        return self._folders

    def cached_view(self, folder_id, unread_only=False, limit=50):
        """Instant first paint from the encrypted cache, before any network call."""
        rows, total = self.cache.summaries(str(folder_id), 0, limit, unread_only)
        return {'folders': self.cache.get('folders') or [], 'rows': rows, 'cachedTotal': total}

    def messages(self, folder_id, offset=0, limit=50, unread_only=False):
        folder_id = str(folder_id)
        summaries = self._online(self.adapter.list_messages, folder_id, offset, limit)
        rows = [_summary_dict(item) for item in summaries]
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

    def _document(self, mid):
        if mid in self._docs:
            return self._docs[mid]
        raw = self.cache.get_body(mid)
        if raw is None:
            raw = self._online(self.adapter.fetch_mime, mid)
            self.cache.put_body(mid, raw)
        document = self._parse(raw, mid)
        if document.encryption_state != 'locked':
            self._docs = {mid: document, **dict(list(self._docs.items())[:7])}
        return document

    def open_message(self, mid, mark_read=None):
        document = self._document(mid)
        header = lambda key: ', '.join(document.headers.get(key, ()))
        body_html = document.html
        remote = False
        if body_html:
            def cid(match):
                key = match.group(2)
                found = document.cid_resources.get(key)
                if not found:
                    return match.group(0)
                return f'src={match.group(1)}data:{found[0]};base64,{base64.b64encode(found[1]).decode("ascii")}{match.group(1)}'
            body_html = re.sub(r'src=(["\'])cid:([^"\']+)\1', cid, body_html)
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
                                  for a in document.attachments]}
        if mark_read is None:
            mark_read = self.prefs()['markReadOnOpen']
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
                    self.cache.put_body(mid, self.adapter.fetch_mime(mid))
                    fetched += 1
            except Exception:
                continue
        return fetched

    def attachment(self, mid, part):
        document = self._document(mid)
        data = document.attachment_payloads.get(part)
        info = next((a for a in document.attachments if a.part_id == part), None)
        if data is None or info is None:
            raise AppError('MESSAGE_UNAVAILABLE')
        return info.name, info.media_type, data

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

    def _count(self, fid, delta_unread=0, delta_total=0):
        folder = next((f for f in self._folders if f['id'] == fid), None)
        if folder:
            folder['unread'] = max(0, (folder['unread'] or 0) + delta_unread)
            if folder['total'] is not None:
                folder['total'] = max(0, folder['total'] + delta_total)

    def set_flags(self, mid, read=None, flagged=None):
        flags = {k: v for k, v in (('read', read), ('flagged', flagged)) if v is not None}
        ids = [mid] if isinstance(mid, str) else [str(m) for m in mid]
        before = {m: self.cache.summary(m) or {} for m in ids}
        done, failed = self._apply(ids, self.adapter.set_message_flags, getattr(self.adapter, 'set_flags_many', None), flags)
        for m in done:
            was = before[m].get('flags', {}).get('read')
            if 'read' in flags and was is not None and bool(was) != read:
                self._count(before[m].get('fid'), -1 if read else 1)
            self.cache.update(m, flags=flags)
        if self._folders:
            self.cache.set('folders', self._folders)
        return flags if isinstance(mid, str) else {'flags': flags, 'done': done, 'failed': failed, 'folders': self._folders}

    def move(self, mid, folder_id):
        folder_id = str(folder_id)
        ids = [mid] if isinstance(mid, str) else [str(m) for m in mid]
        before = {m: self.cache.summary(m) or {} for m in ids}
        done, failed = self._apply(ids, self.adapter.move_message, getattr(self.adapter, 'move_many', None), folder_id)
        for m in done:
            unread = 0 if before[m].get('flags', {}).get('read', True) else 1
            if before[m].get('fid') and before[m]['fid'] != folder_id:
                self._count(before[m]['fid'], -unread, -1)
                self._count(folder_id, unread, 1)
            self.cache.update(m, fid=folder_id)
        if self._folders:
            self.cache.set('folders', self._folders)
        return True if isinstance(mid, str) else {'done': done, 'failed': failed, 'folders': self._folders}

    def delete(self, mid):
        ids = [mid] if isinstance(mid, str) else [str(m) for m in mid]
        trash = next((f['id'] for f in self._folders if f['role'] == 'trash'), '4')
        # Permanent deletion is deliberately not offered: mail already in the
        # trash stays there.
        movable = [m for m in ids if (self.cache.summary(m) or {}).get('fid') != trash]
        if not movable:
            raise AppError('OPERATION_NOT_ALLOWED')
        if isinstance(mid, str):
            return self.move(mid, trash)
        return {**self.move(movable, trash), 'skipped': [m for m in ids if m not in movable]}

    # Search ----------------------------------------------------------------
    def search_local(self, query, filters=None):
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

    def members(self, dn, refresh=False):
        if not isinstance(dn, str) or not dn or len(dn) > 500 or not hasattr(self.adapter, 'directory_members'):
            raise AppError('INVALID_CONTACT')
        key = 'members:' + hashlib.sha256(dn.encode('utf-8')).hexdigest()[:24]
        value = self._stale(key, lambda: self.adapter.directory_members(dn), refresh)
        return {**value['data'], 'at': value['at'], 'cached': value['cached']}

    def personal_contacts(self, refresh=False):
        if not hasattr(self.adapter, 'personal_contacts'):
            return {'rows': [], 'at': None, 'cached': False}
        value = self._stale('pab', self.adapter.personal_contacts, refresh)
        return {'rows': value['data'], 'at': value['at'], 'cached': value['cached']}

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

    def compose(self, mode='new', mid=None):
        identity = self.identities[0] if self.identities else (self.account or '')
        base = {'localId': uuid.uuid4().hex, 'revision': 0, 'mode': mode, 'from': identity,
                'to': [], 'cc': [], 'bcc': [], 'subject': '', 'html': '<p><br></p>' + self._signature_html(),
                'replySourceId': None, 'referenceHeaders': {}, 'attachments': [], 'encrypt': None, 'sign': None,
                'scheduleAt': None, 'receipt': False}
        if mode == 'new' or not mid:
            return base
        document = self._document(mid)
        if mode in ('reply', 'reply_all'):
            draft = build_reply(document, set(self.identities), mode == 'reply_all', identity)
            return {**base, 'mode': draft.mode, 'to': list(draft.to), 'cc': list(draft.cc), 'subject': draft.subject,
                    'html': '<p><br></p>' + self._signature_html() + _quote_html(document),
                    'replySourceId': draft.reply_source_id, 'referenceHeaders': dict(draft.reference_headers),
                    'encrypt': True if document.encryption_state != 'none' else None}
        if mode == 'forward':
            subject = next(iter(document.headers.get('subject', ())), '')
            attachments = [{'name': a.name, 'type': a.media_type, 'size': len(document.attachment_payloads[a.part_id]),
                            'data': base64.b64encode(document.attachment_payloads[a.part_id]).decode('ascii')}
                           for a in document.attachments if a.part_id in document.attachment_payloads]
            return {**base, 'mode': 'forward', 'subject': ('' if subject.lower().startswith('fwd:') else 'Fwd: ') + subject,
                    'html': '<p><br></p>' + self._signature_html() + _quote_html(document), 'attachments': attachments,
                    'encrypt': True if document.encryption_state != 'none' else None}
        raise AppError('INVALID_MAIL_ACTION')

    def _draft(self, value):
        attachments = tuple(DraftAttachment(str(a['name']), str(a.get('type') or 'application/octet-stream'),
                                            base64.b64decode(a['data'], validate=True)) for a in value.get('attachments', ()))
        validate_attachments(attachments)
        listify = lambda key: tuple(str(v).strip() for v in value.get(key, ()) if str(v).strip())
        mode = value.get('mode', 'new')
        return DraftDocument(str(value['localId']), int(value.get('revision', 0)), mode,
                             str(value.get('from') or self.identities[0]), listify('to'), listify('cc'), listify('bcc'),
                             str(value.get('subject', '')), str(value.get('html', '')),
                             {str(k): str(v) for k, v in dict(value.get('referenceHeaders') or {}).items()},
                             value.get('encrypt') is True, value.get('replySourceId') if mode in ('reply', 'reply_all') else None,
                             attachments, True, signature_required=value.get('sign') is True,
                             schedule_at=_schedule_iso(value.get('scheduleAt')), request_receipt=value.get('receipt') is True)

    def save_draft(self, value):
        draft = self._draft(value)
        draft = replace(draft, revision=draft.revision + 1)
        self.services['save_draft'](draft=draft)
        return {'localId': draft.local_id, 'revision': draft.revision}

    def list_drafts(self):
        return self.services['list_drafts']()

    def restore_draft(self, local_id):
        draft = self.services['restore_draft'](local_id=local_id)
        body = draft.body if draft.is_html else '<div style="white-space:pre-wrap">' + html.escape(draft.body) + '</div>'
        return {'localId': draft.local_id, 'revision': draft.revision, 'mode': draft.mode, 'from': draft.from_identity,
                'to': list(draft.to), 'cc': list(draft.cc), 'bcc': list(draft.bcc), 'subject': draft.subject,
                'html': body, 'replySourceId': draft.reply_source_id, 'referenceHeaders': dict(draft.reference_headers),
                'encrypt': True if draft.encryption_required else None,
                # Signing follows encryption unless the user chose otherwise.
                'sign': True if draft.signature_required else False if draft.encryption_required else None,
                'scheduleAt': _schedule_ms(draft.schedule_at), 'receipt': draft.request_receipt,
                'attachments': [{'name': a.name, 'type': a.media_type, 'size': len(a.payload),
                                 'data': base64.b64encode(a.payload).decode('ascii')} for a in draft.attachments]}

    def delete_draft(self, local_id):
        self.services['delete_draft'](local_id=local_id)
        return True

    def send(self, value):
        draft = self._draft(value)
        draft = replace(draft, revision=draft.revision + 1)
        if not (draft.to or draft.cc or draft.bcc):
            raise AppError('RECIPIENT_REQUIRED')
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
            missing = self.encryption_check(list(draft.to + draft.cc + draft.bcc))['missing']
            if missing:
                # The user decides: remove them, or send this one unencrypted.
                return {'state': 'NeedsCertificates', 'missing': missing, 'localId': draft.local_id, 'revision': draft.revision}
        job = self._online(self.services['request_send'], draft=draft)
        if job.state == 'Accepted':
            self.delete_draft(draft.local_id)
            if draft.mode in ('reply', 'reply_all') and draft.reply_source_id:
                self.cache.update(draft.reply_source_id, flags={'replied': True})
        from coremail_desktop.ui.messages import user_message
        return {'state': job.state, 'failure': job.failure_code, 'localId': draft.local_id, 'revision': draft.revision,
                'failureMessage': user_message(job.failure_code) if job.failure_code else None,
                'scheduledAt': _schedule_ms(draft.schedule_at) if job.state == 'Accepted' else None}

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
        for entry in self._online(self.adapter.delivery_history, str(mid)):
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
        if not hasattr(self.adapter, 'recall_message'):
            raise AppError('OPERATION_NOT_ALLOWED')
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
        return {'folders': folders, 'inbox': inbox, 'rows': page['rows'], 'total': page['total'], 'new': fresh}
