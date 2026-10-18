"""Read-only follow-up to probe_rules_tags_cal: rule vocabulary, tag ids, listing by tag.

Records only structure: every rule condition's field/operator and flag keys, every
action's type and key names (a move target is a folder id), operand lengths and
comma counts; tag ids and colour codes (system values, not user text); per-tag
message counts from mbox:listMessages {fids, tags} as webmail lists a tag view
(X.03931.js ke()); auto-reply switches (mode, instation, welcome tip, title flag) and
the account time zone name. No rule operand, rule name, reply text or mail content.
"""
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.models import AccountConfig  # noqa: E402
from coremail_desktop.protocol.coremail import CoremailAdapter, DesktopSession  # noqa: E402
from tools.probes.probe_rules_tags_cal import attempt  # noqa: E402
from tools.probes.probe_smime_send2 import ask  # noqa: E402

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'
EXTRA = {'user:getMailRules', 'mbox:listTags', 'mbox:getMessageInfos'}
SAFE = re.compile(r'[A-Za-z_=<>!~:.()+-]{1,30}')


def condition(c):
    out = {k: (v if type(v) in (bool, int) or v is None else type(v).__name__) for k, v in sorted(c.items()) if k != 'operand'}
    for key in ('field', 'operator', 'type'):
        if isinstance(c.get(key), str):
            out[key] = c[key] if SAFE.fullmatch(c[key]) else f'str(len {len(c[key])})'
    operand = c.get('operand')
    if isinstance(operand, str):
        out['operand'] = {'len': len(operand), 'commas': operand.count(','), 'semicolons': operand.count(';'), 'digits_only': operand.isdigit(),
                          'has_at': '@' in operand}
    elif isinstance(operand, (int, list, dict)) or operand is None:
        out['operand'] = operand if type(operand) in (int,) or operand is None else type(operand).__name__
    return out


def action(a):
    out = {k: (v if type(v) in (bool, int) or v is None else type(v).__name__) for k, v in sorted(a.items())}
    if isinstance(a.get('type'), str) and SAFE.fullmatch(a['type']):
        out['type'] = a['type']
    return out


def main():
    password = ask('鸿雁 · 规则/标签检查（只读）', f'{ACCOUNT} 邮箱密码（仅本次内存使用，不发信、不修改）:')
    if not password:
        return 130
    adapter = CoremailAdapter()
    adapter.login(AccountConfig('primary', 'https://newmail.inspur.com/coremail/', ACCOUNT), password)
    del password
    session = adapter._session
    session.ALLOWED = DesktopSession.ALLOWED | EXTRA
    report = {'date': time.strftime('%Y-%m-%d %H:%M')}
    try:
        ok, rules = attempt(lambda: adapter._call('user:getMailRules', {}, True))
        report['rules'] = [{'id': r.get('id'), 'keys': sorted(r), 'continue': r.get('continue'), 'disabled': r.get('disabled'),
                            'operator': r.get('operator') if isinstance(r.get('operator'), str) and SAFE.fullmatch(r['operator']) else type(r.get('operator')).__name__,
                            'name_len': len(r.get('name') or ''),
                            'condictions': [condition(c) for c in r.get('condictions') or [] if isinstance(c, dict)],
                            'conditions': [condition(c) for c in r.get('conditions') or [] if isinstance(c, dict)],
                            'exceptions': {k: [condition(c) for c in v if isinstance(c, dict)] for k, v in r.items() if isinstance(v, list) and k not in ('condictions', 'conditions', 'actions')},
                            'actions': [action(a) for a in r.get('actions') or [] if isinstance(a, dict)]}
                           for r in (rules.get('var') or [] if ok else []) if isinstance(r, dict)] if ok else rules
        ok, attrs = attempt(lambda: adapter._call('user:getAttrs', {'optionalAttrIds': ['autorepmode', 'autorep_condition', 'autorep_welcometip', 'autoreptitle', 'time_zone']}, True))
        var = attrs.get('var') if ok and isinstance(attrs.get('var'), dict) else {}
        cond = var.get('autorep_condition') if isinstance(var.get('autorep_condition'), dict) else {}
        report['autorep'] = {'autorepmode': var.get('autorepmode'), 'welcometip': var.get('autorep_welcometip'), 'has_title': 'autoreptitle' in var,
                             'condition_keys': sorted(cond), 'instation': cond.get('instation'),
                             'begin_pattern': re.sub(r'\d', '9', str(cond.get('begintime', ''))), 'end_pattern': re.sub(r'\d', '9', str(cond.get('endtime', ''))),
                             'time_zone': var.get('time_zone') if isinstance(var.get('time_zone'), str) and SAFE.fullmatch(var['time_zone'].replace('/', '_')) else None}
        raw, _ = session._fetch(Request(urljoin(session.base_url, 'XT/index.jsp') + '?' + urlencode({'sid': session.sid})))
        text = raw[:4 * 1024 * 1024].decode('utf-8', errors='replace')
        for key in ('enableAutoRepTitle', 'supportMailTagged', 'supportCalendar', 'limitTagCount', 'enableAutoReply', 'limitFilterCount', 'maxMailRules'):
            match = re.search(r'["\']?' + key + r'["\']?\s*:\s*(true|false|-?\d+)', text)
            report.setdefault('page', {})[key] = match.group(1) if match else None
        ok, tags = attempt(lambda: adapter._call('mbox:listTags', {}, True))
        tag_rows = tags.get('var') if ok and isinstance(tags.get('var'), list) else []
        report['tags'] = [{'id': t[0] if isinstance(t[0], str) and t[0].isdigit() else f'str(len {len(str(t[0]))})',
                           'attr_keys': sorted(t[1]) if len(t) > 1 and isinstance(t[1], dict) else None,
                           'color': (lambda c: c if re.fullmatch(r'#?[0-9A-Za-z]{1,8}', str(c)) else type(c).__name__)(t[1].get('color'))
                           if len(t) > 1 and isinstance(t[1], dict) else None}
                          for t in tag_rows if isinstance(t, list) and t]
        folders = adapter._call('mbox:getAllFolders', {}, True).get('var') or []
        fids = [f['id'] for f in folders if isinstance(f, dict) and type(f.get('id')) is int and f['id'] != 4]
        report['fids_count'] = len(fids)
        per_tag = []
        for t in tag_rows[:12]:
            if not (isinstance(t, list) and t):
                continue
            ok, listed = attempt(lambda: adapter._call('mbox:listMessages', {'fids': fids, 'skipLockedFolders': True, 'tags': [t[0]], 'start': 0, 'limit': 20,
                                                                             'mode': 'count', 'order': 'date', 'desc': True, 'returnTotal': True, 'returnTag': True, 'summaryWindowSize': 0}, True))
            entry = {'ok': ok}
            if ok:
                rows = listed.get('var') if isinstance(listed.get('var'), list) else []
                entry.update(total=listed.get('total'), rows=len(rows), tag_field=sorted({k for r in rows if isinstance(r, dict) for k in r if 'tag' in k.lower()}),
                             tag_value=next((type(r['tag']).__name__ + (f"[{len(r['tag'])}]" if isinstance(r['tag'], list) else '') for r in rows if isinstance(r, dict) and 'tag' in r), None),
                             contains_id=all(isinstance(r.get('tag'), list) and t[0] in r['tag'] for r in rows if isinstance(r, dict)) if rows else None)
                if rows and isinstance(rows[0], dict):
                    ok2, info = attempt(lambda: adapter._call('mbox:getMessageInfos', {'ids': [rows[0]['id']], 'returnTag': True}, True))
                    entry['infos_tag'] = (lambda i: type(i.get('tag')).__name__ if isinstance(i, dict) else None)((info.get('var') or [None])[0]) if ok2 else info
            else:
                entry['code'] = listed
            per_tag.append(entry)
        report['per_tag'] = per_tag
        ok, plain = attempt(lambda: adapter._call('mbox:listMessages', {'fid': 1, 'tags': [], 'start': 0, 'limit': 5, 'returnTotal': True, 'returnTag': True}, True))
        report['empty_tags_ok'] = ok
    finally:
        adapter.close()
    out = ROOT / 'analysis' / f'rules-tags-shape2-{time.strftime("%Y-%m-%d")}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
