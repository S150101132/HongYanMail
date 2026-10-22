"""Persistent HTTPS connections for urllib.

urllib opens a new TLS connection for every request (about 0.4 s to the mail server,
and the handshake through its CDN now and then fails outright). This handler keeps
idle connections per host and reuses them; a connection the server has closed is
noticed before reuse. Failures that happen before a request is sent - setting up the
connection, including the TLS handshake - are retried, since nothing reached the
server. A request that fails on a reused connection (the server dropped it while idle)
is sent once more on a fresh one, unless it may not be repeated (sending mail)."""
import http.client
import io
import select
import ssl
import threading
import time
from urllib.error import URLError
from urllib.request import HTTPSHandler
from urllib.response import addinfourl

IDLE_SECONDS = 50   # Keep under the server's own keep-alive timeout.
POOL_SIZE = 4       # Idle connections kept per host (one per worker thread is plenty).
CONNECT_TRIES = 3
MAX_BODY = 64 * 1024 * 1024 + 1  # Callers refuse anything larger; one byte over shows it.
_STALE = (http.client.RemoteDisconnected, http.client.BadStatusLine, ConnectionResetError,
          ConnectionAbortedError, BrokenPipeError)


def _dropped(conn):
    """The server closed this idle connection (or sent something unasked)."""
    sock = conn.sock
    if sock is None:
        return True
    try:
        return bool(select.select([sock], [], [], 0)[0])
    except (OSError, ValueError):
        return True


class KeepAliveHTTPSHandler(HTTPSHandler):
    def __init__(self, repeatable=lambda request: True):
        super().__init__()
        self._repeatable = repeatable  # False: always a fresh connection, never a resend.
        self._tls = ssl.create_default_context()
        self._tls.set_alpn_protocols(['http/1.1'])
        self._idle = {}
        self._lock = threading.Lock()

    def https_open(self, req):
        if req._tunnel_host:  # Behind a proxy: urllib's own path handles the tunnel.
            return super().https_open(req)
        headers = dict(req.unredirected_hdrs)
        headers.update({k: v for k, v in req.headers.items() if k not in headers})
        headers['Connection'] = 'keep-alive'
        headers = {name.title(): value for name, value in headers.items()}
        reuse = self._repeatable(req)
        while True:
            conn = self._take(req.host) if reuse else None
            reused = conn is not None
            if reused:
                conn.sock.settimeout(req.timeout)
            else:
                conn = self._connect(req.host, req.timeout)
            try:
                conn.request(req.get_method(), req.selector, req.data, headers)
                response = conn.getresponse()
                body = response.read(MAX_BODY)
            except _STALE as error:
                conn.close()
                if reused:
                    continue  # Dropped while idle: the server never saw this request.
                raise URLError(error) from None
            except (OSError, http.client.HTTPException) as error:
                conn.close()
                raise URLError(error) from None
            if response.will_close or not response.isclosed():
                conn.close()  # Server ends it, or the body was over the cap.
            else:
                self._give(req.host, conn)
            result = addinfourl(io.BytesIO(body), response.msg, req.get_full_url(), response.status)
            result.msg = response.reason
            return result

    def _connect(self, host, timeout):
        for attempt in range(CONNECT_TRIES):
            conn = http.client.HTTPSConnection(host, timeout=timeout, context=self._tls)
            try:
                conn.connect()
                return conn
            except OSError as error:  # Refused, reset or a failed handshake: nothing was sent.
                conn.close()
                if attempt == CONNECT_TRIES - 1 or isinstance(error, ssl.SSLCertVerificationError):
                    raise URLError(error) from None
                time.sleep(0.3 * (attempt + 1))

    def _take(self, host):
        with self._lock:
            pool = self._idle.get(host, [])
            while pool:
                conn, since = pool.pop()
                if time.monotonic() - since < IDLE_SECONDS and not _dropped(conn):
                    return conn
                conn.close()
        return None

    def _give(self, host, conn):
        with self._lock:
            pool = self._idle.setdefault(host, [])
            pool.append((conn, time.monotonic()))
            while len(pool) > POOL_SIZE:
                pool.pop(0)[0].close()

    def close_all(self):
        with self._lock:
            for pool in self._idle.values():
                for conn, _since in pool:
                    conn.close()
            self._idle.clear()
