import socket
import ssl
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError

from coremail_desktop.protocol import netcheck
from coremail_desktop.app.demo import DemoNetProbe

URL = 'https://newmail.example.test/coremail/'


class FakeProbe:
    """Each primitive succeeds unless a failure is given for it."""
    def __init__(self, fail=None, proxy=None, public=True, status=200):
        self.fail, self._proxy, self.public, self.status, self.calls = fail or {}, proxy, public, status, []
    def _maybe(self, name):
        self.calls.append(name)
        if name in self.fail: raise self.fail[name]
    def local_address(self): self._maybe('local')
    def proxy(self, scheme): return self._proxy
    def resolve(self, host, port): self._maybe('dns')
    def connect(self, host, port):
        if (host, port) == netcheck.PUBLIC_HOST:
            if not self.public: raise OSError
            return
        self._maybe('tcp' if self._proxy is None else 'proxy')
    def handshake(self, host, port): self._maybe('tls')
    def get(self, url):
        self._maybe('http')
        if self.status >= 400: raise HTTPError(url, self.status, 'x', {}, None)
        return self.status


class NetCheckTest(unittest.TestCase):
    def failed(self, probe):
        result = netcheck.check(URL, probe)
        self.assertTrue(all(s['step'] in netcheck.STEPS and isinstance(s['ms'], int) for s in result['steps']))
        return result['failed'], result['steps'][-1]['code'], result['internet']

    def test_all_steps_pass(self):
        result = netcheck.check(URL, FakeProbe())
        self.assertIsNone(result['failed'])
        self.assertEqual([s['step'] for s in result['steps']], ['local', 'dns', 'tcp', 'tls', 'http'])

    def test_each_link_is_named(self):
        self.assertEqual(self.failed(FakeProbe({'local': OSError()})), ('local', 'NO_NETWORK', None))
        self.assertEqual(self.failed(FakeProbe({'dns': socket.gaierror()})), ('dns', 'DNS_FAILED', True))
        self.assertEqual(self.failed(FakeProbe({'dns': socket.gaierror()}, public=False)), ('dns', 'DNS_FAILED', False))
        self.assertEqual(self.failed(FakeProbe({'tcp': socket.timeout()})), ('tcp', 'TIMEOUT', True))
        self.assertEqual(self.failed(FakeProbe({'tcp': ConnectionRefusedError()})), ('tcp', 'REFUSED', True))
        self.assertEqual(self.failed(FakeProbe({'tls': ssl.SSLCertVerificationError()})), ('tls', 'CERT_INVALID', None))
        self.assertEqual(self.failed(FakeProbe({'tls': ssl.SSLError()})), ('tls', 'TLS_FAILED', None))
        self.assertEqual(self.failed(FakeProbe({'http': OSError()})), ('http', 'NO_RESPONSE', None))
        self.assertEqual(self.failed(FakeProbe(status=502)), ('http', 'SERVER_ERROR', None))

    def test_client_error_status_still_means_reachable(self):
        self.assertIsNone(netcheck.check(URL, FakeProbe(status=404))['failed'])

    def test_proxy_replaces_direct_steps(self):
        probe = FakeProbe(proxy=('proxy.example.test', 8080))
        self.assertEqual([s['step'] for s in netcheck.check(URL, probe)['steps']], ['local', 'proxy', 'http'])
        self.assertEqual(self.failed(FakeProbe({'proxy': OSError()}, proxy=('p', 1))), ('proxy', 'PROXY_UNREACHABLE', None))

    def test_no_exception_text_in_result(self):
        result = netcheck.check(URL, FakeProbe({'tcp': OSError('secret server detail')}))
        self.assertNotIn('secret', repr(result))

    def test_demo_probe_follows_offline_adapter(self):
        backend = SimpleNamespace(adapter=SimpleNamespace(offline=False))
        self.assertIsNone(netcheck.check(URL, DemoNetProbe(backend))['failed'])
        backend.adapter.offline = True
        result = netcheck.check(URL, DemoNetProbe(backend))
        self.assertEqual((result['failed'], result['internet']), ('tcp', True))


if __name__ == '__main__':
    unittest.main()
