"""K1–K4 (第二轮架构评审): the rules that were repeated or leaked across a seam, each in one place.
Demo adapter only; nothing leaves the machine."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter
from tests.test_app_backend import XorProtector, local_services_stub
from tests.test_compose_v05 import scratch

ROOT = Path(__file__).resolve().parents[1] / 'coremail_desktop'


def backend(test, adapter=DemoAdapter):
    b = MailBackend(scratch(test), adapter_factory=adapter, protector=XorProtector(), services_factory=local_services_stub)
    b.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
    test.addCleanup(b.logout)
    return b


class Busy(DemoAdapter):
    """Another thread lists a different folder while this list is on its way back."""
    def list_messages(self, folder_id, offset, limit=50, mboxa=''):
        page = super().list_messages(folder_id, offset, limit, mboxa)
        super().list_messages('3', 0, 1)
        return page


class ServerSeamTest(unittest.TestCase):  # K1
    def test_the_list_carries_its_folder_total(self):
        b = backend(self)
        page = b.adapter.list_messages('1', 0, 5)
        self.assertEqual(len(page), 5)
        self.assertEqual(page.total, sum(r['fid'] == '1' for r in b.adapter.rows.values()))  # The folder, not the page.
        self.assertFalse(hasattr(b.adapter, 'last_folder_total'))

    def test_a_list_elsewhere_does_not_change_the_total(self):
        b = backend(self, Busy)
        total = b.messages('1', 0, 10)['total']
        self.assertIsNotNone(total)
        self.assertEqual(total, b.adapter.list_messages('1', 0, 100).total)

    def test_the_demo_server_does_not_depend_on_the_client(self):
        self.assertFalse('app.backend' in (ROOT / 'app' / 'demo.py').read_text(encoding='utf-8'))
        from coremail_desktop.app import backend as backend_module, searchfilter
        self.assertIs(backend_module.filter_match, searchfilter.filter_match)  # Still importable from the backend.


SUBJECTS = ['Re: 接口联调时间确认', 'RE: Fwd：接口联调时间确认', '回复：答复: Weekly  Report', ' OK ', 'Re: OK', '请假', '回复：请假一天',
            'Re: [2] : 项目进展与本周计划', '', 'fw: abcdef', 'fw: abcde']
KEYS_JS = r'''
const vm = require('vm'), fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const start = src.indexOf('const SUBJECT_PREFIX'), end = src.indexOf('function conversations(');
const box = {};
vm.runInNewContext(src.slice(start, end) + ';this.threadKey = threadKey;', box);
console.log(JSON.stringify(JSON.parse(process.argv[3]).map(box.threadKey)));
'''


class ConversationTest(unittest.TestCase):  # K2
    def test_a_short_subject_is_a_conversation_of_its_own(self):
        from coremail_desktop.app.conversation import conversation_key
        self.assertEqual(conversation_key('回复：接口联调时间确认'), '接口联调时间确认')
        for short in ('Re: OK', '回复：请假', 'fw: abcde', ''):
            self.assertEqual(conversation_key(short), '', short)

    def test_short_subjects_do_not_join_in_the_backend(self):
        b = backend(self)
        b.folders()
        row = lambda i, subject, fid='1': {'id': i, 'fid': fid, 'subject': subject, 'from': 'x@example.invalid', 'fromName': 'X', 'to': '',
                                           'date': '2026-11-01 10:00:00', 'ts': 1761980400.0 + len(i), 'preview': '', 'size': 1, 'flags': {}, 'tags': []}
        b.cache.put_summaries('1', [row('ok1', 'OK'), row('ok2', 'Re: OK'), row('long1', '接口联调时间确认'), row('long2', 'Re: 接口联调时间确认')])
        self.assertEqual([r['id'] for r in b.thread('ok1')['rows']], ['ok1'])
        self.assertEqual(b.thread_counts(['ok1', 'long1']), {'ok1': 1, 'long1': 2})

    def test_one_skip_list(self):
        from coremail_desktop.app import conversation
        source = (ROOT / 'app' / 'backend.py').read_text(encoding='utf-8')
        self.assertFalse("('trash', 'spam', 'virus', 'drafts')" in source)  # Only conversation.THREAD_SKIP.
        self.assertFalse('len(key) >= 4' in source or 'len(key) >= 6' in source or 'len(key) < 6' in source)
        self.assertEqual(conversation.THREAD_SKIP, ('trash', 'spam', 'virus', 'drafts'))

    @unittest.skipUnless(shutil.which('node'), 'node is not installed')
    def test_the_page_groups_by_the_same_rule(self):
        from coremail_desktop.app.conversation import conversation_key
        out = subprocess.run([shutil.which('node'), '-', str(ROOT / 'app' / 'web' / 'app.js'), json.dumps(SUBJECTS)], input=KEYS_JS,
                             capture_output=True, text=True, encoding='utf-8', check=True)
        self.assertEqual(json.loads(out.stdout), [conversation_key(s) for s in SUBJECTS])


if __name__ == '__main__':
    unittest.main()
