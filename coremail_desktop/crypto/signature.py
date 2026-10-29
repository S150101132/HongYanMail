"""S/MIME signatures: CMS SignedData creation and verification.

Signing uses pyca's PKCS7SignatureBuilder (SHA-256 with RSA PKCS#1 v1.5, one of
the SignMethods the official client lists). pyca cannot verify CMS, so
verification parses SignedData with the DER reader in asn1.py and checks it with
pyca primitives only: messageDigest against the content, then the signature over
the DER SET of signed attributes (RFC 5652 section 5.4).

There is no CA store, so a valid signature proves integrity and possession of the
certificate's key; the issuer is reported, not trusted.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID

from . import asn1
from .cms import CONTEXT_0, SIGNED_DATA, _unwrap_explicit, looks_like_signed_data
from .models import CryptoError

MAX_SIGNED = 64 * 1024 * 1024
SIGNED_MIME_TYPES = ('application/pkcs7-mime', 'application/x-pkcs7-mime')
SIGNATURE_MIME_TYPES = ('application/pkcs7-signature', 'application/x-pkcs7-signature')

DIGESTS = {'1.3.14.3.2.26': hashes.SHA1, '2.16.840.1.101.3.4.2.4': hashes.SHA224,
           '2.16.840.1.101.3.4.2.1': hashes.SHA256, '2.16.840.1.101.3.4.2.2': hashes.SHA384,
           '2.16.840.1.101.3.4.2.3': hashes.SHA512,
           # Some clients put the RSA signature OID in digestAlgorithm (seen: sha512WithRSAEncryption).
           '1.2.840.113549.1.1.5': hashes.SHA1, '1.2.840.113549.1.1.14': hashes.SHA224,
           '1.2.840.113549.1.1.11': hashes.SHA256, '1.2.840.113549.1.1.12': hashes.SHA384,
           '1.2.840.113549.1.1.13': hashes.SHA512}
RSA_SIGNATURES = {'1.2.840.113549.1.1.1', '1.2.840.113549.1.1.5', '1.2.840.113549.1.1.14',
                  '1.2.840.113549.1.1.11', '1.2.840.113549.1.1.12', '1.2.840.113549.1.1.13'}
ECDSA_SIGNATURES = {'1.2.840.10045.2.1', '1.2.840.10045.4.1', '1.2.840.10045.4.3.1', '1.2.840.10045.4.3.2',
                    '1.2.840.10045.4.3.3', '1.2.840.10045.4.3.4'}
CONTENT_TYPE = '1.2.840.113549.1.9.3'
MESSAGE_DIGEST = '1.2.840.113549.1.9.4'
SIGNING_TIME = '1.2.840.113549.1.9.5'
UTC_TIME, GENERALIZED_TIME = 0x17, 0x18


@dataclass(frozen=True)
class SignatureReport:
    """status: 'valid' (signature checks out over this content), 'invalid'
    (content or signature altered) or 'unverifiable' (code says why)."""
    status: str
    code: str = ''
    issuer: str = ''
    subject: str = ''
    emails: tuple[str, ...] = ()
    email_match: bool | None = None
    time_valid: bool | None = None
    signed_at: datetime | None = None
    digest: str = ''
    fingerprint: str = ''
    certificate_der: bytes = field(default=b'', repr=False)

    def to_dict(self):
        return {'status': self.status, 'code': self.code, 'issuer': self.issuer, 'subject': self.subject,
                'emails': list(self.emails), 'emailMatch': self.email_match, 'timeValid': self.time_valid,
                'signedAt': self.signed_at.isoformat() if self.signed_at else None, 'digest': self.digest}


# ---------------------------------------------------------------- signing
def _signature_builder(data, cert, key, chain=()):
    if not isinstance(key, rsa.RSAPrivateKey):
        raise CryptoError('CERT_ALGORITHM_UNSUPPORTED')
    builder = pkcs7.PKCS7SignatureBuilder().set_data(data).add_signer(
        cert, key, hashes.SHA256(), rsa_padding=padding.PKCS1v15())
    for extra in chain:
        if extra != cert:
            builder = builder.add_certificate(extra)
    return builder


def sign_opaque(data: bytes, cert, key, chain=()) -> bytes:
    """SignedData DER with the content encapsulated (what the official client
    puts inside its EnvelopedData)."""
    if len(data) > MAX_SIGNED:
        raise CryptoError('MESSAGE_TOO_LARGE')
    try:
        return _signature_builder(data, cert, key, chain).sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary])
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise CryptoError('SIGN_FAILED') from None


def sign_detached(data: bytes, cert, key, chain=()) -> bytes:
    """SignedData DER without content, for application/pkcs7-signature."""
    if len(data) > MAX_SIGNED:
        raise CryptoError('MESSAGE_TOO_LARGE')
    try:
        return _signature_builder(data, cert, key, chain).sign(
            serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary, pkcs7.PKCS7Options.DetachedSignature])
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise CryptoError('SIGN_FAILED') from None


def can_sign(cert) -> bool:
    try:
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        return True
    return usage.digital_signature or usage.content_commitment


# ---------------------------------------------------------------- verification
def _raw_children(body: bytes):
    """Like asn1.children, but also yields each element's full encoding."""
    offset = 0
    while offset < len(body):
        tag, start, length, nxt = asn1.read_tlv(body, offset)
        yield tag, body[start:start + length], body[offset:nxt]
        offset = nxt


def _parse_time(tag, body):
    text = body.decode('ascii').rstrip('Z')
    if tag == UTC_TIME:
        year = int(text[:2])
        text = ('19' if year >= 50 else '20') + text
    return datetime.strptime(text[:14], '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)


def _signed_parts(der: bytes):
    """Return (encapsulated content or None, [certificate DER], [signerInfo body])."""
    tag, content_info, _ = asn1.element(der, 0)
    fields = list(asn1.children(content_info))
    if tag != asn1.SEQUENCE or len(fields) < 2 or fields[0][0] != asn1.OID \
            or asn1.decode_oid(fields[0][1]) != SIGNED_DATA or fields[1][0] != CONTEXT_0:
        raise CryptoError('INVALID_SIGNED_MESSAGE')
    signed = list(_raw_children(_unwrap_explicit(fields[1][1])))
    if len(signed) < 4 or signed[2][0] != asn1.SEQUENCE or signed[-1][0] != asn1.SET:
        raise CryptoError('INVALID_SIGNED_MESSAGE')
    content = None
    for tag, chunk in list(asn1.children(signed[2][1]))[1:]:
        if tag == asn1.OCTET_STRING:
            content = chunk
        elif tag == CONTEXT_0:
            # [0] EXPLICIT OCTET STRING, possibly constructed (BER-style chunks).
            content = b''.join(inner for inner_tag, inner in asn1.children(chunk))
    certificates = []
    for tag, chunk, raw in signed[3:-1]:
        if tag == CONTEXT_0:
            certificates.extend(cert_raw for cert_tag, _, cert_raw in _raw_children(chunk) if cert_tag == asn1.SEQUENCE)
    signers = [chunk for tag, chunk in asn1.children(signed[-1][1]) if tag == asn1.SEQUENCE]
    if not signers:
        raise CryptoError('INVALID_SIGNED_MESSAGE')
    return content, certificates, signers


def _find_certificate(sid_tag, sid_body, certificates):
    loaded = []
    for der in certificates:
        try:
            loaded.append(x509.load_der_x509_certificate(der))
        except ValueError:
            continue
    if sid_tag == asn1.SEQUENCE:
        issuer_and_serial = list(_raw_children(sid_body))
        if len(issuer_and_serial) != 2:
            return None
        serial = int.from_bytes(issuer_and_serial[1][1], 'big', signed=True)
        by_serial = [cert for cert in loaded if cert.serial_number == serial]
        exact = [cert for cert in by_serial if cert.issuer.public_bytes() == issuer_and_serial[0][2]]
        # Re-encoding a name can change string types; a unique serial still identifies it.
        return exact[0] if exact else (by_serial[0] if len(by_serial) == 1 else None)
    for cert in loaded:
        if sid_tag in (0x80, CONTEXT_0):
            try:
                identifier = cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest
            except x509.ExtensionNotFound:
                continue
            if identifier == sid_body:
                return cert
    return None


def _issuer_name(cert):
    names = cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME) or \
        cert.issuer.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
    return str(names[0].value) if names else cert.issuer.rfc4514_string()


def _cert_emails(cert):
    addresses = {value.value.casefold() for value in cert.subject.get_attributes_for_oid(NameOID.EMAIL_ADDRESS)}
    try:
        addresses.update(value.casefold() for value in cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value.get_values_for_type(x509.RFC822Name))
    except x509.ExtensionNotFound:
        pass
    return tuple(sorted(addresses))


def _verify_signer(info: bytes, content: bytes, certificates, sender: str, now):
    parts = list(_raw_children(info))
    if len(parts) < 5:
        raise CryptoError('INVALID_SIGNED_MESSAGE')
    sid_tag, sid_body, _ = parts[1]
    digest_oid = asn1.decode_oid(asn1.expect(parts[2][1], asn1.OID))
    index = 3
    signed_attrs = None
    if parts[index][0] == CONTEXT_0:
        signed_attrs = parts[index]
        index += 1
    signature_oid = asn1.decode_oid(asn1.expect(parts[index][1], asn1.OID))
    if parts[index + 1][0] != asn1.OCTET_STRING:
        raise CryptoError('INVALID_SIGNED_MESSAGE')
    signature = parts[index + 1][1]

    cert = _find_certificate(sid_tag, sid_body, certificates)
    if cert is None:
        return SignatureReport('unverifiable', 'SIGNER_CERT_MISSING')
    emails = _cert_emails(cert)
    base = dict(issuer=_issuer_name(cert), subject=cert.subject.rfc4514_string(), emails=emails,
                email_match=(sender.casefold() in emails) if sender else None,
                fingerprint=cert.fingerprint(hashes.SHA256()).hex(), certificate_der=cert.public_bytes(serialization.Encoding.DER))
    if digest_oid not in DIGESTS:
        return SignatureReport('unverifiable', 'SIGNATURE_ALGORITHM_UNSUPPORTED', **base)
    algorithm = DIGESTS[digest_oid]()
    base['digest'] = algorithm.name

    signed_at = None
    if signed_attrs is not None:
        attributes = {}
        for _, attribute in asn1.children(signed_attrs[1]):
            items = list(asn1.children(attribute))
            if len(items) == 2 and items[0][0] == asn1.OID and items[1][0] == asn1.SET:
                values = list(asn1.children(items[1][1]))
                if values:
                    attributes[asn1.decode_oid(items[0][1])] = values[0]
        if MESSAGE_DIGEST not in attributes:
            raise CryptoError('INVALID_SIGNED_MESSAGE')
        digest = hashes.Hash(algorithm)
        digest.update(content)
        if attributes[MESSAGE_DIGEST][1] != digest.finalize():
            return SignatureReport('invalid', 'CONTENT_DIGEST_MISMATCH', **base)
        if SIGNING_TIME in attributes and attributes[SIGNING_TIME][0] in (UTC_TIME, GENERALIZED_TIME):
            try:
                signed_at = _parse_time(*attributes[SIGNING_TIME])
            except ValueError:
                signed_at = None
        # The signature covers the attributes re-encoded as a universal SET.
        signed_data = b'\x31' + signed_attrs[2][1:]
    else:
        signed_data = content

    key = cert.public_key()
    try:
        if signature_oid in RSA_SIGNATURES and isinstance(key, rsa.RSAPublicKey):
            key.verify(signature, signed_data, padding.PKCS1v15(), algorithm)
        elif signature_oid in ECDSA_SIGNATURES and isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(signature, signed_data, ec.ECDSA(algorithm))
        else:
            # SM2, RSA-PSS and other methods: reported, never guessed.
            return SignatureReport('unverifiable', 'SIGNATURE_ALGORITHM_UNSUPPORTED', **base)
    except InvalidSignature:
        return SignatureReport('invalid', 'SIGNATURE_MISMATCH', **base)
    except (ValueError, UnsupportedAlgorithm):
        return SignatureReport('unverifiable', 'SIGNATURE_ALGORITHM_UNSUPPORTED', **base)
    moment = signed_at if signed_at and signed_at <= now else now
    valid_time = cert.not_valid_before_utc <= moment <= cert.not_valid_after_utc
    return SignatureReport('valid', '', time_valid=valid_time, signed_at=signed_at, **base)


def verify_signed_data(der: bytes, content: bytes | None = None, sender: str = '', now=None):
    """Verify a SignedData ContentInfo. Returns (report, content).

    content: the detached content for a multipart/signed message; None means the
    content is encapsulated. A SignedData with several signers is valid only if
    every signer verifies.
    """
    now = now or datetime.now(timezone.utc)
    try:
        encapsulated, certificates, signers = _signed_parts(der)
        if content is None:
            if encapsulated is None:
                return SignatureReport('unverifiable', 'SIGNED_CONTENT_MISSING'), b''
            content = encapsulated
        reports = [_verify_signer(info, content, certificates, sender, now) for info in signers]
    except CryptoError as error:
        return SignatureReport('unverifiable', error.code), content or b''
    except (asn1.DerError, IndexError, ValueError, UnicodeDecodeError):
        return SignatureReport('unverifiable', 'INVALID_SIGNED_MESSAGE'), content or b''
    for status in ('invalid', 'unverifiable'):
        for report in reports:
            if report.status == status:
                return report, content
    return reports[0], content


def _crlf(data: bytes) -> bytes:
    return data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')


def _signed_part_bytes(raw: bytes, boundary: str):
    from coremail_desktop.mime.parser import _raw_children as mime_children
    children = mime_children(raw, boundary)
    return children[0] if len(children) >= 2 else None


def unwrap_signed(raw: bytes, sender: str = ''):
    """Peel one S/MIME signature layer off a MIME entity.

    Returns (inner MIME bytes, SignatureReport) for multipart/signed and opaque
    signed-data, or None when the entity is not signed. An invalid signature still
    returns the content, so the reader can show it under a warning.
    """
    if len(raw) > MAX_SIGNED:
        raise CryptoError('MESSAGE_TOO_LARGE')
    message = BytesParser(policy=policy.default).parsebytes(raw)
    kind = message.get_content_type()
    if kind == 'multipart/signed':
        protocol = str(message.get_param('protocol', '')).lower()
        parts = message.get_payload()
        if protocol not in SIGNATURE_MIME_TYPES or not isinstance(parts, list) or len(parts) != 2:
            return None
        content = _signed_part_bytes(raw, message.get_boundary())
        signature = parts[1].get_payload(decode=True)
        if content is None or not signature:
            return content or b'', SignatureReport('unverifiable', 'INVALID_SIGNED_MESSAGE')
        report, _ = verify_signed_data(signature, content, sender)
        if report.status == 'invalid' and _crlf(content) != content:
            # Canonical S/MIME content is CRLF; some stores hand back bare LF.
            retry, _ = verify_signed_data(signature, _crlf(content), sender)
            if retry.status == 'valid':
                report = retry
        return content, report
    if kind in SIGNED_MIME_TYPES:
        smime_type = str(message.get_param('smime-type', '')).lower()
        der = message.get_payload(decode=True)
        if smime_type not in ('signed-data', '') or not der or not looks_like_signed_data(der):
            return None
        report, content = verify_signed_data(der, None, sender)
        return content, report
    return None
