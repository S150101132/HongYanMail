"""Push channel: WebSocket frames, Engine.IO/Socket.IO packets, reconnect logic."""
import threading
import unittest

from coremail_desktop.protocol import push
from coremail_desktop.protocol.push import (Backoff, FrameReader, PushClient, PushError, decode_polling_payload,
                                            encode_frame, parse_eio, parse_open, parse_sio)


def server_frame(opcode, payload, fin=True):
    """Unmasked server-to-client frame."""
    head = bytearray([(0x80 if fin else 0) | opcode])
    if len(payload) < 126:
        head.append(len(payload))
    elif len(payload) < 1 << 16:
        head += bytes([126]) + len(payload).to_bytes(2, 'big')
    else:
        head += bytes([127]) + len(payload).to_bytes(8, 'big')
    return bytes(head) + payload


class FrameTest(unittest.TestCase):
    def test_client_frames_are_masked_and_roundtrip(self):
        frame = encode_frame(push.OP_TEXT, '2probe', mask=b'\x01\x02\x03\x04')
        self.assertEqual(frame[0], 0x81)
        self.assertEqual(frame[1], 0x80 | 6)
        self.assertEqual(FrameReader().feed(frame), [(push.OP_TEXT, b'2probe')])

    def test_extended_lengths(self):
        for size in (125, 126, 70000):
            payload = b'x' * size
            self.assertEqual(FrameReader().feed(server_frame(push.OP_TEXT, payload)), [(push.OP_TEXT, payload)])
            self.assertEqual(FrameReader().feed(encode_frame(push.OP_TEXT, payload)), [(push.OP_TEXT, payload)])

    def test_partial_input_is_buffered(self):
        data = server_frame(push.OP_TEXT, b'42["x"]') + server_frame(push.OP_PING, b'p')
        reader = FrameReader()
        out = []
        for byte in data:
            out += reader.feed(bytes([byte]))
        self.assertEqual(out, [(push.OP_TEXT, b'42["x"]'), (push.OP_PING, b'p')])

    def test_fragments_reassemble_around_control_frames(self):
        data = (server_frame(push.OP_TEXT, b'42["a', fin=False) + server_frame(push.OP_PING, b'')
                + server_frame(push.OP_CONT, b'b"]'))
        self.assertEqual(FrameReader().feed(data), [(push.OP_PING, b''), (push.OP_TEXT, b'42["ab"]')])

    def test_protocol_violations(self):
        for bad in (server_frame(push.OP_CONT, b'x'),              # continuation without start
                    bytes([0xC1, 0x00]),                          # RSV bit without extension
                    server_frame(push.OP_PING, b'p', fin=False),  # fragmented control frame
                    server_frame(0x3, b''),                       # reserved opcode
                    server_frame(push.OP_TEXT, b'a', fin=False) + server_frame(push.OP_TEXT, b'b')):
            with self.assertRaises(PushError):
                FrameReader().feed(bad)

    def test_oversized_message_rejected(self):
        reader = FrameReader(max_message=10)
        with self.assertRaises(PushError):
            reader.feed(server_frame(push.OP_TEXT, b'x' * 11))
        reader = FrameReader(max_message=10)
        with self.assertRaises(PushError):
            reader.feed(server_frame(push.OP_TEXT, b'x' * 6, fin=False) + server_frame(push.OP_CONT, b'x' * 6))


class PacketTest(unittest.TestCase):
    OPEN = '0{"sid":"abc","upgrades":["websocket"],"pingInterval":30000,"pingTimeout":60000}'

    def test_polling_payload_text_and_binary(self):
        text = f'{len(self.OPEN)}:{self.OPEN}2:40'.encode()
        self.assertEqual(decode_polling_payload(text), [self.OPEN, '40'])
        size = [int(d) for d in str(len(self.OPEN))]
        binary = bytes([0] + size + [255]) + self.OPEN.encode() + bytes([0, 2, 255]) + b'40'
        self.assertEqual(decode_polling_payload(binary), [self.OPEN, '40'])
        for bad in (b'5:abc', b'x:1', b'\x00\x05abc'):
            with self.assertRaises((PushError, ValueError)):
                decode_polling_payload(bad)

    def test_polling_length_counts_characters(self):
        packet = '42["新邮件"]'
        self.assertEqual(decode_polling_payload(f'{len(packet)}:{packet}'.encode('utf-8')), [packet])

    def test_open_packet(self):
        kind, data = parse_eio(self.OPEN)
        self.assertEqual(kind, push.EIO_OPEN)
        hs = parse_open(data)
        self.assertEqual((hs.sid, hs.ping_interval, hs.ping_timeout), ('abc', 30.0, 60.0))
        for bad in ('{"sid":"a b","upgrades":["websocket"]}', '{"sid":"a","upgrades":[]}',
                    '{"sid":"a","upgrades":["websocket"],"pingInterval":5}', '[]', 'nope'):
            with self.assertRaises(PushError):
                parse_open(bad)

    def test_eio_types(self):
        self.assertEqual(parse_eio('3probe'), (push.EIO_PONG, 'probe'))
        self.assertEqual(parse_eio('6'), (push.EIO_NOOP, ''))
        for bad in ('', '9', 'x'):
            with self.assertRaises(PushError):
                parse_eio(bad)

    def test_sio_packets(self):
        self.assertEqual(parse_sio('0').type, push.SIO_CONNECT)
        event = parse_sio('2["mailmessage",{"fid":1}]')
        self.assertEqual((event.type, event.nsp, event.event, event.args), (push.SIO_EVENT, '/', 'mailmessage', [{'fid': 1}]))
        named = parse_sio('2/mail,12["new",1]')
        self.assertEqual((named.nsp, named.ack_id, named.event), ('/mail', 12, 'new'))
        self.assertEqual(parse_sio('0/mail').nsp, '/mail')
        binary = parse_sio('51-["up",{"_placeholder":true,"num":0}]')
        self.assertEqual(binary.event, 'up')
        self.assertIsNone(parse_sio('3[1]').event)
        for bad in ('', '9', '2[unterminated', '5x-[]'):
            with self.assertRaises(PushError):
                parse_sio(bad)


class BackoffTest(unittest.TestCase):
    def test_grows_caps_and_resets(self):
        backoff = Backoff(base=2, cap=60, rand=lambda: 1.0)
        self.assertEqual([backoff.next() for _ in range(7)], [2, 4, 8, 16, 32, 60, 60])
        backoff.reset()
        self.assertEqual(backoff.next(), 2)

    def test_jitter_stays_within_half_to_full(self):
        low = Backoff(base=8, rand=lambda: 0.0)
        self.assertEqual(low.next(), 4)


TOPIC = '/org/onstat/mail/u@example/in_mail'
MOD = '/org/onstat/mail/u@example/mod_mail'
READY = '42["ready",{}]'


def message(topic):
    return '42' + push.json.dumps(['message', {'topic': topic, 'payload': '{"type":"x","payload":{}}'}])


def creds(**extra):
    return {'origin': 'https://mail.example', 'cookie': 'Coremail.sid=x', 'opener': None, 'sid': 'S',
            'username': 'u@example', 'topics': [TOPIC, MOD], **extra}


class FakeChannel:
    """Scripted channel: each instance pops the next scenario."""
    def __init__(self, scenarios, log):
        self.scenarios, self.log = scenarios, log

    def __call__(self, origin, cookie, opener):
        self.log.append(('open', origin))
        return _Scripted(self.scenarios.pop(0), self.log)


class _Scripted:
    def __init__(self, scenario, log):
        self.scenario, self.log = scenario, log

    def open(self):
        if isinstance(self.scenario, Exception):
            raise self.scenario
        return self.scenario.get('replay', [])

    def packets(self, stop):
        for packet in self.scenario.get('packets', []):
            yield parse_eio(packet)
        raise self.scenario.get('end', PushError('CLOSED'))

    def emit(self, name, data):
        self.log.append(('emit', name, data))

    def close(self):
        self.log.append(('close',))


class PushClientTest(unittest.TestCase):
    def make(self, scenarios, waits_before_stop, credentials=None, clock=None):
        log, changes, states, waits = [], [], [], []
        client = None

        def wait(seconds):
            waits.append(seconds)
            if len(waits) >= waits_before_stop:
                client._stop.set()
                return True
            return False
        client = PushClient(credentials or creds, changes.append,
                            lambda state, code=None: states.append((state, code)),
                            channel_factory=FakeChannel(scenarios, log), backoff=Backoff(base=2, cap=60, rand=lambda: 1.0),
                            wait=wait, **({'clock': clock} if clock else {}))
        return client, log, changes, states, waits

    def test_auth_ready_subscribe_then_messages(self):
        scenarios = [{'replay': ['40'], 'packets': [READY, '3', message(TOPIC), message(MOD), message('/someone/else')]}]
        client, log, changes, states, waits = self.make(scenarios, 1)
        client.run()
        emits = [e[1:] for e in log if e[0] == 'emit']
        self.assertEqual(emits[0], ('auth', {'clientId': 'webmail:S', 'sid': 'S', 'username': 'u@example'}))
        self.assertEqual(emits[1:], [('subscribe', {'topic': TOPIC}), ('subscribe', {'topic': MOD})])
        self.assertEqual(changes, ['in_mail', 'mod_mail'])  # Unsubscribed topics are ignored.
        self.assertIn(('connected', None), states)

    def test_ready_resets_backoff(self):
        scenarios = [PushError('NETWORK'), PushError('NETWORK'), {'packets': ['40', READY]}, PushError('NETWORK')]
        client, log, changes, states, waits = self.make(scenarios, 4)
        client.run()
        # 2, 4 before connecting; 'ready' resets, so 2 again, then 4.
        self.assertEqual(waits, [2, 4, 2, 4])
        self.assertEqual(states[-1], ('stopped', None))
        self.assertTrue(client.connected_once)
        self.assertEqual(log.count(('close',)), 4)  # Every channel is closed, even after a failed open.

    def test_connect_without_ready_does_not_reset(self):
        scenarios = [PushError('NETWORK'), {'packets': ['40']}, PushError('NETWORK')]
        client, log, changes, states, waits = self.make(scenarios, 3)
        client.run()
        self.assertEqual(waits, [2, 4, 8])
        self.assertFalse(client.connected_once)

    def test_missing_ready_times_out_as_auth_rejected(self):
        now = [0.0]

        def clock():
            now[0] += 15
            return now[0]
        client, log, changes, states, waits = self.make([{'packets': ['40', '3', '3', '3']}], 1, clock=clock)
        client.run()
        self.assertEqual([c for s, c in states if s == 'waiting'], ['AUTH_REJECTED'])

    def test_failure_codes_are_reported(self):
        scenarios = [PushError('AUTH_REJECTED'), {'replay': ['40', '44"denied"']}]
        client, log, changes, states, waits = self.make(scenarios, 2)
        client.run()
        self.assertEqual([c for s, c in states if s == 'waiting'], ['AUTH_REJECTED', 'AUTH_REJECTED'])
        self.assertFalse(changes)

    def test_server_close_and_garbage_reconnect(self):
        scenarios = [{'packets': ['40', READY, '1']}, {'packets': ['40', '4{bad']}, {'packets': ['41']}]
        client, log, changes, states, waits = self.make(scenarios, 3)
        client.run()
        self.assertEqual([c for s, c in states if s == 'waiting'], ['CLOSED', 'PROTOCOL', 'CLOSED'])
        self.assertEqual(len([e for e in log if e[0] == 'open']), 3)

    def test_replaced_session_reconnects_quickly(self):
        alive = [True]
        scenarios = [{'packets': ['40', READY, '3', '3']}, {'packets': ['40', READY]}]
        client, log, changes, states, waits = self.make(scenarios, 2, credentials=lambda: creds(alive=lambda: alive[0]))
        original = client._handle

        def handle(kind, data):
            original(kind, data)
            if kind == push.EIO_PONG:
                alive[0] = False
        client._handle = handle
        client.run()
        self.assertEqual([c for s, c in states if s == 'waiting'][0], 'SESSION_CHANGED')
        self.assertEqual(waits[0], 0.5)

    def test_signed_out_or_disabled_waits_without_connecting(self):
        for credentials, code in ((lambda: None, 'SIGNED_OUT'), (lambda: creds(topics=[]), 'DISABLED')):
            log, states, waits = [], [], []

            def wait(seconds):
                waits.append(seconds)
                return len(waits) >= 2
            client = PushClient(credentials, lambda name: None, lambda s, c=None: states.append((s, c)),
                                channel_factory=FakeChannel([], log), backoff=Backoff(rand=lambda: 1.0), wait=wait)
            client.run()
            self.assertEqual(log, [])
            self.assertEqual([c for s, c in states if s == 'waiting'], [code, code])

    def test_credential_and_callback_errors_do_not_kill_the_worker(self):
        def broken():
            raise OSError('offline')
        client, log, changes, states, waits = self.make([], 1, credentials=broken)
        client.run()
        self.assertEqual([c for s, c in states if s == 'waiting'], ['NETWORK'])

        def boom(name):
            raise RuntimeError('ui gone')
        client = PushClient(creds, boom, channel_factory=FakeChannel([{'packets': ['40', READY, message(TOPIC)]}], []),
                            backoff=Backoff(rand=lambda: 1.0), wait=lambda s: True)
        client.run()
        self.assertEqual(client.state, 'stopped')

    def test_start_stop_thread(self):
        opened = threading.Event()

        class Blocking:
            def __init__(self, *a):
                pass

            def open(self):
                opened.set()
                return ['40']

            def emit(self, name, data):
                pass

            def packets(self, stop):
                while not stop.is_set():
                    stop.wait(0.05)
                return
                yield

            def close(self):
                pass
        client = PushClient(creds, lambda n: None, channel_factory=Blocking)
        client.start()
        self.assertTrue(opened.wait(2))
        client.stop()
        self.assertFalse(client._thread.is_alive())
        self.assertEqual(client.state, 'stopped')


class SessionCredentialsTest(unittest.TestCase):
    def session(self, config):
        from unittest import mock
        from http.cookiejar import Cookie
        from coremail_desktop.protocol.coremail import DesktopSession
        session = DesktopSession('https://mail.example/coremail/')
        session.sid = 'SID'
        session._cookies.set_cookie(Cookie(0, 'Coremail', 'C', None, False, 'mail.example', False, False, '/', False,
                                           True, None, False, None, None, {}))
        patcher = mock.patch('coremail_desktop.protocol.push.read_push_config', return_value=config)
        patcher.start()
        self.addCleanup(patcher.stop)
        return session

    def test_cookie_topics_and_liveness(self):
        session = self.session({'enablePushNotify': True, 'onlineHost': 'https://mail.example/', 'orgid': 'o',
                                'dnemail': 'u@example', 'onlineInMailPath': '/$org_id$/onstat/mail/$uid$/in_mail'})
        value = session.push_credentials()
        self.assertEqual(value['origin'], 'https://mail.example')
        self.assertEqual(value['cookie'], 'Coremail=C; Coremail.sid=SID')
        self.assertEqual(value['topics'], ['/o/onstat/mail/u@example/in_mail'])
        self.assertTrue(value['alive']())
        session.close()
        self.assertFalse(value['alive']())

    def test_push_off_or_foreign_host_means_no_push(self):
        self.assertIsNone(self.session({'enablePushNotify': False}).push_credentials())
        self.assertIsNone(self.session({'enablePushNotify': True, 'onlineHost': 'https://push.other.example/'}).push_credentials())


class ConfigTest(unittest.TestCase):
    PAGE = ("<script>X.U={enablePushNotify:true,onlineHost:'https:\\/\\/mail.example\\/',orgid:'org',"
            "dnemail:\"u@example\",onlineInMailPath:'/$org_id$/onstat/mail/$uid$/in_mail',"
            "onlineModMailPath:'/$org_id$/onstat/mail/$uid$/mod_mail',onlineTryCount:5,secret:'x'};</script>")

    def test_parse_and_fill_topics(self):
        config = push.parse_push_config(self.PAGE)
        self.assertEqual(config['onlineHost'], 'https://mail.example/')
        self.assertIs(config['enablePushNotify'], True)
        self.assertEqual(config['onlineTryCount'], 5)
        self.assertNotIn('secret', config)
        self.assertEqual(push.topics(config), ['/org/onstat/mail/u@example/in_mail', '/org/onstat/mail/u@example/mod_mail'])

    def test_unfillable_topics_are_dropped(self):
        self.assertEqual(push.topics({'onlineInMailPath': '/$org_id$/x/$uid$/in_mail', 'dnemail': 'u'}), [])
        self.assertEqual(push.topics({}), [])


class ChannelTest(unittest.TestCase):
    def test_rejects_non_https_origin(self):
        for origin in ('http://mail.example', 'https://user:pw@mail.example', 'ftp://x'):
            with self.assertRaises(PushError):
                push.Channel(origin, 'c')

    def test_handshake_url_is_same_origin(self):
        url = push.Channel('https://Mail.Example/coremail/', 'c').handshake_url()
        self.assertTrue(url.startswith('https://mail.example/socket.io/1/?t='))
        self.assertIn('EIO=3', url)
        self.assertIn('transport=polling', url)

    def test_open_upgrades_with_probe(self):
        sent = []

        class Response:
            def __init__(self, body):
                self.body = body

            def geturl(self):
                return 'https://mail.example/socket.io/1/'

            def read(self, n):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        open_packet = PacketTest.OPEN
        body = f'{len(open_packet)}:{open_packet}2:40'.encode()

        class Opener:
            def open(self, request, timeout):
                sent.append(('GET', request.full_url, request.get_header('Cookie')))
                return Response(body)

        class FakeWs:
            incoming = ['3probe']

            def settimeout(self, s):
                pass

            def send_text(self, text):
                sent.append(('ws', text))

            def recv_text(self):
                return self.incoming.pop(0) if self.incoming else None

            def close(self):
                pass

        def factory(host, port, path, headers):
            sent.append(('connect', host, port, path, headers['Cookie']))
            return FakeWs()
        channel = push.Channel('https://mail.example', 'Coremail.sid=s', Opener(), ws_factory=factory)
        self.assertEqual(channel.open(), ['40'])
        self.assertEqual(sent[0][2], 'Coremail.sid=s')
        self.assertEqual(sent[1], ('connect', 'mail.example', 443, '/socket.io/1/websocket/?EIO=3&transport=websocket&sid=abc', 'Coremail.sid=s'))
        self.assertEqual([s[1] for s in sent if s[0] == 'ws'], ['2probe', '5'])

    def test_redirect_to_other_origin_is_refused(self):
        class Response:
            def geturl(self):
                return 'https://evil.example/socket.io/1/'

            def read(self, n):
                return b''

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Opener:
            def open(self, request, timeout):
                return Response()
        with self.assertRaises(PushError) as caught:
            push.Channel('https://mail.example', 'c', Opener()).open()
        self.assertEqual(caught.exception.code, 'UNSAFE_ORIGIN')

    def test_silence_times_out_and_pings_on_schedule(self):
        now = [0.0]
        sent = []

        class FakeWs:
            def send_text(self, text):
                sent.append(text)

            def recv_text(self):
                now[0] += 10
                return None

            def close(self):
                pass
        channel = push.Channel('https://mail.example', 'c', clock=lambda: now[0])
        channel.ws = FakeWs()
        channel.handshake = push.Handshake('s', 30, 60, ['websocket'])
        with self.assertRaises(PushError) as caught:
            list(channel.packets(threading.Event()))
        self.assertEqual(caught.exception.code, 'TIMEOUT')
        self.assertGreaterEqual(sent.count('2'), 2)


if __name__ == '__main__':
    unittest.main()
