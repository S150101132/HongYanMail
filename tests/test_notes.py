"""「关于我 · 资料」(Me3–Me5, memory/notes.py): limits, matching and the catalog, read_note,
the AI's writes and their limits (the user's lines, locked notes, outside mail), undo,
the editor's line-by-line ownership, 可能过时, .md round trip. Plain functions only."""
import time
import unittest

from coremail_desktop.errors import AppError
from coremail_desktop.memory import notes as N

NOW = 1_800_000_000.0
DAY = 86400


def customer(lines=('## Planning', 'Division Manager：Ali', 'Inventory 签核：Omar'), **kw):
    return N.note('客户组织 · NG DCM', '客户', '客户 DCM 部各模块负责人', {'domains': ['ngrid.sa']}, lines, 'ai', 'mail', 'm0', NOW)


class NoteFieldsTest(unittest.TestCase):
    def test_limits(self):
        for args in (('',), ('x' * 41,), ('t', 'f' * 21), ('t', '', 'a' * 61)):
            with self.assertRaises(AppError) as caught:
                N.note(*args)
            self.assertEqual(caught.exception.code, 'NOTE_INVALID')
        for bad in ({'domains': ['not a domain']}, {'addresses': ['nobody']}, {'words': ['ab']}, {'domains': [f'd{i}.com' for i in range(21)]}):
            with self.assertRaises(AppError):
                N.note('t', match=bad)
        with self.assertRaises(AppError) as caught:
            N.note('t', lines=['x' * 300] * 14)
        self.assertEqual(caught.exception.code, 'NOTE_FULL')
        made = N.note(' 系统  · IPAM ', match={'domains': ['@Inspur.com'], 'words': 'IPAM, 地址池'}, lines=['  a  b ', '', 'c'])
        self.assertEqual((made['title'], made['match']['domains'], made['match']['words'], N.body(made)), ('系统 · IPAM', ['inspur.com'], ['IPAM', '地址池'], 'a b\nc'))
        self.assertEqual(N.notes([made, {'id': 1}, 'x', {'id': 'y', 'title': '', 'lines': []}]), [made])


class SentAlongTest(unittest.TestCase):
    def setUp(self):
        self.a = {**customer(), 'used': NOW - 10}
        self.b = {**N.note('系统 · IPAM', '系统', '地址管理找谁', {'words': ['IPAM']}, ['找 Bob'], at=NOW), 'used': NOW}
        self.c = {**N.note('一个人', match={'addresses': ['ali@ngrid.sa']}, lines=['x'], at=NOW), 'used': NOW - 99}
        self.items = [self.a, self.b, self.c]

    def test_order_and_budget(self):
        seen = {'addresses': ['Ali@NGRID.sa', 'bob@hq.ngrid.sa'], 'text': 'Re: IPAM change'}
        attached, related = N.match(self.items, seen)
        self.assertEqual(attached, [self.c['id'], self.a['id']])  # address > domain (subdomain counts) > word
        self.assertEqual(related, [self.b['id']])
        big = {**self.a, 'lines': [N.line('x' * 300) for _ in range(11)]}
        attached, related = N.match([big, self.b], seen)
        self.assertEqual((attached, related), ([self.b['id']], [big['id']]))  # 3300 chars do not fit; the next one does
        self.assertEqual(N.match([{**self.a, 'state': 'archived'}], seen), ([], []))

    def test_catalog_and_read(self):
        numbered = N.refs(self.items)
        self.assertEqual(list(numbered.values()), [self.b['id'], self.a['id'], self.c['id']])  # most recently used first
        text = N.prompt(self.items, [self.a['id']], [self.c['id']])
        self.assertIn('- N2 客户组织 · NG DCM — 客户 DCM 部各模块负责人（已附上）', text)
        self.assertIn('- N3 一个人（相关，未附上）', text)
        self.assertIn('【资料：客户组织 · NG DCM】\n## Planning\nDivision Manager：Ali', text)
        self.assertNotIn('找 Bob', text)
        self.assertEqual(N.prompt([], []), '')
        many = [{**N.note(f'资料{i:02d}', about='说' * 50, at=NOW), 'used': NOW - i} for i in range(40)]
        cut = N.prompt(many)
        self.assertIn('份没列出', cut)
        self.assertLessEqual(len(cut.split('\n- ……')[0]), N.CATALOG)
        found, read = N.read(self.items, 'N2')
        self.assertEqual(found['id'], self.a['id'])
        self.assertTrue(read.startswith('《客户组织 · NG DCM》（客户）\n## Planning'))
        for ref in ('N9', '', None):
            with self.assertRaises(AppError):
                N.read(self.items, ref)
        self.assertEqual(N.touch(self.items, [self.c['id']], NOW + 5)[2]['used'], NOW + 5)


class AiWritesTest(unittest.TestCase):
    def test_ai_lines_change_user_lines_and_locked_notes_only_get_marked(self):
        n = customer()
        n['lines'][2]['by'] = 'user'  # The user wrote the Inventory line.
        items = [n]
        change = {'note': 'N1', 'add': ['Network 焦点人：Sara'], 'replace': [{'line': 'Division Manager: Ali', 'with': 'Division Manager：Khalid'},
                  {'line': 'Inventory 签核：Omar', 'with': 'Inventory 签核：Fahad'}], 'remove': ['不存在的一行']}
        items, result = N.apply(items, change, 'ai', 'm1', False, 'x@ngrid.sa', now=NOW + 1)
        self.assertEqual({k: result[k] for k in ('added', 'replaced', 'stale', 'dropped')}, {'added': 1, 'replaced': 1, 'stale': 1, 'dropped': 1})
        self.assertEqual(N.body(items[0]), '## Planning\nDivision Manager：Khalid\nInventory 签核：Omar\nNetwork 焦点人：Sara')
        self.assertEqual(items[0]['stale'][0]['fix'], 'Inventory 签核：Fahad')
        self.assertEqual(items[0]['checked'], NOW + 1)
        locked = N.lock(items, n['id'], True)
        after, result = N.apply(locked, {'note': 'N1', 'add': ['新的一行'], 'remove': ['Network 焦点人：Sara']}, 'ai', 'm2', True, now=NOW + 2)
        self.assertEqual((result['added'], result['removed'], result['stale']), (0, 0, 1))
        self.assertEqual(N.body(after[0]), N.body(items[0]))
        fixed = N.stale_fix(items, n['id'], items[0]['stale'][0]['id'], NOW + 3)
        self.assertEqual((fixed[0]['lines'][2]['text'], fixed[0]['lines'][2]['by'], fixed[0]['stale']), ('Inventory 签核：Fahad', 'user', []))
        self.assertEqual(N.stale_drop(items, n['id'], items[0]['stale'][0]['id'])[0]['stale'], [])

    def test_outside_mail_reaches_only_its_own_domain(self):
        items = [customer(), N.note('系统 · IPAM', lines=['找 Bob'], at=NOW)]
        same, result = N.apply(items, {'note': 'N2', 'add': ['外来的一行']}, 'ai', 'm1', False, 'eve@evil.com')
        self.assertEqual((N.body(same[1]), result['dropped']), ('找 Bob', 1))
        made, result = N.apply(items, {'title': '供应商 · Evil', 'domains': ['inspur.com'], 'add': ['a', 'b']}, 'ai', 'm1', False, 'eve@evil.com', now=NOW)
        self.assertTrue(result['created'])
        self.assertEqual((made[-1]['match']['domains'], made[-1]['src'], [x['by'] for x in made[-1]['lines']]), (['evil.com'], 'mail', ['ai', 'ai']))
        inside, _ = N.apply(items, {'note': 'N2', 'add': ['同事说的']}, 'ai', 'm1', True, 'a@inspur.com')
        self.assertIn('同事说的', N.body(inside[1]))
        reused, result = N.apply(items, {'title': '客户组织 · ng dcm', 'add': ['同名就改这一份']}, 'ai', 'm1', False, 'x@ngrid.sa')
        self.assertEqual((len(reused), result['created'], result['added']), (2, False, 1))

    def test_limits_archived_full_and_gone(self):
        items = [customer()]
        _, result = N.apply(items, {'note': 'N1', 'add': [f'行{i}' for i in range(20)]}, 'ai', 'm1')
        self.assertEqual((result['added'], result['dropped']), (N.MAIL_LINES, 8))
        archived = N.archive(items, items[0]['id'])
        same, result = N.apply(archived, {'title': '客户组织 · NG DCM', 'add': ['x']}, 'ai', 'm1')
        self.assertEqual(len(same), 2)  # The archived one is not written; a new one is made beside it.
        full = [{**items[0], 'lines': [N.line('x' * 300) for _ in range(13)]}]
        _, result = N.apply(full, {'note': 'N1', 'add': ['y' * 200]}, 'ai', 'm1')
        self.assertEqual(result['full'], 1)
        gone = [{'title': '供应商 · Evil', 'domain': 'evil.com', 'at': NOW - 5 * DAY}]
        _, result = N.apply([], {'title': '供应商 · evil', 'add': ['a']}, 'ai', 'm9', False, 'x@evil.com', gone=gone, now=NOW)
        self.assertFalse(result['created'])
        _, result = N.apply([], {'title': '供应商 · evil', 'add': ['a']}, 'ai', 'm9', False, 'x@evil.com', gone=gone, now=NOW + 40 * DAY)
        self.assertTrue(result['created'])

    def test_undo_each_kind_and_the_same_mail_does_not_redo_it(self):
        items, _ = N.apply([customer()], {'note': 'N1', 'add': ['新行'], 'replace': [{'line': 'Division Manager：Ali', 'with': 'Division Manager：K'}],
                                           'remove': ['## Planning']}, 'ai', 'm1', now=NOW)
        log = {e['op']: e['id'] for e in items[0]['log']}
        self.assertEqual(sorted(e['op'] for e in N.recent(items, now=NOW + 1)), ['add', 'remove', 'replace'])
        self.assertEqual(N.recent(items, now=NOW + 8 * DAY), [])
        for op in ('add', 'replace', 'remove'):
            items, gone = N.undo(items, items[0]['id'], log[op], NOW + 2)
            self.assertIsNone(gone)
        self.assertEqual(sorted(N.body(items[0]).split('\n')), sorted(N.body(customer()).split('\n')))
        self.assertEqual(N.recent(items, now=NOW + 3), [])
        with self.assertRaises(AppError):
            N.undo(items, items[0]['id'], log['add'])
        again, result = N.apply(items, {'note': 'N1', 'add': ['新行'], 'replace': [{'line': 'Division Manager：Ali', 'with': 'Division Manager：K'}]}, 'ai', 'm1')
        self.assertEqual((result['added'], result['replaced'], N.body(again[0])), (0, 0, N.body(items[0])))
        _, result = N.apply(items, {'note': 'N1', 'add': ['新行']}, 'ai', 'm2')
        self.assertEqual(result['added'], 1)  # Another mail may say it again.
        made, result = N.apply([], {'title': '新客户', 'add': ['a']}, 'ai', 'm3', False, 'x@new.sa', now=NOW)
        create = next(e['id'] for e in made[0]['log'] if e['op'] == 'create')
        left, gone = N.undo(made, made[0]['id'], create, NOW)
        self.assertEqual((left, gone), ([], {'title': '新客户', 'domain': 'new.sa', 'at': NOW}))

    def test_save_note_from_chat_is_the_users_word(self):
        n = customer()
        n['lines'][2]['by'] = 'user'
        items, result = N.apply([N.lock([n], n['id'])[0]], {'note': 'N1', 'replace': [{'line': 'Inventory 签核：Omar', 'with': 'Inventory 签核：Fahad'}],
                                                           'add': ['x']}, 'user', now=NOW)
        self.assertEqual((result['replaced'], result['added'], items[0]['lines'][2]['by'], items[0]['log'][-1]['by']), (1, 1, 'user', 'user'))
        made, result = N.apply([], {'title': '流程 · 报销', 'add': ['先找财务']}, 'user')
        self.assertEqual((made[0]['src'], made[0]['lines'][0]['by']), ('chat', 'user'))


class UserTest(unittest.TestCase):
    def test_editor_keeps_who_wrote_each_line(self):
        n = customer()
        n['stale'] = [{'id': 's1', 'line': 'Inventory 签核：Omar', 'why': 'x', 'mid': 'm', 'at': NOW, 'fix': None}]
        items, saved = N.save([n], n['id'], '客户组织 · NG DCM', '客户', '说明', {'domains': 'ngrid.sa'},
                              '## Planning\nDivision Manager：Ali（经理）\n新加的一行', NOW + 1)
        self.assertEqual([x['by'] for x in saved['lines']], ['ai', 'user', 'user'])
        self.assertEqual((saved['lines'][0]['mid'], saved['stale'], saved['checked']), ('m0', [], NOW + 1))
        items, made = N.save(items, '', '新的', text='a\nb')
        self.assertEqual((len(items), made['lines'][1]['by']), (2, 'user'))
        with self.assertRaises(AppError):
            N.save(items, 'nope', 't')

    def test_archive_remove_and_marks(self):
        n = customer()
        items = N.archive([n], n['id'])
        self.assertEqual((N.current(items), N.refs(items)), ([], {}))
        items = N.unarchive(items, n['id'])
        self.assertEqual(len(N.current(items)), 1)
        self.assertEqual(N.remove(items, n['id'])[0], [])
        self.assertEqual(N.marks(n, NOW + 91 * DAY), {'unused': True, 'unchecked': False})
        self.assertEqual(N.marks({**n, 'used': NOW + 100 * DAY}, NOW + 181 * DAY), {'unused': False, 'unchecked': True})
        self.assertEqual(N.marks(N.check([n], n['id'], NOW + 180 * DAY)[0], NOW + 181 * DAY)['unchecked'], False)
        self.assertNotIn('log', N.view(n))

    def test_markdown_round_trip(self):
        n = N.note('系统 · IPAM', '系统', '地址管理找谁', {'domains': ['inspur.com'], 'addresses': ['bob@inspur.com'], 'words': ['IPAM']},
                   ['## 联系人', '- Bob：地址分配'], 'ai')
        back = N.from_md(N.to_md(n), NOW)
        self.assertEqual({k: back[k] for k in ('title', 'folder', 'about', 'match', 'src')}, {**{k: n[k] for k in ('title', 'folder', 'about', 'match')}, 'src': 'import'})
        self.assertEqual((N.body(back), {x['by'] for x in back['lines']}), (N.body(n), {'user'}))
        plain = N.from_md('随手记\r\n第一行\r\n\r\n第二行')
        self.assertEqual((plain['title'], N.body(plain)), ('随手记', '第一行\n第二行'))
        with self.assertRaises(AppError):
            N.from_md('  \n ')


class BackendNotesTest(unittest.TestCase):
    """The backend: storage, the catalog and matching in AI calls, the 理解层 writing, 问鸿雁's
    tools, the page's calls, 从「人」移到资料 and 从最近 30 天整理. Demo adapter, fake AI only."""

    def setUp(self):
        from coremail_desktop.app.backend import MailBackend
        from coremail_desktop.app.demo import DemoAdapter
        from tests.test_app_backend import XorProtector, local_services_stub
        from tests.test_compose_v05 import scratch
        self.backend = b = MailBackend(scratch(self), adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        b.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(b.logout)
        b.folders()
        b.messages('1', 0, 100)
        rows = b.cache.summaries('1', 0, 100)[0]
        self.partner = next(r['id'] for r in rows if 'external.example' in (r.get('from') or ''))
        self.colleague = next(r['id'] for r in rows if 'zhangxm@' in (r.get('from') or ''))

    def seed(self, by='ai'):
        made = N.note('客户组织 · 合作伙伴', '客户', '合作伙伴各模块负责人', {'domains': ['external.example']},
                      ['项目经理：P', '接口联调焦点人：赵工', 'Inventory 签核：王工'], by, 'mail')
        other = N.note('系统 · 接口平台', '系统', '接口找谁', {'words': ['不会命中的词']}, ['对接人：张晓明'])
        self.backend._memory().set('notes', [made, other])
        return made, other

    def test_catalog_and_matching_reach_the_ai_and_nothing_is_stored_in_the_clear(self):
        from tests.test_understand import FakeAi
        import json
        b = self.backend
        self.seed()
        b.ai_client = ai = FakeAi(json.dumps({'conclusion': '结论', 'points': [], 'actions': []}, ensure_ascii=False))
        for mid in (self.partner, self.colleague):
            b.open_message(mid, mark_read=False)
            b.summarize(mid, refresh=True)
        partner, colleague = ai.calls[-2][0]['content'], ai.calls[-1][0]['content']
        self.assertIn('【资料目录】', partner)
        self.assertIn('（已附上）', partner)
        self.assertIn('【资料：客户组织 · 合作伙伴】\n项目经理：P', partner)
        self.assertIn('客户组织 · 合作伙伴 — 合作伙伴各模块负责人', colleague)  # The catalog only...
        self.assertNotIn('接口联调焦点人', colleague)  # ...not the body.
        self.assertNotIn('接口联调焦点人', ai.calls[-2][1]['content'])  # System prompt, outside the mail's delimiters.
        self.assertTrue(b.notes_list()['notes'][0]['used'])  # Attached: 最近用到.
        raw = b.memory.path.read_bytes() + (b.memory.path.with_name(b.memory.path.name + '-wal').read_bytes()
                                            if b.memory.path.with_name(b.memory.path.name + '-wal').exists() else b'')
        self.assertNotIn('接口联调焦点人'.encode('utf-8'), raw)

    def test_mail_writes_at_once_within_the_limits_and_each_change_undoes(self):
        from tests.test_understand import FakeAi, card_json
        b = self.backend
        made, _ = self.seed()
        b.set_understand('on')
        change = {'note': 'N1', 'replace': [{'line': '接口联调焦点人：赵工', 'with': '接口联调焦点人：钱工'}], 'add': ['联调时间由项目经理确认']}
        b.ai_client = ai = FakeAi(card_json(notes=[change, {'note': 'N2', 'add': ['外部邮件不能改这一份']}],
                                            facts=[{'project': 'NG BI', 'type': 'owner', 'text': '某某负责联调'}]))
        b.understand_fetch([self.partner])
        b.understand_mail(self.partner)
        self.assertIn('"notes"', ai.calls[-1][0]['content'])  # Asked for, with the matched body attached.
        self.assertIn('【资料：客户组织 · 合作伙伴】', ai.calls[-1][0]['content'])
        card = b.memory.card(self.partner)
        self.assertNotIn('notes', card)
        self.assertEqual(card['facts'], [])  # 分工 now lives in 资料 only.
        notes_now = {n['title']: n for n in b.notes_list()['notes']}
        self.assertEqual(N.body(notes_now['客户组织 · 合作伙伴']), '项目经理：P\n接口联调焦点人：钱工\nInventory 签核：王工\n联调时间由项目经理确认')
        self.assertEqual(N.body(notes_now['系统 · 接口平台']), '对接人：张晓明')  # Outside mail: only the sender's own organisation.
        recent = b.notes_list()['recent']
        self.assertEqual(sorted(e['op'] for e in recent), ['add', 'replace'])
        self.assertTrue(all(e['mid'] == self.partner for e in recent))
        replaced = next(e for e in recent if e['op'] == 'replace')
        b.note_undo(made['id'], [replaced['log_id']])
        self.assertIn('接口联调焦点人：赵工', N.body(b._notes_items()[0]))
        # The user's line: only marked 可能过时; the switch off: nothing written.
        saved = b.note_save(made['id'], made['title'], '客户', '', {'domains': ['external.example']}, '项目经理：P（我确认过）\n接口联调焦点人：赵工')['saved']
        b.memory.put_card(self.partner, {**card, 'v': 0})
        b.ai_client = FakeAi(card_json(notes=[{'note': 'N1', 'remove': ['项目经理：P（我确认过）']}]))
        b.understand_mail(self.partner)
        note = next(n for n in b.notes_list()['notes'] if n['id'] == saved)
        self.assertEqual((N.body(note), len(note['stale'])), ('项目经理：P（我确认过）\n接口联调焦点人：赵工', 1))
        b.set_prefs({'notesFromMail': False})
        b.ai_client = ai = FakeAi(card_json(notes=[{'note': 'N1', 'add': ['关掉了']}]))
        b.understand_mail(self.partner)
        self.assertNotIn('"notes"', ai.calls[-1][0]['content'])
        self.assertNotIn('关掉了', N.body(b._notes_items()[0]))

    def test_chat_reads_on_demand_and_saves_what_the_user_asked(self):
        from coremail_desktop.ai.tools import AssistantTools, MCP_SPECS
        import json
        b = self.backend
        made, _ = self.seed()
        tools = AssistantTools(lambda *a: ([], 0), b._document, lambda n: [], {}, tasks=lambda v, q: [], task=None, mine=lambda t: True,
                               notes=b._note_read, save_note=b._note_write)
        self.assertNotIn('read_note', {s['function']['name'] for s in MCP_SPECS})
        tools.begin()
        b._about(numbered=True, refs_out=tools.note_refs)
        result, step = tools.run('read_note', json.dumps({'note': 'n1'}))
        self.assertTrue(result['text'].startswith('《客户组织 · 合作伙伴》（客户）\n项目经理：P'))
        self.assertIn('读资料《客户组织 · 合作伙伴》', step)
        self.assertIn('error', tools.run('read_note', json.dumps({'note': 'N9'}))[0])
        result, _ = tools.run('save_note', json.dumps({'note': 'N1', 'add': ['Planning 经理：Ali', '项目经理：P'],
                                                       'replace': [{'line': 'Inventory 签核：王工', 'with': 'Inventory 签核：Omar'}]}, ensure_ascii=False))
        self.assertEqual((result['saved'], result['added'], result['replaced']), (True, 1, 1))
        note = b._notes_items()[0]
        self.assertEqual([x['by'] for x in note['lines']][-2:], ['user', 'user'])  # Asked for: the user's lines.
        saved = tools.notes_saved[0]
        b.note_undo(saved['note'], saved['logs'])
        self.assertEqual(N.body(b._notes_items()[0]), N.body(made))
        made_new, _ = tools.run('save_note', json.dumps({'title': '流程 · 报销', 'add': ['先找财务']}, ensure_ascii=False))
        self.assertTrue(made_new['saved'])
        self.assertIn('error', tools.run('save_note', json.dumps({'add': ['没有标题']}, ensure_ascii=False))[0])

    def test_page_calls_people_to_notes_and_backfill(self):
        from tests.test_understand import FakeAi
        import json
        b = self.backend
        made, other = self.seed()
        b.note_state(other['id'], locked=True)
        b.note_state(other['id'], archived=True)
        view = b.notes_list()
        self.assertEqual(([n['ref'] for n in view['notes'] if n['id'] == other['id']], view['on']), ([''], True))  # Archived: not in the catalog.
        self.assertNotIn('系统 · 接口平台', b._about())
        b.note_state(other['id'], archived=False)
        name, text = b.note_export(made['id'])
        self.assertEqual(name, '客户组织_·_合作伙伴.md')
        copied = b.note_import(text.replace('# 客户组织 · 合作伙伴', '# 导入的一份'))['saved']
        self.assertEqual({x['by'] for x in next(n for n in b._notes_items() if n['id'] == copied)['lines']}, {'user'})
        with self.assertRaises(AppError):
            b.note_import('   ')
        b.note_remove(made['id'])
        self.assertEqual(b.memory.get('notesGone')[0]['domain'], 'external.example')  # The AI does not make it again soon.
        # 「人」 entries into a note, kept as 以前.
        b.about_entry(group='people', text='客户 A 部经理：Ali', email='ali@external.example')
        entry = b.about_me()['entries'][0]
        moved = b.about_to_note([entry['id']], '', '客户组织 · A')
        self.assertEqual((moved['added'], moved['entries'][0]['state']), (1, 'past'))
        self.assertIn('客户 A 部经理：Ali（ali@external.example）', N.body(next(n for n in b._notes_items() if n['id'] == moved['note'])))
        # 从最近 30 天邮件整理: an estimate first, then mail by mail.
        b.ai_client = ai = FakeAi(json.dumps({'notes': [{'title': '供应商 · 外部', 'add': ['外部联系人：P']}]}, ensure_ascii=False))
        plan = b.notes_backfill_plan()
        self.assertTrue(plan['ids'] and plan['tokens'] == len(plan['ids']) * b.BACKFILL_TOKENS)
        b.understand_fetch([self.partner])
        self.assertEqual(b.notes_backfill(self.partner)['changed'], 2)  # create + add
        self.assertIn('只整理「资料」', ai.calls[-1][0]['content'])
        new = next(n for n in b._notes_items() if n['title'] == '供应商 · 外部')
        self.assertEqual((new['match']['domains'], new['src']), (['external.example'], 'mail'))


if __name__ == '__main__':
    unittest.main()
