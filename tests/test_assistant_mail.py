"""操作类工具 (A3): mail operations the conversation proposes as plans — refs or a whole
search, limits, folders by name, nothing done until 执行, then 撤销 — and 定时 on drafts.
Scripted models and demo mail only; nothing leaves."""
import json
import unittest
from datetime import datetime
from types import SimpleNamespace

from coremail_desktop.ai.agent import ask
from coremail_desktop.ai.tools import MAIL_PLAN_LIMIT, AssistantTools
from coremail_desktop.app.demo import DemoAiClient
from coremail_desktop.errors import AppError
from tests.test_ai_ask import FakeMailbox, Script, row
from tests.test_assistant import TaskCenterCase

NOW = datetime(2026, 10, 21, 9, 0)
TARGETS = [{'id': '1', 'name': '收件箱', 'role': 'inbox'}, {'id': '4', 'name': '已删除', 'role': 'trash'},
           {'id': '3', 'name': '已发送', 'role': 'sent'}, {'id': '100', 'name': '项目归档', 'role': None},
           {'id': '101', 'name': '项目资料', 'role': None}]


def tools(rows, actions=True, total=None):
    box = FakeMailbox(rows)
    if total is not None:
        search = box.search
        box.search = lambda *a: {**search(*a), 'total': total}
    return box, AssistantTools(box.search, box.read, box.match, {'names': {'1': '收件箱'}, 'trash': '4', 'targets': TARGETS},
                               tasks=lambda v, q: [], task=None, mine=lambda t: True, actions=actions, now=lambda: NOW)


def run(t, name, **args):
    return t.run(name, json.dumps(args, ensure_ascii=False))


class MailToolTest(unittest.TestCase):
    def setUp(self):
        self.rows = [row(1, flags={'read': False}), row(2, flags={'read': True}), row(3, flags={'read': False}),
                     row(4, id='arc:4', flags={'read': False})]
        self.box, self.tools = tools(self.rows)

    def test_offered_only_when_allowed(self):
        names = lambda t: [s['function']['name'] for s in t.specs]
        self.assertIn('plan_mail', names(self.tools))
        self.tools.allow_actions(False)
        self.assertNotIn('plan_mail', names(self.tools))
        self.assertIn('error', run(self.tools, 'plan_mail', action='read', mails=['M1'])[0])
        self.tools.allow_actions(True)
        self.assertEqual(names(self.tools).count('plan_mail'), 1)

    def test_a_whole_search_is_previewed_not_run(self):
        found, _ = run(self.tools, 'search_mail', keywords='预算')
        self.assertEqual(found['search'], 'S1')
        self.assertEqual(run(self.tools, 'search_mail', keywords='预算')[0]['search'], 'S1')  # The same search, the same ref.
        result, step = run(self.tools, 'plan_mail', action='read', search='S1', summary='标为已读')
        self.assertEqual((result['count'], result['skipped'], result['local']), (2, 1, 1))  # Already read; local archive.
        self.assertIn('不要说已经做了', result['note'])
        self.assertEqual(step, '拟定邮件操作：标为已读 2 封')
        plan = self.tools.plans[0]
        self.assertEqual((plan['kind'], plan['op'], plan['mids']), ('mail', 'read', ['mid1', 'mid3']))
        self.assertEqual(self.box.searches[-1][2], 100)  # The whole result, page by page.

    def test_refs_folders_and_refusals(self):
        run(self.tools, 'search_mail', keywords='预算')
        result, _ = run(self.tools, 'plan_mail', action='move', mails=['m1', 'M2'], folder='项目归档')
        self.assertEqual((result['count'], self.tools.plans[-1]['folder'], self.tools.plans[-1]['what']), (2, '100', '移动到「项目归档」'))
        self.assertEqual(run(self.tools, 'plan_mail', action='move', mails=['M1'], folder='归档')[0]['plan'], 'P2')  # Unique part of a name.
        for bad in ({'action': 'move', 'mails': ['M1'], 'folder': '项目'}, {'action': 'move', 'mails': ['M1'], 'folder': '已删除'},
                    {'action': 'move', 'mails': ['M1'], 'folder': '已发送'}, {'action': 'move', 'mails': ['M1']},
                    {'action': 'read', 'mails': ['M9']}, {'action': 'read', 'mails': ['M1'], 'search': 'S1'}, {'action': 'read'},
                    {'action': 'read', 'search': 'S7'}, {'action': 'purge', 'mails': ['M1']}, {'action': 'read', 'mails': ['M2']},
                    {'action': 'read', 'mails': ['M4']}):
            self.assertIn('error', run(self.tools, 'plan_mail', **bad)[0], bad)
        self.assertEqual(len(self.tools.plans), 2)  # No plan from a refused call.

    def test_too_many(self):
        _, many = tools([row(1)], total=MAIL_PLAN_LIMIT + 1)
        run(many, 'search_mail', keywords='通知')
        self.assertIn('缩小范围', run(many, 'plan_mail', action='trash', search='S1')[0]['error'])

    def test_send_at_on_drafts(self):
        t = self.tools
        self.assertEqual(t._send_at('2026-10-22 08:30'), (int(datetime(2026, 10, 22, 8, 30).timestamp() * 1000), None))
        for bad in ('明天早上', '2026-10-21 09:02', '2028-01-01 08:00', '2026-13-01 08:00'):
            self.assertIsNotNone(t._send_at(bad)[1], bad)
        self.assertEqual(t._send_at(''), (None, None))

    def test_agent_rules(self):
        model = Script(('answer', '好'))
        ask('把通知都标为已读', model, self.tools)
        self.assertIn('plan_mail 只是预览', model.seen[0][0][0]['content'])
        self.tools.allow_actions(False)
        model = Script(('answer', '好'))
        ask('把通知都标为已读', model, self.tools)
        self.assertNotIn('plan_mail 只是预览', model.seen[0][0][0]['content'])


class MailBackendTest(TaskCenterCase):
    """A plan run through the list's batch methods, once, then undone, once."""
    def setUp(self):
        super().setUp()
        self.backend.folders()  # As the window does after login: 项目归档 is a target.

    def ask(self, *steps, question='把评审的邮件处理一下'):
        model = Script(*steps)
        self.backend.ai_client = SimpleNamespace(model='m', complete=lambda messages, tools=None, max_tokens=0: (model(messages, tools), 1))
        return self.backend.ask_mailbox(question), model

    def plan(self, **args):
        result, _ = self.ask(('tools', [('search_mail', {'keywords': '评审'})]), ('tools', [('plan_mail', {'search': 'S1', **args})]),
                             ('answer', '请确认'))
        return result['plans'][0]

    def test_move_then_undo(self):
        plan = self.plan(action='move', folder='项目归档')
        view = plan['mail']
        self.assertEqual((plan['kind'], view['op'], view['folder']), ('mail', 'move', '项目归档'))
        self.assertTrue(view['count'] and view['samples'] and plan['basis'])
        mids = [s['mid'] for s in view['samples']]
        origin = {m: self.backend.cache.summary(m)['fid'] for m in mids}
        self.assertNotIn('100', origin.values())  # Nothing moved before 执行.
        done = self.backend.assistant_mail_run(plan['id'])
        self.assertEqual(len(done['done']), view['count'])
        self.assertTrue(all(self.backend.cache.summary(m)['fid'] == '100' for m in mids))
        self.assertIn('用户已执行计划', self.backend._chats[next(iter(self.backend._chats))]['turns'][0][1])
        with self.assertRaises(AppError):
            self.backend.assistant_mail_run(plan['id'])  # Once.
        undone = self.backend.assistant_mail_undo(done['undo'])
        self.assertEqual(len(undone['done']), view['count'])
        self.assertEqual({m: self.backend.cache.summary(m)['fid'] for m in mids}, origin)
        with self.assertRaises(AppError):
            self.backend.assistant_mail_undo(done['undo'])

    def test_flags_and_trash_undo(self):
        plan = self.plan(action='flag')
        mids = [s['mid'] for s in plan['mail']['samples']]
        done = self.backend.assistant_mail_run(plan['id'])
        self.assertTrue(all(self.backend.cache.summary(m)['flags']['flagged'] for m in mids))
        self.backend.assistant_mail_undo(done['undo'])
        self.assertFalse(any(self.backend.cache.summary(m)['flags'].get('flagged') for m in mids))
        trash = self.backend.assistant_mail_run(self.plan(action='trash')['id'])
        self.assertTrue(all(self.backend.cache.summary(m)['fid'] == '4' for m in mids))
        self.backend.assistant_mail_undo(trash['undo'])
        self.assertFalse(any(self.backend.cache.summary(m)['fid'] == '4' for m in mids))

    def test_plan_kinds_and_expiry(self):
        plan = self.plan(action='flag')
        with self.assertRaises(AppError):
            self.backend.assistant_plan_run(plan['id'])  # A mail plan is not a task plan.
        self.backend._plans[plan['id']]['at'] -= self.backend.PLAN_TTL + 1
        with self.assertRaises(AppError):
            self.backend.assistant_mail_run(plan['id'])
        for bad in ('nope', None, 3):
            with self.assertRaises(AppError):
                self.backend.assistant_mail_run(bad)
            with self.assertRaises(AppError):
                self.backend.assistant_mail_undo(bad)

    def test_setting(self):
        self.assertTrue(self.backend.ai_settings()['actions'])
        self.backend.set_ai_settings(actions=False)
        self.assertFalse(self.backend.ai_settings()['actions'])
        _, model = self.ask(('answer', '请自己在列表里操作'))
        self.assertNotIn('plan_mail', [s['function']['name'] for s in model.seen[0][1]])
        self.backend.set_ai_settings(actions=True)
        _, model = self.ask(('answer', '好'))
        self.assertIn('plan_mail', [s['function']['name'] for s in model.seen[0][1]])

    def test_draft_scheduled(self):
        mid = self.backend.messages('1', 0, 50)['rows'][0]['id']
        self.backend.task_link(330, mid)
        later = datetime.now().replace(second=0, microsecond=0)
        at = f'{later.year + 1 if later.month == 12 else later.year}-{later.month % 12 + 1:02d}-01 09:00'
        result, _ = self.ask(('tools', [('read_task', {'ref': 'T330'})]), ('tools', [('task_thread', {'task': 'T330'})]),
                             ('tools', [('draft_reply', {'mail': 'M1', 'purpose': '告知', 'task': 'T330', 'send_at': at})]), ('answer', '看卡片'))
        plan = result['plans'][0]
        ms = int(datetime.strptime(at, '%Y-%m-%d %H:%M').timestamp() * 1000)
        self.assertEqual(plan['draft']['scheduleAt'], ms)
        self.assertEqual(self.backend.assistant_draft_compose(plan['id'], 'reply')['scheduleAt'], ms)

    def test_demo(self):
        self.backend.ai_client = DemoAiClient()
        result = self.backend.ask_mailbox('把「评审」的邮件移到项目归档')
        plan = result['plans'][0]
        self.assertEqual((plan['kind'], plan['mail']['folder']), ('mail', '项目归档'))
        self.assertIn('撤销', result['answer'])
        self.backend.assistant_mail_run(plan['id'])


class PageTest(unittest.TestCase):
    def test_wiring(self):
        from pathlib import Path
        web = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'
        source = (web / 'ai.js').read_text(encoding='utf-8')
        for field in ('m.what', 's.subject', 's.fromName', 'p.basis', 'p.summary', 'notes'):
            self.assertIn(f'esc({field}', source)
        self.assertIn("api('assistantMailRun', { plan: p.id })", source)
        self.assertIn("api('assistantMailUndo', { undo })", source)
        self.assertIn('id="ai-actions"', (web / 'index.html').read_text(encoding='utf-8'))
        from coremail_desktop.app.shell import AI_METHODS, BACKEND_METHODS, FAST_METHODS, TASK_METHODS
        for name in ('assistantMailRun', 'assistantMailUndo'):  # The mail thread, like the list's batch buttons.
            self.assertIn(name, BACKEND_METHODS)
            self.assertNotIn(name, AI_METHODS | FAST_METHODS | TASK_METHODS)


if __name__ == '__main__':
    unittest.main()
