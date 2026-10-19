"""Folder tree shapes: mbox:getAllFolders (RPC) vs mail.jsp getAllFolders (webmail's tree).

Read-only, except with --cleanup: then empty test folders named 'HY测试…' (made by
tools/verify/verify_mail_management_real.py) and their empty sub-folders are deleted,
children first. Records keys, counts and the test folders' own names only.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.models import AccountConfig  # noqa: E402
from coremail_desktop.protocol.coremail import CoremailAdapter  # noqa: E402
from tools.probes.probe_rules_tags_cal import attempt, jsp, shape  # noqa: E402
from tools.probes.probe_smime_send2 import ACCOUNT, ask  # noqa: E402

CLEANUP = '--cleanup' in sys.argv


def walk(items, depth=0, parent=None):
    for item in items or []:
        if isinstance(item, dict):
            yield item, depth, parent
            yield from walk(item.get('children') or item.get('items'), depth + 1, item.get('id'))


def main():
    password = ask('鸿雁 · 文件夹树检查', f'{ACCOUNT} 邮箱密码（仅本次内存使用）:\n读取文件夹结构' + ('，并删除验证留下的空测试文件夹 HY测试…' if CLEANUP else '（只读）'))
    if not password:
        return 130
    adapter = CoremailAdapter()
    adapter.login(AccountConfig('primary', 'https://newmail.inspur.com/coremail/', ACCOUNT), password)
    del password
    session = adapter._session
    report = {'date': time.strftime('%Y-%m-%d %H:%M')}
    try:
        rpc = adapter._call('mbox:getAllFolders', {'stats': True}, True)['var']
        report['rpc'] = {'count': len(rpc), 'test': [{'id': f['id'], 'name': f['name'], 'keys': sorted(f)} for f in rpc if str(f.get('name', '')).startswith(('HY测试', '子文件夹'))]}
        for name, params in (('jsp_stats', {'stats': 'true'}), ('jsp_plain', {})):
            ok, value = attempt(lambda: jsp(session, 'getAllFolders', params))
            if not ok:
                report[name] = value
                continue
            var = value.get('var')
            items = list(walk(var if isinstance(var, list) else [var]))
            report[name] = {'top_type': type(var).__name__, 'nodes': len(items), 'max_depth': max((d for _, d, _ in items), default=0),
                            'keys': sorted({k for i, _, _ in items for k in i}),
                            'with_parentId': sum(1 for i, _, _ in items if i.get('parentId') not in (None, 0)),
                            'test': [{'id': i.get('id'), 'name': i.get('name'), 'depth': d, 'tree_parent': p, 'parentId': i.get('parentId'),
                                      'keys': sorted(i), 'stats': shape(i.get('stats')),
                                      'messages': (i.get('stats') or {}).get('messageCount') if isinstance(i.get('stats'), dict) else None} for i, d, p in items
                                     if str(i.get('name', '')).startswith(('HY测试', '子文件夹'))]}
        if CLEANUP:
            tests = report.get('jsp_stats', {}).get('test') if isinstance(report.get('jsp_stats'), dict) else []
            done = []
            counts = {str(f['id']): (f.get('stats') or {}).get('messageCount') for f in rpc}
            tops = {i['id'] for i in tests or [] if str(i['name']).startswith('HY测试')}  # Sub-folders count only under these.
            for item in sorted(tests or [], key=lambda i: -i['depth']):  # Children first.
                messages = item['messages'] if item['messages'] is not None else counts.get(str(item['id']))
                ours = str(item['name']).startswith('HY测试') or (item['tree_parent'] or item['parentId']) in tops
                if messages != 0 or not ours:
                    done.append({'name': item['name'], 'skipped': 'not verifiably empty'})
                    continue
                ok, value = attempt(lambda: adapter._call('mbox:deleteFolders', {'ids': [int(item['id'])]}))
                done.append({'name': item['name'], 'depth': item['depth'], 'ok': ok, 'code': None if ok else value})
            report['cleanup'] = done
            left = adapter._call('mbox:getAllFolders', {'stats': True}, True)['var']
            ok, value = attempt(lambda: jsp(session, 'getAllFolders', {'stats': 'true'}))
            report['left_rpc'] = sum(str(f.get('name', '')).startswith('HY测试') for f in left)
            report['left_jsp'] = sum(str(i.get('name', '')).startswith(('HY测试', '子文件夹')) for i, _, _ in walk(value.get('var') if ok and isinstance(value.get('var'), list) else []))
    finally:
        adapter.close()
    out = ROOT / 'analysis' / f'folder-tree-shape-{time.strftime("%Y-%m-%d-%H%M")}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
