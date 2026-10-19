"""Conversations expanded in place: received and sent mail of one conversation, from every folder."""
import shutil
import tempfile
import unittest

from coremail_desktop.app.backend import MailBackend, thread_key
from coremail_desktop.app.demo import DemoAdapter
from coremail_desktop.errors import AppError
from tests.test_app_backend import XorProtector, local_services_stub

KEY = '接口联调时间确认（演示）'  # Demo subjects repeat every 8 messages: inbox, sent and 项目归档.


class ThreadTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.backend = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'x')
        self.backend.folders()
        self.backend.messages('1', 0, 100)

    def tearDown(self):
        self.backend.logout()
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_key_drops_reply_and_forward_prefixes(self):
        for subject in ('Re: 接口联调时间确认（演示）', 'RE: Fwd：接口联调时间确认（演示）', '回复：答复: 接口联调时间确认（演示）', ' 接口联调时间确认（演示） '):
            self.assertEqual(thread_key(subject), KEY)
        self.assertEqual(thread_key('Re: A  b'), 'a b')

    def test_thread_brings_in_sent_mail_and_other_folders(self):
        b = self.backend
        inbox = [r for r in b.cache.summaries('1', 0, 200)[0] if thread_key(r['subject']) == KEY]
        mid = inbox[0]['id']
        self.assertEqual(b.thread_counts([mid])[mid], len(inbox))  # Only what the cache knows yet.
        rows = b.thread(mid)['rows']
        folders = {r['fid'] for r in rows}
        self.assertIn('3', folders)  # The sent reply, though the sent folder was never opened.
        self.assertIn('100', folders)
        self.assertTrue(all(thread_key(r['subject']) == KEY for r in rows))
        self.assertEqual([r['ts'] for r in rows], sorted((r['ts'] for r in rows), reverse=True))
        self.assertEqual(b.thread_counts([mid])[mid], len(rows))  # The server's part is cached now.

    def test_trash_and_drafts_stay_out(self):
        b = self.backend
        inbox = [r for r in b.cache.summaries('1', 0, 200)[0] if thread_key(r['subject']) == KEY]
        b.delete(inbox[1]['id'])
        ids = {r['id'] for r in b.thread(inbox[0]['id'])['rows']}
        self.assertNotIn(inbox[1]['id'], ids)
        self.assertIn(inbox[2]['id'], ids)

    def test_offline_returns_the_cached_part(self):
        b = self.backend
        mid = next(r['id'] for r in b.cache.summaries('1', 0, 200)[0] if thread_key(r['subject']) == KEY)

        def offline(*args, **kwargs):
            raise AppError('NETWORK_ERROR')
        b.adapter.search_messages = offline
        rows = b.thread(mid)['rows']
        self.assertTrue(rows and all(r['fid'] == '1' for r in rows))
        self.assertEqual(b.thread('unknown')['rows'], [])


if __name__ == '__main__':
    unittest.main()
