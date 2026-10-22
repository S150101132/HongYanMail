"""个人工作助理 W1–W3: the conversation reads the task center and 待我处理 — task refs, the
"mine" rule, citations of tasks, the task brought along — proposes task changes that run
only on 执行 (the plan cache), and drafts mail from a task's conversation that the user
opens, checks and sends. Scripted models only; nothing leaves."""
import json
import unittest
from types import SimpleNamespace

from coremail_desktop.ai import agent
from coremail_desktop.ai.agent import ask
from coremail_desktop.ai.tools import SPECS, AssistantTools
from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter, DemoAiClient
from coremail_desktop.errors import AppError
from tests.test_ai_ask import FakeMailbox, Script, row
from tests.test_app_backend import XorProtector, local_services_stub
from tests.test_compose_v05 import scratch
from tests.test_tasks import Clock, folder_with, task_row


def task(i, **values):
    return {'id': i, 'system': 'CRM', 'content': f'任务{i}', 'priority': '', 'status': '进行中', 'latest': '', 'owner': '',
            'start': '', 'due': '', 'end': '', 'blockers': '', 'next': '', 'updated': '', 'history': '很长的历史', 'acceptance': '',
            'overdue': False, **values}


class Board:
    """A task center and 待我处理 the tools can read."""
    def __init__(self, tasks, pending=None):
        self.tasks, self.pending_rows = {t['id']: t for t in tasks}, pending

    def list(self, view, query):
        return [t for t in self.tasks.values() if not query or query in t['content']]

    def get(self, task_id):
        if task_id not in self.tasks:
            raise AppError('TASK_NOT_FOUND')
        t = self.tasks[task_id]
        return {'task': t, 'overdue': t['overdue'], 'events': [{'date': '2026-10-19', 'kind': 'progress', 'text': '已发邮件'}],
                'links': [{'mid': 'mid2', 'subject': '主题2', 'date': '2026-10-12', 'row': row(2)}]}

    def tools(self, box=None, plan=None, thread=None, groups=None):
        box = box or FakeMailbox()
        return AssistantTools(box.search, box.read, box.match, {'names': {}}, tasks=self.list, task=self.get,
                              mine=lambda t: t['owner'] in ('', '用户本人'),
                              pending=(lambda: self.pending_rows) if self.pending_rows is not None else None, plan=plan,
                              thread=thread, groups=groups)


class Previewer:
    """center.preview stand-in: records what it was asked; complete on a done task fails."""
    def __init__(self, board):
        self.board, self.seen = board, []

    def __call__(self, actions):
        self.seen.append(actions)
        out = []
        for a in actions:
            t = self.board.tasks.get(a.get('id'), {})
            if a['op'] == 'complete' and t.get('status') == '已完成':
                out.append({'op': a['op'], 'id': a['id'], 'error': 'TASK_ARCHIVED', 'message': '已归档'})
            else:
                out.append({'op': a['op'], 'id': a.get('id') or 99, 'status': t.get('status', ''), 'content': t.get('content', ''),
                            'changes': {'status': [t.get('status', ''), '已完成']}})
        return out


class ToolsTest(unittest.TestCase):
    def test_list_tasks_mine_and_order(self):
        board = Board([task(5), task(7, owner='张三'), task(9, due='2026-10-30'), task(3, due='2026-10-01', overdue=True)])
        tools = board.tools()
        result, step = tools.run('list_tasks', json.dumps({'mine': True}))
        self.assertEqual([r['ref'] for r in result['rows']], ['T3', 'T9', 'T5'])  # Overdue, then by due date, then the rest.
        self.assertEqual(result['total'], 3)
        self.assertIn('只看我负责的', step)
        self.assertEqual(len(tools.run('list_tasks', '{}')[0]['rows']), 4)
        self.assertNotIn('history', result['rows'][0])
        self.assertEqual(set(tools.task_refs), {'T3', 'T5', 'T7', 'T9'})

    def test_read_task_links_become_mail_refs(self):
        tools = Board([task(12, next='问客户')]).tools()
        result, _ = tools.run('read_task', '{"ref": "t12"}')
        self.assertEqual(result['fields']['next'], '问客户')
        self.assertNotIn('history', result['fields'])  # The long history stays out; the last 5 steps go in.
        self.assertEqual(result['progress'][0]['text'], '已发邮件')
        self.assertEqual(result['mails'][0]['ref'], 'M1')
        self.assertEqual(tools.mails['M1']['id'], 'mid2')  # read_mail can open it now.
        self.assertIn('error', tools.run('read_task', '{"ref": "T99"}')[0])
        self.assertIn('error', tools.run('read_task', '{"ref": "12"}')[0])

    def test_pending_only_when_the_understanding_is_on(self):
        off = Board([]).tools()
        self.assertNotIn('pending_mail', [s['function']['name'] for s in off.specs])
        self.assertIn('error', off.run('pending_mail', '{}')[0])
        on = Board([], pending=[{**row(4), 'card': {'ask': '回复报价', 'gist': '', 'due': '2026-10-22', 'project': 'CRM'}}]).tools()
        result, step = on.run('pending_mail', '{}')
        self.assertEqual((result['rows'][0]['ref'], result['rows'][0]['ask'], step), ('M1', '回复报价', '要我做：1 封'))
        self.assertEqual(on.specs[:len(SPECS)], SPECS)


class PlanToolTest(unittest.TestCase):
    def setUp(self):
        self.board = Board([task(12), task(14, status='已完成')])
        self.preview = Previewer(self.board)
        self.tools = self.board.tools(plan=self.preview)
        self.tools.run('list_tasks', '{"view": "all"}')

    def plan(self, actions, summary='完成'):
        return self.tools.run('plan_tasks', json.dumps({'summary': summary, 'actions': actions}, ensure_ascii=False))

    def test_offered_only_with_a_previewer(self):
        self.assertIn('plan_tasks', [s['function']['name'] for s in self.tools.specs])
        read_only = self.board.tools()
        self.assertNotIn('plan_tasks', [s['function']['name'] for s in read_only.specs])
        self.assertIn('error', read_only.run('plan_tasks', '{"actions": [{"op": "complete", "task": "T12"}]}')[0])

    def test_plan_is_previewed_and_kept_not_run(self):
        result, step = self.plan([{'op': 'complete', 'task': 't12', 'mails': [{'mid': 'x'}]}, {'op': 'complete', 'id': 14}])
        self.assertEqual(self.preview.seen[0][0]['id'], 12)
        self.assertNotIn('mails', self.preview.seen[0][0])  # Only the app links mail to an operation.
        self.assertEqual(result['plan'], 'P1')
        self.assertEqual([(i['task'], i['ok']) for i in result['items']], [('T12', True), ('T14', False)])
        self.assertEqual(result['items'][1]['problem'], '已归档')
        self.assertIn('不要说已经改好了', result['note'])
        self.assertEqual(step, '拟定任务操作：1 项待确认，1 项不能执行')
        self.assertEqual([(p['ref'], p['summary'], len(p['actions'])) for p in self.tools.plans], [('P1', '完成', 2)])
        self.tools.begin()
        self.assertEqual(self.tools.plans, [])
        self.assertEqual(self.plan([{'op': 'complete', 'task': 'T12'}])[0]['plan'], 'P2')  # Refs stay unique in a conversation.

    def test_refused_plans(self):
        for actions in ([{'op': 'complete', 'task': 'T77'}],  # Never returned by a tool.
                        [{'op': 'complete'}],
                        [{'op': 'complete', 'task': 'T12'}, {'op': 'progress', 'task': 'T12', 'text': 'x'}],
                        [{'op': 'delete', 'task': 'T12'}], [], ['T12'], [{'op': 'create', 'fields': {'content': 'x'}}] * 11):
            self.assertIn('error', self.plan(actions)[0], actions)
        self.assertEqual(self.preview.seen, [])
        self.assertIn('error', self.tools.run('plan_tasks', '{"actions": "T12"}')[0])
        self.assertEqual(self.plan([{'op': 'create', 'fields': {'content': '新任务'}}])[0]['items'][0]['task'], '')
        for _ in range(2):
            self.plan([{'op': 'complete', 'task': 'T12'}])
        self.assertIn('最多', self.plan([{'op': 'complete', 'task': 'T12'}])[0]['error'])

    def test_task_center_not_ready(self):
        def closed(actions):
            raise AppError('TASK_NOT_IMPORTED')
        tools = self.board.tools(plan=closed)
        tools.run('list_tasks', '{}')
        result, _ = tools.run('plan_tasks', '{"actions": [{"op": "complete", "task": "T12"}]}')
        self.assertIn('TASK_NOT_IMPORTED', result['error'])
        self.assertEqual(tools.plans, [])

    def test_agent_rules_and_plans(self):
        model = Script(('tools', [('plan_tasks', {'actions': [{'op': 'complete', 'task': 'T12'}]})]), ('answer', '请确认 [T12]'))
        result = ask('T12 已完成', model, self.tools)
        system = model.seen[0][0][0]['content']
        self.assertIn('plan_tasks', system)
        self.assertNotIn('只能查看', system)
        self.assertEqual([p['ref'] for p in result['plans']], ['P1'])


def mail(i, sender, mine=False, people=()):
    return {**row(i, **{'from': sender, 'fromEmail': sender.rpartition('<')[2].strip('>')}), 'mine': mine,
            'people': [{'name': n, 'email': e} for n, e in people]}


class DraftToolTest(unittest.TestCase):
    """task_thread, list_groups and the draft plans: refs only, never addresses to the model."""
    def setUp(self):
        self.board = Board([task(12), task(14)])
        client = ('Ali', 'ali@cust.com')
        self.rows = [mail(1, '"Ali" <ali@cust.com>', people=[client, ('王五', 'wang@inspur.com')]),
                     mail(2, '"我" <me@inspur.com>', mine=True, people=[client]),
                     mail(3, '"Ali" <ali@cust.com>', people=[client, ('Bob', 'bob@cust.com')])]
        self.asked = []

        def groups(addresses):
            self.asked.append(sorted(addresses))
            return [{'id': 'g-uuid', 'name': '沙特项目组', 'count': 3, 'to': 2, 'cc': 1, 'domains': ['cust.com'], 'external': 2,
                     'overlap': 1 if addresses else 0}]
        self.tools = self.board.tools(plan=Previewer(self.board), groups=groups,
                                      thread=lambda i: {'rows': self.rows if i == 12 else [], 'internal': {'inspur.com'}})
        self.tools.run('list_tasks', '{}')

    def run_tool(self, name, **args):
        return self.tools.run(name, json.dumps(args, ensure_ascii=False))

    def test_offered_only_with_thread_and_groups(self):
        names = [s['function']['name'] for s in self.tools.specs]
        self.assertTrue({'task_thread', 'list_groups', 'draft_reply', 'draft_new'} <= set(names))
        plain = self.board.tools(plan=Previewer(self.board))
        self.assertNotIn('draft_reply', [s['function']['name'] for s in plain.specs])
        self.assertIn('error', plain.run('draft_reply', '{"mail": "M1", "purpose": "x"}')[0])

    def test_task_thread(self):
        result, step = self.run_tool('task_thread', task='T12')
        self.assertEqual([(m['ref'], m['mine']) for m in result['mails']], [('M1', False), ('M2', True), ('M3', False)])
        self.assertEqual((result['last'], result['last_from_other'], result['replied_after_it']), ('M3', 'M3', False))
        self.assertEqual([(c['name'], c['domain']) for c in result['external_contacts']], [('Ali', 'cust.com'), ('Bob', 'cust.com')])
        self.assertNotIn('@cust.com', json.dumps(result))  # Contacts are refs, names and domains.
        self.assertEqual(step, '查看 T12 的邮件会话：3 封')
        self.rows.append(mail(4, '"我" <me@inspur.com>', mine=True))
        self.assertTrue(self.run_tool('task_thread', task='12')[0]['replied_after_it'])
        self.assertIn('hint', self.run_tool('task_thread', task='T14')[0])
        self.assertIn('error', self.run_tool('task_thread', task='T99')[0])

    def test_groups_rank_by_the_thread_and_hide_addresses(self):
        self.assertNotIn('overlap', self.run_tool('list_groups')[0]['groups'][0])
        self.run_tool('task_thread', task='T12')
        result, _ = self.run_tool('list_groups', task='T12')
        self.assertEqual((result['groups'][0]['ref'], result['groups'][0]['overlap']), ('G1', 1))
        self.assertIn('ali@cust.com', self.asked[-1])  # The overlap is counted here, not by the model.
        self.assertNotIn('g-uuid', json.dumps(result))
        self.assertEqual(self.run_tool('list_groups')[0]['groups'][0]['ref'], 'G1')  # Refs stay.

    def test_draft_reply_plan(self):
        self.run_tool('task_thread', task='T12')
        self.run_tool('list_groups', task='T12')
        result, step = self.run_tool('draft_reply', mail='m3', purpose='询问端口', points=['端口', '', 'Base DN'] + ['x'] * 9, group='G1')
        self.assertEqual(result['plan'], 'P1')
        self.assertIn('不要说已经写好或发出', result['note'])
        plan = self.tools.plans[0]
        self.assertEqual((plan['kind'], plan['mid'], plan['group'], plan['task'], plan['mode']), ('draft', 'mid3', 'g-uuid', 12, 'reply'))
        self.assertEqual(plan['points'][:2], ['端口', 'Base DN'])
        self.assertEqual(len(plan['points']), 7)  # At most 8 lines; blanks dropped.
        self.assertEqual(plan['mail']['domain'], 'cust.com')
        self.assertIn('ali@cust.com', plan['people'])
        self.assertIn('收件人组', step)
        for bad in ({'mail': 'M9', 'purpose': 'x'}, {'mail': 'M3', 'purpose': ''}, {'mail': 'M3', 'purpose': 'x', 'group': 'G7'}):
            self.assertIn('error', self.run_tool('draft_reply', **bad)[0], bad)

    def test_draft_new_only_to_known_contacts(self):
        self.run_tool('task_thread', task='T12')
        result, _ = self.run_tool('draft_new', to=['P1'], subject='Next step', purpose='约时间', task='T12')
        plan = self.tools.plans[0]
        self.assertEqual(plan['contacts'], [{'name': 'Ali', 'email': 'ali@cust.com'}])
        self.assertEqual((plan['reply'], plan['task'], plan['subject']), (False, 12, 'Next step'))
        for bad in ({'to': ['ali@cust.com'], 'subject': 's', 'purpose': 'p'}, {'to': ['P9'], 'subject': 's', 'purpose': 'p'},
                    {'subject': 's', 'purpose': 'p'}, {'to': ['P1'], 'subject': '', 'purpose': 'p'}):
            self.assertIn('error', self.run_tool('draft_new', **bad)[0], bad)

    def test_agent_drafting_rules(self):
        model = Script(('answer', '好'))
        ask('回个邮件', model, self.tools)
        system = model.seen[0][0][0]['content']
        self.assertIn('你永远不能发信', system)
        self.assertNotIn('请用户自己写信', system)
        model = Script(('answer', '好'))
        ask('回个邮件', model, self.board.tools(plan=Previewer(self.board)))
        self.assertIn('请用户自己写信', model.seen[0][0][0]['content'])

    def test_link_candidates_for_a_task_without_mail(self):
        self.assertIn('suggest_links', self.run_tool('task_thread', task='T14')[0]['hint'])
        self.run_tool('search_mail', keywords='LDAP')
        result, step = self.run_tool('suggest_links', task='T14', mails=['m1', 'M2', 'M1'])
        self.assertEqual((result['plan'], step), ('P1', '提议关联邮件：2 封候选'))
        self.assertIn('不要说已经关联', result['note'])
        plan = self.tools.plans[0]
        self.assertEqual((plan['kind'], plan['task'], [m['mid'] for m in plan['mails']]), ('link', 14, ['mid1', 'mid2']))
        for bad in ({'task': 'T14', 'mails': ['M9']}, {'task': 'T14', 'mails': []}, {'task': 'T99', 'mails': ['M1']},
                    {'task': 'T14', 'mails': ['M1'] * 2 + [f'M{i}' for i in range(2, 7)]}):
            self.assertIn('error', self.run_tool('suggest_links', **bad)[0], bad)
        model = Script(('answer', '好'))
        ask('回个邮件', model, self.tools)
        self.assertIn('suggest_links', model.seen[0][0][0]['content'])


class AgentTest(unittest.TestCase):
    def test_task_citations_and_rules(self):
        tools = Board([task(3, due='2026-10-01', overdue=True), task(5)]).tools()
        model = Script(('tools', [('list_tasks', {'mine': True})]), ('answer', '两条：[T3] 已到期，[T5]，[T99] 不存在。'))
        result = ask('哪些要我做', model, tools)
        self.assertEqual(result['answer'], '两条：[T3] 已到期，[T5]， 不存在。')
        self.assertEqual([(t['ref'], t['id'], t['status']) for t in result['tasks']], [('T3', 3, '进行中'), ('T5', 5, '进行中')])
        self.assertEqual(result['sources'], [])
        system = model.seen[0][0][0]['content']
        self.assertIn('list_tasks', system)
        self.assertIn('只能查看', system)
        self.assertEqual(model.seen[0][1], tools.specs)

    def test_current_task_and_follow_up(self):
        tools = Board([task(12, content='LDAP 对接')]).tools()
        current = tools.add_task(tools._task(12)['task'])
        model = Script(('answer', '这条还在进行中 [T12]'))
        ask('这个任务怎么样了', model, tools, current_task=current)
        self.assertIn('用户附带了当前任务 T12｜CRM｜LDAP 对接｜进行中', model.seen[0][0][1]['content'])
        tools.begin()
        model = Script(('answer', '答'))
        ask('它的下一步呢', model, tools, history=[('这个任务怎么样了', '这条还在进行中 [T12]')], known=['T12'])
        self.assertIn('T12｜CRM｜LDAP 对接', model.seen[0][0][1]['content'])  # Known from the last turn.

    def test_mail_only_tools_unchanged(self):
        model = Script(('answer', '答 [T1]'))
        result = ask('问', model, FakeMailbox().tools())
        self.assertEqual(result['tasks'], [])
        self.assertNotIn('list_tasks', model.seen[0][0][0]['content'])
        self.assertEqual(model.seen[0][1], SPECS)


class TaskCenterCase(unittest.TestCase):
    """A demo account with a small live task list."""
    def setUp(self):
        self.backend = MailBackend(scratch(self), adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(self.backend.logout)
        self.addCleanup(lambda: self.backend.tasks and self.backend.tasks.close())
        folder = folder_with(self, open_rows=[task_row(302, '受阻', blockers='等待客户', owner='张三'),
                                              task_row(330, '进行中', owner='用户本人', due='2026-10-01'), task_row(333)])
        self.backend.tasks_set_folder(str(folder))
        self.backend.tasks_import('live')
        self.backend.tasks.clock = Clock()


class BackendTest(TaskCenterCase):
    def test_mine(self):
        mine = self.backend._assist_mine
        self.assertTrue(mine({'owner': ''}) and mine({'owner': '用户本人'}) and mine({'owner': '我和 demo 一起'}))
        self.assertFalse(mine({'owner': '张三'}))

    def test_asks_about_tasks_with_the_task_brought_along(self):
        model = Script(('tools', [('list_tasks', {'mine': True})]), ('answer', '要你做的：[T330] 已到期，[T333]'),
                       ('tools', [('read_task', {'ref': 'T302'})]), ('answer', '在等客户 [T302]'))
        self.backend.ai_client = SimpleNamespace(model='m', complete=lambda messages, tools=None, max_tokens=0: (model(messages, tools), 1))
        first = self.backend.ask_mailbox('哪些要我做')
        listed = json.loads([m for m in model.seen[1][0] if m['role'] == 'tool'][0]['content'])
        self.assertEqual([r['ref'] for r in listed['rows']], ['T330', 'T333'])  # 张三's task is not mine.
        self.assertTrue(listed['rows'][0]['overdue'])
        self.assertEqual([t['id'] for t in first['tasks']], [330, 333])
        second = self.backend.ask_mailbox('这个任务卡在哪', chat=first['chat'], current_task=302)
        self.assertIn('用户附带了当前任务 T302', '\n'.join(m['content'] for m in model.seen[2][0] if m['role'] == 'system'))
        self.assertEqual([t['ref'] for t in second['tasks']], ['T302'])
        model.steps.append(('answer', '答'))
        self.backend.ask_mailbox('还有呢', chat=first['chat'], current_task=999)  # A task deleted meanwhile is just left out.
        self.assertNotIn('用户附带了当前任务 T', '\n'.join(m['content'] for m in model.seen[4][0] if m['role'] == 'system'))
        for bad in ('302', True, -1, 100000):
            with self.assertRaises(AppError):
                self.backend.ask_mailbox('问', current_task=bad)

    def test_pending_leaves_out_linked_mail(self):
        self.backend.understand_view = lambda kind='act': {'rows': [{'id': 'a'}, {'id': 'b'}], 'total': 2}
        self.backend.tasks.store.links = lambda task_id=None: [{'mid': 'a'}]
        self.assertEqual(self.backend._assist_pending(), [{'id': 'b'}])

    def test_demo_lists_tasks(self):
        self.backend.ai_client = DemoAiClient()
        result = self.backend.ask_mailbox('现在有什么任务，哪些需要我做？')
        self.assertTrue(result['answer'].startswith('演示模式：你负责的未完成任务有 2 条'))
        self.assertEqual([t['id'] for t in result['tasks']], [330, 333])
        again = self.backend.ask_mailbox('这个任务现在怎么样', chat=result['chat'], current_task=302)
        self.assertIn('受阻', again['answer'])


class PlanBackendTest(TaskCenterCase):
    """The plan cache: proposed on the AI thread, run by id on 执行, once."""
    def ask(self, *steps, **kwargs):
        model = Script(*steps)
        self.backend.ai_client = SimpleNamespace(model='m', complete=lambda messages, tools=None, max_tokens=0: (model(messages, tools), 1))
        return self.backend.ask_mailbox(kwargs.pop('question', '330 已完成'), **kwargs), model

    def complete_330(self, **kwargs):
        return self.ask(('tools', [('read_task', {'ref': 'T330'})]), ('tools', [('plan_tasks', {'summary': '完成 #330', 'actions': [
            {'op': 'complete', 'task': 'T330'}]})]), ('answer', '请确认 [T330]'), **kwargs)

    def test_propose_then_run_once(self):
        result, model = self.complete_330()
        plan = result['plans'][0]
        self.assertEqual((plan['ref'], plan['summary']), ('P1', '完成 #330'))
        self.assertNotIn('actions', plan)  # The page holds only the id.
        self.assertEqual(plan['preview'][0]['changes']['status'][1], '已完成')
        self.assertEqual(self.backend.tasks.store.tasks[330]['status'], '进行中')  # Nothing written before 执行.
        done = self.backend.assistant_plan_run(plan['id'])
        self.assertEqual((done['done'], done['failed']), ([{'op': 'complete', 'id': 330}], []))
        self.assertEqual(self.backend.tasks.store.tasks[330]['status'], '已完成')
        self.assertIn('用户已确认执行计划 P1', self.backend._chats[result['chat']]['turns'][0][1])
        with self.assertRaises(AppError) as caught:
            self.backend.assistant_plan_run(plan['id'])
        self.assertEqual(caught.exception.code, 'AI_PLAN_EXPIRED')
        for bad in ('nope', None, 3):
            with self.assertRaises(AppError):
                self.backend.assistant_plan_run(bad)

    def test_errors_explained_and_plans_expire_or_get_replaced(self):
        result, _ = self.ask(('tools', [('read_task', {'ref': 'T302'})]), ('tools', [('plan_tasks', {'actions': [
            {'op': 'update', 'task': 'T302', 'fields': {'status': '已完成'}}]})]), ('answer', '不能这样改'), question='302 改成已完成')
        self.assertTrue(result['plans'][0]['preview'][0]['message'])  # A fixed code's text, as in 任务中心.
        first, _ = self.complete_330()
        second, _ = self.complete_330(chat=first['chat'], question='还是 330 已完成')
        with self.assertRaises(AppError):  # The newer plan for #330 replaced the older one.
            self.backend.assistant_plan_run(first['plans'][0]['id'])
        self.backend._plans[second['plans'][0]['id']]['at'] -= self.backend.PLAN_TTL + 1
        with self.assertRaises(AppError):
            self.backend.assistant_plan_run(second['plans'][0]['id'])
        self.assertEqual(self.backend.tasks.store.tasks[330]['status'], '进行中')

    def test_demo_plans_with_the_task_brought_along(self):
        self.backend.ai_client = DemoAiClient()
        result = self.backend.ask_mailbox('这个任务已完成', current_task=330)
        self.assertIn('请在下面的卡片上核对', result['answer'])
        self.assertEqual(result['plans'][0]['preview'][0]['op'], 'complete')
        self.backend.assistant_plan_run(result['plans'][0]['id'])
        self.assertEqual(self.backend.tasks.store.tasks[330]['status'], '已完成')
        progress = self.backend.ask_mailbox('#333 记一下：已发邮件给客户')
        self.assertEqual(progress['plans'][0]['preview'][0]['op'], 'progress')


class DraftBackendTest(TaskCenterCase):
    """A draft plan from a task's conversation: composer values, groups, the remembered
    group, the body, and the progress line offered after sending."""
    def setUp(self):
        super().setUp()
        self.mid = self.backend.messages('1', 0, 50)['rows'][0]['id']
        self.backend.task_link(330, self.mid)
        self.group = self.backend.save_recipient_group({'name': '沙特项目组', 'members': [
            {'email': 'ali@cust.com', 'field': 'to'}, {'email': 'wang@example.invalid', 'field': 'cc'}]})

    def plan(self, question='330 回个邮件问问', group=None, **kwargs):
        steps = [('tools', [('read_task', {'ref': 'T330'})]), ('tools', [('task_thread', {'task': 'T330'})])]
        if group:
            steps.append(('tools', [('list_groups', {'task': 'T330'})]))
        steps += [('tools', [('draft_reply', {'mail': 'M1', 'purpose': '询问具体信息', 'points': ['还缺端口'], 'task': 'T330',
                                              **({'group': group} if group else {})})]), ('answer', '请看卡片 [T330]')]
        model = Script(*steps)
        self.backend.ai_client = SimpleNamespace(model='m', complete=lambda messages, tools=None, max_tokens=0: (model(messages, tools), 1))
        return self.backend.ask_mailbox(question, **kwargs)['plans'][0]

    def test_plan_view_has_no_addresses(self):
        plan = self.plan(group='G1')
        view = plan['draft']
        self.assertEqual((plan['kind'], view['reply'], view['group'], view['task'], view['mode']), ('draft', True, self.group['id'], 330, 'reply'))
        self.assertEqual(view['groups'][0]['external'], 1)
        self.assertNotIn('ali@cust.com', json.dumps(plan, ensure_ascii=False))
        self.assertEqual(view['points'], ['还缺端口'])
        self.assertNotIn('mid', view)

    def test_compose_reply_or_group(self):
        plan = self.plan()
        reply = self.backend.compose('reply', self.mid)
        value = self.backend.assistant_draft_compose(plan['id'], 'reply')
        self.assertEqual((value['to'], value['subject'], value['replySourceId']), (reply['to'], reply['subject'], reply['replySourceId']))
        self.assertTrue(value['assist'])
        grouped = self.backend.assistant_draft_compose(plan['id'], self.group['id'])
        self.assertEqual((grouped['to'], grouped['cc']), (['ali@cust.com'], ['wang@example.invalid']))
        self.assertEqual((grouped['subject'], grouped['replySourceId']), (reply['subject'], reply['replySourceId']))
        kept = self.backend.assistant_draft_compose(plan['id'], self.group['id'], keep_sender=True)
        self.assertEqual(kept['to'], reply['to'][:1] + ['ali@cust.com'])
        # The task remembers the group (its id only) as the next card's default; a deleted group falls back.
        self.assertEqual(self.backend.tasks.store.get('assist_group:330'), self.group['id'])
        self.assertEqual(self.plan()['draft']['group'], self.group['id'])
        self.backend.delete_recipient_group(self.group['id'])
        self.assertEqual(self.plan()['draft']['group'], '')
        for bad in ('new', 'nope', 3):
            with self.assertRaises(AppError):
                self.backend.assistant_draft_compose(plan['id'], bad)

    def test_new_mail_to_chosen_contacts(self):
        tools_plan = {'ref': 'P1', 'kind': 'draft', 'summary': 's', 'actions': [], 'reply': False, 'group': '', 'task': 330, 'people': [],
                      'contacts': [{'name': 'Ali', 'email': 'ali@cust.com'}, {'name': '', 'email': 'bob@cust.com'}],
                      'subject': 'Next step', 'purpose': '约时间', 'points': []}
        plan = self.backend._keep_plan(tools_plan, 'chat', '问', 1)
        self.assertEqual([c['external'] for c in plan['draft']['contacts']], [True, True])
        value = self.backend.assistant_draft_compose(plan['id'], '', contacts=[1])
        self.assertEqual((value['to'], value['subject'], value['mode']), (['bob@cust.com'], 'Next step', 'new'))
        both = self.backend.assistant_draft_compose(plan['id'], self.group['id'])
        self.assertEqual(both['to'], ['"Ali" <ali@cust.com>', 'bob@cust.com'])  # Ali is in the group too: once.
        for bad in ([5], ['0'], 'all'):
            with self.assertRaises(AppError):
                self.backend.assistant_draft_compose(plan['id'], '', contacts=bad)
        self.backend.ai_client = DemoAiClient()
        self.assertIn('Dear all', self.backend.assistant_draft_write(plan['id'], points=['周四'])['text'])

    def test_body_then_progress_offered_once_after_sending(self):
        plan = self.plan()
        self.backend.assistant_draft_compose(plan['id'], 'reply')
        self.backend.ai_client = DemoAiClient()
        text = self.backend.assistant_draft_write(plan['id'], points=['端口', 'Base DN'])['text']
        self.assertIn('1. 端口', text)
        next_plan = self.backend.assistant_sent(plan['id'])
        self.assertEqual((next_plan['kind'], next_plan['preview'][0]['op'], next_plan['preview'][0]['id']), ('tasks', 'progress', 330))
        self.assertIsNone(self.backend.assistant_sent(plan['id']))  # Offered once.
        self.assertIn('用户已发出', self.backend._chats[next(iter(self.backend._chats))]['turns'][0][1])
        self.assertNotIn('已发邮件给', ''.join(self.backend.tasks.store.tasks[330]['history']))  # Not written until 执行.
        self.backend.assistant_plan_run(next_plan['id'])
        self.assertIn('已发邮件给', str(self.backend.tasks.store.tasks[330]['history']))

    def test_plan_ids_are_checked_by_kind(self):
        plan = self.plan()
        with self.assertRaises(AppError):
            self.backend.assistant_plan_run(plan['id'])  # A draft plan does not run as task operations.
        for bad in ('nope', None):
            for method in (self.backend.assistant_draft_compose, self.backend.assistant_sent):
                with self.assertRaises(AppError):
                    method(bad)
        with self.assertRaises(AppError):
            self.backend.assistant_draft_write(plan['id'], points='端口')

    def test_demo_drafts_a_reply_for_the_task_brought_along(self):
        self.backend.ai_client = DemoAiClient()
        result = self.backend.ask_mailbox('这个任务回个邮件问问具体信息', current_task=330)
        self.assertIn('起草', result['answer'])
        self.assertEqual(result['plans'][0]['kind'], 'draft')
        named = self.backend.ask_mailbox('这个任务回给沙特项目组', current_task=330)
        self.assertEqual(named['plans'][0]['draft']['group'], self.group['id'])


class NudgeBackendTest(TaskCenterCase):
    """提醒回信 (W4): after 执行, once, when the task's conversation ends with someone else's
    unanswered mail; 「不用」 or a sent reply holds until that last mail changes."""
    def setUp(self):
        super().setUp()
        self.mid = self.backend.messages('1', 0, 50)['rows'][0]['id']
        self.backend.task_link(330, self.mid)

    def run_plan(self, *actions, task='T330'):
        model = Script(('tools', [('read_task', {'ref': task})]), ('tools', [('plan_tasks', {'actions': list(actions)})]), ('answer', '请确认'))
        self.backend.ai_client = SimpleNamespace(model='m', complete=lambda messages, tools=None, max_tokens=0: (model(messages, tools), 1))
        plan = self.backend.ask_mailbox('改任务')['plans'][0]
        return self.backend.assistant_plan_run(plan['id'])

    def test_offered_once_after_completing(self):
        done = self.run_plan({'op': 'complete', 'task': 'T330'})
        nudge = self.backend.assistant_nudge(done['nudge'])
        self.assertIn('你还没回。要回一封告诉对方已完成吗？', nudge['text'])
        plan = nudge['plan']
        self.assertEqual((plan['kind'], nudge['task'], plan['draft']['reply'], plan['draft']['purpose'], plan['draft']['mode']),
                         ('draft', 330, True, '告知已完成', 'reply'))
        row = self.backend.cache.summary(self.mid)
        self.assertNotIn(row['fromEmail'], json.dumps(nudge, ensure_ascii=False))  # Names and domains only.
        self.assertIn('助理提醒', self.backend._chats[next(iter(self.backend._chats))]['turns'][0][1])
        with self.assertRaises(AppError):
            self.backend.assistant_nudge(done['nudge'])  # Asked once.
        value = self.backend.assistant_draft_compose(plan['id'], 'reply')  # 起草 goes the W3 way.
        self.assertEqual(value['to'], self.backend.compose('reply', self.mid)['to'])

    def test_dismissed_until_the_last_mail_changes(self):
        nudge = self.backend.assistant_nudge(self.run_plan({'op': 'progress', 'task': 'T330', 'text': '端口已确认'})['nudge'])
        self.assertEqual((nudge['plan']['draft']['purpose'], nudge['plan']['draft']['points']), ('告知最新进展', ['端口已确认']))
        self.assertTrue(self.backend.assistant_nudge_dismiss(nudge['plan']['id']))
        self.assertEqual(self.backend.tasks.store.get('assist_nudge:330'), self.mid)
        self.assertIsNone(self.backend.assistant_nudge(self.run_plan({'op': 'progress', 'task': 'T330', 'text': '再记一条'})['nudge']))
        thread = self.backend._assist_thread  # A newer mail in the conversation: asked again.
        newer = {'id': 'newer', 'subject': '回复：新的问题', 'from': '"Ali" <ali@cust.invalid>', 'date': '2026-10-21 08:00', 'mine': False, 'people': []}
        self.backend._assist_thread = lambda i: {**thread(i), 'rows': thread(i)['rows'] + [newer]}
        again = self.backend.assistant_nudge(self.run_plan({'op': 'update', 'task': 'T330', 'fields': {'status': '待验收'}})['nudge'])
        self.assertEqual((again['plan']['draft']['purpose'], again['plan']['draft']['mail']['subject']), ('告知状态已改为「待验收」', '回复：新的问题'))
        self.assertIn('Ali 10-21 发的', again['text'])
        with self.assertRaises(AppError):
            self.backend.assistant_nudge_dismiss(again['plan']['id'] + 'x')

    def test_not_when_answered_unlinked_or_unchanged(self):
        self.assertEqual(self.run_plan({'op': 'update', 'task': 'T330', 'fields': {'next': '等客户'}})['nudge'], '')
        self.assertIsNone(self.backend.assistant_nudge(self.run_plan({'op': 'complete', 'task': 'T333'}, task='T333')['nudge']))
        thread = self.backend._assist_thread
        self.backend._assist_thread = lambda i: {**thread(i), 'rows': [{**r, 'mine': True} for r in thread(i)['rows']]}
        self.assertIsNone(self.backend.assistant_nudge(self.run_plan({'op': 'complete', 'task': 'T330'})['nudge']))

    def test_a_sent_reply_counts_as_answered(self):
        nudge = self.backend.assistant_nudge(self.run_plan({'op': 'complete', 'task': 'T330'})['nudge'])
        self.backend.assistant_draft_compose(nudge['plan']['id'], 'reply')
        progress = self.backend.assistant_sent(nudge['plan']['id'])
        self.assertIsNone(self.backend.assistant_nudge(self.backend.assistant_plan_run(progress['id'])['nudge']))


class LinkBackendTest(TaskCenterCase):
    """没有关联邮件 (W4): candidates on a card; only the picked ones are linked, once."""
    def test_pick_and_link(self):
        rows = self.backend.messages('1', 0, 50)['rows'][:3]
        plan = self.backend._keep_plan({'ref': 'P1', 'kind': 'link', 'summary': 's', 'actions': [], 'task': 333,
                                        'mails': [{'ref': f'M{i}', 'mid': r['id'], 'subject': r['subject'], 'fromName': 'x', 'date': r['date']}
                                                  for i, r in enumerate(rows, 1)]}, 'chat', '问', 1)
        self.assertEqual([m['mid'] for m in plan['link']['mails']], [r['id'] for r in rows])
        for bad in ([], [3], ['0'], None):
            with self.assertRaises(AppError):
                self.backend.assistant_link_run(plan['id'], bad)
        self.assertEqual(self.backend.assistant_link_run(plan['id'], [2, 0, 2]), {'task': 333, 'linked': 2})
        self.assertEqual({l['mid'] for l in self.backend.tasks.store.links(333)}, {rows[0]['id'], rows[2]['id']})
        with self.assertRaises(AppError):
            self.backend.assistant_link_run(plan['id'], [1])

    def test_demo_offers_candidates(self):
        self.backend.ai_client = DemoAiClient()
        result = self.backend.ask_mailbox('这个任务回个邮件问问具体信息', current_task=333)
        self.assertEqual(result['plans'][0]['kind'], 'link')
        self.assertIn('关联', result['answer'])
        self.backend.assistant_link_run(result['plans'][0]['id'], [0])
        again = self.backend.ask_mailbox('这个任务回个邮件问问具体信息', current_task=333)
        self.assertEqual(again['plans'][0]['kind'], 'draft')


class PageTest(unittest.TestCase):
    def test_wiring(self):
        from pathlib import Path
        web = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'
        source = (web / 'ai.js').read_text(encoding='utf-8')
        for field in ('k.content', 'k.status', 'k.system', 'here.subject'):
            self.assertIn(f'esc({field}', source)
        self.assertIn("current_task: here && here.kind === 'task'", source)
        self.assertIn('openTask(task.dataset.task)', source)
        self.assertIn('chatMailChanged()', (web / 'tasks.js').read_text(encoding='utf-8'))  # The chip follows the selected task.
        self.assertIn("api('assistantPlanRun', { plan: p.id })", source)
        self.assertIn('esc(p.summary)', source)
        self.assertIn('p.preview.map(planItem)', source)  # The task center's own preview rows (esc inside).
        from coremail_desktop.app.shell import BACKEND_METHODS, TASK_METHODS
        self.assertEqual(BACKEND_METHODS['assistantPlanRun'], 'assistant_plan_run')
        self.assertIn('assistantPlanRun', TASK_METHODS)  # Writes run on the task thread, after any other change.
        from coremail_desktop.app.shell import AI_METHODS, STREAM_METHODS
        self.assertTrue({'assistantDraftWrite'} <= AI_METHODS & STREAM_METHODS)
        self.assertIn('assistantSent', TASK_METHODS)
        for field in ('d.purpose', 'd.mail.fromName', 'd.mail.subject', 'd.subject', 'points', 'c.name || c.domain'):
            self.assertIn(f'esc({field})', source)
        self.assertIn("['Accepted', 'Queued'].includes(res.state)", source)  # Progress is offered only once it went out.
        for name in ('assistantNudgeDismiss', 'assistantLinkRun'):  # Task writes, in order with the rest.
            self.assertIn(name, TASK_METHODS)
        self.assertEqual(BACKEND_METHODS['assistantNudge'], 'assistant_nudge')
        self.assertIn("api('assistantNudge', { nudge: p.outcome.nudge })", source)
        for field in ('n.text', 'm.subject', 'm.fromName'):
            self.assertIn(f'esc({field})', source)


if __name__ == '__main__':
    unittest.main()
