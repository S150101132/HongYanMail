import tempfile
import unittest
from coremail_desktop.models import DraftDocument
from coremail_desktop.outbox.service import OutboxService
from coremail_desktop.outbox.journal import SendJournal

class OutboxTests(unittest.TestCase):
    def test_failed_preparation_can_retry_same_revision_without_duplicate_delivery(self):
        from coremail_desktop.errors import AppError
        class Adapter:
            attempts=0; deliveries=0
            def create_compose(self,draft):
                self.attempts+=1
                if self.attempts==1: raise AppError('NETWORK_ERROR')
                return 'prepared'
            def deliver(self,*args): self.deliveries+=1; return {'code':'S_OK'}
        with tempfile.TemporaryDirectory() as directory:
            adapter=Adapter(); service=OutboxService(adapter,SendJournal(directory))
            draft=DraftDocument('retry',1,'new','me@example.test',('to@example.test',),(),(),'s','body',{})
            failed=service.request_send(draft)
            self.assertEqual(service.journal.load(failed).state,'Failed')
            accepted=service.request_send(draft)
            self.assertEqual(service.journal.load(accepted).state,'Accepted')
            self.assertEqual(service.request_send(draft),accepted)
            self.assertEqual(adapter.deliveries,1)

    def test_preparation_failure_retains_fixed_reason_without_exception_text(self):
        from coremail_desktop.errors import AppError
        class Adapter:
            def create_compose(self,draft): raise AppError('REPLY_SOURCE_MISMATCH')
        with tempfile.TemporaryDirectory() as directory:
            service=OutboxService(Adapter(),SendJournal(directory))
            draft=DraftDocument('failed',1,'new','me@example.test',('to@example.test',),(),(),'s','body',{})
            job=service.journal.load(service.request_send(draft))
            self.assertEqual(getattr(job,'failure_code',None),'REPLY_SOURCE_MISMATCH')

    def test_retry_is_not_stuck_when_recording_preparation_failure_hits_disk_error(self):
        from coremail_desktop.errors import AppError
        class Adapter:
            attempts=0
            def create_compose(self,draft):
                self.attempts+=1
                if self.attempts==1: raise AppError('NETWORK_ERROR')
                return 'prepared'
            def deliver(self,*args): return {'code':'S_OK'}
        with tempfile.TemporaryDirectory() as directory:
            journal=SendJournal(directory); persist=journal.persist
            def faulty(job):
                if job.state=='Failed': raise OSError('simulated disk error')
                persist(job)
            journal.persist=faulty
            service=OutboxService(Adapter(),journal)
            draft=DraftDocument('retrydisk',1,'new','me@example.test',('to@example.test',),(),(),'s','body',{})
            with self.assertRaises(OSError):service.request_send(draft)
            accepted=service.request_send(draft)
            self.assertEqual(journal.load(accepted).state,'Accepted')

    def test_expired_auth_during_preparation_propagates_after_recording_no_send(self):
        from coremail_desktop.errors import AppError
        class Adapter:
            def create_compose(self,draft): raise AppError('SESSION_EXPIRED')
        with tempfile.TemporaryDirectory() as directory:
            service=OutboxService(Adapter(),SendJournal(directory))
            draft=DraftDocument('expired',1,'new','me@example.test',('to@example.test',),(),(),'s','body',{})
            with self.assertRaises(AppError) as raised:service.request_send(draft)
            self.assertEqual(raised.exception.code,'SESSION_EXPIRED')
            self.assertEqual(service.journal.recover()[0].state,'Failed')

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
