"""Read-only probe: can mbox:searchMessages back an agent-style mailbox knowledge base?

Measures body/full-text search, field filters, date/folder scoping, pagination,
snippet availability, groupings and latency. Records only codes, totals, row
counts, key names, booleans and timings. Query words are generic Chinese terms;
no subjects, addresses, names or bodies are written. The password is asked in
a local masked dialog and kept in memory only.
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

WORDS = ['会议', '项目', '合同']
FTS_ALL = {'subject': 1, 'from': 1, 'to': 1, 'cc': 1, 'content': 1, 'attachname': 1}
BASE = {'start': 0, 'limit': 20, 'order': 'receivedDate', 'desc': True, 'returnTotal': True}


def variants(word, me, since_day, since_time):
    yield 'pattern', {**BASE, 'pattern': word}
    yield 'pattern+summary', {**BASE, 'pattern': word, 'summaryWindowSize': 200}
    for field in ('subject', 'content', 'attachname', 'from'):
        yield f'fts.{field}', {**BASE, 'pattern': word, 'fts': {'fields': {field: 1}}}
    yield 'fts.all', {**BASE, 'pattern': word, 'fts': {'fields': FTS_ALL}}
    for field in ('subject', 'content'):
        yield f'cond.{field}', {**BASE, 'conditions': [{'field': field, 'operator': 'contains', 'operand': word}]}
    yield 'fid=1', {**BASE, 'pattern': word, 'fid': 1}
    yield 'fid=3', {**BASE, 'pattern': word, 'fid': 3}
    yield 'recursive', {**BASE, 'pattern': word, 'recursive': True}
    for label, value in (('day', since_day), ('time', since_time)):
        yield f'since30.{label}', {**BASE, 'pattern': word,
                                   'conditions': [{'field': 'receivedDate', 'operator': '>=', 'operand': value}]}
    yield 'cond.from=me', {**BASE, 'pattern': word, 'conditions': [{'field': 'from', 'operator': 'contains', 'operand': me}]}
    yield 'groupings', {**BASE, 'pattern': word, 'groupings': {'fid': '', 'from': '', 'sentDate': ''}}
    yield 'page2', {**BASE, 'pattern': word, 'start': 20}


def rpc(session, params):
    started = time.perf_counter()
    raw, _ = session._fetch(Request(session._rpc_url('mbox:searchMessages'),
                                    data=json.dumps(params, ensure_ascii=False).encode('utf-8'),
                                    headers={'Accept': 'text/x-json', 'Content-Type': 'text/x-json'}))
    return decode_extended_json(raw.decode('utf-8-sig')), round((time.perf_counter() - started) * 1000)


def form(session, pattern):
    started = time.perf_counter()
    url = urljoin(session.base_url, 'XT/jsp/mail.jsp') + '?' + urlencode({'func': 'searchMessages', 'sid': session.sid})
    data = urlencode({'pattern': pattern, 'start': 0, 'limit': 20, 'summaryWindowSize': 0}).encode('utf-8')
    raw, _ = session._fetch(Request(url, data=data, headers={'Content-Type': 'application/x-www-form-urlencoded',
                                                             'Accept': 'text/x-json'}))
    return decode_extended_json(raw.decode('utf-8-sig')), round((time.perf_counter() - started) * 1000)


def measure(result, ms):
    code = result.get('code') if isinstance(result, dict) else None
    if code != 'S_OK':
        return {'code': code if isinstance(code, str) and code.isupper() else '?', 'ms': ms}
    rows = result.get('var') if isinstance(result.get('var'), list) else []
    keys = sorted({k for row in rows if isinstance(row, dict) for k in row})
    summaries = [row.get('summary') for row in rows if isinstance(row, dict) and isinstance(row.get('summary'), str)]
    out = {'code': code, 'ms': ms, 'total': result.get('total'), 'rows': len(rows), 'item_keys': keys,
           'ids': [row.get('id') for row in rows if isinstance(row, dict)]}
    if summaries:
        out['summary_rows'] = len(summaries)
        out['summary_avg_len'] = round(sum(map(len, summaries)) / len(summaries))
    if result.get('groupings') is not None:
        out['groupings_shape'] = shape(result['groupings'])
    if result.get('keywords') is not None:
        out['keywords_shape'] = shape(result['keywords'])
    return out


def main():
    account = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'
    password = ask_password(account)
    if not password:
        print('cancelled')
        return 130
    session = DesktopSession('https://newmail.inspur.com/coremail/')
    session.ALLOWED = session.ALLOWED | {'mbox:searchMessages'}
    now = datetime.now() - timedelta(days=30)
    report = {'read_only': True, 'words': len(WORDS)}
    try:
        session.login(account, password)
        del password
        folders = session.call('mbox:getAllFolders', {'stats': True}).get('var') or []
        report['mailbox'] = {'folders': len(folders),
                             'messages': sum(f.get('stats', {}).get('messageCount', 0) for f in folders if isinstance(f, dict)),
                             'bytes': sum(f.get('stats', {}).get('messageSize', 0) for f in folders if isinstance(f, dict))}
        for index, word in enumerate(WORDS):
            block = {}
            try:
                block['form'] = measure(*form(session, word))
            except (AppError, CoremailError) as error:
                block['form'] = {'code': error.code}
            for name, params in variants(word, account.split('@')[0], now.strftime('%Y-%m-%d'), now.strftime('%Y-%m-%d %H:%M:%S')):
                try:
                    block[name] = measure(*rpc(session, params))
                except (AppError, CoremailError) as error:
                    block[name] = {'code': error.code}
            first, second = block.get('pattern', {}).get('ids') or [], block.get('page2', {}).get('ids') or []
            block['page2_disjoint'] = bool(second) and not set(first) & set(second)
            for item in block.values():
                if isinstance(item, dict):
                    item.pop('ids', None)
            report[f'word{index}'] = block
        two = measure(*rpc(session, {**BASE, 'pattern': WORDS[0] + ' ' + WORDS[1]}))
        two.pop('ids', None)
        report['two_words'] = two
    except (AppError, CoremailError) as error:
        report['login'] = error.code
    finally:
        session.close()
    out = ROOT / 'analysis' / 'search-capability-2026-10-17.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print('\n已写入', out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
