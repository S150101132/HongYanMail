"""Probe server-side S/MIME sending (read-only checks plus one encrypted send-to-self).

Records only config key names/booleans, response codes and shapes, and whether
the delivered self-test is an enveloped-data message that the user's own
certificate decrypts to the expected marker. No mail content or addresses saved.
"""
import json
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.protocol.coremail import DesktopSession, CoremailAdapter  # noqa: E402
from coremail_desktop.models import AccountConfig  # noqa: E402
from probe.coremail_transport import decode_extended_json  # noqa: E402

ACCOUNT = 'wangbiao@inspur.com'
PFX = ROOT / '王标-certificate.pfx'
SEND = '--send' in sys.argv


def ask(title, prompt):
    import tkinter
    from tkinter import simpledialog
    root = tkinter.Tk(); root.withdraw(); root.attributes('-topmost', True)
    try:
        return simpledialog.askstring(title, prompt, show='*', parent=root)
    finally:
        root.destroy()


def shape(value, depth=0):
    if isinstance(value, dict) and depth < 4:
        return {k: shape(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return {'<list>': len(value), **(shape(value[0], depth + 1) if value and isinstance(value[0], dict) else {})}
    if isinstance(value, bool):
        return value
    return type(value).__name__


def raw_call(session, func, params):
    url = session._rpc_url(func) if not func.startswith('%') else session._rpc_url(func)
    raw, _ = session._fetch(Request(url, data=json.dumps(params, ensure_ascii=False).encode('utf-8'),
                                    headers={'Accept': 'text/x-json', 'Content-Type': 'text/x-json'}))
    return decode_extended_json(raw.decode('utf-8-sig'))


def main():
    password = ask('鸿雁 · S/MIME 探测', f'{ACCOUNT} 邮箱密码（仅本次内存使用）:')
    if not password:
        return 130
    report = {'date': time.strftime('%Y-%m-%d %H:%M')}
    adapter = CoremailAdapter()
    adapter.login(AccountConfig('primary', 'https://newmail.inspur.com/coremail/', ACCOUNT), password)
    del password
    session = adapter._session
    try:
        # 1. S/MIME module config as served to webmail (key names and booleans only).
        raw, _ = session._fetch(Request(urljoin(session.base_url, 'XT/index.jsp') + '?' + urlencode({'sid': session.sid})))
        page = raw.decode('utf-8', errors='replace')
        found = {}
        for m in re.finditer(r'["\']?(SMIME|smime\w*|defaultEncrypt|keypass|supportSMIME\w*)["\']?\s*:\s*(true|false|\d+|\{[^{}]{0,600}\})', page):
            value = m.group(2)
            if value.startswith('{'):
                value = {k: v for k, v in re.findall(r'["\']?(\w+)["\']?\s*:\s*(true|false|\d+)', value)}
            found.setdefault(m.group(1), value)
        report['smime_config'] = found

        # 2. Certificate lookups.
        colleague = next((p['email'] for p in adapter.match_contacts('wang', 10)
                          if p['email'].lower().endswith('@inspur.com') and p['email'].lower() != ACCOUNT), None)
        report['certs'] = {}
        for label, params in [('get', {}), ('list_self', {'emails': [ACCOUNT]}), ('list_self_addr', {'addrs': [ACCOUNT]}),
                              ('list_self_email', {'email': ACCOUNT}),
                              ('list_colleague', {'emails': [colleague]} if colleague else None)]:
            if params is None:
                continue
            func = 'cert:get' if label == 'get' else 'cert:listPublicKey'
            try:
                result = raw_call(session, func, params)
                report['certs'][label] = {'code': result.get('code'), 'shape': shape(result.get('var')) if result.get('code') == 'S_OK' else None}
            except Exception as error:
                report['certs'][label] = {'error': type(error).__name__}

        # 3. One encrypted send-to-self through the webmail flag.
        if SEND:
            marker = 'HYS-' + uuid.uuid4().hex[:12]
            attrs = {'account': ACCOUNT, 'to': [ACCOUNT], 'cc': [], 'bcc': [], 'subject': f'鸿雁 加密发送验证 {marker}',
                     'content': f'<p>加密发送验证 <b>{marker}</b></p>', 'isHtml': True, 'saveSentCopy': True,
                     'attachments': [], 'smimeSign': False, 'smimeEncrypt': True, 'smimeEnvelopId': ''}
            started = raw_call(session, '%mbox/compose', {'action': 'continue', 'returnInfo': True, 'attrs': attrs})
            compose_id = started.get('var') if isinstance(started.get('var'), str) else (started.get('var') or {}).get('id')
            report['continue'] = started.get('code')
            delivered = raw_call(session, '%mbox/compose', {'action': 'deliver', 'id': compose_id, 'returnInfo': True,
                                                            'autosaveHitCounter': True, 'attrs': attrs})
            report['deliver'] = {'code': delivered.get('code'), 'shape': shape(delivered.get('var'))}
            if delivered.get('code') == 'S_OK':
                for _ in range(18):
                    time.sleep(5)
                    rows = [m for m in adapter.list_messages('1', 0, 20) if marker in m.subject]
                    if rows:
                        raw = adapter.fetch_mime(rows[0].remote_id)
                        head = raw[:raw.find(b'\r\n\r\n') if b'\r\n\r\n' in raw else 4000].decode('latin-1')
                        ctype = re.search(r'(?im)^content-type:\s*([^;\r\n]+)(.*)', head)
                        report['received'] = {'content_type': ctype.group(1) if ctype else None,
                                              'enveloped': 'enveloped-data' in head.lower(),
                                              'x_cm_smime': re.search(r'(?im)^x-cm-smime:\s*(.*)$', head).group(1).strip()
                                              if re.search(r'(?im)^x-cm-smime:', head) else None,
                                              'flags': dict(rows[0].flags)}
                        if report['received']['enveloped']:
                            pfx_password = ask('鸿雁 · 证书', '个人证书 (.pfx) 密码，仅用于在内存中解密这封验证邮件:')
                            if pfx_password:
                                from coremail_desktop.crypto.service import CryptoService
                                crypto = CryptoService()
                                crypto.unlock(str(PFX), pfx_password)
                                del pfx_password
                                plain = crypto.decrypt(raw).mime_bytes
                                report['decrypted'] = {'marker_found': marker.encode() in plain}
                                crypto.lock()
                        break
                else:
                    report['received'] = 'not_arrived_in_90s'
    finally:
        adapter.close()
    out = ROOT / 'analysis' / 'smime-send-probe-2026-10-17.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
