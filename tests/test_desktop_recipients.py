import unittest
from coremail_desktop.models import MessageDocument
from coremail_desktop.compose.recipients import normalize_recipients
from coremail_desktop.compose.service import build_reply, build_forward

class RecipientTests(unittest.TestCase):
    def test_normalize_and_header_injection(self):
        self.assertEqual(normalize_recipients(('Name <A@example.test>', 'a@example.test')), ('A@example.test',))
        with self.assertRaises(Exception): normalize_recipients(('a@example.test\r\nBcc: bad@example.test',))
        with self.assertRaises(Exception): normalize_recipients(('not email',))
    def test_reply_prefers_reply_to_excludes_self_and_bcc(self):
        original = MessageDocument('m', 'digest', 'body', None, (), {'reply-to': ('reply@example.test',), 'from': ('sender@example.test',), 'to': ('me@example.test, other@example.test',), 'cc': ('other@example.test, cc@example.test',), 'bcc': ('hidden@example.test',), 'message-id': ('<original@example.test>',), 'subject': ('Topic',)})
        draft = build_reply(original, {'me@example.test'}, True)
        self.assertEqual(draft.to, ('reply@example.test','other@example.test'))
        self.assertEqual(draft.cc, ('cc@example.test',))
        self.assertEqual(draft.bcc, ())
        self.assertEqual(draft.reference_headers['In-Reply-To'], '<original@example.test>')
        self.assertIn('正文', build_forward(original, 'me@example.test').body)
