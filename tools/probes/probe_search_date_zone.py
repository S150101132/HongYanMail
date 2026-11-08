"""Read-only probe: in which zone does the web-form searchMessages write a row's date?

listMessages (JSON-RPC, tz="Asia/Riyadh" header) is the reference. The newest sent mails are
searched by subject through the form route three ways — as the app does now, with the tz
parameter on the Content-Type, and with a tz form field — and each match's date is compared
with its list date. Records only the hour offsets and counts; subjects, ids and dates stay
in memory. Writes analysis/search-date-zone-2026-11-08.json.
"""
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.errors import AppError  # noqa: E402
from coremail_desktop.protocol.coremail import DesktopSession  # noqa: E402
from tools.probes.probe_daily_features import ask_password  # noqa: E402

FORM = 'application/x-www-form-urlencoded'
WAYS = {'now': (FORM, {}), 'content_type_tz': (FORM + '; tz="Asia/Riyadh"', {}), 'form_tz': (FORM, {'tz': 'Asia/Riyadh'})}


def when(value):
    return datetime.strptime(str(value)[:19], '%Y-%m-%d %H:%M:%S')


def search(session, subject, content_type, extra):
    url = urljoin(session.base_url, 'XT/jsp/mail.jsp') + '?' + urlencode({'func': 'searchMessages', 'sid': session.sid})
    data = urlencode({'subject': subject, 'start': 0, 'limit': 20, 'summaryWindowSize': 0, **extra}).encode('utf-8')
    raw, _ = session._fetch(Request(url, data=data, headers={'Content-Type': content_type, 'Accept': 'text/x-json'}))
    rows = session._decode_response(raw).get('var')
    return rows if isinstance(rows, list) else []


def main():
    account = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'
    password = ask_password(account)
    if not password:
        print('cancelled')
        return 130
    session = DesktopSession('https://newmail.inspur.com/coremail/')
    report = {'read_only': True, 'reference': 'listMessages fid 3 with tz header'}
    try:
        session.login(account, password)
        del password
        listed = session.call('mbox:listMessages', {'fid': 3, 'start': 0, 'limit': 10, 'mode': 'count', 'order': 'date', 'desc': True,
                                                    'returnTotal': True, 'summaryWindowSize': 0})
        var = listed.get('var')
        rows = var if isinstance(var, list) else (var.get('messages') if isinstance(var, dict) else None) or []
        stamp = lambda r: r.get('date') or r.get('receivedDate')
        reference = {r['id']: (r.get('subject') or '', when(stamp(r))) for r in rows if isinstance(r, dict) and stamp(r)}
        report['listed'] = {'code': listed.get('code'), 'var': type(var).__name__, 'rows': len(rows), 'dated': len(reference),
                            'keys': sorted(rows[0]) if rows and isinstance(rows[0], dict) else []}
        for name, (content_type, extra) in WAYS.items():
            offsets, missed = Counter(), 0
            for mid, (subject, date) in list(reference.items())[:6]:
                try:
                    found = [r for r in search(session, subject[:200], content_type, extra) if r.get('id') == mid and stamp(r)]
                except AppError as error:
                    offsets['error:' + error.code] += 1
                    continue
                if not found:
                    missed += 1
                    continue
                offsets[str(round((when(stamp(found[0])) - date).total_seconds() / 3600, 2))] += 1
            report[name] = {'offset_hours': dict(offsets), 'not_found': missed}
    except AppError as error:
        report['login'] = error.code
    out = ROOT / 'analysis' / 'search-date-zone-2026-11-08.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
