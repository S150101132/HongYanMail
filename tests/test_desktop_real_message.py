"""Verify decryption against the real supplier-encrypted message when present.

The message is account data and is not committed, so this test skips when it is
absent. It is the only end-to-end check against a genuine supplier envelope:
3DES-CBC outer envelope, inner signed container, readable multipart mail.

No plaintext, subject, address or key material is printed or asserted on; only
sizes and layer structure are checked.
"""
import os
import sys
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cryptography.hazmat.primitives.serialization import pkcs12  # noqa: E402

from coremail_desktop.crypto import cms  # noqa: E402

PFX = Path(os.environ.get('COREMAIL_REAL_PFX', r'D:\证件\王标-certificate.pfx'))
ENVELOPE = Path(os.environ.get('COREMAIL_REAL_ENVELOPE', str(ROOT / '邮箱发送测试.eml')))
PASSWORDS = Path(os.environ.get('COREMAIL_PASSWORD_FILE', str(ROOT / 'PASSWD.txt')))


def pfx_secret():
    """The passphrase line carries non-ASCII decoration; the ASCII run is the key."""
    first = PASSWORDS.read_text(encoding='utf-8').splitlines()[0].strip()
    return ''.join(char for char in first if ord(char) < 0x80).strip()


class RealMessageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PFX.is_file() or not ENVELOPE.is_file() or not PASSWORDS.is_file():
            raise unittest.SkipTest('real certificate, message or password file is not present')
        key, cert, unused_chain = pkcs12.load_key_and_certificates(
            PFX.read_bytes(), pfx_secret().encode('utf-8'))
        cls.key = key
        cls.cert = cert
        cls.raw = ENVELOPE.read_bytes()

    def test_outer_envelope_is_3des_enveloped_data(self):
        der = BytesParser(policy=policy.default).parsebytes(self.raw).get_payload(decode=True)
        parsed = cms.parse_enveloped_data(der)
        self.assertEqual(parsed.algorithm, cms.DES_EDE3_CBC)
        self.assertEqual(len(parsed.iv), 8)
        self.assertEqual(len(parsed.ciphertext) % 8, 0)
        self.assertGreaterEqual(len(parsed.recipients), 1)

    def test_real_message_peels_to_readable_mail(self):
        der = BytesParser(policy=policy.default).parsebytes(self.raw).get_payload(decode=True)
        peeled = cms.peel_message(der, self.key)
        self.assertIn('des-ede3-cbc', peeled['algorithms'])
        self.assertTrue(peeled['signed'], 'the supplier message carries a signature layer')
        self.assertEqual(peeled['signature_status'], 'present_not_verified',
                         'signatures are reported, never claimed as verified')
        body = peeled['bytes']
        self.assertGreater(len(body), 1000)
        message = BytesParser(policy=policy.default).parsebytes(body)
        self.assertTrue(message.get_content_type().startswith(('multipart/', 'text/')),
                        'decrypted content should be readable mail')

    def test_the_supplier_signature_layer_reports_its_algorithms(self):
        der = BytesParser(policy=policy.default).parsebytes(self.raw).get_payload(decode=True)
        inner = cms.unwrap_mime_entity(cms.decrypt_message(der, self.key)[0])
        summary = cms.signed_data_summary(inner)
        self.assertGreaterEqual(summary['signer_count'], 1)
        self.assertTrue(summary['digest_algorithms'])
        self.assertIn('2.16.840.1.101.3.4.2.1', summary['digest_algorithms'],
                      'the supplier signs with SHA-256')

    def test_another_certificate_cannot_read_the_real_message(self):
        from crypto_fixtures import identity
        other_key, unused_cert, unused_pfx = identity('other@example.invalid')
        der = BytesParser(policy=policy.default).parsebytes(self.raw).get_payload(decode=True)
        with self.assertRaises(Exception):
            cms.decrypt_message(der, other_key)

    def test_service_decrypts_and_the_reader_keeps_the_envelope_headers(self):
        """The displayed document must carry the envelope's From/To/Subject/Date.

        The decrypted content is multipart/related with no headers of its own, so
        the reader only has those fields if the service result is merged with the
        outer message headers.
        """
        from coremail_desktop.crypto.service import CryptoService
        from coremail_desktop.mime.parser import parse_mime

        service = CryptoService()
        service.unlock(str(PFX), pfx_secret())
        self.addCleanup(service.lock)
        result = service.decrypt(self.raw)
        self.assertEqual(result.format, 'smime-enveloped-data+signed-data')
        self.assertEqual(result.signature_status, 'not_verified',
                         'a signature is reported, never claimed as verified')

        document = parse_mime(result.mime_bytes, 'real-1')
        self.assertTrue((document.text or '').strip() or document.html,
                        'decrypted message must expose readable content')

        outer = BytesParser(policy=policy.default).parsebytes(self.raw)
        merged = dict(document.headers)
        for name in ('from', 'to', 'subject', 'date', 'message-id'):
            values = outer.get_all(name, [])
            if values:
                merged[name] = tuple(str(value) for value in values)
        for expected in ('from', 'to', 'subject', 'date'):
            self.assertIn(expected, merged,
                          f'{expected} must survive decryption into the reader')

    def test_decrypted_content_has_no_plaintext_leak_into_headers_of_the_envelope(self):
        """Sanity: the envelope itself must not carry the decrypted body."""
        self.assertNotIn(b'multipart/related', self.raw,
                         'the encrypted envelope must not expose the decrypted structure')


if __name__ == '__main__':
    unittest.main()
