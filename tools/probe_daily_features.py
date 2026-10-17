"""Read-only shape probe for the daily-use client (folder stats, list preview, contacts).

Records only response codes, key names, value types and counts. No subjects,
addresses, names or bodies are written. The password is asked in a local masked
dialog and kept in memory only.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coremail_desktop.protocol.coremail import DesktopSession  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402
from probe.coremail_transport import CoremailError  # noqa: E402

PROBES = [
    ('mbox:getAllFolders', {'stats': True}),
    ('mbox:listMessages', {'fid': 1, 'start': 0, 'limit': 3, 'order': 'date', 'desc': True,
                           'returnTotal': True, 'summaryWindowSize': 120}),
    ('mbox:listMessages', {'fid': 1, 'start': 0, 'limit': 3, 'order': 'date', 'desc': True,
                           'returnTotal': True, 'filter': {'flags': {'read': False}}}),
    ('oab:getDirectories', {}),
    ('oab:listEx', {'dn': 'a', 'returnAttrs': ['@id', 'true_name', 'email'], 'start': 0, 'limit': 3}),
    ('oab:searchEx', {'pattern': '{Q}', 'limit': 5}),
    ('oab:searchEx', {'keyword': '{Q}', 'limit': 5}),
    ('oab:autoMatch', {'pattern': '{Q}', 'limit': 5}),
    ('oab:autoMatch', {'keyword': '{Q}', 'limit': 5}),
    ('pab:getAllGroups', {}),
    ('pab:getContacts', {}),
    ('pab:searchContacts', {'pattern': '{Q}'}),
    ('user:getAttrs', {'optionalAttrIds': ['true_name', 'signature']}),
]


def shape(value, depth=0):
    if depth > 4:
        return type(value).__name__
    if isinstance(value, dict):
        return {key: shape(item, depth + 1) for key, item in sorted(value.items())}
    if isinstance(value, list):
        merged = {}
        for item in value[:20]:
            s = shape(item, depth + 1)
            if isinstance(s, dict):
                for k, v in s.items():
                    merged.setdefault(k, v)
            else:
                merged.setdefault('<item>', s)
        return {'<list>': len(value), **merged}
    return type(value).__name__


def ask_password(account):
    import tkinter
    from tkinter import simpledialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        return simpledialog.askstring('鸿雁 · 只读探测', f'{account} 的邮箱密码（仅本次内存使用）:', show='*', parent=root)
    finally:
        root.destroy()


def main():
    account = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'
    query = account.split('@')[0][:4]
    password = ask_password(account)
    if not password:
        print('cancelled')
        return 130
    session = DesktopSession('https://newmail.inspur.com/coremail/')
    session.ALLOWED = frozenset(name for name, _ in PROBES) | session.ALLOWED
    report = {}
    try:
        session.login(account, password)
        del password
        for index, (func, params) in enumerate(PROBES):
            params = json.loads(json.dumps(params).replace('{Q}', query))
            key = f'{index:02d} {func} {sorted(params)}'
            try:
                from urllib.request import Request
                from probe.coremail_transport import decode_extended_json
                raw, _ = session._fetch(Request(session._rpc_url(func), data=json.dumps(params).encode('utf-8'),
                                                headers={'Accept': 'text/x-json', 'Content-Type': 'text/x-json'}))
                result = decode_extended_json(raw.decode('utf-8-sig'))
                code = result.get('code') if isinstance(result, dict) else None
                # Server failure codes are fixed FA_* identifiers, not user data.
                report[key] = {'code': code if isinstance(code, str) and code.isupper() else '?',
                               'shape': shape({k: v for k, v in result.items() if k not in ('code', 'messages')}) if code == 'S_OK' else None}
            except (AppError, CoremailError) as error:
                report[key] = {'code': error.code}
    except (AppError, CoremailError) as error:
        report['login'] = error.code
    finally:
        session.close()
    out = ROOT / 'analysis' / 'daily-features-shape-2026-10-17.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
