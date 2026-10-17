"""Tests for CMS decryption of the algorithms this deployment actually uses.

Envelopes are generated with OpenSSL rather than mocked, so the parser is checked
against real DER: including the primitive context tag [0] form and the explicit
wrapper the supplier build uses.
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

from coremail_desktop.crypto import cms  # noqa: E402
from coremail_desktop.crypto.models import CryptoError  # noqa: E402
from crypto_fixtures import identity  # noqa: E402

OPENSSL_CANDIDATES = (
    Path(r'C:\Program Files\Git\usr\bin\openssl.exe'),
    Path(r'C:\Program Files\OpenSSL-Win64\bin\openssl.exe'),
    Path('/usr/bin/openssl'),
)
BODY = (b'From: sender@example.test\r\nTo: me@example.test\r\n'
        b'Subject: =?utf-8?B?Q01TIOa1i+ivlQ==?=\r\n\r\n'
        + 'CMS 内容正文 with ASCII\r\n'.encode('utf-8'))


def openssl_path():
    for candidate in OPENSSL_CANDIDATES:
        if candidate.is_file():
            return candidate
    found = shutil.which('openssl')
    return Path(found) if found else None


def make_envelope(certificate_pem: bytes, cipher: str, extra_recipient_pem: bytes = None):
    openssl = openssl_path()
    if openssl is None:
        return None
    directory = Path(tempfile.mkdtemp(prefix='cms-test-'))
    try:
        recipient = directory / 'recipient.pem'
        recipient.write_bytes(certificate_pem)
        message = directory / 'message.eml'
        message.write_bytes(BODY)
        output = directory / 'envelope.pem'
        command = [str(openssl), 'smime', '-encrypt', '-' + cipher, '-in', str(message),
                   '-out', str(output), '-outform', 'SMIME']
        command.append(str(recipient))
        if extra_recipient_pem is not None:
            extra = directory / 'extra.pem'
            extra.write_bytes(extra_recipient_pem)
            command.append(str(extra))
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if completed.returncode != 0 or not output.is_file():
            return None
        return output.read_bytes(), directory
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


class CmsDecryptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key, cls.cert, cls.pfx = identity()
        cls.other_key, cls.other_cert, cls.other_pfx = identity('other@example.invalid')
        cls.cert_pem = cls.cert.public_bytes(serialization.Encoding.PEM)
        cls.other_pem = cls.other_cert.public_bytes(serialization.Encoding.PEM)

    def setUp(self):
        self.temporary = []

    def tearDown(self):
        for directory in self.temporary:
            shutil.rmtree(directory, ignore_errors=True)

    def envelope(self, cipher, extra=None):
        built = make_envelope(self.cert_pem, cipher, extra)
        if built is None:
            self.skipTest('OpenSSL is not available')
        data, directory = built
        self.temporary.append(directory)
        return data

    def test_3des_envelope_roundtrip_matches_the_original_bytes(self):
        envelope = self.envelope('des3')
        der = BytesParser(policy=policy.default).parsebytes(envelope).get_payload(decode=True)
        plaintext, algorithm = cms.decrypt_message(der, self.key)
        self.assertEqual(algorithm, 'des-ede3-cbc')
        self.assertEqual(plaintext, BODY)

    def test_aes256_envelope_roundtrip(self):
        envelope = self.envelope('aes256')
        der = BytesParser(policy=policy.default).parsebytes(envelope).get_payload(decode=True)
        plaintext, algorithm = cms.decrypt_message(der, self.key)
        self.assertEqual(algorithm, 'aes256-cbc')
        self.assertEqual(plaintext, BODY)

    def test_an_unrelated_key_cannot_decrypt(self):
        envelope = self.envelope('des3')
        der = BytesParser(policy=policy.default).parsebytes(envelope).get_payload(decode=True)
        with self.assertRaises(CryptoError) as raised:
            cms.decrypt_message(der, self.other_key)
        self.assertEqual(raised.exception.code, 'DECRYPT_FAILED')

    def test_short_wrong_key_unwrap_is_reported_as_decryption_failure(self):
        # Providers may use implicit rejection and return synthetic short key
        # material instead of throwing for an unrelated RSA recipient.
        from unittest.mock import patch
        envelope=self.envelope('des3')
        der=BytesParser(policy=policy.default).parsebytes(envelope).get_payload(decode=True)
        with patch.object(cms,'unwrap_content_keys',return_value=[b'bad']):
            with self.assertRaises(CryptoError) as raised: cms.decrypt_message(der,self.other_key)
        self.assertEqual(raised.exception.code,'DECRYPT_FAILED')

    def test_multi_recipient_message_decrypts_for_each_holder(self):
        envelope = self.envelope('aes256', extra=self.other_pem)
        der = BytesParser(policy=policy.default).parsebytes(envelope).get_payload(decode=True)
        self.assertEqual(cms.decrypt_message(der, self.key)[0], BODY)
        self.assertEqual(cms.decrypt_message(der, self.other_key)[0], BODY)

    def test_tampered_ciphertext_is_refused(self):
        envelope = self.envelope('des3')
        original = bytearray(BytesParser(policy=policy.default).parsebytes(envelope).get_payload(decode=True))
        # Corrupt the final two blocks so the padding cannot survive, or (rarely)
        # the padding still validates but the content must differ. Either outcome
        # is acceptable; silently returning the original plaintext is not.
        refusals = 0
        for offset in range(9, 25):
            tampered = bytearray(original)
            tampered[-offset] ^= 0xFF
            try:
                plaintext, unused = cms.decrypt_message(bytes(tampered), self.key)
            except CryptoError:
                refusals += 1
                continue
            self.assertNotEqual(plaintext, BODY, 'tampered ciphertext produced the original text')
        self.assertGreater(refusals, 0, 'no tampering attempt was refused')

    def test_plain_message_is_not_treated_as_encrypted(self):
        der = BytesParser(policy=policy.default).parsebytes(BODY).get_payload(decode=True)
        self.assertFalse(cms.looks_like_enveloped_data(BODY))
        with self.assertRaises(CryptoError):
            cms.decrypt_message(BODY, self.key)

    def test_unsupported_content_algorithm_is_reported_specifically(self):
        # An RC2 envelope exercises the "recognised cryptography, unsupported here"
        # path when the local OpenSSL is willing to build one.
        built = make_envelope(self.cert_pem, 'rc2-128')
        if built is None:
            self.skipTest('local OpenSSL will not build an RC2 envelope')
        envelope, directory = built
        self.temporary.append(directory)
        der = BytesParser(policy=policy.default).parsebytes(envelope).get_payload(decode=True)
        with self.assertRaises(CryptoError) as raised:
            cms.decrypt_message(der, self.key)
        self.assertEqual(raised.exception.code, 'ENCRYPTED_ALGORITHM_UNSUPPORTED')

    def test_padding_removal_rejects_malformed_padding(self):
        self.assertEqual(cms.strip_pkcs7_padding(b'\x01\x02\x03\x04\x04\x04\x04\x04', 8), b'\x01\x02\x03\x04')
        for bad in (b'', b'\x00' * 8, b'\x01\x02\x03\x04\x05\x06\x07\x09', b'\x01\x02\x03'):
            with self.assertRaises(CryptoError):
                cms.strip_pkcs7_padding(bad, 8)

    def test_oid_labels_match_rfc5652(self):
        self.assertEqual(cms.ENVELOPED_DATA, '1.2.840.113549.1.7.3')
        self.assertEqual(cms.SIGNED_DATA, '1.2.840.113549.1.7.2')
        self.assertEqual(cms.DES_EDE3_CBC, '1.2.840.113549.3.7')


if __name__ == '__main__':
    unittest.main()
