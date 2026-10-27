"""The relay an AI tool starts (`python -m coremail_desktop.mcp`): MCP over stdio, one JSON
message per line, passed to the running app over its named pipe and back.

It holds no mail and opens no account: without the app running, or with 设置 → 允许本机 AI
工具读取邮箱 off, every request gets an error saying so. Only the standard library and
DPAPI; nothing is written.
"""
import json
import os
import sys
from multiprocessing.connection import Client
from pathlib import Path

from coremail_desktop.mcp.server import KEY_FILE

OFF = '鸿雁没有运行，或没有在「设置 → AI 助手」里打开「允许本机 AI 工具读取邮箱」。请打开后重试。'


def _key_file():
    """mcp.key where the app keeps its data (bootstrap._data_root): the newest of the places
    it may be, since this process may see other folders as writable than the app did."""
    override = os.environ.get('HONGYAN_DATA_DIR')
    places = [Path(override)] if override else [
        Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'CoremailDesktopPreview',
        Path(__file__).resolve().parents[2] / 'HongyanData', Path(sys.executable).parent / 'HongyanData']
    found = [p / KEY_FILE for p in places if (p / KEY_FILE).is_file()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def _connect(protector):
    path = _key_file()
    if path is None:
        return None
    try:
        record = json.loads(protector.unprotect(path.read_bytes()).decode('utf-8'))
        return Client(record['pipe'], family='AF_PIPE', authkey=record['key'].encode('ascii'))
    except Exception:
        return None  # Not running, turned off and on again (a new key), or another user's file.


def main(stdin=sys.stdin, stdout=sys.stdout, protector=None):
    from coremail_desktop.storage.dpapi import DataProtector
    protector, connection = protector or DataProtector(), None
    for line in stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        reply = None
        for _ in range(2):  # The app may have restarted since the last request: connect again once.
            connection = connection or _connect(protector)
            if connection is None:
                break
            try:
                connection.send(message)
                reply = connection.recv()
                break
            except (EOFError, OSError):
                connection = None
        else:
            connection = None
        if connection is None and reply is None:
            if not isinstance(message, dict) or 'id' not in message:
                continue
            reply = {'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32000, 'message': OFF}}
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + '\n')
            stdout.flush()


if __name__ == '__main__':
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    main()
