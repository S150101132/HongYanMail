"""Read-only probe: why do most org-tree nodes list no members?

Hypotheses from webmail (X.03931.js):
- the tree is loaded with oab:getDirectories {dn, attrIds:['email'], needChildCount:true,
  depth:3} and expanded lazily, so the plain {} call may be truncated;
- people hang off leaf departments; webmail lists a node's own people with oab:listEx
  plus condition {field:'@type', operator:'=', operand:'XUL'}, and searches a whole
  subtree with XT/jsp/contact.jsp?func=oabSearch {dn, recursive:true, keyword} (ids),
  or oab:searchEx {dn, conditions, returnAttrs, windowSize}.
Records only counts, codes and booleans: no ids, names, addresses or numbers.
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.protocol.coremail import DesktopSession  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402
from probe.coremail_transport import CoremailError, decode_extended_json  # noqa: E402
from tools.probes.probe_daily_features import ask_password  # noqa: E402
from tools.probes.probe_contacts_search import code_of, rpc  # noqa: E402

EXTRA = {'oab:getDirectories', 'oab:listEx', 'oab:get', 'oab:searchEx'}
BASE = ['@id', '@type', 'true_name', 'email', 'duty', '@location']


def jsp(session, func, params):
    started = time.perf_counter()
    url = urljoin(session.base_url, 'XT/jsp/contact.jsp') + '?' + urlencode({'func': func, 'sid': session.sid})
    try:
        raw, _ = session._fetch(Request(url, data=json.dumps(params, ensure_ascii=False).encode('utf-8'),
                                        headers={'Accept': 'text/x-json', 'Content-Type': 'text/x-json'}))
        result = decode_extended_json(raw.decode('utf-8-sig'))
    except (AppError, CoremailError) as error:
        result = {'code': error.code}
    except Exception:
        result = {'code': 'undecodable'}
    return result, round((time.perf_counter() - started) * 1000)


def walk(nodes, depth=1, parent=None):
    for n in nodes or []:
        if isinstance(n, dict):
            yield n, depth, parent
            yield from walk(n.get('ou'), depth + 1, n)


def tree_stats(var):
    root = var[0] if isinstance(var, list) else var  # With a dn the answer is that node itself.
    nodes = list(walk(root.get('ou')))
    return {'nodes': len(nodes), 'by_depth': {str(d): sum(1 for _, x, _ in nodes if x == d) for d in sorted({x for _, x, _ in nodes})},
            'root_keys': sorted(root), 'node_keys': sorted({k for n, _, _ in nodes for k in n}),
            'has_childCount': sum(1 for n, _, _ in nodes if 'childCount' in n),
            'truncated': sum(1 for n, _, _ in nodes if (n.get('childCount') or 0) > 0 and not n.get('ou')),
            'leaves': sum(1 for n, _, _ in nodes if not n.get('ou')),
            'has_dn': sum(1 for n, _, _ in nodes if n.get('dn'))}, root, nodes


def listed(session, dn, condition=None, limit=1000):
    params = {'dn': dn, 'returnAttrs': BASE, 'start': 0, 'limit': limit}
    if condition:
        params['condition'] = {'field': '@type', 'operator': '=', 'operand': condition}
    result, ms = rpc(session, 'oab:listEx', params)
    rows = [r for r in result.get('var') or [] if isinstance(r, dict)] if isinstance(result.get('var'), list) else []
    return {'code': code_of(result), 'total': result.get('total') if type(result.get('total')) is int else None, 'rows': len(rows), 'ms': ms}, rows


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
        plain, _ = rpc(session, 'oab:getDirectories', {})
        report['tree_plain'], root, plain_nodes = tree_stats(plain['var'])
        rid = str(root['id'])
        deep = {}
        for depth in (3, 6, 10):
            result, ms = rpc(session, 'oab:getDirectories', {'dn': rid, 'attrIds': ['email'], 'needChildCount': True, 'depth': depth})
            var = result.get('var')
            if code_of(result) == 'S_OK' and isinstance(var, (list, dict)) and var:
                stats, droot, dnodes = tree_stats(var)
                deep[depth] = (droot, dnodes)
                report[f'tree_depth{depth}'] = {'code': 'S_OK', 'ms': ms, 'var_type': type(var).__name__, **stats}
            else:
                report[f'tree_depth{depth}'] = {'code': code_of(result), 'ms': ms, 'var_type': type(var).__name__}
        result, _ = rpc(session, 'oab:getDirectories', {'dn': rid, 'needChildCount': True})
        report['tree_dn_only'] = {'code': code_of(result), **(tree_stats(result['var'])[0] if code_of(result) == 'S_OK' and result.get('var') else {})}
        best = deep[max(deep)][1] if deep else plain_nodes
        by_id = {str(n['id']): (n, d, p) for n, d, p in best}

        # Own entry: where does the directory put me?
        me, _ = rpc(session, 'oab:get', {'list': [account], 'returnAttrs': BASE + ['@ou'], 'forEmail': True})
        row = next((r for r in me.get('var') or [] if isinstance(r, dict)), {})
        ou = str(row.get('@ou') or '')
        loc = [p for p in str(row.get('@location') or '').split('/') if p]
        report['self'] = {'code': code_of(me), 'found': bool(row), 'ou_present': bool(ou), 'ou_has_slash': '/' in ou,
                          'ou_in_plain_tree': any(str(n['id']) == ou.split('/')[-1] for n, _, _ in plain_nodes),
                          'ou_in_deep_tree': ou.split('/')[-1] in by_id, 'location_segments': len(loc)}
        # Chain from my department up to the root (from the deep tree).
        chain, node = [], by_id.get(ou.split('/')[-1])
        while node:
            chain.append(node)
            parent = node[2]
            node = by_id.get(str(parent['id'])) if parent else None
        plain_ids = {str(n['id']): n for n, _, _ in plain_nodes}
        levels = []
        for n, depth, _ in chain:
            dn = f"{rid}/{n['id']}"
            direct, rows = listed(session, dn)
            xul, xrows = listed(session, dn, 'XUL')
            info = {'depth': depth, 'children_deep': len(n.get('ou') or []), 'childCount': n.get('childCount'),
                    'in_plain_tree': str(n['id']) in plain_ids, 'children_plain': len((plain_ids.get(str(n['id'])) or {}).get('ou') or []),
                    'listEx': direct, 'self_in_listEx': any(r.get('email', '').casefold() == account.casefold() for r in rows),
                    'listEx_XUL': xul, 'xul_types': sorted({str(r.get('@type')) for r in xrows})}
            srch, ms = jsp(session, 'oabSearch', {'dn': dn, 'recursive': True, 'keyword': '', 'type': 'U'})
            var = srch.get('var')
            info['oabSearch_recursive_empty_kw'] = {'code': code_of(srch), 'ms': ms, 'var_type': type(var).__name__,
                                                    'len': len(var) if isinstance(var, list) else None,
                                                    'item_type': type(var[0]).__name__ if isinstance(var, list) and var else None,
                                                    'self_in': account.split('@')[0] in json.dumps(var) if var else False}
            sx, ms = rpc(session, 'oab:searchEx', {'dn': dn, 'conditions': [{'field': '@type', 'operator': '=', 'operand': 'U'}],
                                                   'returnAttrs': BASE, 'windowSize': 500})
            sv = sx.get('var')
            info['searchEx'] = {'code': code_of(sx), 'ms': ms, 'var_type': type(sv).__name__, 'len': len(sv) if isinstance(sv, list) else None,
                                'total': sx.get('total') if type(sx.get('total')) is int else None,
                                'self_in': any(isinstance(r, dict) and r.get('email', '').casefold() == account.casefold() for r in sv or []),
                                'location_depths': sorted({len(str(r.get('@location') or '').split('/')) for r in sv or [] if isinstance(r, dict)})}
            levels.append(info)
        report['self_chain'] = levels
        # Across the tree: how many nodes list nobody directly, by leaf / non-leaf.
        sample = best[::max(1, len(best) // 40)]
        empty = {'leaf': [0, 0], 'parent': [0, 0]}
        for n, depth, _ in sample:
            meta, _ = listed(session, f"{rid}/{n['id']}", limit=1)
            kind = 'parent' if n.get('ou') or (n.get('childCount') or 0) > 0 else 'leaf'
            empty[kind][0] += 1
            empty[kind][1] += (meta['total'] or 0) == 0
        report['direct_empty_sample'] = {k: {'tried': v[0], 'empty': v[1]} for k, v in empty.items()}
    except (AppError, CoremailError) as error:
        report['login'] = error.code
    except Exception as error:  # Keep what was learned so far; the type only, never the message.
        report['crash'] = type(error).__name__
    finally:
        session.close()
    out = ROOT / 'analysis' / f'contacts-subtree-shape-{datetime.now():%Y-%m-%d}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
