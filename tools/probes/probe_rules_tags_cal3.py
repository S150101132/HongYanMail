"""Read-only: the shape of the user-made sample rule HY-SAMPLE and of a self-sent invitation.

The user created, in webmail, a disabled rule named HY-SAMPLE using the conditions
and actions the client needs (has attachment, size, mark read, flag, forward, tag),
and a meeting invitation to their own address. This probe records the stored rule's
structure (field/operator/type names, key names, value types; an operand only as its
length, unless it is a small number) and, for invitations among the newest inbox
messages sent by the account itself, the calendar part's structure and the
calendar block webmail's readMessage returns (key names and enum values only).
"""
import json
import re
import sys
import time
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.models import AccountConfig  # noqa: E402
from coremail_desktop.protocol.coremail import CoremailAdapter, DesktopSession  # noqa: E402
from tools.probes.probe_rules_tags_cal import attempt, calendar_parts, shape  # noqa: E402
from tools.probes.probe_rules_tags_cal2 import SAFE, action, condition  # noqa: E402
from tools.probes.probe_smime_send2 import ask  # noqa: E402

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'
EXTRA = {'user:getMailRules', 'mbox:listTags', 'mbox:getMessageInfos'}


def rich(value):
    """Like condition()/action() but keeps small numbers and nested key names."""
    if isinstance(value, dict):
        return {k: rich(v) if k not in ('operand', 'target', 'to', 'address', 'content', 'text') or not isinstance(v, str)
                else (f'str(len {len(v)})' + (' @' if '@' in v else '')) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [rich(v) for v in value[:6]]
    if isinstance(value, str):
        return value if SAFE.fullmatch(value) else f'str(len {len(value)})'
    return value


def main():
    password = ask('鸿雁 · 示例规则/会议邀请检查（只读）', f'{ACCOUNT} 邮箱密码（仅本次内存使用，不发信、不修改）:')
    if not password:
        return 130
    adapter = CoremailAdapter()
    adapter.login(AccountConfig('primary', 'https://newmail.inspur.com/coremail/', ACCOUNT), password)
    del password
    session = adapter._session
    session.ALLOWED = DesktopSession.ALLOWED | EXTRA
    report = {'date': time.strftime('%Y-%m-%d %H:%M')}
    try:
        rules = adapter._call('user:getMailRules', {}, True).get('var') or []
        report['rules_count'] = len(rules)
        sample = [r for r in rules if isinstance(r, dict) and str(r.get('name', '')).strip().upper() == 'HY-SAMPLE']
        report['sample_found'] = len(sample)
        report['sample'] = [{'keys': sorted(r), 'id_type': type(r.get('id')).__name__, 'continue': r.get('continue'), 'disabled': r.get('disabled'),
                             'operator': r.get('operator'), 'condictions_rich': rich(r.get('condictions')), 'conditions_rich': rich(r.get('conditions')),
                             'actions_rich': rich(r.get('actions')), 'other': {k: rich(v) for k, v in r.items() if k not in ('id', 'name', 'condictions', 'conditions', 'actions')}}
                            for r in sample]
        report['sample_conditions'] = [condition(c) for r in sample for c in (r.get('condictions') or r.get('conditions') or []) if isinstance(c, dict)]
        report['sample_actions'] = [action(a) for r in sample for a in r.get('actions') or [] if isinstance(a, dict)]
        listed = adapter._call('mbox:listMessages', {'fid': 1, 'start': 0, 'limit': 40, 'mode': 'count', 'order': 'date', 'desc': True,
                                                     'returnTotal': True, 'returnTag': True, 'summaryWindowSize': 0}, True).get('var') or []
        me = ACCOUNT.casefold()
        invites = []
        for row in listed:
            if not isinstance(row, dict) or len(invites) >= 3:
                continue
            ok, raw = attempt(lambda: adapter.fetch_mime(row['id']))
            if not ok:
                continue
            message = BytesParser(policy=policy.default).parsebytes(raw)
            if parseaddr(str(message.get('From', '')))[1].casefold() != me:
                continue
            parts = calendar_parts(message)
            if not parts:
                continue
            entry = {'parts': parts, 'row_keys': sorted(row), 'row_flags': sorted(row.get('flags') or {}),
                     'was_read': (row.get('flags') or {}).get('read') is True, 'mime_top': message.get_content_type(),
                     'mime_tree': [p.get_content_type() for p in message.walk()][:12]}
            url = urljoin(session.base_url, 'XT/jsp/mail.jsp') + '?' + urlencode({'func': 'readMessage', 'sid': session.sid})
            for name, form in (('mid', {'mid': row['id']}), ('id', {'id': row['id']})):
                ok, read = attempt(lambda: session._decode_response(session._fetch(Request(url, data=urlencode(form).encode('utf-8'),
                                                                                            headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'text/x-json'}))[0]))
                entry[f'jsp_read_{name}_ok'] = ok
                if ok:
                    var = read.get('var') if isinstance(read.get('var'), dict) else {}
                    mail = var.get('mail') if isinstance(var.get('mail'), dict) else {}
                    entry['jsp_var_keys'] = sorted(var)
                    entry['jsp_mail_has_calendar'] = 'calendar' in mail
                    cal = mail.get('calendar') if isinstance(mail.get('calendar'), dict) else var.get('calendar')
                    entry['calendar'] = rich({k: v for k, v in (cal or {}).items() if k not in ('meetingRequest',)}) if isinstance(cal, dict) else type(cal).__name__
                    mr = (cal or {}).get('meetingRequest') if isinstance(cal, dict) else None
                    entry['meetingRequest'] = shape(mr) if mr is not None else None
                    break
                entry[f'jsp_read_{name}'] = read
            ok, info = attempt(lambda: adapter._call('mbox:getMessageInfos', {'ids': [row['id']]}, True))
            entry['infos_keys'] = sorted((info.get('var') or [{}])[0]) if ok else info
            invites.append(entry)
        report['self_invites'] = invites
    finally:
        adapter.close()
    out = ROOT / 'analysis' / f'rules-cal-sample-shape-{time.strftime("%Y-%m-%d")}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
