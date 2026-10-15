from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class CertificateInfo:
    fingerprint: str
    subject: str
    emails: tuple[str, ...]
    not_before: datetime
    not_after: datetime
    can_encrypt: bool
    trust_status: str
    certificate_der: bytes = field(repr=False)


@dataclass(frozen=True)
class DecryptionResult:
    mime_bytes: bytes = field(repr=False)
    format: str = 'smime-enveloped-data'
    signature_status: str = 'unsigned'
    trust_status: str = 'not_verified'


class CryptoError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
