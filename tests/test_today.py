"""GitHub Trending (G1, the page that was 今天 R2a), its 在等别人 sub-page, and a sent reply
marking its mail answered (R3a). Demo adapter, local task workbooks, a stand-in network; nothing leaves."""
import unittest
from pathlib import Path

from coremail_desktop.ai import trending, understand
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

    def test_trending_with_chinese_intros_once_a_day(self):  # G1
        b, gets = self.b, []
        b.trending_get = lambda url, timeout=15: gets.append(url) or trending.demo_get(url)
        view = b.trending()
        self.assertEqual((view['ready'], view['ai']), (False, False))  # No AI: the English descriptions only.
        self.assertEqual([r['name'] for r in view['repos']], [r['name'] for r in trending.DEMO_REPOS])
        self.assertNotIn('intro', view['repos'][0])
        self.assertEqual(gets, [trending.TRENDING_URL])  # No READMEs without an AI to read them.
        b.ai_client = DemoAiClient()
        view = b.trending()  # AI set up since: the same list, intros now.
        self.assertTrue(view['ai'])
        self.assertTrue(all('演示模式' in r['intro'] for r in view['repos']))
        self.assertEqual(gets.count(trending.TRENDING_URL), 1)
        calls = []
        b.ai_client = type('Ai', (), {'model': 'm', 'chat': lambda *a, **k: calls.append(a)})()
        self.assertEqual(b.trending(), view)  # Kept for the day.
        self.assertEqual(calls, [])
        b.ai_client = type('Ai', (), {'model': 'm', 'chat': lambda *a, **k: ('not json', 1)})()
        view = b.trending(refresh=True)  # 刷新 fetches again; a bad answer leaves the descriptions.
        self.assertEqual((view['ai'], gets.count(trending.TRENDING_URL)), (False, 2))
        self.assertIn('AI', view['error'])
        b.trending_get = lambda url, timeout=15: (_ for _ in ()).throw(OSError())
        with self.assertRaises(AppError) as caught:
            b.trending(refresh=True)
        self.assertEqual(caught.exception.code, 'TRENDING_UNAVAILABLE')

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
        for call in ("api('trending'", "api('todayWaiting')", "api('tasksPending')", "api('taskReview'", "api('taskGet'", "api('openExternal'"):
            self.assertIn(call, today)
        self.assertNotIn("api('todayOverview')", today)  # G1: the 今天 list gave way to Trending.
        self.assertIn('<span class="fname">Trending</span>', page)
        self.assertIn("S.prefs.startPage === 'today'", today)  # R3b: the inbox first unless chosen.
        for text in ('esc(r.name)', 'esc(r.intro', 'esc(r.description)', 'esc(data.error)'):
            self.assertIn(text, today)  # Repository text from GitHub and the AI is shown as text.
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
