"""Encrypted send-to-self through the pre-encoded MIME path (webmail's CA path).

Webmail puts the fully built S/MIME message into attrs.lettercontent and POSTs
the attrs as form data to !mformdata (XT/jsp/mformdata.jsp) with
func=mbox:composeF&uploadMode=preEncodedData. The native client's
protocol_sendmail_coremail.cpp likewise posts its main part to mbox:composeF.

Records only codes, shapes, booleans and content-type names. Passwords are
asked in local dialogs and kept in memory only.
"""
import json
import re
import sys
import time
import uuid
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.ciphers import algorithms  # noqa: E402
from cryptography.hazmat.primitives.serialization import pkcs7  # noqa: E402
from tools.probe_smime_send2 import ACCOUNT, PFX, ask, load_cert, shape  # noqa: E402
from coremail_desktop.protocol.coremail import CoremailAdapter  # noqa: E402
from coremail_desktop.models import AccountConfig  # noqa: E402
from coremail_desktop.crypto.service import CryptoService  # noqa: E402
from probe.coremail_transport import decode_extended_json  # noqa: E402

CRLF = '\r\n'


def multipart(fields):
    boundary = 'Hongyan' + uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        chunks.append(f'--{boundary}{CRLF}Content-Disposition: form-data; name="{name}"{CRLF}{CRLF}'.encode() + value.encode('utf-8') + CRLF.encode())
    chunks.append(f'--{boundary}--{CRLF}'.encode())
    return b''.join(chunks), 'multipart/form-data; boundary=' + boundary


def main():
    password = ask('鸿雁 · 加密发送探测', f'{ACCOUNT} 邮箱密码（仅本次内存使用）:')
    if not password:
        return 130
    pfx_password = ask('鸿雁 · 证书', '个人证书 (.pfx) 密码（仅本次内存使用）:')
    if pfx_password is None:
        return 130
    crypto = CryptoService()
    crypto.unlock(str(PFX), pfx_password)
    del pfx_password
    adapter = CoremailAdapter()
    adapter.login(AccountConfig('primary', 'https://newmail.inspur.com/coremail/', ACCOUNT), password)
    del password
    session = adapter._session
    report = {'date': time.strftime('%Y-%m-%d %H:%M')}

    def call(func, params):
        raw, _ = session._fetch(Request(session._rpc_url(func), data=json.dumps(params, ensure_ascii=False).encode('utf-8'),
                                        headers={'Accept': 'text/x-json', 'Content-Type': 'text/x-json'}))
        return decode_extended_json(raw.decode('utf-8-sig'))

    try:
        listed = call('cert:listPublicKey', {'userList': [ACCOUNT]})
        entry = next(v for k, v in listed['var'].items() if k.casefold() == ACCOUNT)
        self_cert, _ = load_cert(entry['publicKey'])

        marker = 'HYF-' + uuid.uuid4().hex[:12]
        inner = EmailMessage()
        inner.set_content(f'加密发送验证 {marker}')
        inner.add_alternative(f'<p>加密发送验证 <b>{marker}</b></p>', subtype='html')
        envelope = (pkcs7.PKCS7EnvelopeBuilder().set_data(bytes(inner)).add_recipient(self_cert)
                    .set_content_encryption_algorithm(algorithms.AES256)
                    .encrypt(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary]))
        subject = f'鸿雁 加密发送验证 {marker}'
        outer = EmailMessage()
        outer['From'] = ACCOUNT
        outer['To'] = ACCOUNT
        outer['Subject'] = subject
        outer['Date'] = formatdate(localtime=True)
        outer['Message-ID'] = make_msgid(domain='inspur.com')
        outer['x-cm-smime'] = 'encrypted'
        outer.set_content(envelope, maintype='application', subtype='pkcs7-mime', cte='base64', disposition='attachment',
                          filename='smime.p7m', params={'smime-type': 'enveloped-data', 'name': 'smime.p7m'})
        attrs = {'account': ACCOUNT, 'to': [ACCOUNT], 'cc': [], 'bcc': [], 'subject': subject,
                 'isHtml': False, 'saveSentCopy': True, 'attachments': []}
        started = call('%mbox/compose', {'action': 'continue', 'returnInfo': True, 'attrs': attrs})
        compose_id = started.get('var') if isinstance(started.get('var'), str) else (started.get('var') or {}).get('id')
        report['continue'] = started.get('code')
        query = {'func': 'mbox:composeF', 'uploadMode': 'preEncodedData', 'sid': session.sid, 'id': compose_id,
                 'action': 'deliver', 'returnInfo': 'true', 'autosaveHitCounter': 'true'}
        body, ctype = multipart({'returnInfo': 'true', 'account': ACCOUNT, 'to': ACCOUNT, 'cc': '', 'bcc': '',
                                 'subject': subject, 'isHtml': 'false', 'saveSentCopy': 'true',
                                 'lettercontent': outer.as_string()})
        raw, _ = session._fetch(Request(urljoin(session.base_url, 'XT/jsp/mformdata.jsp') + '?' + urlencode(query),
                                        data=body, headers={'Content-Type': ctype, 'Accept': 'text/x-json'}))
        try:
            result = decode_extended_json(raw.decode('utf-8-sig'))
            report['deliver'] = {'code': result.get('code') if isinstance(result.get('code'), str) else '?',
                                 'shape': shape(result.get('var'))}
        except Exception:
            report['deliver'] = {'code': 'non-json', 'length': len(raw)}
            result = {}
        ok = False
        if result.get('code') == 'S_OK':
            for _ in range(18):
                time.sleep(5)
                rows = [m for m in adapter.list_messages('1', 0, 20) if marker in m.subject]
                if not rows:
                    continue
                message = adapter.fetch_mime(rows[0].remote_id)
                end = message.find(b'\r\n\r\n')
                head = message[:end].decode('latin-1') if end > 0 else ''
                top = re.search(r'(?im)^content-type:\s*([^;\r\n]+)', head)
                report['received'] = {'top_content_type': top.group(1).strip() if top else None,
                                      'enveloped_top_level': 'enveloped-data' in head.lower(),
                                      'x_cm_smime_kept': bool(re.search(r'(?im)^x-cm-smime:', head)),
                                      'server_flag_encrypted': bool(dict(rows[0].flags).get('encrypted'))}
                try:
                    plain = crypto.decrypt(message).mime_bytes
                    report['decrypted'] = {'ok': True, 'marker_found': marker.encode() in plain}
                except Exception as error:
                    report['decrypted'] = {'ok': False, 'code': getattr(error, 'code', type(error).__name__)}
                ok = report['received']['enveloped_top_level'] and report['decrypted'].get('marker_found', False)
                break
            else:
                report['received'] = 'not_arrived_in_90s'
        report['success'] = bool(ok)
    finally:
        adapter.close()
        crypto.lock()
    out = ROOT / 'analysis' / 'smime-send-probe4-2026-10-17.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
