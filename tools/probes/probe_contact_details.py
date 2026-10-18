"""Read-only probe for colleague details in the contacts view (v0.4).

Asks which per-person attributes the organization directory offers and how to
fetch a head image:
- oab:listEx with a wide returnAttrs list (webmail's card asks for duty, @ou,
  mobile_number, home_phone, company_phone, fax_number, address, ... plus the
  site's X.O.cardExt extras);
- oab:get {list, returnAttrs, forEmail} (webmail X.03931.js: the contact card
  lookup), with and without returnAttrs;
- oab:getHeadImageData {uid} (official client) and webmail's avatar/ URL.

The directory is colleagues' personal data: only attribute NAMES and how many
sampled people have each are written, plus codes, byte counts and image magic.
No ids, names, addresses, numbers or image bytes are written. The password is
asked in a local masked dialog and kept in memory only.
"""
import json
import re
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

EXTRA = {'oab:getDirectories', 'oab:listEx', 'oab:get', 'oab:getHeadImageData'}
CANDIDATES = ['@id', '@type', '@ou', '@location', '@duty', 'true_name', 'email', 'duty', 'gender', 'nick_name', 'alias_emails',
              'mobile_number', 'company_phone', 'home_phone', 'fax_number', 'telephone', 'phone', 'short_number', 'extension',
              'address', 'zipcode', 'company', 'province', 'city', 'country', 'remarks', 'user_id', 'birthday',
              'employee_number', 'employee_id', 'emp_no', 'job_number', 'staff_no', 'office', 'office_location', 'location',
              'work_address', 'manager', 'superior', 'leader', 'title', 'position', 'department', 'head_image',
              'security_level', 'sender_security_level', 'org_unit_id', 'fullPY', 'm']


def magic(raw):
    for sig, name in ((b'\x89PNG', 'png'), (b'\xff\xd8', 'jpeg'), (b'GIF8', 'gif'), (b'RIFF', 'webp?'), (b'{', 'json'), (b'<', 'html')):
        if raw.lstrip().startswith(sig):
            return name
    return 'other' if raw else 'empty'


def fetch(session, request):
    try:
        raw, _ = session._fetch(request)
        kind = magic(raw)
        out = {'bytes': len(raw), 'magic': kind}
        if kind == 'json':
            from probe.coremail_transport import decode_extended_json
            try:
                result = decode_extended_json(raw.decode('utf-8-sig'))
                out['code'] = code_of(result)
                var = result.get('var') if isinstance(result, dict) else None
                out['var_type'] = type(var).__name__
                if isinstance(var, dict):
                    out['var_keys'] = sorted(var)
                if isinstance(var, str):
                    out['var_len'] = len(var)
                    out['var_prefix_is_data_uri'] = var.startswith('data:')
                    out['var_magic_b64'] = {'iVBOR': 'png', '/9j/': 'jpeg', 'R0lG': 'gif'}.get(next((p for p in ('iVBOR', '/9j/', 'R0lG') if var.split(',')[-1].startswith(p)), ''), 'other')
            except Exception:
                out['code'] = 'undecodable'
        return out
    except (AppError, CoremailError) as error:
        return {'error': error.code}


def counts(rows, names=None):
    seen = {}
    for r in rows:
        for k, v in r.items():
            if v not in (None, '', [], {}):
                seen[k] = seen.get(k, 0) + 1
    return dict(sorted(seen.items()))


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
        # Site card extras (attribute names only).
        try:
            raw, _ = session._fetch(Request(urljoin(session.base_url, 'XT/index.jsp') + '?' + urlencode({'sid': session.sid})))
            page = raw[:4 * 1024 * 1024].decode('utf-8', errors='replace')
            m = re.search(r'cardExt["\']?\s*:\s*(\[[^\]]{0,2000}\]|"[^"]{0,500}"|\'[^\']{0,500}\')', page)
            report['cardExt'] = m.group(1)[:600] if m else None
            report['page_attr_mentions'] = sorted({a for a in CANDIDATES if re.search(r'[\'"]' + re.escape(a) + r'[\'"]', page)})
        except (AppError, CoremailError) as error:
            report['cardExt'] = error.code
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
        sample = []
        tried = 0
        for n in depts[::max(1, len(depts) // 25)]:
            tried += 1
            result, ms = rpc(session, 'oab:listEx', {'dn': f"{rid}/{n['id']}", 'returnAttrs': CANDIDATES, 'start': 0, 'limit': 8})
            if code_of(result) != 'S_OK':
                report.setdefault('listEx_codes', []).append(code_of(result))
                continue
            sample.extend(r for r in result.get('var') or [] if isinstance(r, dict))
            if len(sample) >= 120:
                break
        report['listEx_wide'] = {'departments_tried': tried, 'people': len(sample), 'nonempty_by_attr': counts(sample)}
        users = [r for r in sample if isinstance(r.get('email'), str) and '@' in r['email']][:40]
        emails = [r['email'] for r in users]
        for label, params in (('oab_get_wide', {'list': emails, 'returnAttrs': CANDIDATES, 'forEmail': True}),
                              ('oab_get_noattrs', {'list': emails, 'forEmail': True}),
                              ('oab_get_ids', {'list': [r['@id'] for r in users if r.get('@id')][:20], 'returnAttrs': ['true_name', 'email', 'mobile_number']})):
            result, ms = rpc(session, 'oab:get', params)
            var = result.get('var')
            rows = [r for r in var if isinstance(r, dict)] if isinstance(var, list) else []
            report[label] = {'code': code_of(result), 'ms': ms, 'var_type': type(var).__name__, 'asked': len(params['list']),
                             'rows': len(rows), 'nonempty_by_attr': counts(rows)}
        # Head image: official client oab:getHeadImageData uid=..., webmail avatar/<id>.
        heads = {}
        for r in users[:12]:
            uid = r.get('@id')
            for label, request in (
                    ('get_query_uid', Request(session._rpc_url('oab:getHeadImageData', uid=uid or ''))),
                    ('post_uid', Request(session._rpc_url('oab:getHeadImageData'), data=json.dumps({'uid': uid}).encode(), headers={'Content-Type': 'text/x-json', 'Accept': 'text/x-json'})),
                    ('get_query_email', Request(session._rpc_url('oab:getHeadImageData', uid=r['email']))),
                    ('avatar_url', Request(urljoin(session.base_url, 'avatar/' + quote(',' + str(uid or ''))) + '?' + urlencode({'sid': session.sid})))):
                out = fetch(session, request)
                key = json.dumps(out, sort_keys=True)
                heads.setdefault(label, {}).setdefault(key, 0)
                heads[label][key] += 1
        report['head_image'] = {label: [{**json.loads(k), 'people': v} for k, v in kinds.items()] for label, kinds in heads.items()}
    except (AppError, CoremailError) as error:
        report['login'] = error.code
    finally:
        session.close()
    out = ROOT / 'analysis' / f'contact-details-shape-{datetime.now():%Y-%m-%d}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
