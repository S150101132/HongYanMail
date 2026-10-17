"""Regression tests around content-encryption algorithm support.

History: the real deployment encrypts with 3DES-CBC, which pyca's high-level
pkcs7 helper refuses. That surfaced as an unhandled UnsupportedAlgorithm and then,
once caught, as a blanket "unsupported" result even after a direct CMS path made
3DES work. These tests pin the behaviour that must hold now:

  * a 3DES message decrypts through the product surface;
  * an algorithm that is genuinely unsupported is reported specifically;
  * neither case ever escapes as an unhandled exception.

Envelopes are built with OpenSSL, so the checks run against real DER.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
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
           b'Subject: =?utf-8?B?M0RFUyDmtYvortg=?=\r\n\r\n'
           + 'algorithm regression body\r\n'.encode('utf-8'))


def openssl_path():
    for candidate in OPENSSL_CANDIDATES:
        if candidate.is_file():
            return candidate
    found = shutil.which('openssl')
    return Path(found) if found else None


def build_envelope(certificate_pem: bytes, cipher: str):
    openssl = openssl_path()
    if openssl is None:
        return None
    directory = Path(tempfile.mkdtemp(prefix='algo-test-'))
    try:
        recipient = directory / 'recipient.pem'
        recipient.write_bytes(certificate_pem)
        message = directory / 'message.eml'
        message.write_bytes(MESSAGE)
        output = directory / 'envelope.pem'
        completed = subprocess.run(
            [str(openssl), 'smime', '-encrypt', '-' + cipher, '-in', str(message),
             '-out', str(output), '-outform', 'SMIME', str(recipient)],
            capture_output=True, text=True, timeout=120)
        if completed.returncode != 0 or not output.is_file():
            shutil.rmtree(directory, ignore_errors=True)  # Unsupported cipher: leave nothing behind.
            return None
        return output.read_bytes(), directory
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


class ContentAlgorithmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key, cls.cert, cls.pfx = identity()
        cls.cert_pem = cls.cert.public_bytes(serialization.Encoding.PEM)

    def setUp(self):
        self.temporary = []

    def tearDown(self):
        for directory in self.temporary:
            shutil.rmtree(directory, ignore_errors=True)

    def service(self):
        service = CryptoService()
        service.unlock_bytes(self.pfx, 'test-password')
        self.addCleanup(service.lock)
        return service

    def test_3des_envelope_decrypts_through_the_product_service(self):
        built = build_envelope(self.cert_pem, 'des3')
        if built is None:
            self.skipTest('OpenSSL is not available')
        envelope, directory = built
        self.temporary.append(directory)
        plaintext = self.service().decrypt(envelope).mime_bytes
        self.assertEqual(plaintext, MESSAGE)

    def test_aes_envelope_still_decrypts_through_the_product_service(self):
        built = build_envelope(self.cert_pem, 'aes256')
        if built is None:
            self.skipTest('OpenSSL is not available')
        envelope, directory = built
        self.temporary.append(directory)
        self.assertEqual(self.service().decrypt(envelope).mime_bytes, MESSAGE)

    def test_genuinely_unsupported_algorithm_is_reported_specifically(self):
        built = build_envelope(self.cert_pem, 'rc2-128')
        if built is None:
            self.skipTest('local OpenSSL will not build an RC2 envelope')
        envelope, directory = built
        self.temporary.append(directory)
        with self.assertRaises(CryptoError) as raised:
            self.service().decrypt(envelope)
        self.assertEqual(raised.exception.code, 'ENCRYPTED_ALGORITHM_UNSUPPORTED')

    def test_a_wrong_key_is_never_reported_as_an_algorithm_problem(self):
        other_key, other_cert, other_pfx = identity('other@example.invalid')
        built = build_envelope(self.cert_pem, 'des3')
        if built is None:
            self.skipTest('OpenSSL is not available')
        envelope, directory = built
        self.temporary.append(directory)
        service = CryptoService()
        service.unlock_bytes(other_pfx, 'test-password')
        self.addCleanup(service.lock)
        with self.assertRaises(CryptoError) as raised:
            service.decrypt(envelope)
        self.assertEqual(raised.exception.code, 'DECRYPT_FAILED')

    def test_every_resulting_code_has_a_user_message(self):
        from coremail_desktop.ui.messages import GENERIC, user_message
        for code in ('ENCRYPTED_ALGORITHM_UNSUPPORTED', 'DECRYPT_FAILED'):
            self.assertNotEqual(user_message(code), GENERIC)


if __name__ == '__main__':
    unittest.main()
