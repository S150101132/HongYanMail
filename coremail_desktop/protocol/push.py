"""Coremail real-time push: Socket.IO v2 over Engine.IO v3 over WebSocket.

Shape taken from the official client's logs and binary strings (see
analysis/push-shape-*.json):

* handshake ``GET /socket.io/1/?t=<ms>&EIO=3&transport=polling`` on the mail
  origin, authenticated by the ``Coremail.sid`` cookie;
* upgrade to ``wss://<host>/socket.io/1/websocket/?EIO=3&transport=websocket&sid=<eio sid>``
  with the ``2probe``/``3probe``/``5`` exchange;
* the server pushes Socket.IO events (``42[...]``) when mail arrives, changes
  or is deleted; the client pings (``2``) every ``pingInterval``.

Stdlib only (ssl + socket): the WebSocket part is the small client subset of
RFC 6455 that this channel needs. Everything runs on one background thread and
never talks to any origin other than the mail server's.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

SOCKET_PATH = '/socket.io/1/'
MAX_MESSAGE = 1024 * 1024
WS_GUID = b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'


class PushError(Exception):
    """A fixed failure code; never carries server text."""
    CODES = {'PROTOCOL', 'HANDSHAKE_REJECTED', 'AUTH_REJECTED', 'UNSAFE_ORIGIN', 'NETWORK', 'TIMEOUT', 'CLOSED', 'SESSION_CHANGED'}

    def __init__(self, code):
        self.code = code if code in self.CODES else 'PROTOCOL'
        super().__init__(self.code)


# WebSocket frames -------------------------------------------------------------
OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


def encode_frame(opcode, payload=b'', mask=None):
    """A single final client frame; clients must mask (RFC 6455 5.3)."""
    if isinstance(payload, str):
        payload = payload.encode('utf-8')
    mask = os.urandom(4) if mask is None else mask
    head = bytearray([0x80 | opcode])
    size = len(payload)
    if size < 126:
        head.append(0x80 | size)
    elif size < 1 << 16:
        head.append(0x80 | 126)
        head += size.to_bytes(2, 'big')
    else:
        head.append(0x80 | 127)
        head += size.to_bytes(8, 'big')
    return bytes(head) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


class FrameReader:
    """Incremental frame parser; ``feed`` returns complete messages.

    Data frames are reassembled across continuation frames; control frames are
    returned as they arrive (they may interleave with a fragmented message)."""

    def __init__(self, max_message=MAX_MESSAGE):
        self._buffer = bytearray()
        self._parts = None
        self._opcode = None
        self.max_message = max_message

    def feed(self, data):
        self._buffer += data
        messages = []
        while True:
            frame = self._next_frame()
            if frame is None:
                return messages
            fin, opcode, payload = frame
            if opcode >= 0x8:
                if not fin or len(payload) > 125:
                    raise PushError('PROTOCOL')
                messages.append((opcode, payload))
            elif opcode == OP_CONT:
                if self._parts is None:
                    raise PushError('PROTOCOL')
                self._parts.append(payload)
                self._check_size()
                if fin:
                    messages.append((self._opcode, b''.join(self._parts)))
                    self._parts = self._opcode = None
            elif opcode in (OP_TEXT, OP_BINARY):
                if self._parts is not None:
                    raise PushError('PROTOCOL')
                if fin:
                    messages.append((opcode, payload))
                else:
                    self._parts, self._opcode = [payload], opcode
                    self._check_size()
            else:
                raise PushError('PROTOCOL')

    def _check_size(self):
        if sum(len(p) for p in self._parts) > self.max_message:
            raise PushError('PROTOCOL')

    def _next_frame(self):
        buf = self._buffer
        if len(buf) < 2:
            return None
        first, second = buf[0], buf[1]
        if first & 0x70:
            raise PushError('PROTOCOL')  # No extensions were negotiated.
        size, offset = second & 0x7F, 2
        if size == 126:
            if len(buf) < 4:
                return None
            size, offset = int.from_bytes(buf[2:4], 'big'), 4
        elif size == 127:
            if len(buf) < 10:
                return None
            size, offset = int.from_bytes(buf[2:10], 'big'), 10
        if size > self.max_message:
            raise PushError('PROTOCOL')
        mask = None
        if second & 0x80:
            if len(buf) < offset + 4:
                return None
            mask, offset = bytes(buf[offset:offset + 4]), offset + 4
        if len(buf) < offset + size:
            return None
        payload = bytes(buf[offset:offset + size])
        del buf[:offset + size]
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return bool(first & 0x80), first & 0x0F, payload


# Engine.IO v3 / Socket.IO v2 packets ------------------------------------------
EIO_OPEN, EIO_CLOSE, EIO_PING, EIO_PONG, EIO_MESSAGE, EIO_UPGRADE, EIO_NOOP = range(7)
SIO_CONNECT, SIO_DISCONNECT, SIO_EVENT, SIO_ACK, SIO_ERROR, SIO_BINARY_EVENT, SIO_BINARY_ACK = range(7)


def parse_eio(text):
    """``'4...'`` -> ``(4, '...')``."""
    if not isinstance(text, str) or not text or text[0] not in '0123456':
        raise PushError('PROTOCOL')
    return int(text[0]), text[1:]


def decode_polling_payload(raw):
    """Split an Engine.IO v3 polling body into packets.

    Text form: ``<len>:<packet>`` repeated (len counts characters). Binary
    (XHR2) form: ``\\x00<digits>\\xff<packet>`` (``\\x01`` for binary packets)."""
    packets = []
    if raw[:1] in (b'\x00', b'\x01'):
        index = 0
        while index < len(raw):
            kind = raw[index]
            end = raw.index(b'\xff', index + 1)
            digits = raw[index + 1:end]
            if kind not in (0, 1) or not digits or any(d > 9 for d in digits):
                raise PushError('PROTOCOL')
            size = int(''.join(str(d) for d in digits))
            body = raw[end + 1:end + 1 + size]
            if len(body) != size:
                raise PushError('PROTOCOL')
            if kind == 0:
                packets.append(body.decode('utf-8'))
            index = end + 1 + size
        return packets
    text = raw.decode('utf-8')
    index = 0
    while index < len(text):
        colon = text.find(':', index)
        if colon <= index or not text[index:colon].isdigit():
            raise PushError('PROTOCOL')
        size = int(text[index:colon])
        packet = text[colon + 1:colon + 1 + size]
        if len(packet) != size:
            raise PushError('PROTOCOL')
        packets.append(packet)
        index = colon + 1 + size
    return packets


@dataclass
class Handshake:
    sid: str
    ping_interval: float
    ping_timeout: float
    upgrades: list = field(default_factory=list)


def parse_open(data):
    try:
        value = json.loads(data)
    except ValueError:
        raise PushError('PROTOCOL') from None
    if not isinstance(value, dict) or not isinstance(value.get('sid'), str) or not value['sid'] or len(value['sid']) > 200:
        raise PushError('PROTOCOL')
    if any(c in value['sid'] for c in '&?#/ \r\n'):
        raise PushError('PROTOCOL')
    upgrades = value.get('upgrades')
    if not isinstance(upgrades, list) or 'websocket' not in upgrades:
        raise PushError('PROTOCOL')
    interval = value.get('pingInterval', 25000)
    timeout = value.get('pingTimeout', 60000)
    if not all(isinstance(v, (int, float)) and 1000 <= v <= 600000 for v in (interval, timeout)):
        raise PushError('PROTOCOL')
    return Handshake(value['sid'], interval / 1000, timeout / 1000, upgrades)


@dataclass
class SioPacket:
    type: int
    nsp: str = '/'
    ack_id: int | None = None
    data: object = None

    @property
    def event(self):
        if self.type in (SIO_EVENT, SIO_BINARY_EVENT) and isinstance(self.data, list) and self.data and isinstance(self.data[0], str):
            return self.data[0]
        return None

    @property
    def args(self):
        return self.data[1:] if self.event is not None else []


def parse_sio(text):
    """Socket.IO v2: ``<type>[<attachments>-][<nsp>,][<ack id>][<json>]``."""
    if not text or text[0] not in '0123456':
        raise PushError('PROTOCOL')
    kind, index = int(text[0]), 1
    if kind in (SIO_BINARY_EVENT, SIO_BINARY_ACK):
        dash = text.find('-', index)
        if dash < 0 or not text[index:dash].isdigit():
            raise PushError('PROTOCOL')
        index = dash + 1
    nsp = '/'
    if index < len(text) and text[index] == '/':
        comma = text.find(',', index)
        end = comma if comma >= 0 else len(text)
        nsp, index = text[index:end], (end + 1 if comma >= 0 else end)
    start = index
    while index < len(text) and text[index].isdigit():
        index += 1
    ack_id = int(text[start:index]) if index > start else None
    data = None
    if index < len(text):
        try:
            data = json.loads(text[index:])
        except ValueError:
            raise PushError('PROTOCOL') from None
    return SioPacket(kind, nsp, ack_id, data)


# Push settings and subscription -------------------------------------------------
# The webmail page carries the push settings in its X.U config; the webmail
# script authenticates with emit('auth', {clientId, sid, username}), waits for
# the server's 'ready', then emit('subscribe', {topic}) per topic, and receives
# emit('message', {topic, payload: '<json>'}).
CONFIG_KEYS = ('enablePushNotify', 'onlineHost', 'onlineTryCount', 'orgid', 'dnemail',
               'onlineInMailPath', 'onlineModMailPath', 'onlineDelMailPath')
TOPIC_KEYS = ('onlineInMailPath', 'onlineModMailPath', 'onlineDelMailPath')
_CONFIG_VALUE = r'''["']?%s["']?\s*[:=]\s*("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|true|false|-?\d+)'''


def parse_push_config(page):
    """Pick the push settings out of the webmail page; nothing else is kept."""
    import re
    config = {}
    for key in CONFIG_KEYS:
        match = re.search(_CONFIG_VALUE % re.escape(key), page)
        if not match:
            continue
        raw = match.group(1)
        if raw in ('true', 'false'):
            config[key] = raw == 'true'
        elif raw[0] in '"\'':
            config[key] = re.sub(r'\\(.)', r'\1', raw[1:-1])
        else:
            config[key] = int(raw)
    return config


def read_push_config(session):
    """GET the webmail landing page with the session (same-origin guarded) and parse it."""
    from urllib.parse import urlencode as _enc, urljoin
    from urllib.request import Request
    raw, _ = session._fetch(Request(urljoin(session.base_url, 'XT/index.jsp') + '?' + _enc({'sid': session.sid})))
    return parse_push_config(raw[:4 * 1024 * 1024].decode('utf-8', errors='replace'))


def topics(config):
    org, uid = str(config.get('orgid', '')), str(config.get('dnemail', ''))
    result = []
    for key in TOPIC_KEYS:
        path = config.get(key)
        if isinstance(path, str) and path:
            if ('$org_id$' in path and not org) or ('$uid$' in path and not uid):
                continue
            result.append(path.replace('$org_id$', org).replace('$uid$', uid))
    return result


def auth_payload(sid, username):
    return {'clientId': 'webmail:' + sid, 'sid': sid, 'username': username}


# Reconnect policy -------------------------------------------------------------
class Backoff:
    """Exponential backoff with full jitter on top of a floor; reset on success."""

    def __init__(self, base=2.0, cap=300.0, factor=2.0, rand=random.random):
        self.base, self.cap, self.factor, self._rand = base, cap, factor, rand
        self.attempt = 0

    def next(self):
        ceiling = min(self.cap, self.base * self.factor ** self.attempt)
        self.attempt += 1
        return ceiling / 2 + ceiling / 2 * self._rand()

    def reset(self):
        self.attempt = 0


# Transport --------------------------------------------------------------------
class WebSocket:
    """Blocking client WebSocket over TLS, text messages only."""

    def __init__(self, sock):
        self._sock = sock
        self._reader = FrameReader()
        self._pending = []
        self._lock = threading.Lock()

    @classmethod
    def connect(cls, host, port, path, headers, timeout=15.0, context=None):
        context = context or ssl.create_default_context()
        raw = socket.create_connection((host, port), timeout=timeout)
        try:
            sock = context.wrap_socket(raw, server_hostname=host)
            key = base64.b64encode(os.urandom(16)).decode('ascii')
            lines = [f'GET {path} HTTP/1.1', f'Host: {host}', 'Upgrade: websocket', 'Connection: Upgrade',
                     f'Sec-WebSocket-Key: {key}', 'Sec-WebSocket-Version: 13']
            lines += [f'{k}: {v}' for k, v in headers.items() if '\r' not in v and '\n' not in v]
            sock.sendall(('\r\n'.join(lines) + '\r\n\r\n').encode('latin-1'))
            response = b''
            while b'\r\n\r\n' not in response:
                chunk = sock.recv(4096)
                if not chunk or len(response) > 16384:
                    raise PushError('HANDSHAKE_REJECTED')
                response += chunk
            head, rest = response.split(b'\r\n\r\n', 1)
            status, *header_lines = head.decode('latin-1').split('\r\n')
            parts = status.split(' ')
            if len(parts) < 2 or parts[1] != '101':
                raise PushError('AUTH_REJECTED' if len(parts) > 1 and parts[1] in ('401', '403') else 'HANDSHAKE_REJECTED')
            values = {}
            for line in header_lines:
                name, _, value = line.partition(':')
                values[name.strip().lower()] = value.strip()
            accept = base64.b64encode(hashlib.sha1(key.encode('ascii') + WS_GUID).digest()).decode('ascii')
            if values.get('sec-websocket-accept') != accept or values.get('upgrade', '').lower() != 'websocket':
                raise PushError('HANDSHAKE_REJECTED')
            ws = cls(sock)
            ws._pending = ws._reader.feed(rest)
            return ws
        except BaseException:
            raw.close()
            raise

    def settimeout(self, seconds):
        self._sock.settimeout(seconds)

    def send_text(self, text):
        with self._lock:
            self._sock.sendall(encode_frame(OP_TEXT, text))

    def recv_text(self):
        """Next text message; ``None`` on read timeout. Answers pings itself."""
        while True:
            while self._pending:
                opcode, payload = self._pending.pop(0)
                if opcode == OP_TEXT:
                    return payload.decode('utf-8')
                if opcode == OP_PING:
                    with self._lock:
                        self._sock.sendall(encode_frame(OP_PONG, payload))
                elif opcode == OP_CLOSE:
                    raise PushError('CLOSED')
            try:
                chunk = self._sock.recv(65536)
            except (socket.timeout, TimeoutError):
                return None
            if not chunk:
                raise PushError('CLOSED')
            self._pending = self._reader.feed(chunk)

    def close(self):
        try:
            with self._lock:
                self._sock.sendall(encode_frame(OP_CLOSE, b'\x03\xe8'))
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


class Channel:
    """One connected push session: handshake, upgrade, then ``events()``."""

    def __init__(self, origin_url, cookie_header, opener=None, ws_factory=WebSocket.connect, clock=time.monotonic):
        parts = urlsplit(origin_url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
            raise PushError('UNSAFE_ORIGIN')
        self.host, self.port = parts.hostname.lower(), parts.port or 443
        self.cookie_header = cookie_header
        self._opener = opener
        self._ws_factory = ws_factory
        self._clock = clock
        self.ws = None
        self.handshake = None

    def handshake_url(self):
        port = '' if self.port == 443 else f':{self.port}'
        return f'https://{self.host}{port}{SOCKET_PATH}?' + urlencode({'t': int(time.time() * 1000), 'EIO': 3, 'transport': 'polling'})

    def open(self):
        from urllib.request import Request
        from urllib.error import HTTPError, URLError
        request = Request(self.handshake_url(), headers={'Cookie': self.cookie_header, 'Accept': '*/*'})
        try:
            with self._opener.open(request, timeout=15) as response:
                final = urlsplit(response.geturl())
                if final.scheme != 'https' or (final.hostname or '').lower() != self.host or (final.port or 443) != self.port:
                    raise PushError('UNSAFE_ORIGIN')
                raw = response.read(65537)
        except HTTPError as error:
            raise PushError('AUTH_REJECTED' if error.code in (401, 403) else 'HANDSHAKE_REJECTED') from None
        except (URLError, OSError):
            raise PushError('NETWORK') from None
        if len(raw) > 65536:
            raise PushError('PROTOCOL')
        packets = decode_polling_payload(raw)
        if not packets:
            raise PushError('PROTOCOL')
        kind, data = parse_eio(packets[0])
        if kind != EIO_OPEN:
            raise PushError('HANDSHAKE_REJECTED')
        self.handshake = parse_open(data)
        path = SOCKET_PATH + 'websocket/?' + urlencode({'EIO': 3, 'transport': 'websocket', 'sid': self.handshake.sid})
        try:
            self.ws = self._ws_factory(self.host, self.port, path, {'Cookie': self.cookie_header})
        except PushError:
            raise
        except (OSError, ssl.SSLError):
            raise PushError('NETWORK') from None
        self.ws.settimeout(1.0)
        self.ws.send_text('2probe')
        deadline = self._clock() + 15
        while True:
            text = self.ws.recv_text()
            if text == '3probe':
                break
            if text is None and self._clock() > deadline:
                raise PushError('TIMEOUT')
        self.ws.send_text('5')
        # Packets that arrived on the polling transport (usually the Socket.IO
        # CONNECT "40") are replayed first so nothing is lost.
        return packets[1:]

    def packets(self, stop):
        """Yield Engine.IO packets; ping on schedule; raise on silence."""
        hs = self.handshake
        last_rx = next_ping = self._clock()
        next_ping += hs.ping_interval
        while not stop.is_set():
            now = self._clock()
            if now >= next_ping:
                self.ws.send_text('2')
                next_ping = now + hs.ping_interval
            if now - last_rx > hs.ping_interval + hs.ping_timeout:
                raise PushError('TIMEOUT')
            text = self.ws.recv_text()
            if text is None:
                continue
            last_rx = self._clock()
            yield parse_eio(text)

    def emit(self, name, data):
        self.ws.send_text('42' + json.dumps([name, data], ensure_ascii=False))

    def close(self):
        if self.ws is not None:
            self.ws.close()
            self.ws = None


class PushClient:
    """Background thread keeping one push channel up, with reconnect/backoff.

    ``credentials()`` returns ``None`` when signed out, else a dict with
    ``origin``, ``cookie``, ``opener``, ``sid``, ``username``, ``topics`` and
    ``alive`` (a callable that turns false once that session is replaced).
    ``on_change(kind)`` fires on the worker thread for each pushed message,
    ``kind`` being the topic's last segment (``in_mail``/``mod_mail``/``del_mail``).
    ``on_state(state, code)`` reports ``connecting``/``connected``/``waiting``/``stopped``."""

    READY_TIMEOUT = 20.0

    def __init__(self, credentials, on_change, on_state=lambda state, code=None: None,
                 channel_factory=Channel, backoff=None, wait=None, clock=time.monotonic):
        self._credentials = credentials
        self._on_change = on_change
        self._on_state = on_state
        self._channel_factory = channel_factory
        self.backoff = backoff or Backoff()
        self._stop = threading.Event()
        self._wait = wait or self._stop.wait
        self._clock = clock
        self._thread = None
        self._channel = None
        self._creds = None
        self._ready_by = None
        self.state = 'stopped'
        self.connected_once = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name='hongyan-push', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        channel = self._channel
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def _set(self, state, code=None):
        self.state = state
        try:
            self._on_state(state, code)
        except Exception:
            pass

    def run(self):
        while not self._stop.is_set():
            code = self.run_once()
            if self._stop.is_set():
                break
            self._set('waiting', code)
            if self._wait(0.5 if code == 'SESSION_CHANGED' else self.backoff.next()):
                break
        self._set('stopped')

    def run_once(self):
        """One connect-and-listen cycle; returns the failure code."""
        try:
            creds = self._credentials()
        except Exception:
            return 'NETWORK'
        if creds is None:
            return 'SIGNED_OUT'
        if not creds.get('topics'):
            return 'DISABLED'
        self._creds = creds
        self._set('connecting')
        try:
            self._channel = channel = self._channel_factory(creds['origin'], creds['cookie'], creds.get('opener'))
            replay = channel.open()
            self._ready_by = self._clock() + self.READY_TIMEOUT
            for packet in replay:
                self._handle(*parse_eio(packet))
            for kind, data in channel.packets(self._stop):
                self._handle(kind, data)
                self._check()
            return 'STOPPED'
        except PushError as error:
            return error.code
        except Exception:  # Never let the worker die; the poll fallback covers it.
            return 'PROTOCOL'
        finally:
            if self._channel is not None:
                try:
                    self._channel.close()
                except Exception:
                    pass
            self._channel = None

    def _check(self):
        if self._ready_by is not None and self._clock() > self._ready_by:
            raise PushError('AUTH_REJECTED')  # Never got 'ready' after auth.
        alive = self._creds.get('alive')
        if alive is not None and not alive():
            raise PushError('SESSION_CHANGED')

    def _handle(self, kind, data):
        if kind == EIO_CLOSE:
            raise PushError('CLOSED')
        if kind != EIO_MESSAGE:
            return
        packet = parse_sio(data)
        if packet.nsp != '/':
            return
        if packet.type == SIO_CONNECT:
            self._channel.emit('auth', auth_payload(self._creds['sid'], self._creds['username']))
        elif packet.type == SIO_DISCONNECT:
            raise PushError('CLOSED')
        elif packet.type == SIO_ERROR:
            raise PushError('AUTH_REJECTED')
        elif packet.event == 'ready':
            for topic in self._creds['topics']:
                self._channel.emit('subscribe', {'topic': topic})
            self._ready_by = None
            self.backoff.reset()
            self.connected_once = True
            self._set('connected')
        elif packet.event == 'message':
            message = packet.args[0] if packet.args and isinstance(packet.args[0], dict) else {}
            topic = message.get('topic')
            if topic not in self._creds['topics']:
                return
            try:
                self._on_change(topic.rstrip('/').rsplit('/', 1)[-1])
            except Exception:
                pass
