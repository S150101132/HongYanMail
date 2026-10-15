"""MIME is parsed on the session worker, never inside the reader widget."""
import hashlib
import re
from email import policy
from email.parser import BytesParser
from coremail_desktop.models import AttachmentInfo, MessageDocument
from .sanitizer import sanitize_html, html_to_text, normalize_cid

MAX_RAW_BYTES = 25 * 1024 * 1024
MAX_DECODED_BYTES = 64 * 1024 * 1024
MAX_DEPTH = 30
IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}


def parse_mime(raw: bytes, message_id: str) -> MessageDocument:
    # Accept the documented keyword names; remote identity never comes from Message-ID.
    digest = hashlib.sha256(raw).hexdigest()
    def result(text, html=None, attachments=(), headers=None, resources=None, state='none'):
        return MessageDocument(message_id, digest, text, html, tuple(attachments), headers or {},
                               cid_resources=resources or {}, encryption_state=state)
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
        attachments, resources, payloads = [], {}, {}
        stack, total = [(message, '1', 0, False)], 0
        while stack:
            part, part_id, depth, hidden = stack.pop()
            if depth > MAX_DEPTH:
                return result('邮件 MIME 层级超出限制，未加载正文。', headers=headers)
            filename = part.get_filename()
            attached = part.get_content_disposition() == 'attachment' or bool(filename) or part.get_content_type() == 'message/rfc822'
            payload = part.get_payload(decode=True)
            if payload is not None:
                total += len(payload)
                if total > MAX_DECODED_BYTES:
                    return result('邮件解码内容超出大小限制，未加载正文。', headers=headers)
                payloads[id(part)] = payload
            if attached and not hidden:
                name = re.sub(r'[\x00-\x1f\x7f]', '', (filename or '未命名附件').replace('\\', '/').split('/')[-1])[:255]
                size = len(payload) if payload is not None else None
                attachments.append(AttachmentInfo(part_id, name or '未命名附件', part.get_content_type(), size))
            if not hidden and part.get_content_disposition() != 'attachment' and payload is not None and part.get_content_type() in IMAGE_TYPES:
                cid = normalize_cid(str(part.get('Content-ID', '')))
                if cid and cid not in resources:
                    resources[cid] = (part.get_content_type(), payload)
            if part.is_multipart():
                children = part.get_payload()
                for index in range(len(children) - 1, -1, -1):
                    stack.append((children[index], f'{part_id}.{index+1}', depth+1, hidden or attached))

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
            return (decoded, None) if part.get_content_type() == 'text/plain' else ('', sanitize_html(decoded, resources))
        text, html = body(message)
        return result(text or (html_to_text(html) if html else '此邮件没有可显示的正文。'), html, attachments, headers, resources)
    except (RecursionError, ValueError, TypeError):
        return result('邮件格式损坏或层级超出限制，未加载正文。')
