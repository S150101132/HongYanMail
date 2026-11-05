"""A mail over the download limit is read part by part (mbox:readMessage + getMessageData)."""
import shutil
import tempfile
import unittest

from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter
from coremail_desktop.errors import AppError
from test_app_backend import XorProtector, local_services_stub

GIF = b'GIF89a\x01\x00\x01\x00\x00\x00\x00;'


class LargeAdapter(DemoAdapter):
    structure = None

    def fetch_mime(self, message_id, mboxa=''):
        raise AppError('RESPONSE_TOO_LARGE')

    def read_structure(self, message_id, mboxa=''):
        self.read_calls = getattr(self, 'read_calls', 0) + 1
        return LargeAdapter.structure

    def fetch_part(self, message_id, part, mboxa=''):
        self.parts = getattr(self, 'parts', []) + [part]
        return {'1': '正文文本'.encode('utf-8'), '2': '<p>你好<img src="cid:logo@x"><script>x()</script></p>'.encode('gb18030'),
                '3': GIF, '4': b'PK\x03\x04 big file'}[part]


STRUCTURE = {'subject': 'CRM', 'from': ['"甲" <a@example.invalid>'], 'to': ['b@example.invalid'], 'cc': [],
             'text': {'id': 1, 'contentType': 'text/plain'}, 'html': {'id': 2, 'contentType': 'text/html'},
             'attachments': [{'id': 3, 'filename': 'logo.gif', 'contentType': 'image/gif', 'contentId': '<logo@x>', 'estimateSize': 14},
                             {'id': 4, 'filename': '升级包.zip', 'contentType': 'application/zip', 'estimateSize': 70000000}]}


class LargeMessageTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory, True)
        LargeAdapter.structure = STRUCTURE
        self.backend = MailBackend(self.directory, adapter_factory=LargeAdapter, protector=XorProtector(),
                                   services_factory=local_services_stub)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw')
        self.mid = self.backend.messages('1', 0, 1)['rows'][0]['id']

    def tearDown(self):
        self.backend.logout()

    def test_body_and_shown_pictures_load_without_the_attachments(self):
        m = self.backend.open_message(self.mid, mark_read=False)
        self.assertEqual(m['subject'], 'CRM')
        self.assertIn('你好', m['html'])
        self.assertIn('data:image/gif;base64,', m['html'])
        self.assertNotIn('script', m['html'])
        self.assertEqual(m['text'], '正文文本')
        self.assertEqual([(a['part'], a['name'], a['size']) for a in m['attachments']], [('4', '升级包.zip', 70000000)])
        self.assertEqual(m['encryption'], 'none')
        self.assertNotIn('4', self.backend.adapter.parts)

    def test_an_attachment_is_fetched_when_asked(self):
        self.backend.open_message(self.mid, mark_read=False)
        self.assertEqual(self.backend.attachment(self.mid, '4'), ('升级包.zip', 'application/zip', b'PK\x03\x04 big file'))
        with self.assertRaises(AppError):
            self.backend.attachment(self.mid, '9')

    def test_mail_without_a_readable_body_stays_refused(self):
        LargeAdapter.structure = {'subject': 'x', 'attachments': [{'id': 1, 'filename': 'smime.p7m', 'contentType': 'application/pkcs7-mime'}]}
        with self.assertRaises(AppError) as raised:
            self.backend.open_message(self.mid, mark_read=False)
        self.assertEqual(raised.exception.code, 'RESPONSE_TOO_LARGE')


if __name__ == '__main__':
    unittest.main()
