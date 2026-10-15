import unittest
from coremail_desktop.models import DraftDocument,MessageDocument

class ModelTests(unittest.TestCase):
    def test_draft_freezes_caller_owned_collections(self):
        to = ['to@example.test']
        refs = {'References':'<first>'}
        draft = DraftDocument('d',1,'new','me@example.test',to,[],[],'s','body',refs)
        to.append('later@example.test'); refs['References'] = '<later>'
        self.assertEqual(draft.to,('to@example.test',))
        self.assertEqual(draft.reference_headers['References'],'<first>')
        with self.assertRaises(TypeError): draft.reference_headers['References'] = 'change'
    def test_document_freezes_header_values(self):
        values = ['subject']
        document = MessageDocument('m','digest','body',None,[],{'subject':values})
        values.append('later')
        self.assertEqual(document.headers['subject'],('subject',))
