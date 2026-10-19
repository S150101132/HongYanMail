"""Real-account check of the v0.5 compose features through MailBackend, send-to-self only.

Body picture + table on the normal path, server drafts (edit, save back, send; plain
and encrypted), forward as attachment. Records booleans, counts and content types
only (analysis/compose-v05-real-verification-2026-10-18.json); every test message
is moved to the trash and the temporary data folder is deleted.
"""
import base64
import json
import re
import shutil
import sys
import tempfile
import time
import uuid
from email import policy
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.app.backend import MailBackend  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402
from coremail_desktop.models import DraftAttachment  # noqa: E402
from tools.probes.probe_compose_v05 import PNG  # noqa: E402
from tools.probes.probe_smime_send2 import ACCOUNT, ask  # noqa: E402

REPORT = {'date': time.strftime('%Y-%m-%d %H:%M')}
IMG = '<img src="data:image/png;base64,' + base64.b64encode(PNG).decode('ascii') + '">'
TABLE = '<table style="border-collapse:collapse"><tr><td style="border:1px solid #999">a</td><td colspan="2">b</td></tr></table>'


def find(backend, folder, marker, tries=1):
    for attempt in range(tries):
        rows = [r for r in backend.messages(folder, 0, 60)['rows'] if marker in r['subject']]
        if rows or attempt == tries - 1:
            return rows
        time.sleep(8)


def mime(backend, mid):
    return BytesParser(policy=policy.default).parsebytes(backend.adapter.fetch_mime(mid))


def webmail_draft(backend, subject):
    """A draft as another client leaves it: compose save with one attachment."""
    a, s = backend.adapter, backend.adapter._session
    base = {'account': ACCOUNT, 'to': [ACCOUNT], 'cc': [], 'bcc': [], 'isHtml': True, 'saveSentCopy': True, 'subject': subject,
            'content': '<p>草稿</p>' + TABLE}
    value = a._call('%mbox/compose', {'action': 'continue', 'returnInfo': True, 'attrs': {**base, 'attachments': []}})
    cid = value['var']['id'] if isinstance(value.get('var'), dict) else value.get('var')
    att = s.upload_attachment(cid, DraftAttachment('note.txt', 'text/plain', b'draft attachment'))
    a._call('%mbox/compose', {'action': 'save', 'id': cid, 'returnInfo': True, 'attrs': {**base, 'attachments': [att]}})


def main():
    password = ask('鸿雁 · 写信功能验证', f'{ACCOUNT} 邮箱密码（仅本次内存使用）:\n将给自己发 4 封测试邮件、建 2 份服务器草稿，结束后移到已删除')
    if not password:
        return 130
    data = Path(tempfile.mkdtemp(prefix='hongyan-compose-', dir=str(ROOT / 'build')))
    backend = MailBackend(data)
    run = 'HYV5-' + uuid.uuid4().hex[:8]
    try:
        backend.login('https://newmail.inspur.com/coremail/', ACCOUNT, password, False)
        del password
        backend.folders()

        def draft(tag, **extra):
            value = backend.compose('new')
            value.update({'to': [ACCOUNT], 'subject': f'鸿雁 写信验证 {run}-{tag}', 'encrypt': False, 'sign': False, **extra})
            return value

        # 1. Body picture and table, normal path.
        REPORT['inline_send'] = backend.send(draft('I', html='<p>图和表</p>' + TABLE + IMG))['state']
        got = find(backend, '1', f'{run}-I', 12)
        if got:
            m = mime(backend, got[0]['id'])
            html = m.get_body(preferencelist=('html',)).get_content()
            ids = {str(p.get('Content-ID', '')).strip('<>') for p in m.walk() if p.get('Content-ID')}
            refs = re.findall(r'src=["\']cid:([^"\']+)', html)
            REPORT['inline'] = {'related': 'multipart/related' in [p.get_content_type() for p in m.walk()],
                                'cid_resolves': bool(refs) and all(r in ids for r in refs), 'data_uri_left': 'data:image' in html,
                                'table_kept': '<table' in html and 'colspan' in html,
                                'reader_attachments': len(backend.open_message(got[0]['id'], mark_read=False)['attachments'])}

        # 2. Server draft from "another client": edit, save back, send.
        webmail_draft(backend, f'鸿雁 写信验证 {run}-D')
        rows = find(backend, '2', f'{run}-D', 6)
        REPORT['server_draft_listed'] = len(rows)
        if rows:
            value = backend.edit_server_draft(rows[0]['id'])
            REPORT['edit_loaded'] = {'attachments': len(value['attachments']), 'table': '<table' in value['html'],
                                     'to_self': any(ACCOUNT in a for a in value['to'])}
            value['subject'] += 'b'
            saved = backend.save_draft(value, server=True)
            REPORT['save_back'] = {'new_id': bool(saved.get('serverDraftId')) and saved['serverDraftId'] != rows[0]['id'],
                                   'error': saved.get('serverErrorCode'), 'local_copy_kept': bool(backend.list_drafts())}
            time.sleep(4)
            now = find(backend, '2', f'{run}-D')
            REPORT['after_save'] = {'copies': len(now), 'subject_updated': any(r['subject'].endswith('-Db') for r in now)}
            if now:
                REPORT['after_save']['attachments'] = sum(1 for p in mime(backend, now[0]['id']).walk() if p.get_filename())
                value = backend.edit_server_draft(now[0]['id'])
                REPORT['resaved_attachments_seen'] = len(value['attachments'])
                REPORT['draft_send'] = backend.send(value)['state']
                time.sleep(6)
                REPORT['draft_left_after_send'] = len(find(backend, '2', f'{run}-D'))
                got = find(backend, '1', f'{run}-D', 12)
                if got:
                    m = mime(backend, got[0]['id'])
                    REPORT['draft_arrived_attachments'] = sum(1 for p in m.walk() if p.get_filename())

        # 3. Encrypted send of a server draft (signing off): encrypted on arrival, draft consumed.
        webmail_draft(backend, f'鸿雁 写信验证 {run}-X')
        rows = find(backend, '2', f'{run}-X', 6)
        if rows:
            value = backend.edit_server_draft(rows[0]['id'])
            REPORT['encrypted_draft_send'] = backend.send({**value, 'encrypt': True, 'sign': False})['state']
            time.sleep(6)
            REPORT['encrypted_draft_left'] = len(find(backend, '2', f'{run}-X'))
            got = find(backend, '1', f'{run}-X', 12)
            if got:
                REPORT['encrypted_arrival_type'] = mime(backend, got[0]['id']).get_content_type()

        # 4. Forward as attachment of the picture mail.
        source = find(backend, '1', f'{run}-I')
        if source:
            value = backend.compose('forward_attach', source[0]['id'])
            value.update(to=[ACCOUNT], subject=f'鸿雁 写信验证 {run}-F', encrypt=False, sign=False)
            REPORT['forward_attach_send'] = backend.send(value)['state']
            got = find(backend, '1', f'{run}-F', 12)
            if got:
                m = mime(backend, got[0]['id'])
                parts = [p for p in m.walk() if p.get_content_type() == 'message/rfc822']
                REPORT['forward_attach'] = {'rfc822_parts': len(parts),
                                            'inner_subject_intact': bool(parts) and run in str(parts[0].get_payload()[0].get('Subject'))}
    except AppError as error:
        REPORT['error'] = error.code
    finally:
        try:
            ids = [r['id'] for folder in ('1', '2', '3') for r in backend.messages(folder, 0, 60)['rows'] if run in r['subject']]
            if ids:
                REPORT['cleanup'] = len(backend.move(ids, '4')['done'])
        except AppError as error:
            REPORT['cleanup_error'] = error.code
        backend.logout()
        shutil.rmtree(data, ignore_errors=True)
        out = ROOT / 'analysis' / 'compose-v05-real-verification-2026-10-18.json'
        out.write_text(json.dumps(REPORT, ensure_ascii=False, indent=1), encoding='utf-8')
        print(json.dumps(REPORT, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
