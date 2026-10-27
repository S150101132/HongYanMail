"""Step-by-step network check when the server cannot be reached: which link is broken.

Steps run in order and stop at the first failure; each result is a fixed step id and a
fixed outcome code, never exception or server text. Read-only: one DNS lookup, TCP
connects, one TLS handshake and one GET of the sign-in page (no cookies, no sid)."""
import socket
import ssl
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, getproxies, ProxyHandler

# A public name to tell "no internet at all" from "only the mail server is unreachable".
PUBLIC_HOST = ('www.baidu.com', 443)
STEPS = ('local', 'proxy', 'dns', 'tcp', 'tls', 'http')


def _ms(start):
    return int((time.monotonic() - start) * 1000)


class Probe:
    """The network primitives, replaceable in tests."""
    def __init__(self, timeout=6):
        self.timeout = timeout
    def local_address(self):
        # A UDP "connect" picks the outgoing interface and sends nothing.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('8.8.8.8', 53))
            return sock.getsockname()[0]
    def proxy(self, scheme):
        value = getproxies().get(scheme)
        if not value: return None
        parts = urlsplit(value if '://' in value else 'http://' + value)
        return (parts.hostname, parts.port or 80) if parts.hostname else None
    def resolve(self, host, port):
        return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    def connect(self, host, port):
        socket.create_connection((host, port), timeout=self.timeout).close()
    def handshake(self, host, port):
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=self.timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host):
                pass
    def get(self, url):
        # The default opener honours the system proxy, as the mail session does.
        with build_opener(ProxyHandler()).open(Request(url, headers={'Accept': 'text/html'}), timeout=self.timeout * 2) as response:
            return response.status


def _public_reachable(probe):
    try:
        probe.connect(*PUBLIC_HOST)
        return True
    except OSError:
        return False


def check(server_url, probe=None):
    """{'steps': [{'step', 'ok', 'code', 'ms'}], 'failed': step id or None, 'internet': bool or None}."""
    probe = probe or Probe()
    parts = urlsplit(server_url)
    host, port = parts.hostname or '', parts.port or (443 if parts.scheme == 'https' else 80)
    steps, internet = [], None

    def done(failed=None):
        return {'steps': steps, 'failed': failed, 'internet': internet}

    start = time.monotonic()
    try:
        probe.local_address()
        steps.append({'step': 'local', 'ok': True, 'code': 'OK', 'ms': _ms(start)})
    except OSError:
        steps.append({'step': 'local', 'ok': False, 'code': 'NO_NETWORK', 'ms': _ms(start)})
        return done('local')

    proxy = probe.proxy(parts.scheme or 'https')
    if proxy:
        start = time.monotonic()
        try:
            probe.connect(*proxy)
            steps.append({'step': 'proxy', 'ok': True, 'code': 'OK', 'ms': _ms(start)})
        except OSError:
            steps.append({'step': 'proxy', 'ok': False, 'code': 'PROXY_UNREACHABLE', 'ms': _ms(start)})
            return done('proxy')
    else:
        start = time.monotonic()
        try:
            probe.resolve(host, port)
            steps.append({'step': 'dns', 'ok': True, 'code': 'OK', 'ms': _ms(start)})
        except (OSError, UnicodeError):
            steps.append({'step': 'dns', 'ok': False, 'code': 'DNS_FAILED', 'ms': _ms(start)})
            internet = _public_reachable(probe)
            return done('dns')
        start = time.monotonic()
        try:
            probe.connect(host, port)
            steps.append({'step': 'tcp', 'ok': True, 'code': 'OK', 'ms': _ms(start)})
        except socket.timeout:
            steps.append({'step': 'tcp', 'ok': False, 'code': 'TIMEOUT', 'ms': _ms(start)})
            internet = _public_reachable(probe)
            return done('tcp')
        except OSError:
            steps.append({'step': 'tcp', 'ok': False, 'code': 'REFUSED', 'ms': _ms(start)})
            internet = _public_reachable(probe)
            return done('tcp')
        if parts.scheme == 'https':
            start = time.monotonic()
            try:
                probe.handshake(host, port)
                steps.append({'step': 'tls', 'ok': True, 'code': 'OK', 'ms': _ms(start)})
            except ssl.SSLCertVerificationError:
                steps.append({'step': 'tls', 'ok': False, 'code': 'CERT_INVALID', 'ms': _ms(start)})
                return done('tls')
            except (OSError, ssl.SSLError):
                steps.append({'step': 'tls', 'ok': False, 'code': 'TLS_FAILED', 'ms': _ms(start)})
                return done('tls')

    start = time.monotonic()
    try:
        status = probe.get(server_url)
    except Exception as error:  # urllib raises HTTPError for 4xx/5xx; anything else is the link.
        status = getattr(error, 'code', None)
        if not isinstance(status, int):
            steps.append({'step': 'http', 'ok': False, 'code': 'TIMEOUT' if isinstance(getattr(error, 'reason', error), socket.timeout) else 'NO_RESPONSE', 'ms': _ms(start)})
            return done('http')
    ok = status < 500
    steps.append({'step': 'http', 'ok': ok, 'code': 'OK' if ok else 'SERVER_ERROR', 'ms': _ms(start)})
    return done(None if ok else 'http')
