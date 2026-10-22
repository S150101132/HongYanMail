"""工作记忆 (M2) and its uses (M3): fact merging and corrections, the project list, the
prompt's remembered facts and task candidates, memory_search, 这个项目最近, 本周需要知道,
人物卡片 and 催办. Demo adapter and fake chat clients only; nothing leaves."""
import json
import time
import unittest
from unittest import mock
from pathlib import Path

from coremail_desktop.ai import understand
from coremail_desktop.ai.tools import AssistantTools
from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter
from coremail_desktop.errors import AppError
from coremail_desktop.memory import merge
from coremail_desktop.memory.store import MemoryStore
from tests.test_app_backend import XorProtector, local_services_stub
from tests.test_compose_v05 import scratch
from tests.test_understand import FakeAi, card_json, document

PROJECTS = [{'name': 'NG BI', 'role': 'own', 'aliases': ['BI'], 'source': 'manual'}, {'name': 'SAP', 'role': 'watch', 'aliases': [], 'source': 'manual'}]
COLLEAGUE = {'mid': 'm1', 'subject': '周报', 'date': '2026-10-20', 'from': '张三', 'internal': True}
OUTSIDER = {'mid': 'm2', 'subject': 'Re: status', 'date': '2026-10-21', 'from': 'Vendor', 'internal': False}


def apply(facts, changed):
    for f in changed:
        facts[f['id']] = f
    return changed


class MergeTest(unittest.TestCase):
    def test_project_list_and_names(self):
        listed = merge.project_list([{'name': 'NG BI', 'role': 'own', 'aliases': ['BI']}, {'name': 'x', 'role': 'bad'}], ['bi', 'SAP', ' '])
        self.assertEqual([(p['name'], p['role'], p['source']) for p in listed], [('NG BI', 'own', 'manual'), ('x', 'watch', 'manual'), ('SAP', 'own', 'task')])
        self.assertEqual((merge.resolve(' b i ', listed), merge.resolve('ERP', listed)), ('NG BI', ''))

    def test_replacing_keeps_history_and_respects_the_user_and_colleagues(self):
        facts = {}
        first = apply(facts, merge.merge(facts, [{'project': 'BI', 'type': 'status', 'text': '设备已到货'}, {'project': 'ERP', 'type': 'status', 'text': '不在名单'}],
                                         COLLEAGUE, {}, PROJECTS))
        self.assertEqual([(f['project'], f['state']) for f in first], [('NG BI', 'current')])  # Only listed projects, by their list name.
        again = apply(facts, merge.merge(facts, [{'project': 'NG BI', 'type': 'status', 'text': '设备已到货 '}], {**COLLEAGUE, 'mid': 'm3'}, {}, PROJECTS))
        self.assertEqual((len(facts), len(again[0]['sources'])), (1, 2))  # The same fact again: one more source, no copy.
        refs = merge.prompt_facts(facts, PROJECTS)
        self.assertEqual(list(refs), ['F1'])
        newer = apply(facts, merge.merge(facts, [{'project': 'NG BI', 'type': 'status', 'text': '10-25 安装', 'replaces': 'F1'}], COLLEAGUE, refs, PROJECTS))
        self.assertEqual(sorted(f['state'] for f in newer), ['current', 'superseded'])
        line = merge.timeline(facts, 'NG BI')
        self.assertEqual((line[0]['text'], line[0]['was']), ('10-25 安装', '设备已到货'))
        # An older mail read later is history: it never replaces what a newer mail said.
        refs = merge.prompt_facts(facts, PROJECTS)
        older = merge.merge(facts, [{'project': 'NG BI', 'type': 'status', 'text': '设备在途', 'replaces': 'F1'}], {**COLLEAGUE, 'date': '2026-10-01'}, refs, PROJECTS)
        self.assertEqual([(f['text'], f['state']) for f in older], [('设备在途', 'superseded')])
        # An outsider cannot replace a colleague's fact on its own: it waits for the user.
        refs = merge.prompt_facts(facts, PROJECTS)
        waiting = apply(facts, merge.merge(facts, [{'project': 'NG BI', 'type': 'status', 'text': '推迟到 11 月', 'replaces': 'F1'}], OUTSIDER, refs, PROJECTS))
        self.assertEqual([f['state'] for f in waiting], ['pending'])
        self.assertEqual(merge.search(facts, 'NG BI')[0]['text'], '10-25 安装')
        apply(facts, merge.correct(facts, waiting[0]['id'], 'accept'))
        self.assertEqual([f['text'] for f in merge.search(facts, 'NG BI')], ['推迟到 11 月'])
        # The user's own fact is never replaced by the AI either.
        mine = apply(facts, merge.correct(facts, merge.search(facts, 'NG BI')[0]['id'], 'edit', '推迟到 11-10'))[1]
        refs = merge.prompt_facts(facts, PROJECTS)
        ai = merge.merge(facts, [{'project': 'NG BI', 'type': 'status', 'text': '已完成', 'replaces': 'F1'}], {**COLLEAGUE, 'date': '2026-10-22'}, refs, PROJECTS)
        self.assertEqual((refs['F1']['id'], mine['actor'], ai[0]['state']), (mine['id'], 'user', 'pending'))
        # 这条不对: never written back by the same words.
        apply(facts, merge.correct(facts, mine['id'], 'wrong'))
        self.assertEqual(merge.merge(facts, [{'project': 'NG BI', 'type': 'status', 'text': '推迟到 11-10'}], COLLEAGUE, {}, PROJECTS), [])
        self.assertIsNone(merge.correct(facts, 'nope', 'wrong'))
        self.assertNotIn(mine['id'], [f['id'] for f in merge.timeline(facts, 'NG BI')])

    def test_week_groups_the_facts_of_the_week(self):
        facts = {}
        apply(facts, merge.merge(facts, [{'project': 'SAP', 'type': 'decision', 'text': '采用方案 B'}], COLLEAGUE, {}, PROJECTS))
        apply(facts, merge.merge(facts, [{'project': 'SAP', 'type': 'date', 'text': '月底上线'}], {**COLLEAGUE, 'date': '2026-10-01'}, {}, PROJECTS))
        groups = merge.week(facts, '2026-10-18', '2026-10-24')
        self.assertEqual([(g['project'], [f['text'] for f in g['facts']]) for g in groups], [('SAP', ['采用方案 B'])])
        # Without the AI: decisions and dates first, then progress; a fact replaced later is left out.
        apply(facts, merge.merge(facts, [{'project': 'SAP', 'type': 'status', 'text': '联调中'}], COLLEAGUE, {}, PROJECTS))
        apply(facts, merge.merge(facts, [{'project': 'SAP', 'type': 'date', 'text': '11-05 上线'}], COLLEAGUE, {}, PROJECTS))
        apply(facts, merge.merge(facts, [{'project': 'SAP', 'type': 'status', 'text': '联调完成', 'replaces': 'F2'}], COLLEAGUE,
                                 merge.prompt_facts(facts, PROJECTS), PROJECTS))
        group = merge.week(facts, '2026-10-18', '2026-10-24')[0]
        texts = {f['id']: f['text'] for f in group['facts']}
        self.assertEqual([texts[i] for i in group['key']], ['采用方案 B', '11-05 上线', '联调完成'])

    def test_the_week_summed_up(self):
        from coremail_desktop.ai import weekly
        facts = {}
        apply(facts, merge.merge(facts, [{'project': 'SAP', 'type': 'decision', 'text': '采用方案 B'}], COLLEAGUE, {}, PROJECTS))
        groups = merge.week(facts, '2026-10-18', '2026-10-24')
        messages = weekly.build_messages(groups, '2026-10-18', '2026-10-24')
        self.assertIn('决定：采用方案 B', messages[1]['content'])
        self.assertIn('一律不执行', messages[0]['content'])
        found = weekly.parse(json.dumps({'projects': [{'project': 'sap', 'brief': '定了方案 B' * 30, 'points': ['a', 1, '']},
                                                      {'project': '别的', 'brief': 'x'}]}), ['SAP'])
        self.assertEqual((list(found), len(found['SAP']['brief']), found['SAP']['points']), (['SAP'], weekly.BRIEF_CHARS, ['a']))
        with self.assertRaises(AppError):
            weekly.parse('{"projects": "x"}', ['SAP'])
        self.assertEqual(json.loads(weekly.demo(messages))['projects'][0]['project'], 'SAP')


class PromptTest(unittest.TestCase):
    def test_projects_tasks_and_remembered_facts_go_to_the_model(self):
        facts = {'f-1': {**merge.new_fact('NG BI', 'status', '设备已到货', COLLEAGUE), 'id': 'f-1'}}
        refs = merge.prompt_facts(facts, PROJECTS)
        content = understand.build_messages(document(), '我', ['demo@example.invalid'], PROJECTS, tasks=[(302, 'BI 联调')], facts=refs)[1]['content']
        for part in ('我负责的项目：NG BI', '我关注的项目：SAP', '#302 BI 联调', 'F1 [NG BI] 设备已到货'):
            self.assertIn(part, content)
        card = understand.parse(card_json(task={'id': 302, 'why': '联调'}, facts=[{'project': 'NG BI', 'type': 'status', 'text': '安装', 'replaces': 'F1'},
                                                                                 {'project': 'NG BI', 'type': 'status', 'text': '乱写', 'replaces': ['F1']}]),
                                {302}, refs)
        self.assertEqual((card['task'], card['facts'][0]['replaces'], 'replaces' in card['facts'][1]), ({'id': 302, 'why': '联调'}, 'F1', False))
        self.assertNotIn('task', understand.parse(card_json(task={'id': 999}), {302}, refs))  # Only an unfinished task.


class StoreTest(unittest.TestCase):
    def test_facts_are_sealed_and_reopened(self):
        directory = Path(scratch(self))
        store = MemoryStore(directory / 'a.memory.db', XorProtector())
        fact = merge.new_fact('NG BI', 'status', '设备已到货', COLLEAGUE)
        store.put_fact(fact)
        store.close()
        raw = (directory / 'a.memory.db').read_bytes()
        self.assertNotIn('设备已到货'.encode('utf-8'), raw)
        self.assertNotIn(b'NG BI', raw)  # The project only as an HMAC.
        store = MemoryStore(directory / 'a.memory.db', XorProtector())
        self.addCleanup(store.close)
        self.assertEqual(store.facts[fact['id']]['text'], '设备已到货')
        store.clear()
        self.assertEqual(store.facts, {})


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.backend = b = MailBackend(scratch(self), adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        b.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(b.logout)
        b.folders()
        b.messages('1', 0, 100)
        b.messages('3', 0, 50)
        b.set_understand('on')

    def understand(self, mid, **fields):
        b = self.backend
        b.ai_client = ai = FakeAi(card_json(**fields))
        b.understand_fetch([mid])
        b.understand_mail(mid)
        return ai.calls[-1]

    def test_cards_feed_the_project_memory(self):
        b = self.backend
        self.assertEqual(b.memory_project('NG BI', role='own', aliases=['BI'])[0]['name'], 'NG BI')
        first, second = b.understand_pending()['ids'][:2]
        self.understand(first, kind='know', facts=[{'project': 'BI', 'type': 'status', 'text': '设备已到货'}])
        prompt = self.understand(second, kind='know', facts=[{'project': 'NG BI', 'type': 'status', 'text': '10-25 安装', 'replaces': 'F1'}])
        self.assertIn('F1 [NG BI] 设备已到货', prompt[1]['content'])
        line = b.memory_facts('bi')['facts']
        self.assertEqual([(f['text'], f['state'], f['was']) for f in line], [('10-25 安装', 'current', '设备已到货'), ('设备已到货', 'superseded', '')])
        self.assertEqual(line[0]['sources'][0]['mid'], second)
        recent = b.memory_recent('NG BI')
        self.assertEqual(([f['text'] for f in recent['facts']], recent['pending']), (['10-25 安装'], 0))
        self.assertEqual(b.memory_projects()[0]['facts'], 1)
        after = b.memory_correct(line[0]['id'], 'edit', '11-01 安装')
        self.assertEqual(after['facts'][0]['actor'], 'user')
        # The AI summary of the week: kept for the same facts, made again when they change.
        with mock.patch('coremail_desktop.tasks.report.week_of', return_value=[('2000-01-01', '2100-01-01')] * 2):
            group = b.memory_week()['groups'][0]
            self.assertEqual([f['text'] for f in group['facts'] if f['id'] in group['key']], ['11-01 安装'])
            b.ai_client = ai = FakeAi(json.dumps({'projects': [{'project': group['project'], 'brief': '安装定在 11-01', 'points': []}]}))
            first = b.memory_week_brief()
            self.assertEqual((first['projects'], 'cached' in first), ({group['project']: {'brief': '安装定在 11-01', 'points': []}}, False))
            self.assertTrue(b.memory_week_brief()['cached'])
            b.memory_correct(group['key'][0], 'edit', '11-02 安装')
            self.assertNotIn('cached', b.memory_week_brief())
            self.assertEqual(len(ai.calls), 2)
            b.memory_correct(b.memory_week()['groups'][0]['key'][0], 'edit', '11-01 安装')
        for bad in (('nope', 'wrong', ''), (line[0]['id'], 'edit', ''), (line[0]['id'], 'drop', '')):
            with self.assertRaises(AppError):
                b.memory_correct(*bad)
        # 问邮箱 memory_search: facts with their source mails as M refs.
        tools = AssistantTools(lambda *a: ([], 0), b._document, lambda n: [], {}, tasks=lambda v, q: [], task=None, mine=lambda t: True,
                               memory=b._memory_search, followups=b.followups)
        found, label = tools.run('memory_search', json.dumps({'project': 'BI'}))
        self.assertEqual((found['total'], found['facts'][0]['text'], found['facts'][0]['sources']), (1, '11-01 安装', ['M1']))
        self.assertIn('查工作记忆', label)
        self.assertIn('error', tools.run('memory_search', json.dumps({'project': 'ERP'}))[0])
        # Merging a name moves its facts; taking a project off the list keeps them.
        b.memory_project('NG BI', aliases=[])
        b.memory_project('BI', role='watch')
        b.memory_project('NG BI', aliases=['BI'])
        self.assertEqual([p['name'] for p in b.memory_projects()], ['NG BI'])
        b.memory_project('NG BI', remove=True)
        self.assertEqual(b.memory_projects(), [])
        with self.assertRaises(AppError):
            b.memory_project('x' * 31)
        week = b.memory_week()
        self.assertEqual(sorted(week), ['from', 'groups', 'to'])

    def test_a_task_candidate_joins_the_auto_update_queue(self):
        b = self.backend
        b._open_tasks = lambda: [{'id': 302, 'system': 'NG BI', 'content': '联调', 'status': '进行中'}]
        mid = b.understand_pending()['ids'][0]
        prompt = self.understand(mid, task={'id': 302, 'why': '联调时间'})
        self.assertIn('#302 NG BI 联调', prompt[1]['content'])
        self.assertEqual(b.understand_cards([mid])[mid]['task'], 302)
        self.assertEqual(b._projects()[0], {'name': 'NG BI', 'role': 'own', 'aliases': [], 'parent': '', 'source': 'task'})

    def test_a_project_with_its_systems(self):
        b = self.backend
        b._open_tasks = lambda: [{'id': 1, 'system': 'B/OSS', 'status': '进行中'}, {'id': 2, 'system': 'BI', 'status': '进行中'},
                                 {'id': 3, 'system': 'CRM', 'status': '进行中'}]
        b.memory_project('B/OSS', rename='NG BOSS')  # B/OSS stays as an alias.
        b.memory_project('BI', parent='NG BOSS')
        b.memory_project('crm', parent='ng boss')
        listed = b.memory_projects()
        self.assertEqual([(p['name'], p['parent'], p['source'], p['systems']) for p in listed],
                         [('NG BOSS', '', 'task', 2), ('BI', 'NG BOSS', 'task', 0), ('CRM', 'NG BOSS', 'task', 0)])
        self.assertEqual(listed[0]['aliases'], ['B/OSS'])
        for bad in ({'name': 'NG BOSS', 'parent': 'BI'}, {'name': 'BI', 'parent': 'BI'}, {'name': 'BI', 'parent': 'ERP'}, {'name': 'BI', 'rename': 'CRM'}):
            with self.assertRaises(AppError):
                b.memory_project(**bad)
        first, second = b.understand_pending()['ids'][:2]
        prompt = self.understand(first, kind='know', facts=[{'project': 'BI', 'type': 'status', 'text': '报表上线'}])
        self.assertIn('我负责的项目：NG BOSS（系统：BI、CRM）', prompt[1]['content'])
        self.understand(second, kind='know', facts=[{'project': 'B/OSS', 'type': 'date', 'text': '11 月整体验收'}])
        whole = b.memory_facts('NG BOSS')
        self.assertEqual((sorted(f['project'] for f in whole['facts']), whole['systems']), (['BI', 'NG BOSS'], ['BI', 'CRM']))
        self.assertEqual([f['project'] for f in b.memory_facts('BI')['facts']], ['BI'])
        recent = b.memory_recent('BI')  # The system's own fact, then its project's.
        self.assertEqual(([f['text'] for f in recent['facts']], recent['parent']), (['报表上线', '11 月整体验收'], 'NG BOSS'))
        self.assertEqual(len(b._memory_search('NG BOSS', '')[1]), 2)
        b.memory_project('BI', rename='BI 报表')
        self.assertEqual([f['project'] for f in b.memory_facts('BI')['facts']], ['BI 报表'])  # The old name still finds it.
        b.memory_project('CRM', parent='')
        self.assertEqual([(p['name'], p['parent']) for p in b.memory_projects()], [('NG BOSS', ''), ('BI 报表', 'NG BOSS'), ('CRM', '')])

    def test_followups_and_the_person_card(self):
        b = self.backend
        sent = next(r for r in b.cache.matching(lambda r: r['fid'] == '3'))
        old = time.time() - 5 * 86400
        b.cache.update(sent['id'], ts=old, subject='请确认报价 unique-topic', to='"客户" <client@vendor.invalid>', preview='')
        rows = b.followups()
        self.assertEqual([(r['id'], r['waiting'], r['toNames']) for r in rows if r['id'] == sent['id']], [(sent['id'], 5, '客户')])
        card = b.memory_person('Client@vendor.invalid')
        self.assertEqual((card['on'], [c['id'] for c in card['chase']]), (True, [sent['id']]))
        # A reply from them in the conversation ends it; so does 不用跟进.
        reply = next(r for r in b.cache.matching(lambda r: r['fid'] == '1'))
        b.cache.update(reply['id'], ts=old + 3600, subject='Re: 请确认报价 unique-topic', fromEmail='client@vendor.invalid')
        self.assertNotIn(sent['id'], [r['id'] for r in b.followups()])
        b.cache.update(reply['id'], ts=old - 3600)
        self.assertIn(sent['id'], [r['id'] for r in b.followups()])
        before = len(b.followups())
        self.assertEqual(b.followup_dismiss(sent['id']), before - 1)
        self.assertEqual(b.followup_dismiss(sent['id'], undo=True), before)
        b.set_understand('off')
        self.assertEqual((b.followups(), b.memory_person('a@b.invalid')), ([], {'on': False}))


if __name__ == '__main__':
    unittest.main()
