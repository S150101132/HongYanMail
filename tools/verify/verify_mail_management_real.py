"""Real-account check of folder management, mark-all-read, spam reports, sender lists and permanent deletion.

Touches only what it creates itself:
- two test folders 'HY测试-<run>' (one with a sub-folder), deleted again;
- one test address hongyan-test-<run>@example.invalid on the blocklist and then the
  allowlist, removed again (the lists are compared before and after);
- three mails sent to the account's own address, plus their copies in 已发送. They are
  reported as spam and back, marked read in a test folder, moved to the trash by
  deleting that folder, emptied permanently, and finally deleted for good.
The real inbox, trash and spam folders are never emptied or marked read.
Records codes, booleans and counts only; no subjects, bodies or addresses.
"""
import json
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.app.backend import MailBackend  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402
from tools.probes.probe_smime_send2 import ACCOUNT, ask  # noqa: E402

REPORT = {'date': time.strftime('%Y-%m-%d %H:%M')}
MARK = '鸿雁 邮件管理验证 HYM-'  # Every test mail's subject starts with this; cleanup removes all of them.


def code_of(fn, *args, **kw):
    try:
        fn(*args, **kw)
        return 'OK'
    except AppError as error:
        return error.code


def find(backend, folder, marker, tries=1):
    for attempt in range(tries):
        rows = [r for r in backend.messages(folder, 0, 60)['rows'] if marker in r['subject']]
        if rows or attempt == tries - 1:
            return rows
        time.sleep(10)


def folder(backend, fid):
    return next((f for f in backend.folders(refresh=True) if f['id'] == fid), None)


def main():
    password = ask('鸿雁 · 邮件管理验证', f'{ACCOUNT} 邮箱密码（仅本次内存使用）:\n'
                   '将创建并删除 2 个测试文件夹，在黑白名单中临时加入 1 个测试地址，\n给自己发 3 封测试邮件，最后彻底删除这些测试邮件')
    if not password:
        return 130
    data = Path(tempfile.mkdtemp(prefix='hongyan-manage-', dir=str(ROOT / 'build')))
    backend = MailBackend(data)
    run = 'HYM-' + uuid.uuid4().hex[:6]
    created, test_address = [], f'hongyan-test-{run.lower()}@example.invalid'
    try:
        backend.login('https://newmail.inspur.com/coremail/', ACCOUNT, password, False)
        del password
        backend.folders()

        # 1. Folders: create, sub-folder, rename, delete.
        top = backend.create_folder(f'HY测试-{run[-6:]}')['id']
        created.append(top)
        sub = backend.create_folder('子文件夹', top)['id']
        created.append(sub)
        raw = {str(f['id']): f for f in backend.adapter._call('mbox:getAllFolders', {'stats': True}, True)['var']}
        REPORT['folder_create'] = {'top_listed': top in raw, 'sub_listed': sub in raw, 'sub_keys': sorted(raw.get(sub, {})),
                                   'sub_parent_reported': folder(backend, sub)['parent'] == top, 'top_system': folder(backend, top)['system']}
        backend.rename_folder(sub, '子文件夹改名')
        REPORT['folder_rename'] = folder(backend, sub)['name'] == '子文件夹改名'
        REPORT['delete_parent_with_child'] = code_of(backend.delete_folder, top)
        REPORT['delete_sub'] = code_of(backend.delete_folder, sub)
        REPORT['delete_top'] = code_of(backend.delete_folder, top)
        REPORT['folders_gone'] = folder(backend, sub) is None and folder(backend, top) is None
        created.clear()

        # 2. Sender lists: a test address on each list and off again; everything else unchanged.
        before = backend.sender_lists()
        REPORT['lists_before_counts'] = {k: len(v) for k, v in before.items()}
        on_block = backend.add_senders('refuselist', test_address)
        REPORT['block_add'] = test_address in backend.sender_lists()['refuselist'] and test_address in on_block['refuselist']
        moved = backend.add_senders('safelist', test_address)
        now = backend.sender_lists()
        REPORT['allow_moves_from_block'] = test_address in now['safelist'] and test_address not in now['refuselist'] and moved == now
        backend.remove_sender('safelist', test_address)
        after = backend.sender_lists()
        REPORT['lists_restored'] = after == before

        # 3. Mail sent to the account itself.
        def send(tag):
            value = backend.compose('new')
            value.update({'to': [ACCOUNT], 'subject': f'{MARK}{run[4:]}-{tag}', 'encrypt': False, 'html': '<p>测试邮件，验证后自动彻底删除。</p>'})
            return backend.send(value)['state']
        REPORT['sent'] = [send(t) for t in ('A', 'B', 'C')]
        arrived = {t: find(backend, '1', f'{run}-{t}', 12) for t in ('A', 'B', 'C')}
        REPORT['arrived'] = {t: len(v) for t, v in arrived.items()}
        a, b, c = (arrived[t][0]['id'] if arrived[t] else None for t in ('A', 'B', 'C'))

        if a:  # Spam report and back, then delete -> trash -> purge.
            res = backend.report_spam([a], True)
            REPORT['spam_report'] = {'done': len(res['done']), 'in_spam': bool(find(backend, '5', f'{run}-A', 3))}
            backend.messages('5', 0, 60)
            res = backend.report_spam([a], False)
            REPORT['not_spam'] = {'done': len(res['done']), 'in_inbox': bool(find(backend, '1', f'{run}-A', 3))}
            backend.delete([a])
            backend.messages('4', 0, 60)
            REPORT['purge'] = code_of(backend.purge, [a])
            REPORT['purged_gone'] = not find(backend, '4', f'{run}-A') and not find(backend, '1', f'{run}-A')
            REPORT['purge_outside_trash_refused'] = code_of(backend.purge, [b]) if b else None

        if b:  # Mark all read in a test folder, then delete that folder with the mail still in it.
            f2 = backend.create_folder(f'HY测试2-{run[-6:]}')['id']
            created.append(f2)
            backend.move([b], f2)
            REPORT['test_folder_unread_before'] = folder(backend, f2)['unread']
            res = backend.mark_all_read(f2)
            REPORT['mark_all_read'] = {'done': res['done'], 'failed': res['failed'], 'unread_after': folder(backend, f2)['unread']}
            res = backend.delete_folder(f2, mail='trash')
            created.remove(f2)
            REPORT['delete_folder_with_mail'] = {'moved': res['moved'], 'folder_gone': folder(backend, f2) is None,
                                                 'mail_in_trash': bool(find(backend, '4', f'{run}-B', 3))}

        if c:  # mbox:emptyFolder permanently, on a test folder holding only this mail.
            f3 = backend.create_folder(f'HY测试3-{run[-6:]}')['id']
            created.append(f3)
            backend.move([c], f3)
            REPORT['empty_test_folder'] = code_of(backend.adapter.empty_folder, f3)
            REPORT['emptied'] = {'count_after': folder(backend, f3)['total'], 'not_in_trash': not find(backend, '4', f'{run}-C'),
                                 'not_in_inbox': not find(backend, '1', f'{run}-C')}
            REPORT['delete_emptied_folder'] = code_of(backend.delete_folder, f3)
            created.remove(f3)
    except AppError as error:
        REPORT['error'] = error.code
    finally:
        try:  # Leave nothing behind: test folders, test addresses and every test copy -- also of an earlier, interrupted run.
            for fid in reversed(created):
                REPORT.setdefault('cleanup_folders', []).append(code_of(backend.delete_folder, fid, mail='trash'))
            leftovers = [f for f in backend.folders(refresh=True) if f['name'].startswith('HY测试') and not f['system']]
            for f in sorted(leftovers, key=lambda f: -f['depth']):  # Children first; their mail goes to the trash, never deleted with them.
                for child in [c for c in backend.folders(refresh=True) if c['parent'] == f['id']]:
                    REPORT.setdefault('cleanup_old_folders', []).append(code_of(backend.delete_folder, child['id'], mail='trash'))
                REPORT.setdefault('cleanup_old_folders', []).append(code_of(backend.delete_folder, f['id'], mail='trash'))
            lists = backend.sender_lists()
            for kind in ('safelist', 'refuselist'):
                for entry in [e for e in lists[kind] if e.startswith('hongyan-test-') and e.endswith('@example.invalid')]:
                    backend.remove_sender(kind, entry)
                    REPORT['cleanup_address'] = REPORT.get('cleanup_address', 0) + 1
            stray = [r['id'] for fid in ('1', '2', '3', '5') for r in backend.messages(fid, 0, 60)['rows'] if MARK in r['subject']]
            if stray:
                backend.move(stray, '4')
            trash = [r['id'] for r in backend.messages('4', 0, 100)['rows'] if MARK in r['subject']]
            if trash:
                REPORT['cleanup_purged'] = len(backend.purge(trash)['done'])
            REPORT['cleanup_left'] = sum(1 for fid in ('1', '2', '3', '4', '5') for r in backend.messages(fid, 0, 60)['rows'] if MARK in r['subject'])
            REPORT['cleanup_left_folders'] = sum(f['name'].startswith('HY测试') for f in backend.folders(refresh=True))
        except AppError as error:
            REPORT['cleanup_error'] = error.code
        backend.logout()
        shutil.rmtree(data, ignore_errors=True)
        out = ROOT / 'analysis' / f'mail-management-real-verification-{time.strftime("%Y-%m-%d")}.json'
        out.write_text(json.dumps(REPORT, ensure_ascii=False, indent=1), encoding='utf-8')
        print(json.dumps(REPORT, ensure_ascii=False, indent=1))
    return 0 if 'error' not in REPORT and REPORT.get('cleanup_left') == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
