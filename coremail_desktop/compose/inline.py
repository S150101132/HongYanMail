"""Body images (写信插入图片).

The composer keeps a pasted or inserted picture in the HTML as a data: URI, so
local drafts, templates and the undo-send copy hold it as it is shown. Sending
turns each one into its own MIME part: the S/MIME builder writes cid: references
into a multipart/related body; the normal path uploads the image with inlined=1
and points the body at webmail's mbox:getComposeData URL, which the server
rewrites into cid: (verified 2026-10-18, analysis/compose-v05-shape-2026-10-18.json).
"""
import base64
import hashlib
import re
from coremail_desktop.errors import AppError
from coremail_desktop.models import DraftAttachment

INLINE_TYPES = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp', 'image/bmp': 'bmp'}
_DATA_IMG = re.compile(r'''(<img\b[^>]*?\bsrc\s*=\s*)(["'])data:([A-Za-z/+.-]+);base64,([^"']*)\2''', re.I)
_TOKEN = re.compile(r'''(?<=["'])hy-inline:(\d+)(?=["'])''')


def extract_inline(html):
    """-> (html with each data: image as 'hy-inline:N', [DraftAttachment]). The same
    picture pasted twice becomes one part. Other data: URIs are dropped."""
    images, seen = [], {}

    def take(match):
        kind = match.group(3).lower()
        if kind == 'image/jpg':
            kind = 'image/jpeg'
        if kind not in INLINE_TYPES:
            return match.group(1) + match.group(2) + match.group(2)
        try:
            payload = base64.b64decode(re.sub(r'\s', '', match.group(4)), validate=True)
        except ValueError:
            raise AppError('INVALID_ATTACHMENT') from None
        if not payload:
            raise AppError('INVALID_ATTACHMENT')
        key = hashlib.sha256(payload).hexdigest()
        if key not in seen:
            seen[key] = len(images)
            images.append(DraftAttachment(f'image{len(images) + 1}.{INLINE_TYPES[kind]}', kind, payload))
        return f'{match.group(1)}{match.group(2)}hy-inline:{seen[key]}{match.group(2)}'
    return _DATA_IMG.sub(take, html or ''), images


def fill_inline(template, sources):
    """Put a real src (cid:..., or the compose-data URL) where extract_inline left tokens."""
    return _TOKEN.sub(lambda m: sources[int(m.group(1))], template)


def has_inline(html):
    return bool(_DATA_IMG.search(html or ''))
