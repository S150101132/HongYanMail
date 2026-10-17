from pathlib import Path
from threading import RLock
from email import policy
from email.parser import BytesParser
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from .models import CertificateInfo, DecryptionResult, CryptoError
from .backend import describe, decrypt_smime, encrypt_smime


class CryptoService:
    def __init__(self):
        self._lock = RLock()
        self._key = None
        self._certificate = None
        self._chain = ()
        self.certificate_info = None

    def unlock(self, pfx_path: str, password: str) -> CertificateInfo:
        try:
            path = Path(pfx_path)
            if path.stat().st_size > 4 * 1024 * 1024:
                raise CryptoError('CERT_FILE_TOO_LARGE')
            data = path.read_bytes()
        except OSError:
            raise CryptoError('CERT_FILE_UNAVAILABLE') from None
        return self.unlock_bytes(data, password)

    def unlock_bytes(self, data: bytes, password: str) -> CertificateInfo:
        with self._lock:
            self.lock()
            if len(data) > 4 * 1024 * 1024:
                raise CryptoError('CERT_FILE_TOO_LARGE')
            try:
                key, cert, chain = pkcs12.load_key_and_certificates(data, password.encode('utf-8') or None)
            except (ValueError, TypeError, NotImplementedError):
                raise CryptoError('CERT_UNLOCK_FAILED') from None
            if cert is None or key is None:
                raise CryptoError('CERT_PRIVATE_KEY_MISSING')
            if not isinstance(key, rsa.RSAPrivateKey):
                raise CryptoError('CERT_ALGORITHM_UNSUPPORTED')
            if key.public_key().public_numbers() != cert.public_key().public_numbers():
                raise CryptoError('CERT_KEY_MISMATCH')
            self._key, self._certificate, self._chain = key, cert, tuple(chain or ())
            self.certificate_info = describe(cert, 'own_key')
            return self.certificate_info

    def lock(self) -> None:
        with self._lock:
            self._key = self._certificate = self.certificate_info = None
            self._chain = ()

    def decrypt(self, envelope: bytes, sender: str = '') -> DecryptionResult:
        """Decrypt an encrypted message and return readable MIME.

        The official client signs, then encrypts, so the plaintext is usually an
        opaque signed-data entity. That signature layer is verified and peeled;
        any further encrypted layer is refused explicitly instead of being
        presented as though it were the body.
        """
        with self._lock:
            if self._key is None:
                raise CryptoError('CERT_LOCKED')
            from . import cms
            from .signature import unwrap_signed

            plain = decrypt_smime(envelope, self._certificate, self._key)
            format_name, report = 'smime-enveloped-data', None
            unwrapped = unwrap_signed(plain, sender)
            if unwrapped is not None:
                plain, report = unwrapped
                format_name = 'smime-enveloped-data+signed-data'
                if not plain:
                    # Detached signature with no content to show.
                    raise CryptoError('NESTED_CRYPTO_UNSUPPORTED')
            message = BytesParser(policy=policy.default).parsebytes(plain)
            if message.get_content_type() in {'application/pkcs7-mime', 'application/x-pkcs7-mime'}:
                inner_der = message.get_payload(decode=True) or b''
                if cms.looks_like_enveloped_data(inner_der) or cms.looks_like_signed_data(inner_der):
                    # Encryption inside encryption is not a supported shape yet.
                    raise CryptoError('NESTED_CRYPTO_UNSUPPORTED')
                raise CryptoError('UNSUPPORTED_ENCRYPTION_FORMAT' if inner_der else 'INVALID_ENCRYPTED_MESSAGE')
            return DecryptionResult(plain, format=format_name,
                                    signature_status=report.status if report else 'unsigned', signature=report)

    def signing_identity(self):
        """(certificate, key, chain) for signing, or CryptoError when locked/unusable."""
        from .signature import can_sign
        with self._lock:
            if self._key is None:
                raise CryptoError('CERT_LOCKED')
            if not can_sign(self._certificate):
                raise CryptoError('CERT_SIGN_USAGE')
            return self._certificate, self._key, self._chain

    def encrypt(self, mime_bytes: bytes, recipients: tuple[CertificateInfo, ...]) -> bytes:
        return encrypt_smime(mime_bytes, recipients)
