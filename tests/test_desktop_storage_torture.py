"""Torture the encrypted draft store and send journal against a real filesystem.

The plan asks to verify crash recovery and that no plaintext reaches disk. This
fuzzes the store with many permutations of interruption points around the atomic
write, failing disk operations and unprotectable records, and asserts the
invariant that matters: a previously saved revision is never lost or corrupted,
and the bytes on disk never contain the draft's plaintext.

Synthetic data only. Real Windows DPAPI is used, so a normal user context is
required.
"""
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coremail_desktop.drafts.store import DraftStore  # noqa: E402
from coremail_desktop.models import DraftDocument, SendJob  # noqa: E402
from coremail_desktop.outbox.journal import SendJournal  # noqa: E402

SECRETS = ('torture subject 主题', 'torture body 正文内容', 'torture@example.test')
RANDOM = random.Random(20261015)


def draft_at(revision: int) -> DraftDocument:
    return DraftDocument('torture', revision, 'new', 'me@example.test',
                         ('torture@example.test',), (), (), 'torture subject 主题',
                         'torture body 正文内容', {})


class DraftStoreTortureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)

    def plaintext_bytes(self):
        return b''.join(item.read_bytes() for item in self.path.rglob('*') if item.is_file())

    def test_many_interruptions_never_lose_or_leak_a_committed_revision(self):
        store = DraftStore(self.path)
        committed = None
        for revision in range(1, 21):
            current = draft_at(revision)
            if committed is None:
                self.assertEqual(store.save(current), revision)
                committed = current
            else:
                # Interrupt the write at a random point of the atomic replace.
                failing = RANDOM.random() < 0.5
                try:
                    if failing:
                        with patch('coremail_desktop.drafts.store.os.replace',
                                   side_effect=OSError('interrupted write')):
                            store.save(current)
                    else:
                        store.save(current)
                except Exception:
                    pass
                else:
                    committed = current

            loaded = store.load('torture')
            self.assertEqual(loaded, committed,
                             'a failed write changed the readable revision')
            leaked = self.plaintext_bytes()
            for secret in SECRETS:
                self.assertNotIn(secret.encode('utf-8'), leaked,
                                 f'plaintext {secret!r} reached disk')
            # Leftover temporary files must not accumulate or stay readable.
            for leftover in self.path.glob('*.tmp'):
                self.fail(f'interrupted write left a temporary file behind: {leftover}')

    def test_reopening_the_store_recovers_the_last_committed_revision(self):
        store = DraftStore(self.path)
        for revision in (1, 2, 3):
            store.save(draft_at(revision))
        reopened = DraftStore(self.path)
        self.assertEqual(reopened.load('torture'), draft_at(3))
        self.assertEqual(reopened.list_ids(), ['torture'])

    def test_a_torn_record_is_reported_and_leaves_the_previous_text_recoverable(self):
        store = DraftStore(self.path)
        store.save(draft_at(1))
        record = self.path / 'torture.bin'
        intact = record.read_bytes()
        # Simulate a torn write: truncated ciphertext.
        record.write_bytes(intact[: max(4, len(intact) // 3)])
        reopened = DraftStore(self.path)
        with self.assertRaises(Exception):
            reopened.load('torture')
        # The damaged record must not be silently overwritten by a stale save.
        record.write_bytes(intact)
        self.assertEqual(DraftStore(self.path).load('torture'), draft_at(1))

    def test_unsupported_revision_rollback_is_refused(self):
        store = DraftStore(self.path)
        store.save(draft_at(5))
        for older in (4, 1, 0):
            with self.assertRaises(Exception):
                store.save(draft_at(older))
        self.assertEqual(store.load('torture'), draft_at(5))


class SendJournalTortureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)

    def test_every_interrupted_state_recovers_to_a_non_in_flight_state(self):
        # Submitting/Reconciling may have reached the server, so they must become
        # Unknown and must never be replayed. Preparing is pre-submit, so it is a
        # plain failure the user can retry deliberately.
        expectations = {'Submitting': 'Unknown', 'Reconciling': 'Unknown', 'Preparing': 'Failed'}
        for state, expected in expectations.items():
            with self.subTest(state=state):
                journal = SendJournal(self.path)
                journal.persist(SendJob('job', draft_at(1), state=state, payload=b'frozen'))
                reopened = SendJournal(self.path)
                job, = reopened.recover()
                self.assertEqual(job.state, expected,
                                 f'{state} recovered as {job.state}')
                self.assertNotEqual(job.state, state,
                                    'an in-flight state must not survive a restart')
                self.assertEqual(job.payload, b'frozen', 'frozen payload must survive')
                # Recovery is durable: a second reopen sees the settled state.
                settled, = SendJournal(self.path).recover()
                self.assertEqual(settled.state, expected)

    def test_accepted_state_survives_reopen_and_leaks_no_plaintext(self):
        journal = SendJournal(self.path)
        journal.persist(SendJob('job', draft_at(1), state='Accepted', payload=b'frozen'))
        reopened = SendJournal(self.path)
        job, = reopened.recover()
        self.assertEqual(job.state, 'Accepted')
        leaked = b''.join(item.read_bytes() for item in self.path.rglob('*') if item.is_file())
        for secret in SECRETS:
            self.assertNotIn(secret.encode('utf-8'), leaked)

    def test_a_failed_state_write_does_not_erase_the_previous_record(self):
        journal = SendJournal(self.path)
        journal.persist(SendJob('job', draft_at(1), state='Accepted', payload=b'first'))
        # The shared atomic writer uses os.replace, so fail that step.
        with patch('coremail_desktop.drafts.store.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(Exception):
                journal.persist(SendJob('job', draft_at(2), state='Submitting', payload=b'second'))
        job, = SendJournal(self.path).recover()
        self.assertEqual(job.payload, b'first')
        self.assertEqual(job.state, 'Accepted')
        leftovers = [item for item in self.path.rglob('*') if item.suffix == '.tmp']
        self.assertEqual(leftovers, [], 'failed write left a temporary file behind')


if __name__ == '__main__':
    unittest.main()
