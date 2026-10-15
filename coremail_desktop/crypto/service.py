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
                key, cert, _chain = pkcs12.load_key_and_certificates(data, password.encode('utf-8') or None)
            except (ValueError, TypeError, NotImplementedError):
                raise CryptoError('CERT_UNLOCK_FAILED') from None
            if cert is None or key is None:
                raise CryptoError('CERT_PRIVATE_KEY_MISSING')
            if not isinstance(key, rsa.RSAPrivateKey):
                raise CryptoError('CERT_ALGORITHM_UNSUPPORTED')
            if key.public_key().public_numbers() != cert.public_key().public_numbers():
                raise CryptoError('CERT_KEY_MISMATCH')
            self._key, self._certificate = key, cert
            self.certificate_info = describe(cert, 'own_key')
            return self.certificate_info

    def lock(self) -> None:
        with self._lock:
            self._key = self._certificate = self.certificate_info = None

    def decrypt(self, envelope: bytes) -> DecryptionResult:
        with self._lock:
            if self._key is None:
                raise CryptoError('CERT_LOCKED')
            plain = decrypt_smime(envelope, self._certificate, self._key)
            message = BytesParser(policy=policy.default).parsebytes(plain)
            signed = message.get_content_type() == 'multipart/signed'
            if message.get_content_type() in {'application/pkcs7-mime', 'application/x-pkcs7-mime'}:
                raise CryptoError('NESTED_CRYPTO_UNSUPPORTED')
            return DecryptionResult(plain, signature_status='not_verified' if signed else 'unsigned')

    def encrypt(self, mime_bytes: bytes, recipients: tuple[CertificateInfo, ...]) -> bytes:
        return encrypt_smime(mime_bytes, recipients)
