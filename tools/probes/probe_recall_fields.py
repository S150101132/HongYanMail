"""Read-only: which send/recall status fields does the server return for sent mail?

Lists the Sent folder raw (mbox:listMessages) and fetches getMessageInfos the way
webmail does (ids as a string), for the newest messages to the colleague test
address. Records only key names and the values of the status fields
(sndStatus, rclStatus, recallable, dsIcon) — never subjects, addresses or content.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.models import AccountConfig  # noqa: E402
from coremail_desktop.protocol.coremail import CoremailAdapter  # noqa: E402
from tools.probes.probe_smime_send2 import ask  # noqa: E402

ACCOUNT = 'wangbiao@inspur.com'
COLLEAGUE = 'hanbiao@inspur.com'
STATUS = ('sndStatus', 'rclStatus', 'recallable', 'dsIcon', 'rcptRead', 'rcptSucceed', 'rcptFailed')


def pick(item):
    return {k: item.get(k) for k in STATUS if k in item}


def main():
    password = ask('鸿雁 · 撤回字段检查（只读）', f'{ACCOUNT} 邮箱密码（仅本次内存使用，不发信、不修改）:')
    if not password:
        return 130
    adapter = CoremailAdapter()
    adapter.login(AccountConfig('primary', 'https://newmail.inspur.com/coremail/', ACCOUNT), password)
    del password
    report = {'date': time.strftime('%Y-%m-%d %H:%M'), 'rows': []}
    try:
        sent = next((f.remote_id for f in adapter.list_folders() if f.role == 'sent'), '3')
        listing = adapter._call('mbox:listMessages', {'fid': int(sent), 'start': 0, 'limit': 20, 'order': 'date', 'desc': True,
                                                      'returnTotal': True}, True).get('var') or []
        for item in listing:
            if COLLEAGUE not in str(item.get('to', '')).lower():
                continue
            mid = item['id']
            info = adapter._call('mbox:getMessageInfos', {'ids': mid}, True).get('var')
            info = info[0] if isinstance(info, list) and info else {}
            report['rows'].append({'encrypted': bool((item.get('flags') or {}).get('encrypted')),
                                   'list_keys': sorted(item), 'list_status': pick(item),
                                   'info_keys': sorted(info), 'info_status': pick(info),
                                   'has_tinfo': bool(info.get('tinfo') or item.get('tinfo'))})
    finally:
        adapter.close()
    out = ROOT / 'analysis' / 'recall-fields-2026-10-17.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    for row in report['rows']:
        print('enc' if row['encrypted'] else 'plain', 'list', row['list_status'], 'info', row['info_status'], 'tinfo', row['has_tinfo'])
    print('list keys:', report['rows'][0]['list_keys'] if report['rows'] else None)
    return 0


if __name__ == '__main__':
    sys.exit(main())
