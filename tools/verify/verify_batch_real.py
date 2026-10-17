"""Real-account check of multi-id mbox:updateMessageInfos (read/unread only).

Marks two already-read inbox messages unread in one call, confirms both changed
on the server, then restores them (one call, then per-id for anything left).
Records only counts and booleans, never message content or ids.
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.app.backend import MailBackend  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'


def ask_password():
    import tkinter
    from tkinter import simpledialog
    root = tkinter.Tk(); root.withdraw(); root.attributes('-topmost', True)
    try:
        return simpledialog.askstring('鸿雁 · 批量操作验证', f'{ACCOUNT} 的密码（仅本次内存使用）:\n把 2 封已读邮件标为未读，确认后立即恢复', show='*', parent=root)
    finally:
        root.destroy()


def main():
    password = ask_password()
    if not password:
        return 130
    data = Path(tempfile.mkdtemp(prefix='hongyan-batch-', dir=str(ROOT / 'build')))
    report = {'date': time.strftime('%Y-%m-%d %H:%M'), 'operation': 'mbox:updateMessageInfos ids:[2] attrs.flags.read'}
    backend = MailBackend(data)
    adapter = backend.adapter
    ids = []
    try:
        backend.login('https://newmail.inspur.com/coremail/', ACCOUNT, password, False)
        read_state = lambda: {r.remote_id: bool(dict(r.flags).get('read')) for r in adapter.list_messages('1', 0, 50)}
        before = read_state()
        ids = [mid for mid, read in before.items() if read][:2]
        report['candidates'] = len(ids)
        if len(ids) < 2:
            raise AppError('NOT_ENOUGH_READ_MESSAGES')
        report['unread_confirmed'] = len(adapter.set_flags_many(ids, {'read': False}))
        after = read_state()
        report['unread_applied'] = sum(after.get(mid) is False for mid in ids)
        report['read_confirmed'] = len(adapter.set_flags_many(ids, {'read': True}))
        restored = read_state()
        report['read_applied'] = sum(restored.get(mid) is True for mid in ids)
        report['others_unchanged'] = all(restored.get(mid) == read for mid, read in before.items() if mid not in ids and mid in restored)
        report['status'] = 'passed' if report['unread_applied'] == report['read_applied'] == 2 else 'failed'
    except AppError as error:
        report['status'], report['code'] = 'failed', error.code
    finally:
        try:  # Whatever happened above, both messages end up read again.
            if ids:
                state = {r.remote_id: bool(dict(r.flags).get('read')) for r in adapter.list_messages('1', 0, 50)}
                for mid in ids:
                    if state.get(mid) is not True:
                        adapter.set_message_flags(mid, {'read': True})
                        report['restored_per_id'] = report.get('restored_per_id', 0) + 1
        except AppError as error:
            report['restore_error'] = error.code
        backend.logout()
        shutil.rmtree(data, ignore_errors=True)
    out = ROOT / 'analysis' / 'batch-update-real-verification-2026-10-17.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report.get('status') == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
