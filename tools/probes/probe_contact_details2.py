"""Read-only follow-up: which directory attributes may be asked for, one at a time.

Round 1 (probe_contact_details.py) found a wide returnAttrs list refused with
FA_FORBIDDEN by both oab:listEx and oab:get, and the site card config (X.O.cardExt)
naming mobile_number and home_phone. This adds each candidate alone to the
verified listEx attributes and records the code and how many sampled people have
it non-empty; then checks oab:get {list, returnAttrs, forEmail} with the allowed
set and oab:getHeadImageData for head images. Only attribute names, counts, codes,
byte counts and image magic are written; never ids, names, addresses or numbers.
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.protocol.coremail import DesktopSession  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402
from probe.coremail_transport import CoremailError  # noqa: E402
from tools.probes.probe_daily_features import ask_password  # noqa: E402
from tools.probes.probe_contacts_search import code_of, rpc  # noqa: E402
from tools.probes.probe_contact_details import CANDIDATES, EXTRA, counts, fetch  # noqa: E402

BASE = ['@id', '@type', 'true_name', 'email', 'duty', '@location']


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
        tree, _ = rpc(session, 'oab:getDirectories', {})
        root = tree['var'][0]
        rid = str(root['id'])
        depts = []

        def walk(nodes):
            for n in nodes or []:
                if isinstance(n, dict):
                    depts.append(n)
                    walk(n.get('ou'))
        walk(root.get('ou'))
        # A few departments with people, found with the verified attribute list.
        dns = []
        for n in depts[::max(1, len(depts) // 40)]:
            result, _ = rpc(session, 'oab:listEx', {'dn': f"{rid}/{n['id']}", 'returnAttrs': BASE, 'start': 0, 'limit': 20})
            if code_of(result) == 'S_OK' and result.get('var'):
                dns.append(f"{rid}/{n['id']}")
            if len(dns) >= 4:
                break
        report['departments'] = len(dns)
        allowed, per_attr = [], {}
        for attr in [a for a in CANDIDATES if a not in BASE]:
            rows, codes = [], set()
            for dn in dns:
                result, _ = rpc(session, 'oab:listEx', {'dn': dn, 'returnAttrs': BASE + [attr], 'start': 0, 'limit': 20})
                codes.add(code_of(result))
                rows.extend(r for r in result.get('var') or [] if isinstance(r, dict))
            have = sum(1 for r in rows if r.get(attr) not in (None, '', [], {}))
            per_attr[attr] = {'codes': sorted(codes), 'people': len(rows), 'nonempty': have}
            if codes == {'S_OK'}:
                allowed.append(attr)
        report['listEx_single_attr'] = per_attr
        report['allowed'] = allowed
        sample = []
        for dn in dns:
            result, _ = rpc(session, 'oab:listEx', {'dn': dn, 'returnAttrs': BASE + allowed, 'start': 0, 'limit': 20})
            report.setdefault('listEx_allowed_codes', []).append(code_of(result))
            sample.extend(r for r in result.get('var') or [] if isinstance(r, dict))
        report['listEx_allowed'] = {'people': len(sample), 'nonempty_by_attr': counts(sample)}
        users = [r for r in sample if isinstance(r.get('email'), str) and '@' in r['email']][:20]
        for label, params in (('oab_get_allowed', {'list': [u['email'] for u in users], 'returnAttrs': BASE + allowed, 'forEmail': True}),
                              ('oab_get_one', {'list': [u['email'] for u in users[:1]], 'returnAttrs': BASE + allowed, 'forEmail': True}),
                              ('oab_get_ids', {'list': [u['@id'] for u in users if u.get('@id')], 'returnAttrs': BASE + allowed})):
            result, ms = rpc(session, 'oab:get', params)
            var = result.get('var')
            rows = [r for r in var if isinstance(r, dict)] if isinstance(var, list) else []
            same_case = sum(1 for r in rows if r.get('email') in params['list']) if label != 'oab_get_ids' else None
            report[label] = {'code': code_of(result), 'ms': ms, 'var_type': type(var).__name__, 'asked': len(params['list']),
                             'rows': len(rows), 'email_matches_exactly': same_case, 'nonempty_by_attr': counts(rows)}
        heads = {}
        for r in users[:10]:
            uid = str(r.get('@id') or '')
            for label, request in (
                    ('get_query_uid', Request(session._rpc_url('oab:getHeadImageData', uid=uid))),
                    ('post_uid', Request(session._rpc_url('oab:getHeadImageData'), data=json.dumps({'uid': uid}).encode(),
                                         headers={'Content-Type': 'text/x-json', 'Accept': 'text/x-json'})),
                    ('get_query_email', Request(session._rpc_url('oab:getHeadImageData', uid=r['email']))),
                    ('avatar_url', Request(urljoin(session.base_url, 'avatar/' + quote(',' + uid)) + '?' + urlencode({'sid': session.sid})))):
                key = json.dumps(fetch(session, request), sort_keys=True)
                heads.setdefault(label, {}).setdefault(key, 0)
                heads[label][key] += 1
        report['head_image'] = {label: [{**json.loads(k), 'people': v} for k, v in kinds.items()] for label, kinds in heads.items()}
    except (AppError, CoremailError) as error:
        report['login'] = error.code
    finally:
        session.close()
    out = ROOT / 'analysis' / f'contact-details-shape2-{datetime.now():%Y-%m-%d}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
