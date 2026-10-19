"""Read-only follow-up: where do the people of "empty" tree nodes live?

Round 1 (probe_contacts_subtree.py): the tree is complete (169 nodes, 3 levels,
nothing truncated), the user's own top-level node lists them directly (2035 people),
yet 17/27 sampled leaves list nobody. This lists the whole company with
oab:searchEx at the root and groups people by their '@ou' to see which tree nodes
own people, what '@ou' looks like (segments), whether the dn listEx needs is the
'@ou' value itself, and whether node names repeat. Counts and booleans only.
"""
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.protocol.coremail import DesktopSession  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402
from probe.coremail_transport import CoremailError  # noqa: E402
from tools.probes.probe_daily_features import ask_password  # noqa: E402
from tools.probes.probe_contacts_search import code_of, rpc  # noqa: E402
from tools.probes.probe_contacts_subtree import EXTRA, listed, walk  # noqa: E402


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
        nodes = list(walk(root.get('ou')))
        ids = {str(n['id']): (n, d, p) for n, d, p in nodes}
        names = Counter(str(n.get('name') or '') for n, _, _ in nodes)
        report['names'] = {'nodes': len(nodes), 'distinct': len(names), 'repeated_names': sum(1 for c in names.values() if c > 1),
                           'nodes_with_repeated_name': sum(c for c in names.values() if c > 1)}
        me, _ = rpc(session, 'oab:get', {'list': [account], 'returnAttrs': ['@ou', 'email'], 'forEmail': True})
        my_ou = str(next((r for r in me.get('var') or [] if isinstance(r, dict)), {}).get('@ou') or '')
        my_node = ids.get(my_ou.split('/')[-1])
        report['self'] = {'ou_segments': len(my_ou.split('/')), 'ou_is_root_slash_id': my_ou == f"{rid}/{my_ou.split('/')[-1]}",
                          'name_repeated': bool(my_node) and names[str(my_node[0].get('name') or '')] > 1}
        # The whole company, by '@ou'.
        everyone, codes, window = [], set(), None
        for size in (20000, 6000):
            result, ms = rpc(session, 'oab:searchEx', {'dn': rid, 'conditions': [{'field': '@type', 'operator': '=', 'operand': 'U'}],
                                                       'returnAttrs': ['@id', '@ou', 'email', '@location'], 'windowSize': size})
            codes.add(code_of(result))
            if code_of(result) == 'S_OK' and isinstance(result.get('var'), list):
                everyone, window = [r for r in result['var'] if isinstance(r, dict)], {'size': size, 'ms': ms, 'result_keys': sorted(result)}
                break
        report['searchEx_root'] = {'codes': sorted(codes), **(window or {}), 'people': len(everyone),
                                   'with_email': sum(1 for r in everyone if r.get('email')), 'with_ou': sum(1 for r in everyone if r.get('@ou')),
                                   'self_in': any(str(r.get('email') or '').casefold() == account.casefold() for r in everyone),
                                   'ou_segments': dict(Counter(len(str(r.get('@ou') or '').split('/')) for r in everyone)),
                                   'location_segments': dict(Counter(len(str(r.get('@location') or '').split('/')) for r in everyone))}
        by_ou = Counter(str(r.get('@ou') or '') for r in everyone)
        owners = Counter()
        unmatched = 0
        for ou, count in by_ou.items():
            hit = ids.get(ou.split('/')[-1])
            if hit:
                owners[str(hit[0]['id'])] += count
            else:
                unmatched += count
        report['people_by_tree_node'] = {
            'distinct_ou': len(by_ou), 'people_unmatched_to_tree': unmatched,
            'ou_values_root_slash_id': sum(1 for ou in by_ou if ou == f"{rid}/{ou.split('/')[-1]}"),
            'nodes_owning_people': len(owners),
            'owning_by_kind': dict(Counter(('parent' if n.get('ou') else 'leaf') + f'@{d}' for i, (n, d, _) in ids.items() if owners[i])),
            'empty_by_kind': dict(Counter(('parent' if n.get('ou') else 'leaf') + f'@{d}' for i, (n, d, _) in ids.items() if not owners[i])),
            'people_on_root_or_none': by_ou.get(rid, 0) + by_ou.get('', 0)}
        # Do the '@ou' owners list their people with listEx, and do empty nodes list anyone?
        checks = {'owner_listEx_matches': 0, 'owner_tried': 0, 'empty_listEx_nonzero': 0, 'empty_tried': 0, 'owner_dn_equals_ou_value': 0}
        owner_ids = [i for i in ids if owners[i]][:12]
        for i in owner_ids:
            meta, _ = listed(session, f'{rid}/{i}', limit=1)
            checks['owner_tried'] += 1
            checks['owner_listEx_matches'] += meta['total'] == owners[i]
            checks['owner_dn_equals_ou_value'] += f'{rid}/{i}' in by_ou
        for i in [i for i in ids if not owners[i]][:12]:
            meta, _ = listed(session, f'{rid}/{i}', limit=1)
            checks['empty_tried'] += 1
            checks['empty_listEx_nonzero'] += bool(meta['total'])
        report['listEx_vs_ou'] = checks
    except (AppError, CoremailError) as error:
        report['login'] = error.code
    except Exception as error:  # Keep what was learned so far; the type only, never the message.
        report['crash'] = type(error).__name__
    finally:
        session.close()
    out = ROOT / 'analysis' / f'contacts-subtree-shape2-{datetime.now():%Y-%m-%d}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
