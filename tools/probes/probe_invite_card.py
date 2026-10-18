"""Read-only: how the client sees the newest meeting invitation in the inbox.

Opens the newest inbox messages without marking them read, parses the text/calendar part
as the reader card does, and reads the server's calendar record for it (webmail's
readMessage; this is how an answer finds calFid/calUid). Records only method, booleans,
counts, key names and enum values -- no titles, places, people or text.
"""
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.app.backend import MailBackend  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402
from tools.probes.probe_smime_send2 import ask  # noqa: E402

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'
ENUM = re.compile(r'[A-Za-z_][A-Za-z0-9_.:-]{0,30}')


def meta_shape(meta):
    if not isinstance(meta, dict):
        return type(meta).__name__
    out = {}
    for k, v in sorted(meta.items()):
        if isinstance(v, (bool, int)) or v is None:
            out[k] = v
        elif isinstance(v, str):
            out[k] = v if ENUM.fullmatch(v) and k not in ('calUid', 'calFid') else f'str(len {len(v)})'
        elif isinstance(v, dict):
            out[k] = sorted(v)
        else:
            out[k] = type(v).__name__
    return out


def main():
    password = ask('鸿雁 · 会议邀请检查（只读）', f'{ACCOUNT} 邮箱密码（仅本次内存使用，不答复、不修改）:')
    if not password:
        return 130
    data = Path(tempfile.mkdtemp(prefix='hongyan-probe-', dir=str(ROOT / 'build')))
    backend = MailBackend(data)
    report = {'date': time.strftime('%Y-%m-%d %H:%M'), 'invites': []}
    try:
        backend.login('https://newmail.inspur.com/coremail/', ACCOUNT, password, False)
        del password
        backend.folders()
        rows = backend.messages('1', 0, 30)['rows']
        for row in rows:
            if len(report['invites']) >= 2:
                break
            try:
                m = backend.open_message(row['id'], mark_read=False)
            except AppError:
                continue
            card = m.get('invitation')
            if not card:
                continue
            entry = {'was_read': bool(row['flags'].get('read')), 'external_sender': not row['fromEmail'].lower().endswith('@inspur.com'),
                     'method': card['method'], 'cancelled': card['cancelled'], 'has_start': card['start'] is not None,
                     'has_end': card['end'] is not None, 'all_day': card['allDay'], 'floating': bool(card['floating']),
                     'tzid_kind': 'none' if not card['tzid'] else 'iana' if '/' in card['tzid'] else 'other',
                     'attendees': len(card['attendees']), 'me_listed': any(a['email'].lower() == ACCOUNT for a in card['attendees']),
                     'my_partstat': next((a['partstat'] for a in card['attendees'] if a['email'].lower() == ACCOUNT), None),
                     'has_organizer': bool(card['organizer']['email']), 'has_location': bool(card['location']),
                     'recurring': card['recurring'], 'can_answer': card['canAnswer'], 'ics_saveable': False}
            try:
                entry['ics_saveable'] = backend.attachment(row['id'], 'invite.ics')[1] == 'text/calendar'
            except AppError as error:
                entry['ics_error'] = error.code
            try:
                entry['server_record'] = meta_shape(backend.adapter.invitation_meta(row['id']))
            except AppError as error:
                entry['server_record'] = error.code
            entry['read_after'] = bool((backend.cache.summary(row['id']) or {}).get('flags', {}).get('read'))
            report['invites'].append(entry)
        report['scanned'] = len(rows)
    finally:
        backend.logout()
        shutil.rmtree(data, ignore_errors=True)
    out = ROOT / 'analysis' / f'invite-card-shape-{time.strftime("%Y-%m-%d")}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
