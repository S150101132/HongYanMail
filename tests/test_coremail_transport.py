import io
import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from probe.coremail_transport import (CoremailSession, CoremailError,
    LoginChallenge, SameOriginRedirectHandler, decode_extended_json)


class Response(io.BytesIO):
    def __init__(self, body, url='https://newmail.inspur.com/coremail/'):
        super().__init__(body.encode() if isinstance(body, str) else body)
        self.url = url
        self.headers = {}

    def geturl(self):
        return self.url


class TransportTests(unittest.TestCase):
    def session(self, responses):
        session = CoremailSession()
        self.requests = []
        def open_request(request, timeout):
            self.requests.append(request)
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        session._opener.open = open_request
        return session

    def test_public_embedded_form_is_submitted_without_reusing_identity(self):
        page = Path('analysis/web-login/public-login.html').read_text(encoding='utf-8')
        session = self.session([Response(page), Response('ok',
            'https://newmail.inspur.com/coremail/main.jsp?sid=own-test-session')])
        session.login('wangbiao@inspur.com', 'test-secret')
        self.assertEqual(session.sid, 'own-test-session')
        request = self.requests[1]
        self.assertEqual(urlsplit(request.full_url).path, '/coremail/index.jsp')
        form = parse_qs(request.data.decode(), keep_blank_values=True)
        self.assertEqual(form['locale'], ['zh_CN'])
        self.assertEqual(form['password'], ['test-secret'])
        self.assertEqual(form['signature'], [''])
        self.assertEqual(form['authorizeDevice'], [''])
        self.assertEqual(json.loads(form['device'][0])['deviceType'], 'Webmail')
        session.close()
        self.assertIsNone(session.sid)

    def test_cross_origin_form_never_receives_credentials(self):
        page = '<form class="j-login-form" action="https://evil.test/login"><input name="password" type="password"></form>'
        session = self.session([Response(page)])
        with self.assertRaises(CoremailError):
            session.login('uid', 'test-secret')
        self.assertEqual(len(self.requests), 1)

    def test_cross_origin_redirect_is_blocked_before_following(self):
        handler = SameOriginRedirectHandler('https://newmail.inspur.com')
        from urllib.request import Request
        for target in ['https://evil.test/a', 'http://newmail.inspur.com/a']:
            with self.assertRaises(CoremailError):
                handler.redirect_request(Request('https://newmail.inspur.com/a'), None,
                                         307, '', {}, target)

    def test_login_without_session_stops_after_single_attempt(self):
        page = '<form class="j-login-form" action="index.jsp"><input name="password" type="password"></form>'
        session = self.session([Response(page), Response('MFA test-secret')])
        with self.assertRaises(LoginChallenge) as result:
            session.login('uid', 'test-secret')
        self.assertEqual(result.exception.code, 'AUTH_INTERACTION_REQUIRED')
        self.assertNotIn('test-secret', str(result.exception))
        self.assertEqual(len(self.requests), 2)

    def test_session_can_be_extracted_from_own_x_object_response(self):
        page = '<form class="j-login-form" action="index.jsp"><input name="password" type="password"></form>'
        session = self.session([Response(page), Response(
            "<script>var X = {$: 'main', v: 'build', s: 'own-object-session', r: '/coremail'};</script>")])
        session.login('uid', 'test-secret')
        self.assertEqual(session.sid, 'own-object-session')

    def test_login_follows_observed_javascript_entry_redirect_without_evaluating_code(self):
        page = '<form class="j-login-form" action="index.jsp"><input name="password" type="password"></form>'
        target = 'https://newmail.inspur.com/coremail/XT/index.jsp?sid=own-js-session'
        session = self.session([Response(page), Response(
            "<script>window.location.replace('/coremail/XT/index.jsp?sid=own-js-session');</script>",
            'https://newmail.inspur.com/coremail/index.jsp?cus=1'), Response('authenticated application', target)])
        session.login('uid', 'secret-password')
        self.assertEqual(session.sid, 'own-js-session')
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.requests[2].full_url, target)
        self.assertEqual(self.requests[2].get_method(), 'GET')
        self.assertIsNone(self.requests[2].data)

    def test_javascript_redirect_cannot_send_session_to_another_origin(self):
        page = '<form class="j-login-form" action="index.jsp"><input name="password" type="password"></form>'
        session = self.session([Response(page), Response(
            "<script>location.replace('https://evil.test/coremail/XT/index.jsp?sid=secret');</script>")])
        with self.assertRaises(CoremailError):
            session.login('uid', 'secret-password')
        self.assertEqual(len(self.requests), 2)

    def test_failed_login_diagnostics_record_structure_without_secret_values(self):
        page = '<form class="j-login-form" action="index.jsp"><input name="password" type="password"></form>'
        failure = '''<form class="j-login-form"><input type="password"></form>
        FA_NEED_VERIFY_CODE secret-password
        <script>location.replace('/coremail/XT/index.jsp?sid=secret-session&token=secret-token');</script>
        <meta http-equiv="refresh" content="0;url=/coremail/index.jsp?sid=secret-session">
        '''
        session = self.session([Response(page), Response(failure,
            'https://newmail.inspur.com/coremail/index.jsp?error=secret-password')])
        with self.assertRaises(LoginChallenge):
            session.login('uid@example.test', 'secret-password')
        diagnostic = session.last_login_diagnostics
        self.assertTrue(diagnostic['has_login_form'])
        self.assertTrue(diagnostic['has_password_form'])
        self.assertEqual(diagnostic['codes'], ['FA_NEED_VERIFY_CODE'])
        self.assertEqual(diagnostic['final_path'], '/coremail/index.jsp')
        self.assertEqual(diagnostic['final_query_keys'], ['error'])
        self.assertTrue(diagnostic['redirects'])
        serialized = json.dumps(diagnostic)
        for secret in ['secret-password', 'secret-session', 'secret-token', 'uid@example.test']:
            self.assertNotIn(secret, serialized)
        session.close()
        self.assertEqual(session.last_login_diagnostics, diagnostic)

    def test_rpc_preserves_envelope_and_rejects_non_success(self):
        session = self.session([Response('{"code":"S_OK","var":[1],"extra":true}'),
                                Response('{"code":"SECRET_sid_123","var":"test-secret"}')])
        session.sid = 'own-test-session'
        self.assertEqual(session.call('mbox:getAllFolders', {}),
                         {'code': 'S_OK', 'var': [1], 'extra': True})
        with self.assertRaises(CoremailError) as result:
            session.call('mbox:getAllFolders', {})
        self.assertNotIn('SECRET', str(result.exception))
        self.assertNotIn('test-secret', str(result.exception))
        self.assertEqual(result.exception.code, 'RPC_REJECTED')

    def test_destructive_rpc_never_reaches_network(self):
        session = self.session([])
        session.sid = 'test'
        with self.assertRaises(CoremailError):
            session.call('mbox:deleteMessages', {})
        self.assertFalse(self.requests)

    def test_confirmed_compose_alias_uses_evidenced_jsp_route(self):
        session = self.session([Response('{"code":"S_OK","var":"draft-1"}')])
        session.sid = 'test'
        self.assertEqual(session.call('%mbox/compose', {'action': 'continue'})['var'], 'draft-1')
        url = urlsplit(self.requests[0].full_url)
        self.assertEqual(url.path, '/coremail/common/mbox/compose.jsp')
        self.assertEqual(parse_qs(url.query)['isUserConfirmed'], ['true'])

    def test_extended_date_tokens_leave_message_text_unchanged(self):
        source = '''{"date":!!date '2026-10-15 12:34:56',"text":"literal !!date '2020-01-01 00:00:00' and \\\" quote"}'''
        result = decode_extended_json(source)
        self.assertEqual(result['date'], '2026-10-15 12:34:56')
        self.assertEqual(result['text'], 'literal !!date \'2020-01-01 00:00:00\' and " quote')

    def test_raw_message_rejects_json_error_and_html_login(self):
        session = self.session([Response('{"code":"FA_INVALID_SESSION","var":"secret"}'),
                                Response('<!DOCTYPE html><html>login</html>'),
                                Response(b'From: self@example.test\r\nSubject: test\r\n\r\nbody')])
        session.sid = 'test'
        for unused in range(2):
            with self.assertRaises(CoremailError):
                session.get_message('1')
        self.assertTrue(session.get_message('1').startswith(b'From:'))

    def test_network_exception_is_sanitized(self):
        session = self.session([OSError('secret-password ?sid=secret-session')])
        with self.assertRaises(CoremailError) as result:
            session.login('uid', 'secret-password')
        self.assertNotIn('secret', str(result.exception))
        self.assertIsNone(result.exception.__cause__)


if __name__ == '__main__':
    unittest.main()
