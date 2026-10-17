"""Real-account check of encrypted sending through the production backend path.

Sends one encrypted message to the account owner only, then opens the inbox
copy and the sent copy with the owner's certificate. Records only booleans,
codes and timings; the temporary cache folder is deleted afterwards.
"""
import json
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coremail_desktop.app.backend import MailBackend  # noqa: E402
from coremail_desktop.crypto.service import CryptoService  # noqa: E402
from tools.probe_smime_send2 import ACCOUNT, PFX, ask  # noqa: E402


def main():
    password = ask('鸿雁 · 加密发送验证', f'{ACCOUNT} 邮箱密码（仅本次内存使用）:\n将给自己发 1 封加密邮件')
    if not password:
        return 130
    pfx_password = ask('鸿雁 · 证书', '个人证书 (.pfx) 密码（仅本次内存使用）:')
    if pfx_password is None:
        return 130
    data = Path(tempfile.mkdtemp(prefix='hongyan-enc-', dir=str(ROOT / 'build')))
    report = {'date': time.strftime('%Y-%m-%d %H:%M')}
    backend = MailBackend(data, crypto=CryptoService())
    try:
        backend.login('https://newmail.inspur.com/coremail/', ACCOUNT, password, False)
        del password
        backend.unlock_certificate(str(PFX), pfx_password, remember=False)
        del pfx_password
        backend.folders()
        report['internal_domains'] = backend.profile()['internalDomains']
        report['self_has_directory_cert'] = backend.encryption_check([ACCOUNT])['missing'] == []
        marker = 'HYV-' + uuid.uuid4().hex[:12]
        draft = backend.compose('new')
        draft.update(to=[ACCOUNT], subject=f'鸿雁 加密邮件验证 {marker}', encrypt=True,
                     html=f'<p>这是一封<b>加密</b>验证邮件。</p><p style="color:#157f3c">{marker}</p>' + draft['html'])
        started = time.perf_counter()
        result = backend.send(draft)
        report['send'] = {'state': result['state'], 'failure': result.get('failure'), 'ms': round((time.perf_counter() - started) * 1000)}
        if result['state'] == 'Accepted':
            def find(folder):
                for _ in range(18):
                    rows = [r for r in backend.messages(folder, 0, 20)['rows'] if marker in r['subject']]
                    if rows:
                        return rows[0]
                    time.sleep(5)
            for label, folder in (('inbox', '1'), ('sent', next((f['id'] for f in backend.folders() if f['role'] == 'sent'), '3'))):
                row = find(folder)
                if row is None:
                    report[label] = 'not_found_in_90s'
                    continue
                message = backend.open_message(row['id'], mark_read=False)
                report[label] = {'server_flag_encrypted': bool(row['flags'].get('encrypted')),
                                 'encryption': message['encryption'],
                                 'marker_in_html': bool(message['html']) and marker in message['html'],
                                 'bold_kept': bool(message['html']) and '<b>' in message['html']}
    finally:
        backend.logout()
        if backend.crypto:
            backend.crypto.lock()
        shutil.rmtree(data, ignore_errors=True)
    out = ROOT / 'analysis' / 'encrypted-send-real-verification-2026-10-17.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    ok = all(isinstance(report.get(k), dict) and report[k].get('encryption') == 'decrypted' and report[k].get('marker_in_html')
             for k in ('inbox', 'sent'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
