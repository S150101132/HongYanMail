import tempfile
import unittest
from coremail_desktop.models import DraftDocument
from coremail_desktop.outbox.service import OutboxService
from coremail_desktop.outbox.journal import SendJournal

class OutboxTests(unittest.TestCase):
    def test_deliver_never_runs_before_submitting_snapshot_is_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = SendJournal(directory)
            persist = journal.persist
            def fail_submitting(job):
                if job.state == 'Submitting': raise OSError('disk failure')
                persist(job)
            journal.persist = fail_submitting
            class Adapter:
                calls = 0
                def create_compose(self,draft): return 'compose'
                def deliver(self,*args): self.calls += 1
            adapter = Adapter()
            service = OutboxService(adapter,journal)
            draft = DraftDocument('d',1,'new','me@example.test',('to@example.test',),(),(),'s','body',{})
            with self.assertRaises(OSError): service.request_send(draft)
            self.assertEqual(adapter.calls,0)
    def test_crash_after_accept_before_local_update_recovers_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = SendJournal(directory)
            persist = journal.persist
            def fail_accepted(job):
                if job.state == 'Accepted': raise OSError('crash')
                persist(job)
            journal.persist = fail_accepted
            class Adapter:
                def create_compose(self,draft): return 'compose'
                def deliver(self,*args): return {'code':'S_OK'}
            service = OutboxService(Adapter(),journal)
            draft = DraftDocument('d',1,'new','me@example.test',('to@example.test',),(),(),'s','body',{})
            from coremail_desktop.errors import AppError
            with self.assertRaises(AppError) as raised: service.request_send(draft)
            self.assertEqual(raised.exception.code,'SEND_OUTCOME_UNKNOWN')
            self.assertEqual(raised.exception.category,'unknown')
            self.assertEqual(journal.recover()[0].state,'Unknown')
    def test_double_click_and_lost_response_never_replays(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = SendJournal(directory)
            class Adapter:
                calls = 0
                def create_compose(self, draft): return 'cid'
                def deliver(self, cid, draft):
                    self.calls += 1
                    self_test.assertEqual(journal.recover()[0].state, 'Unknown')
                    raise TimeoutError()
                def find_sent_candidates(self, job, limit=50): return []
            self_test = self
            adapter = Adapter()
            service = OutboxService(adapter,journal)
            draft = DraftDocument('d',1,'new','me@example.test',('to@example.test',),(),(),'s','body',{})
            jid = service.request_send(draft)
            self.assertEqual(service.request_send(draft), jid)
            self.assertEqual(adapter.calls, 1)
            self.assertEqual(service.recover_pending()[0].state,'Unknown')
            self.assertEqual(service.reconcile(jid).state,'Unknown')
    def test_encryption_requires_explicit_preparer(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as directory:
            class Adapter:
                def create_compose(self, draft): raise AssertionError('plaintext forbidden')
            service = OutboxService(Adapter(), SendJournal(directory))
            draft = DraftDocument('d',1,'new','me@example.test',('to@example.test',),(),(),'s','body',{},True)
            jid = service.request_send(draft)
            self.assertEqual(service.journal.load(jid).state, 'Failed')
