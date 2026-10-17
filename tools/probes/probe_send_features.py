"""Shape probe for scheduled send, recall, read receipts and delivery status.

Read-only calls first, then a few messages to the account owner only:
  R   immediate send with requestReadReceipt -> delivery history before/after reading
  RC  immediate send, then mbox:recallMessage2 on the sent copy
  S1  %mbox/compose action=schedule, scheduleDate written in the request's tz header zone
  S2  mbox:composeF (pre-encoded, plain MIME) action=schedule
  C   a scheduled message far ahead, cancelled by re-saving it as an ordinary draft

Records only codes, key names, value types, counts, booleans and minute offsets.
No subjects, bodies, names or addresses are written. Test copies are moved to the
trash afterwards (reversible). The password is asked in a local masked dialog.
"""
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesHeaderParser
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.protocol.coremail import DesktopSession  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402
from probe.coremail_transport import CoremailError, decode_extended_json  # noqa: E402
from tools.probes.probe_daily_features import shape  # noqa: E402

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'
HEADER_TZ = timezone(timedelta(hours=3))  # The client sends tz="Asia/Riyadh" (UTC+3, no DST).
REPORT = {'date': time.strftime('%Y-%m-%d %H:%M'), 'machine_utc_offset_h': datetime.now().astimezone().utcoffset().total_seconds() / 3600}


def ask_password():
    import tkinter
    from tkinter import simpledialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        return simpledialog.askstring('鸿雁 · 发信功能探测', f'{ACCOUNT} 的邮箱密码（仅本次内存使用）:\n将给自己发几封测试邮件（定时/召回/回执）', show='*', parent=root)
    finally:
        root.destroy()


class Probe:
    def __init__(self, session):
        self.s = session

    def rpc(self, func, payload, tz=True):
        headers = {'Accept': 'text/x-json', 'Content-Type': 'text/x-json; tz="Asia/Riyadh"' if tz else 'text/x-json'}
        raw, _ = self.s._fetch(Request(self.s._rpc_url(func), data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers=headers))
        result = decode_extended_json(raw.decode('utf-8-sig'))
        return result if isinstance(result, dict) else {'code': '?'}

    def form(self, path, query, fields, multipart=False):
        url = urljoin(self.s.base_url, path) + '?' + urlencode({**query, 'sid': self.s.sid})
        if multipart:
            boundary = 'Hongyan' + uuid.uuid4().hex
            data = b''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode('ascii') + str(v).encode('utf-8') + b'\r\n' for k, v in fields.items()) + f'--{boundary}--\r\n'.encode('ascii')
            ctype = 'multipart/form-data; boundary=' + boundary
        else:
            data, ctype = urlencode(fields).encode('utf-8'), 'application/x-www-form-urlencoded'
        raw, _ = self.s._fetch(Request(url, data=data, headers={'Content-Type': ctype, 'Accept': 'text/x-json'}))
        result = decode_extended_json(raw.decode('utf-8-sig'))
        return result if isinstance(result, dict) else {'code': '?'}

    def summary(self, result, keep=()):
        code = result.get('code')
        out = {'code': code if isinstance(code, str) and code.replace('_', '').isalnum() else '?'}
        if code != 'S_OK':
            return out
        out['shape'] = shape({k: v for k, v in result.items() if k != 'code'})
        return out

    def list(self, fid, limit=30):
        r = self.rpc('mbox:listMessages', {'fid': fid, 'start': 0, 'limit': limit, 'order': 'date', 'desc': True, 'returnTotal': True})
        v = r.get('var')
        return v if isinstance(v, list) else []

    def find(self, fid, marker):
        return [m for m in self.list(fid) if marker in str(m.get('subject', ''))]


def attrs(subject, **extra):
    return {'account': ACCOUNT, 'to': [ACCOUNT], 'cc': [], 'bcc': [], 'subject': subject,
            'content': '<p>鸿雁发信功能探测，可删除。</p>', 'isHtml': True, 'saveSentCopy': True, 'attachments': [], **extra}


def send(p, subject, action='deliver', **extra):
    r = p.rpc('%mbox/compose', {'action': 'continue', 'returnInfo': True, 'attrs': attrs(subject)})
    cid = r.get('var', {}).get('id') if isinstance(r.get('var'), dict) else r.get('var')
    if not isinstance(cid, str):
        return {'continue': p.summary(r)}, None
    r = p.rpc('%mbox/compose', {'action': action, 'id': cid, 'returnInfo': True, 'autosaveHitCounter': True, 'attrs': attrs(subject, **extra)})
    return p.summary(r), r


def date_fields(item, intended_utc):
    """Minute offset between each date-like field and the intended moment, read in the header zone."""
    out = {}
    for key, value in item.items():
        if 'date' in key.lower() and isinstance(value, str):
            try:
                seen = datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=HEADER_TZ)
                out[key] = round((seen - intended_utc).total_seconds() / 60, 1)
            except ValueError:
                out[key] = 'unparsed'
    return out


def wait_inbox(p, markers, seconds):
    """markers: {label: (marker, submitted_monotonic)} -> minutes until each appears in the inbox."""
    arrived, deadline = {}, time.monotonic() + seconds
    while time.monotonic() < deadline and len(arrived) < len(markers):
        rows = p.list(1, 40)
        for label, (marker, started) in markers.items():
            if label not in arrived and any(marker in str(m.get('subject', '')) for m in rows):
                arrived[label] = round((time.monotonic() - started) / 60, 2)
        time.sleep(15)
    return {label: arrived.get(label, f'not_in_{seconds // 60}min') for label in markers}


def history(p, mid):
    r = p.rpc('user:listDeliveryHistory', {'mid': mid})
    out = p.summary(r)
    rows = r.get('var') if isinstance(r.get('var'), list) else []
    out['entries'] = [{'to_is_self': str(e.get('to', '')).casefold() == ACCOUNT.casefold(),
                       **{k: e.get(k) for k in ('result', 'rclResult', 'auditstat', 'flag') if isinstance(e.get(k), int)}} for e in rows if isinstance(e, dict)]
    return out


def run(p):
    run_id = uuid.uuid4().hex[:8]
    mk = lambda tag: f'HYP-{run_id}-{tag}'
    # Read-only -------------------------------------------------------------
    r = p.rpc('user:getAttrs', {'optionalAttrIds': ['time_zone', 'op_readreceipt', 'query_read_status', 'save_sent']})
    REPORT['user_attrs'] = p.summary(r)
    tzv = r.get('var', {}).get('time_zone') if isinstance(r.get('var'), dict) else None
    REPORT['user_time_zone'] = tzv if isinstance(tzv, (str, int)) and len(str(tzv)) < 40 else type(tzv).__name__
    sent = p.list(3, 5)
    REPORT['sent_row_keys'] = shape(sent)
    REPORT['sent_id_has_colon'] = [':' in str(m.get('id')) for m in sent]
    drafts = p.list(2, 50)
    REPORT['drafts_scheduled_count'] = sum(1 for m in drafts if isinstance(m.get('flags'), dict) and m['flags'].get('scheduleDelivery'))
    if sent:
        REPORT['history_latest_sent'] = history(p, sent[0]['id'])
        REPORT['getMessageInfos_sent'] = p.summary(p.rpc('mbox:getMessageInfos', {'ids': [sent[0]['id']]}))

    # R: read receipt + delivery status --------------------------------------
    started = {}
    REPORT['R_send'], raw_r = send(p, mk('R'), requestReadReceipt=True)
    started['R'] = (mk('R'), time.monotonic())
    # RC: recall --------------------------------------------------------------
    REPORT['RC_send'], raw_rc = send(p, mk('RC'))
    started['RC'] = (mk('RC'), time.monotonic())
    REPORT['deliver_var_keys'] = shape(raw_rc.get('var')) if raw_rc else None
    REPORT['arrival_immediate'] = wait_inbox(p, started, 180)

    rc_sent = p.find(3, mk('RC'))
    rc_in = p.find(1, mk('RC'))
    if rc_sent:
        mid = rc_sent[0]['id']
        REPORT['RC_history_before'] = history(p, mid)
        r = p.rpc('mbox:recallMessage2', {'mid': mid})
        out = p.summary(r)
        var = r.get('var')
        if isinstance(var, dict):
            out['states'] = {('self' if str(k).casefold() == ACCOUNT.casefold() else 'other'): v for k, v in var.items() if isinstance(v, (int, str)) and len(str(v)) < 20}
        REPORT['RC_recall'] = out
        time.sleep(20)
        REPORT['RC_inbox_copy_still_there'] = bool(p.find(1, mk('RC')))
        REPORT['RC_history_after'] = history(p, mid)
        after = p.find(3, mk('RC'))
        REPORT['RC_sent_row_after'] = {k: v for k, v in (after[0] if after else {}).items() if k in ('sndStatus', 'rclStatus', 'recallable')}
        REPORT['RC_inbox_before_recall'] = bool(rc_in)

    r_sent, r_in = p.find(3, mk('R')), p.find(1, mk('R'))
    if r_sent:
        mid = r_sent[0]['id']
        REPORT['R_sent_row'] = {k: v for k, v in r_sent[0].items() if k in ('sndStatus', 'rclStatus', 'recallable')}
        REPORT['R_sent_flags'] = sorted(k for k, v in (r_sent[0].get('flags') or {}).items() if v)
        REPORT['R_history_before_read'] = history(p, mid)
        if r_in:
            raw = p.s.get_message(r_in[0]['id'])
            headers = BytesHeaderParser(policy=policy.default).parsebytes(raw)
            REPORT['R_inbox_has_disposition_notification_to'] = headers.get('Disposition-Notification-To') is not None
            REPORT['R_inbox_flags'] = sorted(k for k, v in (r_in[0].get('flags') or {}).items() if v)
            REPORT['R_inbox_requestReadReceipt_field'] = r_in[0].get('requestReadReceipt') if isinstance(r_in[0].get('requestReadReceipt'), bool) else 'absent'
            REPORT['R_mark_read'] = p.summary(p.rpc('mbox:updateMessageInfos', {'ids': [r_in[0]['id']], 'attrs': {'flags': {'read': True}}}))
            REPORT['R_sendMDN'] = p.summary(p.rpc('mbox:sendMDN', {'id': r_in[0]['id'], 'dispositionInfo': {'mode': 'manual', 'type': 'displayed'}}))
            time.sleep(30)
            REPORT['R_history_after_read'] = history(p, mid)
            again = p.find(3, mk('R'))
            REPORT['R_sent_flags_after_read'] = sorted(k for k, v in ((again[0].get('flags') if again else None) or {}).items() if v)

    # S1 / S2 / C: scheduled send ---------------------------------------------
    target1 = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=3)
    REPORT['S1_schedule'], _ = send(p, mk('S1'), action='schedule', scheduleDate=target1.astimezone(HEADER_TZ).strftime('%Y-%m-%d %H:%M:%S'))
    s1_started = time.monotonic()
    time.sleep(5)
    rows = p.find(2, mk('S1'))
    REPORT['S1_in_drafts'] = len(rows)
    if rows:
        REPORT['S1_draft_flags'] = sorted(k for k, v in (rows[0].get('flags') or {}).items() if v)
        REPORT['S1_draft_date_offsets_min'] = date_fields(rows[0], target1)
        info = p.rpc('mbox:getMessageInfos', {'ids': [rows[0]['id']]})
        REPORT['S1_info'] = p.summary(info)
        v = info.get('var')
        if isinstance(v, list) and v and isinstance(v[0], dict):
            REPORT['S1_info_date_offsets_min'] = date_fields(v[0], target1)

    # S2: pre-encoded path, plain MIME (the encrypted path differs only in the payload).
    target2 = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=4)
    r = p.rpc('%mbox/compose', {'action': 'continue', 'returnInfo': True, 'attrs': {**attrs(mk('S2')), 'content': ''}})
    cid = r.get('var', {}).get('id') if isinstance(r.get('var'), dict) else r.get('var')
    msg = EmailMessage()
    msg['From'], msg['To'], msg['Subject'] = ACCOUNT, ACCOUNT, mk('S2')
    msg['Message-ID'] = f'<{uuid.uuid4().hex}@hongyan.local>'
    msg.set_content('鸿雁发信功能探测，可删除。')
    fields = {'returnInfo': 'true', 'account': ACCOUNT, 'to': ACCOUNT, 'cc': '', 'bcc': '', 'subject': mk('S2'), 'isHtml': 'false',
              'saveSentCopy': 'true', 'scheduleDate': target2.astimezone(HEADER_TZ).strftime('%Y-%m-%d %H:%M:%S'),
              'lettercontent': msg.as_bytes(policy=policy.SMTP).decode('utf-8')}
    query = {'func': 'mbox:composeF', 'uploadMode': 'preEncodedData', 'id': cid, 'action': 'schedule', 'returnInfo': 'true', 'autosaveHitCounter': 'true'}
    REPORT['S2_schedule'] = p.summary(p.form('XT/jsp/mformdata.jsp', query, fields, multipart=True))
    s2_started = time.monotonic()
    time.sleep(5)
    rows = p.find(2, mk('S2'))
    REPORT['S2_in_drafts'] = len(rows)
    if rows:
        REPORT['S2_draft_flags'] = sorted(k for k, v in (rows[0].get('flags') or {}).items() if v)
        REPORT['S2_draft_date_offsets_min'] = date_fields(rows[0], target2)

    # C: schedule far ahead, then cancel by reopening the draft and saving it unscheduled.
    target3 = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=45)
    REPORT['C_schedule'], _ = send(p, mk('C'), action='schedule', scheduleDate=target3.astimezone(HEADER_TZ).strftime('%Y-%m-%d %H:%M:%S'))
    time.sleep(5)
    rows = p.find(2, mk('C'))
    REPORT['C_in_drafts'] = len(rows)
    if rows:
        opened = p.form('XT/jsp/compose.jsp', {}, {'ctype': 'draft', 'mid': rows[0]['id'], 'stringifyScheduleDate': 'true'})
        REPORT['C_open_draft'] = p.summary(opened)
        var = opened.get('var') if isinstance(opened.get('var'), dict) else {}
        if isinstance(var.get('id'), str):
            keep = {k: var[k] for k in ('account', 'to', 'cc', 'bcc', 'subject', 'content', 'isHtml', 'saveSentCopy', 'priority', 'requestReadReceipt', 'attachments') if k in var}
            REPORT['C_open_date_offsets_min'] = date_fields(var, target3)
            saved = p.rpc('%mbox/compose', {'action': 'save', 'id': var['id'], 'returnInfo': True, 'attrs': {**keep, 'scheduleDate': None}})
            REPORT['C_save_unscheduled'] = p.summary(saved)
            time.sleep(5)
            rows = p.find(2, mk('C'))
            REPORT['C_after'] = {'count': len(rows), 'scheduled': [bool((m.get('flags') or {}).get('scheduleDelivery')) for m in rows]}

    REPORT['arrival_scheduled_min'] = wait_inbox(p, {'S1': (mk('S1'), s1_started), 'S2': (mk('S2'), s2_started)}, 540)
    REPORT['S1_after_in_drafts'] = len(p.find(2, mk('S1')))
    REPORT['S1_after_in_sent'] = len(p.find(3, mk('S1')))
    REPORT['S2_after_in_drafts'] = len(p.find(2, mk('S2')))
    REPORT['S2_after_in_sent'] = len(p.find(3, mk('S2')))
    s2_in = p.find(1, mk('S2'))
    if s2_in:
        raw = p.s.get_message(s2_in[0]['id'])
        REPORT['S2_inbox_message_id_kept'] = msg['Message-ID'] in raw.decode('utf-8', 'replace')

    # Cleanup: every test copy goes to the trash (reversible).
    ids = [m['id'] for fid in (1, 2, 3) for m in p.list(fid, 50) if f'HYP-{run_id}-' in str(m.get('subject', ''))]
    if ids:
        REPORT['cleanup'] = p.summary(p.rpc('mbox:updateMessageInfos', {'ids': ids, 'attrs': {'fid': 4}}))
        REPORT['cleanup_count'] = len(ids)


def main():
    password = ask_password()
    if not password:
        print('cancelled')
        return 130
    session = DesktopSession('https://newmail.inspur.com/coremail/')
    try:
        session.login(ACCOUNT, password)
        del password
        run(Probe(session))
    except (AppError, CoremailError) as error:
        REPORT['error'] = error.code
    finally:
        session.close()
        out = ROOT / 'analysis' / 'send-features-shape-2026-10-17.json'
        out.write_text(json.dumps(REPORT, ensure_ascii=False, indent=1), encoding='utf-8')
        print(json.dumps(REPORT, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
