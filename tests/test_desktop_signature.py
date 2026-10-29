"""S/MIME signatures: SHA-256/RSA signing, sign-then-encrypt, and verification of
opaque and multipart/signed mail with generated certificates."""
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import pkcs7, pkcs12
from cryptography.x509.oid import NameOID

from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter, demo_certificate
from coremail_desktop.compose.smime import build_signed_message
from coremail_desktop.crypto import cms, signature
from coremail_desktop.crypto.service import CryptoService
from coremail_desktop.models import DraftDocument
from tests.test_app_backend import XorProtector, local_services_stub

CONTENT = b'Content-Type: text/plain; charset="utf-8"\r\nContent-Transfer-Encoding: base64\r\n\r\n5YaF6YOo6LWE5paZ\r\n'


def certificate(email, days=365, start=-1, issuer='Test CA'):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, email), x509.NameAttribute(NameOID.EMAIL_ADDRESS, email)])
    cert = (x509.CertificateBuilder().subject_name(subject)
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)]))
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now + timedelta(days=start)).not_valid_after(now + timedelta(days=start + days))
            .add_extension(x509.SubjectAlternativeName([x509.RFC822Name(email)]), False)
            .sign(key, hashes.SHA256()))
    return cert, key


def pfx_for(email):
    _der, key, cert = demo_certificate(email)
    return pkcs12.serialize_key_and_certificates(b'me', key, cert, None, serialization.BestAvailableEncryption(b'pw'))


def draft(**values):
    base = dict(local_id='l1', revision=1, mode='new', from_identity='demo@example.invalid', to=('partner@external.example',),
                cc=(), bcc=('hidden@example.invalid',), subject='季度预算', body='<p>内部资料</p>', reference_headers={},
                is_html=True)
    base.update(values)
    return DraftDocument(**base)


class SignedDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cert, cls.key = certificate('me@example.invalid', issuer='Example Issuing CA')

    def test_opaque_signature_is_valid_and_names_the_issuer(self):
        der = signature.sign_opaque(CONTENT, self.cert, self.key)
        report, content = signature.verify_signed_data(der, sender='Me@Example.invalid')
        self.assertEqual((report.status, report.digest, content), ('valid', 'sha256', CONTENT))
        self.assertEqual(report.issuer, 'Example Issuing CA')
        self.assertTrue(report.email_match)
        self.assertTrue(report.time_valid)
        self.assertIsNotNone(report.signed_at)
        self.assertEqual(cms.signed_data_summary(der)['digest_algorithms'], ['2.16.840.1.101.3.4.2.1'])

    def test_altered_content_or_signature_is_invalid(self):
        der = signature.sign_opaque(CONTENT, self.cert, self.key)
        self.assertEqual(signature.verify_signed_data(der.replace(b'5YaF', b'5YaG'))[0].code, 'CONTENT_DIGEST_MISMATCH')
        detached = signature.sign_detached(CONTENT, self.cert, self.key)
        self.assertEqual(signature.verify_signed_data(detached, CONTENT)[0].status, 'valid')
        broken = detached[:-1] + bytes([detached[-1] ^ 1])
        self.assertEqual(signature.verify_signed_data(broken, CONTENT)[0].code, 'SIGNATURE_MISMATCH')

    def test_without_attributes_the_signature_covers_the_content(self):
        der = (pkcs7.PKCS7SignatureBuilder().set_data(CONTENT)
               .add_signer(self.cert, self.key, hashes.SHA512(), rsa_padding=padding.PKCS1v15())
               .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary, pkcs7.PKCS7Options.NoAttributes]))
        self.assertEqual(signature.verify_signed_data(der)[0].status, 'valid')
        self.assertEqual(signature.verify_signed_data(der.replace(b'5YaF', b'5YaG'))[0].code, 'SIGNATURE_MISMATCH')

    def test_signature_oid_in_the_digest_field_is_read_as_its_hash(self):
        der = (pkcs7.PKCS7SignatureBuilder().set_data(CONTENT)
               .add_signer(self.cert, self.key, hashes.SHA512(), rsa_padding=padding.PKCS1v15())
               .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary]))
        sha512, sha512_rsa = bytes.fromhex('0609608648016503040203'), bytes.fromhex('06092a864886f70d01010d')
        self.assertEqual(der.count(sha512), 2)  # digestAlgorithms and the signer's digestAlgorithm
        report = signature.verify_signed_data(der.replace(sha512, sha512_rsa))[0]
        self.assertEqual((report.status, report.digest), ('valid', 'sha512'))
        tampered = der.replace(sha512, sha512_rsa).replace(b'5YaF', b'5YaG')
        self.assertEqual(signature.verify_signed_data(tampered)[0].code, 'CONTENT_DIGEST_MISMATCH')

    def test_missing_certificate_or_content_is_unverifiable_not_valid(self):
        no_certs = (pkcs7.PKCS7SignatureBuilder().set_data(CONTENT)
                    .add_signer(self.cert, self.key, hashes.SHA256(), rsa_padding=padding.PKCS1v15())
                    .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary, pkcs7.PKCS7Options.NoCerts]))
        self.assertEqual(signature.verify_signed_data(no_certs)[0].code, 'SIGNER_CERT_MISSING')
        detached = signature.sign_detached(CONTENT, self.cert, self.key)
        self.assertEqual(signature.verify_signed_data(detached)[0].code, 'SIGNED_CONTENT_MISSING')
        self.assertEqual(signature.verify_signed_data(b'\x30\x03\x02\x01\x00')[0].status, 'unverifiable')

    def test_wrong_sender_and_expired_certificate_are_reported(self):
        report = signature.verify_signed_data(signature.sign_opaque(CONTENT, self.cert, self.key), sender='boss@example.invalid')[0]
        self.assertEqual((report.status, report.email_match), ('valid', False))
        old_cert, old_key = certificate('me@example.invalid', days=30, start=-400)
        report = signature.verify_signed_data(signature.sign_opaque(CONTENT, old_cert, old_key))[0]
        self.assertEqual((report.status, report.time_valid), ('valid', False))

    def test_signing_requires_an_rsa_key_and_signing_usage(self):
        self.assertTrue(signature.can_sign(self.cert))
        now = datetime.now(timezone.utc)
        encrypt_only = (x509.CertificateBuilder().subject_name(self.cert.subject).issuer_name(self.cert.issuer)
                        .public_key(self.key.public_key()).serial_number(1).not_valid_before(now)
                        .not_valid_after(now + timedelta(days=1))
                        .add_extension(x509.KeyUsage(False, False, True, False, False, False, False, False, False), True)
                        .sign(self.key, hashes.SHA256()))
        self.assertFalse(signature.can_sign(encrypt_only))


class SignedMimeTest(unittest.TestCase):
    def setUp(self):
        _der, self.key, self.cert = demo_certificate('demo@example.invalid')

    def test_multipart_signed_message_verifies_and_survives_lf_storage(self):
        raw = build_signed_message(draft(), (self.cert, self.key, ()), '演示用户')
        raw.decode('ascii')  # The pre-encoded submission path takes ASCII only.
        outer = BytesParser(policy=policy.default).parsebytes(raw)
        self.assertEqual(outer.get_content_type(), 'multipart/signed')
        self.assertEqual(outer.get_param('micalg'), 'sha-256')
        self.assertIsNone(outer['Bcc'])
        content, report = signature.unwrap_signed(raw, 'demo@example.invalid')
        self.assertEqual(report.status, 'valid')
        self.assertIn('内部资料', BytesParser(policy=policy.default).parsebytes(content).get_body(('html',)).get_content())
        self.assertEqual(signature.unwrap_signed(raw.replace(b'\r\n', b'\n'))[1].status, 'valid')

    def test_tampered_multipart_signed_body_is_invalid(self):
        raw = build_signed_message(draft(is_html=False, body='hello'), (self.cert, self.key, ()))
        tampered = raw.replace(b'aGVsbG8', b'aGVsbG9')
        self.assertNotEqual(raw, tampered)
        self.assertEqual(signature.unwrap_signed(tampered)[1].status, 'invalid')

    def test_unsigned_mail_is_left_alone(self):
        self.assertIsNone(signature.unwrap_signed(b'Content-Type: text/plain\r\n\r\nhi\r\n'))


class SignedSendTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.backend = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(),
                                   services_factory=local_services_stub, crypto=CryptoService())
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw')
        self.addCleanup(self.backend.logout)

    def unlock(self, email='demo@example.invalid'):
        self.backend.crypto.unlock_bytes(pfx_for(email), 'pw')

    def draft(self, **values):
        value = self.backend.compose('new')
        value.update(subject='季度预算', html='<p>内部资料 <b>请勿外传</b></p>', **values)
        return value

    def test_new_drafts_leave_signing_to_follow_encryption(self):
        self.assertIsNone(self.backend.compose('new')['sign'])

    def test_locked_certificate_asks_instead_of_sending_unsigned(self):
        result = self.backend.send(self.draft(to=['zhangxm@example.invalid'], encrypt=True, sign=True))
        self.assertEqual(result['state'], 'NeedsSigningKey')
        self.assertEqual(self.backend.adapter.sent, [])
        self.assertEqual(len(self.backend.list_drafts()), 1)
        restored = self.backend.restore_draft(result['localId'])
        self.assertEqual((restored['encrypt'], restored['sign']), (True, True))

    def test_signed_and_encrypted_mail_is_sign_then_encrypt_and_reads_back_valid(self):
        self.unlock()
        self.assertEqual(self.backend.send(self.draft(to=['zhangxm@example.invalid'], encrypt=True, sign=True))['state'], 'Accepted')
        payload = self.backend.adapter.sent_payloads[-1]
        self.assertTrue(self.backend.adapter.sent[-1].signature_required)
        _der, key, _cert = demo_certificate('zhangxm@example.invalid')
        plain, _ = cms.decrypt_message(BytesParser(policy=policy.default).parsebytes(payload).get_payload(decode=True), key)
        entity = BytesParser(policy=policy.default).parsebytes(plain)
        self.assertEqual((entity.get_content_type(), entity.get_param('smime-type')), ('application/pkcs7-mime', 'signed-data'))
        self.assertTrue(cms.looks_like_signed_data(entity.get_payload(decode=True)))
        # The sender's copy opens decrypted with a valid signature badge.
        self.backend.cache.put_body('sent1', payload)
        message = self.backend.open_message('sent1')
        self.assertEqual(message['encryption'], 'decrypted')
        self.assertIn('请勿外传', message['html'])
        self.assertEqual(message['signature']['status'], 'valid')
        self.assertTrue(message['signature']['emailMatch'])
        self.assertTrue(message['signature']['directory'])

    def test_encrypted_without_signature_stays_unsigned(self):
        self.unlock()
        self.backend.send(self.draft(to=['zhangxm@example.invalid'], encrypt=True, sign=False))
        self.backend.cache.put_body('sent2', self.backend.adapter.sent_payloads[-1])
        self.assertIsNone(self.backend.open_message('sent2')['signature'])

    def test_signed_only_mail_to_external_goes_pre_encoded_as_multipart_signed(self):
        self.unlock()
        self.assertEqual(self.backend.send(self.draft(to=['partner@external.example'], encrypt=False, sign=True))['state'], 'Accepted')
        payload = self.backend.adapter.sent_payloads[-1]
        self.assertEqual(BytesParser(policy=policy.default).parsebytes(payload).get_content_type(), 'multipart/signed')
        self.backend.cache.put_body('sent3', payload)
        message = self.backend.open_message('sent3')
        self.assertEqual((message['encryption'], message['signature']['status']), ('none', 'valid'))
        self.assertIn('请勿外传', message['html'])
        self.assertEqual([a['name'] for a in message['attachments']], [])  # smime.p7s is not an attachment.

    def test_certificate_for_another_address_blocks_the_send(self):
        self.unlock('lihua@example.invalid')
        result = self.backend.send(self.draft(to=['partner@external.example'], encrypt=False, sign=True))
        self.assertEqual((result['state'], result['failure']), ('Failed', 'SIGN_CERT_ADDRESS_MISMATCH'))
        self.assertEqual(self.backend.adapter.sent_payloads, [])


if __name__ == '__main__':
    unittest.main()
