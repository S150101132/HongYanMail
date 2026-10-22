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
                 'autostart': False, 'precheck': True, 'sidebarWidth': 232, 'listWidth': 400, 'undoSeconds': 10,
                 'ctTreeWidth': 250, 'ctListWidth': 340, 'tkListWidth': 460, 'tagsOpen': False, 'sidebarHidden': False,
                 'closeAction': '',  # ✕ of the main window: '' asks, 'tray' keeps running, 'quit' exits.
                 'understand': '',  # 理解层: '' not asked yet, 'on', 'off'.
                 'listGist': True,  # The list's second line shows the card's 要点 instead of the body's start.
                 'toolbar': 'lean',  # A4: 'lean' folds low-use buttons into ⋯ / 格式 / the send menu; 'full' shows them all.
                 'startPage': 'inbox',  # R2a/R3b: 'inbox' opens the 收件箱 after signing in, 'today' 今天.
                 'memoryFacts': False,  # R3c: the 理解层 also keeps project facts (工作记忆); off: it only sorts mail.
                 'hiddenFolders': []}  # Sidebar entries the user chose not to show: folder ids, or 'local:<entry>'.
CLOSE_ACTIONS = ('', 'tray', 'quit')
UNDO_CHOICES = (0, 5, 10, 20, 30)  # 撤销发送 delay in seconds; 0 sends at once.
NETWORK_CODES = ('NETWORK_ERROR', 'TLS_ERROR')
MAX_EML_BYTES = 40 * 1024 * 1024
# Column widths the page may store (px); the page enforces the same bounds while dragging.
WIDTH_LIMITS = {'sidebarWidth': (180, 400), 'listWidth': (280, 900), 'ctTreeWidth': (160, 480), 'ctListWidth': (240, 720), 'tkListWidth': (320, 900)}
TEMPLATE_LIMIT = 100
# 邮件签名: pictures (a company logo) live in the HTML as data: URIs and go out as
# inline cid: parts like any body image (compose/inline.py).
SIGNATURE_IMAGES, SIGNATURE_IMAGE_BYTES, SIGNATURE_TEXT = 5, 1024 * 1024, 20000
# 收件人组: local only (encrypted cache kv), never on the server.
GROUP_LIMIT, GROUP_MEMBERS, GROUP_FIELDS = 100, 200, ('to', 'cc')
ADDRESS_RE = re.compile(r'^[^\s<>@,;"]+@[^\s<>@,;"]+\.[^\s<>@,;"]+$')
# Coremail system folder ids. Used only for icons and ordering; the name shown is
# always the server's own folder name.
SYSTEM_ROLES = {'1': 'inbox', '2': 'drafts', '3': 'sent', '4': 'trash', '5': 'spam', '6': 'virus'}
REAUTH_CODES = ('SESSION_EXPIRED', 'NOT_AUTHENTICATED')
FOLDER_ORDER = {'inbox': 0, 'drafts': 2, 'sent': 3, 'trash': 8, 'spam': 7, 'virus': 9}
MAX_FOLDER_DEPTH = 5  # Nesting levels the server allows (index.jsp maxFolderNestLevel).
SENDER_LIST_LIMIT = 500
# Attachment preview. Images and PDFs are recognised by their bytes, never by name alone;
# SVG is shown only sanitised and as an <img>; HTML and Office documents become sanitised
# HTML shown in the same script-less, network-less frame as mail bodies.
PREVIEW_IMAGE_LIMIT = 20 * 1024 * 1024
PREVIEW_PDF_LIMIT = 50 * 1024 * 1024
PREVIEW_TEXT_LIMIT = 2 * 1024 * 1024
PREVIEW_HTML_LIMIT = 5 * 1024 * 1024
PREVIEW_OFFICE_LIMIT = 30 * 1024 * 1024
PREVIEW_TEXT = ('.txt', '.log', '.csv', '.tsv', '.md', '.markdown', '.json', '.xml', '.ini', '.cfg', '.conf', '.yaml', '.yml',
                '.eml', '.toml', '.properties', '.rst', '.tex', '.srt', '.vtt', '.ics', '.vcf', '.diff', '.patch', '.sql',
                '.py', '.pyw', '.js', '.mjs', '.ts', '.tsx', '.jsx', '.vue', '.java', '.kt', '.kts', '.gradle', '.groovy', '.scala',
                '.c', '.h', '.cc', '.cpp', '.cxx', '.hpp', '.cs', '.go', '.rs', '.rb', '.php', '.pl', '.lua', '.r', '.swift',
                '.m', '.dart', '.sh', '.bash', '.bat', '.cmd', '.ps1', '.psm1', '.css', '.scss', '.sass', '.less', '.proto',
                '.graphql', '.dockerfile', '.env', '.gitignore', '.editorconfig', '.reg', '.nfo', '.asc', '.pem', '.crt')
PREVIEW_HTML = ('.html', '.htm', '.xhtml')
PREVIEW_OFFICE = {'.docx': 'docx', '.docm': 'docx', '.dotx': 'docx', '.dotm': 'docx',
                  '.xlsx': 'xlsx', '.xlsm': 'xlsx', '.xltx': 'xlsx', '.xltm': 'xlsx',
                  '.pptx': 'pptx', '.pptm': 'pptx', '.ppsx': 'pptx', '.ppsm': 'pptx', '.potx': 'pptx'}
# 打开 never starts programs or scripts straight from a mail; they can still be saved.
BLOCKED_OPEN = ('.exe', '.com', '.scr', '.pif', '.bat', '.cmd', '.msi', '.msp', '.mst', '.cpl', '.msc', '.jar', '.lnk',
                '.url', '.website', '.appref-ms', '.application', '.gadget', '.hta', '.js', '.jse', '.vb', '.vbs', '.vbe',
                '.wsf', '.wsh', '.wsc', '.ws', '.ps1', '.ps1xml', '.ps2', '.psc1', '.psm1', '.reg', '.inf', '.scf', '.sct',
                '.shb', '.shs', '.chm', '.hlp', '.vsto', '.xll', '.dll', '.sys', '.drv', '.ocx', '.library-ms', '.settingcontent-ms')


def is_attached_mail(name, media_type=''):
    return str(media_type or '').lower() == 'message/rfc822' or str(name or '').lower().endswith('.eml')


def preview_kind(name, data):
    """'image/png' ... for an image, 'pdf', 'html', 'docx' / 'xlsx' / 'pptx', 'text' or None."""
    head, lower = bytes(data[:16]), str(name or '').lower()
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if head.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    if head[:2] == b'BM' and len(data) > 26:
        return 'image/bmp'
    if head[:4] == b'\x00\x00\x01\x00' and lower.endswith(('.ico', '.cur')):
        return 'image/x-icon'
    if head[4:12] in (b'ftypavif', b'ftypavis'):
        return 'image/avif'
    if head[:4] in (b'II*\x00', b'MM\x00*'):
        return 'image/tiff'
    if head.startswith(b'%PDF-'):
        return 'pdf'
    if head.startswith(b'PK\x03\x04') and lower.endswith(tuple(PREVIEW_OFFICE)):
        return PREVIEW_OFFICE[lower[lower.rindex('.'):]]
    if b'\x00' in bytes(data[:8192]):
        return None
    if lower.endswith('.svg'):
        return 'image/svg+xml'  # Only ever shown after office.sanitize_svg.
    if lower.endswith(PREVIEW_HTML):
        return 'html'
    if lower.endswith(PREVIEW_TEXT):
        return 'text'
    return None


def _decode_text(raw, declared=None):
    """Text of unknown encoding: BOM, the declared charset, UTF-8, then GB18030."""
    if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
        return raw.decode('utf-16', errors='replace')
    for encoding in ([declared] if declared else []) + ['utf-8-sig', 'gb18030']:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode('utf-8', errors='replace')


def _with_images(source, images):
    """cid: pictures of a converted document as data: URIs."""
    def cid(match):
        found = images.get(match.group(2))
        return f'src={match.group(1)}data:{found[0]};base64,{base64.b64encode(found[1]).decode("ascii")}{match.group(1)}' if found else ''
    return re.sub(r'src=(["\'])cid:([^"\']+)\1', cid, source)


def safe_file_name(name):
    """A Windows-safe file name that keeps the extension (for the opened/ copy)."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(name or '')).replace(',', '，').strip().rstrip('. ')
    stem, dot, ext = name.rpartition('.')
    if not dot:
        stem, ext = name, ''
    if re.fullmatch(r'(?i)(con|prn|aux|nul|com\d|lpt\d)', stem.strip()):
        stem = '_' + stem
    ext = ext[:16]
    stem = (stem or '附件')[:max(1, 120 - len(ext))]
    return stem + ('.' + ext if ext else '')


def shell_open(path):
    """Open a file with its default program, started by the desktop shell rather than as a
    child of this process: explorer.exe hands the file to the running Explorer and exits.
    A program started straight from here inherits Hongyan's environment and restrictions
    (the same ones that break Hongyan's own Chromium sandbox) and its start-up handshake
    (Excel's DDE) used to hold up the window."""
    import os
    import subprocess
    if os.name != 'nt':
        subprocess.Popen(['xdg-open', str(path)], close_fds=True)
        return
    explorer = Path(os.environ.get('SystemRoot') or r'C:\Windows') / 'explorer.exe'
    try:
        subprocess.Popen([str(explorer), str(path)], close_fds=True)
    except OSError:
        os.startfile(str(path))  # noqa: S606 -- the user's own 打开; executables are refused before this.
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


def _check_signature(signature):
    """The signature's pictures and text within SIGNATURE_* (the text counted without them)."""
    try:
        template, images = extract_inline(signature)
    except AppError:
        raise AppError('INVALID_SIGNATURE') from None
    if (len(images) > SIGNATURE_IMAGES or sum(len(i.payload) for i in images) > SIGNATURE_IMAGE_BYTES
            or len(template) > SIGNATURE_TEXT):
        raise AppError('SIGNATURE_TOO_LARGE')


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


# 理解层: which mail is read, how far back and how much per round (design 4.2 / 4.4).
UNDERSTAND_SKIP = ('sent', 'drafts', 'trash', 'spam', 'virus')
UNDERSTAND_DAYS, UNDERSTAND_ROUND = 30, 10
# 催办 (M3): sent mail that asked something of its To recipients and got no reply.
FOLLOWUP_DAYS, FOLLOWUP_KEEP = 3, 500
TODAY_DAYS = 3  # 今天 (R2a): tasks due within this many days are on the list.
ASKING = re.compile(r'[?？]|请|麻烦|能否|是否|可否|确认|please|could you|can you|kindly|let me know|advise', re.I)


def _fact_view(fact):
    """A memory fact for the page: its sources without message ids."""
    return {'id': fact['id'], 'project': fact['project'], 'type': fact['type'], 'text': fact['text'], 'date': fact.get('date') or '',
            'state': fact['state'], 'actor': fact.get('actor') or 'ai', 'was': fact.get('was', ''),
            'sources': [{'mid': s.get('mid', ''), 'subject': s.get('subject', ''), 'date': s.get('date', ''), 'from': s.get('from', ''),
                         'internal': bool(s.get('internal'))} for s in fact.get('sources') or ()]}


def _card_view(card):
    """What the page sees of a card (its facts go to 工作记忆 and are shown from there)."""
    return {'kind': card.get('kind'), 'sure': bool(card.get('sure')), 'gist': card.get('gist') or '', 'tag': card.get('tag') or '', 'ask': card.get('ask') or '',
            'due': card.get('due') or '', 'project': card.get('project') or '', 'done': bool(card.get('done')), 'answered': card.get('answered') or '',
            'actor': card.get('actor') or 'ai', 'rule': bool(card.get('rule')), 'model': card.get('model') or '', 'at': card.get('at') or '',
            'task': (card.get('task') or {}).get('id') or 0, 'why': (card.get('task') or {}).get('why') or ''}


# Conversations: messages whose subjects match once reply/forward prefixes are gone.
# The page groups its list with the same rule (threadKey in app.js).
SUBJECT_PREFIX = re.compile(r'^\s*(?:re|fwd?|fw|回复|转发|答复)\s*(?:\[\d+\])?\s*[:：]\s*', re.I)
THREAD_SKIP = ('trash', 'spam', 'virus', 'drafts')  # Folders a conversation does not reach into.


def thread_subject(subject):
    text, prev = str(subject or '').strip(), None
    while prev != text:
        prev, text = text, SUBJECT_PREFIX.sub('', text).strip()
    return text


def thread_key(subject):
    return ' '.join(thread_subject(subject).split()).lower()


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
        self.open_file = shell_open  # 打开 for attachments; tests replace it.
        self.convert_image = None  # Set by the app: image bytes -> PNG bytes (Qt), for TIFF previews.
        # 撤销发送: drafts waiting out their undo delay, local id -> {revision, subject, until}.
        # Memory only on purpose: if the app ends during the delay nothing is sent and
        # the draft stays in 本机草稿 (abandon_held records which ones).
        self._held = {}
        self._held_lock = threading.Lock()
        self._imported = {}  # 'eml:<digest>' -> raw bytes of .eml files opened read-only.
        self.archive = None  # 本地存档: the catalog of the official client's archmails (archive/catalog.py).
        self._archive_scan = None  # {'done', 'total'} while a scan runs.
        self._archive_key = ''
        self.tasks = None  # 任务中心 (tasks/center.py), opened on first use.
        self.memory = None  # 理解层 (memory/store.py): cards and correction rules of this account.
        self._understand_failed = {}  # mid -> code: mail the 理解层 could not read this session (not retried).
        self.ai_client = None  # Demo and tests: a stand-in chat client used instead of the configured one.
        self._ai_lock = threading.Lock()
        self._ask_cancel = threading.Event()  # 问邮箱: set by the page's 取消, checked between steps.
        self._chats = {}  # 问邮箱 conversations, in memory only: id -> {'tools', 'turns', 'shown'}.
        self._plans = {}  # 助理 plans waiting for 执行, in memory only: id -> {'kind', 'actions', 'chat', ...}.
        self._ai_cancel = threading.Event()  # Streamed AI text: set by the page's 停止.

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
        if self.archive:
            self.archive.close()
        if self.memory:
            self.memory.close()
        self.cache = self.account = self.archive = self.memory = None
        self._shared = {}
        self._chats.clear()
        self._plans.clear()
        return True

    def _open_account(self, server, username, online=True):
        account_key = hashlib.sha256((server + '\n' + username.casefold()).encode('utf-8')).hexdigest()[:24]
        if self.cache is None or self.account != username:
            if self.cache:
                self.cache.close()
            self.cache = MailCache(self.data_dir / 'cache' / (account_key + '.db'), self.protector)
            if self.archive:
                self.archive.close()
            self.archive = None
            self._archive_key = account_key
            if self.memory:
                self.memory.close()
            from coremail_desktop.memory.store import MemoryStore
            self._memory_error = ''
            try:
                self.memory = MemoryStore(self.data_dir / 'cache' / (account_key + '.memory.db'), self.protector)
            except AppError as error:
                self.memory = None  # The 理解层 is optional; mail works without it.
                self._memory_error = error.code
            self._understand_failed = {}
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
        _check_signature(prefs['signature'])
        if prefs['closeAction'] not in CLOSE_ACTIONS:
            prefs['closeAction'] = ''
        if prefs['understand'] not in ('', 'on', 'off'):
            prefs['understand'] = ''
        if prefs['startPage'] not in ('today', 'inbox'):
            prefs['startPage'] = 'inbox'
        if prefs['toolbar'] not in ('lean', 'full'):
            prefs['toolbar'] = 'lean'
        if prefs['undoSeconds'] not in UNDO_CHOICES:
            prefs['undoSeconds'] = DEFAULT_PREFS['undoSeconds']
        for key, (low, high) in WIDTH_LIMITS.items():
            prefs[key] = max(low, min(int(prefs[key]), high))
        hidden = [v for v in prefs['hiddenFolders'] if isinstance(v, str) and 0 < len(v) <= 200]
        prefs['hiddenFolders'] = list(dict.fromkeys(hidden))[:300]
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
        if mid.startswith('arc:'):
            return self._archive_raw(mid)
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
        summary = self.cache.summary(mid) or self._archive_summary(mid) or {}
        result = {'id': mid, 'fid': summary.get('fid'), 'subject': header('subject') or summary.get('subject', ''),
                  'from': _people(header('from')), 'to': _people(header('to')), 'cc': _people(header('cc')),
                  'replyTo': _people(header('reply-to')), 'date': summary.get('date') or header('date'),
                  'html': body_html, 'text': document.text, 'hasRemoteImages': remote,
                  'encryption': document.encryption_state, 'signature': self._signature_view(document),
                  'aiAllowed': document.encryption_state == 'decrypted' and self._encrypted_ok(mid),
                  'flags': summary.get('flags', {}),
                  'scheduled': bool(summary.get('flags', {}).get('scheduleDelivery')), 'scheduleAt': summary.get('scheduleAt'),
                  'attachments': [{'part': a.part_id, 'name': a.name, 'type': a.media_type, 'size': a.size}
                                  for a in document.attachments],
                  'mailbox': self._box_view(self._locate(mid)[1]), 'tags': list(summary.get('tags') or [])}
        if self._local_only(mid):
            result['imported'] = True  # A local .eml file or archived mail: read-only, not on the server.
            result['archived'] = mid.startswith('arc:')
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

    def open_attachment(self, mid, part):
        """打开: a copy in opened/ handed to the default program. Runs on a worker thread,
        so the window never waits for the program to start."""
        name, _kind, data = self.attachment(mid, part)
        if str(name).lower().rstrip('. ').endswith(BLOCKED_OPEN):
            raise AppError('ATTACHMENT_BLOCKED')
        # The data folder is known to be writable; some machines forbid TEMP for scripts.
        folder = self.data_dir / 'opened' / uuid.uuid4().hex[:12]
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / safe_file_name(name)
        target.write_bytes(data)
        self.open_file(target)
        return True

    def attachment_preview(self, mid, part):
        """An attachment for the in-app viewer: an image as a data: URI, plain text,
        sanitised HTML (HTML files, Word, PowerPoint), the sheets of a workbook as tables,
        or for a PDF only its name (the shell renders its pages to images)."""
        from coremail_desktop.mime import office
        from coremail_desktop.mime.sanitizer import sanitize_rich_html
        name, _kind, data = self.attachment(mid, part)
        kind = preview_kind(name, data)
        result = {'name': name, 'size': len(data)}
        try:
            if kind == 'pdf' and len(data) <= PREVIEW_PDF_LIMIT:
                return {**result, 'kind': 'pdf'}
            if kind and kind.startswith('image/') and len(data) <= PREVIEW_IMAGE_LIMIT:
                if kind == 'image/svg+xml':
                    data = office.sanitize_svg(data)
                elif kind == 'image/tiff':
                    if self.convert_image is None:
                        raise AppError('PREVIEW_UNAVAILABLE')
                    data, kind = self.convert_image(data), 'image/png'
                    if not data:
                        raise AppError('PREVIEW_UNAVAILABLE')
                return {**result, 'kind': 'image', 'data': f'data:{kind};base64,' + base64.b64encode(data).decode('ascii')}
            if kind == 'text':
                return {**result, 'kind': 'text', 'text': _decode_text(bytes(data[:PREVIEW_TEXT_LIMIT])),
                        'truncated': len(data) > PREVIEW_TEXT_LIMIT}
            if kind == 'html' and len(data) <= PREVIEW_HTML_LIMIT:
                raw = bytes(data)
                declared = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:4096], re.I)
                body = sanitize_rich_html(_decode_text(raw, declared and declared.group(1).decode('ascii')), {}, data_images=True)
                return {**result, 'kind': 'html', 'doc': 'html', 'html': body,
                        'hasRemoteImages': bool(re.search(r'<img[^>]+src=["\']https?://', body, re.I))}
            if kind in ('docx', 'pptx') and len(data) <= PREVIEW_OFFICE_LIMIT:
                source, images, truncated = (office.docx_html if kind == 'docx' else office.pptx_html)(data)
                body = _with_images(sanitize_rich_html(source, images), images)
                return {**result, 'kind': 'html', 'doc': kind, 'html': body, 'truncated': truncated}
            if kind == 'xlsx' and len(data) <= PREVIEW_OFFICE_LIMIT:
                return {**result, 'kind': 'sheet', 'sheets': office.xlsx_sheets(data)}
        except office.OfficeError:
            pass  # Damaged, password-protected or over a limit: 用系统中的程序打开.
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
        (mail='trash', still recoverable there) or to 收件箱 (mail='inbox'); any other
        choice keeps the folder. Mail is never deleted with the folder. Sub-folders
        have to go first."""
        folder = self._user_folder(folder_id, refresh=True)
        if any(f['parent'] == folder['id'] for f in self._folders):
            raise AppError('FOLDER_HAS_CHILDREN')
        moved = 0
        if folder['total']:
            if mail not in ('trash', 'inbox'):
                raise AppError('FOLDER_NOT_EMPTY')
            target = self._role_id(mail, '4' if mail == 'trash' else '1')
            ids = self._folder_ids(folder['id'], folder['total'])
            for start in range(0, len(ids), 100):
                result = self.move(ids[start:start + 100], target)
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

    # Conversations (Outlook-style: expanded in place under the list row) -------------
    def _thread_rows(self, mid):
        """(key, mailbox, rows) of the conversation mid belongs to, from the cache:
        received and sent mail alike, never trash, spam or drafts."""
        row = self.cache.summary(mid)
        if row is None:
            return '', None, []
        key, box = thread_key(row.get('subject')), row.get('mailbox')
        if not key:
            return '', box, [row]
        skip = {f['id'] for f in self._all_folders() if f.get('role') in THREAD_SKIP}
        return key, box, self.cache.matching(lambda r: r.get('mailbox') == box and str(r.get('fid')) not in skip
                                             and thread_key(r.get('subject')) == key)

    def thread_counts(self, ids):
        """How many cached messages each listed message's conversation holds (instant)."""
        skip = {f['id'] for f in self._all_folders() if f.get('role') in THREAD_SKIP}
        counts = {}
        for r in self.cache.matching(lambda r: str(r.get('fid')) not in skip):
            key = (r.get('mailbox'), thread_key(r.get('subject')))
            counts[key] = counts.get(key, 0) + 1
        result = {}
        for mid in list(ids or [])[:500]:
            row = isinstance(mid, str) and self.cache.summary(mid)
            if row:
                key = thread_key(row.get('subject'))
                result[mid] = counts.get((row.get('mailbox'), key), 1) if key else 1
        return result

    def thread(self, mid):
        """Every message of the conversation, newest first. The cache answers first;
        a subject search on the server adds mail not yet cached (sent mail in a folder
        never opened). Offline the cached part is returned."""
        key, box, rows = self._thread_rows(mid)
        if key and box is None and hasattr(self.adapter, 'search_messages'):
            subject = thread_subject((self.cache.summary(mid) or {}).get('subject'))
            try:
                found = self._online(self.adapter.search_messages, subject[:200], 0, 100, filters={'scope': 'subject'})
                fresh = [_summary_dict(item) for item in found['messages']]
                fresh = [r for r in fresh if thread_key(r['subject']) == key and not self.cache.summary(r['id'])]
                for fid in {str(r['fid']) for r in fresh}:
                    self.cache.put_summaries(fid, [r for r in fresh if str(r['fid']) == fid])
                if fresh:
                    rows = self._thread_rows(mid)[2]
            except AppError:
                pass  # Offline or refused: the cached part of the conversation still shows.
        rows.sort(key=lambda r: r.get('ts') or 0, reverse=True)
        return {'key': key, 'rows': rows}

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
            if self._local_only(mid):
                # An imported .eml or archived mail is not on the server, so the reply cannot be threaded
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
        if not isinstance(mid, str) or self._locate(mid)[1] or self._local_only(mid):
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

    # Recipient groups (收件人组): named lists of people, each as 主送 or 抄送 -------
    # Local only, in the encrypted cache kv. Applying one only fills the composer;
    # encryption is still decided from the resulting recipients.
    def recipient_groups(self):
        return sorted(self.cache.get('recipient_groups', []), key=lambda g: g['name'].casefold())

    @staticmethod
    def _group_members(members):
        """[{name, email, field}] checked and de-duplicated; one address appears once,
        主送 winning over 抄送."""
        result, index = [], {}
        for member in list(members or ())[:GROUP_MEMBERS + 1]:
            member = dict(member or {}) if isinstance(member, dict) else {'email': member}
            name, email = parseaddr(str(member.get('email') or ''))
            name = str(member.get('name') or name or '').replace('"', '').replace('<', '').replace('>', '').strip()[:80]
            field = member.get('field', 'to')
            if not ADDRESS_RE.match(email) or len(email) > 254 or field not in GROUP_FIELDS:
                raise AppError('INVALID_GROUP')
            key = email.casefold()
            if key in index:
                kept = result[index[key]]
                kept.update(field='to' if 'to' in (kept['field'], field) else 'cc', name=kept['name'] or name)
                continue
            index[key] = len(result)
            result.append({'name': name, 'email': email, 'field': field})
        if not result or len(result) > GROUP_MEMBERS:
            raise AppError('INVALID_GROUP')
        return result

    def save_recipient_group(self, value):
        """Create (no id) or replace a group. Names are unique, ignoring case."""
        value = dict(value or {})
        name = str(value.get('name') or '').strip()
        if not name or len(name) > 40 or '\n' in name:
            raise AppError('INVALID_GROUP')
        members = self._group_members(value.get('members'))
        stored = self.cache.get('recipient_groups', [])
        group_id = value.get('id')
        same = next((g for g in stored if g['id'] == group_id), None) if group_id else None
        if group_id and same is None:
            raise AppError('GROUP_NOT_FOUND')
        if any(g['name'].casefold() == name.casefold() and g is not same for g in stored):
            raise AppError('GROUP_EXISTS')
        if same is None and len(stored) >= GROUP_LIMIT:
            raise AppError('GROUP_LIMIT')
        group = {'id': same['id'] if same else uuid.uuid4().hex, 'name': name, 'members': members,
                 'modified': datetime.now().strftime('%Y-%m-%d %H:%M')}
        self.cache.set('recipient_groups', [group if g is same else g for g in stored] + ([] if same else [group]))
        return group

    def delete_recipient_group(self, group_id):
        self.cache.set('recipient_groups', [g for g in self.cache.get('recipient_groups', []) if g['id'] != group_id])
        return self.recipient_groups()

    def apply_recipient_groups(self, group_ids, to=(), cc=(), bcc=()):
        """The composer's recipients with the groups' members appended: 主送 members to
        主送, 抄送 to 抄送. An address already anywhere in the message (or added by an
        earlier group) is not added again. Returns the lists and how many were added."""
        groups = {g['id']: g for g in self.cache.get('recipient_groups', [])}
        if not group_ids or any(i not in groups for i in group_ids):
            raise AppError('GROUP_NOT_FOUND')
        lists = {'to': [str(a) for a in to or ()], 'cc': [str(a) for a in cc or ()]}
        seen = {parseaddr(a)[1].casefold() for a in [*lists['to'], *lists['cc'], *map(str, bcc or ())]}
        added = 0
        for group_id in group_ids:
            for member in groups[group_id]['members']:
                key = member['email'].casefold()
                if key in seen:
                    continue
                seen.add(key)
                lists[member['field']].append(f'"{member["name"]}" <{member["email"]}>' if member['name'] else member['email'])
                added += 1
        return {**lists, 'added': added}

    def message_recipients(self, mid):
        """A message's 主送 and 抄送 people (without the user's own addresses), for
        saving them as a group."""
        document = self._document(mid)
        own = {i.casefold() for i in self.identities}
        members = [{'name': p['name'], 'email': p['email'], 'field': field}
                   for field in GROUP_FIELDS for p in _people([str(v) for v in document.headers.get(field, ())])
                   if ADDRESS_RE.match(p['email']) and p['email'].casefold() not in own]
        return self._group_members(members[:GROUP_MEMBERS]) if members else []

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

    # AI 助手 ---------------------------------------------------------------------
    # Settings live in their own DPAPI file beside account.bin; the key never goes back
    # to the page. Called on the bridge's AI thread, so mail is read only from the cache.
    def _ai_path(self):
        return self.data_dir / 'ai.bin'

    def _ai_record(self):
        try:
            value = json.loads(self.protector.unprotect(self._ai_path().read_bytes()).decode('utf-8'))
        except Exception:
            value = {}
        return value if isinstance(value, dict) else {}

    def _save_ai(self, record):
        self._ai_path().write_bytes(self.protector.protect(json.dumps(record).encode('utf-8')))

    def ai_settings(self):
        from coremail_desktop.ai.client import DEFAULT_BASE_URL, DEFAULT_MODEL
        record = self._ai_record()
        usage = record.get('usage') or {}
        if usage.get('month') != datetime.now().strftime('%Y-%m'):
            usage = {}
        return {'baseUrl': record.get('baseUrl') or DEFAULT_BASE_URL, 'model': record.get('model') or DEFAULT_MODEL,
                'hasKey': bool(record.get('key')), 'ready': bool(record.get('key')) or self.ai_client is not None,
                'demo': self.ai_client is not None, 'usage': {'tokens': usage.get('tokens', 0), 'calls': usage.get('calls', 0)},
                'actions': record.get('actions') is not False}

    def set_ai_settings(self, base_url=None, model=None, key=None, clear_key=False, actions=None):
        from coremail_desktop.ai.client import check_base_url, check_key, check_model
        with self._ai_lock:
            record = self._ai_record()
            if base_url is not None:
                record['baseUrl'] = check_base_url(base_url)
            if model is not None:
                record['model'] = check_model(model)
            if clear_key:
                record['key'] = ''
            elif key:
                record['key'] = check_key(key)
            if isinstance(actions, bool):
                record['actions'] = actions
            self._save_ai(record)
        return self.ai_settings()

    def _ai(self):
        from coremail_desktop.ai.client import DEFAULT_BASE_URL, DEFAULT_MODEL, ChatClient
        if self.ai_client is not None:
            return self.ai_client
        record = self._ai_record()
        if not record.get('key'):
            raise AppError('AI_NOT_CONFIGURED')
        return ChatClient(record.get('baseUrl') or DEFAULT_BASE_URL, record.get('model') or DEFAULT_MODEL, record['key'])

    def _ai_call(self, messages, **kwargs):
        """One chat call, counted per month (tokens and calls only, never content)."""
        client, tokens = self._ai(), 0
        try:
            content, tokens = client.chat(messages, **kwargs)
            return content, tokens
        finally:
            self._count_ai(tokens)

    def _count_ai(self, tokens):
        with self._ai_lock:
            record = self._ai_record()
            month = datetime.now().strftime('%Y-%m')
            usage = record.get('usage') if (record.get('usage') or {}).get('month') == month else {'month': month}
            usage.update(tokens=usage.get('tokens', 0) + tokens, calls=usage.get('calls', 0) + 1)
            record['usage'] = usage
            try:
                self._save_ai(record)
            except OSError:
                pass  # A blocked data folder must not hide the result.

    def ai_test(self):
        self._ai_call([{'role': 'user', 'content': '只回复一个字：好'}], max_tokens=5)
        return self.ai_settings()

    def summarize(self, mid, allow_encrypted=False, refresh=False):
        """「总结这封」. Decrypted mail goes to the AI service only when the user agreed
        for this message (allow_encrypted), and its summary is never kept."""
        from coremail_desktop.ai.summary import SUMMARY_TOKENS, SUMMARY_VERSION, build_messages, parse_summary
        mid = str(mid)
        if self.cache is None:
            raise AppError('NOT_AUTHENTICATED')
        client = self._ai()  # Not configured: say so before touching the mail.
        model = getattr(client, 'model', '')
        document, encrypted = self._ai_document(mid, allow_encrypted)
        saved = self.cache.get('aiSummaries') or {}
        if (not encrypted or self._encrypted_ok(mid)) and not refresh and saved.get(mid, {}).get('v') == SUMMARY_VERSION:
            return {**saved[mid], 'cached': True}
        summary = self.cache.summary(mid) or {}
        messages = build_messages(document, summary.get('date') or '', self.display_name, [self.account or '', *self.identities])
        content, _ = self._ai_call(messages, max_tokens=SUMMARY_TOKENS, json_mode=True)
        result = {**parse_summary(content), 'model': model, 'at': datetime.now().strftime('%Y-%m-%d %H:%M'), 'encrypted': encrypted,
                  'v': SUMMARY_VERSION}
        if not encrypted or self._encrypted_ok(mid):  # Allowed once for all: kept like the decrypted body cache.
            saved.pop(mid, None)
            saved[mid] = result
            self.cache.set('aiSummaries', dict(list(saved.items())[-300:]))
        return result

    def _ai_document(self, mid, allow_encrypted):
        """The mail for an AI call -> (document, encrypted). Decrypted mail only when the
        user agreed for this message or once for all (ai_encrypted); never downloads (the
        AI thread only reads)."""
        if not self._local_only(mid) and mid not in self._docs and self.cache.get_body(mid) is None:
            raise AppError('AI_MAIL_UNAVAILABLE')  # Opening the mail downloads it; this thread never does.
        document = self._document(mid)
        if document.encryption_state == 'locked':
            raise AppError('CERT_LOCKED')
        encrypted = document.encryption_state == 'decrypted'
        if encrypted and not allow_encrypted and not self._encrypted_ok(mid):
            raise AppError('AI_ENCRYPTED_CONFIRM')
        return document, encrypted

    # 加密邮件与 AI: the company sets no limit (user, 2026-10-21), so one setting replaces
    # asking for every mail. Excluded folders and senders are still asked for each time.
    def ai_encrypted(self):
        value = (self.cache.get('aiEncrypted') if self.cache else None) or {}
        return {'allow': bool(value.get('allow')), 'folders': list(value.get('folders') or []), 'senders': list(value.get('senders') or [])}

    def set_ai_encrypted(self, allow=None, folders=None, senders=None):
        if self.cache is None:
            raise AppError('NOT_AUTHENTICATED')
        setting = self.ai_encrypted()
        if allow is not None:
            setting['allow'] = bool(allow)
        if folders is not None:
            if not isinstance(folders, list) or len(folders) > 100 or not all(isinstance(f, str) and 0 < len(f) <= 200 for f in folders):
                raise AppError('UNDERSTAND_INVALID')
            setting['folders'] = list(dict.fromkeys(folders))
        if senders is not None:
            if not isinstance(senders, list) or len(senders) > 200 or not all(isinstance(s, str) for s in senders):
                raise AppError('UNDERSTAND_INVALID')
            clean = [s.strip().casefold() for s in senders if s.strip()]
            if not all(len(s) <= 200 and re.match(r'^(?:[^\s@]+@|@)[^\s@]+$', s) for s in clean):
                raise AppError('UNDERSTAND_INVALID')
            setting['senders'] = list(dict.fromkeys(clean))
        self.cache.set('aiEncrypted', setting)
        return setting

    def _encrypted_ok(self, mid):
        """Encrypted mail may go to the AI service without asking: allowed, and neither its
        folder (or a parent) nor its sender (address or @domain) is excluded."""
        setting = self.ai_encrypted()
        if not setting['allow']:
            return False
        row = (self.cache.summary(mid) if self.cache else None) or self._archive_summary(mid) or {}
        parents = {f['id']: f.get('parent') for f in self._folders or (self.cache.get('folders') if self.cache else None) or []}
        excluded, fid = set(setting['folders']), row.get('fid')
        for _ in range(MAX_FOLDER_DEPTH + 1):
            if not fid:
                break
            if fid in excluded:
                return False
            fid = parents.get(fid)
        sender = (row.get('fromEmail') or parseaddr(row.get('from') or '')[1]).casefold()
        return not any(sender == s or (s.startswith('@') and sender.endswith(s)) for s in setting['senders'])

    def _ai_stream(self, messages, progress, req, max_tokens):
        """A streamed call: text goes to progress({'req', 'delta'}) at most every 80 ms;
        cancel_ai() stops it. -> the whole text. Counted like _ai_call."""
        client, tokens, buffer, last = self._ai(), 0, [], [0.0]
        self._ai_cancel.clear()

        def flush():
            if buffer and progress:
                progress({'req': req, 'delta': ''.join(buffer)})
            buffer.clear()
            last[0] = time.monotonic()

        def delta(text):
            buffer.append(text)
            if time.monotonic() - last[0] >= 0.08:
                flush()
        try:
            if not hasattr(client, 'stream'):  # A stand-in without streaming: one piece.
                text, tokens = client.chat(messages, max_tokens=max_tokens)
                delta(text)
            else:
                text, tokens = client.stream(messages, delta, self._ai_cancel.is_set, max_tokens=max_tokens)
            return text
        finally:
            flush()
            self._count_ai(tokens)

    def cancel_ai(self):
        self._ai_cancel.set()
        return True

    # 写信助手 / 帮我起草回复 / 发送前检查 (ai/writing.py, ai/precheck.py) ----------
    def rewrite(self, text, mode, instruction='', req='', progress=None):
        """The user's own words rewritten (润色、改语气、翻译), streamed. Only what the page
        sends: it leaves out the signature and the quoted original."""
        from coremail_desktop.ai.writing import WRITE_TOKENS, rewrite_messages, tidy_draft
        messages = rewrite_messages(text, mode, instruction)
        return {'text': tidy_draft(self._ai_stream(messages, progress, str(req)[:40], WRITE_TOKENS))}

    def reply_ideas(self, mid, allow_encrypted=False):
        """2–3 directions to answer this mail ({label, hint})."""
        from coremail_desktop.ai.writing import IDEA_TOKENS, idea_messages, parse_ideas
        mid = str(mid)
        if self.cache is None:
            raise AppError('NOT_AUTHENTICATED')
        self._ai()
        document, encrypted = self._ai_document(mid, allow_encrypted)
        content, _ = self._ai_call(idea_messages(document, (self.cache.summary(mid) or {}).get('date') or ''),
                                   max_tokens=IDEA_TOKENS, json_mode=True)
        return {'ideas': parse_ideas(content), 'encrypted': encrypted}

    def draft_reply(self, mid, idea='', note='', allow_encrypted=False, req='', progress=None):
        """The body of a reply in the chosen direction, streamed. Recipients, subject and
        encryption come from the ordinary reply; the AI only writes text."""
        from coremail_desktop.ai.writing import WRITE_TOKENS, draft_messages, tidy_draft
        mid = str(mid)
        if self.cache is None:
            raise AppError('NOT_AUTHENTICATED')
        self._ai()
        document, _ = self._ai_document(mid, allow_encrypted)
        messages = draft_messages(document, idea, note, (self.cache.summary(mid) or {}).get('date') or '')
        return {'text': tidy_draft(self._ai_stream(messages, progress, str(req)[:40], WRITE_TOKENS))}

    def precheck(self, text, to=(), recipients=()):
        """本机发送前检查 (no network): past dates, a greeting naming nobody in 主送, outside
        addresses in the body. to: [{'name','email'}]; recipients: every address."""
        from coremail_desktop.ai.precheck import check
        if not isinstance(text, str) or not isinstance(to, (list, tuple)) or not isinstance(recipients, (list, tuple)):
            raise AppError('AI_INVALID_INPUT')
        to = [{'name': str(p.get('name') or '')[:100], 'email': str(p.get('email') or '')[:320]}
              for p in to[:200] if isinstance(p, dict)]
        return {'issues': check(text[:50000], to, [str(r)[:320] for r in recipients[:500]], self.internal_domains(),
                                datetime.now().date())}

    def check_draft(self, subject='', to=(), cc=(), text='', attachments=()):
        """AI 发送前检查: problems to look at, never changes. to/cc: display names and domains."""
        from coremail_desktop.ai.writing import CHECK_TOKENS, check_messages, parse_issues
        if not all(isinstance(v, (list, tuple)) for v in (to, cc, attachments)):
            raise AppError('AI_INVALID_INPUT')
        messages = check_messages(subject, list(to), list(cc), text, list(attachments), datetime.now().strftime('%Y-%m-%d'))
        content, _ = self._ai_call(messages, max_tokens=CHECK_TOKENS, json_mode=True)
        return {'issues': parse_issues(content, text)}

    # 总结整个会话 (ai/thread.py) ---------------------------------------------------
    def thread_ai_status(self, mid):
        """What 总结整个会话 needs first: the mail ids (oldest first, as sent to the AI), the
        ones not downloaded yet (the page opens them without marking read) and how many
        are encrypted. Runs on the mail thread (it may search the server and decrypt)."""
        from coremail_desktop.ai.thread import pick
        if self.cache is None:
            raise AppError('NOT_AUTHENTICATED')
        rows = sorted(self.thread(str(mid))['rows'], key=lambda r: r.get('ts') or 0)
        rows, left_out = pick(rows)
        ids = [r['id'] for r in rows]
        missing = [m for m in ids if not self._local_only(m) and m not in self._docs and self.cache.get_body(m) is None]
        state = lambda m: self._document(m).encryption_state
        encrypted = sum(1 for m in ids if m not in missing and (state(m) == 'locked' or state(m) == 'decrypted' and not self._encrypted_ok(m)))
        return {'ids': ids, 'missing': missing, 'encrypted': encrypted, 'leftOut': left_out}

    def summarize_thread(self, mid, allow_encrypted=False, refresh=False):
        """总结整个会话. Encrypted mail goes only with the user's consent (else it is named but
        left out); a result with encrypted mail in it is never kept."""
        from coremail_desktop.ai.thread import THREAD_TOKENS, build_messages, parse, pick, sender
        mid = str(mid)
        if self.cache is None:
            raise AppError('NOT_AUTHENTICATED')
        client = self._ai()
        rows, left_out = pick(sorted(self.thread(mid)['rows'], key=lambda r: r.get('ts') or 0))
        if len(rows) < 2:
            raise AppError('AI_NOT_A_THREAD')
        key = ':'.join(r['id'] for r in rows)
        saved = self.cache.get('aiThreads') or {}
        if not refresh and key in saved:
            return {**saved[key], 'cached': True}
        mails, skipped = [], 0
        for row in rows:
            try:
                document, encrypted = self._ai_document(row['id'], allow_encrypted)
            except AppError as error:
                if error.code not in ('AI_ENCRYPTED_CONFIRM', 'CERT_LOCKED'):
                    raise
                mails.append({'id': row['id'], 'date': row.get('date') or '', 'document': None})
                skipped += 1
                continue
            mails.append({'id': row['id'], 'date': row.get('date') or '', 'document': document, 'encrypted': encrypted})
        content, _ = self._ai_call(build_messages(mails, left_out), max_tokens=THREAD_TOKENS, json_mode=True)
        result = parse(content, len(mails))
        for step in result['timeline']:
            mail = mails[step['n'] - 1]
            step.update(mid=mail['id'], date=str(mail['date'])[5:10], who=sender(mail['document']) if mail['document'] else '')
            del step['n']
        encrypted = any(m.get('encrypted') for m in mails)
        result.update(count=len(mails), leftOut=left_out, skipped=skipped, encrypted=encrypted,
                      model=getattr(client, 'model', ''), at=datetime.now().strftime('%Y-%m-%d %H:%M'))
        if not encrypted:
            saved.pop(key, None)
            saved[key] = result
            self.cache.set('aiThreads', dict(list(saved.items())[-100:]))
        return result

    # 问邮箱: the model searches the server and reads mail through read-only tools
    # (ai/tools.py). Runs on the AI thread; like the fast thread it only reads.
    # A conversation keeps its refs and earlier answers here, in memory, so a follow-up
    # can say "第二封"; the page only holds its id. Closing the app forgets it.
    CHATS = 3  # Conversations kept; the oldest goes first.

    def ask_mailbox(self, question, allow=(), progress=None, chat='', current='', redo=False, current_task=0):
        """allow: ids of encrypted mail the user agreed, for this question, to send.
        chat: the conversation to continue ('' starts one); current: the mail open in
        the reader; current_task: the task selected in 任务 (0: none); redo: answer the
        last question again (it replaces that turn)."""
        from coremail_desktop.ai.agent import ANSWER_TOKENS, ask
        from coremail_desktop.ai.tools import AssistantTools
        question = question.strip() if isinstance(question, str) else ''
        if not question or len(question) > 500 or not isinstance(allow, (list, tuple)) or len(allow) > 20 \
                or not isinstance(chat, str) or len(chat) > 40 or not isinstance(current, str) or len(current) > 200 \
                or type(current_task) is not int or not 0 <= current_task < 100000:
            raise AppError('INVALID_QUESTION')
        if self.cache is None:
            raise AppError('NOT_AUTHENTICATED')
        client = self._ai()
        self._ask_cancel.clear()
        state = self._chats.get(chat) if chat else None
        restarted = bool(chat) and state is None
        if state is None:
            tools = AssistantTools(self._ask_search, self._document, self._ask_match, {},  # Folders: below, each question.
                                   archive=self._ask_archive if self._archive_folder() else None, auto_allow=self._encrypted_ok,
                                   tasks=self._assist_tasks, task=self._assist_task, mine=self._assist_mine,
                                   pending=self._assist_pending if self._understand_on() else None, plan=self._assist_preview,
                                   thread=self._assist_thread, groups=self._assist_groups,
                                   memory=self._memory_search if self._understand_on() else None,
                                   followups=self.followups if self._understand_on() else None)
            chat, state = uuid.uuid4().hex[:16], {'tools': tools, 'turns': [], 'shown': []}
            while len(self._chats) >= self.CHATS:
                self._chats.pop(next(iter(self._chats)))
            self._chats[chat] = state
        tools = state['tools']
        tools.begin({str(m) for m in allow})
        tools.allow_actions(self._ai_record().get('actions') is not False)  # 设置 → 允许 AI 提议邮件操作 (A3), on by default.
        known = self._folders or self.cache.get('folders') or []  # Folders made since the conversation began count too.
        tools.folders.update(names={f['id']: f['name'] for f in known}, targets=[{'id': f['id'], 'name': f['name'], 'role': f.get('role')} for f in known],
                             **{f['role']: f['id'] for f in known if f.get('role') in ('inbox', 'sent', 'trash')})
        turns = state['turns'][:-1] if redo else state['turns']
        row = self.cache.summary(current) if current else None
        here = tools.add(row) if row else None
        task_here = None
        if current_task:
            try:
                task_here = tools.add_task(self._assist_task(current_task)['task'])
            except AppError as error:
                if error.code != 'TASK_NOT_FOUND':
                    raise

        def call(messages, specs):
            tokens = 0
            try:
                message, tokens = client.complete(messages, tools=specs, max_tokens=ANSWER_TOKENS)
                return message
            finally:
                self._count_ai(tokens)
        result = ask(question, call, tools, progress or (lambda step: None), self._ask_cancel.is_set,
                     today=datetime.now().strftime('%Y-%m-%d'), history=turns, known=state['shown'], current=here,
                     current_task=task_here)
        state['turns'] = turns + [(question, result['answer'])]
        refs = [s['ref'] for s in result['sources']] + [t['ref'] for t in result['tasks']]
        state['shown'] = ([r for r in state['shown'] if r not in refs] + refs)[-60:]
        result['plans'] = [self._keep_plan(p, chat, question, len(state['turns'])) for p in result.get('plans', [])]
        return {**result, 'model': getattr(client, 'model', ''), 'chat': chat, 'turn': len(state['turns']), 'restarted': restarted}

    # 助理 plans (W2): a proposal is kept here and runs only when the page sends its id back
    # (执行). A newer plan of the same conversation for the same task replaces an older one.
    PLAN_TTL = 30 * 60
    PLANS = 20

    def _keep_plan(self, plan, chat, question, turn):
        """-> what the page sees of a plan: its id and preview (task operations) or the
        draft's view (names and domains, never addresses)."""
        now = time.monotonic()
        kind = plan.get('kind', 'tasks')
        ids = {a.get('id') for a in plan['actions'] if a.get('id')} if kind == 'tasks' else set()
        for key, old in list(self._plans.items()):
            if now - old['at'] > self.PLAN_TTL or old['chat'] == chat and old['ids'] & ids:
                self._plans.pop(key)
        while len(self._plans) >= self.PLANS:
            self._plans.pop(next(iter(self._plans)))
        key = uuid.uuid4().hex[:16]
        self._plans[key] = {**plan, 'kind': kind, 'ids': ids, 'chat': chat, 'turn': turn, 'basis': question, 'at': now}
        if kind == 'draft':
            return {'id': key, 'ref': plan['ref'], 'kind': kind, 'summary': plan['summary'], 'draft': self._draft_view(plan)}
        if kind == 'link':
            return {'id': key, 'ref': plan['ref'], 'kind': kind, 'summary': plan['summary'],
                    'link': {'task': plan['task'], 'mails': [{k: m[k] for k in ('mid', 'subject', 'fromName', 'date')} for m in plan['mails']]}}
        if kind == 'mail':
            return {'id': key, 'ref': plan['ref'], 'kind': kind, 'summary': plan['summary'], 'basis': question[:200],
                    'mail': {'op': plan['op'], 'what': plan['what'], 'count': len(plan['mids']), 'folder': plan['folderName'],
                             'samples': plan['samples'], 'skipped': plan['skipped'], 'local': plan['local']}}
        return {'id': key, 'ref': plan['ref'], 'kind': kind, 'summary': plan['summary'], 'preview': plan['preview']}

    def _chat_note(self, entry, note):
        """The conversation learns what the user did with a plan of its turn."""
        state = self._chats.get(entry['chat'])
        if state and 0 < entry['turn'] <= len(state['turns']):
            asked, answered = state['turns'][entry['turn'] - 1]
            state['turns'][entry['turn'] - 1] = (asked, answered + note)

    def _plan_entry(self, plan, kind):
        entry = self._plans.get(plan) if isinstance(plan, str) else None
        if entry is None or entry['kind'] != kind or time.monotonic() - entry['at'] > self.PLAN_TTL:
            raise AppError('AI_PLAN_EXPIRED')
        return entry

    def assistant_plan_run(self, plan, picks=None):
        """执行 on a plan card: its operations through run_plan, as the task center's command
        box does; the conversation learns it ran, so a follow-up does not propose it again.
        picks: the indexes the user left ticked (all when None); a new task's #N1 left out
        makes the operations naming it fail rather than run half-done."""
        entry = self._plan_entry(plan, 'tasks')
        actions = entry['actions']
        if picks is not None:
            if not isinstance(picks, list) or not picks or any(type(i) is not int or not 0 <= i < len(actions) for i in picks):
                raise AppError('TASK_INVALID')
            actions = [a for i, a in enumerate(actions) if i in set(picks)]
        self._plans.pop(plan)
        result = self._tasks().run_plan(actions, entry['basis'])
        done = '、'.join(f'#{d["id"]}' for d in result['done']) or '无'
        left = len(entry['actions']) - len(actions)
        self._chat_note(entry, f'\n（用户已确认执行计划 {entry["ref"]}：成功 {len(result["done"])} 项（{done}），失败 {len(result["failed"])} 项'
                               + (f'，没有勾选 {left} 项' if left else '') + '。）')
        # 提醒回信 (W4): which done operations may deserve a reply; the page asks assistant_nudge.
        actions = {a.get('id'): a for a in entry['actions']}
        told = [(d['id'], what) for d in result['done'] if (what := self._nudge_what(actions.get(d['id'], d)))]
        result['nudge'] = ''
        if told:
            result['nudge'] = uuid.uuid4().hex[:16]
            self._plans[result['nudge']] = {'kind': 'nudge', 'ids': set(), 'chat': entry['chat'], 'turn': entry['turn'],
                                            'ref': entry['ref'], 'basis': entry['basis'], 'told': dict(told), 'at': time.monotonic()}
        return self._unblocked(result, [d['id'] for d in result['done']])

    # 操作类工具 (A3): a mail plan runs through the list's batch methods (set_flags, move,
    # delete); what each mail was before is kept for 撤销, once, for as long as a plan lives.
    def assistant_mail_run(self, plan):
        from coremail_desktop.ai.tools import MAIL_PLAN_LIMIT
        entry = self._plan_entry(plan, 'mail')
        if len(entry['mids']) > MAIL_PLAN_LIMIT:
            raise AppError('AI_PLAN_TOO_LARGE')
        self._plans.pop(plan)
        mids, op = list(entry['mids']), entry['op']
        before = {m: self.cache.summary(m) or {} for m in mids}
        if op in ('read', 'unread'):
            result = self.set_flags(mids, read=op == 'read')
        elif op in ('flag', 'unflag'):
            result = self.set_flags(mids, flagged=op == 'flag')
        elif op == 'move':
            result = self.move(mids, entry['folder'])
        else:
            result = self.delete(mids)
        done = result['done']
        self._chat_note(entry, f'\n（用户已执行计划 {entry["ref"]}：{entry["what"]} {len(done)} 封' +
                        (f'，{len(result["failed"])} 封未完成' if result['failed'] else '') + '。）')
        undo = ''
        if done:
            undo = uuid.uuid4().hex[:16]
            key = 'flagged' if op in ('flag', 'unflag') else 'read'
            was = {m: (str(before[m].get('fid') or '') if op in ('move', 'trash') else (before[m].get('flags') or {}).get(key)) for m in done}
            self._plans[undo] = {'kind': 'undo', 'ids': set(), 'chat': entry['chat'], 'turn': entry['turn'], 'ref': entry['ref'],
                                 'op': op, 'was': was, 'at': time.monotonic()}
        return {'done': done, 'failed': result['failed'], 'folders': result['folders'], 'shared': result['shared'], 'undo': undo}

    def assistant_mail_undo(self, undo):
        """撤销: each mail back where it was (folder) or as it was (read, star); once."""
        entry = self._plan_entry(undo, 'undo')
        self._plans.pop(undo)
        groups = {}
        for mid, was in entry['was'].items():
            if was is not None and was != '':
                groups.setdefault(was, []).append(mid)
        done, failed, result = [], [], {'folders': self._folders, 'shared': list(self._shared.values())}
        for was, mids in groups.items():
            if entry['op'] in ('move', 'trash'):
                result = self.move(mids, was)
            else:
                result = self.set_flags(mids, **{'flagged' if entry['op'] in ('flag', 'unflag') else 'read': bool(was)})
            done += result['done']
            failed += result['failed']
        self._chat_note(entry, f'\n（用户已撤销计划 {entry["ref"]}：{len(done)} 封已恢复。）')
        return {'done': done, 'failed': failed, 'folders': result['folders'], 'shared': result['shared']}

    # 提醒回信 (W4, 4.4): after a confirmed plan completes a task, changes its status or adds
    # progress, one line asks whether to tell the other side — decided here, without the AI:
    # the task's conversation ends with someone else's mail the user has not answered. 「不用」
    # (or a reply sent from a draft card) holds until that conversation's last mail changes.
    @staticmethod
    def _nudge_what(action):
        """-> (what to tell, the points) for a done operation, or None."""
        op, fields = action.get('op'), action.get('fields') or {}
        status = f'状态已改为「{fields["status"]}」' if fields.get('status') else ''
        if op == 'complete':
            return '已完成', [action['note']] if action.get('note') else []
        if op == 'cancel':
            return '已取消', [action['reason']] if action.get('reason') else []
        if op == 'reopen':
            return '已重新打开', [action['reason']] if action.get('reason') else []
        if op == 'progress':
            return status or '最新进展', [p for p in (action.get('text'), fields.get('blockers')) if p]
        if op == 'update' and status:
            return status, [fields['blockers']] if fields.get('blockers') else []
        return None

    def assistant_nudge(self, nudge):
        """-> {'text', 'plan'} (a draft plan replying to the conversation's last mail) for the
        first task of a run whose conversation waits on the user, or None. Asked once."""
        entry = self._plan_entry(nudge, 'nudge')
        self._plans.pop(nudge)
        for task_id, (what, points) in entry['told'].items():
            try:
                rows = self._assist_thread(task_id)['rows']
            except AppError:
                continue
            last = rows[-1] if rows else None
            if not last or last['mine'] or self._tasks().store.get(f'assist_nudge:{int(task_id)}', '') == last['id']:
                continue
            state = self._chats.get(entry['chat'])
            mref = state['tools'].add(last) if state else ''
            sender = last.get('fromEmail') or parseaddr(last.get('from') or '')[1]
            name = last.get('fromName') or parseaddr(last.get('from') or '')[0] or sender.partition('@')[0] or '对方'
            people = sorted({p['email'].casefold() for r in rows for p in r['people']})
            plan = {'ref': entry['ref'] + '·回信', 'kind': 'draft', 'summary': f'回复《{last.get("subject") or "（无主题）"}》', 'actions': [],
                    'reply': True, 'mid': last['id'], 'mode': 'reply', 'group': '', 'task': task_id, 'people': people, 'nudge': True,
                    'subject': last.get('subject') or '', 'purpose': f'告知{what}', 'points': [str(p)[:120] for p in points][:8],
                    'mail': {'ref': mref, 'subject': last.get('subject') or '（无主题）', 'fromName': name,
                             'domain': sender.rpartition('@')[2], 'date': str(last.get('date') or '')[:16]}}
            self._chat_note(entry, f'\n（助理提醒：#{task_id} 的会话最后一封是{name}发的，用户还没回；已提议回一封告知{what}。）')
            return {'text': f'最后一封是{name} {str(last.get("date") or "")[5:10]} 发的，你还没回。要回一封告诉对方{what}吗？',
                    'task': task_id, 'plan': self._keep_plan(plan, entry['chat'], entry['basis'], entry['turn'])}
        return None

    def assistant_nudge_dismiss(self, plan):
        """「不用」 on a reminder: not again for this task until its conversation's last mail changes."""
        entry = self._plan_entry(plan, 'draft')
        if not entry.get('nudge'):
            raise AppError('AI_PLAN_EXPIRED')
        self._plans.pop(plan)
        self._tasks().store.set(f'assist_nudge:{int(entry["task"])}', entry['mid'])  # Only the mail's id.
        return True

    def assistant_link_run(self, plan, picks):
        """关联 on a candidates card (W4): the picked mails linked to the task by hand's path."""
        entry = self._plan_entry(plan, 'link')
        if not isinstance(picks, list) or not picks or any(type(i) is not int or not 0 <= i < len(entry['mails']) for i in picks):
            raise AppError('AI_INVALID_INPUT')
        self._plans.pop(plan)
        for i in dict.fromkeys(picks):
            self.task_link(entry['task'], entry['mails'][i]['mid'])
        refs = '、'.join(entry['mails'][i]['ref'] for i in dict.fromkeys(picks))
        self._chat_note(entry, f'\n（用户已关联 {refs} 到任务 #{entry["task"]}。）')
        return {'task': entry['task'], 'linked': len(set(picks))}

    # 助理·起草 (W3): the task's conversation, recipient groups ranked by overlap with it,
    # and a draft plan the page turns into a composer. The AI writes only the body; the
    # recipients come from the ordinary reply, a group or contacts the tools returned.
    def _assist_thread(self, task_id):
        """The conversations of the task's linked mail, oldest first; each row with
        'mine' (the user sent it) and 'people' (everyone on it but the user)."""
        own = {a.casefold() for a in [self.account or '', *self.identities] if a}
        rows = {}
        for link in self._tasks().get(task_id)['links']:
            mid = str(link.get('mid') or '')
            if not mid or not self.cache.summary(mid):
                continue
            try:
                found = self.thread(mid)['rows']
            except AppError:
                found = [self.cache.summary(mid)]
            rows.update((r['id'], r) for r in found)
        out = []
        for r in sorted(rows.values(), key=lambda r: r.get('ts') or 0):
            to = r.get('to')
            to = ', '.join(map(str, to)) if isinstance(to, (list, tuple)) else str(to or '')
            sender = (r.get('fromEmail') or parseaddr(r.get('from') or '')[1]).casefold()
            people = [{'name': n.strip().strip('"\'') or a.partition('@')[0], 'email': a}
                      for n, a in getaddresses([str(r.get('from') or ''), to]) if '@' in a and a.casefold() not in own]
            out.append({**r, 'mine': sender in own, 'people': people})
        return {'rows': out, 'internal': set(self.internal_domains())}

    def _assist_groups(self, addresses=()):
        """Recipient groups without addresses: name, size, 主送/抄送, domains, how many are
        outside the company and how many are on the conversation; most overlap first."""
        internal, near = set(self.internal_domains()), {a.casefold() for a in addresses}
        out = []
        for g in self.cache.get('recipient_groups', []):
            emails = [m['email'].casefold() for m in g['members']]
            out.append({'id': g['id'], 'name': g['name'], 'count': len(emails), 'to': sum(m['field'] == 'to' for m in g['members']),
                        'cc': sum(m['field'] == 'cc' for m in g['members']),
                        'domains': sorted({e.rpartition('@')[2] for e in emails})[:5],
                        'external': sum(e.rpartition('@')[2] not in internal for e in emails), 'overlap': len(near & set(emails))})
        return sorted(out, key=lambda g: (-g['overlap'], g['name'].casefold()))

    def _remembered_group(self, task_id):
        try:
            return self._tasks().store.get(f'assist_group:{int(task_id)}', '') if task_id else ''
        except AppError:
            return ''

    def _draft_view(self, plan):
        internal = set(self.internal_domains())
        groups = self._assist_groups(plan.get('people', ()))
        known = {g['id'] for g in groups}
        default = next((g for g in (plan.get('group'), self._remembered_group(plan.get('task'))) if g in known), '')
        view = {'reply': plan['reply'], 'subject': plan['subject'], 'purpose': plan['purpose'], 'points': plan['points'],
                'task': plan.get('task') or 0, 'group': default, 'groups': groups[:50], 'scheduleAt': plan.get('scheduleAt')}
        if plan['reply']:
            view.update(mail=plan['mail'], mode=plan['mode'])
        else:
            view['contacts'] = [{'name': c['name'], 'domain': c['email'].rpartition('@')[2],
                                 'external': c['email'].rpartition('@')[2].casefold() not in internal} for c in plan['contacts']]
        return view

    def assistant_draft_compose(self, plan, target='reply', keep_sender=False, contacts=None):
        """起草 on a draft card -> a composer value. target: 'reply' / 'reply_all' (a reply)
        or '' (a new mail), or a group id; contacts: indexes of the plan's contacts kept
        (new mail). With a group, 主送/抄送 are the group's (keep_sender: the original
        sender stays in 主送); subject, quote and threading stay the reply's."""
        entry = self._plan_entry(plan, 'draft')
        groups = {g['id'] for g in self.cache.get('recipient_groups', [])}
        group = target if isinstance(target, str) and target in groups else ''
        if not group and target not in (('reply', 'reply_all') if entry['reply'] else ('',)):
            raise AppError('GROUP_NOT_FOUND')
        if entry['reply']:
            value = self.compose('reply_all' if target == 'reply_all' else 'reply', entry['mid'])
            if group:
                lists = self.apply_recipient_groups([group], to=value['to'][:1] if keep_sender is True else [], cc=[])
                value.update(to=lists['to'], cc=lists['cc'])
        else:
            if contacts is None:
                contacts = list(range(len(entry['contacts'])))
            if not isinstance(contacts, list) or any(type(i) is not int or not 0 <= i < len(entry['contacts']) for i in contacts):
                raise AppError('AI_INVALID_INPUT')
            named = lambda c: f'"{c["name"]}" <{c["email"]}>' if c['name'] else c['email']
            value = {**self.compose('new'), 'to': [named(entry['contacts'][i]) for i in dict.fromkeys(contacts)], 'subject': entry['subject']}
            if group:
                lists = self.apply_recipient_groups([group], to=value['to'], cc=[])
                value.update(to=lists['to'], cc=lists['cc'])
        if entry.get('task') and (group or entry['reply']):
            try:
                self._tasks().store.set(f'assist_group:{int(entry["task"])}', group)  # Only the group's id.
            except AppError:
                pass
        entry['sentTo'] = [parseaddr(a)[0] or parseaddr(a)[1].partition('@')[0] for a in value['to']][:3]
        if entry.get('scheduleAt'):  # 定时 on the draft only (A3): the user still presses 发送.
            value['scheduleAt'] = entry['scheduleAt']
        return {**value, 'assist': True}

    def assistant_draft_write(self, plan, points=None, allow_encrypted=False, req='', progress=None):
        """The body of a draft plan, streamed: a reply to its mail, or a new mail from the
        task. points: the card's edited points (else the plan's)."""
        from coremail_desktop.ai.writing import WRITE_TOKENS, clean_points, draft_messages, new_messages, tidy_draft
        entry = self._plan_entry(plan, 'draft')
        points = clean_points(points) if points is not None else entry['points']
        self._ai()
        if entry['reply']:
            document, _ = self._ai_document(entry['mid'], allow_encrypted)
            messages = draft_messages(document, entry['purpose'], '', (self.cache.summary(entry['mid']) or {}).get('date') or '', points)
        else:
            task = self._tasks().store.tasks.get(entry.get('task')) if entry.get('task') else None
            from coremail_desktop.tasks.center import _shown
            fields = {k: str(_shown(task[k]))[:600] for k in ('system', 'content', 'latest', 'next') if task and task.get(k)}
            messages = new_messages(entry['purpose'], points, [c['name'] for c in entry['contacts']], fields)
        return {'text': tidy_draft(self._ai_stream(messages, progress, str(req)[:40], WRITE_TOKENS))}

    def assistant_sent(self, plan):
        """A drafted mail went out (sent or queued): a progress line proposed for its task,
        as a plan to confirm (Q5: never written by itself). None without a task."""
        from coremail_desktop.tasks.assistant import check_actions
        entry = self._plan_entry(plan, 'draft')
        if entry.get('sent') or not entry.get('task'):
            return None
        entry['sent'] = True
        self._chat_note(entry, f'\n（用户已发出按计划 {entry["ref"]} 起草的邮件。）')
        if entry['reply']:  # Answered: no reminder about that mail (the sent copy may not be listed yet).
            self._tasks().store.set(f'assist_nudge:{int(entry["task"])}', entry['mid'])
        names = '、'.join(n for n in entry.get('sentTo') or [] if n) or '对方'
        text = f'已发邮件给{names}：{entry["purpose"]}。' if entry['purpose'] else f'已发邮件给{names}。'
        actions = check_actions([{'op': 'progress', 'id': int(entry['task']), 'text': text}])
        return self._keep_plan({'ref': entry['ref'] + '′', 'summary': '记一条进展', 'actions': actions,
                                'preview': self._assist_preview(actions)}, entry['chat'], entry['basis'], 0)

    def _assist_preview(self, actions):
        """plan_tasks: each operation checked like the task center's preview, nothing written."""
        from coremail_desktop.ui.messages import user_message
        preview = self._tasks().preview(actions)
        for item in preview:
            if item.get('error'):
                item['message'] = user_message(item['error'])
        return preview

    def cancel_ask(self):
        self._ask_cancel.set()
        return True

    # 个人工作助理 (W1): what the conversation may read of the task center and 待我处理.
    def _assist_tasks(self, view, query):
        from coremail_desktop.tasks.center import _shown
        from coremail_desktop.tasks.rules import FIELDS
        center = self._tasks()
        return [{**{f: _shown(center.store.tasks[r['id']][f]) for f in FIELDS}, 'overdue': r['overdue']}
                for r in center.overview(view, query)['rows']]

    def _assist_task(self, task_id):
        d = self._tasks().get(task_id)
        for link in d['links']:
            link['row'] = self.cache.summary(str(link['mid'])) if link.get('mid') else None
        return d

    def _assist_mine(self, task):
        """需要我做 (Q1): no owner, the default owner (the user), or the user's name in it."""
        from coremail_desktop.tasks.rules import OWNER_DEFAULT
        owner = str(task.get('owner') or '').strip()
        names = [n for n in (self.display_name, (self.account or '').partition('@')[0]) if n]
        return not owner or OWNER_DEFAULT in owner or any(n.casefold() in owner.casefold() for n in names)

    def _assist_pending(self):
        """待我处理, without the mail already linked to a task."""
        linked = {str(link.get('mid')) for link in self._tasks().store.links() if link.get('mid')}
        return [r for r in self.understand_view('act')['rows'] if str(r['id']) not in linked]

    def _ask_search(self, pattern, offset, limit, filters):
        found = self._online(self.adapter.search_messages, pattern, offset, limit, filters=filters, snippet=True)
        rows = [_summary_dict(item) for item in found['messages']]
        fresh = [r for r in rows if not self.cache.summary(r['id'])]
        for fid in {str(r['fid']) for r in fresh}:  # So a source card opens like any listed mail.
            self.cache.put_summaries(fid, [r for r in fresh if str(r['fid']) == fid])
        return {'rows': rows, 'total': found['total']}

    def _ask_archive(self, pattern, offset, limit, filters):
        rows, total = self._catalog().search(pattern.split(), sender=filters.get('from', ''),
                                             since=date_ts(filters['since']) if filters.get('since') else None,
                                             until=date_ts(filters['until']) + 86400 if filters.get('until') else None,
                                             limit=offset + limit)
        return {'rows': rows[offset:offset + limit], 'total': total}

    def _ask_match(self, name):
        return self._online(self.adapter.match_contacts, name, 5) if hasattr(self.adapter, 'match_contacts') else []

    # 本地存档 -----------------------------------------------------------------
    # The official client's local archive (archmails/*.cmpack), read-only. Its catalog
    # is an encrypted SQLite beside the mail cache; originals stay in the packs.
    def _local_only(self, mid):
        return mid in self._imported or str(mid).startswith('arc:')

    def _archive_folder(self):
        return (self.cache.get('archive') or {}).get('folder', '') if self.cache else ''

    def _catalog(self):
        from coremail_desktop.archive.catalog import ArchiveCatalog
        if self.cache is None:
            raise AppError('NOT_AUTHENTICATED')
        if self.archive is None:
            self.archive = ArchiveCatalog(self.data_dir / 'cache' / (self._archive_key + '.archive.db'), self.protector)
        return self.archive

    def _archive_raw(self, mid):
        folder = self._archive_folder()
        if not folder:
            raise AppError('ARCHIVE_NOT_FOUND')
        return self._catalog().raw(folder, mid)

    def _archive_summary(self, mid):
        return self._catalog().summary(mid) if str(mid).startswith('arc:') and self.cache is not None else None

    def archive_status(self):
        folder = self._archive_folder()
        catalog = self._catalog() if folder else None
        return {'folder': folder, 'messages': len(catalog.rows) if catalog else 0,
                'lastScan': catalog.get('lastScan') if catalog else None, 'scanning': self._archive_scan}

    def set_archive_folder(self, folder=''):
        """The account folder of the official client (or its archmails); '' turns it off."""
        from coremail_desktop.archive.catalog import archive_dir
        folder = folder.strip() if isinstance(folder, str) else ''
        if folder:
            if len(folder) > 500 or not archive_dir(folder).is_dir():
                raise AppError('ARCHIVE_NOT_FOUND')
        self.cache.set('archive', {'folder': folder})
        return self.archive_status()

    def archive_scan(self, progress=None):
        """Bring the catalog up to date (minutes the first time; unchanged packs are skipped)."""
        folder = self._archive_folder()
        if not folder:
            raise AppError('ARCHIVE_NOT_FOUND')
        if self._archive_scan is not None:
            raise AppError('ARCHIVE_SCANNING')
        self._archive_scan = {'done': 0, 'total': 0}

        def step(done, total):
            self._archive_scan = {'done': done, 'total': total}
            if progress:
                progress({'done': done, 'total': total})
        try:
            result = self._catalog().scan(folder, step)
        finally:
            self._archive_scan = None
        return {**self.archive_status(), 'failed': result['failed'][:50]}

    def archive_view(self, offset=0, limit=5000):
        if not self._archive_folder():
            return {'rows': [], 'total': 0}
        rows, total = self._catalog().listing(int(offset), max(1, min(int(limit), 5000)))
        return {'rows': rows, 'total': total}

    def search_archive(self, query, filters=None):
        """Archived mail matching the search box (every word) and the filters it supports."""
        if not self._archive_folder():
            return {'rows': [], 'total': 0}
        filters = search_filters(filters)
        if filters.get('to') or filters.get('unread') or filters.get('flagged') or filters.get('folder'):
            return {'rows': [], 'total': 0}  # Conditions archived mail cannot meet (or another folder).
        rows, total = self._catalog().search(
            str(query or '').split(), sender=filters.get('from') or filters.get('with') or '',
            since=date_ts(filters['since']) if filters.get('since') else None,
            until=date_ts(filters['until']) + 86400 if filters.get('until') else None,
            subject_only=filters.get('scope') == 'subject')
        if filters.get('attachment'):
            rows = [r for r in rows if r['flags'].get('attached')]
        return {'rows': rows, 'total': total}

    # 任务中心 (tasks/center.py): not tied to the mail account, usable signed out.
    def _tasks(self):
        if self.tasks is None:
            from coremail_desktop.tasks.center import TaskCenter
            self.tasks = TaskCenter(self.data_dir / 'tasks', self.protector)
        return self.tasks

    def tasks_status(self):
        return self._tasks().status()

    def tasks_set_folder(self, folder=''):
        return self._tasks().set_folder(folder)

    def tasks_overview(self, view='open', query=''):
        result = self._tasks().overview(view, query)
        fresh = self._task_new_mail()
        for row in result['rows']:
            row['newMail'] = len(fresh.get(row['id'], ()))
        return result

    def task_get(self, id):
        from coremail_desktop.tasks import plan
        center = self._tasks()
        result = center.get(id)
        result['newMails'] = self._task_new_mail().get(result['task']['id'], [])
        result['relations'] = plan.view(center.store.tasks, result['task']['id'])
        return result

    def tasks_gantt(self, view='open', query=''):
        """甘特图 (P1) of the tasks in the list (same view and words as tasks_overview)."""
        from coremail_desktop.tasks import plan
        center = self._tasks()
        ids = {r['id'] for r in center.overview(view, query)['rows']}
        return plan.gantt(center.store.tasks, center.today(), ids)

    def _unblocked(self, result, ids):
        """前置任务完成 (P1): the 受阻 tasks now free to go on, for the page to offer."""
        from coremail_desktop.tasks import plan
        from coremail_desktop.tasks.rules import DONE
        tasks = self._tasks().store.tasks
        finished = [i for i in ids if i in tasks and tasks[i]['status'] == DONE]
        result['unblocked'] = plan.unblocked(tasks, finished) if finished else []
        for item in result['unblocked']:
            item['rev'] = self._tasks().store.revs.get(item['id'])
        return result

    def task_undo(self, seq):
        return self._tasks().undo(seq)

    def task_story(self, id):
        return self._tasks().story(id)

    def task_story_titles(self, id):
        """Name the steps of a task's story that have no AI title yet (AI thread); the story back."""
        from coremail_desktop.tasks import story
        center = self._tasks()
        result = center.story(id)
        steps = [s for s in result['steps'] if s['key'] in set(result['missing'])]
        if not steps:
            return result
        task = center.store.tasks[result['id']]
        for start in range(0, min(len(steps), story.BATCH * 3), story.BATCH):
            batch = steps[start:start + story.BATCH]
            content, _ = self._ai_call(story.title_messages(task, batch), max_tokens=story.TITLE_TOKENS, json_mode=True)
            center.keep_titles(story.parse_titles(content, {s['key'] for s in batch}))
        return center.story(id)

    def tasks_mail_index(self):
        """{mail id: [task numbers]}: the #编号 chips beside linked mail."""
        index = {}
        for link in self._tasks().store.links():
            index.setdefault(link['mid'], []).append(link['task'])
        return index

    def _task_new_mail(self):
        """{task: [mail]}: newer mail in the conversation of a task's linked mail (same subject
        once Re:/Fwd: are gone) that is not linked yet. Only a hint; nothing is changed."""
        if self.cache is None:
            return {}
        threads = {}
        for link in self._tasks().store.links():
            key = thread_key(link.get('subject') or '')
            if len(key) < 6:
                continue  # A very short subject would match unrelated mail.
            entry = threads.setdefault(link['task'], {'keys': set(), 'mids': set(), 'after': 0.0})
            entry['keys'].add(key)
            entry['mids'].add(link['mid'])
            entry['after'] = max(entry['after'], date_ts(link.get('date') or ''))
        if not threads:
            return {}
        keys = set().union(*(t['keys'] for t in threads.values()))
        skip = {f['id'] for f in self._folders if f.get('role') in ('trash', 'spam', 'virus', 'drafts')}
        rows = self.cache.matching(lambda r: not r.get('mailbox') and r.get('fid') not in skip and thread_key(r.get('subject') or '') in keys)
        found = {}
        for task_id, entry in threads.items():
            new = [r for r in rows if thread_key(r['subject']) in entry['keys'] and r['id'] not in entry['mids'] and r['ts'] > entry['after']]
            if new:
                new.sort(key=lambda r: r['ts'])
                found[task_id] = [{'id': r['id'], 'subject': r['subject'], 'from': r.get('fromName') or r['from'], 'date': r['date']} for r in new[-10:]]
        return found

    def _task_mail(self, mid, allow):
        """One selected mail for 转为任务: the envelope, the Riyadh date, attachment names and
        the body text. Encrypted mail only when the user agreed for it (allow)."""
        from email.utils import getaddresses, parsedate_to_datetime
        from coremail_desktop.ai.summary import _who, mail_text
        from coremail_desktop.tasks.rules import RIYADH
        if not self._local_only(mid) and mid not in self._docs and (self.cache is None or self.cache.get_body(mid) is None):
            raise AppError('AI_MAIL_UNAVAILABLE')
        document = self._document(mid)
        if document.encryption_state == 'locked':
            raise AppError('CERT_LOCKED')
        if document.encryption_state == 'decrypted' and mid not in allow and not self._encrypted_ok(mid):
            raise AppError('AI_ENCRYPTED_CONFIRM')
        header = lambda key: ', '.join(document.headers.get(key, ()))
        summary = (self.cache.summary(mid) if self.cache else None) or self._archive_summary(mid) or {}
        try:
            when = parsedate_to_datetime(header('date')).astimezone(RIYADH)
        except (TypeError, ValueError, IndexError, OverflowError):
            ts = summary.get('ts') or date_ts(summary.get('date') or '')
            when = (datetime.fromtimestamp(ts) if ts else datetime.now()).astimezone(RIYADH)  # No usable date: now.
        sender = getaddresses([header('from')])
        text, _ = mail_text(document)
        return {'mid': mid, 'messageId': header('message-id').strip('<> '), 'subject': header('subject') or summary.get('subject', ''),
                'from': (sender[0][0] or sender[0][1]) if sender else '', 'fromText': _who(header('from'), 1), 'toText': _who(header('to')),
                'date': when.strftime('%Y-%m-%d %H:%M'), 'day': when.strftime('%Y-%m-%d'), 'time': when.strftime('%H:%M'),
                'source': 'archive' if mid.startswith('arc:') else 'eml' if mid.startswith('eml:') else 'server',
                'attachments': [a.name for a in document.attachments][:20], 'text': text}

    def tasks_report(self, start='', end=''):
        """周报 for start..end (Riyadh dates; default this week, Sunday to today): the subject,
        mail HTML and plain text, written by the AI from the task records of that time."""
        from coremail_desktop.tasks import report
        center = self._tasks()
        if center.status()['mode'] == 'off':
            raise AppError('TASK_NOT_IMPORTED')
        if not start and not end:
            start, end = report.week_of(center.today())[0]
        start, end = report.check_range(str(start or ''), str(end or ''))
        material = report.gather(list(center.store.tasks.values()), center.store.events(), start, end)
        content, _ = self._ai_call(report.messages(material), max_tokens=report.REPORT_TOKENS, json_mode=True)
        return {**report.render(report.parse(content), material), 'from': start, 'to': end, 'active': len(material['active'])}

    def tasks_new_mail(self):
        """New mail in the conversations of unfinished tasks not yet looked at by 自动更新, one
        item per task: [{task, mids, subject}], oldest first (the page downloads each mail, then
        calls tasks_auto_mail once for the task, so a busy thread becomes one progress line)."""
        from coremail_desktop.tasks.rules import OPEN_STATUSES
        center = self._tasks()
        if center.status()['mode'] == 'off':
            return []
        seen = center.store.get('autoMail') or {}
        found = [{'task': task_id, 'mid': m['id'], 'subject': m['subject']}
                 for task_id, mails in self._task_new_mail().items()
                 if task_id in center.store.tasks and center.store.tasks[task_id]['status'] in OPEN_STATUSES
                 for m in mails if m['id'] not in seen]
        if self._understand_on():  # 任务候选 of the 理解层 (M2): one queue, each mail once.
            linked, queued = {link['mid'] for link in center.store.links()}, {f['mid'] for f in found}
            for mid, card in list(self.memory.cards.items()):
                task_id = (card.get('task') or {}).get('id')
                row = self.cache.summary(mid) if task_id else None
                if row and mid not in seen and mid not in linked and mid not in queued and task_id in center.store.tasks \
                        and center.store.tasks[task_id]['status'] in OPEN_STATUSES and not card.get('done'):
                    found.append({'task': task_id, 'mid': mid, 'subject': row['subject']})
        grouped = {}
        for item in found:
            entry = grouped.setdefault(item['task'], {'task': item['task'], 'mids': [], 'subject': item['subject']})
            entry['mids'].append(item['mid'])
        return list(grouped.values())[:10]

    def tasks_auto_mail(self, task, mids):
        """自动更新 from the new mails of a task's conversation, read together so that a thread
        with many replies gives one progress line. The AI first says whether the mail belongs to
        the task: only when it is sure ("yes") and every mail is in the task's own conversation
        (not a 理解层 guess) is progress that changes no status written at once (marked AI, can be
        undone); doubt, questions, a status change, completion or cancel are kept as suggestions to
        accept; mail about another matter ("no"), encrypted mail and nothing new leave only the hint.
        More than five new mails: the newest five are read (each reply quotes the earlier ones)."""
        from coremail_desktop.tasks.assistant import MAX_MAILS
        center = self._tasks()
        seen = center.store.get('autoMail') or {}
        mids = [mids] if isinstance(mids, str) else list(mids) if isinstance(mids, list) else []
        new = [m for m in dict.fromkeys(mids) if isinstance(m, str) and m and m not in seen]
        if not new:
            return {'applied': [], 'suggested': [], 'skipped': 'seen'}
        outcome = {'applied': [], 'suggested': [], 'skipped': ''}
        try:
            thread = {m['id'] for m in self._task_new_mail().get(int(task), [])}
            plan = self.tasks_from_mail(new[-MAX_MAILS:], target=int(task), check=True)
        except AppError as error:
            if error.code not in ('AI_ENCRYPTED_CONFIRM', 'CERT_LOCKED', 'AI_MAIL_UNAVAILABLE', 'TASK_NOT_FOUND'):
                raise  # AI unavailable: try again with the next new mail.
            outcome['skipped'] = error.code
            plan = None
        if plan is not None and plan['fit'] == 'no':
            outcome['skipped'] = 'unrelated'
        elif plan is not None:
            subject = plan['mails'][0]['subject'] if plan['mails'] else ''
            basis = f'自动更新：新邮件「{subject}」'
            ready = [(a, p) for a, p in zip(plan['actions'], plan['preview']) if not p.get('error') and p.get('changes') and a.get('id') == int(task)]
            sure = plan['fit'] == 'yes' and not plan['questions'] and all(m in thread for m in new[-MAX_MAILS:])
            if not ready:
                outcome['skipped'] = 'unsure' if plan['questions'] or plan['actions'] else 'nothing'
            elif not sure:
                outcome['skipped'] = 'unsure'
            auto = [a for a, _ in ready if sure and a['op'] == 'progress' and 'status' not in a.get('fields', {})]
            if auto:
                result = center.run_plan(auto, basis)
                outcome['applied'] = [d['id'] for d in result['done']]
            for action, item in ready:
                if action not in auto:
                    center.suggest(action, item, basis, plan['reply'] if sure else '拿不准这封邮件是否属于这条任务，请确认。' + plan['reply'])
                    outcome['suggested'].append(action['id'])
        for mid in new:
            seen[mid] = int(task)
        center.store.set('autoMail', dict(list(seen.items())[-3000:]))
        return outcome

    def tasks_from_mail(self, mids, target='auto', text='', history=None, allow=(), check=False):
        """转为任务（AI）: a plan from one to five selected mails, previewed like a sentence's.
        target: 'auto' (the AI decides), 'new', or a task number to update; check (自动更新 only,
        not from the page): the AI first says whether the mail belongs to that task ('fit')."""
        from coremail_desktop.tasks import assistant
        from coremail_desktop.ui.messages import user_message
        if not isinstance(mids, list) or not 1 <= len(mids) <= assistant.MAX_MAILS or not all(isinstance(m, str) and m for m in mids):
            raise AppError('TASK_MAIL_LIMIT')
        if target not in ('auto', 'new'):
            try:
                target = int(target)
            except (TypeError, ValueError):
                raise AppError('TASK_INVALID') from None
        text = text.strip() if isinstance(text, str) else ''
        if len(text) > assistant.MAX_TEXT or not isinstance(allow, (list, tuple)):
            raise AppError('INVALID_TASK_COMMAND')
        history = assistant.check_history(history or [])
        center = self._tasks()
        if center.status()['mode'] == 'off':
            raise AppError('TASK_NOT_IMPORTED')
        if isinstance(target, int):
            center.get(target)  # TASK_NOT_FOUND before any mail goes out.
        self._ai()  # Not configured: say so before reading the mail.
        mails = [self._task_mail(mid, {str(a) for a in allow}) for mid in dict.fromkeys(mids)]
        messages = assistant.build_mail_messages(mails, target, text, history, list(center.store.tasks.values()), center.today(),
                                                 check=check and isinstance(target, int))
        content, _ = self._ai_call(messages, max_tokens=assistant.PLAN_TOKENS, json_mode=True)
        plan = assistant.parse_plan(content)
        refs = [{k: m[k] for k in assistant.MAIL_KEYS} for m in mails]
        for action in plan['actions']:
            action['mails'] = refs
            if action['op'] == 'progress' and not action['date']:
                action['date'] = max(m['day'] for m in mails)  # The (newest) mail's date is the event's date.
        preview = center.preview(plan['actions'])
        for item in preview:
            if item.get('error'):
                item['message'] = user_message(item['error'])
        known = center.store.tasks
        candidates = [{'id': c, 'system': known[c]['system'], 'content': known[c]['content'], 'status': known[c]['status']}
                      for c in plan['candidates'] if c in known]
        return {**plan, 'candidates': candidates, 'preview': preview, 'raw': content, 'target': target,
                'mails': [{k: m[k] for k in ('mid', 'subject', 'from', 'date')} for m in mails]}

    def task_create(self, fields, date=''):
        return self._tasks().create(fields, date)

    def task_update(self, id, rev, fields, note='', date=''):
        return self._tasks().update(id, rev, fields, note, date)

    def task_progress(self, id, rev, text, date='', latest=True, fields=None):
        return self._tasks().progress(id, rev, text, date, bool(latest), fields)

    def task_complete(self, id, rev, date='', note=''):
        return self._unblocked(self._tasks().complete(id, rev, date, note), [int(id)])

    def task_cancel(self, id, rev, reason='', date=''):
        return self._tasks().cancel(id, rev, reason, date)

    def task_reopen(self, id, rev, status='进行中', reason=''):
        return self._tasks().reopen(id, rev, status, reason)

    def task_review(self, seq, accept):
        return self._tasks().review(seq, bool(accept))

    def tasks_pending(self):
        return self._tasks().pending()

    def tasks_verify(self):
        return self._tasks().verify()

    def tasks_import_preview(self):
        return self._tasks().import_preview()

    def tasks_import(self, mode='trial'):
        return self._tasks().import_books(mode)

    def tasks_export(self):
        return self._tasks().export(message='重新导出任务清单（鸿雁）')

    def tasks_resolve(self, action):
        return self._tasks().resolve_external(action)

    def tasks_stop_live(self):
        return self._tasks().stop_live()

    def tasks_restore(self, mode='live'):
        return self._tasks().restore(mode)

    def tasks_compare(self):
        return self._tasks().compare()

    def task_link(self, id, mid):
        """关联到任务: the open mail's ids, subject and date as they are now."""
        mid = str(mid or '')
        summary = (self.cache.summary(mid) if self.cache else None) or self._archive_summary(mid) or {}
        document = self._docs.get(mid)
        header = lambda key: ', '.join(document.headers.get(key, ())) if document else ''
        source = 'archive' if mid.startswith('arc:') else 'eml' if mid.startswith('eml:') else 'server'
        return self._tasks().link(id, {'mid': mid, 'messageId': header('message-id').strip('<> '), 'source': source,
                                       'subject': summary.get('subject') or header('subject'), 'date': summary.get('date') or header('date')})

    def task_unlink(self, id, link):
        return self._tasks().unlink(id, link)

    def tasks_command(self, text, history=None):
        """自然语言操作: the sentence (and the turns before it, when the assistant asked back)
        -> a plan with each operation previewed. Nothing is written here."""
        from coremail_desktop.tasks import assistant
        from coremail_desktop.ui.messages import user_message
        text = text.strip() if isinstance(text, str) else ''
        if not text or len(text) > assistant.MAX_TEXT:
            raise AppError('INVALID_TASK_COMMAND')
        history = assistant.check_history(history or [])
        center = self._tasks()
        if center.status()['mode'] == 'off':
            raise AppError('TASK_NOT_IMPORTED')
        messages = assistant.build_messages(text, history, list(center.store.tasks.values()), center.today())
        content, _ = self._ai_call(messages, max_tokens=assistant.PLAN_TOKENS, json_mode=True)
        plan = assistant.parse_plan(content)
        preview = center.preview(plan['actions'])
        for item in preview:
            if item.get('error'):
                item['message'] = user_message(item['error'])
        return {**plan, 'preview': preview, 'raw': content}

    def tasks_command_run(self, actions, text=''):
        from coremail_desktop.tasks import assistant
        result = self._tasks().run_plan(assistant.check_actions(actions), text)
        return self._unblocked(result, [d['id'] for d in result['done']])

    # Background sync -------------------------------------------------------
    # 理解层: every received mail read once in the background (ai/understand.py). The page
    # drives it like 任务中心's 自动更新: understand_pending (fast) -> understand_fetch (mail
    # thread, downloads without marking read) -> understand_mail (AI thread, one call each).
    def _memory(self):
        if self.memory is None:
            raise AppError(getattr(self, '_memory_error', '') or 'MEMORY_UNAVAILABLE' if self.cache is not None else 'NOT_AUTHENTICATED')
        return self.memory

    def _understand_on(self):
        return self.memory is not None and self.cache is not None and self.prefs()['understand'] == 'on'

    def _understand_rows(self):
        """Mail the 理解层 covers: received in the last UNDERSTAND_DAYS days, in the inbox and
        the user's own folders (not sent, drafts, trash, spam; not shared mailboxes)."""
        folders = self._folders or self.cache.get('folders') or []
        roles = {f['id']: f.get('role') for f in folders}
        cutoff = time.time() - UNDERSTAND_DAYS * 86400
        return self.cache.matching(lambda r: not r.get('mailbox') and r['fid'] in roles and roles[r['fid']] not in UNDERSTAND_SKIP
                                   and (r.get('ts') or 0) >= cutoff and not r['flags'].get('draft') and not r['flags'].get('scheduleDelivery'))

    def understand_pending(self):
        """The next round: {ids (newest first, at most UNDERSTAND_ROUND), missing (bodies to
        download first), left (all not yet read)}. Encrypted mail the user has not allowed,
        mail the user corrected and mail that failed this session are left out."""
        from coremail_desktop.ai.understand import CARD_VERSION
        if not self._understand_on():
            return {'ids': [], 'missing': [], 'left': 0}
        memory = self.memory
        if not getattr(self, '_cards_tidied', False):
            self._cards_tidied = True
            memory.drop_cards(lambda mid: self.cache.summary(mid) is not None)

        def todo(row):
            card = memory.card(row['id']) or {}
            if card.get('actor') == 'user' or card.get('done') or card.get('v') == CARD_VERSION or row['id'] in self._understand_failed:
                return False
            return not row['flags'].get('encrypted') or self._encrypted_ok(row['id'])
        rows = sorted((r for r in self._understand_rows() if todo(r)), key=lambda r: r.get('ts') or 0, reverse=True)
        ids = [r['id'] for r in rows[:UNDERSTAND_ROUND]]
        return {'ids': ids, 'missing': [m for m in ids if self.cache.get_body(m) is None], 'left': len(rows)}

    def understand_fetch(self, ids):
        """Download the bodies of a round (mail thread; never marks read). -> how many failed."""
        if not isinstance(ids, list) or len(ids) > UNDERSTAND_ROUND:
            raise AppError('UNDERSTAND_INVALID')
        failed = 0
        for mid in ids:
            try:
                self._raw(str(mid))
            except AppError as error:
                if error.code in REAUTH_CODES + NETWORK_CODES:
                    raise
                self._understand_failed[str(mid)] = error.code
                failed += 1
        return failed

    def _open_tasks(self):
        """Unfinished tasks of the 任务中心 when it is already open (never opened for this)."""
        if self.tasks is None:
            return []
        try:
            from coremail_desktop.tasks.rules import OPEN_STATUSES
            if self.tasks.status()['mode'] == 'off':
                return []
            return [t for t in self.tasks.store.tasks.values() if t.get('status') in OPEN_STATUSES]
        except Exception:
            return []

    def _projects(self):
        """The project list (工作记忆 5.3): projects added by hand, then the 系统 of unfinished tasks."""
        from coremail_desktop.memory.merge import project_list
        manual = (self.memory.get('projects') if self.memory else None) or []
        return project_list(manual, sorted({t['system'].strip() for t in self._open_tasks() if str(t.get('system') or '').strip()}))

    def understand_mail(self, mid):
        """Read one mail and keep its card (AI thread). A matching 忽略 rule needs no AI call."""
        from coremail_desktop.ai import understand
        from coremail_desktop.memory import merge
        mid = str(mid)
        memory = self._memory()
        refs = {}
        row = self.cache.summary(mid) or {}
        rule = understand.rule_for(list(memory.rules.values()), row)
        if rule and rule['kind'] == 'ignore':
            card = {'kind': 'ignore', 'sure': True, 'gist': '', 'ask': '', 'due': '', 'project': '', 'facts': [], 'rule': rule['id'], 'model': ''}
        else:
            client = self._ai()
            try:
                document, _ = self._ai_document(mid, False)
            except AppError as error:
                if error.code in ('AI_ENCRYPTED_CONFIRM', 'CERT_LOCKED', 'AI_MAIL_UNAVAILABLE'):
                    self._understand_failed[mid] = error.code
                raise
            projects, tasks = self._projects(), self._open_tasks()
            keep = self.prefs()['memoryFacts']  # R3c: off, the card only sorts the mail; no project facts.
            refs = merge.prompt_facts(memory.facts, projects) if keep else {}
            brief = [(t['id'], ' '.join(filter(None, (t.get('system'), t.get('content'))))) for t in sorted(tasks, key=lambda t: t['id'])][-understand.TASKS:]
            messages = understand.build_messages(document, self.display_name, [self.account or '', *self.identities], projects,
                                                 row.get('date') or '', datetime.now().date(), tasks=brief, facts=refs)
            content, _ = self._ai_call(messages, max_tokens=understand.CARD_TOKENS, json_mode=True)
            try:
                card = understand.apply_rule(understand.parse(content, {i for i, _ in brief}, refs), rule)
            except AppError:
                self._understand_failed[mid] = 'AI_BAD_RESPONSE'  # Not asked again this session.
                raise
            card['model'] = getattr(client, 'model', '')
        card.update(v=understand.CARD_VERSION, at=datetime.now().strftime('%Y-%m-%d %H:%M'), actor='ai',
                    messageId=(self._docs[mid].headers.get('message-id', ('',))[0].strip('<> ') if mid in self._docs else ''))
        memory.put_card(mid, card)
        if card.get('facts') and card['kind'] != 'ignore' and self.prefs()['memoryFacts']:
            self._remember(mid, card, row, refs)
        month = datetime.now().strftime('%Y-%m')
        usage = memory.get('usage') or {}
        memory.set('usage', {'month': month, 'mails': (usage.get('mails', 0) if usage.get('month') == month else 0) + 1})
        return _card_view(card)

    def _internal(self, address):
        """A colleague: the account's own domain (or one of its identities')."""
        domains = {a.rpartition('@')[2].casefold() for a in [self.account or '', *self.identities] if '@' in a}
        return address.rpartition('@')[2].casefold() in domains if '@' in (address or '') else False

    def _remember(self, mid, card, row, refs):
        """工作记忆: the card's facts merged into the project memory (memory/merge.py)."""
        from coremail_desktop.memory import merge
        source = {'mid': mid, 'messageId': card.get('messageId', ''), 'subject': (row.get('subject') or '')[:120],
                  'date': datetime.fromtimestamp(row['ts']).strftime('%Y-%m-%d') if row.get('ts') else '',
                  'from': (row.get('fromName') or '')[:60], 'internal': self._internal(row.get('fromEmail') or '')}
        for fact in merge.merge(self.memory.facts, card['facts'], source, refs, self._projects()):
            self.memory.put_fact(fact)

    def understand_cards(self, ids):
        """{mid: card} for the rows on screen (fast thread); nothing while the 理解层 is off."""
        if not isinstance(ids, list) or len(ids) > 1000:
            raise AppError('UNDERSTAND_INVALID')
        if not self._understand_on():
            return {}
        found = {}
        for mid in ids:
            card = self.memory.card(str(mid))
            if card:
                found[str(mid)] = _card_view(card)
        return found

    def understand_view(self, kind='act'):
        """待我处理 (act: sure, not done; by due date, then newest) or 需要知道 (know: newest
        first). Rows are the cached summaries with their card."""
        if kind not in ('act', 'know'):
            raise AppError('UNDERSTAND_INVALID')
        if not self._understand_on():
            return {'rows': [], 'total': 0}
        rows = []
        for row in self._understand_rows():
            card = self.memory.card(row['id'])
            if not card or card.get('kind') != kind or (kind == 'act' and (card.get('done') or not card.get('sure'))):
                continue
            rows.append({**row, 'card': _card_view(card)})
        if kind == 'act':
            rows.sort(key=lambda r: -(r.get('ts') or 0))
            rows.sort(key=lambda r: r['card']['due'] or '9999')
            rows.sort(key=lambda r: bool(r['card']['answered']))  # Answered (R3a): after the ones still waiting on me.
        else:
            rows.sort(key=lambda r: r.get('ts') or 0, reverse=True)
        return {'rows': rows, 'total': len(rows)}

    def understand_status(self):
        on = self._understand_on()
        counts = {'act': 0, 'know': 0, 'wait': 0}
        if on:
            for row in self._understand_rows():
                card = self.memory.card(row['id']) or {}
                if card.get('kind') == 'act' and card.get('sure') and not card.get('done'):
                    counts['act'] += 1
                elif card.get('kind') == 'know' and not row['flags'].get('read'):
                    counts['know'] += 1
            counts['wait'] = len(self.followups())
        usage = (self.memory.get('usage') if self.memory else None) or {}
        month = datetime.now().strftime('%Y-%m')
        from coremail_desktop.ui.messages import user_message
        error = getattr(self, '_memory_error', '')
        return {'state': self.prefs()['understand'] if self.cache else '', 'available': self.memory is not None,
                'unavailable': user_message(error) if self.memory is None and error else '',
                'encrypted': self.ai_encrypted(), 'counts': counts, 'rules': len(self.memory.rules) if self.memory else 0,
                'month': usage.get('mails', 0) if usage.get('month') == month else 0, 'failed': len(self._understand_failed)}

    def set_understand(self, state):
        if state not in ('on', 'off'):
            raise AppError('UNDERSTAND_INVALID')
        self._memory()
        self.set_prefs({'understand': state})
        return self.understand_status()

    def understand_correct(self, mid, kind=None, project=None, done=None):
        """The user's own word on one card; the 理解层 never reads that mail again."""
        from coremail_desktop.ai.understand import KINDS, PROJECT, _text
        memory, mid = self._memory(), str(mid)
        card = memory.card(mid)
        if card is None:
            raise AppError('UNDERSTAND_NOT_FOUND')
        card = dict(card)
        if kind is not None:
            if kind not in KINDS:
                raise AppError('UNDERSTAND_INVALID')
            card.update(kind=kind, sure=True, actor='user')
            if kind != 'act':
                card.update(ask='', due='')
        if project is not None:
            if not isinstance(project, str) or len(project) > 200:
                raise AppError('UNDERSTAND_INVALID')
            card.update(project=_text(project, PROJECT), actor='user')
        if done is not None:
            card['done'] = bool(done)
        memory.put_card(mid, card)
        return _card_view(card)

    def understand_set_done(self, ids, done=True):
        """全部标为已处理 (and its 撤销): the 要我做 cards among ids set to done; returns the
        ids that changed, so 撤销 puts back exactly those."""
        if not isinstance(ids, list) or len(ids) > 1000:
            raise AppError('UNDERSTAND_INVALID')
        memory, changed = self._memory(), []
        for mid in map(str, ids):
            card = memory.card(mid)
            if card and card.get('kind') == 'act' and bool(card.get('done')) != bool(done):
                memory.put_card(mid, {**card, 'done': bool(done)})
                changed.append(mid)
        return {'changed': changed, 'status': self.understand_status()}

    def understand_answered(self, mid, how='reply'):
        """回复后只标「已回复」(R3a): a reply ('reply') or forward ('forward') of mid went out. A
        reply is not the work done, so the 要我做 card only says so and stays on the list, after
        the ones not yet answered; the user marks it 已处理. Returns the ids that changed."""
        if not isinstance(mid, str) or not mid or len(mid) > 300 or how not in ('reply', 'forward'):
            raise AppError('UNDERSTAND_INVALID')
        card = self.memory.card(mid) if self._understand_on() else None
        if not card or card.get('kind') != 'act' or not card.get('sure') or card.get('done') or card.get('answered') == how:
            return {'changed': []}
        self.memory.put_card(mid, {**card, 'answered': how})
        return {'changed': [mid]}

    # 今天 (R2a) ------------------------------------------------------------------
    def today_overview(self):
        """今天: one list of what is mine to do now — 要我做 mail and unfinished tasks overdue or
        due within TODAY_DAYS days (受阻 ones wait on others, so they are under 在等别人) —
        overdue first, then by due date, undated last (newest first); and the counts behind
        the head's links. From the caches and the task store only: no AI, no network."""
        from coremail_desktop.tasks.rules import is_day
        center = self._tasks()
        today, tasks_on = center.today(), center.status()['mode'] != 'off'
        horizon = (datetime.fromisoformat(today) + timedelta(days=TODAY_DAYS)).date().isoformat()
        understanding = self._understand_on()
        rows = [{'kind': 'mail', 'id': r['id'], 'due': r['card']['due'], 'ts': r.get('ts') or 0, 'mail': r}
                for r in (self.understand_view('act')['rows'] if understanding else [])]
        blocked = []
        for t in center.overview('open')['rows'] if tasks_on else []:
            if t['status'] == '受阻':
                blocked.append(t)
            elif is_day(t['due']) and t['due'] <= horizon:
                rows.append({'kind': 'task', 'id': t['id'], 'due': t['due'], 'ts': 0, 'task': t})
        rows.sort(key=lambda r: -r['ts'])
        rows.sort(key=lambda r: (not (r['due'] and r['due'] < today), r['due'] or '9999', bool(r['kind'] == 'mail' and r['mail']['card']['answered'])))
        waiting = len(self.followups()) if understanding else 0
        return {'date': today, 'rows': rows, 'understanding': understanding, 'tasks': tasks_on,
                'unread': self.understand_pending()['left'] if understanding else 0,  # Mail the AI has not read yet (V2).
                'counts': {'overdue': sum(1 for r in rows if r['due'] and r['due'] < today),
                           'today': sum(1 for r in rows if r['due'] == today),
                           'waiting': waiting + len(blocked), 'confirm': len(center.pending()) if tasks_on else 0}}

    def today_waiting(self):
        """在等别人: sent mail nobody answered (催办) and 受阻 tasks."""
        center = self._tasks()
        tasks = [t for t in center.overview('open')['rows'] if t['status'] == '受阻'] if center.status()['mode'] != 'off' else []
        return {'mail': self.followups() if self._understand_on() else [], 'tasks': tasks}

    def understand_rules(self):
        return sorted(self._memory().rules.values(), key=lambda r: (r['sender'], r['keyword']))

    def understand_add_rule(self, sender='', keyword='', kind='know'):
        """以后这样处理: a rule from a correction. It also applies at once to the cards it
        matches that the user has not corrected by hand."""
        from coremail_desktop.ai.understand import RULE_LIMIT, apply_rule, new_rule, rule_for
        memory = self._memory()
        rule = new_rule(sender, keyword, kind)
        same = [r for r in memory.rules.values() if (r['sender'], r['keyword']) == (rule['sender'], rule['keyword'])]
        if same:
            rule['id'] = same[0]['id']
        elif len(memory.rules) >= RULE_LIMIT:
            raise AppError('UNDERSTAND_RULE_LIMIT')
        memory.put_rule(rule)
        changed = 0
        for mid, card in list(memory.cards.items()):
            row = self.cache.summary(mid)
            if row and card.get('actor') != 'user' and rule_for([rule], row):
                memory.put_card(mid, apply_rule(card, rule))
                changed += 1
        return {'rule': rule, 'changed': changed}

    def understand_delete_rule(self, rule_id):
        self._memory().delete_rule(str(rule_id))
        return self.understand_rules()

    def understand_clear(self):
        """清除理解与记忆: cards, rules and counts of this account; the 理解层 stays as set."""
        self._memory().clear()
        self._understand_failed = {}
        return self.understand_status()

    # 工作记忆 (M2) ----------------------------------------------------------------
    def memory_projects(self):
        """The project list (projects, each followed by its systems) with each one's number of
        current facts and of facts waiting for the user."""
        facts = self._memory().facts.values()
        listed = self._projects()
        order = [p for top in listed if not top['parent'] for p in [top] + [x for x in listed if x['parent'] == top['name']]]
        return [{**p, 'facts': sum(f['project'] == p['name'] and f['state'] == 'current' for f in facts),
                 'pending': sum(f['project'] == p['name'] and f['state'] == 'pending' for f in facts),
                 'systems': sum(x['parent'] == p['name'] for x in listed)} for p in order]

    def memory_project(self, name, role=None, aliases=None, remove=False, parent=None, rename=None):
        """Add or change an entry of the project list: role (own / watch, a project's), aliases
        (other names of the same one, whose facts move to it), parent (the project a system
        belongs to, '' for a project of its own), rename (the old name becomes an alias); or
        take a hand-added one off the list. A 系统 from the task center is changed by keeping
        a copy of it here (source 'task')."""
        from coremail_desktop.memory.merge import NAME, PROJECTS, ROLES, norm
        memory = self._memory()
        clean = lambda v: v.strip() if isinstance(v, str) else ''
        name, rename = clean(name), clean(rename) if rename is not None else None
        if not name or len(name) > NAME or (role is not None and role not in ROLES) or (rename is not None and not 0 < len(rename) <= NAME) or (
                parent is not None and (not isinstance(parent, str) or len(parent) > NAME)) or (aliases is not None and (
                not isinstance(aliases, list) or len(aliases) > 10 or not all(isinstance(a, str) and 0 < len(a.strip()) <= NAME for a in aliases))):
            raise AppError('MEMORY_INVALID')
        listed = self._projects()
        manual = [p for p in memory.get('projects') or [] if isinstance(p, dict) and p.get('name')]
        found = next((p for p in manual if norm(p['name']) == norm(name)), None)
        moved = set()  # Names whose facts now belong to found.
        if remove:
            manual = [p for p in manual if p is not found]
            for p in manual:
                if found and p.get('parent') == found['name']:
                    p['parent'] = ''
        else:
            if found is None:
                if len(manual) >= PROJECTS:
                    raise AppError('MEMORY_INVALID')
                known = next((p for p in listed if norm(p['name']) == norm(name)), None)
                found = {'name': known['name'] if known else name, 'role': known['role'] if known else 'watch', 'aliases': [],
                         'parent': '', 'source': known['source'] if known else 'manual'}
                manual.append(found)
            if role is not None:
                found['role'] = role
            if rename is not None and norm(rename) != norm(found['name']):
                if any(norm(rename) in {norm(n) for n in [p['name'], *p['aliases']]} for p in listed if norm(p['name']) != norm(found['name'])):
                    raise AppError('MEMORY_INVALID')  # Another entry has that name: merge with 别名 instead.
                old = found['name']
                found['aliases'] = [a for a in found.get('aliases') or [] if norm(a) != norm(rename)] + [old]
                found['name'] = rename
                moved.add(norm(old))
                for p in manual:
                    if p.get('parent') == old:
                        p['parent'] = rename
            if aliases is not None:
                found['aliases'] = [a.strip() for a in aliases if norm(a) != norm(found['name'])]
                merged = {norm(a) for a in found['aliases']}
                for p in [p for p in manual if p is not found and norm(p['name']) in merged]:
                    manual.remove(p)  # Merged into this one; its systems come along.
                    for q in manual:
                        if q.get('parent') == p['name']:
                            q['parent'] = found['name'] if not found.get('parent') else ''
                moved |= merged
            if parent is not None:
                target = next((p for p in listed if norm(p['name']) == norm(parent)), None) if parent.strip() else None
                if parent.strip() and (target is None or target['parent'] or norm(target['name']) == norm(found['name'])
                                       or any(x['parent'] == found['name'] for x in listed)):
                    raise AppError('MEMORY_INVALID')  # One level only: a project with systems stays a project.
                if target and not any(norm(p['name']) == norm(target['name']) for p in manual):
                    manual.append({'name': target['name'], 'role': target['role'], 'aliases': list(target['aliases']), 'parent': '', 'source': target['source']})
                found['parent'] = target['name'] if target else ''
        memory.set('projects', manual)
        if found and not remove and moved:
            for fact in list(memory.facts.values()):
                if norm(fact['project']) in moved:
                    memory.put_fact({**fact, 'project': found['name']})
        return self.memory_projects()

    def memory_facts(self, project):
        """One project's memory (a project with its systems'), newest first, with what each
        fact replaced (the 时间线); 'parent' names the project of a system."""
        from coremail_desktop.memory.merge import resolve, timeline
        listed = self._projects()
        name = resolve(project, listed) if isinstance(project, str) else ''
        if not name:
            raise AppError('MEMORY_NOT_FOUND')
        entry = next(p for p in listed if p['name'] == name)
        return {'project': name, 'parent': entry['parent'], 'systems': [p['name'] for p in listed if p['parent'] == name],
                'facts': [_fact_view(f) for f in timeline(self._memory().facts, name, listed)]}

    def memory_recent(self, project):
        """这个项目最近 (the reader's strip): the newest two current facts and how many wait."""
        from coremail_desktop.memory.merge import family, resolve, search
        if not self._understand_on() or not isinstance(project, str):
            return {'project': '', 'facts': [], 'pending': 0}
        listed = self._projects()
        name = resolve(project, listed)
        if not name:
            return {'project': '', 'facts': [], 'pending': 0}
        # A system's own facts first; its project's (and the other systems') fill up to two.
        entry = next(p for p in listed if p['name'] == name)
        rows = search(self.memory.facts, name, projects=listed)
        if len(rows) < 2 and entry['parent']:
            rows += [f for f in search(self.memory.facts, entry['parent'], projects=listed) if f not in rows]
        names = family(entry['parent'] or name, listed)
        return {'project': name, 'parent': entry['parent'], 'facts': [_fact_view(f) for f in rows[:2]],
                'pending': sum(f['project'] in names and f['state'] == 'pending' for f in self.memory.facts.values())}

    def memory_correct(self, fact_id, action, text=''):
        """这条不对 (wrong), 已过时 (stale), 改成 (edit), or accept / reject a waiting replacement."""
        from coremail_desktop.ai.understand import FACT
        from coremail_desktop.memory.merge import correct
        memory = self._memory()
        text = re.sub(r'\s+', ' ', text).strip() if isinstance(text, str) else ''
        if action not in ('wrong', 'stale', 'edit', 'accept', 'reject') or (action == 'edit' and not 0 < len(text) <= FACT):
            raise AppError('MEMORY_INVALID')
        changed = correct(memory.facts, str(fact_id), action, text)
        if changed is None:
            raise AppError('MEMORY_NOT_FOUND')
        for fact in changed:
            memory.put_fact(fact)
        return self.memory_facts(self._top(changed[0]['project']))

    def _top(self, name):
        from coremail_desktop.memory.merge import top
        return top(name, self._projects())

    def _memory_search(self, project, text, history=False):
        """问邮箱's memory_search: (project as listed or '', facts with their source mail rows)."""
        from coremail_desktop.memory.merge import resolve, search
        if not self._understand_on():
            return None
        listed = self._projects()
        name = resolve(project, listed) if project else ''
        if project and not name:
            return '', []
        rows = search(self.memory.facts, name, text, history, listed)
        return name, [{**_fact_view(f), 'rows': [r for r in (self.cache.summary(s['mid']) for s in f['sources'] if s.get('mid')) if r]} for f in rows]

    # 每周需要知道、人物卡片、催办 (M3) -------------------------------------------------
    def memory_week(self, offset=0):
        """本周你需要知道的 (offset 1: last week): per project, the facts of the week."""
        from coremail_desktop.memory.merge import week
        from coremail_desktop.tasks.report import week_of
        if offset not in (0, 1):
            raise AppError('MEMORY_INVALID')
        if not self._understand_on():
            return {'from': '', 'to': '', 'groups': []}
        start, end = week_of(datetime.now().date().isoformat())[offset]
        return {'from': start, 'to': end, 'groups': [{'project': g['project'], 'facts': [_fact_view(f) for f in g['facts']], 'key': g['key']}
                                                     for g in week(self.memory.facts, start, end, self._projects())]}

    def memory_week_brief(self, offset=0, refresh=False):
        """本周你需要知道的, summed up by the AI: per project one sentence and up to 3 points.
        Kept for the same facts of the same week; a new or changed fact makes it again."""
        from coremail_desktop.ai.weekly import WEEK_TOKENS, build_messages, parse
        from coremail_desktop.memory.merge import week
        from coremail_desktop.tasks.report import week_of
        if offset not in (0, 1):
            raise AppError('MEMORY_INVALID')
        if not self._understand_on():
            return {'from': '', 'to': '', 'projects': {}}
        start, end = week_of(datetime.now().date().isoformat())[offset]
        groups = week(self.memory.facts, start, end, self._projects())
        if not groups:
            return {'from': start, 'to': end, 'projects': {}}
        client = self._ai()
        key = hashlib.sha256(json.dumps([start, end, [[g['project'], [(f['id'], f['state'], f['text']) for f in g['facts']]] for g in groups]],
                                        ensure_ascii=False).encode()).hexdigest()[:24]
        saved = self.cache.get('memoryBriefs') or {}
        if not refresh and key in saved:
            return {**saved[key], 'cached': True}
        content, _ = self._ai_call(build_messages(groups, start, end), max_tokens=WEEK_TOKENS, json_mode=True)
        result = {'from': start, 'to': end, 'projects': parse(content, [g['project'] for g in groups]),
                  'model': getattr(client, 'model', ''), 'at': datetime.now().strftime('%Y-%m-%d %H:%M')}
        saved.pop(key, None)
        saved[key] = result
        self.cache.set('memoryBriefs', dict(list(saved.items())[-8:]))
        return result

    def _mine(self):
        return {a.casefold() for a in [self.account or '', *self.identities] if a}

    def memory_person(self, email, name=''):
        """人物卡片: what this person is in charge of (owner facts naming them), the latest mail
        with them, their mail still waiting for me (待我处理) and mine waiting for them (催办)."""
        from coremail_desktop.memory.merge import norm
        email = email.strip().casefold() if isinstance(email, str) else ''
        if not email or len(email) > 200 or '@' not in email or not isinstance(name, str) or len(name) > 100:
            raise AppError('MEMORY_INVALID')
        if not self._understand_on():
            return {'on': False}
        cutoff = time.time() - 90 * 86400
        rows = self.cache.matching(lambda r: not r.get('mailbox') and (r.get('ts') or 0) >= cutoff and not r['flags'].get('draft') and (
            (r.get('fromEmail') or '').casefold() == email or email in (r.get('to') or '').casefold()))
        rows.sort(key=lambda r: r.get('ts') or 0, reverse=True)
        names = {norm(n) for n in [name, *(r.get('fromName') for r in rows if (r.get('fromEmail') or '').casefold() == email)] if norm(n) and norm(n) != email}
        names |= {norm(email.partition('@')[0])}
        owner = [_fact_view(f) for f in self.memory.facts.values()
                 if f['state'] == 'current' and f['type'] == 'owner' and any(n in norm(f['text']) for n in names if len(n) >= 2)]
        light = lambda r: {'id': r['id'], 'subject': r.get('subject') or '', 'date': r.get('date') or ''}
        waiting = []
        for r in rows:
            card = self.memory.card(r['id']) or {}
            if (r.get('fromEmail') or '').casefold() == email and card.get('kind') == 'act' and card.get('sure') and not card.get('done'):
                waiting.append({**light(r), 'ask': card.get('ask') or card.get('gist') or ''})
        chase = [{**light(r), 'waiting': r['waiting']} for r in self.followups() if email in (r.get('to') or '').casefold()]
        return {'on': True, 'owner': owner[:5], 'recent': [light(r) for r in rows[:5]], 'waiting': waiting[:5], 'chase': chase[:5]}

    def followups(self):
        """催办: mail I sent in the last 30 days, at least FOLLOWUP_DAYS ago, that asked
        something (a question mark or a request word in the subject or preview) of its To
        recipients, with no later mail from anyone else in its conversation and no later one
        of mine. Mail the user said needs no follow-up is left out. Only cached mail counts."""
        if not self._understand_on():
            return []
        folders = self._folders or self.cache.get('folders') or []
        sent = {f['id'] for f in folders if f.get('role') == 'sent'}
        now, mine = time.time(), self._mine()
        cutoff, ready = now - UNDERSTAND_DAYS * 86400, now - FOLLOWUP_DAYS * 86400
        dismissed = set(self.memory.get('followupDismissed') or [])
        skip = {f['id'] for f in folders if f.get('role') in THREAD_SKIP}
        mail = self.cache.matching(lambda r: not r.get('mailbox') and (r.get('ts') or 0) >= cutoff and r['fid'] not in skip
                                   and not r['flags'].get('draft') and not r['flags'].get('scheduleDelivery'))
        threads = {}
        for r in mail:
            threads.setdefault(thread_key(r.get('subject') or ''), []).append(r)
        out = []
        for r in mail:
            if r['fid'] not in sent or r['ts'] > ready or r['id'] in dismissed or not ASKING.search(f"{r.get('subject') or ''} {r.get('preview') or ''}"):
                continue
            to = [(n, a) for n, a in getaddresses([r.get('to') or '']) if a and a.casefold() not in mine]
            if not to:
                continue
            key = thread_key(r.get('subject') or '')
            later = [x for x in threads.get(key, ()) if x['ts'] > r['ts'] and x['id'] != r['id']] if len(key) >= 4 else []
            if any(x['fid'] in sent or (x.get('fromEmail') or '').casefold() not in mine for x in later):
                continue  # Answered, or followed up already (the newest of mine is the one to chase).
            out.append({**r, 'waiting': int((now - r['ts']) // 86400), 'toNames': '、'.join(n or a for n, a in to[:5])})
        out.sort(key=lambda r: r['waiting'], reverse=True)
        return out

    def followup_dismiss(self, mid, undo=False):
        """不用跟进 (or its 撤销) for one sent mail."""
        if not isinstance(mid, str) or not mid or len(mid) > 200:
            raise AppError('MEMORY_INVALID')
        memory = self._memory()
        kept = [m for m in memory.get('followupDismissed') or [] if m != mid]
        memory.set('followupDismissed', kept[-FOLLOWUP_KEEP:] if undo else (kept + [mid])[-FOLLOWUP_KEEP:])
        return len(self.followups())

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
