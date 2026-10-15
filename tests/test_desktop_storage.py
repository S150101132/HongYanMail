import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from coremail_desktop.models import DraftDocument, SendJob
from coremail_desktop.drafts.store import DraftStore
from coremail_desktop.outbox.journal import SendJournal
from coremail_desktop.storage.dpapi import DataProtector

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.draft = DraftDocument('draft1', 2, 'new', 'me@example.test', ('you@example.test',), (), (), 'secret subject', 'secret body', {})
    def test_draft_roundtrip_and_encrypted_bytes(self):
        store = DraftStore(self.path)
        self.assertEqual(store.save(self.draft), 2)
        self.assertEqual(store.load('draft1'), self.draft)
        data = b''.join(p.read_bytes() for p in self.path.iterdir())
        for value in (b'secret subject', b'secret body', b'you@example.test'):
            self.assertNotIn(value, data)
        with self.assertRaises(Exception): store.save(replace(self.draft, revision=1))
        self.assertEqual(store.load('draft1').revision, 2)
    def test_dpapi_roundtrip(self):
        protector = DataProtector()
        ciphertext = protector.protect(b'private')
        self.assertNotIn(b'private', ciphertext)
        self.assertEqual(protector.unprotect(ciphertext), b'private')
        with self.assertRaises(Exception): protector.unprotect(b'invalid')
    def test_recover_submitting_is_unknown(self):
        journal = SendJournal(self.path)
        journal.persist(SendJob('job1', self.draft, state='Submitting', payload=b'encrypted payload'))
        job, = journal.recover()
        self.assertEqual(job.state, 'Unknown')
        self.assertEqual(job.payload, b'encrypted payload')
    def test_failed_replace_preserves_record(self):
        from unittest.mock import patch
        store = DraftStore(self.path)
        store.save(self.draft)
        with patch('coremail_desktop.drafts.store.os.replace', side_effect=OSError('disk')):
            with self.assertRaises(Exception): store.save(replace(self.draft, revision=3))
        self.assertEqual(store.load('draft1'), self.draft)
    def test_unprotect_failure_keeps_original_record(self):
        store = DraftStore(self.path)
        store.save(self.draft)
        path = self.path / 'draft1.bin'
        before = path.read_bytes()
        from unittest.mock import patch
        with patch.object(store.protector,'unprotect',side_effect=RuntimeError('cannot decrypt')):
            with self.assertRaises(Exception): store.load('draft1')
            with self.assertRaises(Exception): store.save(replace(self.draft,revision=3))
        self.assertEqual(path.read_bytes(), before)
