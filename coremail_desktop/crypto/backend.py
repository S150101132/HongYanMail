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
    if info.trust_status not in {'explicit_pin', 'own_key'}:
        raise CryptoError('RECIPIENT_CERT_UNTRUSTED')


def decrypt_smime(data: bytes, cert, private_key) -> bytes:
    if len(data) > MAX_ENVELOPE:
        raise CryptoError('MESSAGE_TOO_LARGE')
    message = BytesParser(policy=policy.default).parsebytes(data)
    kind = message.get_content_type().lower()
    if kind not in {'application/pkcs7-mime', 'application/x-pkcs7-mime'}:
        raise CryptoError('UNSUPPORTED_ENCRYPTION_FORMAT')
    if message.get_param('smime-type', '').lower() != 'enveloped-data':
        raise CryptoError('UNSUPPORTED_ENCRYPTION_FORMAT')
    envelope = message.get_payload(decode=True)
    if not isinstance(envelope, bytes) or not envelope:
        raise CryptoError('INVALID_ENCRYPTED_MESSAGE')
    try:
        plaintext = pkcs7.pkcs7_decrypt_der(envelope, cert, private_key, [])
    except UnsupportedAlgorithm:
        # pyca/cryptography's high-level pkcs7 decrypt only accepts AES-CBC and
        # rejects the 3DES-CBC (1.2.840.113549.3.7) that this deployment actually
        # uses. That is a capability gap, not a wrong-owner failure, so it must be
        # reported distinctly and must never surface as an unhandled exception.
        raise CryptoError('ENCRYPTED_ALGORITHM_UNSUPPORTED') from None
    except (ValueError, TypeError, NotImplementedError):
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
