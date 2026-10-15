"""Integration tests that drive the production adapter against a local HTTPS origin.

Requires HTTPS is not an obstacle to test: the transport refuses plain HTTP by
design, so this spins up a local TLS server with a test-generated certificate and
injects a matching CA into the session's opener. That exercises the real network
path: login form parsing, same-origin redirect handling, cookies, ExtendedJSON
decoding, RPC error classification, bounded read retries, MIME retrieval and the
response size limit.

Synthetic data only. This is NOT evidence of compatibility with the real
deployment: the corporate origin, its TLS chain, device checks, the compose
contract and original-client interop remain unverified.
"""
import datetime
import json
import ssl
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPSHandler, HTTPCookieProcessor, Request, build_opener

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from coremail_desktop.errors import AppError
from coremail_desktop.models import AccountConfig, DraftDocument
from coremail_desktop.protocol.coremail import CoremailAdapter
from probe.coremail_transport import SameOriginRedirectHandler

SID = 'local-test-session'
RAW_MESSAGE = (
    'From: sender@example.test\r\n'
    'To: me@example.test\r\n'
    'Subject: =?utf-8?B?5pys5Zyw5rWL6K+V?=\r\n'
    'Message-ID: <local-1@example.test>\r\n'
    'Date: Thu, 15 Oct 2026 12:00:00 +0300\r\n'
    'Content-Type: text/plain; charset=utf-8\r\n'
    '\r\n'
    'LOCAL-ONLY-TEST-BODY\r\n'
).encode('utf-8')

LOGIN_FORM = (
    '<html><body><form class="j-login-form" method="post" action="index.jsp">'
    '<input type="hidden" name="locale" value="zh_CN">'
    '<input type="text" name="uid">'
    '<input type="password" name="password" id="password">'
    '</form></body></html>'
)
LOGIN_OK = ("<html><head><script>var X = {$: 'main', v: 'local', s: '" + SID
            + "', r: '/coremail'};</script></head><body>ok</body></html>")


def generate_test_certificate(directory: Path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, '127.0.0.1')])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(
            __import__('ipaddress').ip_address('127.0.0.1'))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    certificate_path = directory / 'server.pem'
    key_path = directory / 'server.key'
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()))
    return certificate_path, key_path


def rpc(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode('utf-8')


FOLDERS = rpc({'code': 'S_OK', 'var': [
    {'id': '0', 'name': '收件箱', 'role': 'inbox', 'stats': {'total': 1}},
    {'id': '3', 'name': '已发送', 'role': 'sent', 'stats': {'total': 0}},
]})
MESSAGES = rpc({'code': 'S_OK', 'var': [
    {'id': '101', 'subject': '本地主题', 'from': 'sender@example.test',
     'date': '2026-10-15 12:00:00', 'flags': {}},
]})


class Handler(BaseHTTPRequestHandler):
    calls = {}
    modes = {}
    oversized = False

    def log_message(self, *args):
        pass

    def _count(self):
        path = urlsplit(self.path).path
        Handler.calls[path] = Handler.calls.get(path, 0) + 1
        return path

    def _send(self, body: bytes, status=200, content_type='text/html; charset=utf-8'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self):
        """RPC responses are reached by both GET and POST from the transport."""
        mode = Handler.modes.get('json')
        if mode == 'unauthorized':
            return self._send(rpc({'code': 'FA_UNAUTHORIZED'}), content_type='text/x-json')
        if mode == 'transient' and Handler.calls.get('/coremail/s/json', 0) <= 2:
            return self._send(b'server exploded', status=500)
        if Handler.oversized:
            return self._send(b'x' * (64 * 1024 * 1024 + 10), content_type='text/x-json')
        function = parse_qs(urlsplit(self.path).query).get('func', [''])[0]
        if function == 'mbox:getAllFolders':
            return self._send(FOLDERS, content_type='text/x-json')
        if function == 'mbox:listMessages':
            return self._send(MESSAGES, content_type='text/x-json')
        if function == 'mbox:getMessageData':
            return self._send(RAW_MESSAGE, content_type='text/plain; charset=utf-8')
        return self._send(rpc({'code': 'S_OK', 'var': None}), content_type='text/x-json')

    def do_GET(self):
        path = self._count()
        if path.endswith('/coremail/'):
            return self._send(LOGIN_FORM.encode('utf-8'))
        if path.endswith('/s/json'):
            return self._serve_json()
        return self._send(b'not found', status=404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length).decode('utf-8', errors='replace')
        path = self._count()
        if path.endswith('/coremail/index.jsp'):
            Handler.last_form = parse_qs(body, keep_blank_values=True)
            return self._send(LOGIN_OK.encode('utf-8'))
        # The transport posts RPC payloads to the s/json endpoint.
        if path.endswith('/s/json'):
            return self._serve_json()
        if path.endswith('/common/mbox/compose.jsp'):
            mode = Handler.modes.get('compose')
            if mode == 'transient':
                return self._send(b'compose exploded', status=500)
            payload = json.loads(body) if body.lstrip().startswith('{') else {}
            if payload.get('action') == 'continue':
                return self._send(rpc({'code': 'S_OK', 'var': {'id': 'compose-1'}}),
                                  content_type='text/x-json')
            return self._send(rpc({'code': 'S_OK', 'var': {'delivered': True}}),
                              content_type='text/x-json')
        return self._send(b'not found', status=404)


class AdapterIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        certificate, key = generate_test_certificate(Path(cls.temporary.name))
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(certificate), keyfile=str(key))
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f'https://127.0.0.1:{cls.server.server_port}/coremail/'
        cls.account = AccountConfig('primary', cls.origin, 'tester@example.test')
        cls.trusted_context = ssl.create_default_context(cafile=str(certificate))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.temporary.cleanup()

    def setUp(self):
        Handler.calls = {}
        Handler.modes = {}
        Handler.oversized = False
        Handler.last_form = {}
        self.adapter = self.build_adapter()

    def tearDown(self):
        self.adapter.close()

    def build_adapter(self):
        """Trust the test CA only; the shipped default still uses the system store."""
        adapter = CoremailAdapter()
        original_factory = adapter._session_factory

        def factory(origin):
            session = original_factory(origin)
            session._opener = build_opener(
                SameOriginRedirectHandler(origin),
                HTTPCookieProcessor(session._cookies),
                HTTPSHandler(context=self.trusted_context))
            return session

        adapter._session_factory = factory
        return adapter

    def login(self):
        self.adapter.login(self.account, 'local-test-password')

    def test_login_uses_the_public_form_then_reaches_an_authenticated_api(self):
        self.login()
        form = Handler.last_form
        self.assertEqual(form['uid'], ['tester@example.test'])
        self.assertEqual(form['password'], ['local-test-password'])
        self.assertEqual(form['locale'], ['zh_CN'])
        self.assertIn('/coremail/s/json', Handler.calls, 'login did not call an authenticated API')
        self.assertEqual(self.adapter.get_account_identities(), set(),
                         'identities must stay empty until the attrs schema is verified')

    def test_folders_and_paged_messages_map_to_typed_models(self):
        self.login()
        folders = self.adapter.list_folders()
        self.assertEqual([folder.role for folder in folders], ['inbox', 'sent'])
        self.assertEqual(folders[0].name, '收件箱')
        messages = self.adapter.list_messages('0', 0, 50)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].remote_id, '101')
        self.assertEqual(messages[0].folder_id, '0')

    def test_fetch_mime_returns_the_raw_rfc5322_message(self):
        self.login()
        raw = self.adapter.fetch_mime('101')
        self.assertIn(b'LOCAL-ONLY-TEST-BODY', raw)
        self.assertIn(b'Message-ID: <local-1@example.test>', raw)

    def test_compose_continue_then_deliver_and_reply_is_refused(self):
        self.login()
        draft = DraftDocument('d1', 1, 'new', 'tester@example.test', ('you@example.test',),
                              (), (), '本地主题', '本地正文', {})
        compose_id = self.adapter.create_compose(draft)
        self.assertEqual(compose_id, 'compose-1')
        self.assertEqual(self.adapter.deliver(compose_id, draft)['code'], 'S_OK')

        reply = DraftDocument('d2', 1, 'reply', 'tester@example.test', ('you@example.test',),
                              (), (), 'Re: 本地主题', '本地正文',
                              {'in-reply-to': '<local-1@example.test>'})
        with self.assertRaises(AppError) as raised:
            self.adapter.create_compose(reply)
        self.assertEqual(raised.exception.code, 'REPLY_CONTRACT_UNVERIFIED')

        encrypted = DraftDocument('d3', 1, 'new', 'tester@example.test', ('you@example.test',),
                                  (), (), '加密', '正文', {}, True)
        with self.assertRaises(AppError) as raised:
            self.adapter.create_compose(encrypted)
        self.assertEqual(raised.exception.code, 'ENCRYPTED_SUBMISSION_UNVERIFIED')

    def test_expired_session_is_classified_and_not_retried(self):
        self.login()
        Handler.modes['json'] = 'unauthorized'
        before = Handler.calls.get('/coremail/s/json', 0)
        with self.assertRaises(AppError) as raised:
            self.adapter.list_folders()
        self.assertEqual(raised.exception.code, 'SESSION_EXPIRED')
        self.assertEqual(Handler.calls.get('/coremail/s/json', 0) - before, 1,
                         'an auth rejection must not be retried')

    def test_bounded_retry_recovers_a_transient_read(self):
        Handler.modes['json'] = 'transient'
        self.login()
        Handler.modes['json'] = 'transient'
        folders = self.adapter.list_folders()
        self.assertEqual(len(folders), 2, 'read request did not recover within its retry budget')

    def test_deliver_is_never_retried(self):
        self.login()
        draft = DraftDocument('d1', 1, 'new', 'tester@example.test', ('you@example.test',),
                              (), (), '主题', '正文', {})
        Handler.modes['compose'] = 'transient'
        before = Handler.calls.get('/coremail/common/mbox/compose.jsp', 0)
        with self.assertRaises(AppError):
            self.adapter.create_compose(draft)
        self.assertEqual(Handler.calls.get('/coremail/common/mbox/compose.jsp', 0) - before, 1,
                         'a write must be attempted exactly once')

    def test_oversized_response_is_rejected(self):
        self.login()
        Handler.oversized = True
        with self.assertRaises(AppError) as raised:
            self.adapter.list_folders()
        self.assertEqual(raised.exception.code, 'RESPONSE_TOO_LARGE')

    def test_cross_origin_request_is_refused_before_it_is_sent(self):
        self.login()
        session = self.adapter._session
        with self.assertRaises(AppError) as raised:
            session._fetch(Request('https://evil.test/coremail/s/json'))
        self.assertEqual(raised.exception.code, 'UNSAFE_ORIGIN')

    def test_untrusted_certificate_is_reported_as_tls_failure(self):
        adapter = CoremailAdapter()
        with self.assertRaises(AppError) as raised:
            adapter.login(self.account, 'local-test-password')
        self.assertEqual(raised.exception.code, 'TLS_ERROR',
                         'a certificate the system store does not trust must stop login')


if __name__ == '__main__':
    unittest.main()
