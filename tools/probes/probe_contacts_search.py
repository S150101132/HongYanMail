"""Read-only probe for the contacts view and advanced search (v0.3).

Contacts: the oab:getDirectories tree shape, the dn format oab:listEx accepts for
a department (webmail builds it as '<root id>/<department id>'), and whether
personal contacts (pab:*) hold anything.
Search: which fields of webmail's advanced search form (XT/jsp/mail.jsp
func=searchMessages: subject, from, to, sentDate, receivedDate, attachType,
flagType, read, fid) the server applies. A filter counts as applied when its
total differs from the unfiltered total AND every returned row satisfies it.

Records only codes, totals, row counts, key names, booleans and timings. The org
directory is colleagues' personal data: no department ids or names, no people,
addresses, subjects or bodies are written. The password is asked in a local
masked dialog and kept in memory only.
"""
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.protocol.coremail import DesktopSession  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402
from probe.coremail_transport import CoremailError, decode_extended_json  # noqa: E402
from tools.probes.probe_daily_features import ask_password, shape  # noqa: E402

WORD = '会议'
EXTRA = {'oab:getDirectories', 'oab:listEx', 'oab:searchEx', 'pab:getAllGroups', 'pab:getContacts', 'pab:searchContacts'}


def code_of(result):
    code = result.get('code') if isinstance(result, dict) else None
    return code if isinstance(code, str) and code.replace('_', '').isalnum() and code.isupper() else '?'


def rpc(session, func, params):
    started = time.perf_counter()
    try:
        raw, _ = session._fetch(Request(session._rpc_url(func), data=json.dumps(params, ensure_ascii=False).encode('utf-8'),
                                        headers={'Accept': 'text/x-json', 'Content-Type': 'text/x-json'}))
        result = decode_extended_json(raw.decode('utf-8-sig'))
    except (AppError, CoremailError) as error:
        result = {'code': error.code}
    return result, round((time.perf_counter() - started) * 1000)


def form(session, params):
    started = time.perf_counter()
    url = urljoin(session.base_url, 'XT/jsp/mail.jsp') + '?' + urlencode({'func': 'searchMessages', 'sid': session.sid})
    data = urlencode({k: ('true' if v else 'false') if isinstance(v, bool) else v for k, v in
                      {'start': 0, 'limit': 50, 'summaryWindowSize': 0, **params}.items()}).encode('utf-8')
    try:
        raw, _ = session._fetch(Request(url, data=data, headers={'Content-Type': 'application/x-www-form-urlencoded',
                                                                 'Accept': 'text/x-json'}))
        result = decode_extended_json(raw.decode('utf-8-sig'))
    except (AppError, CoremailError) as error:
        result = {'code': error.code}
    return result, round((time.perf_counter() - started) * 1000)


# Contacts ------------------------------------------------------------------
def walk(nodes, depth=1):
    for node in nodes or []:
        if isinstance(node, dict):
            yield node, depth
            yield from walk(node.get('ou'), depth + 1)


def contacts(session, report):
    tree, ms = rpc(session, 'oab:getDirectories', {})
    report['dir.plain'] = {'code': code_of(tree), 'ms': ms}
    roots = tree.get('var') if isinstance(tree.get('var'), list) else []
    if not roots:
        return
    root = roots[0]
    nodes = list(walk(root.get('ou')))
    report['dir.plain'].update({'roots': len(roots), 'root_keys': sorted(root), 'root_id_has_slash': '/' in str(root.get('id')),
                                'nodes': len(nodes), 'max_depth': max((d for _, d in nodes), default=0),
                                'node_keys': sorted({k for n, _ in nodes for k in n}),
                                'node_id_has_slash': any('/' in str(n.get('id')) for n, _ in nodes),
                                'node_ids_unique': len({str(n.get('id')) for n, _ in nodes}) == len(nodes)})
    deep, ms = rpc(session, 'oab:getDirectories', {'dn': root.get('id'), 'attrIds': ['email'], 'needChildCount': True, 'depth': 3})
    deep_roots = deep.get('var') if isinstance(deep.get('var'), list) else []
    deep_nodes = [n for r in deep_roots if isinstance(r, dict) for n in walk(r.get('ou'))]
    report['dir.webmail'] = {'code': code_of(deep), 'ms': ms, 'roots': len(deep_roots), 'nodes': len(deep_nodes),
                             'node_keys': sorted({k for n, _ in deep_nodes for k in n}),
                             'has_dn': any('dn' in n for n, _ in deep_nodes),
                             'has_childCount': any('childCount' in n for n, _ in deep_nodes)}
    attrs = ['@id', '@type', 'true_name', 'email', '@location', 'duty']
    top = [n for n, d in nodes if d == 1][:4]
    second = [n for n, d in nodes if d == 2][:2]
    totals = {}
    for label, picked in (('d1', top), ('d2', second)):
        for index, node in enumerate(picked):
            for form_name, dn in (('root/id', f"{root.get('id')}/{node.get('id')}"), ('id', str(node.get('id')))):
                for cond_name, extra in (('plain', {}), ('XUL', {'condition': {'field': '@type', 'operator': '=', 'operand': ['X', 'U', 'L']}})):
                    result, ms = rpc(session, 'oab:listEx', {'dn': dn, 'returnAttrs': attrs, 'start': 0, 'limit': 50, **extra})
                    rows = result.get('var') if isinstance(result.get('var'), list) else []
                    rows = [r for r in rows if isinstance(r, dict)]
                    entry = {'code': code_of(result), 'ms': ms, 'total': result.get('total') if type(result.get('total')) is int else None,
                             'rows': len(rows), 'keys': sorted({k for r in rows for k in r}),
                             'types': sorted({str(r.get('@type')) for r in rows if isinstance(r.get('@type'), str) and len(r['@type']) <= 3}),
                             'with_email': sum(isinstance(r.get('email'), str) and '@' in r['email'] for r in rows),
                             'with_location': sum(isinstance(r.get('@location'), str) and bool(r['@location']) for r in rows),
                             'location_has_slash': any('/' in str(r.get('@location', '')) for r in rows)}
                    report[f'listEx.{label}.{index}.{form_name}.{cond_name}'] = entry
                    totals[(label, index, form_name, cond_name)] = entry['total']
    # Paging within one department
    if top:
        dn = f"{root.get('id')}/{top[0].get('id')}"
        a, _ = rpc(session, 'oab:listEx', {'dn': dn, 'returnAttrs': ['@id'], 'start': 0, 'limit': 5})
        b, _ = rpc(session, 'oab:listEx', {'dn': dn, 'returnAttrs': ['@id'], 'start': 5, 'limit': 5})
        ids = lambda r: [x.get('@id') for x in (r.get('var') or []) if isinstance(x, dict)]
        report['listEx.paging'] = {'p1': len(ids(a)), 'p2': len(ids(b)), 'disjoint': not set(ids(a)) & set(ids(b))}
    result, ms = rpc(session, 'oab:listEx', {'dn': root.get('id'), 'returnAttrs': ['@id', '@type'], 'start': 0, 'limit': 5})
    report['listEx.root'] = {'code': code_of(result), 'ms': ms, 'total': result.get('total') if type(result.get('total')) is int else None,
                             'rows': len(result.get('var') or []) if isinstance(result.get('var'), list) else None}
    for name, params in (('pab:getAllGroups', {'fullPY': True}), ('pab:searchContacts', {}), ('pab:searchContacts', {'pattern': ''}),
                         ('pab:searchContacts', {'returnAttrs': ['id', 'FN', 'EMAIL;PREF', 'groups']})):
        result, ms = rpc(session, name, params)
        rows = result.get('var') if isinstance(result.get('var'), list) else None
        report[f'{name} {sorted(params)}'] = {'code': code_of(result), 'ms': ms, 'rows': len(rows) if rows is not None else None,
                                             'shape': shape(rows[:20]) if rows else None}


# Search --------------------------------------------------------------------
def rows_of(result):
    rows = result.get('var') if isinstance(result.get('var'), list) else []
    return [r for r in rows if isinstance(r, dict)]


def day(text):
    try:
        return datetime.strptime(str(text)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def search(session, report, account):
    local = account.split('@')[0]
    domain = account.split('@')[-1].split('.')[0]
    today = datetime.now().date()
    a, b = (today - timedelta(days=60)).isoformat(), (today - timedelta(days=30)).isoformat()
    within = lambda field, lo, hi: (lambda r: day(r.get(field)) is not None and lo <= day(r.get(field)) <= hi)
    lo, hi = today - timedelta(days=60), today - timedelta(days=30)
    flag = lambda key, want: (lambda r: bool((r.get('flags') or {}).get(key)) is want)
    has = lambda field, term: (lambda r: term.casefold() in str(r.get(field, '')).casefold())
    cases = [
        ('subject', {'subject': WORD}, has('subject', WORD)),
        ('pattern', {'pattern': WORD}, None),
        ('from.domain', {'from': domain}, has('from', domain)),
        ('fromAddress.domain', {'fromAddress': domain}, has('from', domain)),
        ('to.me', {'to': local}, has('to', local)),
        ('dealings.me', {'dealings': account}, lambda r: has('to', local)(r) or has('from', local)(r)),
        ('dealings.domain', {'dealings': domain}, lambda r: has('to', domain)(r) or has('from', domain)(r)),
        ('sentDate.1m', {'sentDate': '1m'}, lambda r: day(r.get('sentDate')) is not None and day(r.get('sentDate')) >= today - timedelta(days=32)),
        ('sentDate.range', {'sentDate': f'{a}:{b} 23:59:59'}, within('sentDate', lo, hi)),
        ('sentDate.range_plain', {'sentDate': f'{a}:{b}'}, within('sentDate', lo, hi)),
        ('sentDate.from_only', {'sentDate': f'{b}:'}, lambda r: day(r.get('sentDate')) is not None and day(r.get('sentDate')) >= hi),
        ('receivedDate.range', {'receivedDate': f'{a}:{b} 23:59:59'}, within('receivedDate', lo, hi)),
        ('attachType.attached', {'attachType': 'attached'}, flag('attached', True)),
        ('attachType.noAttach', {'attachType': 'noAttach'}, flag('attached', False)),
        ('flagType.flagged', {'flagType': 'flagged'}, flag('flagged', True)),
        ('flagType.noflagged', {'flagType': 'noflagged'}, flag('flagged', False)),
        ('read.false', {'read': False}, flag('read', False)),
        ('read.true', {'read': True}, flag('read', True)),
        ('fid.1', {'fid': 1}, lambda r: str(r.get('fid')) == '1'),
        ('fid.3', {'fid': 3}, lambda r: str(r.get('fid')) == '3'),
        ('combo.pattern+fid1+attached', {'pattern': WORD, 'fid': 1, 'attachType': 'attached'},
         lambda r: str(r.get('fid')) == '1' and bool((r.get('flags') or {}).get('attached'))),
        ('combo.subject+range', {'subject': WORD, 'sentDate': f'{a}:{b} 23:59:59'}, lambda r: has('subject', WORD)(r) and within('sentDate', lo, hi)(r)),
        ('bogus.attachType', {'attachType': 'zzz'}, None),
    ]
    base, ms = form(session, {})
    baseline = base.get('total') if type(base.get('total')) is int else None
    report['search.baseline'] = {'code': code_of(base), 'ms': ms, 'total': baseline, 'rows': len(rows_of(base)),
                                 'item_keys': sorted({k for r in rows_of(base) for k in r})}
    for name, params, check in cases:
        result, ms = form(session, params)
        rows = rows_of(result)
        total = result.get('total') if type(result.get('total')) is int else None
        entry = {'code': code_of(result), 'ms': ms, 'total': total, 'rows': len(rows), 'narrowed': total is not None and baseline is not None and total < baseline}
        if check is not None:
            entry['all_rows_match'] = all(check(r) for r in rows)
            entry['applied'] = entry['narrowed'] and entry['all_rows_match'] or (total == 0 and entry['code'] == 'S_OK')
        report[f'search.{name}'] = entry
    p1, _ = form(session, {'attachType': 'attached', 'limit': 20})
    p2, _ = form(session, {'attachType': 'attached', 'start': 20, 'limit': 20})
    report['search.paging.attached'] = {'p1': len(rows_of(p1)), 'p2': len(rows_of(p2)),
                                        'disjoint': not {r.get('id') for r in rows_of(p1)} & {r.get('id') for r in rows_of(p2)}}


def main():
    account = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'
    password = ask_password(account)
    if not password:
        print('cancelled')
        return 130
    session = DesktopSession('https://newmail.inspur.com/coremail/')
    session.ALLOWED = session.ALLOWED | EXTRA
    report = {'read_only': True}
    try:
        session.login(account, password)
        del password
        contacts(session, report)
        search(session, report, account)
    except (AppError, CoremailError) as error:
        report['login'] = error.code
    finally:
        session.close()
    out = ROOT / 'analysis' / f'contacts-search-shape-{datetime.now():%Y-%m-%d}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print('\n已写入', out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
