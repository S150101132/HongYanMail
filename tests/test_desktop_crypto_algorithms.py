"""Regression: a 3DES-encrypted message must be reported, never crash.

The real deployment encrypts with 3DES-CBC (OID 1.2.840.113549.3.7). pyca's
high-level pkcs7 decrypt accepts only AES-CBC and raises UnsupportedAlgorithm for
3DES, which previously escaped as an unhandled exception and surfaced to the user
as an internal error without a reason.

The envelope here is built with OpenSSL so the test uses a genuinely 3DES-wrapped
message rather than a mock of the failure.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cryptography.hazmat.primitives import serialization  # noqa: E402

from coremail_desktop.crypto.models import CryptoError  # noqa: E402
from coremail_desktop.crypto.service import CryptoService  # noqa: E402
from crypto_fixtures import identity  # noqa: E402

OPENSSL_CANDIDATES = (
    Path(r'C:\Program Files\Git\usr\bin\openssl.exe'),
    Path(r'C:\Program Files\OpenSSL-Win64\bin\openssl.exe'),
    Path('/usr/bin/openssl'),
)
MESSAGE = (b'From: sender@example.test\r\nTo: me@example.test\r\n'
           b'Subject: =?utf-8?B?M0RFUyDmtYvortg=?=\r\n\r\n3DES regression body\r\n')


def openssl_path():
    for candidate in OPENSSL_CANDIDATES:
        if candidate.is_file():
            return candidate
    found = shutil.which('openssl')
    return Path(found) if found else None


def build_3des_envelope(certificate_pem: bytes, directory: Path):
    """Produce an S/MIME enveloped message whose content is 3DES-CBC."""
    openssl = openssl_path()
    if openssl is None:
        return None
    cert = directory / 'recipient.pem'
    cert.write_bytes(certificate_pem)
    message = directory / 'message.eml'
    message.write_bytes(MESSAGE)
    output = directory / 'envelope.pem'
    completed = subprocess.run(
        [str(openssl), 'smime', '-encrypt', '-des3', '-in', str(message),
         '-out', str(output), '-outform', 'SMIME', str(cert)],
        capture_output=True, text=True, timeout=120)
    if completed.returncode != 0 or not output.is_file():
        return None
    return output.read_bytes()


class UnsupportedAlgorithmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key, cls.cert, cls.pfx = identity()

    def test_3des_envelope_reports_a_specific_code_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            envelope = build_3des_envelope(
                self.cert.public_bytes(serialization.Encoding.PEM), directory)
            if envelope is None:
                self.skipTest('OpenSSL with 3DES support is not available')

            parsed = BytesParser(policy=policy.default).parsebytes(envelope)
            self.assertIn(parsed.get_content_type(),
                          ('application/pkcs7-mime', 'application/x-pkcs7-mime'))

            service = CryptoService()
            service.unlock_bytes(self.pfx, 'test-password')
            self.addCleanup(service.lock)
            with self.assertRaises(CryptoError) as raised:
                service.decrypt(envelope)
            # A capability gap is reported as such, not as a wrong-owner failure
            # and not as an unhandled exception.
            self.assertEqual(raised.exception.code, 'ENCRYPTED_ALGORITHM_UNSUPPORTED')

    def test_every_resulting_code_has_a_user_message(self):
        from coremail_desktop.ui.messages import GENERIC, user_message
        self.assertNotEqual(user_message('ENCRYPTED_ALGORITHM_UNSUPPORTED'), GENERIC)
        self.assertIn('3DES', user_message('ENCRYPTED_ALGORITHM_UNSUPPORTED'))


if __name__ == '__main__':
    unittest.main()
