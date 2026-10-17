"""Bounded immutable attachment snapshots; no local path is persisted."""
import mimetypes,re
from pathlib import Path
from coremail_desktop.models import DraftAttachment
from coremail_desktop.errors import AppError

MAX_FILE_SIZE=10*1024*1024
MAX_TOTAL_SIZE=20*1024*1024
MAX_ATTACHMENTS=20

def validate_attachments(attachments):
    if len(attachments)>MAX_ATTACHMENTS: raise AppError('ATTACHMENT_TOO_LARGE')
    total=0
    for attachment in attachments:
        if not isinstance(attachment,DraftAttachment) or not isinstance(attachment.name,str) or not attachment.name or len(attachment.name)>255 or any(c in attachment.name for c in '\r\n\x00/\\"') or attachment.name in ('.','..'): raise AppError('INVALID_ATTACHMENT')
        if not isinstance(attachment.payload,bytes): raise AppError('INVALID_ATTACHMENT')
        if not isinstance(attachment.media_type,str) or not re.fullmatch(r'[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+',attachment.media_type): raise AppError('INVALID_ATTACHMENT')
        if len(attachment.payload)>MAX_FILE_SIZE: raise AppError('ATTACHMENT_TOO_LARGE')
        total+=len(attachment.payload)
    if total>MAX_TOTAL_SIZE: raise AppError('ATTACHMENT_TOO_LARGE')

def read_attachment(path):
    path=Path(path)
    try:
        with path.open('rb') as stream: payload=stream.read(MAX_FILE_SIZE+1)
    except OSError: raise AppError('ATTACHMENT_UNAVAILABLE') from None
    attachment=DraftAttachment(path.name,mimetypes.guess_type(path.name)[0] or 'application/octet-stream',payload)
    validate_attachments((attachment,))
    return attachment
