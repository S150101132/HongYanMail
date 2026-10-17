"""Encrypted sending: colleagues by company certificate, never a silent plaintext fallback."""
import base64
import shutil
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter, demo_certificate
from coremail_desktop.crypto import cms
from coremail_desktop.crypto.service import CryptoService
from coremail_desktop.errors import AppError
from coremail_desktop.protocol.coremail import CoremailAdapter
from tests.test_app_backend import XorProtector, local_services_stub


def decrypt_for(email, payload):
    _der, key, _cert = demo_certificate(email)
    outer = BytesParser(policy=policy.default).parsebytes(payload)
    plain, _algorithm = cms.decrypt_message(outer.get_payload(decode=True), key)
    return outer, BytesParser(policy=policy.default).parsebytes(plain)


class EncryptedSendTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.backend = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(),
                                   services_factory=local_services_stub, crypto=CryptoService())
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw')
        self.addCleanup(self.backend.logout)

    def draft(self, **values):
        draft = self.backend.compose('new')
        draft.update(subject='季度预算', html='<p>内部资料 <b>请勿外传</b></p>', **values)
        return draft

    def test_internal_domains_come_from_the_account(self):
        self.assertEqual(self.backend.profile()['internalDomains'], ['example.invalid'])

    def test_colleagues_receive_a_real_envelope_readable_by_recipient_and_sender(self):
        draft = self.draft(to=['zhangxm@example.invalid'], bcc=['lihua@example.invalid'], encrypt=True)
        draft['attachments'] = [{'name': '预算.txt', 'type': 'text/plain', 'size': 6, 'data': base64.b64encode('预算'.encode()).decode()}]
        self.assertEqual(self.backend.send(draft)['state'], 'Accepted')
        payload = self.backend.adapter.sent_payloads[-1]
        self.assertNotIn('请勿外传'.encode('utf-8'), payload)
        outer, inner = decrypt_for('zhangxm@example.invalid', payload)
        self.assertEqual(outer.get_content_type(), 'application/pkcs7-mime')
        self.assertEqual(outer.get_param('smime-type'), 'enveloped-data')
        self.assertIsNone(outer['Bcc'])
        self.assertEqual(outer['x-cm-smime'], 'encrypted')
        html = inner.get_body(preferencelist=('html',)).get_content()
        self.assertIn('请勿外传', html)
        self.assertEqual([part.get_filename() for part in inner.iter_attachments()], ['预算.txt'])
        decrypt_for('lihua@example.invalid', payload)   # Bcc recipient can read it.
        decrypt_for('demo@example.invalid', payload)    # The sender's sent copy stays readable.
        self.assertTrue(self.backend.adapter.sent[-1].encryption_required)

    def test_recipient_without_certificate_needs_a_decision(self):
        draft = self.draft(to=['zhangxm@example.invalid', 'partner@external.example'], encrypt=True)
        result = self.backend.send(draft)
        self.assertEqual(result['state'], 'NeedsCertificates')
        self.assertEqual(result['missing'], ['partner@external.example'])
        self.assertEqual(self.backend.adapter.sent, [])
        self.assertEqual(len(self.backend.list_drafts()), 1)  # Nothing lost.
        draft.update(encrypt=False, revision=result['revision'])
        self.assertEqual(self.backend.send(draft)['state'], 'Accepted')
        sent = self.backend.adapter.sent[-1]
        self.assertFalse(sent.encryption_required)

    def test_unencrypted_send_stays_on_the_plain_path(self):
        self.backend.send(self.draft(to=['partner@external.example'], encrypt=False))
        self.assertEqual(self.backend.adapter.sent_payloads, [])

    def test_missing_own_certificate_blocks_the_send(self):
        original = self.backend.adapter.public_certificates
        self.backend.adapter.public_certificates = lambda emails: {k: v for k, v in original(emails).items() if k != 'demo@example.invalid'}
        result = self.backend.send(self.draft(to=['zhangxm@example.invalid'], encrypt=True))
        self.assertEqual(result['state'], 'Failed')
        self.assertEqual(result['failure'], 'OWN_CERT_MISSING')
        self.assertEqual(self.backend.adapter.sent, [])

    def test_reply_to_plain_mail_leaves_the_choice_to_the_recipients(self):
        row = self.backend.messages('1', 0, 5)['rows'][0]
        self.assertIsNone(self.backend.compose('reply', row['id'])['encrypt'])

    def test_certificate_is_remembered_with_dpapi_style_protection(self):
        _der, key, cert = demo_certificate('demo@example.invalid')
        pfx = Path(self.directory) / 'me.pfx'
        pfx.write_bytes(pkcs12.serialize_key_and_certificates(b'me', key, cert, None, serialization.BestAvailableEncryption(b'secret')))
        status = self.backend.unlock_certificate(str(pfx), 'secret', remember=True)
        self.assertTrue(status['unlocked'] and status['remembered'])
        self.assertNotIn(b'secret', (Path(self.directory) / 'certificate.bin').read_bytes())
        again = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(),
                            services_factory=local_services_stub, crypto=CryptoService())
        again.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw')
        self.addCleanup(again.logout)
        self.assertTrue(again.certificate_status()['unlocked'])
        self.assertFalse(again.forget_certificate()['remembered'])


class DirectoryCertificateTest(unittest.TestCase):
    def test_only_certificates_naming_the_address_are_accepted(self):
        good, _, _ = demo_certificate('a@example.invalid')
        other, _, _ = demo_certificate('b@example.invalid')

        class Session:
            def call(self, func, payload):
                self.payload = (func, payload)
                return {'code': 'S_OK', 'var': {
                    'a@example.invalid': {'publicKey': base64.b64encode(good).decode()},
                    'c@example.invalid': {'publicKey': base64.b64encode(other).decode()},  # Wrong owner.
                    'd@example.invalid': {'publicKey': ''},
                    'e@example.invalid': {'publicKey': 'bm90IGEgY2VydA=='}}}
        adapter = CoremailAdapter()
        adapter._session = Session()
        found = adapter.public_certificates(['a@example.invalid', 'c@example.invalid', 'd@example.invalid', 'e@example.invalid'])
        self.assertEqual(list(found), ['a@example.invalid'])
        self.assertEqual(adapter._session.payload[0], 'cert:listPublicKey')
        self.assertEqual(adapter._session.payload[1]['userList'][0], 'a@example.invalid')

    def test_plain_compose_path_refuses_encrypted_drafts(self):
        from coremail_desktop.models import DraftDocument
        adapter = CoremailAdapter()
        draft = DraftDocument('d', 1, 'new', 'me@example.invalid', ('a@example.invalid',), (), (), 's', 'b', {}, True)
        with self.assertRaises(AppError) as raised:
            adapter.create_compose(draft)
        self.assertEqual(raised.exception.code, 'ENCRYPTED_SUBMISSION_UNVERIFIED')


if __name__ == '__main__':
    unittest.main()
