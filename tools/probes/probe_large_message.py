"""Read-only: how to read a mail too large to download whole (raw source over 64 MB).

Webmail reads a mail with mbox:readMessage {id, markRead} (body in var.mainPartData,
attachment list in var.attachments) and downloads one attachment with
mbox:getMessageData {mid, part}. This checks both on the largest recent inbox mails
with markRead:false, reading only the first 64 KB of each attachment download.
Records only key names, types, sizes, counts, enum values and booleans -- no subjects,
names, addresses or content.
"""
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.app.backend import MailBackend  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402
from tools.probes.probe_smime_send2 import ask  # noqa: E402

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'
ENUM = re.compile(r'[A-Za-z0-9_.:/+-]{0,40}')
SAFE = {'contentType', 'type', 'encoding', 'charset', 'contentDisposition', 'id', 'partId', 'isHtml', 'isMsg'}


def shape(value, key='', depth=0):
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value if key not in ('content',) else type(value).__name__
    if isinstance(value, str):
        return value if key in SAFE and ENUM.fullmatch(value) else f'str(len {len(value)})'
    if isinstance(value, dict):
        return {k: shape(v, k, depth + 1) for k, v in sorted(value.items())} if depth < 3 else sorted(value)
    if isinstance(value, list):
        return [shape(v, key, depth + 1) for v in value[:3]] + ([f'... {len(value)} items'] if len(value) > 3 else [])
    return type(value).__name__


def head(session, mid, part):
    url = session._rpc_url('mbox:getMessageData', mid=mid, part=part)
    with session._opener.open(Request(url), timeout=60) as response:
        first = response.read(64 * 1024)
        return {'status': response.status, 'content_type': (response.headers.get('Content-Type') or '').split(';')[0],
                'content_length': response.headers.get('Content-Length'), 'disposition': bool(response.headers.get('Content-Disposition')),
                'first_bytes': len(first), 'looks_json': first.lstrip()[:1] in (b'{', b'['), 'looks_html': first.lstrip()[:1] == b'<'}


def main():
    password = ask('鸿雁 · 大邮件读取检查（只读）', f'{ACCOUNT} 邮箱密码（仅本次内存使用，不修改、不标已读）:')
    if not password:
        return 130
    data = Path(tempfile.mkdtemp(prefix='hongyan-probe-', dir=str(ROOT / 'build')))
    backend = MailBackend(data)
    report = {'date': time.strftime('%Y-%m-%d %H:%M'), 'mails': []}
    try:
        backend.login('https://newmail.inspur.com/coremail/', ACCOUNT, password, False)
        del password
        backend.folders()
        rows = backend.messages('1', 0, 100)['rows']
        report['scanned'] = len(rows)
        report['row_keys'] = sorted(rows[0]) if rows else []
        rows = sorted((r for r in rows if r.get('size')), key=lambda r: -r['size'])[:2]
        session = backend.adapter._session
        for row in rows:
            entry = {'list_size': row['size'], 'was_read': bool(row['flags'].get('read'))}
            try:
                var = backend.adapter._call('mbox:readMessage', {'id': row['id'], 'markRead': False}, True).get('var')
                entry['var'] = shape(var)
                main_part = (var or {}).get('mainPartData') or {}
                entry['body_len'] = len(main_part.get('content') or '') if isinstance(main_part, dict) else None
                atts = (var or {}).get('attachments') or []
                entry['attachments'] = len(atts)
                for att in atts[:2]:
                    part = att.get('id') if isinstance(att, dict) else None
                    if part is None:
                        continue
                    try:
                        entry.setdefault('downloads', []).append({'size': att.get('contentLength') or att.get('estimateSize') or att.get('size'), **head(session, row['id'], part)})
                    except Exception as error:  # noqa: BLE001 -- only the kind is recorded
                        entry.setdefault('downloads', []).append({'error': type(error).__name__})
            except AppError as error:
                entry['error'] = error.code
            try:
                entry['flags_after'] = bool(backend.adapter._call('mbox:getMessageInfos', {'ids': [row['id']]}, True)['var'][0]['flags'].get('read'))
            except Exception as error:  # noqa: BLE001
                entry['flags_after'] = type(error).__name__
            report['mails'].append(entry)
    finally:
        backend.logout()
        shutil.rmtree(data, ignore_errors=True)
    out = ROOT / 'analysis' / f'large-message-shape-{time.strftime("%Y-%m-%d")}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
