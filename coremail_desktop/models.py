"""Shared immutable values; no network, Qt or storage ownership."""
from dataclasses import dataclass, field
from types import MappingProxyType

class _ImmutableMappings:
    def __post_init__(self):
        for name in ('to','cc','bcc','attachments'):
            if hasattr(self,name): object.__setattr__(self,name,tuple(getattr(self,name)))
        for name in ('counts', 'flags', 'headers', 'reference_headers', 'cid_resources', 'attachment_payloads'):
            if hasattr(self, name):
                value = dict(getattr(self,name))
                if name == 'headers': value = {key:tuple(items) for key,items in value.items()}
                object.__setattr__(self, name, MappingProxyType(value))

@dataclass(frozen=True)
class AccountConfig:
    local_id: str
    origin: str
    login_uid: str

@dataclass(frozen=True)
class Folder(_ImmutableMappings):
    remote_id: str
    name: str
    role: str | None
    counts: dict = field(default_factory=dict)

@dataclass(frozen=True)
class MessageSummary(_ImmutableMappings):
    remote_id: str
    folder_id: str
    subject: str
    sender: str
    date_text: str
    flags: dict = field(default_factory=dict)
    preview: str = ''
    recipients: str = ''
    size: int | None = None

@dataclass(frozen=True)
class AttachmentInfo:
    part_id: str
    name: str
    media_type: str
    size: int | None

@dataclass(frozen=True)
class MessageDocument(_ImmutableMappings):
    remote_id: str
    raw_digest: str
    text: str
    html: str | None
    attachments: tuple[AttachmentInfo, ...]
    headers: dict[str, tuple[str, ...]]
    cid_resources: dict = field(default_factory=dict)
    encryption_state: str = 'none'
    attachment_payloads: dict[str, bytes] = field(default_factory=dict)

@dataclass(frozen=True)
class DraftAttachment:
    name: str
    media_type: str
    payload: bytes

@dataclass(frozen=True)
class DraftDocument(_ImmutableMappings):
    local_id: str
    revision: int
    mode: str
    from_identity: str
    to: tuple[str, ...]
    cc: tuple[str, ...]
    bcc: tuple[str, ...]
    subject: str
    body: str
    reference_headers: dict[str, str]
    encryption_required: bool = False
    reply_source_id: str | None = None
    attachments: tuple[DraftAttachment, ...] = ()
    is_html: bool = False

@dataclass(frozen=True)
class SendJob:
    local_id: str
    draft: DraftDocument
    compose_id: str | None = None
    state: str = 'Preparing'
    submitted_at: str | None = None
    payload: bytes | None = None
    payload_digest: str | None = None
    envelope_recipients: tuple[str, ...] = ()
    encrypted: bool = False
    failure_code: str | None = None
