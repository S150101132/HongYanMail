"""Read-only follow-up probe for mbox:searchMessages (round 2).

Answers three questions left open by probe_search_capability.py:
  A. what the JSON-RPC route returns per row, and whether start/limit page;
  B. whether a body snippet (summary) can be returned;
  C. whether the web form route accepts fid / conditions filters.
Records only codes, totals, row counts, overlap counts, key names, value types,
lengths and timings. No ids, subjects, addresses, names or bodies are written.
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
BASE = {'start': 0, 'limit': 20, 'order': 'receivedDate', 'desc': True, 'returnTotal': True}


def timed(session, request):
    started = time.perf_counter()
    raw, _ = session._fetch(request)
    return decode_extended_json(raw.decode('utf-8-sig')), round((time.perf_counter() - started) * 1000)


def rpc(session, func, params):
    return timed(session, Request(session._rpc_url(func), data=json.dumps(params, ensure_ascii=False).encode('utf-8'),
                                  headers={'Accept': 'text/x-json', 'Content-Type': 'text/x-json'}))


def form(session, params):
    url = urljoin(session.base_url, 'XT/jsp/mail.jsp') + '?' + urlencode({'func': 'searchMessages', 'sid': session.sid})
    data = urlencode({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                      for k, v in params.items()}).encode('utf-8')
    return timed(session, Request(url, data=data, headers={'Content-Type': 'application/x-www-form-urlencoded',
                                                           'Accept': 'text/x-json'}))


def row_id(row):
    if isinstance(row, dict):
        return str(row.get('id'))
    return row if isinstance(row, str) else json.dumps(row, ensure_ascii=False, sort_keys=True)


def measure(result, ms):
    """Returns (report, ids). ids stay in memory for overlap counting only."""
    code = result.get('code') if isinstance(result, dict) else None
    if code != 'S_OK':
        return {'code': code if isinstance(code, str) and code.isupper() else '?', 'ms': ms}, []
    rows = result.get('var') if isinstance(result.get('var'), list) else []
    out = {'code': code, 'ms': ms, 'total': result.get('total'), 'rows': len(rows),
           'response_keys': sorted(result), 'row_types': sorted({type(r).__name__ for r in rows})}
    dicts = [r for r in rows if isinstance(r, dict)]
    if dicts:
        out['item_keys'] = sorted({k for r in dicts for k in r})
        summaries = [r['summary'] for r in dicts if isinstance(r.get('summary'), str)]
        if summaries:
            out['summary_rows'] = len(summaries)
            out['summary_len_min_max'] = [min(map(len, summaries)), max(map(len, summaries))]
    strings = [r for r in rows if isinstance(r, str)]
    if strings:
        out['str_len_min_max'] = [min(map(len, strings)), max(map(len, strings))]
        out['str_has_colon'] = any(':' in s for s in strings)
    if rows and not dicts and not strings:
        out['row_shape'] = shape(rows[0])
    return out, [row_id(r) for r in rows]


def overlap(a, b):
    return len(set(a) & set(b))


def main():
    account = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'
    password = ask_password(account)
    if not password:
        print('cancelled')
        return 130
    session = DesktopSession('https://newmail.inspur.com/coremail/')
    session.ALLOWED = session.ALLOWED | {'mbox:searchMessages', 'mbox:getMessageInfos', 'mbox:getMessageInfo'}
    since = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    since_cond = [{'field': 'receivedDate', 'operator': '>=', 'operand': since}]
    report = {'read_only': True}
    ids = {}

    def run(name, call, *args):
        try:
            report[name], ids[name] = measure(*call(session, *args))
        except (AppError, CoremailError) as error:
            report[name], ids[name] = {'code': error.code}, []

    try:
        session.login(account, password)
        del password
        # A. JSON-RPC rows and paging
        run('A.rpc.p1', rpc, 'mbox:searchMessages', {**BASE, 'pattern': WORD})
        run('A.rpc.p2', rpc, 'mbox:searchMessages', {**BASE, 'pattern': WORD, 'start': 20})
        run('A.rpc.limit5', rpc, 'mbox:searchMessages', {**BASE, 'pattern': WORD, 'limit': 5})
        run('A.rpc.brief0', rpc, 'mbox:searchMessages', {**BASE, 'pattern': WORD, 'briefOnly': False})
        run('A.rpc.brief1', rpc, 'mbox:searchMessages', {**BASE, 'pattern': WORD, 'briefOnly': True})
        run('A.form.p1', form, {'pattern': WORD, 'start': 0, 'limit': 20, 'summaryWindowSize': 0})
        run('A.form.p2', form, {'pattern': WORD, 'start': 20, 'limit': 20, 'summaryWindowSize': 0})
        report['A.overlap'] = {'rpc_p1_p2': overlap(ids['A.rpc.p1'], ids['A.rpc.p2']),
                               'form_p1_p2': overlap(ids['A.form.p1'], ids['A.form.p2']),
                               'rpc_p1_vs_form_p1': overlap(ids['A.rpc.p1'], ids['A.form.p1']),
                               'rpc_brief0_vs_form_p1': overlap(ids['A.rpc.brief0'], ids['A.form.p1'])}
        # If RPC rows are plain ids, can they be turned into summaries in one batch call?
        rpc_strings = [i for i in ids['A.rpc.p1'] if not i.startswith(('{', '['))][:5]
        form_ids = ids['A.form.p1'][:5]
        sample = rpc_strings or form_ids
        if sample:
            run('A.getMessageInfos', rpc, 'mbox:getMessageInfos', {'ids': sample})
            run('A.getMessageInfo', rpc, 'mbox:getMessageInfo', {'id': sample[0]})
        # B. body snippet
        for size in (100, 300):
            run(f'B.form.summary{size}', form, {'pattern': WORD, 'start': 0, 'limit': 20, 'summaryWindowSize': size})
            run(f'B.rpc.summary{size}', rpc, 'mbox:searchMessages',
                {**BASE, 'pattern': WORD, 'briefOnly': False, 'summaryWindowSize': size})
        # C. form route filters (baseline total comes from A.form.p1)
        run('C.form.fid1', form, {'pattern': WORD, 'start': 0, 'limit': 20, 'fid': 1})
        run('C.form.fid3', form, {'pattern': WORD, 'start': 0, 'limit': 20, 'fid': 3})
        run('C.form.since30', form, {'pattern': WORD, 'start': 0, 'limit': 20, 'conditions': since_cond})
        run('C.form.order_asc', form, {'pattern': WORD, 'start': 0, 'limit': 20, 'order': 'receivedDate', 'desc': 'false'})
        run('C.form.var', form, {'var': {**BASE, 'pattern': WORD, 'fid': 3}})
        run('C.rpc.since30', rpc, 'mbox:searchMessages', {**BASE, 'pattern': WORD, 'conditions': since_cond})
        report['C.order_asc_same_set'] = overlap(ids['C.form.order_asc'], ids['A.form.p1'])
        report['C.since30_rpc_vs_form'] = overlap(ids['C.rpc.since30'], ids['C.form.since30'])
    except (AppError, CoremailError) as error:
        report['login'] = error.code
    finally:
        session.close()
    out = ROOT / 'analysis' / 'search-capability2-2026-10-17.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print('\n已写入', out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
