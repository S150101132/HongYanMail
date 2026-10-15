from email import policy
from email.parser import BytesParser
from .backend import MAX_ENVELOPE
from .models import CryptoError


def detect_envelope_format(raw: bytes) -> str | None:
    if len(raw) > MAX_ENVELOPE:
        raise CryptoError('MESSAGE_TOO_LARGE')
    message = BytesParser(policy=policy.default).parsebytes(raw)
    kind = message.get_content_type()
    if kind in {'application/pkcs7-mime', 'application/x-pkcs7-mime'}:
        return ('smime-enveloped-data' if message.get_param('smime-type', '').lower() == 'enveloped-data'
                else 'unsupported-crypto')
    if kind == 'multipart/encrypted':
        return 'unsupported-crypto'
    return None
