"""Read-only follow-up for the contacts view: nested department dn format and paging.

Round 1 (probe_contacts_search.py) showed oab:listEx answers '<root>/<id>' for
top-level departments. This asks: which dn lists a nested department's members,
whether a department lists its sub-departments' people too, how total/start/limit
page, and how '@location' relates to the tree depth. Records only counts and
booleans; no department ids or names, people or addresses are written.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.protocol.coremail import DesktopSession  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402
from probe.coremail_transport import CoremailError  # noqa: E402
from tools.probes.probe_daily_features import ask_password  # noqa: E402
from tools.probes.probe_contacts_search import EXTRA, code_of, rpc  # noqa: E402


def nodes_with_path(nodes, path=()):
    for node in nodes or []:
        if isinstance(node, dict):
            yield node, path
            yield from nodes_with_path(node.get('ou'), path + (str(node.get('id')),))


def listing(session, dn, start=0, limit=5):
    result, ms = rpc(session, 'oab:listEx', {'dn': dn, 'returnAttrs': ['@id', 'email', '@location'], 'start': start, 'limit': limit})
    rows = [r for r in result.get('var') or [] if isinstance(r, dict)] if isinstance(result.get('var'), list) else []
    return {'code': code_of(result), 'ms': ms, 'total': result.get('total') if type(result.get('total')) is int else None}, rows


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
        everything = list(nodes_with_path(root.get('ou')))
        by_depth = {}
        for node, path in everything:
            by_depth.setdefault(len(path) + 1, []).append((node, path))
        report['depths'] = {str(d): len(v) for d, v in sorted(by_depth.items())}
        sample_big = None
        locations = set()
        for depth, entries in sorted(by_depth.items()):
            flat = full = 0
            leaf_flat = parents_flat = 0
            for node, path in entries[:30]:
                meta, rows = listing(session, f"{rid}/{node['id']}")
                has_children = bool(node.get('ou'))
                if rows:
                    flat += 1
                    leaf_flat += not has_children
                    parents_flat += has_children
                    for r in rows:
                        if isinstance(r.get('@location'), str):
                            locations.add((depth, len(r['@location'].split('/'))))
                    if sample_big is None and (meta['total'] or 0) > 5:
                        sample_big = (f"{rid}/{node['id']}", depth)
                if depth > 1 and len(entries) and full < 3:
                    meta2, rows2 = listing(session, '/'.join((rid, *path, str(node['id']))))
                    full += bool(rows2)
            report[f'depth{depth}'] = {'tried': min(30, len(entries)), 'nonempty_root_id': flat, 'nonempty_leaf': leaf_flat,
                                       'nonempty_with_children': parents_flat,
                                       'with_children': sum(bool(n.get('ou')) for n, _ in entries[:30]),
                                       'nonempty_full_path_first_tries': full}
        report['location_depth_vs_segments'] = sorted([list(x) for x in locations])
        if sample_big:
            dn, depth = sample_big
            m1, r1 = listing(session, dn, 0, 5)
            m2, r2 = listing(session, dn, 5, 5)
            m3, r3 = listing(session, dn, 0, 1000)
            ids = lambda rows: {r.get('@id') for r in rows}
            report['paging'] = {'depth': depth, 'p1': {**m1, 'rows': len(r1)}, 'p2': {**m2, 'rows': len(r2)},
                                'disjoint': not ids(r1) & ids(r2), 'big': {**m3, 'rows': len(r3)},
                                'total_is_page_size': m1['total'] == len(r1)}
    except (AppError, CoremailError) as error:
        report['login'] = error.code
    finally:
        session.close()
    out = ROOT / 'analysis' / f'contacts-search-shape2-{datetime.now():%Y-%m-%d}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
