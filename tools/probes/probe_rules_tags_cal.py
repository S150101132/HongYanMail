"""Read-only: mail rules, holiday auto-reply, tags and meeting invitations.

What webmail does (analysis/web-login):
- rules: X.03931.js module 88812 -- mr:list (user:getMailRules), user:addMailRules
  {items:[rule]}, user:updateMailRules {items:[rule]}, user:adjustMailRuleOrder.
- auto-reply: user:getAttrs {optionalAttrIds:[autorepmode, autorep_condition,
  autorep_welcometip, ...]}; written with user:setAttrs {attrs:{...}} (public-login.js
  updateUser). autorep_condition carries begintime/endtime.
- tags: mbox:listTags {mboxa} -> [[id, attrs], ...]; mbox:manageTags
  {add|update:[[id, attrs]]} / {delete:[id]}; mbox:updateMessageTags {ids, add, delete}.
- invitations: '!readMessage' returns mail.calendar {calFid, calUid,
  meetingRequestMethod, meetingRequestStatus, meetingRequest, attendees, ...};
  answered with cal:respond {fid: calFid, eventId: calUid, type: 2|3|4, inform}.

This probe only reads. It records key names, types, counts, booleans and short
identifier-like enum values -- never addresses, names, subjects, rule operands or
reply text. Only already-read messages are opened.
"""
import json
import re
import sys
import time
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.errors import AppError  # noqa: E402
from coremail_desktop.models import AccountConfig  # noqa: E402
from coremail_desktop.protocol.coremail import CoremailAdapter, DesktopSession  # noqa: E402
from tools.probes.probe_smime_send2 import ask  # noqa: E402

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'
EXTRA = {'user:getMailRules', 'mbox:listTags', 'cal:listCalFolder', 'mbox:readMessage', 'mbox:getMessageInfos'}
SECRET = re.compile(r'(?i)operand|value|content|text|tip|name|addr|email|subject|keyword|title|desc|summary|location|organizer|to$|from$|cc$|uid|calUid|target|des$|forward')
ENUM = re.compile(r'[A-Za-z_][A-Za-z0-9_.:-]{0,23}')
AUTOREP = ['autorepmode', 'autorep_condition', 'autorep_welcometip', 'autoreptext', 'autoreptitle', 'autorep_title', 'autorep_text',
           'autorep_content', 'autorep_inner', 'autorep_outer', 'autorep_external', 'autorep_internal', 'autorep_range', 'autoreply_on',
           'autorep_once', 'autorep_outside', 'autorep_scope', 'fltforward', 'forwarddes', 'time_zone']


def attempt(fn):
    try:
        return True, fn()
    except AppError as error:
        return False, error.code
    except Exception as error:  # noqa: BLE001 - shape probe: record the class only
        return False, type(error).__name__


def pattern(text):
    """A date-ish string as its digit pattern (2026-10-18 -> 9999-99-99)."""
    return re.sub(r'\d', '9', text)[:40]


def shape(value, key='', depth=0):
    if isinstance(value, dict) and depth < 6:
        return {(k if '@' not in k else '<email-key>'): shape(v, k, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        out = {'<list>': len(value)}
        if value:
            out['<item>'] = shape(value[0], key, depth + 1)
            if len(value) > 1 and isinstance(value[1], (dict, list)):
                out['<item1>'] = shape(value[1], key, depth + 1)
        return out
    if type(value) in (bool, int, float) or value is None:
        return value if not SECRET.search(key) or type(value) is bool else type(value).__name__
    if isinstance(value, str):
        if re.fullmatch(r'[\d\s:./TZ+-]{6,40}', value):
            return 'str:' + pattern(value)
        if not SECRET.search(key) and ENUM.fullmatch(value):
            return 'str=' + value
        return f'str(len {len(value)})'
    return type(value).__name__


def jsp(session, func, params, form=True):
    url = urljoin(session.base_url, 'XT/jsp/mail.jsp') + '?' + urlencode({'func': func, 'sid': session.sid})
    data = urlencode(params).encode('utf-8') if form else json.dumps(params).encode('utf-8')
    raw, _ = session._fetch(Request(url, data=data, headers={'Content-Type': 'application/x-www-form-urlencoded' if form else 'text/x-json', 'Accept': 'text/x-json'}))
    return session._decode_response(raw)


def page_flags(session):
    raw, _ = session._fetch(Request(urljoin(session.base_url, 'XT/index.jsp') + '?' + urlencode({'sid': session.sid})))
    text = raw[:4 * 1024 * 1024].decode('utf-8', errors='replace')
    out = {}
    for key in ('enableAutoReply', 'supportAutoRepTitle', 'limitTagCount', 'label0Count', 'enableInviteEvent', 'supportCalendar',
                'supportCalendar30', 'supportMailRule', 'enableMailRule', 'limitMailRuleCount', 'supportTag', 'enableTag',
                'supportAutoForward', 'enableAutoForward', 'supportAutoRepInner', 'supportAutoRepOuter'):
        match = re.search(r'["\']?' + key + r'["\']?\s*:\s*(true|false|-?\d+)', text)
        out[key] = (match.group(1) == 'true' if match.group(1) in ('true', 'false') else int(match.group(1))) if match else None
    out['other_autorep_keys'] = sorted(set(re.findall(r'\b(\w*[Aa]uto[Rr]ep\w*)\b', text)))[:30]
    out['other_rule_keys'] = sorted(set(re.findall(r'\b(\w*(?:MailRule|mailRule|Filter[A-Z]\w*)\w*)\b', text)))[:30]
    return out


def calendar_parts(message):
    found = []
    for part in message.walk():
        if part.get_content_type() == 'text/calendar' or (part.get_filename() or '').lower().endswith('.ics'):
            try:
                body = part.get_content()
            except Exception:  # noqa: BLE001
                body = ''
            if isinstance(body, bytes):
                body = body.decode('utf-8', errors='replace')
            method = re.search(r'(?im)^METHOD:([A-Z-]+)\s*$', body or '')
            found.append({'content_type': part.get_content_type(), 'param_method': part.get_param('method'),
                          'disposition': part.get_content_disposition(), 'method': method.group(1) if method else None,
                          'props': sorted(set(re.findall(r'(?m)^([A-Z][A-Z-]{1,30})[;:]', body or '')))[:60],
                          'has_tzid': 'TZID' in (body or ''), 'has_rrule': 'RRULE' in (body or '')})
    return found


def main():
    password = ask('鸿雁 · 规则/标签/会议邀请检查（只读）', f'{ACCOUNT} 邮箱密码（仅本次内存使用，不发信、不修改）:')
    if not password:
        return 130
    adapter = CoremailAdapter()
    adapter.login(AccountConfig('primary', 'https://newmail.inspur.com/coremail/', ACCOUNT), password)
    del password
    session = adapter._session
    session.ALLOWED = DesktopSession.ALLOWED | EXTRA
    report = {'date': time.strftime('%Y-%m-%d %H:%M')}
    try:
        report['page_flags'] = attempt(lambda: page_flags(session))
        # Rules
        ok, rules = attempt(lambda: adapter._call('user:getMailRules', {}, True))
        report['rules_ok'] = ok
        report['rules'] = shape(rules) if ok else rules
        if ok and isinstance(rules.get('var'), list):
            rows = rules['var']
            report['rules_count'] = len(rows)
            report['rule_keys'] = sorted({k for r in rows if isinstance(r, dict) for k in r})
            report['rule_items'] = [shape(r) for r in rows[:6]]
        # Auto-reply attributes, one by one so an unknown id cannot hide the others.
        ok, attrs = attempt(lambda: adapter._call('user:getAttrs', {'optionalAttrIds': AUTOREP}, True))
        report['autorep_ok'] = ok
        report['autorep'] = shape(attrs.get('var')) if ok else attrs
        single = {}
        for key in AUTOREP:
            ok, value = attempt(lambda: adapter._call('user:getAttrs', {'optionalAttrIds': [key]}, True))
            single[key] = (shape(value.get('var')) if ok else value)
        report['autorep_single'] = single
        ok, every = attempt(lambda: adapter._call('user:getAttrs', {}, True))
        if ok and isinstance(every.get('var'), dict):
            report['attrs_default_keys_matching'] = sorted(k for k in every['var'] if re.search(r'(?i)autorep|rule|forward|tag|cal|label|holiday', k))
            report['attrs_default_count'] = len(every['var'])
        else:
            report['attrs_default'] = every if not ok else shape(every)
        # Tags
        ok, tags = attempt(lambda: adapter._call('mbox:listTags', {}, True))
        report['tags_ok'] = ok
        report['tags'] = shape(tags) if ok else tags
        if ok and isinstance(tags.get('var'), list):
            report['tags_count'] = len(tags['var'])
            report['tag_attr_keys'] = sorted({k for t in tags['var'] if isinstance(t, list) and len(t) > 1 and isinstance(t[1], dict) for k in t[1]})
            report['tag_id_samples'] = [pattern(str(t[0])) if isinstance(t, list) and t else type(t).__name__ for t in tags['var'][:8]]
        # Tags on list rows
        ok, listed = attempt(lambda: adapter._call('mbox:listMessages', {'fid': 1, 'start': 0, 'limit': 100, 'mode': 'count', 'order': 'date', 'desc': True,
                                                                         'returnTotal': True, 'returnTag': True, 'summaryWindowSize': 0}, True))
        rows = listed.get('var') if ok else []
        rows = rows.get('messages') if isinstance(rows, dict) else rows
        rows = [r for r in rows or [] if isinstance(r, dict)]
        report['list_keys'] = sorted({k for r in rows for k in r})
        report['list_flag_keys'] = sorted({k for r in rows if isinstance(r.get('flags'), dict) for k in r['flags']})
        tagged = [r for r in rows if any(re.search(r'(?i)tag|label', k) for k in r)]
        report['list_tag_fields'] = {k: shape(r[k], 'x') for r in tagged[:5] for k in r if re.search(r'(?i)tag|label', k)}
        report['list_rows'] = len(rows)
        # Invitations: MIME of read messages, newest first.
        report['calendar_folders'] = (lambda r: shape(r[1]) if r[0] else r[1])(attempt(lambda: adapter._call('cal:listCalFolder', {}, True)))
        invites, scanned = [], 0
        for row in rows:
            if scanned >= 60 or len(invites) >= 4:
                break
            if not (isinstance(row.get('flags'), dict) and row['flags'].get('read') is True):
                continue
            scanned += 1
            ok, raw = attempt(lambda: adapter.fetch_mime(row['id']))
            if not ok:
                continue
            parts = calendar_parts(BytesParser(policy=policy.default).parsebytes(raw))
            if not parts:
                continue
            entry = {'parts': parts, 'row_flag_keys': sorted(row.get('flags') or {}), 'row_keys': sorted(row)}
            ok, info = attempt(lambda: adapter._call('mbox:getMessageInfos', {'ids': [row['id']]}, True))
            entry['infos'] = shape((info.get('var') or [None])[0]) if ok else info
            ok, read = attempt(lambda: adapter._call('mbox:readMessage', {'id': row['id']}, True))
            entry['rpc_read_ok'] = ok
            entry['rpc_read'] = shape(read.get('var')) if ok else read
            ok, read = attempt(lambda: jsp(session, 'readMessage', {'mid': row['id']}))
            entry['jsp_read_ok'] = ok
            if ok:
                var = read.get('var') if isinstance(read.get('var'), dict) else {}
                entry['jsp_read_keys'] = sorted(var)
                entry['jsp_calendar'] = shape((var.get('mail') or {}).get('calendar') if isinstance(var.get('mail'), dict) else var.get('calendar'))
            else:
                entry['jsp_read'] = read
            invites.append(entry)
        report['invites_scanned'] = scanned
        report['invites'] = invites
    finally:
        adapter.close()
    out = ROOT / 'analysis' / f'rules-tags-cal-shape-{time.strftime("%Y-%m-%d")}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
