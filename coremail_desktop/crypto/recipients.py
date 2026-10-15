from pathlib import Path
from cryptography import x509
from .backend import describe, validate_recipient
from .models import CertificateInfo, CryptoError


class RecipientCertificateStore:
    """Public certificates pinned by explicit user choice, session memory only.

Pinning is displayed as manual trust, never as OS chain/revocation validation.
"""
    def __init__(self):
        self._by_email = {}

    def add_bytes(self, public_data: bytes, trusted=False) -> CertificateInfo:
        if len(public_data) > 1024 * 1024:
            raise CryptoError('CERT_FILE_TOO_LARGE')
        try:
            cert = (x509.load_pem_x509_certificate(public_data) if b'-----BEGIN CERTIFICATE-----' in public_data
                    else x509.load_der_x509_certificate(public_data))
            info = describe(cert, 'explicit_pin' if trusted else 'untrusted')
        except (ValueError, TypeError):
            raise CryptoError('CERT_FORMAT_UNSUPPORTED') from None
        for address in info.emails:
            self._by_email[address] = info
        return info

    def add_file(self, path: str, trusted=False) -> CertificateInfo:
        try:
            if Path(path).stat().st_size > 1024 * 1024:
                raise CryptoError('CERT_FILE_TOO_LARGE')
            data = Path(path).read_bytes()
        except OSError:
            raise CryptoError('CERT_FILE_UNAVAILABLE') from None
        return self.add_bytes(data, trusted)

    def resolve(self, address: str) -> CertificateInfo | None:
        return self._by_email.get(address.strip().casefold())

    def require(self, addresses: tuple[str, ...]) -> tuple[CertificateInfo, ...]:
        result = []
        for address in addresses:
            info = self.resolve(address)
            if info is None:
                raise CryptoError('RECIPIENT_CERT_MISSING')
            validate_recipient(info)
            result.append(info)
        return tuple(result)
