"""Read-only probe of the Coremail push channel with the user's own session.

Signs in (password from a local dialog, memory only), reads the push settings
the webmail page carries (X.U: enablePushNotify, onlineHost, online*Path),
opens the socket.io channel, authenticates and subscribes the way the webmail
script does (auth -> ready -> subscribe {topic}), then sends one message to
the account's own address and measures how long until the push arrives.

Records only frame/packet types, event names, topic templates (placeholders,
never the filled-in ids) and payload key names / value types.
Output: analysis/push-shape-<date>.json.
"""
import json
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coremail_desktop.app.backend import MailBackend  # noqa: E402
from coremail_desktop.protocol import push  # noqa: E402

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'
SEND = '--no-send' not in sys.argv
LISTEN = 120


def ask_password():
    import tkinter
    from tkinter import simpledialog
    root = tkinter.Tk(); root.withdraw(); root.attributes('-topmost', True)
    try:
        return simpledialog.askstring('鸿雁 · 推送通道探测', f'{ACCOUNT} 的密码（仅本次内存使用）:\n只读探测推送通道 + 给自己发 1 封测试信', show='*', parent=root)
    finally:
        root.destroy()


def shape(value, depth=0):
    if depth > 4:
        return '…'
    if isinstance(value, dict):
        return {(k if re.fullmatch(r'[A-Za-z_][\w.-]{0,40}', str(k)) else '<key>'): shape(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [shape(value[0], depth + 1), f'len={len(value)}'] if value else []
    if isinstance(value, str):
        if value[:1] in '{[':
            try:
                return {'json': shape(json.loads(value), depth + 1)}
            except ValueError:
                pass
        return 'str'
    if isinstance(value, bool):
        return 'bool'
    if isinstance(value, (int, float)):
        return 'num'
    return type(value).__name__


def safe_name(name):
    return name if isinstance(name, str) and re.fullmatch(r'/?[A-Za-z_][\w.:/-]{0,40}', name) else '<redacted>'


def template(path, fills):
    """Topic with the filled ids put back as placeholders; other segments kept only if plain words."""
    for value, mark in fills:
        if value:
            path = path.replace(value, mark)
    return '/'.join(seg if re.fullmatch(r'(\$\w+\$|[A-Za-z_]{1,30})?', seg) else '<seg>' for seg in path.split('/'))


def main():
    password = ask_password()
    if not password:
        return 130
    data = Path(tempfile.mkdtemp(prefix='hongyan-push-', dir=str(ROOT / 'build')))
    report = {'date': time.strftime('%Y-%m-%d %H:%M'), 'scope': 'push channel shapes; no values', 'steps': {}, 'frames': []}
    steps = report['steps']
    t0 = time.monotonic()
    state = {'ready': threading.Event(), 'first': None}

    def record(kind, **fields):
        report['frames'].append({'t': round(time.monotonic() - t0, 2), 'kind': kind, **fields})

    backend = MailBackend(data)
    try:
        backend.login('https://newmail.inspur.com/coremail/', ACCOUNT, password, False)
        password = None
        steps['login'] = 'passed'
        session = backend.adapter._session
        from coremail_desktop.protocol.push import read_push_config
        config = read_push_config(session)
        fills = [(config.get('orgid'), '$org_id$'), (config.get('dnemail'), '$uid$'), (ACCOUNT, '$uid$'), (session.sid, '$sid$')]
        steps['page_config'] = {
            'keys': sorted(config),
            'enablePushNotify': config.get('enablePushNotify'),
            'onlineHost': (lambda p: {'scheme': p.scheme, 'same_host': (p.hostname or '') == 'newmail.inspur.com', 'path': p.path or '/'})(urlsplit(config['onlineHost'])) if config.get('onlineHost') else None,
            'paths': {k: template(v, [(None, None)]) for k, v in config.items() if k.endswith('Path') and isinstance(v, str)},
            'orgid': type(config.get('orgid')).__name__, 'dnemail_is_account': config.get('dnemail', '').lower() == ACCOUNT.lower(),
        }
        topics = push.topics(config)
        steps['topics'] = [template(t, fills) for t in topics]

        creds = backend.adapter.push_credentials()
        origin, cookie, opener = creds['origin'], creds['cookie'], creds['opener']
        channel = push.Channel(origin, cookie, opener)
        replay = channel.open()
        steps['upgrade'] = {'status': 'passed', 'replayed': len(replay)}
        stop = threading.Event()
        topic_templates = dict(zip(topics, steps['topics']))

        def handle(kind, body):
            entry = {'eio': kind}
            if kind == push.EIO_MESSAGE:
                sio = push.parse_sio(body)
                entry.update(sio=sio.type, nsp=sio.nsp if sio.nsp == '/' else safe_name(sio.nsp),
                             event=safe_name(sio.event) if sio.event else None)
                if sio.type == push.SIO_CONNECT:
                    channel.ws.send_text('42' + json.dumps(['auth', push.auth_payload(session.sid, ACCOUNT)]))
                    record('sent', event='auth', args={'clientId': 'str', 'sid': 'str', 'username': 'str'})
                if sio.event == 'ready':
                    state['ready'].set()
                if sio.event:
                    args = sio.args
                    entry['args'] = shape(args)
                    if sio.event == 'message' and args and isinstance(args[0], dict):
                        entry['topic'] = topic_templates.get(args[0].get('topic'), template(str(args[0].get('topic')), fills))
                        if state.get('sent') and state['first'] is None:
                            state['first'] = {'after_send_s': round(time.monotonic() - state['sent'], 2), 'topic': entry['topic']}
            record('ws', **entry)

        def listen():
            try:
                for p in replay:
                    handle(*push.parse_eio(p))
                for kind, body in channel.packets(stop):
                    handle(kind, body)
            except push.PushError as error:
                record('ws-end', code=error.code)

        thread = threading.Thread(target=listen, daemon=True)
        thread.start()
        steps['ready'] = state['ready'].wait(15)
        for topic in topics:
            channel.ws.send_text('42' + json.dumps(['subscribe', {'topic': topic}]))
            record('sent', event='subscribe', topic=topic_templates[topic])
        time.sleep(5)

        if SEND:
            marker = 'HY-PUSH-' + uuid.uuid4().hex[:10]
            draft = backend.compose('new')
            draft.update(to=[ACCOUNT], subject=f'鸿雁 推送通道验证 {marker}', html=f'<p>推送通道验证 {marker}</p>' + draft['html'])
            state['sent'] = time.monotonic()
            steps['send_to_self'] = backend.send(draft)['state']
        deadline = time.monotonic() + LISTEN
        while time.monotonic() < deadline and thread.is_alive():
            if state['first'] and time.monotonic() - state['sent'] > state['first']['after_send_s'] + 15:
                break
            time.sleep(1)
        stop.set()
        channel.close()
        thread.join(5)
        steps['first_message_after_send'] = state['first']
    except push.PushError as error:
        steps['failure'] = error.code
    except Exception as error:
        steps['failure'] = type(error).__name__
    finally:
        backend.logout()
        shutil.rmtree(data, ignore_errors=True)
    out = ROOT / 'analysis' / f"push-shape-{time.strftime('%Y-%m-%d')}.json"
    previous = json.loads(out.read_text(encoding='utf-8')) if out.exists() else None
    if previous and 'runs' not in previous:
        previous = {'runs': [previous]}
    result = previous or {'runs': []}
    result['runs'].append(report)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print('written:', out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
