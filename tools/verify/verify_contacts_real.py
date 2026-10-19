"""Real-account check of the contacts view's backend path (read-only).

Opens the directory and the user's own department through MailBackend exactly as
the 通讯录 view does, then a sample of tree nodes, and records only counts,
booleans, codes and timings: no department ids or names, people or addresses.
The cache for this run lives in a temporary folder that is deleted afterwards.
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.app.backend import MailBackend  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402
from tools.probes.probe_daily_features import ask_password  # noqa: E402

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'


def main():
    password = ask_password(ACCOUNT)
    if not password:
        return 130
    data = Path(tempfile.mkdtemp(prefix='hongyan-verify-', dir=str(ROOT / 'build')))
    report = {'date': time.strftime('%Y-%m-%d %H:%M'), 'checks': {}}
    checks = report['checks']

    def check(name, fn):
        start = time.perf_counter()
        try:
            checks[name] = {'status': 'passed', **(fn() or {})}
        except AppError as error:
            checks[name] = {'status': 'failed', 'code': error.code}
        except Exception as error:  # Only the type name; no message text.
            checks[name] = {'status': 'failed', 'code': type(error).__name__}
        checks[name]['ms'] = round((time.perf_counter() - start) * 1000)
        return checks[name]['status'] == 'passed'

    backend = MailBackend(data)
    try:
        if not check('login', lambda: {'ok': bool(backend.login('https://newmail.inspur.com/coremail/', ACCOUNT, password, False))}):
            return 2
        state = {}

        def tree():
            state['tree'] = backend.directory()['tree']
            nodes = []

            def walk(ns, depth):
                for n in ns:
                    nodes.append((n, depth))
                    walk(n['children'], depth + 1)
            walk(state['tree'][0]['children'], 1)
            state['nodes'] = nodes
            return {'nodes': len(nodes)}
        check('directory', tree)

        def own():
            me = backend.person(ACCOUNT, refresh=True)
            state['dept'] = me.get('dept', '')
            names = [p for p in state['dept'].split(' / ') if p]
            # The node whose path matches my department path (names compared in memory only).
            hits = [n for n, d in state['nodes'] if names and n['name'] == names[-1]]
            return {'found': me.get('found'), 'dept_levels': len(names), 'nodes_with_my_dept_name': len(hits)}
        check('own_person', own)

        def own_members():
            names = [p for p in state['dept'].split(' / ') if p]
            out = []
            for n, d in state['nodes']:
                if names and n['name'] == names[-1]:
                    page = backend.members(n['id'], refresh=True)
                    out.append({'depth': d, 'children': len(n['children']), 'rows': len(page['rows']), 'total': page['total'],
                                'self_in': any(r['email'].casefold() == ACCOUNT.casefold() for r in page['rows']),
                                'rows_with_dept': sum(1 for r in page['rows'] if r.get('dept'))})
                    again = backend.members(n['id'])
                    out[-1]['cached_rows'] = len(again['rows'])
            return {'nodes': out}
        check('own_department', own_members)

        def sample():
            rows = {'leaf': [0, 0, 0], 'parent': [0, 0, 0]}
            for n, d in state['nodes'][::4]:
                kind = 'parent' if n['children'] else 'leaf'
                page = backend.members(n['id'], refresh=True)
                rows[kind][0] += 1
                rows[kind][1] += not page['rows']
                rows[kind][2] += page['total'] > len(page['rows'])
            return {k: {'tried': v[0], 'empty': v[1], 'capped': v[2]} for k, v in rows.items()}
        check('sample_nodes', sample)
    finally:
        password = None
        backend.logout()
        shutil.rmtree(data, ignore_errors=True)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
