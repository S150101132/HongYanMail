import http.client
import ssl
import unittest
from unittest import mock
from urllib.error import URLError
from urllib.request import Request, build_opener
from coremail_desktop.protocol import keepalive
from coremail_desktop.protocol.keepalive import KeepAliveHTTPSHandler


class FakeResponse:
    def __init__(self, body=b'ok', close=False):
        self.body, self.will_close, self.status, self.reason = body, close, 200, 'OK'
        self.msg = http.client.HTTPMessage(); self._closed = False
    def read(self, amt=None):
        self._closed = True; return self.body
    def isclosed(self): return self._closed


class FakeConnection:
    debuglevel, _http_vsn = 0, 11
    made = []
    plan = []  # Per new connection: a connect error, or a list of request outcomes.
    def __init__(self, host, timeout=None, context=None):
        self.host, self.sock, self.closed, self.sent = host, mock.Mock(), False, []
        self.outcomes = FakeConnection.plan.pop(0) if FakeConnection.plan else []
        FakeConnection.made.append(self)
    def connect(self):
        if isinstance(self.outcomes, Exception): raise self.outcomes
    def request(self, method, selector, data, headers):
        self.sent.append((method, selector, headers.get('Connection')))
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception): raise outcome
    def getresponse(self): return FakeResponse()
    def close(self): self.closed = True


class KeepAliveTests(unittest.TestCase):
    def setUp(self):
        FakeConnection.made, FakeConnection.plan = [], []
        patches = [mock.patch.object(keepalive.http.client, 'HTTPSConnection', FakeConnection),
                   mock.patch.object(keepalive, '_dropped', lambda conn: conn.closed),
                   mock.patch.object(keepalive.time, 'sleep', lambda s: None)]
        for p in patches: p.start(); self.addCleanup(p.stop)

    def opener(self, repeatable=lambda request: True):
        self.handler = KeepAliveHTTPSHandler(repeatable)
        return build_opener(self.handler)

    def test_requests_share_one_connection(self):
        opener = self.opener()
        for _ in range(3):
            with opener.open('https://mail.example.test/a', timeout=5) as response:
                self.assertEqual(response.read(), b'ok')
        self.assertEqual(len(FakeConnection.made), 1)
        self.assertEqual(len(FakeConnection.made[0].sent), 3)
        self.assertEqual(FakeConnection.made[0].sent[0][2], 'keep-alive')

    def test_dropped_idle_connection_is_resent_on_a_fresh_one(self):
        opener = self.opener()
        FakeConnection.plan = [[None, http.client.RemoteDisconnected('gone')], []]
        opener.open('https://mail.example.test/a', timeout=5).read()
        self.assertEqual(opener.open('https://mail.example.test/b', timeout=5).read(), b'ok')
        self.assertEqual(len(FakeConnection.made), 2)
        self.assertTrue(FakeConnection.made[0].closed)
        self.assertEqual(FakeConnection.made[1].sent[0][1], '/b')

    def test_once_only_requests_never_reuse_or_resend(self):
        opener = self.opener(lambda request: '/send' not in request.full_url)
        opener.open('https://mail.example.test/a', timeout=5).read()
        FakeConnection.plan = [[http.client.RemoteDisconnected('gone')]]
        with self.assertRaises(URLError):
            opener.open(Request('https://mail.example.test/send', data=b'x'), timeout=5)
        self.assertEqual(len(FakeConnection.made), 2)  # A fresh connection, tried once.
        self.assertEqual(len(FakeConnection.made[1].sent), 1)
        self.assertEqual(len(FakeConnection.made[0].sent), 1)  # The idle one stayed untouched.

    def test_failed_handshake_is_retried_but_bad_certificate_is_not(self):
        opener = self.opener()
        FakeConnection.plan = [ssl.SSLError('handshake'), ConnectionResetError(), []]
        self.assertEqual(opener.open('https://mail.example.test/a', timeout=5).read(), b'ok')
        self.assertEqual(len(FakeConnection.made), 3)
        FakeConnection.made, FakeConnection.plan = [], [ssl.SSLCertVerificationError('bad')]
        self.handler.close_all()
        with self.assertRaises(URLError) as raised:
            opener.open('https://mail.example.test/a', timeout=5)
        self.assertIsInstance(raised.exception.reason, ssl.SSLCertVerificationError)
        self.assertEqual(len(FakeConnection.made), 1)

    def test_close_all_drops_idle_connections(self):
        opener = self.opener()
        opener.open('https://mail.example.test/a', timeout=5).read()
        self.handler.close_all()
        self.assertTrue(FakeConnection.made[0].closed)
        opener.open('https://mail.example.test/a', timeout=5).read()
        self.assertEqual(len(FakeConnection.made), 2)


if __name__ == '__main__':
    unittest.main()
