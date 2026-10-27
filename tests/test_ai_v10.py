"""v1.0 AI: streamed replies, 写信助手, 帮我起草回复, 发送前检查 (本机 and AI) and 总结整个会话.
Fake openers and fake chat clients only; nothing leaves."""
import json
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from coremail_desktop.ai import precheck, thread, writing
from coremail_desktop.app.backend import MailBackend, thread_key
from coremail_desktop.app.demo import DemoAdapter, DemoAiClient
from coremail_desktop.errors import AppError
from tests.test_ai_summary import FakeOpener, client
from tests.test_app_backend import XorProtector, local_services_stub
from tests.test_compose_v05 import scratch

WEB = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'


def sse(*pieces, usage=55, done=True):
    lines = [b': keep-alive', b'']
    for piece in pieces:
        lines += [b'data: ' + json.dumps({'choices': [{'delta': {'content': piece}}]}, ensure_ascii=False).encode('utf-8'), b'']
    lines += [b'data: ' + json.dumps({'choices': [], 'usage': {'total_tokens': usage}}).encode('utf-8'), b'']
    if done:
        lines += [b'data: [DONE]', b'']
    return b'\n'.join(lines) + b'\n'


class StreamTest(unittest.TestCase):
    def test_pieces_arrive_in_order_with_usage(self):
        chat, opener, _ = client(sse('你', '好', None, '！'))
        got = []
        self.assertEqual(chat.stream([{'role': 'user', 'content': 'x'}], got.append), ('你好！', 55))
        self.assertEqual(got, ['你', '好', '！'])
        body = json.loads(opener.requests[0].data)
        self.assertEqual((body['stream'], body['stream_options']), (True, {'include_usage': True}))
        self.assertEqual(opener.requests[0].get_header('Accept'), 'text/event-stream')

    def test_retry_only_before_any_text(self):
        chat, opener, sleeps = client(503, sse('好'))
        self.assertEqual(chat.stream([], lambda d: None)[0], '好')
        self.assertEqual((len(opener.requests), sleeps), (2, [1]))
        chat, opener, _ = client(sse('半', done=False), sse('不该重试'))
        with self.assertRaises(AppError) as raised:
            chat.stream([], lambda d: None)
        self.assertEqual((raised.exception.code, len(opener.requests)), ('AI_UNAVAILABLE', 1))

    def test_errors_are_fixed_codes(self):
        for body, code in ((b'data: [DONE]\n', 'AI_BAD_RESPONSE'), (b'data: {not json\n', 'AI_BAD_RESPONSE'), (b'', 'AI_BAD_RESPONSE'),
                           (b'data: ' + json.dumps({'choices': [{'delta': {'content': 5}}]}).encode() + b'\n', 'AI_BAD_RESPONSE')):
            chat, _, _ = client(body)
            with self.assertRaises(AppError) as raised:
                chat.stream([], lambda d: None)
            self.assertEqual(raised.exception.code, code, body)
        chat, _, _ = client(401)
        with self.assertRaises(AppError) as raised:
            chat.stream([], lambda d: None)
        self.assertEqual(raised.exception.code, 'AI_AUTH_FAILED')
        chat, _, _ = client(TimeoutError())
        with self.assertRaises(AppError) as raised:
            chat.stream([], lambda d: None)
        self.assertEqual(raised.exception.code, 'AI_TIMEOUT')

    def test_cancel_stops_between_pieces(self):
        chat, _, _ = client(sse('一', '二', '三'))
        got = []
        with self.assertRaises(AppError) as raised:
            chat.stream([], got.append, cancelled=lambda: len(got) >= 2)
        self.assertEqual((raised.exception.code, got), ('AI_CANCELLED', ['一', '二']))

    def test_too_long_is_refused(self):
        from coremail_desktop.ai import client as module
        chat, _, _ = client(sse('长' * 50))
        old, module.MAX_RESPONSE = module.MAX_RESPONSE, 100
        try:
            with self.assertRaises(AppError) as raised:
                chat.stream([], lambda d: None)
        finally:
            module.MAX_RESPONSE = old
        self.assertEqual(raised.exception.code, 'AI_BAD_RESPONSE')


class WritingTest(unittest.TestCase):
    def test_ideas_are_checked(self):
        ideas = writing.parse_ideas('```json\n{"ideas": [{"label": "同意", "hint": "确认时间"}, {"label": ""}, "x", {"label": "改期"}, {"label": "a"}, {"label": "b"}]}\n```')
        self.assertEqual([i['label'] for i in ideas], ['同意', '改期', 'a'])
        for bad in ('不是 JSON', '{"ideas": []}', '{"ideas": "同意"}'):
            with self.assertRaises(AppError):
                writing.parse_ideas(bad)

    def test_draft_needs_a_direction(self):
        document = SimpleNamespace(headers={'subject': ('测试',), 'from': ('张三 <zs@inspur.com>',)}, html=None, text='请周四前给方案。', attachments=())
        with self.assertRaises(AppError):
            writing.draft_messages(document, '', '  ')
        messages = writing.draft_messages(document, '同意：确认时间', '周四前给')
        self.assertIn('张三（inspur.com）', messages[1]['content'])
        self.assertNotIn('zs@inspur.com', messages[1]['content'])
        self.assertIn('回复方向：同意：确认时间', messages[1]['content'])
        self.assertEqual(writing.tidy_draft('```\n您好：\n\n\n\n收到。\n```'), '您好：\n\n收到。')

    def test_note_prompt(self):
        document = SimpleNamespace(headers={'subject': ('LDAP 测试',), 'from': ('Ahmed <ah@stc.com.sa>',)}, html=None, text='Please confirm.', attachments=())
        messages = writing.note_messages('跟他说周四前做完', 'Re: LDAP 测试', ['Ahmed（外部）'], [], document, how='reply')
        self.assertIn('有外部收件人时用英文', messages[0]['content'])
        self.assertIn('这封邮件是回复', messages[1]['content'])
        self.assertIn('主送：Ahmed（外部）', messages[1]['content'])
        self.assertIn('用户的口述：\n跟他说周四前做完', messages[1]['content'])
        self.assertIn('要回复的原邮件', messages[1]['content'])
        self.assertNotIn('ah@stc.com.sa', messages[1]['content'])
        mark = messages[0]['content'].split(' 与 ')[0].rsplit('\n', 1)[-1]
        self.assertTrue(mark.startswith('<<<MAIL-'))
        again = writing.note_messages('要点', rounds=[('第一版', '  更正式\n一点 '), ('第二版', '译成英文')])
        self.assertEqual([m['role'] for m in again], ['system', 'user', 'assistant', 'user', 'assistant', 'user'])
        self.assertEqual(again[2]['content'], '第一版')
        self.assertIn('修改意见：更正式 一点', again[3]['content'])
        self.assertEqual(len(writing.note_messages('要点', rounds=[('版', '改')] * 9)), 2 + 2 * writing.ROUNDS)
        with self.assertRaises(AppError):
            writing.note_messages('要点', rounds=[('第一版', ' ')])
        plain = writing.note_messages('通知大家开会', to=['张三（同事）'])[1]['content']
        self.assertIn('这封邮件是新邮件', plain)
        self.assertNotIn('原邮件', plain)
        for note, code in (('  ', 'AI_INVALID_INPUT'), ('长' * 8001, 'AI_TEXT_TOO_LONG')):
            with self.assertRaises(AppError) as raised:
                writing.note_messages(note)
            self.assertEqual(raised.exception.code, code)


class PrecheckTest(unittest.TestCase):
    TODAY = date(2026, 10, 20)

    def test_past_dates_only_in_future_phrases(self):
        found = precheck.past_dates('请在10月15日前提交；10月15日已经开过会。会议定于2026-10-25。', self.TODAY)
        self.assertEqual([i['quote'] for i in found], ['10月15日'])
        self.assertEqual(precheck.past_dates('我们计划 1月5日 上线', self.TODAY), [])  # Long ago: probably next year.
        self.assertEqual(precheck.past_dates('截止 2026年10月19日', self.TODAY)[0]['kind'], 'date')
        self.assertEqual(precheck.past_dates('请在2月30日前', self.TODAY), [])

    def test_greeting(self):
        to = [{'name': '李四', 'email': 'ls@inspur.com'}]
        self.assertEqual(precheck.greeting_name('张总，你好：\n正文'), '张')
        self.assertEqual(precheck.greeting_name('王彪：'), '王彪')
        self.assertEqual(precheck.greeting_name('Dear Tom,'), 'Tom')
        for line in ('好的！', '收到，谢谢', '你好，', '各位领导：', 'Hi all,', '这是一段很长很长很长很长很长很长很长很长的正文开头，不是称呼'):
            self.assertEqual(precheck.greeting_name(line), '', line)
        self.assertEqual(precheck.greeting('张总，你好：', to)[0]['kind'], 'greeting')
        self.assertEqual(precheck.greeting('李工，你好：', to), [])
        self.assertEqual(precheck.greeting('张总，你好：', [{'name': '', 'email': 'zhang@x.com'}]), [])  # No Chinese name: unknown.
        self.assertEqual(precheck.greeting('Dear Tom,', [{'name': '', 'email': 'tom.smith@x.com'}]), [])
        self.assertTrue(precheck.greeting('Dear Tom,', [{'name': 'Jerry', 'email': 'jerry@x.com'}]))

    def test_outside_addresses(self):
        text = '请联系 a@partner.com 或 me@inspur.com，抄送 b@partner.com。'
        found = precheck.check(text, [], ['b@partner.com'], ['inspur.com'], '2026-10-20')
        self.assertEqual([i['quote'] for i in found], ['a@partner.com'])


class ThreadTextTest(unittest.TestCase):
    def doc(self, text, sender='张三 <zs@inspur.com>'):
        return SimpleNamespace(headers={'from': (sender,), 'to': ('李四 <ls@inspur.com>',), 'date': ('d',)}, html=None, text=text)

    def test_own_text_drops_quotes(self):
        text = '好的，周四给。\n> 上一封\n\n-----原始邮件-----\n发件人: 李四\n发送时间: 昨天\n很长的原信'
        self.assertEqual(thread.own_text(self.doc(text)), '好的，周四给。')

    def test_pick_and_limits(self):
        rows = list(range(25))
        picked, left = thread.pick(rows)
        self.assertEqual((picked[:3], picked[-1], len(picked), left), ([0, 1, 7], 24, 20, 5))
        mails = [{'date': f'2026-10-{i:02d}', 'document': self.doc('字' * 5000)} for i in range(1, 21)]
        mails[3] = {'date': '2026-10-04', 'document': None}
        content = thread.build_messages(mails, left_out=5)[1]['content']
        self.assertLess(len(content), thread.TOTAL_CHARS + 5000)
        self.assertIn('（中间还有 5 封邮件没有提供）', content)
        self.assertIn('加密邮件，用户没有同意发送', content)
        self.assertNotIn('zs@inspur.com', content)

    def test_parse_keeps_known_numbers(self):
        value = thread.parse(json.dumps({'conclusion': '待确认', 'timeline': [{'n': 1, 'text': '提出'}, {'n': 9, 'text': '越界'}, {'n': 'x'}],
                                         'open': '一个问题', 'actions': []}, ensure_ascii=False), 3)
        self.assertEqual(value, {'conclusion': '待确认', 'timeline': [{'n': 1, 'text': '提出'}], 'open': ['一个问题'], 'actions': []})
        with self.assertRaises(AppError):
            thread.parse('{"open": []}', 3)


class FakeStreamAi:
    model = 'fake-model'

    def __init__(self, content='{"ideas": [{"label": "同意", "hint": "确认"}]}'):
        self.calls, self.content = [], content

    def chat(self, messages, max_tokens=800, json_mode=False):
        self.calls.append(messages)
        return self.content, 10

    def stream(self, messages, on_delta, cancelled=lambda: False, max_tokens=800):
        self.calls.append(messages)
        for piece in ('改好', '的文字'):
            if cancelled():
                raise AppError('AI_CANCELLED')
            on_delta(piece)
        return '改好的文字', 20


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.directory = scratch(self)
        self.backend = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(self.backend.logout)

    def test_write_streams_and_counts(self):
        ai = self.backend.ai_client = FakeStreamAi()
        pieces = []
        self.assertEqual(self.backend.write_from_note('原文', req='r1', progress=pieces.append), {'text': '改好的文字'})
        self.assertEqual(''.join(p['delta'] for p in pieces), '改好的文字')
        self.assertTrue(all(p['req'] == 'r1' for p in pieces))
        self.assertEqual(self.backend.ai_settings()['usage'], {'tokens': 20, 'calls': 1})
        self.backend.cancel_ai()  # A stop before the next call does not leak into it.
        rounds = [{'text': '改好的文字', 'want': '译成英文'}]
        self.assertEqual(self.backend.write_from_note('原文', rounds=rounds)['text'], '改好的文字')
        self.assertIn('修改意见：译成英文', ai.calls[1][-1]['content'])
        with self.assertRaises(AppError):
            self.backend.write_from_note('原文', rounds=['译成英文'])

    def test_write_follows_the_users_own_sent_mail(self):
        ai = self.backend.ai_client = FakeStreamAi()
        colleague, outside = [{'name': '张晓明', 'email': 'zhangxm@example.invalid'}], [{'name': 'P', 'email': 'p@external.example'}]
        self.backend.write_from_note('原文', to=colleague)
        self.assertNotIn('风格样例', ai.calls[-1][1]['content'])  # Nothing sent is on this computer yet.
        rows = self.backend.messages('3')['rows']
        for r in rows:
            self.backend.open_message(r['id'], mark_read=False)
        self.backend.write_from_note('原文', to=colleague)
        system, user = ai.calls[-1][0]['content'], ai.calls[-1][1]['content']
        self.assertIn('写作风格样例', system)
        self.assertIn('用户以前写的邮件（风格样例 1）：\n各位同事', user)
        self.assertNotIn('@example.invalid', user)
        self.assertLessEqual(user.count('风格样例'), writing.SAMPLES)
        self.backend.write_from_note('原文', to=colleague + outside)  # Any outside recipient: mail that went outside.
        self.assertIn('风格样例 1', ai.calls[-1][1]['content'])

    def test_style_sample_drops_signature_quote_and_contacts(self):
        mail = SimpleNamespace(html='<p>王总您好：</p><p>方案已更新，请查收，有问题打 13812345678 或写信 wb@inspur.com。</p>'
                                    '<div class="hy-signature">王彪 · 浪潮</div><div class="hy-quote">原邮件</div>', text='')
        sample = writing.style_sample(mail)
        self.assertIn('王总您好：', sample)
        self.assertIn('【电话】', sample)
        self.assertIn('【邮箱】', sample)
        for gone in ('王彪', '原邮件', '13812345678', 'wb@'):
            self.assertNotIn(gone, sample)
        elsewhere = SimpleNamespace(html=None, text='李工：\n\n收到，测试环境周四前准备好，到时给您答复。\n\n王彪\n平台部\n\n发件人: 李工\n发送时间: 昨天\n原文')
        self.assertEqual(writing.style_sample(elsewhere, '王彪\n平台部'), '李工：\n\n收到，测试环境周四前准备好，到时给您答复。')
        self.assertEqual(writing.style_sample(SimpleNamespace(html=None, text='好的')), '')

    def test_cancel_while_streaming(self):
        self.backend.ai_client = FakeStreamAi()
        with self.assertRaises(AppError) as raised:
            self.backend.write_from_note('原文', progress=lambda p: self.backend.cancel_ai())
        self.assertEqual(raised.exception.code, 'AI_CANCELLED')

    def test_reply_ideas_and_draft(self):
        ai = self.backend.ai_client = FakeStreamAi()
        with self.assertRaises(AppError) as raised:
            self.backend.reply_ideas('demo2')  # Not downloaded.
        self.assertEqual(raised.exception.code, 'AI_MAIL_UNAVAILABLE')
        self.backend.open_message('demo1', mark_read=False)
        self.assertEqual(self.backend.reply_ideas('demo1')['ideas'], [{'label': '同意', 'hint': '确认'}])
        self.assertEqual(self.backend.draft_reply('demo1', idea='同意：确认', note='周四')['text'], '改好的文字')
        self.assertIn('用户的补充说明：周四', ai.calls[-1][1]['content'])
        self.backend._docs['demo1'] = replace(self.backend._document('demo1'), encryption_state='decrypted')
        for call in (lambda: self.backend.reply_ideas('demo1'), lambda: self.backend.draft_reply('demo1', idea='同意')):
            with self.assertRaises(AppError) as raised:
                call()
            self.assertEqual(raised.exception.code, 'AI_ENCRYPTED_CONFIRM')
        self.assertTrue(self.backend.reply_ideas('demo1', allow_encrypted=True)['encrypted'])

    def test_write_from_note(self):
        ai = self.backend.ai_client = FakeStreamAi()
        to = [{'name': '李四', 'email': 'ls@example.invalid'}, {'name': '', 'email': 'bob@outside.com'}]
        self.assertEqual(self.backend.write_from_note('说一下周四开会', '开会', to, mid='demo2', how='reply')['text'], '改好的文字')
        sent = ai.calls[-1][1]['content']
        self.assertIn('李四（同事）、bob（外部）', sent)
        self.assertNotIn('outside.com', sent)
        self.assertNotIn('要回复的原邮件', sent)  # demo2 is not downloaded: written without it.
        self.backend.open_message('demo1', mark_read=False)
        self.backend.write_from_note('同意', mid='demo1', how='reply')
        self.assertIn('要回复的原邮件', ai.calls[-1][1]['content'])
        self.backend._docs['demo1'] = replace(self.backend._document('demo1'), encryption_state='decrypted')
        with self.assertRaises(AppError) as raised:
            self.backend.write_from_note('同意', mid='demo1', how='reply')
        self.assertEqual(raised.exception.code, 'AI_ENCRYPTED_CONFIRM')
        self.backend.write_from_note('同意', mid='demo1', how='reply', allow_encrypted=True)

    def test_precheck_and_ai_check(self):
        result = self.backend.precheck('张总，你好：\n请联系 x@outside.com', to=[{'name': '李四', 'email': 'ls@example.invalid'}],
                                       recipients=['ls@example.invalid'])
        self.assertEqual([i['kind'] for i in result['issues']], ['greeting', 'address'])
        self.assertEqual(self.backend.precheck('请联系 y@example.invalid', [], [])['issues'], [])  # Own domain.
        with self.assertRaises(AppError):
            self.backend.precheck(None)

    def test_thread_summary(self):
        b = self.backend
        b.ai_client = DemoAiClient()
        b.folders()
        b.messages('1', 0, 100)
        mid = next(r['id'] for r in b.cache.summaries('1', 0, 200)[0] if thread_key(r['subject']) == '接口联调时间确认（演示）')
        status = b.thread_ai_status(mid)
        self.assertGreaterEqual(len(status['ids']), 2)
        self.assertTrue(status['missing'])
        with self.assertRaises(AppError) as raised:
            b.summarize_thread(mid)
        self.assertEqual(raised.exception.code, 'AI_MAIL_UNAVAILABLE')
        for m in status['missing']:
            b.open_message(m, mark_read=False)
        self.assertEqual(b.thread_ai_status(mid)['missing'], [])
        value = b.summarize_thread(mid)
        self.assertEqual(value['count'], len(status['ids']))
        self.assertTrue(all(s['mid'] in status['ids'] and 'n' not in s for s in value['timeline']))
        self.assertTrue(b.summarize_thread(mid)['cached'])

    def test_demo_stream(self):
        self.backend.ai_client = DemoAiClient()
        self.backend.open_message('demo1', mark_read=False)
        self.assertEqual(len(self.backend.reply_ideas('demo1')['ideas']), 3)
        self.assertIn('演示模式', self.backend.draft_reply('demo1', idea='同意')['text'])
        pieces = []
        self.assertTrue(self.backend.write_from_note('周四前做完', progress=pieces.append)['text'].startswith('您好：\n\n周四前做完\n'))
        self.assertTrue(pieces)


class WiringTest(unittest.TestCase):
    def test_codes_and_threads(self):
        from coremail_desktop.app import shell
        from coremail_desktop.ui.messages import MESSAGES
        for code in ('AI_CANCELLED', 'AI_INVALID_INPUT', 'AI_TEXT_TOO_LONG', 'AI_NOT_A_THREAD'):
            self.assertIn(code, MESSAGES)
        for name in ('replyIdeas', 'draftReply', 'writeFromNote', 'summarizeThread'):
            self.assertIn(name, shell.AI_METHODS)
        self.assertFalse({'rewrite', 'checkDraft'} & set(shell.BACKEND_METHODS))  # The AI menu is gone: one way to write.
        self.assertLessEqual(shell.STREAM_METHODS, shell.AI_METHODS)
        self.assertIn('precheck', shell.FAST_METHODS)
        self.assertIn('cancelAi', shell.FAST_METHODS)
        self.assertNotIn('threadAiStatus', shell.FAST_METHODS | shell.AI_METHODS)  # Mail thread: it may search and decrypt.

    def test_page_keeps_ai_text_plain(self):
        source = (WEB / 'ai.js').read_text(encoding='utf-8')
        for field in ('x.label', 'x.hint', 's.text', 's.who', 'a.text', 'i.advice', 'i.quote'):
            self.assertIn(f'esc({field})', source)
        self.assertIn("live.textContent = text", source)
        self.assertIn("'.hy-signature, .hy-quote'", source)  # Never sent to the AI service.
        self.assertIn('id="cmp-ai"', (WEB / 'index.html').read_text(encoding='utf-8'))
        self.assertIn("$('#cmp-ai').onclick = () => writeFromNote();", source)
