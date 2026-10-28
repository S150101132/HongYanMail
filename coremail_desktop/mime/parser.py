"""MIME is parsed on the session worker, never inside the reader widget."""
import hashlib
import os
import re
import base64
import quopri
from email import policy
from email.parser import BytesParser
from coremail_desktop.models import AttachmentInfo, MessageDocument
from .sanitizer import sanitize_html, sanitize_rich_html, html_to_text, normalize_cid

# 20 MiB of legal attachments expands to about 28 MiB with MIME base64.
MAX_RAW_BYTES = 40 * 1024 * 1024
MAX_DECODED_BYTES = 64 * 1024 * 1024
MAX_DEPTH = 30
IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}
# Some senders lose a file's name when quoting (reply_..._att4.blob): such names get the
# extension the content itself shows, so the icon, preview and 打开 all work.
GENERIC_EXTENSIONS = {'', '.blob', '.bin', '.dat', '.tmp', '.octet-stream'}
CONTENT_MAGIC = ((b'\x89PNG\r\n\x1a\n', '.png', 'image/png'), (b'\xff\xd8\xff', '.jpg', 'image/jpeg'),
                 (b'GIF87a', '.gif', 'image/gif'), (b'GIF89a', '.gif', 'image/gif'), (b'%PDF-', '.pdf', 'application/pdf'))


def _true_name(name, media_type, payload):
    ext = os.path.splitext(name)[1]
    if ext.lower() not in GENERIC_EXTENSIONS or not payload: return name, media_type
    stem, head = name[:len(name) - len(ext)], bytes(payload[:16])
    found = next(((e, t) for magic, e, t in CONTENT_MAGIC if head.startswith(magic)), None)
    if found is None and head[:4] == b'RIFF' and head[8:12] == b'WEBP': found = ('.webp', 'image/webp')
    return (stem + found[0], found[1]) if found else (name, media_type)


def _raw_body(raw):
    if raw.startswith(b'\r\n'): return raw[2:]
    if raw.startswith((b'\n',b'\r')): return raw[1:]
    separator=re.search(br'\r\n\r\n|\n\n|\r\r',raw)
    return raw[separator.end():] if separator else b''


def _raw_children(raw, boundary):
    """Keep original octets; the CRLF introducing a boundary is not payload."""
    if raw is None or not boundary: return []
    try: delimiter=boundary.encode('ascii')
    except UnicodeEncodeError: return []
    body=_raw_body(raw)
    markers=list(re.finditer(br'(?m)^--'+re.escape(delimiter)+br'(--)?[ \t]*(?:\r?\n|$)',body))
    children=[]
    for current,following in zip(markers,markers[1:]):
        if current.group(1): break
        chunk=body[current.end():following.start()]
        if chunk.endswith(b'\r\n'): chunk=chunk[:-2]
        elif chunk.endswith(b'\n'): chunk=chunk[:-1]
        children.append(chunk)
    return children


def parse_mime(raw: bytes, message_id: str, rich: bool = False) -> MessageDocument:
    # Accept the documented keyword names; remote identity never comes from Message-ID.
    digest = hashlib.sha256(raw).hexdigest()
    def result(text, html=None, attachments=(), headers=None, resources=None, state='none', attachment_payloads=None):
        return MessageDocument(message_id, digest, text, html, tuple(attachments), headers or {},
                               cid_resources=resources or {}, encryption_state=state, attachment_payloads=attachment_payloads or {})
    if len(raw) > MAX_RAW_BYTES:
        return result('邮件原始内容超出大小限制，未加载正文。')
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        headers = {key.lower(): tuple(str(v) for v in message.get_all(key, []))
                   for key in set(message.keys())}
        encrypted_types = {'application/pkcs7-mime', 'application/x-pkcs7-mime', 'multipart/encrypted'}
        if message.get_content_type() in encrypted_types:
            recognized = message.get_content_type() != 'multipart/encrypted' and message.get_param('smime-type', '').lower() == 'enveloped-data'
            return result('加密邮件已锁定，请解锁证书后解密。' if recognized else '加密或签名封装格式尚不支持，未加载正文。',
                          headers=headers, state='locked' if recognized else 'unsupported')
        attachments, resources, payloads, attachment_payloads = [], {}, {}, {}
        inline_cids = {}  # attachment part id -> Content-ID, for images the body may show inline
        stack, total = [(message, '1', 0, False, raw)], 0
        while stack:
            part, part_id, depth, hidden, original = stack.pop()
            if depth > MAX_DEPTH:
                return result('邮件 MIME 层级超出限制，未加载正文。', headers=headers)
            filename = part.get_filename()
            attached = part.get_content_disposition() == 'attachment' or bool(filename) or part.get_content_type() == 'message/rfc822'
            payload = part.get_payload(decode=True)
            if attached and not hidden and part.get_content_type() == 'message/rfc822':
                # Serializing the parsed child folds headers and normalizes EOLs.
                # Save the original transfer-decoded body instead.
                if original is not None:
                    payload=_raw_body(original)
                    encoding=str(part.get('Content-Transfer-Encoding','')).lower().strip()
                    if encoding=='base64': payload=base64.b64decode(payload)
                    elif encoding=='quoted-printable': payload=quopri.decodestring(payload)
            if payload is not None:
                total += len(payload)
                if total > MAX_DECODED_BYTES:
                    return result('邮件解码内容超出大小限制，未加载正文。', headers=headers)
                payloads[id(part)] = payload
            if attached and not hidden:
                name = re.sub(r'[\x00-\x1f\x7f]', '', (filename or '未命名附件').replace('\\', '/').split('/')[-1])[:255]
                size = len(payload) if payload is not None else None
                name, media_type = _true_name(name or '未命名附件', part.get_content_type(), payload)
                attachments.append(AttachmentInfo(part_id, name, media_type, size))
                if payload is not None: attachment_payloads[part_id] = payload
                cid = normalize_cid(str(part.get('Content-ID', '')))
                if cid and part.get_content_type() in IMAGE_TYPES and part.get_content_disposition() != 'attachment':
                    inline_cids[part_id] = cid
            if not hidden and part.get_content_disposition() != 'attachment' and payload is not None and part.get_content_type() in IMAGE_TYPES:
                cid = normalize_cid(str(part.get('Content-ID', '')))
                if cid and cid not in resources:
                    resources[cid] = (part.get_content_type(), payload)
            if part.is_multipart():
                children = part.get_payload()
                originals=_raw_children(original,part.get_boundary()) if part.get_content_type()!='message/rfc822' else []
                for index in range(len(children) - 1, -1, -1):
                    child_raw=originals[index] if len(originals)==len(children) else None
                    stack.append((children[index], f'{part_id}.{index+1}', depth+1, hidden or attached, child_raw))

        def body(part):
            if part.get_content_disposition() == 'attachment' or part.get_filename() or part.get_content_type() == 'message/rfc822':
                return '', None
            if part.is_multipart():
                children = part.get_payload()
                # A mixed message has one body root. Other parts are attachments/resources.
                if part.get_content_subtype() != 'alternative':
                    return body(children[0]) if children else ('', None)
                text, html = '', None
                for child in children:
                    child_text, child_html = body(child)
                    text = child_text or text
                    html = child_html or html
                return text, html
            if part.get_content_type() not in {'text/plain', 'text/html'}:
                return '', None
            payload = payloads.get(id(part), b'')
            charset = part.get_content_charset() or 'utf-8'
            try:
                decoded = payload.decode(charset, errors='replace')
            except LookupError:
                decoded = payload.decode('utf-8', errors='replace')
            return (decoded, None) if part.get_content_type() == 'text/plain' else ('', (sanitize_rich_html if rich else sanitize_html)(decoded, resources))
        text, html = body(message)
        if html and inline_cids:
            # Signature logos and pasted pictures (Outlook's image001.png...) are part of
            # the body when the HTML shows them via cid:, not files the user attached.
            # Images the body does not reference stay listed, so they can still be saved.
            shown = {normalize_cid(ref) for ref in re.findall(r'''cid:([^"'\s>)]+)''', html)}
            attachments = [a for a in attachments if inline_cids.get(a.part_id) not in shown]
        return result(text or (html_to_text(html) if html else '此邮件没有可显示的正文。'), html, attachments, headers, resources, attachment_payloads=attachment_payloads)
    except (RecursionError, ValueError, TypeError):
        return result('邮件格式损坏或层级超出限制，未加载正文。')
