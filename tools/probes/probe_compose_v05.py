"""Shapes for v0.5 compose features, send-to-self only (2026-10-18).

1. Server drafts: create one with mbox:compose save, reopen it (compose.jsp
   ctype=draft), save it again, deliver it. Records whether the message id
   stays, how many copies exist, and whether delivering removes the draft.
2. Inline images: upload:prepare/directData with inlined=1 and the body image
   pointing at webmail's mbox:getComposeData URL (absolute and relative
   variants). Records whether the delivered copy is multipart/related with a
   cid: reference that resolves, and whether any sid leaked into the body.
3. message/rfc822 attachment through the normal upload: arrives intact?

Only keys, types, counts and booleans are written (analysis/compose-v05-shape-2026-10-18.json).
Every test message is moved to the trash at the end.
"""
import json
import sys
import time
import uuid
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import urlencode, urljoin

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.errors import AppError  # noqa: E402
from coremail_desktop.models import AccountConfig, DraftAttachment  # noqa: E402
from coremail_desktop.protocol.coremail import CoremailAdapter  # noqa: E402
from tools.probes.probe_smime_send2 import ACCOUNT, ask  # noqa: E402

PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
       b'\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\xa7\xc1\x9b\xb7\x00\x00\x00\x00IEND\xaeB`\x82')
REPORT = {'date': time.strftime('%Y-%m-%d %H:%M')}


def keys(value, depth=0):
    if isinstance(value, dict) and depth < 3:
        return {k: keys(v, depth + 1) for k, v in sorted(value.items()) if '@' not in k}
    if isinstance(value, list):
        return [keys(value[0], depth + 1)] if value else []
    return type(value).__name__


def rows(adapter, fid, marker):
    return [m for m in adapter.list_messages(fid, 0, 60) if marker in m.subject]


def wait(adapter, fid, marker, tries=12):
    for _ in range(tries):
        found = rows(adapter, fid, marker)
        if found:
            return found
        time.sleep(8)
    return []


def tree(message):
    return [part.get_content_type() for part in message.walk()]


def main():
    password = ask('鸿雁 · 写信功能探测', f'{ACCOUNT} 邮箱密码（仅本次内存使用）:\n将给自己发 4 封测试邮件并建 1 份服务器草稿，结束后移到已删除')
    if not password:
        return 130
    adapter = CoremailAdapter()
    run = 'HYP5-' + uuid.uuid4().hex[:8]
    base = {'account': ACCOUNT, 'to': [ACCOUNT], 'cc': [], 'bcc': [], 'isHtml': True, 'saveSentCopy': True, 'attachments': []}
    try:
        adapter.login(AccountConfig('primary', 'https://newmail.inspur.com/coremail/', ACCOUNT), password)
        del password
        s = adapter._session
        compose = lambda body: adapter._call('%mbox/compose', body)
        REPORT['drafts_folder_rows'] = len(adapter.list_messages('2', 0, 100))

        # 1. Server draft lifecycle.
        subject = f'鸿雁 草稿探测 {run}-D'
        value = compose({'action': 'continue', 'returnInfo': True, 'attrs': {**base, 'subject': subject, 'content': '<p>草稿 v1</p>'}})
        cid = value['var']['id'] if isinstance(value.get('var'), dict) else value.get('var')
        saved = compose({'action': 'save', 'id': cid, 'returnInfo': True, 'attrs': {**base, 'subject': subject, 'content': '<p>草稿 v1</p>'}})
        REPORT['save_response'] = keys({k: v for k, v in saved.items() if k != 'var'})
        REPORT['save_var'] = keys(saved.get('var'))
        first = wait(adapter, '2', run + '-D')
        REPORT['draft_listed'] = len(first)
        if first:
            mid = first[0].remote_id
            REPORT['draftId_equals_listed_mid'] = saved.get('draftId') == mid
            REPORT['draft_flags'] = sorted(k for k, v in first[0].flags.items() if v)
            opened = s.open_draft(mid)
            REPORT['open_draft_var'] = keys(opened)
            REPORT['open_draft_roundtrip'] = {'subject': opened.get('subject') == subject, 'isHtml': opened.get('isHtml'),
                                              'to_is_list': isinstance(opened.get('to'), list), 'content_has_v1': 'v1' in str(opened.get('content', '')),
                                              'id_differs_from_mid': opened.get('id') != mid}
            again = compose({'action': 'save', 'id': opened['id'], 'returnInfo': True,
                             'attrs': {**base, 'subject': subject + 'b', 'content': '<p>草稿 v2</p>'}})
            REPORT['resave_draftId_same_mid'] = again.get('draftId') == mid
            time.sleep(4)
            now = rows(adapter, '2', run + '-D')
            REPORT['after_resave_copies'] = len(now)
            REPORT['after_resave_old_mid_present'] = any(m.remote_id == mid for m in now)
            REPORT['after_resave_subject_updated'] = any(m.subject.endswith('-Db') for m in now)
            # Deliver from a compose session reopened from the (new) draft.
            target = next((m for m in now if m.subject.endswith('-Db')), None)
            if target:
                reopened = s.open_draft(target.remote_id)
                delivered = compose({'action': 'deliver', 'id': reopened['id'], 'returnInfo': True,
                                     'attrs': {**base, 'subject': subject + 'b', 'content': '<p>草稿 v2</p>'}})
                REPORT['deliver_from_draft'] = delivered.get('code')
                time.sleep(6)
                REPORT['draft_left_after_deliver'] = len(rows(adapter, '2', run + '-D'))
                REPORT['draft_arrived_inbox'] = len(wait(adapter, '1', run + '-D'))

        # 2. Inline image, absolute and relative URL variants.
        for tag, absolute in (('IA', True), ('IR', False)):
            subject = f'鸿雁 内嵌图探测 {run}-{tag}'
            value = compose({'action': 'continue', 'returnInfo': True, 'attrs': {**base, 'subject': subject, 'content': ''}})
            cid = value['var']['id'] if isinstance(value.get('var'), dict) else value.get('var')
            att = s.upload_attachment(cid, DraftAttachment('shot.png', 'image/png', PNG), inlined=True)
            query = urlencode({'sid': s.sid, 'func': 'mbox:getComposeData', 'composeId': cid, 'attachId': att['id']})
            src = (urljoin(s.base_url, 's/json') if absolute else 's/json') + '?' + query
            html = f'<p>内嵌图片：</p><p><img src="{src}"></p>'
            response = compose({'action': 'deliver', 'id': cid, 'returnInfo': True,
                                'attrs': {**base, 'subject': subject, 'content': html, 'attachments': [{**att, 'inlined': True}]}})
            REPORT[f'inline_{tag}_deliver'] = response.get('code')
            got = wait(adapter, '1', run + '-' + tag)
            if got:
                message = BytesParser(policy=policy.default).parsebytes(adapter.fetch_mime(got[0].remote_id))
                types = tree(message)
                body = message.get_body(preferencelist=('html',))
                text = body.get_content() if body else ''
                ids = {str(p.get('Content-ID', '')).strip('<>') for p in message.walk() if p.get('Content-ID')}
                refs = [r for r in __import__('re').findall(r'src=["\']cid:([^"\']+)', text)]
                images = [p for p in message.walk() if p.get_content_type() == 'image/png']
                REPORT[f'inline_{tag}'] = {'types': types, 'has_related': 'multipart/related' in types,
                                          'cid_refs': len(refs), 'cid_resolves': bool(refs) and all(r in ids for r in refs),
                                          'image_intact': any(p.get_payload(decode=True) == PNG for p in images),
                                          'image_disposition': [str(p.get_content_disposition()) for p in images],
                                          'sid_in_body': s.sid in text, 'getComposeData_in_body': 'getComposeData' in text}

        # 3. message/rfc822 attachment through the normal upload.
        subject = f'鸿雁 附件转发探测 {run}-E'
        inner = (b'From: a@example.invalid\r\nTo: b@example.invalid\r\nSubject: inner\r\nMessage-ID: <x@example.invalid>\r\n'
                 b'MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nhello\r\n')
        value = compose({'action': 'continue', 'returnInfo': True, 'attrs': {**base, 'subject': subject, 'content': '<p>原邮件见附件</p>'}})
        cid = value['var']['id'] if isinstance(value.get('var'), dict) else value.get('var')
        att = s.upload_attachment(cid, DraftAttachment('original.eml', 'message/rfc822', inner))
        response = compose({'action': 'deliver', 'id': cid, 'returnInfo': True,
                            'attrs': {**base, 'subject': subject, 'content': '<p>原邮件见附件</p>', 'attachments': [att]}})
        REPORT['rfc822_deliver'] = response.get('code')
        got = wait(adapter, '1', run + '-E')
        if got:
            message = BytesParser(policy=policy.default).parsebytes(adapter.fetch_mime(got[0].remote_id))
            parts = [p for p in message.walk() if p.get_content_type() == 'message/rfc822']
            REPORT['rfc822'] = {'types': tree(message), 'rfc822_parts': len(parts),
                                'inner_subject_intact': bool(parts) and str(parts[0].get_payload()[0].get('Subject')) == 'inner'}
    except AppError as error:
        REPORT['error'] = error.code
    finally:
        try:
            ids = [m.remote_id for fid in ('1', '2', '3') for m in rows(adapter, fid, run)]
            done = [i for i in ids if adapter.move_message(i, '4')]
            REPORT['cleanup_moved'] = len(done)
        except AppError as error:
            REPORT['cleanup_error'] = error.code
        adapter.close()
        out = ROOT / 'analysis' / 'compose-v05-shape-2026-10-18.json'
        out.write_text(json.dumps(REPORT, ensure_ascii=False, indent=1), encoding='utf-8')
        print(json.dumps(REPORT, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
