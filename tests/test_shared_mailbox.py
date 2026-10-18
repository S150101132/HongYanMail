"""Shared / department mailboxes: mboxa on the wire, per-mailbox cache, rw/send rights."""
import shutil
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter
from coremail_desktop.errors import AppError
from coremail_desktop.models import DraftDocument
from tests.test_app_backend import XorProtector, local_services_stub

MIME = b'From: a@example.invalid\r\nTo: b@example.invalid\r\nSubject: s\r\nMessage-ID: <m1@example.invalid>\r\n\r\nbody\r\n'


class RecordingSession:
    def __init__(self, shared=()):
        self.calls, self.fetched, self.replies, self.shared = [], [], [], list(shared)

    def call(self, op, params):
        self.calls.append((op, params))
        if op == 'mbox:getAllFolders':
            return {'code': 'S_OK', 'var': [{'id': 1, 'name': 'Inbox', 'stats': {'messageCount': 2, 'unreadMessageCount': 1}},
                                            {'id': 4, 'name': 'Trash', 'flags': {'system': True}}]}
        if op == 'mbox:listMessages':
            return {'code': 'S_OK', 'var': [{'id': 'm1', 'subject': 's', 'from': 'a@example.invalid', 'flags': {'read': False}}], 'total': 1}
        if op == 'mbox:listSharedAccessMbox':
            return {'code': 'S_OK', 'var': self.shared}
        if op == 'user:listDeliveryHistory':
            return {'code': 'S_OK', 'var': []}
        return {'code': 'S_OK', 'originalMsgInfos': [{'id': i} for i in params.get('ids', [])]}

    def get_message(self, mid, **extra):
        self.fetched.append((mid, extra))
        return MIME

    def prepare_reply(self, source_id, reply_all=False, **extra):
        self.replies.append((source_id, extra))
        return {'id': 'c1'}


class AdapterTest(unittest.TestCase):
    def adapter(self, **kw):
        from coremail_desktop.protocol.coremail import CoremailAdapter
        adapter = CoremailAdapter()
        adapter._session = RecordingSession(**kw)
        return adapter

    def test_own_mailbox_requests_are_unchanged(self):
        adapter = self.adapter()
        adapter.list_folders(); adapter.list_messages('1', 0, 10); adapter.set_flags_many(['m1'], {'read': True}); adapter.fetch_mime('m1')
        self.assertTrue(all('mboxa' not in params for _, params in adapter._session.calls))
        self.assertEqual(adapter._session.fetched, [('m1', {})])
        self.assertEqual(adapter.last_folder_id, '1')

    def test_shared_mailbox_is_named_in_every_mail_call(self):
        adapter = self.adapter()
        adapter.list_folders('@7'); adapter.list_messages('1', 0, 10, mboxa='@7')
        adapter.set_message_flags('m1', {'read': True}, mboxa='@7'); adapter.set_flags_many(['m1'], {'flagged': True}, mboxa='@7')
        adapter.move_many(['m1'], '4', mboxa='@7'); adapter.move_message('m1', '4', mboxa='@7'); adapter.delivery_history('m1', '@7')
        self.assertTrue(all(params.get('mboxa') == '@7' for _, params in adapter._session.calls))
        self.assertEqual(adapter.last_folder_id, '1@7')
        adapter.fetch_mime('m1', '@7')
        self.assertEqual(adapter._session.fetched, [('m1', {'mboxa': '@7'})])

    def test_message_data_url_carries_mboxa_only_for_shared_mail(self):
        from probe.coremail_transport import CoremailSession
        session = CoremailSession('https://mail.example.invalid/coremail/')
        session.sid = 'sid'
        urls = []
        session._fetch = lambda request: (urls.append(request.full_url), (MIME, request.full_url))[1]
        session.get_message('m1'); session.get_message('m1', mboxa='@7')
        self.assertNotIn('mboxa', parse_qs(urlsplit(urls[0]).query))
        self.assertEqual(parse_qs(urlsplit(urls[1]).query)['mboxa'], ['@7'])

    def test_malformed_mboxa_is_refused_before_the_network(self):
        adapter = self.adapter()
        for bad in ('7', '@', '@a b', '@a&b=1', 7):
            with self.assertRaises(AppError):
                adapter.list_messages('1', 0, 10, mboxa=bad)
        self.assertEqual(adapter._session.calls, [])

    def test_shared_list_rights_are_read_strictly(self):
        adapter = self.adapter(shared=[
            {'id': 7, 'email': 'dept@example.invalid', 'remark': '部门', 'send': 1, 'rw': 0},
            {'id': 'x', 'email': 'ro@example.invalid', 'send': '0', 'rw': False},
            {'id': 8, 'remark': 'no address'}, 'junk'])
        boxes = adapter.list_shared_mailboxes()
        self.assertEqual(boxes, [{'id': '7', 'email': 'dept@example.invalid', 'name': '部门', 'send': True, 'rw': False, 'recallable': False},
                                 {'id': 'x', 'email': 'ro@example.invalid', 'name': '', 'send': False, 'rw': False, 'recallable': False}])
        self.assertEqual(adapter._session.calls[-1], ('mbox:listSharedAccessMbox', {'showHidden': True, 'v': 2}))

    def test_reply_to_shared_mail_reads_and_replies_in_that_mailbox(self):
        adapter = self.adapter()
        draft = DraftDocument('l1', 1, 'reply', 'dept@example.invalid', ('a@example.invalid',), (), (), 'Re: s', 'x',
                              {'In-Reply-To': '<m1@example.invalid>'}, reply_source_id='m1', reply_mailbox='@7')
        self.assertEqual(adapter._compose_session(draft), 'c1')
        self.assertEqual(adapter._session.fetched, [('m1', {'mboxa': '@7'})])
        self.assertEqual(adapter._session.replies, [('m1', {'mboxa': '@7'})])


class BackendSharedTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.backend = self.open()
        self.box = self.backend.adapter.shared['@rd']

    def open(self):
        backend = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        return backend

    def tearDown(self):
        self.backend.logout()

    def rights(self, **values):
        self.box['info'].update(values)
        self.backend.shared_mailboxes()

    def test_sidebar_lists_mailbox_folders_with_unread_counts(self):
        [box] = self.backend.shared_mailboxes()
        self.assertEqual((box['id'], box['email'], box['send'], box['rw']), ('rd', 'rd-office@example.invalid', True, True))
        folders = {f['id']: f for f in box['folders']}
        self.assertEqual(folders['1@rd']['role'], 'inbox')
        self.assertEqual(folders['1@rd']['unread'], 5)
        self.assertEqual(box['unread'], 5)
        self.assertEqual([f['id'] for f in self.backend.folders()][:1], ['1'])  # Own folders untouched.

    def test_shared_mail_never_mixes_with_own_mail_in_the_cache(self):
        self.backend.shared_mailboxes()
        own = self.backend.messages('1', 0, 50)['rows']
        shared = self.backend.messages('1@rd', 0, 50)
        self.assertEqual(shared['total'], 13)
        self.assertIn('demo0', {r['id'] for r in own})
        self.assertIn('demo0@rd', {r['id'] for r in shared['rows']})
        self.assertTrue(all(r['fid'] == '1@rd' and r['mailbox'] == 'rd' for r in shared['rows']))
        self.assertNotEqual(self.backend.cache.summary('demo0')['subject'], self.backend.cache.summary('demo0@rd')['subject'])
        self.assertEqual(self.backend.open_message('demo0@rd')['subject'], self.backend.cache.summary('demo0@rd')['subject'])
        self.assertNotEqual(self.backend.cache.get_body('demo0'), self.backend.cache.get_body('demo0@rd'))
        self.assertEqual(len(self.backend.cached_view('1')['rows']), 50)
        self.assertEqual(self.backend.cached_view('1@rd')['cachedTotal'], 13)
        # Own-mail search and suggestions leave shared mail out.
        self.assertFalse(any(r.get('mailbox') for r in self.backend.search_local('演示')))
        self.assertFalse(self.backend.search_local('服务器采购'))

    def test_opening_marks_read_only_with_write_access(self):
        self.backend.shared_mailboxes()
        self.backend.messages('1@rd', 0, 50)
        self.assertTrue(self.backend.open_message('demo0@rd')['markedRead'])
        self.assertTrue(self.box['rows']['demo0']['flags']['read'])
        self.assertFalse(self.backend.adapter.rows['demo0']['flags']['read'])  # The user's own demo0 is untouched.
        self.assertEqual(self.backend.shared_mailboxes(refresh=False)[0]['unread'], 4)
        self.rights(rw=False)
        opened = self.backend.open_message('demo3@rd')
        self.assertFalse(opened.get('markedRead'))
        self.assertFalse(self.box['rows']['demo3']['flags']['read'])
        self.assertEqual(opened['mailbox'], {'id': 'rd', 'email': 'rd-office@example.invalid', 'name': '研发部公共邮箱', 'send': True, 'rw': False})

    def test_read_only_mailbox_refuses_every_change(self):
        self.rights(rw=False)
        self.backend.messages('1@rd', 0, 50)
        before = {k: dict(v['flags'], fid=v['fid']) for k, v in self.box['rows'].items()}
        for action in (lambda: self.backend.set_flags('demo3@rd', read=True), lambda: self.backend.set_flags(['demo3@rd', 'demo6@rd'], flagged=True),
                       lambda: self.backend.move('demo3@rd', '4@rd'), lambda: self.backend.delete(['demo3@rd'])):
            with self.assertRaises(AppError) as caught:
                action()
            self.assertEqual(caught.exception.code, 'SHARED_READ_ONLY')
        self.assertEqual(before, {k: dict(v['flags'], fid=v['fid']) for k, v in self.box['rows'].items()})

    def test_changes_stay_inside_the_mailbox(self):
        self.backend.shared_mailboxes()
        self.backend.messages('1@rd', 0, 50)
        self.backend.messages('1', 0, 50)
        result = self.backend.set_flags(['demo0@rd', 'demo3@rd'], read=True)
        self.assertEqual(result['done'], ['demo0@rd', 'demo3@rd'])
        self.assertEqual(next(f for f in result['shared'][0]['folders'] if f['id'] == '1@rd')['unread'], 3)
        self.assertFalse(self.backend.adapter.rows['demo0']['flags']['read'])
        self.assertTrue(self.backend.delete('demo1@rd'))
        self.assertEqual((self.box['rows']['demo1']['fid'], self.backend.cache.summary('demo1@rd')['fid']), ('4', '4@rd'))
        self.assertEqual(self.backend.adapter.rows['demo1']['fid'], '1')
        for target in ('100', '3'):
            with self.assertRaises(AppError) as caught:
                self.backend.move('demo2@rd', target)
            self.assertEqual(caught.exception.code, 'SHARED_MOVE_OUTSIDE')
        with self.assertRaises(AppError):
            self.backend.move('demo2', '1@rd')  # Own mail cannot go into a shared mailbox either.
        with self.assertRaises(AppError):
            self.backend.set_flags(['demo2', 'demo2@rd'], read=True)  # One mailbox per action.

    def test_replies_go_out_as_the_mailbox_only_with_send_rights(self):
        self.backend.shared_mailboxes()
        self.backend.messages('1@rd', 0, 50)
        reply = self.backend.compose('reply', 'demo0@rd')
        self.assertEqual(reply['from'], 'rd-office@example.invalid')
        self.assertNotIn('rd-office@example.invalid', reply['to'])
        self.assertEqual(reply['replySourceId'], 'demo0@rd')
        self.assertEqual(self.backend.compose('new', mailbox='rd')['from'], 'rd-office@example.invalid')
        self.assertEqual(self.backend.compose('new')['from'], 'demo@example.invalid')
        self.assertEqual(reply['fromChoices'], ['demo@example.invalid', 'rd-office@example.invalid'])
        result = self.backend.send({**reply, 'encrypt': False, 'sign': False})
        self.assertEqual(result['state'], 'Accepted')
        sent = self.backend.adapter.sent[-1]
        self.assertEqual((sent.from_identity, sent.reply_source_id, sent.reply_mailbox), ('rd-office@example.invalid', 'demo0', '@rd'))
        self.assertTrue(self.backend.cache.summary('demo0@rd')['flags'].get('replied'))
        self.assertFalse(self.backend.cache.summary('demo0') and self.backend.cache.summary('demo0')['flags'].get('replied'))

    def test_without_send_rights_mail_is_composed_from_the_own_address(self):
        self.rights(send=False)
        self.backend.messages('1@rd', 0, 50)
        reply = self.backend.compose('reply', 'demo0@rd')
        self.assertEqual(reply['from'], 'demo@example.invalid')
        self.assertEqual(reply['fromChoices'], ['demo@example.invalid'])
        self.assertEqual(self.backend.compose('new', mailbox='rd')['from'], 'demo@example.invalid')
        drafts = len(self.backend.list_drafts())
        with self.assertRaises(AppError) as caught:
            self.backend.send({**reply, 'from': 'rd-office@example.invalid', 'encrypt': False})
        self.assertEqual(caught.exception.code, 'SHARED_SEND_DENIED')
        self.assertEqual(len(self.backend.list_drafts()), drafts)
        self.assertEqual(self.backend.adapter.sent, [])

    def test_reply_draft_keeps_its_mailbox_across_restore(self):
        self.backend.shared_mailboxes()
        self.backend.messages('1@rd', 0, 50)
        reply = self.backend.compose('reply', 'demo0@rd')
        self.backend.save_draft(reply)
        restored = self.backend.restore_draft(reply['localId'])
        self.assertEqual(restored['replySourceId'], 'demo0@rd')
        self.assertIn('rd-office@example.invalid', restored['fromChoices'])

    def test_unshared_mailbox_leaves_the_cache_and_restart_keeps_the_list(self):
        self.backend.shared_mailboxes()
        self.backend.messages('1', 0, 50)
        self.backend.messages('1@rd', 0, 50)
        self.backend.open_message('demo0@rd')
        self.backend.logout()
        self.backend = self.open()  # Restart: the cached list still qualifies ids.
        self.assertEqual([b['id'] for b in self.backend.shared_mailboxes(refresh=False)], ['rd'])
        self.assertEqual(self.backend.cached_view('1@rd')['cachedTotal'], 13)
        self.backend.adapter.shared.clear()
        self.assertEqual(self.backend.shared_mailboxes(), [])
        self.assertIsNone(self.backend.cache.summary('demo0@rd'))
        self.assertIsNone(self.backend.cache.get_body('demo0@rd'))
        self.assertIsNotNone(self.backend.cache.summary('demo0'))

    def test_poll_refreshes_shared_counts_without_notifying(self):
        self.backend.shared_mailboxes()
        self.box['rows']['demo99'] = {**self.box['rows']['demo0'], 'id': 'demo99', 'date': '2026-10-17 12:00:00', 'flags': {'read': False}}
        result = self.backend.poll()
        self.assertEqual(result['shared'][0]['unread'], 6)
        self.assertFalse(any(r['id'].endswith('@rd') for r in result['new']))

    def test_recall_is_not_offered_for_shared_mail(self):
        self.backend.shared_mailboxes()
        self.backend.messages('3@rd', 0, 50)
        with self.assertRaises(AppError) as caught:
            self.backend.recall('demo13@rd')
        self.assertEqual(caught.exception.code, 'OPERATION_NOT_ALLOWED')
        self.assertTrue(self.backend.delivery_status('demo13@rd')['rows'])


if __name__ == '__main__':
    unittest.main()
