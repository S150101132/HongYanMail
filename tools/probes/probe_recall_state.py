"""Read-only: why can a sent message not be recalled?

For recent sent messages, compare encrypted vs plain: does getMessageInfos give a
tinfo (delivery tracking id), and what does listDeliveryHistory say per recipient
(recallable, result, rclResult)? Records only booleans, codes and key names —
no subjects, addresses or content. Nothing is sent or changed.
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


def main():
    password = ask('鸿雁 · 撤回状态检查（只读）', f'{ACCOUNT} 邮箱密码（仅本次内存使用，不发信、不修改）:')
    if not password:
        return 130
    adapter = CoremailAdapter()
    adapter.login(AccountConfig('primary', 'https://newmail.inspur.com/coremail/', ACCOUNT), password)
    del password
    report = {'date': time.strftime('%Y-%m-%d %H:%M'), 'messages': []}
    try:
        sent = next((f.remote_id for f in adapter.list_folders() if f.role == 'sent'), '3')
        rows = adapter.list_messages(sent, 0, 30)
        for row in rows:
            to_colleague = COLLEAGUE in row.recipients.lower()
            to_self_only = row.recipients.lower().strip().strip('<>').endswith(ACCOUNT) and ',' not in row.recipients
            if not (to_colleague or to_self_only):
                continue  # Only the user's own test traffic.
            infos = adapter._call('mbox:getMessageInfos', {'ids': [row.remote_id]}, True).get('var')
            info = next((i for i in infos if isinstance(i, dict) and i.get('id') == row.remote_id), {}) if isinstance(infos, list) else {}
            try:
                history = adapter.delivery_history(row.remote_id)
            except Exception as error:
                history = getattr(error, 'code', type(error).__name__)
            report['messages'].append({
                'to': 'colleague' if to_colleague else 'self',
                'age_minutes': None,
                'encrypted_flag': bool(dict(row.flags).get('encrypted')),
                'has_tinfo': isinstance(info.get('tinfo'), str) and bool(info.get('tinfo')),
                'info_keys': sorted(info)[:40],
                'delivery': history if isinstance(history, str) else [
                    {'recallable': h['recallable'], 'result': h['result'], 'recall': h['recall']} for h in history],
            })
            if len(report['messages']) >= 8:
                break
    finally:
        adapter.close()
    out = ROOT / 'analysis' / 'recall-state-2026-10-17.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
