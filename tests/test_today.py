"""今天 (R2a) and 回复即已处理 (R1a): one list of 要我做 mail and tasks due soon, and a sent
reply marking its conversation done. Demo adapter, local task workbooks; nothing leaves."""
import unittest
from pathlib import Path

from coremail_desktop.ai import understand
from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter
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

    def test_one_list_of_mail_and_tasks_by_due_date(self):
        b = self.b
        self.tasks()
        b.set_understand('on')
        soon, undated = b.understand_pending()['ids'][:2]
        act(b, soon, '2026-10-21')
        act(b, undated)
        view = b.today_overview()
        self.assertEqual([(r['kind'], r['id']) for r in view['rows']],
                         [('task', 401), ('task', 402), ('mail', soon), ('task', 406), ('mail', undated)])
        self.assertEqual(view['rows'][2]['mail']['card']['due'], '2026-10-21')  # The row the list draws, card included.
        self.assertEqual((view['date'], view['understanding'], view['tasks']), ('2026-10-20', True, True))
        counts = view['counts']
        self.assertEqual((counts['overdue'], counts['today'], counts['confirm']), (1, 1, 0))
        self.assertEqual(counts['waiting'], len(b.followups()) + 1)  # 受阻 #404 waits on others.
        self.assertEqual([t['id'] for t in b.today_waiting()['tasks']], [404])
        b.understand_set_done([soon])
        self.assertNotIn(soon, [r['id'] for r in b.today_overview()['rows']])  # Done: off the list.

    def test_without_understanding_or_tasks(self):
        b = self.b
        self.assertEqual(b.today_overview()['rows'], [])
        self.assertEqual((b.today_overview()['understanding'], b.today_overview()['tasks']), (False, False))
        self.assertEqual(b.today_waiting(), {'mail': [], 'tasks': []})
        self.tasks()
        self.assertEqual([r['id'] for r in b.today_overview()['rows']], [401, 402, 406])
        calls = []
        b.ai_client = type('Ai', (), {'model': 'm', 'chat': lambda *a, **k: calls.append(a)})()
        b.today_overview()
        self.assertEqual(calls, [])  # Never asks the AI.

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

    def test_the_list_says_how_much_it_covers(self):  # V2
        b = self.b
        b.set_understand('on')
        left = b.understand_pending()['left']
        self.assertGreater(left, 0)
        self.assertEqual(b.today_overview()['unread'], left)
        b.set_understand('off')
        self.assertEqual(b.today_overview()['unread'], 0)

    def test_inbox_first_and_facts_off_by_default(self):  # R3b, R3c
        prefs = self.b.prefs()
        self.assertEqual((prefs['startPage'], prefs['memoryFacts']), ('inbox', False))
        self.assertEqual(self.b.set_prefs({'startPage': 'nope'})['startPage'], 'inbox')


class PageTest(unittest.TestCase):
    """The page side: 今天 in the sidebar and as the first page, R1a/R1b in the reader, one assistant."""
    def setUp(self):
        web = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'
        self.read = lambda name: (web / name).read_text(encoding='utf-8')

    def test_today_page(self):
        page, today = self.read('index.html'), self.read('today.js')
        self.assertIn('id="nav-today"', page)
        self.assertIn('id="reader-task"', page)
        self.assertIn('id="pref-start"', page)
        self.assertLess(page.index('src="memory.js"'), page.index('src="today.js"'))
        self.assertLess(page.index('src="today.js"'), page.index('src="commands.js"'))
        for call in ("api('todayOverview')", "api('todayWaiting')", "api('tasksPending')", "api('taskReview'", "api('taskGet'"):
            self.assertIn(call, today)
        self.assertIn("S.prefs.startPage === 'today'", today)  # R3b: the inbox first unless chosen.
        self.assertIn('已识别的事都处理完了', today)  # V2: never "nothing to do".
        self.assertNotIn('今天没有要你做的事', today)
        self.assertIn("if (S.local) { S.local = null;", self.read('app.js'))  # V1: a search leaves 今天.
        for text in ('esc(t.content', 'esc(t.system)', 'esc(text)', 'esc(tkText(t.content))', 'esc(err.message)'):
            self.assertIn(text, today)  # Task text and AI suggestions are shown as text.
        memory = self.read('memory.js')
        self.assertIn("if (kind !== 'week')", memory)  # 本周概括 left the inbox's 知会 for 今天.
        self.assertNotIn('id="seg-u-', page)  # 要我做 / 知会 / 等回复 left the list head (R4: marks on the rows).
        self.assertIn('class="uc-kind act"', self.read('understand.js'))

    def test_reply_and_strip(self):
        app, understand_js, memory = self.read('app.js'), self.read('understand.js'), self.read('memory.js')
        self.assertIn('draft.sourceMid = mid', app)
        self.assertIn("source: C.draft.sourceMid || C.draft.replySourceId || '', how: C.draft.mode === 'forward' ? 'forward' : 'reply'", app)
        self.assertIn("api('understandAnswered', { mid, how })", understand_js)
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
