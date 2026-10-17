"""Real-account check of the daily-use backend (read-only plus one HTML send-to-self).

Records only pass/fail, counts and timings. Opens mail without marking it read.
The cache for this run lives in a temporary folder that is deleted afterwards.
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
from coremail_desktop.errors import AppError  # noqa: E402

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'
SEND = '--no-send' not in sys.argv


def ask_password():
    import tkinter
    from tkinter import simpledialog
    root = tkinter.Tk(); root.withdraw(); root.attributes('-topmost', True)
    try:
        return simpledialog.askstring('鸿雁 · 真实账号验证', f'{ACCOUNT} 的密码（仅本次内存使用）:\n只读检查 + 给自己发 1 封 HTML 测试信', show='*', parent=root)
    finally:
        root.destroy()


def main():
    password = ask_password()
    if not password:
        return 130
    data = Path(tempfile.mkdtemp(prefix='hongyan-verify-', dir=str(ROOT / 'build')))
    report = {'date': time.strftime('%Y-%m-%d %H:%M'), 'checks': {}}
    checks = report['checks']

    def check(name, fn):
        start = time.perf_counter()
        try:
            value = fn()
            checks[name] = {'status': 'passed', 'ms': round((time.perf_counter() - start) * 1000), **(value or {})}
        except AppError as error:
            checks[name] = {'status': 'failed', 'code': error.code}
        except Exception as error:  # Only the type name; no message text.
            checks[name] = {'status': 'failed', 'code': type(error).__name__}
        return checks[name]['status'] == 'passed'

    backend = MailBackend(data)
    try:
        if not check('login', lambda: {'identities': len(backend.login('https://newmail.inspur.com/coremail/', ACCOUNT, password, False)['identities'])}):
            return 2
        state = {}

        def folders():
            f = backend.folders()
            state['folders'] = f
            return {'folders': len(f), 'roles': sorted({x['role'] for x in f if x['role']}),
                    'unread_known': all(isinstance(x['unread'], int) for x in f)}
        check('folders_with_unread', folders)

        def listing():
            page = backend.messages('1', 0, 50)
            state['rows'] = page['rows']
            return {'rows': len(page['rows']), 'with_preview': sum(bool(r['preview']) for r in page['rows']),
                    'total_known': page['total'] is not None, 'more': page['more']}
        check('inbox_page', listing)
        check('second_page', lambda: {'rows': len(backend.messages('1', 50, 50)['rows'])})
        check('cache_first_paint', lambda: {'rows': len(backend.cached_view('1')['rows'])})

        def reading():
            opened = 0; html = 0; attachments = 0; encrypted = 0
            for row in state['rows'][:8]:
                m = backend.open_message(row['id'], mark_read=False)
                opened += 1; html += bool(m['html']); attachments += len(m['attachments'])
                encrypted += m['encryption'] != 'none'
            unchanged = all(backend.cache.summary(r['id'])['flags'].get('read') == r['flags'].get('read') for r in state['rows'][:8])
            return {'opened': opened, 'html_bodies': html, 'attachments': attachments, 'encrypted': encrypted, 'read_state_unchanged': unchanged}
        check('open_messages', reading)
        check('sent_folder', lambda: {'rows': len(backend.messages(next((f['id'] for f in state['folders'] if f['role'] == 'sent'), '3'), 0, 50)['rows'])})
        check('local_search', lambda: {'hits': len(backend.search_local(ACCOUNT.split('@')[0][:2]))})
        check('server_search', lambda: {'total': backend.search_server('会议')['total'] >= 0})
        check('suggest_directory', lambda: {'results': len(backend.suggest('wang')),
                                            'directory': sum(r['source'] == 'directory' for r in backend.suggest('wang'))})
        check('poll', lambda: {'new': len(backend.poll()['new'])})

        if SEND:
            marker = 'HY-' + uuid.uuid4().hex[:12]

            def send():
                draft = backend.compose('new')
                draft.update(to=[ACCOUNT], subject=f'鸿雁 HTML 发送验证 {marker}',
                             html=f'<p>这是<b>鸿雁</b>自动发给自己的验证邮件。</p><p style="color:#2563eb">{marker}</p>' + draft['html'])
                result = backend.send(draft)
                state['send'] = result['state']
                return {'state': result['state']}
            if check('send_html_to_self', send) and state.get('send') == 'Accepted':
                def arrival():
                    for _ in range(18):
                        time.sleep(5)
                        for row in backend.messages('1', 0, 20)['rows']:
                            if marker in row['subject']:
                                m = backend.open_message(row['id'], mark_read=False)
                                return {'arrived': True, 'html_kept': bool(m['html']) and marker in m['html'] and '<b>' in m['html']}
                    raise AppError('NOT_DELIVERED_IN_90S')
                check('html_delivery', arrival)
    finally:
        backend.logout()
        shutil.rmtree(data, ignore_errors=True)
    out = ROOT / 'analysis' / 'daily-client-real-verification-2026-10-17.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if all(c['status'] == 'passed' for c in checks.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
