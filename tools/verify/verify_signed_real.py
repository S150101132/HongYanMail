"""Real-account check of S/MIME signatures through the production backend path.

1. Describes the personal certificate (algorithms and usages only).
2. Verifies the signature of the real received message (邮箱发送测试.eml), read-only.
3. Sends to the account owner only: one signed+encrypted and one signed-only
   message, then opens the inbox and sent copies and verifies their signatures.

Records only booleans, codes, algorithm names and timings, never content or
addresses; the temporary cache folder is deleted afterwards.
"""
import json
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.app.backend import MailBackend  # noqa: E402
from coremail_desktop.crypto.service import CryptoService  # noqa: E402
from tools.probes.probe_smime_send2 import ACCOUNT, PFX, ask  # noqa: E402


def local_file(name):
    """The pfx and the sample message live in the main checkout, not in worktrees."""
    for base in (ROOT, *ROOT.parents):
        if (base / name).is_file():
            return base / name
    return ROOT / name


def describe_certificate(crypto):
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import rsa
    from coremail_desktop.crypto.signature import _issuer_name, can_sign
    cert, key, chain = crypto.signing_identity() if crypto.certificate_info else (None, None, ())
    if cert is None:
        return {'unlocked': False}
    info = {'key': 'rsa' if isinstance(key, rsa.RSAPrivateKey) else type(key).__name__, 'key_bits': key.key_size,
            'cert_signature_hash': cert.signature_hash_algorithm.name if cert.signature_hash_algorithm else None,
            'cert_signature_oid': cert.signature_algorithm_oid.dotted_string, 'chain_count': len(chain),
            'can_sign': can_sign(cert), 'email_matches_account': ACCOUNT.casefold() in crypto.certificate_info.emails,
            'issuer': _issuer_name(cert)}
    try:
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        info['key_usage'] = [name for name in ('digital_signature', 'content_commitment', 'key_encipherment')
                             if getattr(usage, name)]
    except x509.ExtensionNotFound:
        info['key_usage'] = None
    try:
        info['eku'] = [oid.dotted_string for oid in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value]
    except x509.ExtensionNotFound:
        info['eku'] = None
    return info


def badge(signature):
    if not signature:
        return None
    return {key: signature.get(key) for key in ('status', 'code', 'emailMatch', 'timeValid', 'directory', 'digest')}


def main():
    password = ask('鸿雁 · 签名验证', f'{ACCOUNT} 邮箱密码（仅本次内存使用）:\n将给自己发 2 封签名邮件（1 封签名+加密，1 封仅签名）')
    if not password:
        return 130
    pfx_password = ask('鸿雁 · 证书', '个人证书 (.pfx) 密码（仅本次内存使用）:')
    if pfx_password is None:
        return 130
    (ROOT / 'build').mkdir(exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix='hongyan-sig-', dir=str(ROOT / 'build')))
    report = {'date': time.strftime('%Y-%m-%d %H:%M')}
    backend = MailBackend(data, crypto=CryptoService())
    try:
        backend.login('https://newmail.inspur.com/coremail/', ACCOUNT, password, False)
        del password
        backend.unlock_certificate(str(local_file(PFX.name)), pfx_password, remember=False)
        del pfx_password
        report['certificate'] = describe_certificate(backend.crypto)
        sample = local_file('邮箱发送测试.eml')
        if sample.is_file():
            document = backend._parse(sample.read_bytes(), 'sample')
            report['received_sample'] = {'encryption': document.encryption_state,
                                         'signature': badge(document.signature and document.signature.to_dict())}
        backend.folders()
        sent_folder = next((f['id'] for f in backend.folders() if f['role'] == 'sent'), '3')
        for label, encrypt in (('signed_encrypted', True), ('signed_only', False)):
            marker = 'HYS-' + uuid.uuid4().hex[:12]
            draft = backend.compose('new')
            draft.update(to=[ACCOUNT], subject=f'鸿雁 签名验证 {marker}', encrypt=encrypt, sign=True,
                         html=f'<p>这是一封<b>数字签名</b>验证邮件。</p><p style="color:#157f3c">{marker}</p>' + draft['html'])
            started = time.perf_counter()
            result = backend.send(draft)
            entry = report[label] = {'send': {'state': result['state'], 'failure': result.get('failure'),
                                              'ms': round((time.perf_counter() - started) * 1000)}}
            if result['state'] != 'Accepted':
                continue

            def find(folder):
                for _ in range(18):
                    rows = [r for r in backend.messages(folder, 0, 20)['rows'] if marker in r['subject']]
                    if rows:
                        return rows[0]
                    time.sleep(5)
            for copy, folder in (('inbox', '1'), ('sent', sent_folder)):
                row = find(folder)
                if row is None:
                    entry[copy] = 'not_found_in_90s'
                    continue
                message = backend.open_message(row['id'], mark_read=False)
                entry[copy] = {'encryption': message['encryption'], 'signature': badge(message['signature']),
                               'marker_in_html': bool(message['html']) and marker in message['html'],
                               'attachments': len(message['attachments'])}
    finally:
        backend.logout()
        if backend.crypto:
            backend.crypto.lock()
        shutil.rmtree(data, ignore_errors=True)
    out = ROOT / 'analysis' / 'signed-send-real-verification-2026-10-17.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    ok = all(isinstance(report.get(label, {}).get(copy), dict)
             and (report[label][copy]['signature'] or {}).get('status') == 'valid'
             for label in ('signed_encrypted', 'signed_only') for copy in ('inbox', 'sent'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
