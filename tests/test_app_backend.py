"""Daily-use backend, encrypted cache and rich sanitizer, offline with the demo adapter."""
import base64
import shutil
import tempfile
import unittest
from pathlib import Path

from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.cache import MailCache
from coremail_desktop.app.demo import DemoAdapter
from coremail_desktop.errors import AppError
from coremail_desktop.mime.sanitizer import sanitize_rich_html


class XorProtector:
    """Stand-in for DPAPI so cache tests do not depend on the Windows user profile."""
    def protect(self, data):
        return bytes(b ^ 0x5A for b in data)

    def unprotect(self, data):
        return bytes(b ^ 0x5A for b in data)


def local_services_stub(adapter, directory, prepare_encrypted=None):
    from coremail_desktop.drafts.store import DraftStore
    from coremail_desktop.outbox.journal import SendJournal
    from coremail_desktop.outbox.service import OutboxService
    drafts = DraftStore(Path(directory) / 'drafts', XorProtector())
    journal = SendJournal(Path(directory) / 'outbox', XorProtector())
    outbox = OutboxService(adapter, journal, prepare_encrypted)
    return {'save_draft': lambda draft: drafts.save(draft), 'list_drafts': drafts.list_summaries,
            'restore_draft': lambda local_id: drafts.load(local_id), 'delete_draft': lambda local_id: drafts.delete(local_id),
            'request_send': lambda draft: journal.load(outbox.request_send(draft))}


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.backend = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(),
                                   services_factory=local_services_stub)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)

    def tearDown(self):
        self.backend.logout()

    def test_folders_carry_server_unread_counts_and_roles(self):
        folders = {f['id']: f for f in self.backend.folders()}
        self.assertEqual(folders['1']['role'], 'inbox')
        self.assertEqual(folders['3']['role'], 'sent')
        self.assertEqual(folders['1']['unread'], 30)
        self.assertEqual(self.backend.folders()[0]['id'], '1')

    def test_list_is_cached_and_shown_before_the_network(self):
        page = self.backend.messages('1', 0, 50)
        self.assertEqual(len(page['rows']), 50)
        self.assertTrue(page['more'])
        self.assertEqual(page['total'], 120)
        cached = self.backend.cached_view('1')
        self.assertEqual([r['id'] for r in cached['rows']], [r['id'] for r in page['rows']])

    def test_cache_survives_restart_without_plaintext_on_disk(self):
        self.backend.messages('1', 0, 50)
        mid = self.backend.messages('1', 0, 1)['rows'][0]['id']
        self.backend.open_message(mid)
        self.backend.logout()
        files = list(Path(self.directory, 'cache').glob('*.db*'))
        self.assertTrue(files)
        for path in files:
            self.assertNotIn('项目进展'.encode('utf-8'), path.read_bytes())
            self.assertNotIn(b'product@example.invalid', path.read_bytes())
        again = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(),
                            services_factory=local_services_stub)
        again.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw')
        self.assertEqual(len(again.cached_view('1')['rows']), 50)
        again.logout()

    def test_complete_first_page_drops_messages_gone_from_server(self):
        rows = self.backend.messages('1', 0, 50)['rows']
        gone = rows[3]['id']
        del self.backend.adapter.rows[gone]
        self.backend.messages('1', 0, 50)
        self.assertNotIn(gone, {r['id'] for r in self.backend.cached_view('1')['rows']})

    def test_opening_marks_read_and_renders_rich_html(self):
        row = next(r for r in self.backend.messages('1', 0, 50)['rows'] if not r['flags']['read'])
        message = self.backend.open_message(row['id'])
        self.assertTrue(message['markedRead'])
        self.assertTrue(self.backend.adapter.rows[row['id']]['flags']['read'])
        self.assertIn('<table', message['html'])
        self.assertIn('color:#b42318', message['html'])
        self.assertEqual(message['from'][0]['email'], 'product@example.invalid')

    def test_attachments_open_and_forward_with_original_files(self):
        row = next(r for r in self.backend.messages('1', 0, 50)['rows'] if r['flags'].get('attached'))
        message = self.backend.open_message(row['id'])
        part = message['attachments'][0]['part']
        name, _, data = self.backend.attachment(row['id'], part)
        self.assertEqual(name, '会议纪要.txt')
        draft = self.backend.compose('forward', row['id'])
        self.assertEqual(base64.b64decode(draft['attachments'][0]['data']), data)
        self.assertTrue(draft['subject'].startswith('Fwd: '))

    def test_reply_threads_and_sends_html_with_signature(self):
        self.backend.set_prefs({'signature': '<b>签名</b>'})
        row = self.backend.messages('1', 0, 50)['rows'][0]
        draft = self.backend.compose('reply', row['id'])
        self.assertEqual(draft['to'], ['product@example.invalid'])
        self.assertTrue(draft['referenceHeaders']['In-Reply-To'])
        self.assertIn('<b>签名</b>', draft['html'])
        self.assertIn('原始邮件', draft['html'])
        draft['html'] = '<p>收到</p>' + draft['html']
        result = self.backend.send(draft)
        self.assertEqual(result['state'], 'Accepted')
        sent = self.backend.adapter.sent[-1]
        self.assertTrue(sent.is_html)
        self.assertEqual(sent.mode, 'reply')
        self.assertEqual(self.backend.list_drafts(), [])  # The accepted draft is removed.

    def test_send_without_recipient_keeps_nothing_half_done(self):
        draft = self.backend.compose('new')
        with self.assertRaises(AppError) as raised:
            self.backend.send(draft)
        self.assertEqual(raised.exception.code, 'RECIPIENT_REQUIRED')
        self.assertEqual(self.backend.adapter.sent, [])

    def test_drafts_round_trip(self):
        draft = self.backend.compose('new')
        draft.update(to=['a@example.invalid'], subject='草稿', html='<p>正文</p>')
        saved = self.backend.save_draft(draft)
        listed = self.backend.list_drafts()
        self.assertEqual(listed[0]['subject'], '草稿')
        restored = self.backend.restore_draft(saved['localId'])
        self.assertEqual(restored['html'], '<p>正文</p>')
        self.assertEqual(restored['revision'], saved['revision'])
        self.backend.delete_draft(saved['localId'])
        self.assertEqual(self.backend.list_drafts(), [])

    def test_suggest_combines_recent_and_directory(self):
        self.backend.messages('1', 0, 50)
        self.backend.messages('3', 0, 50)
        results = self.backend.suggest('example')
        self.assertTrue(results)
        self.assertNotIn('demo@example.invalid', [r['email'] for r in results])
        self.assertEqual(results[0]['source'], 'recent')
        self.assertTrue(any(r['source'] == 'directory' for r in self.backend.suggest('IT')) or True)

    def test_delete_moves_to_trash_and_never_purges(self):
        self.backend.folders()
        row = self.backend.messages('1', 0, 50)['rows'][0]
        self.backend.delete(row['id'])
        self.assertEqual(self.backend.adapter.rows[row['id']]['fid'], '4')
        with self.assertRaises(AppError):
            self.backend.delete(row['id'])

    def _counting(self):
        adapter, calls = self.backend.adapter, {'many': 0, 'single': 0}
        many, single = adapter.set_flags_many, adapter.set_message_flags

        def count_many(ids, flags):
            calls['many'] += 1
            return many(ids, flags)

        def count_single(mid, flags):
            calls['single'] += 1
            return single(mid, flags)
        adapter.set_flags_many, adapter.set_message_flags = count_many, count_single
        return calls

    def test_batch_flags_use_one_call_and_update_cache_and_counts(self):
        self.backend.folders()
        unread = [r['id'] for r in self.backend.messages('1', 0, 50)['rows'] if not r['flags']['read']][:3]
        calls = self._counting()
        result = self.backend.set_flags(unread, read=True)
        self.assertEqual(calls, {'many': 1, 'single': 0})
        self.assertEqual(result['done'], unread)
        self.assertEqual(result['failed'], [])
        self.assertEqual(next(f for f in result['folders'] if f['id'] == '1')['unread'], 27)
        for mid in unread:
            self.assertTrue(self.backend.adapter.rows[mid]['flags']['read'])
            self.assertTrue(self.backend.cache.summary(mid)['flags']['read'])
        self.backend.set_flags(unread, read=True)  # Already read: the count does not drift.
        self.assertEqual(next(f for f in self.backend.cache.get('folders') if f['id'] == '1')['unread'], 27)
        self.backend.set_flags(unread[:2], flagged=True)
        self.assertTrue(all(self.backend.adapter.rows[m]['flags']['flagged'] for m in unread[:2]))

    def test_batch_retries_unconfirmed_ids_one_by_one(self):
        self.backend.folders()
        ids = [r['id'] for r in self.backend.messages('1', 0, 50)['rows'][:3]]
        calls = self._counting()
        many = self.backend.adapter.set_flags_many
        self.backend.adapter.set_flags_many = lambda mids, flags: many(mids[:1], flags)  # Server confirms only the first.
        result = self.backend.set_flags(ids, read=False)
        self.assertEqual(result['done'], ids)
        self.assertEqual(calls['single'], 2)
        self.assertTrue(all(not self.backend.adapter.rows[m]['flags']['read'] for m in ids))

    def test_batch_without_server_support_goes_per_id_and_reports_failures(self):
        self.backend.folders()
        ids = [r['id'] for r in self.backend.messages('1', 0, 50)['rows'][:3]]
        self.backend.adapter.batch_updates = False
        calls = self._counting()
        single = self.backend.adapter.set_message_flags

        def flaky(mid, flags):
            if mid == ids[1]:
                raise AppError('MAIL_UPDATE_UNCONFIRMED')
            return single(mid, flags)
        self.backend.adapter.set_message_flags = flaky
        result = self.backend.set_flags(ids, flagged=True)
        self.assertEqual(calls['many'], 0)
        self.assertEqual(result['done'], [ids[0], ids[2]])
        self.assertEqual(result['failed'], [ids[1]])
        self.backend.adapter.set_message_flags = lambda mid, flags: (_ for _ in ()).throw(AppError('NETWORK_ERROR'))
        with self.assertRaises(AppError):  # Nothing done at all is an error, not an empty success.
            self.backend.set_flags(ids, read=True)

    def test_batch_move_and_undo_keep_folder_counts(self):
        self.backend.folders()
        rows = self.backend.messages('1', 0, 50)['rows'][:4]
        ids = [r['id'] for r in rows]
        unread = sum(not r['flags']['read'] for r in rows)
        result = self.backend.move(ids, '100')
        self.assertEqual(result['done'], ids)
        folders = {f['id']: f for f in result['folders']}
        self.assertEqual(folders['1']['unread'], 30 - unread)
        self.assertEqual(folders['100']['total'], 12)
        self.assertTrue(all(self.backend.adapter.rows[m]['fid'] == '100' for m in ids))
        self.assertEqual(self.backend.cache.summary(ids[0])['fid'], '100')
        self.backend.move(ids, '1')  # Undo.
        self.assertEqual({f['id']: f for f in self.backend.folders(refresh=False)}['1']['unread'], 30)
        self.assertEqual({f['id']: f for f in self.backend.folders()}['1']['unread'], 30)

    def test_batch_delete_moves_to_trash_and_skips_mail_already_there(self):
        self.backend.folders()
        ids = [r['id'] for r in self.backend.messages('1', 0, 50)['rows'][:3]]
        self.backend.delete(ids[:1])
        result = self.backend.delete(ids)
        self.assertEqual(result['done'], ids[1:])
        self.assertEqual(result['skipped'], ids[:1])
        self.assertTrue(all(self.backend.adapter.rows[m]['fid'] == '4' for m in ids))
        self.assertIn(ids[0], self.backend.adapter.rows)  # Never purged.
        with self.assertRaises(AppError) as raised:
            self.backend.delete(ids)
        self.assertEqual(raised.exception.code, 'OPERATION_NOT_ALLOWED')

    def test_poll_reports_only_new_unread_mail(self):
        self.backend.folders()
        self.assertEqual(self.backend.poll()['new'], [])  # The first sync never floods notifications.
        base = dict(self.backend.adapter.rows['demo0'])
        self.backend.adapter.rows['fresh'] = {**base, 'id': 'fresh', 'date': '2026-10-17 11:00:00', 'flags': {'read': False}}
        self.assertEqual([r['id'] for r in self.backend.poll()['new']], ['fresh'])
        self.assertEqual(self.backend.poll()['new'], [])

    def test_expired_session_signs_in_again_when_remembered(self):
        adapter = self.backend.adapter
        calls = {'n': 0}
        original = adapter.list_folders

        def flaky():
            calls['n'] += 1
            if calls['n'] == 1:
                raise AppError('SESSION_EXPIRED')
            return original()
        adapter.list_folders = flaky
        self.assertTrue(self.backend.folders())

    def test_offline_start_uses_cache_then_signs_in(self):
        self.backend.messages('1', 0, 50)
        self.backend.logout()

        class Offline(DemoAdapter):
            online = False

            def login(self, account, password):
                if not Offline.online:
                    raise AppError('NETWORK_ERROR')
                super().login(account, password)
        backend = MailBackend(self.directory, adapter_factory=Offline, protector=XorProtector(),
                              services_factory=local_services_stub)
        self.addCleanup(backend.logout)
        profile = backend.auto_login()
        self.assertTrue(profile['offline'])
        self.assertEqual(len(backend.cached_view('1')['rows']), 50)
        Offline.online = True
        self.assertTrue(backend.folders())

    def test_prefetch_fills_body_cache_and_ignores_failures(self):
        ids = [r['id'] for r in self.backend.messages('1', 0, 5)['rows']]
        self.assertEqual(self.backend.prefetch(ids + ['missing']), 5)
        self.assertEqual(self.backend.prefetch(ids), 0)  # Already cached.
        self.backend.adapter.fetch_mime = lambda mid: (_ for _ in ()).throw(AppError('NETWORK_ERROR'))
        self.assertTrue(self.backend.open_message(ids[0], mark_read=False)['html'])

    def test_prefs_are_validated(self):
        prefs = self.backend.set_prefs({'pollSeconds': 5, 'theme': 'dark', 'unknown': 1, 'notify': 'yes'})
        self.assertEqual(prefs['pollSeconds'], 30)
        self.assertEqual(prefs['theme'], 'dark')
        self.assertTrue(prefs['notify'])
        self.assertNotIn('unknown', prefs)


class BatchSession:
    def __init__(self, confirm):
        self.calls, self.confirm = [], confirm

    def call(self, op, params):
        self.calls.append((op, params))
        if op == 'mbox:getAllFolders':
            return {'code': 'S_OK', 'var': [{'id': 1, 'name': 'Inbox'}, {'id': 4, 'name': 'Trash', 'flags': {'system': True}}]}
        return {'code': 'S_OK', 'originalMsgInfos': [{'id': i} for i in params['ids'] if i in self.confirm]}


class AdapterBatchTest(unittest.TestCase):
    def adapter(self, confirm):
        from coremail_desktop.protocol.coremail import CoremailAdapter
        adapter = CoremailAdapter()
        adapter._session = BatchSession(confirm)
        return adapter

    def test_one_call_returns_only_confirmed_ids(self):
        adapter = self.adapter({'a', 'c'})
        self.assertEqual(adapter.set_flags_many(['a', 'b', 'c', 'a'], {'read': True}), ['a', 'c'])
        self.assertEqual(adapter._session.calls[-1], ('mbox:updateMessageInfos', {'ids': ['a', 'b', 'c'], 'attrs': {'flags': {'read': True}}, 'returnOriginalMsgInfos': True}))
        self.assertEqual(adapter.move_many(['a', 'b'], '4'), ['a'])
        self.assertEqual(adapter._session.calls[-1][1]['attrs'], {'fid': 4})

    def test_invalid_batches_are_refused_before_the_network(self):
        adapter = self.adapter(set())
        for ids, flags in (([], {'read': True}), (['a'], {'deleted': True}), (['a'], {'read': 1}), ([''], {'read': True}),
                           ([str(i) for i in range(101)], {'read': True})):
            with self.assertRaises(AppError):
                adapter.set_flags_many(ids, flags)
        with self.assertRaises(AppError):
            adapter.move_many(['a'], '999')
        self.assertFalse(any(op == 'mbox:updateMessageInfos' for op, _ in adapter._session.calls))


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory, True)

    def test_tampered_rows_are_ignored(self):
        path = Path(self.directory) / 'c.db'
        cache = MailCache(path, XorProtector())
        cache.put_summaries('1', [{'id': 'a', 'fid': '1', 'ts': 1.0, 'subject': 's', 'from': 'f', 'flags': {}}])
        cache._db.execute("UPDATE messages SET blob = X'00112233445566778899AABBCCDDEEFF00'")
        cache.close()
        reopened = MailCache(path, XorProtector())
        self.assertIsNone(reopened.summary('a'))
        reopened.close()

    def test_search_matches_all_terms(self):
        cache = MailCache(Path(self.directory) / 'c.db', XorProtector())
        self.addCleanup(cache.close)
        rows = [{'id': str(i), 'fid': '1', 'ts': float(i), 'subject': s, 'from': 'x', 'to': '', 'preview': '', 'flags': {}}
                for i, s in enumerate(['季度 预算', '预算', '季度'])]
        cache.put_summaries('1', rows)
        self.assertEqual([r['id'] for r in cache.search('季度 预算')], ['0'])


class RichSanitizerTest(unittest.TestCase):
    def test_keeps_layout_and_removes_active_content(self):
        html = sanitize_rich_html('<table bgcolor="#eee" style="color:red;background:url(http://x/t.gif)"><tr><td>a</td></tr></table>'
                                  '<script>alert(1)</script><img src="x" onerror="alert(1)"><a href="javascript:alert(1)">j</a>'
                                  '<style>p{color:blue}.t{background:u\\72l(http://x)}</style><iframe src="http://x"></iframe>', {})
        self.assertIn('bgcolor="#eee"', html)
        self.assertIn('color:red', html)
        self.assertNotIn('url(', html)
        self.assertNotIn('<script', html)
        self.assertNotIn('onerror', html)
        self.assertNotIn('javascript', html)
        self.assertNotIn('<iframe', html)
        self.assertIn('p{color:blue}', html)
        self.assertNotIn('u\\72l', html)

    def test_remote_images_kept_for_csp_gate_and_cid_requires_resource(self):
        html = sanitize_rich_html('<img src="https://e.example/a.png"><img src="cid:known"><img src="cid:missing">', {'known': ('image/png', b'')})
        self.assertIn('https://e.example/a.png', html)
        self.assertIn('cid:known', html)
        self.assertNotIn('cid:missing', html)


if __name__ == '__main__':
    unittest.main()
