"""Supported subset: RSA S/MIME EnvelopedData using pyca/cryptography.

Enterprise formats and opaque signatures are not silently treated as plaintext.
No subprocess receives a private key, and nothing writes plaintext key material.
"""
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from .models import CertificateInfo, CryptoError

MAX_ENVELOPE = 25 * 1024 * 1024
MAX_PLAINTEXT = 64 * 1024 * 1024


def describe(cert: x509.Certificate, trust_status='untrusted') -> CertificateInfo:
    addresses = {value.value.casefold() for value in cert.subject.get_attributes_for_oid(NameOID.EMAIL_ADDRESS)}
    try:
        addresses.update(value.casefold() for value in cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value.get_values_for_type(x509.RFC822Name))
    except x509.ExtensionNotFound:
        pass
    permitted = isinstance(cert.public_key(), rsa.RSAPublicKey)
    try:
        permitted = permitted and cert.extensions.get_extension_for_class(x509.KeyUsage).value.key_encipherment
    except x509.ExtensionNotFound:
        pass
    try:
        permitted = permitted and ExtendedKeyUsageOID.EMAIL_PROTECTION in cert.extensions.get_extension_for_class(
            x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound:
        pass
    now = datetime.now(timezone.utc)
    permitted = permitted and cert.not_valid_before_utc <= now <= cert.not_valid_after_utc
    return CertificateInfo(cert.fingerprint(hashes.SHA256()).hex(), cert.subject.rfc4514_string(),
                           tuple(sorted(addresses)), cert.not_valid_before_utc, cert.not_valid_after_utc,
                           permitted, trust_status, cert.public_bytes(serialization.Encoding.DER))


def validate_recipient(info: CertificateInfo):
    now = datetime.now(timezone.utc)
    if not info.not_before <= now <= info.not_after:
        raise CryptoError('RECIPIENT_CERT_EXPIRED')
    fresh = describe(x509.load_der_x509_certificate(info.certificate_der), info.trust_status)
    if not fresh.can_encrypt:
        raise CryptoError('RECIPIENT_CERT_USAGE')
    # 'directory': fetched from the company server over the authenticated
    # same-origin session (cert:listPublicKey) and matched to the address.
    if info.trust_status not in {'explicit_pin', 'own_key', 'directory'}:
        raise CryptoError('RECIPIENT_CERT_UNTRUSTED')


def decrypt_smime(data: bytes, cert, private_key) -> bytes:
    """Decrypt an S/MIME enveloped message, tolerating provider variations.

    pyca's high-level pkcs7 decrypt accepts only AES-CBC, but this deployment
    encrypts with 3DES-CBC and the supplier build wraps content in an explicit
    context tag, so the same primitives are driven directly as a fallback. Both
    paths report unsupported algorithms distinctly instead of as a decryption
    failure.
    """
    if len(data) > MAX_ENVELOPE:
        raise CryptoError('MESSAGE_TOO_LARGE')
    message = BytesParser(policy=policy.default).parsebytes(data)
    kind = message.get_content_type().lower()
    if kind not in {'application/pkcs7-mime', 'application/x-pkcs7-mime'}:
        raise CryptoError('UNSUPPORTED_ENCRYPTION_FORMAT')
    smime_type = message.get_param('smime-type', '').lower()
    if smime_type != 'enveloped-data':
        raise CryptoError('UNSUPPORTED_ENCRYPTION_FORMAT')
    envelope = message.get_payload(decode=True)
    if not isinstance(envelope, bytes) or not envelope:
        raise CryptoError('INVALID_ENCRYPTED_MESSAGE')

    plaintext = None
    try:
        plaintext = pkcs7.pkcs7_decrypt_der(envelope, cert, private_key, [])
    except UnsupportedAlgorithm:
        # The algorithm is not one the high-level helper offers. The direct CMS
        # path can still handle it (notably 3DES-CBC).
        pass
    except (ValueError, TypeError):
        # pyca reports a wrong key and several provider variants with the same
        # exception. The envelope's declared algorithm decides the interpretation:
        # a wrong-key failure on a supported algorithm must stay a decrypt
        # failure, so that "the private key does not match" is never masked.
        from . import cms
        if cms.declared_content_algorithm(envelope) in cms.SUPPORTED_CONTENT_ALGORITHMS:
            raise CryptoError('DECRYPT_FAILED') from None
        pass
    except NotImplementedError:
        pass
    if plaintext is None:
        plaintext = _decrypt_with_cms(envelope, private_key)
    if len(plaintext) > MAX_PLAINTEXT:
        raise CryptoError('MESSAGE_TOO_LARGE')
    return plaintext


def _decrypt_with_cms(envelope: bytes, private_key) -> bytes:
    from . import cms
    try:
        plaintext, unused_algorithm = cms.decrypt_message(envelope, private_key)
    except CryptoError:
        raise
    except Exception:
        raise CryptoError('DECRYPT_FAILED') from None
    if len(plaintext) > MAX_PLAINTEXT:
        raise CryptoError('MESSAGE_TOO_LARGE')
    return plaintext


def encrypt_smime(data: bytes, recipients: tuple[CertificateInfo, ...]) -> bytes:
    if len(data) > MAX_ENVELOPE:
        raise CryptoError('MESSAGE_TOO_LARGE')
    if not recipients:
        raise CryptoError('RECIPIENT_CERT_MISSING')
    builder = pkcs7.PKCS7EnvelopeBuilder().set_data(data)
    seen = set()
    for info in recipients:
        validate_recipient(info)
        if info.fingerprint not in seen:
            builder = builder.add_recipient(x509.load_der_x509_certificate(info.certificate_der))
            seen.add(info.fingerprint)
    try:
        return builder.encrypt(serialization.Encoding.SMIME, [pkcs7.PKCS7Options.Binary])
    except (ValueError, TypeError, NotImplementedError):
        raise CryptoError('ENCRYPT_FAILED') from None


def encrypt_envelope_der(data: bytes, recipients: tuple[CertificateInfo, ...]) -> bytes:
    """CMS EnvelopedData (AES-256-CBC, RSA key transport) as DER, for a top-level
    application/pkcs7-mime part. The official client lists AES128/192/256 and
    DES3 as supported content encryption methods."""
    from cryptography.hazmat.primitives.ciphers import algorithms
    if len(data) > MAX_ENVELOPE:
        raise CryptoError('MESSAGE_TOO_LARGE')
    if not recipients:
        raise CryptoError('RECIPIENT_CERT_MISSING')
    builder = pkcs7.PKCS7EnvelopeBuilder().set_data(data).set_content_encryption_algorithm(algorithms.AES256)
    seen = set()
    for info in recipients:
        validate_recipient(info)
        if info.fingerprint not in seen:
            builder = builder.add_recipient(x509.load_der_x509_certificate(info.certificate_der))
            seen.add(info.fingerprint)
    try:
        return builder.encrypt(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary])
    except (ValueError, TypeError, NotImplementedError):
        raise CryptoError('ENCRYPT_FAILED') from None
