"""Scheduled send, recall, read receipts and delivery status: adapter contracts and backend flows."""
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter
from coremail_desktop.compose.tracking import delivery_state, recall_state
from coremail_desktop.errors import AppError
from coremail_desktop.models import DraftDocument
from coremail_desktop.protocol.coremail import CoremailAdapter, account_zone
from tests.test_app_backend import XorProtector, local_services_stub

ME = 'me@example.test'


def draft(**extra):
    return DraftDocument('d1', 1, 'new', ME, ('you@example.test',), (), (), 's', '<p>b</p>', {}, is_html=True, **extra)


class FakeSession:
    """Answers like the deployment did in analysis/send-features-shape*-2026-10-17.json."""
    def __init__(self, zone='Asia/Shanghai'):
        self.calls, self.zone = [], zone
        self.drafts = [{'id': '2:sched', 'subject': 's', 'from': ME, 'sentDate': '2026-10-18 09:00:00',
                        'flags': {'read': True, 'scheduleDelivery': True}}]
        self.save_unschedules = True
        self.answers = {}

    def call(self, op, params):
        self.calls.append((op, params))
        if op == 'user:getAttrs':
            return {'code': 'S_OK', 'var': {'time_zone': self.zone}}
        if op == '%mbox/compose':
            if params['action'] == 'continue':
                return {'code': 'S_OK', 'var': 'cid1'}
            if params['action'] == 'schedule':
                return {'code': 'S_OK', **self.answers.get('schedule', {'scheduledSent': {'mid': 'x', 'msid': 2}})}
            if params['action'] == 'save':
                if self.save_unschedules:
                    self.drafts[0]['flags']['scheduleDelivery'] = False
                return {'code': 'S_OK', 'draftId': '2:new'}
            return {'code': 'S_OK', 'savedSent': {'mid': 'x'}}
        if op == 'mbox:listMessages':
            return {'code': 'S_OK', 'var': [dict(d) for d in self.drafts] if params['fid'] == 2 else [], 'total': 1}
        if op == 'user:listDeliveryHistory':
            return {'code': 'S_OK', 'total': 2, 'var': [
                {'to': 'a@example.test', 'to_name': 'A', 'result': 509, 'rclResult': 0, 'recallable': False, 'modtime': '2026-10-17 10:00:00'},
                {'to': 'b@example.test', 'to_name': '', 'result': 100, 'rclResult': 0, 'recallable': True, 'modtime': '2026-10-17 10:01:00'},
                'junk']}
        if op == 'mbox:getMessageInfos':
            return {'code': 'S_OK', 'var': [{'id': params['ids'][0], 'tinfo': '1:2:777'}]}
        raise AssertionError(op)

    def call_code(self, op, params):
        self.calls.append((op, params))
        return self.answers[op]

    def open_draft(self, mid):
        self.calls.append(('open_draft', mid))
        return {'id': 'cid9', 'account': ME, 'to': ['you@example.test'], 'subject': 's', 'content': 'c', 'isHtml': True,
                'scheduleDate': '2026-10-18 14:00:00', 'deliveryMsgType': 0}


def adapter(session):
    result = CoremailAdapter()
    result._session = session
    return result


class ScheduleAdapterTest(unittest.TestCase):
    def test_schedule_uses_account_zone_and_requires_the_stored_copy(self):
        session = FakeSession()
        a = adapter(session)
        response = a.deliver('cid1', draft(schedule_at='2026-10-18T06:00:00Z'))
        self.assertIn('scheduledSent', response)
        op, params = session.calls[-1]
        self.assertEqual((op, params['action'], params['attrs']['scheduleDate']), ('%mbox/compose', 'schedule', '2026-10-18 14:00:00'))
        session.answers['schedule'] = {}
        with self.assertRaises(AppError) as raised:
            a.deliver('cid1', draft(schedule_at='2026-10-18T06:00:00Z'))
        self.assertEqual(raised.exception.code, 'INVALID_RESPONSE')  # The outbox records Unknown, never resends.

    def test_unknown_or_daylight_saving_zone_refuses_to_guess(self):
        a = adapter(FakeSession(zone='Europe/Berlin'))
        with self.assertRaises(AppError) as raised:
            a.deliver('cid1', draft(schedule_at='2026-10-18T06:00:00Z'))
        self.assertEqual(raised.exception.code, 'SCHEDULE_ZONE_UNSUPPORTED')
        self.assertEqual(account_zone('GMT+08:00').utcoffset(None), timedelta(hours=8))
        self.assertEqual(account_zone('Asia/Kolkata').utcoffset(None), timedelta(hours=5, minutes=30))

    def test_plain_send_stays_deliver_and_receipt_goes_on_both_steps(self):
        session = FakeSession()
        a = adapter(session)
        d = draft(request_receipt=True)
        a.create_compose(d)
        a.deliver('cid1', d)
        composes = [p for op, p in session.calls if op == '%mbox/compose']
        self.assertEqual([p['action'] for p in composes], ['continue', 'deliver'])
        self.assertTrue(all(p['attrs'].get('requestReadReceipt') is True for p in composes))
        self.assertNotIn('requestReadReceipt', a._attrs(draft()))

    def test_scheduled_list_reads_the_moment_in_the_response_zone(self):
        rows = adapter(FakeSession()).scheduled_messages()
        self.assertEqual([(r.remote_id, r.schedule_at) for r in rows], [('2:sched', '2026-10-18T06:00:00Z')])

    def test_unschedule_resaves_without_date_and_checks_the_list(self):
        session = FakeSession()
        self.assertEqual(adapter(session).unschedule('2:sched')['draft_id'], '2:new')
        save = next(p for op, p in session.calls if op == '%mbox/compose' and p['action'] == 'save')
        self.assertEqual(save['id'], 'cid9')
        self.assertIsNone(save['attrs']['scheduleDate'])
        self.assertEqual(save['attrs']['content'], 'c')
        self.assertNotIn('deliveryMsgType', save['attrs'])
        with self.assertRaises(AppError) as raised:
            adapter(session).unschedule('2:sched')  # No longer scheduled.
        self.assertEqual(raised.exception.code, 'NOT_SCHEDULED')

    def test_unschedule_not_reflected_by_the_server_is_unconfirmed(self):
        session = FakeSession()
        session.save_unschedules = False
        with self.assertRaises(AppError) as raised:
            adapter(session).unschedule('2:sched')
        self.assertEqual(raised.exception.code, 'SCHEDULE_UNCONFIRMED')


class RecallAdapterTest(unittest.TestCase):
    def test_follows_webmail_cancel_then_recall_with_tid(self):
        session = FakeSession()
        session.answers = {'mbox:cancelDeliver': {'code': 'FA_MAIL_NOT_FOUND'},
                           'mbox:recallMessage2': {'code': 'S_OK', 'var': {'a@example.test': 2, 'b@example.test': 5}}}
        result = adapter(session).recall_message('3:abc')
        self.assertEqual(result, {'cancelled': False, 'states': {'a@example.test': 2, 'b@example.test': 5}})
        self.assertIn(('mbox:cancelDeliver', {'tinfo': '1:2:777', 'mid': '3:abc'}), session.calls)
        self.assertIn(('mbox:recallMessage2', {'mid': '3:abc', 'tid': '777'}), session.calls)

    def test_cancelled_delivery_is_reported_as_such(self):
        session = FakeSession()
        session.answers = {'mbox:cancelDeliver': {'code': 'S_OK'}}
        self.assertEqual(adapter(session).recall_message('3:abc'), {'cancelled': True, 'states': {}})

    def test_refusals_and_empty_answers_are_never_success(self):
        for code, expected in (('FA_UNSUPPORT_RECALL', 'RECALL_UNSUPPORTED'), ('FA_MAIL_EXPIRED', 'RECALL_EXPIRED'),
                               ('FS_MTA_N11', 'RECALL_LATER'), ('FA_SOMETHING_NEW', 'RECALL_UNCONFIRMED')):
            session = FakeSession()
            session.answers = {'mbox:cancelDeliver': {'code': 'FA_MAIL_NOT_FOUND'}, 'mbox:recallMessage2': {'code': code}}
            with self.assertRaises(AppError) as raised:
                adapter(session).recall_message('3:abc')
            self.assertEqual(raised.exception.code, expected)
        session = FakeSession()
        session.answers = {'mbox:cancelDeliver': {'code': 'FA_MAIL_NOT_FOUND'}, 'mbox:recallMessage2': {'code': 'S_OK', 'var': {}}}
        with self.assertRaises(AppError) as raised:
            adapter(session).recall_message('3:abc')
        self.assertEqual(raised.exception.code, 'RECALL_UNCONFIRMED')

    def test_delivery_history_keeps_only_well_formed_rows(self):
        rows = adapter(FakeSession()).delivery_history('3:abc')
        self.assertEqual([(r['to'], r['result'], r['recallable']) for r in rows], [('a@example.test', 509, False), ('b@example.test', 100, True)])


class TrackingTest(unittest.TestCase):
    def test_codes_follow_webmail_tables(self):
        self.assertEqual(delivery_state(509)['kind'], 'read')  # Verified: read copy, 509 % 100 == 9.
        self.assertEqual(delivery_state(100), {'text': '已投递到对方邮箱', 'kind': 'ok'})
        self.assertEqual(delivery_state(100, 2)['text'], '已撤回')
        self.assertEqual(delivery_state(None)['kind'], 'wait')
        self.assertEqual(delivery_state(777)['kind'], 'wait')
        self.assertTrue(recall_state(3)['recalled'])
        self.assertFalse(recall_state(5)['recalled'])
        self.assertFalse(recall_state('2')['recalled'])
        self.assertFalse(recall_state(99)['recalled'])


class BackendSendFeaturesTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.backend = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(),
                                   services_factory=local_services_stub)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.backend.folders()

    def tearDown(self):
        self.backend.logout()

    def new(self, **extra):
        value = self.backend.compose('new')
        value.update(to=['rd@example.invalid'], subject='定时', html='<p>x</p>', encrypt=False, **extra)
        return value

    def test_scheduled_send_lists_and_cancels(self):
        at = int((time.time() + 3600) * 1000) // 1000 * 1000
        result = self.backend.send(self.new(scheduleAt=at, receipt=True))
        self.assertEqual((result['state'], result['scheduledAt']), ('Accepted', at))
        sent = self.backend.adapter.sent[-1]
        self.assertTrue(sent.request_receipt)
        self.assertEqual(sent.schedule_at, datetime.fromtimestamp(at / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
        rows = self.backend.scheduled()['rows']
        mine = next(r for r in rows if r['subject'] == '定时')
        self.assertEqual(mine['scheduleAt'], at)
        opened = self.backend.open_message(mine['id'])
        self.assertTrue(opened['scheduled'])
        self.assertTrue(self.backend.unschedule(mine['id']))
        self.assertNotIn(mine['id'], [r['id'] for r in self.backend.scheduled()['rows']])
        self.assertFalse(self.backend.open_message(mine['id'])['scheduled'])

    def test_schedule_in_the_past_or_encrypted_is_refused_before_submission(self):
        for extra, code in (({'scheduleAt': int(time.time() * 1000)}, 'INVALID_SCHEDULE'),
                            ({'scheduleAt': int((time.time() + 400 * 86400) * 1000)}, 'INVALID_SCHEDULE'),
                            ({'scheduleAt': 'tomorrow'}, 'INVALID_SCHEDULE'),
                            ({'scheduleAt': int((time.time() + 3600) * 1000), 'encrypt': True}, 'SCHEDULE_ENCRYPTED_UNSUPPORTED')):
            value = self.new()
            value.update(extra)
            with self.assertRaises(AppError) as raised:
                self.backend.send(value)
            self.assertEqual(raised.exception.code, code)
        self.assertEqual(self.backend.adapter.sent, [])

    def test_draft_keeps_schedule_and_receipt(self):
        at = int((time.time() + 7200) * 1000) // 1000 * 1000
        saved = self.backend.save_draft(self.new(scheduleAt=at, receipt=True))
        restored = self.backend.restore_draft(saved['localId'])
        self.assertEqual((restored['scheduleAt'], restored['receipt']), (at, True))

    def test_old_draft_records_without_new_fields_still_load(self):
        from coremail_desktop.drafts.store import decode_draft, draft_dict
        record = draft_dict(draft())
        del record['schedule_at'], record['request_receipt']
        record['field_from_a_newer_version'] = 1
        loaded = decode_draft(record)
        self.assertIsNone(loaded.schedule_at)
        self.assertFalse(loaded.request_receipt)

    def test_delivery_status_and_recall_report_each_recipient(self):
        sent = self.backend.messages('3', 0, 50)['rows']
        read = next(r for r in sent if r['flags'].get('rcptRead'))
        status = self.backend.delivery_status(read['id'])
        self.assertEqual(status['read'], len(status['rows']))
        result = self.backend.recall(read['id'])
        self.assertEqual(result['recalled'], 0)
        self.assertTrue(all(r['text'].startswith('撤回失败') or r['text'] == '不支持撤回' for r in result['rows']))
        unread = next(r for r in sent if not r['flags'].get('rcptRead') and r['to'].endswith('example.invalid>'))
        result = self.backend.recall(unread['id'])
        self.assertEqual(result['recalled'], len(result['rows']))
        self.assertEqual({r['text'] for r in self.backend.delivery_status(unread['id'])['rows']}, {'已撤回'})


if __name__ == '__main__':
    unittest.main()
