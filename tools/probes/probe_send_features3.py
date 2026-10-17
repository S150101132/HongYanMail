"""Third shape probe (send-to-self only): scheduled send through mbox:composeF.

probe_send_features2.py showed the JSON compose path accepts scheduleDate in the
account zone, but composeF (pre-encoded) answered FA_INVALID_DATE with the same
form field. First run: scheduleDate on the session, only on the session, or as a URL
parameter -> all FA_INVALID_DATE. Second run (VARIANTS below): the
stringifyScheduleDate flag webmail passes, and other encodings of the form field.
Then waits for arrival and checks the delivered MIME is the pre-encoded one.
Records only codes, counts, booleans and minutes.
"""
import json
import sys
import time
import uuid
from email import policy
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.protocol.coremail import DesktopSession  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402
from probe.coremail_transport import CoremailError  # noqa: E402
from tools.probes import probe_send_features as v1  # noqa: E402
from tools.probes.probe_send_features2 import Probe, SHANGHAI, ahead, fmt  # noqa: E402

ACCOUNT = v1.ACCOUNT
REPORT = {'date': time.strftime('%Y-%m-%d %H:%M')}


def _variants():
    target = ahead(4)
    text = fmt(target, SHANGHAI)
    return {'stringify_flag': ({'stringifyScheduleDate': 'true'}, {'scheduleDate': text}),
            'stringify_flag_session': ({'stringifyScheduleDate': 'true'}, {'scheduleDate': text}, True),
            'epoch_ms': ({}, {'scheduleDate': str(int(target.timestamp() * 1000))}),
            'iso_offset': ({}, {'scheduleDate': target.astimezone(SHANGHAI).isoformat()}),
            'iso_t': ({}, {'scheduleDate': text.replace(' ', 'T')})}


def attempt(p, name, marker):
    spec = _variants()[name]
    query_extra, form_extra, on_session = spec[0], spec[1], len(spec) > 2
    session_attrs = {**v1.attrs(marker), 'content': ''}
    if on_session:
        session_attrs['scheduleDate'] = form_extra['scheduleDate']
    r = p.rpc('%mbox/compose', {'action': 'continue', 'returnInfo': True, 'attrs': session_attrs})
    cid = r.get('var', {}).get('id') if isinstance(r.get('var'), dict) else r.get('var')
    msg = EmailMessage()
    msg['From'], msg['To'], msg['Subject'] = ACCOUNT, ACCOUNT, marker
    msg['Message-ID'] = f'<{uuid.uuid4().hex}@hongyan.local>'
    msg.set_content('鸿雁发信功能探测，可删除。')
    fields = {'returnInfo': 'true', 'account': ACCOUNT, 'to': ACCOUNT, 'cc': '', 'bcc': '', 'subject': marker, 'isHtml': 'false',
              'saveSentCopy': 'true', 'lettercontent': msg.as_bytes(policy=policy.SMTP).decode('utf-8')}
    fields.update(form_extra)
    query = {'func': 'mbox:composeF', 'uploadMode': 'preEncodedData', 'id': cid, 'action': 'schedule', 'returnInfo': 'true', 'autosaveHitCounter': 'true', **query_extra}
    return p.summary(p.form('XT/jsp/mformdata.jsp', query, fields, multipart=True)), msg['Message-ID']


def run(p, run_id):
    REPORT['tries'] = {}
    for name in _variants():
        marker = f'HYP-{run_id}-F-{name}'
        summary, message_id = attempt(p, name, marker)
        REPORT['tries'][name] = summary
        if summary['code'] != 'S_OK':
            continue
        started = time.monotonic()
        time.sleep(5)
        rows = p.find(2, marker)
        REPORT['in_drafts'] = len(rows)
        REPORT['draft_scheduled'] = [bool((m.get('flags') or {}).get('scheduleDelivery')) for m in rows]
        REPORT['arrival_min'] = p.retry(v1.wait_inbox, p, {name: (marker, started)}, 600)
        got = p.find(1, marker)
        if got:
            REPORT['delivered_message_id_kept'] = message_id in p.retry(p.s.get_message, got[0]['id']).decode('utf-8', 'replace')
        REPORT['sent_copies'] = len(p.find(3, marker))
        return


def main():
    password = v1.ask_password()
    if not password:
        print('cancelled')
        return 130
    session = DesktopSession('https://newmail.inspur.com/coremail/')
    run_id = uuid.uuid4().hex[:8]
    p = Probe(session)
    try:
        session.login(ACCOUNT, password)
        del password
        run(p, run_id)
    except (AppError, CoremailError) as error:
        REPORT['error'] = error.code
    finally:
        try:
            ids = [m['id'] for fid in (1, 2, 3) for m in p.list(fid, 60) if f'HYP-{run_id}-' in str(m.get('subject', ''))]
            if ids:
                REPORT['cleanup'] = {'count': len(ids), **p.summary(p.rpc('mbox:updateMessageInfos', {'ids': ids, 'attrs': {'fid': 4}}))}
        except (AppError, CoremailError) as error:
            REPORT['cleanup_error'] = error.code
        session.close()
        out = ROOT / 'analysis' / 'send-features-shape4-2026-10-17.json'
        out.write_text(json.dumps(REPORT, ensure_ascii=False, indent=1), encoding='utf-8')
        print(json.dumps(REPORT, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
