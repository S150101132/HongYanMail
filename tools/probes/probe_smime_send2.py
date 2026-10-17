"""Reproduce the native client's encrypted-send flow, sending to self only.

Flow observed in the official client's logs (redacted shapes):
  continue (attrs without body, extraHeaders incl. x-cm-smime) ->
  upload:prepare + upload:directData&inlined=true of smime.p7m -> deliver.
Recipient keys come from cert:listPublicKey {userList:[...]}.

Records only codes, shapes, booleans and content-type names. Both passwords are
asked in local dialogs and kept in memory only.
"""
import base64
import json
import re
import sys
import time
import uuid
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.ciphers import algorithms  # noqa: E402
from cryptography.hazmat.primitives.serialization import pkcs7  # noqa: E402
from coremail_desktop.protocol.coremail import CoremailAdapter  # noqa: E402
from coremail_desktop.models import AccountConfig  # noqa: E402
from coremail_desktop.crypto.service import CryptoService  # noqa: E402
from probe.coremail_transport import decode_extended_json  # noqa: E402

ACCOUNT = 'wangbiao@inspur.com'
PFX = ROOT / '王标-certificate.pfx'


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
        return {k if '@' not in k else '<email-key>': shape(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return {'<list>': len(value), **(shape(value[0], depth + 1) if value and isinstance(value[0], dict) else {})}
    return type(value).__name__


def load_cert(text):
    """Accept PEM, base64 DER or raw DER as served; report which."""
    if isinstance(text, str) and '-----BEGIN' in text:
        return x509.load_pem_x509_certificate(text.encode()), 'pem'
    raw = base64.b64decode(re.sub(r'\s+', '', text)) if isinstance(text, str) else text
    return x509.load_der_x509_certificate(raw), 'base64-der'


def main():
    password = ask('鸿雁 · 加密发送探测', f'{ACCOUNT} 邮箱密码（仅本次内存使用）:')
    if not password:
        return 130
    pfx_password = ask('鸿雁 · 证书', '个人证书 (.pfx) 密码（仅本次内存使用）:')
    if pfx_password is None:
        return 130
    crypto = CryptoService()
    own = crypto.unlock(str(PFX), pfx_password)
    del pfx_password
    report = {'date': time.strftime('%Y-%m-%d %H:%M'), 'pfx_can_encrypt': own.can_encrypt}
    adapter = CoremailAdapter()
    adapter.login(AccountConfig('primary', 'https://newmail.inspur.com/coremail/', ACCOUNT), password)
    del password
    session = adapter._session

    def call(func, params, url=None):
        raw, _ = session._fetch(Request(url or session._rpc_url(func), data=json.dumps(params, ensure_ascii=False).encode('utf-8'),
                                        headers={'Accept': 'text/x-json', 'Content-Type': 'text/x-json'}))
        return decode_extended_json(raw.decode('utf-8-sig'))

    try:
        got = call('cert:get', {})
        server_own, fmt = load_cert(got['var']['publicKey'])
        report['cert_get'] = {'format': fmt, 'matches_pfx': server_own.fingerprint(hashes.SHA256()).hex() == own.fingerprint}

        colleague = next((p['email'] for p in adapter.match_contacts('wang', 10)
                          if p['email'].lower().endswith('@inspur.com') and p['email'].lower() != ACCOUNT), None)
        listed = call('cert:listPublicKey', {'userList': [ACCOUNT] + ([colleague] if colleague else [])})
        report['list_public_key'] = {'code': listed.get('code'), 'shape': shape(listed.get('var'))}
        var = listed.get('var')
        entries = var if isinstance(var, list) else ([{'email': k, **(v if isinstance(v, dict) else {'publicKey': v})} for k, v in var.items()]
                                                      if isinstance(var, dict) else [])
        self_cert = None
        report['list_entries'] = []
        for entry in entries:
            text = entry.get('publicKey') or entry.get('cert') or entry.get('certificate')
            if not text:
                report['list_entries'].append({'has_key': False}); continue
            cert, fmt = load_cert(text)
            emails = []
            try:
                emails = [e.casefold() for e in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.RFC822Name)]
            except x509.ExtensionNotFound:
                pass
            is_self = cert.fingerprint(hashes.SHA256()).hex() == own.fingerprint
            report['list_entries'].append({'has_key': True, 'format': fmt, 'is_self_pfx': is_self,
                                           'cert_has_email': bool(emails), 'email_matches_request': any(e in (ACCOUNT, (colleague or '').casefold()) for e in emails)})
            if is_self:
                self_cert = cert
        if self_cert is None:
            self_cert = server_own  # Fall back to cert:get for the self-test.

        def send_variant(name, explicit_content_type, route='%mbox/compose'):
            marker = 'HYE-' + uuid.uuid4().hex[:12]
            inner = EmailMessage()
            inner.set_content(f'加密发送验证 {marker}')
            inner.add_alternative(f'<p>加密发送验证 <b>{marker}</b></p>', subtype='html')
            envelope = (pkcs7.PKCS7EnvelopeBuilder().set_data(bytes(inner)).add_recipient(self_cert)
                        .set_content_encryption_algorithm(algorithms.AES256)
                        .encrypt(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary]))
            headers = {'MIME-Version': '1.0', 'Content-Disposition': 'attachment; filename="smime.p7m"',
                       'Content-Transfer-Encoding': 'base64', 'x-cm-smime': 'encrypted'}
            if explicit_content_type:
                headers['Content-Type'] = 'application/pkcs7-mime; smime-type=enveloped-data; name="smime.p7m"'
            attrs = {'account': ACCOUNT, 'to': [ACCOUNT], 'cc': [], 'bcc': [], 'subject': f'鸿雁 加密发送验证 {marker}',
                     'isHtml': False, 'charset': 'utf-8', 'saveSentCopy': True, 'ctrls': {}, 'extraHeaders': headers, 'attachments': []}
            out = {}
            started = call(route, {'action': 'continue', 'returnInfo': True, 'attrs': attrs})
            compose_id = started.get('var') if isinstance(started.get('var'), str) else (started.get('var') or {}).get('id')
            out['continue'] = started.get('code')
            prep = call('upload:prepare', {'composeId': compose_id, 'attachmentId': -1, 'fileName': 'smime.p7m', 'displayName': 'smime.p7m',
                                           'contentType': 'application/pkcs7-mime; smime-type=enveloped-data; name="smime.p7m"', 'size': len(envelope)})
            out['prepare'] = prep.get('code')
            att_id = prep['var']['attachmentId']
            boundary = 'Hongyan' + uuid.uuid4().hex
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="smime.p7m"\r\n'
                    f'Content-Type: application/pkcs7-mime\r\n\r\n').encode() + envelope + f'\r\n--{boundary}--\r\n'.encode()
            url = urljoin(session.base_url, 'XT/jsp/upload.jsp') + '?' + urlencode({'func': 'directData', 'sid': session.sid, 'inlined': 'true',
                                                                                  'composeId': compose_id, 'attachmentId': att_id, 'offset': 0})
            raw, _ = session._fetch(Request(url, data=body, headers={'Content-Type': 'multipart/form-data; boundary=' + boundary, 'Accept': 'text/x-json'}))
            up = decode_extended_json(raw.decode('utf-8-sig'))
            out['upload'] = {'code': up.get('code'), 'complete': (up.get('var') or {}).get('actualSize') == len(envelope)}
            attrs['attachments'] = [{'id': att_id, 'type': 'upload', 'name': 'smime.p7m', 'size': len(envelope)}]
            sent = call(route, {'action': 'deliver', 'id': compose_id, 'returnInfo': True, 'autosaveHitCounter': True, 'attrs': attrs})
            out['deliver'] = sent.get('code')
            if sent.get('code') != 'S_OK':
                return out, False
            for _ in range(18):
                time.sleep(5)
                rows = [m for m in adapter.list_messages('1', 0, 20) if marker in m.subject]
                if not rows:
                    continue
                raw = adapter.fetch_mime(rows[0].remote_id)
                head = raw[:raw.find(b'\r\n\r\n')].decode('latin-1') if b'\r\n\r\n' in raw else ''
                ctype = re.search(r'(?im)^content-type:\s*([^;\r\n]+)', head)
                out['received'] = {'top_content_type': ctype.group(1).strip() if ctype else None,
                                   'enveloped_top_level': 'enveloped-data' in head.lower(),
                                   'x_cm_smime_kept': bool(re.search(r'(?im)^x-cm-smime:', head)),
                                   'server_flag_encrypted': bool(dict(rows[0].flags).get('encrypted'))}
                try:
                    plain = crypto.decrypt(raw).mime_bytes
                    out['decrypted'] = {'ok': True, 'marker_found': marker.encode() in plain}
                except Exception as error:
                    out['decrypted'] = {'ok': False, 'code': getattr(error, 'code', type(error).__name__)}
                return out, out['received']['enveloped_top_level'] and out['decrypted'].get('marker_found')
            out['received'] = 'not_arrived_in_90s'
            return out, False

        # Variants a/b (common/mbox/compose.jsp) were run on 2026-10-17 14:38: the
        # server ignored extraHeaders and wrapped smime.p7m in multipart/mixed.
        # The native client uses the JSON-RPC route, so try that.
        report['variant_c_rpc_native_headers'], ok = send_variant('c', False, 'mbox:compose')
        if not ok:
            report['variant_d_rpc_explicit_content_type'], ok = send_variant('d', True, 'mbox:compose')
        report['success'] = bool(ok)
    finally:
        adapter.close()
        crypto.lock()
    out = ROOT / 'analysis' / 'smime-send-probe3-2026-10-17.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
