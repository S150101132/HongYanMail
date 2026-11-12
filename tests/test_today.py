"""The pages that were 今天 (R2a; 每日速览 G1/G2 taken out in G3): 在等别人, and a sent reply
marking its mail answered (R3a). Demo adapter, local task workbooks, a stand-in network; nothing leaves."""
import unittest

from pathlib import Path

from coremail_desktop.ai import understand
from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter, DemoAiClient
from coremail_desktop.errors import AppError
from tests.test_app_backend import XorProtector, local_services_stub
from tests.test_compose_v05 import scratch
from tests.test_tasks import Clock, folder_with, task_row
from tests.test_understand import card_json


def act(b, mid, due='', **fields):
    b.memory.put_card(mid, {**understand.parse(card_json(due=due, **fields)), 'v': understand.CARD_VERSION})


class TodayTest(unittest.TestCase):
    def setUp(self):
        self.b = b = MailBackend(scratch(self), adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        b.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(b.logout)
        self.addCleanup(lambda: b.tasks and b.tasks.close())
        b.folders()
        b.messages('1', 0, 100)
        b.messages('3', 0, 50)

    def tasks(self):
        rows = [task_row(401, '进行中', due='2026-10-19'), task_row(402, '进行中', due='2026-10-20'), task_row(403, due='2026-10-30'),
                task_row(404, '受阻', due='2026-10-21'), task_row(405, '进行中'), task_row(406, '待处理', due='2026-10-23')]
        self.b.tasks_set_folder(str(folder_with(self, open_rows=rows)))
        self.b.tasks_import('live')
        self.b.tasks.clock = Clock()  # 2026-10-20 in Riyadh.

    def test_waiting_on_others(self):  # 在等别人: 受阻 tasks and unanswered sent mail; never asks the AI.
        b = self.b
        self.assertEqual(b.today_waiting(), {'mail': [], 'tasks': []})
        calls = []
        b.ai_client = type('Ai', (), {'model': 'm', 'chat': lambda *a, **k: calls.append(a)})()
        self.tasks()
        self.assertEqual([t['id'] for t in b.today_waiting()['tasks']], [404])
        self.assertEqual(calls, [])

    def test_a_reply_only_marks_the_mail_answered(self):  # R3a: a reply is not the work done.
        b = self.b
        self.assertEqual(b.understand_answered('demo1'), {'changed': []})  # 理解层 off: nothing to do.
        b.set_understand('on')
        ids = b.understand_pending()['ids']
        mid, other, third = ids[:3]
        for i, due in ((mid, '2026-10-23'), (other, '2026-10-23'), (third, '')):
            act(b, i, due)
        self.assertEqual(b.understand_answered(mid)['changed'], [mid])
        self.assertEqual(b.understand_answered(mid)['changed'], [])  # Once.
        self.assertEqual(b.understand_answered(mid, how='forward')['changed'], [mid])
        card = b.memory.card(mid)
        self.assertEqual((card['answered'], card.get('done')), ('forward', None))
        rows = [r['id'] for r in b.understand_view('act')['rows']]
        self.assertIn(mid, rows)  # Still to do: it stays on the list...
        self.assertEqual(rows[-1], mid)  # ...after the ones not yet answered.
        self.assertEqual(b.understand_view('act')['rows'][-1]['card']['answered'], 'forward')
        self.assertIsNone(b.memory.card(other).get('answered'))  # Another mail stays as it was.
        b.understand_set_done([mid])
        self.assertEqual(b.understand_answered(mid)['changed'], [])  # Done already: left alone.
        act(b, other, sure=False)
        self.assertEqual(b.understand_answered(other)['changed'], [])  # Not sure: never on the list anyway.
        for bad in ('', None):
            with self.assertRaises(AppError):
                b.understand_answered(bad)
        with self.assertRaises(AppError):
            b.understand_answered(mid, how='send')

    def test_facts_off_by_default(self):  # R3c; G3: no 启动时打开, the inbox always first.
        self.b.set_prefs({'startPage': 'today'})  # An old setting is dropped.
        self.assertEqual((self.b.prefs()['memoryFacts'], 'startPage' in self.b.prefs()), (False, False))


class PageTest(unittest.TestCase):
    """The page side: 今天 in the sidebar and as the first page, R1a/R1b in the reader, one assistant."""
    def setUp(self):
        web = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'
        self.read = lambda name: (web / name).read_text(encoding='utf-8')

    def test_today_page(self):
        page, today = self.read('index.html'), self.read('today.js')
        self.assertIn('id="reader-task"', page)
        self.assertLess(page.index('src="memory.js"'), page.index('src="today.js"'))
        self.assertLess(page.index('src="today.js"'), page.index('src="commands.js"'))
        for call in ("api('todayWaiting')", "api('tasksPending')", "api('taskReview'", "api('taskGet'"):
            self.assertIn(call, today)
        self.assertNotIn("api('todayOverview')", today)  # G1: the 今天 list gave way to Trending.
        for gone in ('id="nav-today"', '每日速览', 'id="daily-view"', 'id="pref-start"'):
            self.assertNotIn(gone, page)  # G3: the page, its entry and 启动时打开 are gone.
        for gone in ("api('dailyNews'", "api('trending'", 'startPage'):
            self.assertNotIn(gone, today)
        self.assertIn('← 收件箱', today)  # The sub-pages lead back to the inbox.
        self.assertIn("if (S.local) { S.local = null;", self.read('app.js'))  # V1: a search leaves 今天.
        for text in ('esc(t.content', 'esc(t.system)', 'esc(text)', 'esc(tkText(t.content))', 'esc(err.message)'):
            self.assertIn(text, today)  # Task text and AI suggestions are shown as text.
        memory = self.read('memory.js')
        self.assertIn("if (kind !== 'week')", memory)  # 本周概括 left the inbox's 知会 for 今天.
        self.assertNotIn('id="seg-u-', page)  # 要我做 / 知会 / 在等别人 left the list head (R4: marks on the rows).
        self.assertIn('class="uc-kind act"', self.read('understand.js'))

    def test_reply_and_strip(self):
        app, understand_js, memory = self.read('app.js'), self.read('understand.js'), self.read('memory.js')
        self.assertIn('draft.sourceMid = mid', app)
        self.assertIn('function ucShowAnswered({ mid, how })', understand_js)  # 发后跟进: the backend marked it.
        self.assertIn("label: '已处理', run: () => ucCorrect(mid, { done: true }, {})", understand_js)  # R3a: done is the user's click.
        self.assertNotIn('没有需要你处理的邮件', understand_js)  # V3
        self.assertIn('关联 #${c.task}', memory)  # The task candidate is a button on the one strip.
        self.assertIn('if (c && c.project && S.prefs.memoryFacts && memRecentOpen())', memory)  # Only when opened, with facts kept (R3c).
        self.assertIn('pickGroup', self.read('groups.js'))  # V6: every group can be found.

    def test_one_assistant(self):
        page, tasks = self.read('index.html'), self.read('tasks.js')
        for gone in ('id="tk-cmd"', 'id="tk-thread"', 'id="tk-ask-hint"'):
            self.assertNotIn(gone, page)
        self.assertIn('id="tk-filter"', page)
        self.assertIn("$('#tk-ask').onclick = () => showAsk();", tasks)
        self.assertNotIn("api('tasksCommand'", tasks)
        self.assertIn("writeReport('last')", tasks)
        ai = self.read('ai.js')
        self.assertIn("S.local === 'today' && typeof TD !== 'undefined' && TD.task", ai)  # The task card goes along.


if __name__ == '__main__':
    unittest.main()
