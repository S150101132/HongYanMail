import unittest
from cryptography.hazmat.primitives import serialization
from crypto_fixtures import identity
from coremail_desktop.crypto.service import CryptoService, CryptoError
from coremail_desktop.crypto.recipients import RecipientCertificateStore
from coremail_desktop.crypto.formats import detect_envelope_format


class CryptoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key, cls.cert, cls.pfx = identity()
        cls.other_key, cls.other_cert, cls.other_pfx = identity('other@example.invalid')

    def service(self):
        service = CryptoService()
        service.unlock_bytes(self.pfx, 'test-password')
        return service

    def test_wrong_password_has_fixed_error_without_secret(self):
        with self.assertRaises(CryptoError) as result:
            CryptoService().unlock_bytes(self.pfx, 'wrong-secret')
        self.assertEqual(result.exception.code, 'CERT_UNLOCK_FAILED')
        self.assertNotIn('wrong-secret', str(result.exception))

    def test_unlock_returns_public_identity_without_private_key(self):
        info = self.service().certificate_info
        self.assertEqual(info.emails, ('test@example.invalid',))
        self.assertFalse(hasattr(info, 'private_key'))
        self.assertTrue(info.can_encrypt)

    def test_real_smime_roundtrip_preserves_chinese_and_crlf(self):
        service = self.service()
        raw = 'Content-Type: text/plain; charset=utf-8\r\n\r\n证书测试\r\n'.encode()
        encrypted = service.encrypt(raw, (service.certificate_info,))
        self.assertNotIn('证书测试'.encode(), encrypted)
        self.assertEqual(service.decrypt(encrypted).mime_bytes, raw)

    def test_another_key_cannot_decrypt(self):
        first = self.service()
        encrypted = first.encrypt(b'Content-Type: text/plain\r\n\r\nsecret', (first.certificate_info,))
        other = CryptoService()
        other.unlock_bytes(self.other_pfx, 'test-password')
        with self.assertRaises(CryptoError) as result:
            other.decrypt(encrypted)
        self.assertEqual(result.exception.code, 'DECRYPT_FAILED')

    def test_lock_removes_key_and_rejects_decryption(self):
        service = self.service()
        encrypted = service.encrypt(b'Content-Type: text/plain\r\n\r\nsecret', (service.certificate_info,))
        service.lock()
        self.assertIsNone(service.certificate_info)
        with self.assertRaises(CryptoError) as result:
            service.decrypt(encrypted)
        self.assertEqual(result.exception.code, 'CERT_LOCKED')

    def test_plain_or_unknown_format_is_never_claimed_as_decrypted(self):
        with self.assertRaises(CryptoError) as result:
            self.service().decrypt(b'Content-Type: application/octet-stream\r\n\r\nunknown')
        self.assertEqual(result.exception.code, 'UNSUPPORTED_ENCRYPTION_FORMAT')

    def test_format_detector_distinguishes_signed_and_encrypted(self):
        self.assertIsNone(detect_envelope_format(b'Content-Type: text/plain\r\n\r\nplain'))
        self.assertEqual(detect_envelope_format(b'Content-Type: application/pkcs7-mime; smime-type=enveloped-data\r\n\r\n'), 'smime-enveloped-data')
        self.assertEqual(detect_envelope_format(b'Content-Type: application/pkcs7-mime; smime-type=signed-data\r\n\r\n'), 'unsupported-crypto')

    def test_oversized_input_rejected_before_parse(self):
        service = self.service()
        with self.assertRaises(CryptoError) as result:
            service.decrypt(b'x' * (25 * 1024 * 1024 + 1))
        self.assertEqual(result.exception.code, 'MESSAGE_TOO_LARGE')

    def test_untrusted_recipient_requires_explicit_pin(self):
        store = RecipientCertificateStore()
        public = self.other_cert.public_bytes(serialization.Encoding.PEM)
        info = store.add_bytes(public, trusted=False)
        self.assertEqual(info.trust_status, 'untrusted')
        with self.assertRaises(CryptoError) as result:
            store.require(('other@example.invalid',))
        self.assertEqual(result.exception.code, 'RECIPIENT_CERT_UNTRUSTED')

    def test_missing_recipient_never_falls_back_to_cleartext(self):
        store = RecipientCertificateStore()
        with self.assertRaises(CryptoError) as result:
            store.require(('missing@example.invalid',))
        self.assertEqual(result.exception.code, 'RECIPIENT_CERT_MISSING')

    def test_recipient_identity_is_bound_to_certificate(self):
        store = RecipientCertificateStore()
        store.add_bytes(self.other_cert.public_bytes(serialization.Encoding.DER), trusted=True)
        self.assertIsNone(store.resolve('different@example.invalid'))
        self.assertEqual(len(store.require(('other@example.invalid',))), 1)

    def test_expired_certificate_can_unlock_for_history_but_not_encrypt(self):
        _, _, pfx = identity(expired=True)
        service = CryptoService()
        info = service.unlock_bytes(pfx, 'test-password')
        self.assertFalse(info.can_encrypt)
        with self.assertRaises(CryptoError) as result:
            service.encrypt(b'Content-Type: text/plain\r\n\r\nmessage', (info,))
        self.assertEqual(result.exception.code, 'RECIPIENT_CERT_EXPIRED')


if __name__ == '__main__':
    unittest.main()
