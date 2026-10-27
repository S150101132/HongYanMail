"""Outlook data files (.pst), read-only, as one more source of the local archive.

libpff (pypff) reads the file. For the catalog a mail item is described straight from its
MAPI properties (plain text, names of attachments; no attachment is read); to open one it
is rebuilt as an RFC 822 message so the reader and S/MIME work on it as on any other mail.
Only mail is taken (IPM.Note*, meeting messages, reports); appointments and contacts are
skipped. A message is found again by its place in the folder tree and checked against its
node identifier and submit time.
"""
import hashlib
import re
from datetime import timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, formataddr
from pathlib import Path

from coremail_desktop.errors import AppError

MAIL_CLASSES = ('ipm.note', 'ipm.schedule.meeting', 'report.ipm.note', 'ipm.outlook.recall', 'ipm.recall')
# MAPI property tags (MS-OXPROPS).
CLASS, SUBJECT, SENT_NAME, SENT_SMTP, SENDER_NAME, SENDER_SMTP, SENDER_ADDR = 0x001A, 0x0037, 0x0042, 0x5D02, 0x0C1A, 0x5D01, 0x0C1F
MESSAGE_ID, IN_REPLY_TO, REFERENCES, SIZE, CPID = 0x1035, 0x1042, 0x1039, 0x0E08, 0x3FDE
SUBMITTED, DELIVERED, PLAIN, HTML = 0x0039, 0x0E06, 0x1000, 0x1013
R_TYPE, R_NAME, R_ADDR, R_SMTP = 0x0C15, 0x3001, 0x3003, 0x39FE
A_METHOD, A_LONG, A_SHORT, A_MIME, A_CID = 0x3705, 0x3707, 0x3704, 0x370E, 0x3712
BY_VALUE, EMBEDDED = 1, 5
MAX_ATTACHMENT = 200 * 1024 * 1024
CODEPAGES = {936: 'gb18030', 54936: 'gb18030', 65001: 'utf-8', 1252: 'cp1252', 950: 'big5', 20127: 'ascii', 28591: 'latin-1'}


def _pypff():
    try:
        import pypff
    except ImportError:
        raise AppError('ARCHIVE_UNSUPPORTED')
    return pypff


def _props(item):
    """{tag: record entry} of an item's first record set (messages and attachments have one)."""
    found = {}
    for records in item.record_sets:
        for entry in records.entries:
            found.setdefault(entry.entry_type, entry)
        break
    return found


def _text(props, tag):
    entry = props.get(tag)
    if entry is None:
        return ''
    try:
        return (entry.data_as_string or '').strip()
    except Exception:
        return ''


def _int(props, tag):
    entry = props.get(tag)
    try:
        return entry.data_as_integer if entry is not None else None
    except Exception:
        return None


def _bytes(props, tag):
    entry = props.get(tag)
    try:
        return entry.data if entry is not None else None
    except Exception:
        return None


def is_mail(props):
    return _text(props, CLASS).lower().startswith(MAIL_CLASSES)


def _when(props):
    """Sent time, else delivery time (UTC, naive as libpff gives it)."""
    for tag in (SUBMITTED, DELIVERED):
        entry = props.get(tag)
        try:
            if entry is not None and entry.data_as_datetime:
                return entry.data_as_datetime
        except Exception:
            continue
    return None


def _decode(data, codepage):
    if data is None or isinstance(data, str):
        return data or ''
    try:
        return data.decode(CODEPAGES.get(codepage, f'cp{codepage}' if codepage else 'utf-8'), 'replace')
    except LookupError:
        return data.decode('utf-8', 'replace')


def _bodies(message, props):
    """(plain, html) as text. Some items (recalls, reports) are plain pypff items: their
    bodies come from the properties."""
    plain = getattr(message, 'plain_text_body', None) if hasattr(message, 'plain_text_body') else _bytes(props, PLAIN)
    html = getattr(message, 'html_body', None) if hasattr(message, 'html_body') else _bytes(props, HTML)
    if plain is not None and not isinstance(plain, str):
        plain = plain.decode('utf-8', 'replace') if hasattr(message, 'plain_text_body') else plain.decode('utf-16-le', 'replace')
    return plain or '', _decode(html, _int(props, CPID))


def _people(message, props):
    """{'from': (name, address), 1/2/3 (to/cc/bcc): [(name, address)]}; addresses that are
    not SMTP (Exchange's /O=…) are dropped."""
    def smtp(address):
        return address if '@' in address and address.isascii() and ' ' not in address else ''
    found = {'from': (_text(props, SENT_NAME) or _text(props, SENDER_NAME),
                      smtp(_text(props, SENT_SMTP) or _text(props, SENDER_SMTP) or _text(props, SENDER_ADDR))),
             1: [], 2: [], 3: []}
    recipients = getattr(message, 'recipients', None)
    for records in (recipients.record_sets if recipients is not None else ()):
        row = {e.entry_type: e for e in records.entries}
        kind = _int(row, R_TYPE)
        if kind in found:
            found[kind].append((_text(row, R_NAME), smtp(_text(row, R_SMTP) or _text(row, R_ADDR))))
    return found


def _shown(people):
    return ', '.join(f'{n} <{a}>' if n and a else a or n for n, a in people if n or a)


def _header_addr(name, address):
    return formataddr((name, address)) if address else (f'"{name}"' if name else '')


def _attachments(message, html):
    """(attachment, props, maintype, subtype, inline) of each attachment."""
    found = []
    for attachment in getattr(message, 'attachments', ()):
        a = _props(attachment)
        maintype, _, subtype = (_text(a, A_MIME).lower() or 'application/octet-stream').partition('/')
        found.append((attachment, a, maintype, subtype, bool(html and _text(a, A_CID) and maintype == 'image')))
    return found


class PstFile:
    """An open .pst; use as a context manager."""
    def __init__(self, path):
        self.path = Path(path)
        try:
            self._handle = open(self.path, 'rb')
        except OSError:
            raise AppError('ARCHIVE_NOT_FOUND')
        try:
            self._file = _pypff().file()
            self._file.open_file_object(self._handle)  # A file object: libpff's own open mangles 'D:/…' paths.
        except AppError:
            self._handle.close()
            raise
        except Exception:
            self._handle.close()
            raise AppError('ARCHIVE_UNSUPPORTED')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        try:
            self._file.close()
        except Exception:
            pass
        self._handle.close()

    def messages(self):
        """(place, message or None) of every item, folders depth-first. A place is the
        folder indexes from the root, then the message index."""
        def walk(folder, place):
            for index in range(folder.number_of_sub_messages):
                try:
                    yield place + [index], folder.get_sub_message(index)
                except Exception:
                    yield place + [index], None
            for index in range(folder.number_of_sub_folders):
                yield from walk(folder.get_sub_folder(index), place + [index])
        yield from walk(self._file.root_folder, [])

    def count(self):
        """Items in all folders (quick: no item is read)."""
        def walk(folder):
            return folder.number_of_sub_messages + sum(walk(folder.get_sub_folder(i)) for i in range(folder.number_of_sub_folders))
        return walk(self._file.root_folder)

    def message(self, place):
        try:
            folder = self._file.root_folder
            for step in place[:-1]:
                folder = folder.get_sub_folder(step)
            return folder.get_sub_message(place[-1])
        except Exception:
            raise AppError('ARCHIVE_CHANGED')


def check(message, props=None):
    """What identifies a message at its place: node identifier and sent time."""
    when = _when(props if props is not None else _props(message))
    return f'{message.identifier}:{when.isoformat() if when else ""}'


def message_key(message, props):
    """Stable across rescans and moves of the file: Message-ID, else identity and time."""
    return hashlib.sha256(('pst|' + (_text(props, MESSAGE_ID) or check(message, props) + '|' + _text(props, SUBJECT))).encode('utf-8')).hexdigest()


def describe(message, props, mid):
    """The catalog row (as catalog.describe makes it), straight from the properties."""
    from coremail_desktop.ai.summary import _tidy, visible_text
    from coremail_desktop.archive import catalog
    kind = _text(props, CLASS).lower()
    if kind.startswith('ipm.note.smime.multipartsigned'):
        row = catalog.describe(to_eml(message, content=False), mid)  # Its text is inside the signed entity.
        row['size'] = _int(props, SIZE) or row['size']
        return row
    people, when = _people(message, props), _when(props)
    local = when.replace(tzinfo=timezone.utc).astimezone() if when else None  # Local time, as server rows show it.
    row = {'id': mid, 'subject': _text(props, SUBJECT), 'from': _shown([people['from']]), 'to': _shown(people[1]),
           'cc': _shown(people[2]), 'date': local.strftime('%Y-%m-%d %H:%M:%S') if local else '',
           'ts': local.timestamp() if local else 0.0, 'messageId': _text(props, MESSAGE_ID).strip('<> '),
           'size': _int(props, SIZE) or 0, 'encrypted': kind.startswith('ipm.note.smime'), 'attachments': [], 'text': '', 'preview': ''}
    if row['encrypted']:
        return row
    plain, html = _bodies(message, props)
    text = _tidy(plain or visible_text(html))
    names = [_text(a, A_LONG) or _text(a, A_SHORT) for _, a, _, _, inline in _attachments(message, html) if not inline]
    row.update(attachments=[n for n in names if n][:50], text=text[:catalog.TEXT_CHARS],
               preview=re.sub(r'\s+', ' ', text[:catalog.PREVIEW_CHARS * 2]).strip()[:catalog.PREVIEW_CHARS])
    return row


def _attachment_data(attachment):
    size = attachment.size or 0
    if size > MAX_ATTACHMENT:
        raise AppError('ARCHIVE_UNSUPPORTED')
    attachment.seek_offset(0)
    return attachment.read_buffer(size) if size else b''


def _headers(message, props, out):
    people = _people(message, props)
    out['Subject'] = _text(props, SUBJECT).replace('\r', ' ').replace('\n', ' ')
    if any(people['from']):
        out['From'] = _header_addr(*people['from'])
    for kind, name in ((1, 'To'), (2, 'Cc'), (3, 'Bcc')):
        shown = [h for h in (_header_addr(n, a) for n, a in people[kind]) if h]
        if shown:
            out[name] = ', '.join(shown)
    when = _when(props)
    if when:
        out['Date'] = format_datetime(when.replace(tzinfo=timezone.utc))
    for tag, name in ((MESSAGE_ID, 'Message-ID'), (IN_REPLY_TO, 'In-Reply-To'), (REFERENCES, 'References')):
        if _text(props, tag):
            out[name] = _text(props, tag)


def to_eml(message, content=True, depth=0):
    """The message as RFC 822 bytes. Without content, attachments are named but empty."""
    props = _props(message)
    kind = _text(props, CLASS).lower()
    head = EmailMessage(policy=policy.SMTP)
    _headers(message, props, head)
    attachments = list(getattr(message, 'attachments', ()))
    if kind.startswith('ipm.note.smime'):
        # The original S/MIME entity is the one attachment: an opaque p7m, or the whole
        # multipart/signed entity with its own headers (read for the catalog too: its text).
        signed = kind.endswith('multipartsigned')
        blob = _attachment_data(attachments[0]) if attachments and (content or signed) else b''
        if signed and blob:
            head['MIME-Version'] = '1.0'
            return head.as_bytes().rstrip(b'\r\n') + b'\r\n' + blob
        head.set_content(blob, 'application', 'pkcs7-mime', params={'smime-type': 'enveloped-data', 'name': 'smime.p7m'},
                         filename='smime.p7m')
        return head.as_bytes()
    plain, html = _bodies(message, props)
    if html:
        head.set_content(html, subtype='html', cte='base64')
    else:
        head.set_content(plain, cte='base64')
    # Inline images first: the body becomes multipart/related before it is wrapped in mixed.
    for attachment, a, maintype, subtype, inline in sorted(_attachments(message, html), key=lambda p: not p[4]):
        name, method = _text(a, A_LONG) or _text(a, A_SHORT), _int(a, A_METHOD)
        if inline:
            if content:  # Pictures in the body are not listed as attachments.
                head.add_related(_attachment_data(attachment), maintype, subtype,
                                 cid=f'<{_text(a, A_CID).strip("<>")}>', disposition='inline', filename=name or None)
            continue
        if method == EMBEDDED and depth < 3:
            try:
                inner = to_eml(attachment.get_sub_item(0), content, depth + 1) if content else b''
            except AppError:
                raise
            except Exception:
                inner = b''
            if inner:
                head.add_attachment(BytesParser(policy=policy.SMTP).parsebytes(inner), filename=name or 'message.eml')
            else:
                head.add_attachment(b'', 'application', 'octet-stream', filename=name or 'message.eml')
            continue
        data = _attachment_data(attachment) if content and method == BY_VALUE else b''
        if not subtype or maintype in ('multipart', 'message', 'text'):
            maintype, subtype = 'application', 'octet-stream'
        head.add_attachment(data, maintype, subtype, filename=name or 'attachment')
    return head.as_bytes()
