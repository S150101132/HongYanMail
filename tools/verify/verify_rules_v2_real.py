"""Real-account check of R5 rule shapes (run only with the user's approval).

One write, undone in the same run: add the DISABLED rule "HY-RULE-TEST-V2" whose conditions use the
fields and operators R5 adds (cc / recipients, excludes / != / startsWith) and whose subject value
matches no mail; read it back; list its inbox matches (expected 0); delete it. A disabled rule never
runs on new mail, and 对已有邮件执行 is not exercised here. The user's own rules are not touched.

Records only pass/fail, codes, counts and key names -- no content.
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
from coremail_desktop.protocol import rules as R  # noqa: E402
from tools.verify.verify_rules_tags_real import keys  # noqa: E402

ACCOUNT = next((a for a in sys.argv[1:] if '@' in a), 'wangbiao@inspur.com')
RULE = 'HY-RULE-TEST-V2'


def ask_password():
    import tkinter
    from tkinter import simpledialog
    root = tkinter.Tk(); root.withdraw(); root.attributes('-topmost', True)
    try:
        return simpledialog.askstring('鸿雁 · 收信规则 R5 验证', f'{ACCOUNT} 的密码（仅本次内存使用）:\n'
                                      f'会临时添加一条停用的 {RULE} 规则，读回后删除', show='*', parent=root)
    finally:
        root.destroy()


def main():
    password = ask_password()
    if not password:
        return 130
    data = Path(tempfile.mkdtemp(prefix='hongyan-verify-', dir=str(ROOT / 'build')))
    report = {'date': time.strftime('%Y-%m-%d %H:%M'), 'checks': {}}
    checks, state = report['checks'], {}

    def check(name, fn):
        start = time.perf_counter()
        try:
            checks[name] = {'status': 'passed', 'ms': round((time.perf_counter() - start) * 1000), **(fn() or {})}
        except AppError as error:
            checks[name] = {'status': 'failed', 'code': error.code}
        except Exception as error:  # Only the type name; no message text.
            checks[name] = {'status': 'failed', 'code': type(error).__name__}
        return checks[name]['status'] == 'passed'

    backend = MailBackend(data)
    adapter = backend.adapter
    try:
        if not check('login', lambda: {'ok': bool(backend.login('https://newmail.inspur.com/coremail/', ACCOUNT, password, False))}):
            return 2
        backend.folders()
        state['order'] = [r['id'] for r in backend.rules()['rules']]
        view = {'name': RULE, 'match': 'all', 'enabled': False, 'stop': False,
                'conditions': [{'field': 'subject', 'op': 'startsWith', 'value': RULE},
                               {'field': 'cc', 'op': 'excludes', 'value': 'hy-none@example.invalid'},
                               {'field': 'recipients', 'op': '!=', 'value': '"HY 测试" <hy-none2@example.invalid>'},
                               {'field': 'from', 'op': 'contains', 'value': '@'}],
                'actions': [{'type': 'flag'}]}

        def add():
            state['rule'] = backend.save_rule(view)['saved']
            raw = next(r for r in adapter.mail_rules() if r['id'] == state['rule'])
            back = R.rule_view(raw)
            return {'stored_keys': keys(raw), 'editable': back['editable'], 'same_conditions': back['conditions'] == view['conditions'],
                    'enabled': back['enabled']}
        if not check('rule_add_v2_shapes', add):
            raw = next((r for r in adapter.mail_rules() if r.get('name') == RULE), None)
            checks['rule_add_v2_shapes']['stored_keys'] = keys(raw) if raw else None
            if raw:
                state['rule'] = raw['id']
        if state.get('rule') is not None:
            # Raw, before the app's guard: 0 means the server filters by the rule; the inbox total means it ignores it.
            check('rule_matches_raw', lambda: {'total': adapter.rule_matches(state['rule'], '1', 0, 5)['total'],
                                               'inbox_total': (adapter.list_messages('1', 0, 1), adapter.last_folder_total)[1]})
            check('rule_matches_new_rule', lambda: {k: v for k, v in backend.rule_matches(state['rule']).items() if k in ('total', 'applicable')}
                  | {'rows': len(backend.rule_matches(state['rule'])['rows'])})
    finally:
        cleanup = {}
        try:
            for raw in adapter.mail_rules():
                if raw.get('name') == RULE:
                    adapter.remove_mail_rule(raw['id'])
                    cleanup['rule'] = 'removed'
            if state.get('order') is not None:
                cleanup['order_unchanged'] = [r['id'] for r in adapter.mail_rules()] == state['order']
        except AppError as error:
            cleanup['error'] = error.code
        report['cleanup'] = cleanup
        backend.logout()
        shutil.rmtree(data, ignore_errors=True)
    out = ROOT / 'analysis' / f'rules-v2-real-verification-{time.strftime("%Y-%m-%d")}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if all(c['status'] == 'passed' for c in checks.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
