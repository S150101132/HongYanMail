"""Read-only probe for R5 (对已有邮件执行): does mbox:listMessages honour ruleConditions:'@<id>'?

Webmail lists a saved rule's existing matches with mbox:listMessages {fid, ruleConditions:'@<id>'}
(S.03931.js setting.filter.letter). For every rule this records the inbox total, the total and row
count listed with that rule, and whether every listed row is also in the inbox; plus the answer for
a rule id that does not exist. Only counts, codes and booleans -- no rule text, names or mail.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.models import AccountConfig  # noqa: E402
from coremail_desktop.protocol.coremail import CoremailAdapter  # noqa: E402
from tools.probes.probe_rules_tags_cal import attempt  # noqa: E402
from tools.probes.probe_smime_send2 import ask  # noqa: E402

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'
LIST = {'fid': 1, 'start': 0, 'limit': 100, 'mode': 'count', 'order': 'date', 'desc': True, 'returnTotal': True, 'summaryWindowSize': 0}


def listing(adapter, **extra):
    ok, value = attempt(lambda: adapter._call('mbox:listMessages', {**LIST, **extra}, True))
    if not ok:
        return {'ok': False, 'code': value}, set()
    rows = value.get('var') if isinstance(value.get('var'), list) else []
    return {'ok': True, 'total': value.get('total'), 'rows': len(rows)}, {r.get('id') for r in rows if isinstance(r, dict)}


def main():
    password = ask('鸿雁 · 收信规则匹配检查（只读）', f'{ACCOUNT} 邮箱密码（仅本次内存使用，不发信、不修改）:')
    if not password:
        return 130
    adapter = CoremailAdapter()
    adapter.login(AccountConfig('primary', 'https://newmail.inspur.com/coremail/', ACCOUNT), password)
    del password
    report = {'date': time.strftime('%Y-%m-%d %H:%M')}
    try:
        inbox, inbox_ids = listing(adapter)
        report['inbox'] = inbox
        report['rules'] = []
        # How the rule list comes back (the first run listed none although the account has rules).
        raw_rules = []
        for op in ('user:getMailRules', 'mr:list'):
            ok, value = attempt(lambda: adapter._call(op, {}, True))
            var = value.get('var') if ok and isinstance(value, dict) else None
            report.setdefault('rule_list', {})[op] = {'ok': ok, 'code': None if ok else value, 'keys': sorted(value) if ok and isinstance(value, dict) else None,
                                                      'var_type': type(var).__name__, 'count': len(var) if isinstance(var, (list, dict)) else None,
                                                      'var_keys': sorted(var)[:20] if isinstance(var, dict) else None}
            if not raw_rules and isinstance(var, list):
                raw_rules = [r for r in var if isinstance(r, dict) and type(r.get('id')) in (int, str)]
            if not raw_rules and isinstance(var, dict) and isinstance(var.get('rules'), list):
                raw_rules = [r for r in var['rules'] if isinstance(r, dict) and type(r.get('id')) in (int, str)]
        for rule in raw_rules:
            entry, ids = listing(adapter, ruleConditions=f'@{rule["id"]}')
            entry.update(id=rule['id'], disabled=rule.get('disabled') is True,
                         fields=sorted({c.get('field') for c in rule.get('condictions') or [] if isinstance(c, dict) and isinstance(c.get('field'), str)}),
                         actions=sorted({a.get('type') for a in rule.get('actions') or [] if isinstance(a, dict) and isinstance(a.get('type'), str)}),
                         fewer_than_inbox=entry.get('total') is not None and inbox.get('total') is not None and entry['total'] < inbox['total'],
                         rows_in_first_inbox_page=len(ids & inbox_ids))
            report['rules'].append(entry)
        report['unknown_rule'] = listing(adapter, ruleConditions='@999999999')[0]
        # Through the adapter as the app calls it.
        first = report['rules'][0]['id'] if report['rules'] else None
        if first is not None:
            ok, page = attempt(lambda: adapter.rule_matches(first, '1', 0, 5))
            report['adapter_rule_matches'] = {'ok': ok, 'total': page.get('total') if ok else None, 'rows': len(page['messages']) if ok else None,
                                              'code': None if ok else page}
    finally:
        adapter.close()
    out = ROOT / 'analysis' / f'rule-matches-shape-{time.strftime("%Y-%m-%d")}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
