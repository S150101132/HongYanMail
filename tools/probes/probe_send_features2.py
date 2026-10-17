"""Second shape probe (send-to-self only), after probe_send_features.py showed:
scheduleDate written in UTC+3 -> FA_INVALID_DATE, recallMessage2 {mid} -> FA_UNSUPPORT_RECALL,
requestReadReceipt given only on deliver -> no receipt request.

  CL  move earlier HYP-* test copies to the trash
  RC  webmail's recall order: mbox:cancelDeliver {tinfo, mid}, then recallMessage2 {mid, tid}
  R   requestReadReceipt on both continue and deliver -> receipt header present?
  S1  scheduleDate in the account zone (time_zone attr), plus variants until one is accepted
  S2  the same through mbox:composeF (pre-encoded), C cancel by re-saving the draft unscheduled

Records only codes, key names, counts, booleans and minute offsets.
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.protocol.coremail import DesktopSession  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402
from probe.coremail_transport import CoremailError  # noqa: E402
from tools.probes import probe_send_features as v1  # noqa: E402

ACCOUNT = v1.ACCOUNT
SHANGHAI = timezone(timedelta(hours=8))  # The account's time_zone attr is Asia/Shanghai (no DST).
REPORT = {'date': time.strftime('%Y-%m-%d %H:%M')}


class Probe(v1.Probe):
    def retry(self, fn, *args, **kw):
        for attempt in range(4):
            try:
                return fn(*args, **kw)
            except (AppError, CoremailError) as error:
                if error.code not in ('TLS_ERROR', 'NETWORK_ERROR', 'TRANSPORT_ERROR') or attempt == 3:
                    raise
                time.sleep(5)

    def list(self, fid, limit=30):
        return self.retry(super().list, fid, limit)


def fmt(moment, zone):
    return moment.astimezone(zone).strftime('%Y-%m-%d %H:%M:%S')


def variants(target):
    return {'shanghai_str': {'scheduleDate': fmt(target, SHANGHAI)},
            'shanghai_str+tz': {'scheduleDate': fmt(target, SHANGHAI), 'scheduleTimeZone': 'Asia/Shanghai'},
            'riyadh_str+tz': {'scheduleDate': fmt(target, v1.HEADER_TZ), 'scheduleTimeZone': 'Asia/Riyadh'}}


def ahead(minutes):
    return datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=minutes)


def sent_row(p, marker):
    for _ in range(12):
        rows = p.find(3, marker)
        if rows:
            return rows[0]
        time.sleep(5)


def history(p, mid):
    r = p.rpc('user:listDeliveryHistory', {'mid': mid})
    rows = r.get('var') if isinstance(r.get('var'), list) else []
    return {'code': r.get('code'), 'entries': [{'to_is_self': str(e.get('to', '')).casefold() == ACCOUNT.casefold(),
            'recallable': e.get('recallable'), **{k: e.get(k) for k in ('result', 'rclResult', 'flag', 'msgType', 'sdcode') if isinstance(e.get(k), int)}}
            for e in rows if isinstance(e, dict)]}


def offsets(item, target, zone):
    out = {}
    for key, value in item.items():
        if 'date' in key.lower() and isinstance(value, str) and len(value) == 19:
            try:
                out[key] = round((datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=zone) - target).total_seconds() / 60, 1)
            except ValueError:
                out[key] = 'unparsed'
    return out


def run(p, run_id):
    mk = lambda tag: f'HYP-{run_id}-{tag}'
    # CL ----------------------------------------------------------------------
    old = [m['id'] for fid in (1, 2, 3) for m in p.list(fid, 60) if str(m.get('subject', '')).startswith('HYP-')]
    if old:
        REPORT['CL_old'] = {'count': len(old), **p.summary(p.rpc('mbox:updateMessageInfos', {'ids': old, 'attrs': {'fid': 4}}))}

    # RC ------------------------------------------------------------------------
    REPORT['RC_send'], raw = v1.send(p, mk('RC'))
    sent_tinfo = raw.get('sentTInfo') if raw else None
    saved = raw.get('savedSent') if raw and isinstance(raw.get('savedSent'), dict) else {}
    row = sent_row(p, mk('RC'))
    if row:
        mid = row['id']
        REPORT['RC_tinfo'] = {'row_has_tinfo': isinstance(row.get('tinfo'), str), 'row_equals_sentTInfo': row.get('tinfo') == sent_tinfo,
                              'saved_mid_matches_row': f"{saved.get('msid')}:{saved.get('mid')}" == mid}
        REPORT['RC_history_before'] = history(p, mid)
        tinfo = row.get('tinfo') or sent_tinfo
        if tinfo:
            REPORT['RC_cancelDeliver'] = p.summary(p.rpc('mbox:cancelDeliver', {'tinfo': tinfo, 'mid': mid}))
            r = p.rpc('mbox:recallMessage2', {'mid': mid, 'tid': tinfo.rsplit(':', 1)[-1]})
            out = p.summary(r)
            var = r.get('var')
            if isinstance(var, dict):
                out['states'] = {('self' if str(k).casefold() == ACCOUNT.casefold() else 'other'): v for k, v in var.items() if isinstance(v, int)}
            REPORT['RC_recall_with_tid'] = out
        time.sleep(15)
        REPORT['RC_inbox_copy_still_there'] = bool(p.find(1, mk('RC')))
        REPORT['RC_history_after'] = history(p, mid)

    # R -------------------------------------------------------------------------
    r = p.rpc('%mbox/compose', {'action': 'continue', 'returnInfo': True, 'attrs': v1.attrs(mk('R'), requestReadReceipt=True)})
    cid = r.get('var', {}).get('id') if isinstance(r.get('var'), dict) else r.get('var')
    r = p.rpc('%mbox/compose', {'action': 'deliver', 'id': cid, 'returnInfo': True, 'autosaveHitCounter': True, 'attrs': v1.attrs(mk('R'), requestReadReceipt=True)})
    REPORT['R_send'] = {'code': r.get('code')}
    row = sent_row(p, mk('R'))
    if row:
        headers = BytesHeaderParser(policy=policy.default).parsebytes(p.retry(p.s.get_message, row['id']))
        REPORT['R_sent_copy_has_dnt'] = headers.get('Disposition-Notification-To') is not None
        REPORT['R_sent_copy_dnt_is_self'] = ACCOUNT.casefold() in str(headers.get('Disposition-Notification-To') or '').casefold()
    time.sleep(5)
    inbox = p.find(1, mk('R'))
    if inbox:
        headers = BytesHeaderParser(policy=policy.default).parsebytes(p.retry(p.s.get_message, inbox[0]['id']))
        REPORT['R_inbox_copy_has_dnt'] = headers.get('Disposition-Notification-To') is not None
        info = p.rpc('mbox:readMessage', {'id': inbox[0]['id'], 'mode': 'text', 'markRead': False})
        v = info.get('var') if isinstance(info.get('var'), dict) else {}
        REPORT['R_readMessage'] = {'code': info.get('code'), 'requestReadReceipt': v.get('requestReadReceipt') if isinstance(v.get('requestReadReceipt'), bool) else type(v.get('requestReadReceipt')).__name__}

    # S1: plain compose path ------------------------------------------------------
    REPORT['S1_tries'] = {}
    chosen = s1_started = target1 = None
    for name in variants(ahead(4)):
        target1 = ahead(4)
        summary, _ = v1.send(p, mk('S1-' + name), action='schedule', **variants(target1)[name])
        REPORT['S1_tries'][name] = summary
        if summary['code'] == 'S_OK':
            chosen, s1_started = name, time.monotonic()
            break
    REPORT['S1_variant'] = chosen
    if chosen:
        time.sleep(5)
        rows = p.find(2, mk('S1-' + chosen))
        REPORT['S1_in_drafts'] = len(rows)
        if rows:
            REPORT['S1_draft_flags'] = sorted(k for k, v in (rows[0].get('flags') or {}).items() if v)
            REPORT['S1_draft_keys'] = sorted(rows[0])
            REPORT['S1_draft_offsets_min_as_shanghai'] = offsets(rows[0], target1, SHANGHAI)
            REPORT['S1_draft_offsets_min_as_riyadh'] = offsets(rows[0], target1, v1.HEADER_TZ)

    # S2: pre-encoded path --------------------------------------------------------
    s2_started = None
    target2 = ahead(5)
    r = p.rpc('%mbox/compose', {'action': 'continue', 'returnInfo': True, 'attrs': {**v1.attrs(mk('S2')), 'content': ''}})
    cid = r.get('var', {}).get('id') if isinstance(r.get('var'), dict) else r.get('var')
    msg = EmailMessage()
    msg['From'], msg['To'], msg['Subject'] = ACCOUNT, ACCOUNT, mk('S2')
    msg['Message-ID'] = f'<{uuid.uuid4().hex}@hongyan.local>'
    msg.set_content('鸿雁发信功能探测，可删除。')
    fields = {'returnInfo': 'true', 'account': ACCOUNT, 'to': ACCOUNT, 'cc': '', 'bcc': '', 'subject': mk('S2'), 'isHtml': 'false',
              'saveSentCopy': 'true', **variants(target2)[chosen or 'shanghai_str'], 'lettercontent': msg.as_bytes(policy=policy.SMTP).decode('utf-8')}
    query = {'func': 'mbox:composeF', 'uploadMode': 'preEncodedData', 'id': cid, 'action': 'schedule', 'returnInfo': 'true', 'autosaveHitCounter': 'true'}
    REPORT['S2_schedule'] = p.summary(p.form('XT/jsp/mformdata.jsp', query, fields, multipart=True))
    if REPORT['S2_schedule']['code'] == 'S_OK':
        s2_started = time.monotonic()
        time.sleep(5)
        rows = p.find(2, mk('S2'))
        REPORT['S2_in_drafts'] = len(rows)
        if rows:
            REPORT['S2_draft_flags'] = sorted(k for k, v in (rows[0].get('flags') or {}).items() if v)
            REPORT['S2_draft_offsets_min_as_shanghai'] = offsets(rows[0], target2, SHANGHAI)

    # C: schedule far ahead, cancel by re-saving the draft unscheduled --------------
    if chosen:
        REPORT['C_schedule'], _ = v1.send(p, mk('C'), action='schedule', **variants(ahead(50))[chosen])
        time.sleep(5)
        rows = p.find(2, mk('C'))
        REPORT['C_in_drafts'] = len(rows)
        if rows:
            opened = p.form('XT/jsp/compose.jsp', {}, {'ctype': 'draft', 'mid': rows[0]['id'], 'stringifyScheduleDate': 'true'})
            REPORT['C_open_draft'] = p.summary(opened)
            var = opened.get('var') if isinstance(opened.get('var'), dict) else {}
            if isinstance(var.get('id'), str):
                REPORT['C_open_has_scheduleDate'] = 'scheduleDate' in var
                keep = {k: var[k] for k in ('account', 'to', 'cc', 'bcc', 'subject', 'content', 'isHtml', 'saveSentCopy', 'priority', 'requestReadReceipt', 'attachments') if k in var}
                saved = p.rpc('%mbox/compose', {'action': 'save', 'id': var['id'], 'returnInfo': True, 'attrs': {**keep, 'scheduleDate': None}})
                REPORT['C_save_unscheduled'] = p.summary(saved)
                time.sleep(5)
                rows = p.find(2, mk('C'))
                REPORT['C_after'] = {'count': len(rows), 'scheduled': [bool((m.get('flags') or {}).get('scheduleDelivery')) for m in rows]}

    # Arrival of scheduled mail --------------------------------------------------
    waits = {}
    if s1_started:
        waits['S1'] = (mk('S1-' + chosen), s1_started)
    if s2_started:
        waits['S2'] = (mk('S2'), s2_started)
    if waits:
        REPORT['arrival_scheduled_min'] = p.retry(v1.wait_inbox, p, waits, 600)
        REPORT['S1_after'] = {'drafts': len(p.find(2, mk('S1'))), 'sent': len(p.find(3, mk('S1')))}
        REPORT['S2_after'] = {'drafts': len(p.find(2, mk('S2'))), 'sent': len(p.find(3, mk('S2')))}
        s2_in = p.find(1, mk('S2'))
        if s2_in:
            REPORT['S2_inbox_message_id_kept'] = msg['Message-ID'] in p.retry(p.s.get_message, s2_in[0]['id']).decode('utf-8', 'replace')


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
        out = ROOT / 'analysis' / 'send-features-shape2-2026-10-17.json'
        out.write_text(json.dumps(REPORT, ensure_ascii=False, indent=1), encoding='utf-8')
        print(json.dumps(REPORT, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
