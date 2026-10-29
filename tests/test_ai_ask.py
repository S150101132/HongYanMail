"""问邮箱: tool calling in the client, the read-only tools, the agent loop and its
limits, and the backend and bridge wiring. Scripted models only; nothing leaves."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from coremail_desktop.ai import agent
from coremail_desktop.ai.agent import ask
from coremail_desktop.ai.tools import SPECS, MailboxTools
from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter, DemoAiClient
from coremail_desktop.errors import AppError
from tests.test_ai_summary import client, reply
from tests.test_app_backend import XorProtector, local_services_stub
from tests.test_compose_v05 import scratch

WEB = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'


def tool_reply(*calls, content=None):
    message = {'role': 'assistant', 'content': content,
               'tool_calls': [{'id': f'c{i}', 'type': 'function', 'function': {'name': n, 'arguments': json.dumps(a)}}
                              for i, (n, a) in enumerate(calls)]}
    return json.dumps({'choices': [{'message': message}], 'usage': {'total_tokens': 7}}).encode('utf-8')


def row(i, **extra):
    return {'id': f'mid{i}', 'fid': '1', 'subject': f'主题{i}', 'from': f'"张三" <zhang{i}@inspur.com>', 'fromName': '张三',
            'date': '2026-10-1%d 09:30:00' % i, 'preview': f'正文开头{i}', 'flags': {'attached': i == 2}, **extra}


def document(text='正文内容', state='none'):
    return SimpleNamespace(headers={'subject': ['主题'], 'from': ['"张三" <zhang@inspur.com>'], 'to': ['"李四" <li@inspur.com>']},
                           html=None, text=text, attachments=[SimpleNamespace(name='a.pdf')], encryption_state=state)


class FakeMailbox:
    def __init__(self, rows=None, docs=None, people=None):
        self.rows, self.docs = rows if rows is not None else [row(1), row(2)], docs or {}
        self.people = people if people is not None else [{'name': '张三', 'email': 'zhang@inspur.com'}]
        self.searches, self.reads = [], []

    def search(self, pattern, offset, limit, filters):
        self.searches.append((pattern, offset, limit, filters))
        return {'rows': self.rows[offset:offset + limit], 'total': len(self.rows)}

    def read(self, mid):
        self.reads.append(mid)
        return self.docs.get(mid) or document()

    def match(self, name):
        return [p for p in self.people if name in p['name']]

    def tools(self, allowed=()):
        return MailboxTools(self.search, self.read, self.match, {'inbox': '1', 'sent': '3', 'names': {'1': '收件箱'}}, allowed)


class Script:
    """A model that plays back messages: ('tools', [(name, args)]) or ('answer', text)."""

    def __init__(self, *steps):
        self.steps, self.seen = list(steps), []

    def __call__(self, messages, specs):
        self.seen.append((json.loads(json.dumps(messages)), specs))
        kind, value = self.steps.pop(0)
        if kind == 'answer':
            return {'content': value, 'tool_calls': []}
        return {'content': '', 'tool_calls': [{'id': f'c{len(self.seen)}{i}', 'name': n, 'arguments': json.dumps(a, ensure_ascii=False)}
                                              for i, (n, a) in enumerate(value)]}


class ClientToolsTest(unittest.TestCase):
    def test_tools_go_out_and_calls_come_back(self):
        chat, opener, _ = client(tool_reply(('search_mail', {'keywords': '预算'})))
        message, tokens = chat.complete([{'role': 'user', 'content': '问'}], tools=SPECS)
        body = json.loads(opener.requests[0].data)
        self.assertEqual([t['function']['name'] for t in body['tools']], ['search_mail', 'read_mail', 'find_contact'])
        self.assertEqual(message, {'content': '', 'tool_calls': [{'id': 'c0', 'name': 'search_mail', 'arguments': '{"keywords": "\\u9884\\u7b97"}'}]})
        self.assertEqual(tokens, 7)

    def test_plain_chat_unchanged_and_empty_rejected(self):
        chat, opener, _ = client(reply('好'))
        self.assertEqual(chat.chat([{'role': 'user', 'content': '问'}]), ('好', 42))
        self.assertNotIn('tools', json.loads(opener.requests[0].data))
        for bad in (reply(''), reply('  ')):
            chat, _, _ = client(bad)
            with self.assertRaises(AppError) as raised:
                chat.complete([{'role': 'user', 'content': '问'}])
            self.assertEqual(raised.exception.code, 'AI_BAD_RESPONSE')
        broken = json.dumps({'choices': [{'message': {'content': None, 'tool_calls': [{'id': 'x', 'function': {}}]}}]}).encode()
        chat, _, _ = client(broken)
        with self.assertRaises(AppError):
            chat.complete([{'role': 'user', 'content': '问'}])


class ToolsTest(unittest.TestCase):
    def test_search_hides_ids_and_addresses(self):
        box = FakeMailbox()
        result, step = box.tools().run('search_mail', json.dumps({'keywords': ' 预算  报价 ', 'folder': 'inbox', 'since': '2026-10-01'}))
        self.assertEqual(box.searches, [('预算 报价', 0, 20, {'since': '2026-10-01', 'folder': '1'})])
        self.assertEqual([r['ref'] for r in result['rows']], ['M1', 'M2'])
        text = json.dumps(result, ensure_ascii=False)
        for secret in ('mid1', 'zhang1@', 'zhang1'):
            self.assertNotIn(secret, text)
        self.assertEqual(result['rows'][1], {'ref': 'M2', 'source': '服务器', 'subject': '主题2', 'from': '张三（inspur.com）', 'date': '2026-10-12 09:30',
                                             'folder': '收件箱', 'attachment': True, 'snippet': '正文开头2'})
        self.assertIn('预算 报价', step)
        self.assertIn('2 封', step)

    def test_refs_are_stable_across_searches(self):
        tools = FakeMailbox().tools()
        tools.run('search_mail', '{"keywords": "a"}')
        result, _ = tools.run('search_mail', '{"keywords": "b"}')
        self.assertEqual([r['ref'] for r in result['rows']], ['M1', 'M2'])

    def test_sender_resolution(self):
        box = FakeMailbox(people=[{'name': '张三', 'email': 'zhang@inspur.com'}, {'name': '张三丰', 'email': 'feng@inspur.com'}])
        tools = box.tools()
        tools.run('search_mail', '{"sender": "张三"}')  # One exact name among two matches.
        self.assertEqual(box.searches[-1][3], {'from': 'zhang@inspur.com'})
        tools.run('search_mail', '{"sender": "zhang@inspur"}')
        self.assertEqual(box.searches[-1][3], {'from': 'zhang@inspur'})
        result, _ = tools.run('search_mail', '{"sender": "张"}')
        self.assertEqual([p['ref'] for p in result['ambiguous']], ['P1', 'P2'])
        self.assertNotIn('feng@', json.dumps(result))
        tools.run('search_mail', '{"keywords": "x", "sender": "P2"}')
        self.assertEqual(box.searches[-1][3], {'from': 'feng@inspur.com'})
        result, _ = tools.run('search_mail', '{"sender": "王五"}')
        self.assertIn('error', result)
        empty = MailboxTools(lambda *a: {'rows': [], 'total': 0}, box.read, box.match, {'names': {}})
        self.assertIn('减少关键词', empty.run('search_mail', '{"keywords": "签字件"}')[0]['hint'])

    def test_bad_arguments_are_answered_not_raised(self):
        tools = FakeMailbox().tools()
        for name, arguments in (('search_mail', '{}'), ('search_mail', '{"keywords": "a", "since": "10/01"}'),
                                ('search_mail', '{"keywords": "a", "until": "2026-02-30"}'), ('search_mail', 'not json'),
                                ('read_mail', '{"ref": "M9"}'), ('delete_mail', '{}'), ('find_contact', '{}')):
            result, step = tools.run(name, arguments)
            self.assertIn('error', result, (name, arguments))
            self.assertTrue(step)

    def test_server_refusal_becomes_a_hint(self):
        tools = FakeMailbox().tools()
        tools._search = lambda *a: (_ for _ in ()).throw(AppError('INVALID_SEARCH'))
        self.assertIn('error', tools.run('search_mail', '{"keywords": "a"}')[0])
        tools._search = lambda *a: (_ for _ in ()).throw(AppError('NETWORK_ERROR'))
        with self.assertRaises(AppError):
            tools.run('search_mail', '{"keywords": "a"}')

    def test_read_mail_text_and_limits(self):
        rows = [row(i) for i in range(1, 8)]
        box = FakeMailbox(rows=rows, docs={'mid1': document('甲' * 9000)})
        tools = box.tools()
        tools.run('search_mail', '{"keywords": "a"}')
        result, _ = tools.run('read_mail', '{"ref": "m1"}')
        self.assertTrue(result['truncated'])
        self.assertLess(len(result['text']), 4100)
        self.assertEqual((result['to'], result['attachments']), ('李四（inspur.com）', ['a.pdf']))
        self.assertIn('error', tools.run('read_mail', '{"ref": "M1"}')[0])  # Already read.
        for i in range(2, 6):
            self.assertNotIn('error', tools.run('read_mail', json.dumps({'ref': f'M{i}'}))[0])
        self.assertIn('error', tools.run('read_mail', '{"ref": "M6"}')[0])
        self.assertEqual(len(box.reads), 5)
        self.assertEqual(tools.read_refs, ['M1', 'M2', 'M3', 'M4', 'M5'])

    def test_read_mail_reads_the_attachments_too(self):
        mail = document('详见附件')
        mail.attachments = [SimpleNamespace(name='报价.txt', part_id='2')]
        mail.attachment_payloads = {'2': '单价 42 元'.encode('utf-8')}
        box = FakeMailbox(docs={'mid1': mail})
        tools = box.tools()
        tools.run('search_mail', '{"keywords": "a"}')
        result, _ = tools.run('read_mail', '{"ref": "M1"}')
        self.assertEqual(result['attachmentText'], [{'name': '报价.txt', 'text': '单价 42 元', 'truncated': False}])
        self.assertNotIn('attachmentText', tools.run('read_mail', '{"ref": "M2"}')[0])

    def test_encrypted_mail_needs_the_users_agreement(self):
        docs = {'mid1': document('机密内容', 'decrypted'), 'mid2': document('机密', 'locked')}
        box = FakeMailbox(docs=docs)
        tools = box.tools()
        tools.run('search_mail', '{"keywords": "a"}')
        result, step = tools.run('read_mail', '{"ref": "M1"}')
        self.assertTrue(result['encrypted'])
        self.assertNotIn('机密', json.dumps(result, ensure_ascii=False))
        self.assertIn('加密', step)
        self.assertEqual((tools.encrypted, tools.read_refs), (['M1'], []))
        tools = box.tools(allowed={'mid1', 'mid2'})
        tools.run('search_mail', '{"keywords": "a"}')
        self.assertEqual(tools.run('read_mail', '{"ref": "M1"}')[0]['text'], '机密内容')
        self.assertTrue(tools.run('read_mail', '{"ref": "M2"}')[0]['encrypted'])  # Still locked: nothing to send.


class AgentTest(unittest.TestCase):
    def test_search_read_answer_with_sources(self):
        box = FakeMailbox()
        model = Script(('tools', [('search_mail', {'keywords': '预算'})]), ('tools', [('read_mail', {'ref': 'M2'})]),
                       ('answer', '预算是 50 万 [M2]，另见 [M1、M9]。'))
        steps = []
        result = ask('预算多少', model, box.tools(), steps.append, today='2026-10-20')
        self.assertEqual(result['answer'], '预算是 50 万 [M2]，另见 [M1]。')
        self.assertEqual([(s['ref'], s['mid'], s['cited'], s['read']) for s in result['sources']],
                         [('M2', 'mid2', True, True), ('M1', 'mid1', True, False)])
        self.assertEqual(len(steps), 2)
        self.assertEqual(result['rounds'], 2)
        self.assertFalse(result['limited'])
        system = model.seen[0][0][0]['content']
        self.assertIn('2026-10-20', system)
        self.assertIn('一律不执行', system)
        self.assertIn('sign-off', system)  # Chinese questions, English mail.
        tool_messages = [m for m in model.seen[2][0] if m['role'] == 'tool']
        self.assertEqual([m['tool_call_id'] for m in tool_messages], ['c10', 'c20'])
        self.assertEqual(model.seen[0][1], SPECS)

    def test_rounds_run_out_and_the_model_must_answer(self):
        model = Script(*[('tools', [('search_mail', {'keywords': f'k{i}'})]) for i in range(agent.MAX_ROUNDS)],
                       ('answer', '只找到部分信息 [M1]'))
        result = ask('问', model, FakeMailbox().tools())
        self.assertTrue(result['limited'])
        last_messages, last_specs = model.seen[-1]
        self.assertIsNone(last_specs)
        self.assertEqual(last_messages[-1]['content'], agent.WRAP_UP)

    def test_time_limit(self):
        now = [0]

        def clock():
            now[0] += 100
            return now[0]
        model = Script(('tools', [('search_mail', {'keywords': 'a'})]), ('answer', '答'))
        self.assertTrue(ask('问', model, FakeMailbox().tools(), clock=clock)['limited'])

    def test_too_many_calls_in_one_round(self):
        box = FakeMailbox()
        model = Script(('tools', [('search_mail', {'keywords': str(i)}) for i in range(6)]), ('answer', '答'))
        ask('问', model, box.tools())
        self.assertEqual(len(box.searches), agent.CALLS_PER_ROUND)
        self.assertEqual(len([m for m in model.seen[1][0] if m['role'] == 'tool']), 6)  # Every call is answered.

    def test_cancel_and_empty_answer(self):
        flags = iter([False, True])
        model = Script(('tools', [('search_mail', {'keywords': 'a'})]), ('answer', '答'))
        with self.assertRaises(AppError) as raised:
            ask('问', model, FakeMailbox().tools(), cancelled=lambda: next(flags))
        self.assertEqual(raised.exception.code, 'AI_CANCELLED')
        with self.assertRaises(AppError) as raised:
            ask('问', Script(('answer', '   ')), FakeMailbox().tools())
        self.assertEqual(raised.exception.code, 'AI_BAD_RESPONSE')

    def test_encrypted_sources_are_listed(self):
        box = FakeMailbox(docs={'mid1': document('x', 'decrypted')})
        model = Script(('tools', [('search_mail', {'keywords': 'a'})]), ('tools', [('read_mail', {'ref': 'M1'})]), ('answer', '没能读到'))
        result = ask('问', model, box.tools())
        self.assertEqual(result['encrypted'], 1)
        self.assertEqual([(s['ref'], s['encrypted'], s['cited']) for s in result['sources']], [('M1', True, False)])

    def test_follow_up_gets_history_known_refs_and_current_mail(self):
        box = FakeMailbox()
        tools = box.tools()
        ask('预算多少', Script(('tools', [('search_mail', {'keywords': '预算'})]), ('answer', '50 万 [M1]，另见 [M2]')), tools)
        tools.begin()
        model = Script(('tools', [('read_mail', {'ref': 'M2'})]), ('answer', '第二封是张三发的 [M2]'))
        result = ask('第二封是谁发的', model, tools, history=[('预算多少', '50 万 [M1]，另见 [M2]')], known=['M1', 'M2'])
        messages = model.seen[0][0]
        self.assertIn('M2｜主题2｜张三', messages[1]['content'])
        self.assertIn('连续对话', messages[1]['content'])
        self.assertEqual([(m['role'], m['content']) for m in messages[2:]],
                         [('user', '预算多少'), ('assistant', '50 万 [M1]，另见 [M2]'), ('user', '第二封是谁发的')])
        self.assertEqual(box.reads, ['mid2'])  # No new search: the ref from the first question still works.
        self.assertEqual([(s['ref'], s['cited'], s['read']) for s in result['sources']], [('M2', True, True)])
        current = tools.add(row(7))
        model = Script(('answer', '答'))
        ask('这封说了什么', model, tools, history=[('a', 'b')] * 9, known=['M1'], current=current)
        messages = model.seen[0][0]
        self.assertIn('用户正在看的邮件是 M3｜主题7', messages[2]['content'])
        self.assertEqual(len([m for m in messages if m['role'] == 'assistant']), agent.CHAT_TURNS)
        model = Script(('answer', '答'))
        ask('新问题', model, tools)  # No history: no conversation note, as before.
        self.assertEqual([m['role'] for m in model.seen[0][0]], ['system', 'user'])
        model = Script(('answer', '答'))
        ask('表里我那一行写了什么', model, tools, me='王彪、wangbiao@corp.example')
        note = model.seen[0][0][0]['content']
        self.assertIn('用户本人：王彪、wangbiao@corp.example', note)
        self.assertIn('不要反问用户叫什么', note)

    def test_begin_resets_reads_and_agreements(self):
        box = FakeMailbox(docs={'mid1': document('机密', 'decrypted')})
        tools = box.tools(allowed={'mid1'})
        tools.run('search_mail', '{"keywords": "a"}')
        self.assertEqual(tools.run('read_mail', '{"ref": "M1"}')[0]['text'], '机密')
        tools.begin()
        self.assertTrue(tools.run('read_mail', '{"ref": "M1"}')[0]['encrypted'])  # The agreement was for one question.
        self.assertEqual(tools.encrypted, ['M1'])
        tools.begin({'mid1'})
        self.assertEqual(tools.run('read_mail', '{"ref": "M1"}')[0]['text'], '机密')


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.directory = scratch(self)
        self.backend = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(),
                                   services_factory=local_services_stub)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(self.backend.logout)

    def test_not_configured_and_invalid(self):
        with self.assertRaises(AppError) as raised:
            self.backend.ask_mailbox('预算')
        self.assertEqual(raised.exception.code, 'AI_NOT_CONFIGURED')
        self.backend.ai_client = DemoAiClient()
        for question, allow in (('', ()), ('问' * 501, ()), ('问', 'mid'), ('问', ['m'] * 21)):
            with self.assertRaises(AppError) as raised:
                self.backend.ask_mailbox(question, allow)
            self.assertEqual(raised.exception.code, 'INVALID_QUESTION')

    def test_demo_question_end_to_end(self):
        self.backend.ai_client = DemoAiClient()
        subject = self.backend.adapter.rows['demo1']['subject']
        steps = []
        result = self.backend.ask_mailbox(subject[:2], progress=steps.append)
        self.assertTrue(result['sources'] and result['sources'][0]['cited'])
        self.assertTrue(result['answer'].startswith('演示模式'))
        self.assertEqual(len(steps), 2)
        mid = result['sources'][0]['mid']
        self.assertIsNotNone(self.backend.cache.summary(mid))  # A source card opens like a listed mail.
        self.assertEqual(self.backend.ai_settings()['usage']['calls'], 3)
        self.assertIn('demo@example.invalid', self.backend._me())

    def test_server_search_asks_for_snippets_and_scopes_folders(self):
        seen = []
        original = self.backend.adapter.search_messages

        def search(pattern, offset, limit=50, filters=None, snippet=False):
            seen.append((pattern, filters, snippet))
            return original(pattern, offset, limit, filters)
        self.backend.adapter.search_messages = search
        model = Script(('tools', [('search_mail', {'keywords': '周报', 'folder': 'sent'})]), ('answer', '答'))
        self.backend.ai_client = SimpleNamespace(model='m', complete=lambda messages, tools=None, max_tokens=0: (model(messages, tools), 5))
        self.backend.folders()
        self.backend.ask_mailbox('周报')
        sent = next(f['id'] for f in self.backend._folders if f['role'] == 'sent')
        self.assertEqual(seen, [('周报', {'folder': sent}, True)])
        self.assertEqual(self.backend.ai_settings()['usage']['tokens'], 10)

    def test_cancel(self):
        self.backend.ai_client = DemoAiClient()
        self.backend.cancel_ask()
        self.assertTrue(self.backend._ask_cancel.is_set())
        self.backend.ask_mailbox('周报')  # A new question starts uncancelled.

    def test_conversation_continues_redoes_and_expires(self):
        model = Script(('tools', [('search_mail', {'keywords': '周报'})]), ('answer', '有 [M1]'),
                       ('answer', '是张三 [M1]'), ('answer', '重新回答 [M1]'), ('answer', '新的'))
        self.backend.ai_client = SimpleNamespace(model='m', complete=lambda messages, tools=None, max_tokens=0: (model(messages, tools), 1))
        first = self.backend.ask_mailbox('周报')
        self.assertEqual((first['turn'], first['restarted']), (1, False))
        second = self.backend.ask_mailbox('谁发的', chat=first['chat'])
        self.assertEqual((second['chat'], second['turn']), (first['chat'], 2))
        sent = model.seen[2][0]
        self.assertEqual([m['content'] for m in sent if m['role'] in ('user', 'assistant')], ['周报', '有 [M1]', '谁发的'])
        self.assertIn('M1｜', sent[1]['content'])
        again = self.backend.ask_mailbox('谁发的', chat=first['chat'], redo=True)
        self.assertEqual(again['turn'], 2)  # Replaced, not added.
        self.assertEqual([m['content'] for m in model.seen[3][0] if m['role'] in ('user', 'assistant')], ['周报', '有 [M1]', '谁发的'])
        gone = self.backend.ask_mailbox('还在吗', chat='feedfeedfeedfeed')
        self.assertTrue(gone['restarted'])
        self.assertNotEqual(gone['chat'], first['chat'])
        self.assertEqual([m['role'] for m in model.seen[4][0]], ['system', 'user'])
        for _ in range(MailBackend.CHATS):
            model.steps.append(('answer', '答'))
            self.backend.ask_mailbox('问')
        self.assertNotIn(first['chat'], self.backend._chats)  # Only the latest few are kept.
        self.backend.logout()
        self.assertEqual(self.backend._chats, {})

    def test_current_mail_and_invalid_chat(self):
        self.backend.ai_client = DemoAiClient()
        inbox = next(f['id'] for f in self.backend.folders() if f['role'] == 'inbox')
        mid = self.backend.messages(inbox)['rows'][0]['id']
        result = self.backend.ask_mailbox('这封说了什么', current=mid)
        self.assertTrue(result['answer'].startswith('演示模式：你正在看的'))
        self.assertEqual(result['sources'][0]['mid'], mid)
        for chat, current in ((5, ''), ('x' * 41, ''), ('', ['m'])):
            with self.assertRaises(AppError) as raised:
                self.backend.ask_mailbox('问', chat=chat, current=current)
            self.assertEqual(raised.exception.code, 'INVALID_QUESTION')


class WiringTest(unittest.TestCase):
    def test_bridge(self):
        from coremail_desktop.app import shell
        self.assertEqual((shell.BACKEND_METHODS['askMailbox'], shell.BACKEND_METHODS['cancelAsk']), ('ask_mailbox', 'cancel_ask'))
        self.assertIn('askMailbox', shell.AI_METHODS)
        self.assertIn('cancelAsk', shell.FAST_METHODS)  # Must not wait behind the question it cancels.

    def test_codes_have_messages(self):
        from coremail_desktop.ui.messages import MESSAGES
        for code in ('INVALID_QUESTION', 'AI_CANCELLED'):
            self.assertIn(code, MESSAGES)

    def test_page_renders_answer_escaped(self):
        source = (WEB / 'ai.js').read_text(encoding='utf-8')
        page = (WEB / 'index.html').read_text(encoding='utf-8')
        self.assertIn('id="chat"', page)
        self.assertNotIn('reader-ask', page)
        self.assertIn("chat: chat.id", source)  # The page sends only the conversation's id, not its text.
        self.assertIn('toggleChat()', (WEB / 'app.js').read_text(encoding='utf-8'))  # Ctrl+J
        for field in ('r.answer', 's.subject', 's.fromName', 's.snippet', 'step', 't.question', 'here.subject'):
            self.assertIn(f'esc({field}', source)


class ToolKindsTest(unittest.TestCase):
    """G1d: every tool says whether it only reads or only proposes a card; MCP gets the readers."""

    def test_every_tool_is_declared(self):
        from coremail_desktop.ai.tools import CARD, MCP_SPECS, READ, TOOL_KINDS, AssistantTools
        on = lambda *a, **k: None
        tools = AssistantTools(on, on, on, {}, tasks=on, task=on, mine=on, pending=on, plan=on, thread=on, groups=on,
                               actions=True, memory=on, followups=on, about=on)
        names = [s['function']['name'] for s in tools.specs]
        self.assertEqual(sorted(names), sorted(TOOL_KINDS))  # None undeclared, none declared and gone.
        self.assertEqual(set(TOOL_KINDS.values()), {READ, CARD})
        for spec in tools.specs:
            if TOOL_KINDS[spec['function']['name']] == CARD:
                text = spec['function']['description']  # The model is told nothing happens until the user acts.
                self.assertTrue('只生成' in text or '只提议' in text, text)
        self.assertTrue(MCP_SPECS)
        self.assertTrue(all(TOOL_KINDS[s['function']['name']] == READ for s in MCP_SPECS))


if __name__ == '__main__':
    unittest.main()
