"""Real-account check of scheduled send, cancel, read receipts, delivery status and recall.

Everything goes through MailBackend (the app's own path) and only to the account
owner. Records codes, booleans, counts and minutes; no subjects, bodies or
addresses. Test copies are moved to the trash afterwards and the temporary
data folder is deleted.
"""
import json
import shutil
import sys
import tempfile
import time
import uuid
from email import policy
from email.parser import BytesHeaderParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.app.backend import MailBackend  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402
from tools.probes.probe_smime_send2 import ACCOUNT, ask  # noqa: E402

REPORT = {'date': time.strftime('%Y-%m-%d %H:%M')}


def code_of(fn, *args):
    try:
        fn(*args)
        return 'OK'
    except AppError as error:
        return error.code


def find(backend, folder, marker, tries=1):
    for attempt in range(tries):
        rows = [r for r in backend.messages(folder, 0, 40)['rows'] if marker in r['subject']]
        if rows or attempt == tries - 1:
            return rows
        time.sleep(10)


def main():
    password = ask('鸿雁 · 发信功能验证', f'{ACCOUNT} 邮箱密码（仅本次内存使用）:\n将给自己发 3 封测试邮件（定时/回执），结束后移到已删除')
    if not password:
        return 130
    data = Path(tempfile.mkdtemp(prefix='hongyan-send-', dir=str(ROOT / 'build')))
    backend = MailBackend(data)
    run = 'HYV-' + uuid.uuid4().hex[:8]
    try:
        backend.login('https://newmail.inspur.com/coremail/', ACCOUNT, password, False)
        del password
        backend.folders()

        def draft(tag, **extra):
            value = backend.compose('new')
            value.update({'to': [ACCOUNT], 'subject': f'鸿雁 发信功能验证 {run}-{tag}', 'encrypt': False, 'html': '<p>测试邮件，可删除。</p>', **extra})
            return value

        # Scheduled send, 4 minutes ahead, with a read receipt request.
        at = int((time.time() + 240) * 1000) // 1000 * 1000
        result = backend.send(draft('S', scheduleAt=at, receipt=True))
        started = time.monotonic()
        REPORT['schedule_send'] = {'state': result['state'], 'failure': result['failure'], 'scheduledAt_echo': result['scheduledAt'] == at}
        listed = [r for r in backend.scheduled()['rows'] if run in r['subject']]
        REPORT['scheduled_listed'] = len(listed)
        if listed:
            REPORT['scheduled_time_matches_s'] = abs((listed[0]['scheduleAt'] or 0) - at) / 1000
            REPORT['reader_shows_scheduled'] = backend.open_message(listed[0]['id'], mark_read=False)['scheduled']

        # Cancel: one scheduled an hour ahead, then unscheduled.
        result = backend.send(draft('C', scheduleAt=int((time.time() + 3600) * 1000)))
        REPORT['cancel_send'] = result['state']
        target = next((r for r in backend.scheduled()['rows'] if r['subject'].endswith(f'{run}-C')), None)
        if target:
            REPORT['unschedule'] = code_of(backend.unschedule, target['id'])
            REPORT['cancelled_gone_from_scheduled'] = not any(r['subject'].endswith(f'{run}-C') for r in backend.scheduled()['rows'])
            REPORT['cancelled_kept_as_draft'] = bool(find(backend, '2', f'{run}-C', 3))

        # Encrypted + scheduled is refused before anything is submitted.
        REPORT['encrypted_schedule'] = code_of(backend.send, draft('E', scheduleAt=int((time.time() + 3600) * 1000), encrypt=True))

        # Immediate send with a receipt request: header, delivery status, read state.
        result = backend.send(draft('R', receipt=True))
        REPORT['receipt_send'] = result['state']
        inbox = find(backend, '1', f'{run}-R', 9)
        sent = find(backend, '3', f'{run}-R', 9)
        if inbox:
            raw = backend.adapter.fetch_mime(inbox[0]['id'])
            REPORT['receipt_header'] = BytesHeaderParser(policy=policy.default).parsebytes(raw).get('Disposition-Notification-To') is not None
        if sent:
            status = backend.delivery_status(sent[0]['id'])
            REPORT['status_before_read'] = [r['kind'] for r in status['rows']]
            if inbox:
                backend.set_flags(inbox[0]['id'], read=True)
                time.sleep(20)
                REPORT['status_after_read'] = [r['kind'] for r in backend.delivery_status(sent[0]['id'])['rows']]
            REPORT['recall_self'] = code_of(backend.recall, sent[0]['id'])  # Expected refusal: own copy is not recallable.

        # Arrival of the scheduled message.
        arrived = None
        while time.monotonic() - started < 600:
            if find(backend, '1', f'{run}-S'):
                arrived = round((time.monotonic() - started) / 60, 2)
                break
            time.sleep(15)
        REPORT['scheduled_arrival_min'] = arrived if arrived is not None else 'not_in_10min'
        REPORT['scheduled_after_delivery_listed'] = any(run in r['subject'] and r['subject'].endswith('-S') for r in backend.scheduled()['rows'])
        got = find(backend, '1', f'{run}-S')
        if got:
            raw = backend.adapter.fetch_mime(got[0]['id'])
            REPORT['scheduled_receipt_header'] = BytesHeaderParser(policy=policy.default).parsebytes(raw).get('Disposition-Notification-To') is not None
    except AppError as error:
        REPORT['error'] = error.code
    finally:
        try:
            ids = [r['id'] for folder in ('1', '2', '3') for r in backend.messages(folder, 0, 60)['rows'] if run in r['subject']]
            if ids:
                REPORT['cleanup'] = len(backend.move(ids, '4')['done'])
        except AppError as error:
            REPORT['cleanup_error'] = error.code
        backend.logout()
        shutil.rmtree(data, ignore_errors=True)
        write()
    return 0 if REPORT.get('scheduled_arrival_min') not in (None, 'not_in_10min') and REPORT.get('unschedule') == 'OK' else 1


def write():
    out = ROOT / 'analysis' / 'send-features-real-verification-2026-10-17.json'
    out.write_text(json.dumps(REPORT, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(REPORT, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    sys.exit(main())
