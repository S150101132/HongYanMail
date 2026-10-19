"""Folder management, 全部标为已读, spam and sender lists, permanent deletion, attachment preview (v0.5)."""
import base64
import shutil
import tempfile
import unittest
from pathlib import Path

from coremail_desktop.app.backend import MailBackend, preview_kind
from coremail_desktop.app.demo import DemoAdapter, _demo_chart, _demo_pdf
from coremail_desktop.errors import AppError
from tests.test_app_backend import XorProtector, local_services_stub

# getAllFolders shapes as the deployment returned them (analysis/mail-management-shape-2026-10-19.json).
FOLDERS = [{'id': 1, 'name': 'Inbox', 'flags': {'system': True}, 'keepPeriod': -1, 'stats': {'messageCount': 3, 'unreadMessageCount': 1}},
           {'id': 4, 'name': 'Trash', 'flags': {'system': True}, 'stats': {'messageCount': 2}},
           {'id': 5, 'name': 'Spam', 'flags': {'system': True}, 'stats': {'messageCount': 0}},
           {'id': 975274, 'name': 'A', 'flags': {}, 'stats': {'messageCount': 0, 'unreadMessageCount': 0},
            'children': [{'id': 975276, 'name': 'B', 'flags': {}, 'stats': {'messageCount': 5},
                          'children': [{'id': 975279, 'name': 'C', 'flags': {}, 'stats': {}}]}]}]


class Recording:
    def __init__(self, unread_pages=None, attrs=None):
        self.calls, self.folders, self.unread_pages, self.attrs = [], [dict(f) for f in FOLDERS], list(unread_pages or []), attrs or {}

    def call(self, op, params):
        self.calls.append((op, params))
        if op == 'mbox:getAllFolders':
            return {'code': 'S_OK', 'var': self.folders}
        if op == 'mbox:createUserFolders':
            item = params['items'][0]
            parent = next((f for f in self.folders if f['id'] == item['parent']), None)
            (parent.setdefault('children', []) if parent else self.folders).append({'id': 975300, 'name': item['name'], 'flags': {}, 'stats': {}})
            return {'code': 'S_OK', 'var': [975300]}
        if op == 'mbox:listMessages':
            page = self.unread_pages.pop(0) if self.unread_pages else []
            return {'code': 'S_OK', 'var': [{'id': i, 'flags': {'read': False}} for i in page], 'total': 0}
        if op == 'mbox:updateMessageInfos':
            return {'code': 'S_OK', 'originalMsgInfos': [{'id': i} for i in params['ids']]}
        if op == 'user:getAttrs':
            return {'code': 'S_OK', 'var': self.attrs}
        return {'code': 'S_OK', 'var': None}


class AdapterTest(unittest.TestCase):
    def adapter(self, **kw):
        from coremail_desktop.protocol.coremail import CoremailAdapter
        adapter = CoremailAdapter()
        adapter._session = Recording(**kw)
        return adapter

    def test_allowed_and_folder_shape(self):
        from coremail_desktop.protocol.coremail import DesktopSession
        for op in ('mbox:createUserFolders', 'mbox:updateFolders', 'mbox:deleteFolders', 'mbox:emptyFolder', 'mbox:deleteMessages', 'user:getAttrs', 'user:setAttrs'):
            self.assertIn(op, DesktopSession.ALLOWED)
        folders = {f.remote_id: f for f in self.adapter().list_folders()}
        self.assertTrue(folders['1'].system)
        self.assertFalse(folders['975274'].system)
        self.assertEqual((folders['975274'].parent, folders['975276'].parent, folders['975279'].parent), (None, '975274', '975276'))
        self.assertEqual([f.remote_id for f in self.adapter().list_folders()][-3:], ['975274', '975276', '975279'])  # Tree order kept.

    def test_folder_names_follow_webmail(self):
        from coremail_desktop.protocol.coremail import CoremailAdapter as A
        self.assertEqual(A.folder_name('  项目  '), '项目')
        self.assertEqual(A.folder_name('中' * 12), '中' * 12)
        for bad in ('', '   ', 'a/b', 'x' * 25, '中' * 13, 'a,b', 'a^b', "it's", 'tab\tname'):
            with self.assertRaises(AppError, msg=bad):
                A.folder_name(bad)

    def test_folder_calls(self):
        adapter = self.adapter()
        self.assertEqual(adapter.create_folder('New', '975274'), '975300')
        self.assertEqual(next(f for f in adapter.list_folders() if f.remote_id == '975300').parent, '975274')
        self.assertIn(('mbox:createUserFolders', {'items': [{'name': 'New', 'parent': 975274}]}), adapter._session.calls)
        adapter.rename_folder('975274', 'Renamed')
        self.assertIn(('mbox:updateFolders', {'items': [{'id': 975274, 'name': 'Renamed'}]}), adapter._session.calls)
        adapter.delete_folder('975274')
        self.assertIn(('mbox:deleteFolders', {'ids': [975274]}), adapter._session.calls)
        for folder, code in (('1', 'SYSTEM_FOLDER'), ('4', 'SYSTEM_FOLDER'), ('975276', 'FOLDER_NOT_EMPTY'), ('999', 'INVALID_FOLDER')):
            with self.assertRaises(AppError) as caught:
                adapter.delete_folder(folder)
            self.assertEqual(caught.exception.code, code)
        with self.assertRaises(AppError):
            adapter.create_folder('x', '1;drop')

    def test_purge_spam_and_lists(self):
        adapter = self.adapter(unread_pages=[[f'm{i}' for i in range(100)], ['m100']], attrs={'safelist': 'a@x.example, *@y.example', 'refuselist': ''})
        adapter.delete_messages(['m1', 'm2', 'm1'])
        self.assertIn(('mbox:deleteMessages', {'ids': ['m1', 'm2']}), adapter._session.calls)
        adapter.empty_folder('4')
        self.assertIn(('mbox:emptyFolder', {'id': 4, 'permanently': True}), adapter._session.calls)
        self.assertEqual(adapter.report_spam(['m1']), ['m1'])
        op, params = adapter._session.calls[-1]
        self.assertEqual((op, params['attrs'], params['reportType']), ('mbox:updateMessageInfos', {'fid': 5}, 'spam'))
        adapter.report_spam(['m1'], False)
        self.assertEqual((adapter._session.calls[-1][1]['attrs'], adapter._session.calls[-1][1]['reportType']), ({'fid': 1}, 'notspam'))
        self.assertEqual(len(adapter.unread_ids('1')), 101)  # Two pages of the read:false filter.
        listing = [p for op, p in adapter._session.calls if op == 'mbox:listMessages']
        self.assertEqual(listing[0]['filter'], {'flags': {'read': False}})
        self.assertEqual(listing[1]['start'], 100)
        self.assertEqual(adapter.sender_lists(), {'safelist': ['a@x.example', '*@y.example'], 'refuselist': []})
        adapter.set_sender_lists({'refuselist': ['b@z.example', '*@w.example']})
        self.assertEqual(adapter._session.calls[-1], ('user:setAttrs', {'attrs': {'refuselist': 'b@z.example,*@w.example'}}))
        with self.assertRaises(AppError):
            adapter.set_sender_lists({'other': []})
        with self.assertRaises(AppError):
            adapter.delete_messages([])


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.backend = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'x')
        self.backend.folders()
        self.backend.messages('1')

    def tearDown(self):
        self.backend.logout()
        shutil.rmtree(self.directory, ignore_errors=True)

    def folder(self, fid):
        return next(f for f in self.backend.folders(refresh=False) if f['id'] == fid)

    def code(self, call, *args, **kw):
        with self.assertRaises(AppError) as caught:
            call(*args, **kw)
        return caught.exception.code

    def test_folder_tree(self):
        b = self.backend
        top = b.create_folder('客户')['id']
        east = b.create_folder('华东', top)['id']
        res = b.create_folder('上海', east)
        order = [f['id'] for f in res['folders']]
        self.assertEqual(order[order.index(top):order.index(top) + 3], [top, east, res['id']])  # Children right after their parent.
        self.assertEqual([self.folder(i)['depth'] for i in (top, east, res['id'])], [0, 1, 2])
        deep = b.create_folder('五', b.create_folder('四', res['id'])['id'])['id']
        self.assertEqual(self.folder(deep)['depth'], 4)
        self.assertEqual(self.code(b.create_folder, '六', deep), 'FOLDER_DEPTH_LIMIT')  # Five levels at most.
        self.assertEqual(self.code(b.create_folder, '客户'), 'FOLDER_EXISTS')
        self.assertEqual(self.code(b.create_folder, '子', '1'), 'SYSTEM_FOLDER')
        self.assertEqual(self.code(b.create_folder, 'a/b'), 'INVALID_FOLDER_NAME')
        b.rename_folder(east, '华东区')
        self.assertEqual(self.folder(east)['name'], '华东区')
        self.assertEqual(self.code(b.rename_folder, '4', 'x'), 'SYSTEM_FOLDER')
        self.assertEqual(self.code(b.delete_folder, top), 'FOLDER_HAS_CHILDREN')
        b.shared_mailboxes()
        self.assertEqual(self.code(b.delete_folder, '1@rd'), 'OPERATION_NOT_ALLOWED')  # A shared mailbox's folders are its owner's.

    def test_delete_folder_moves_mail_to_trash_first(self):
        b = self.backend
        fid = b.create_folder('临时')['id']
        ids = [r['id'] for r in b.messages('1', 0, 5)['rows']]
        b.move(ids, fid)
        trash_before = self.folder('4')['total']
        self.assertEqual(self.code(b.delete_folder, fid, mail='keep'), 'FOLDER_NOT_EMPTY')
        self.assertIn(fid, {f['id'] for f in b.folders()})
        res = b.delete_folder(fid)
        self.assertEqual(res['moved'], 5)
        self.assertNotIn(fid, {f['id'] for f in res['folders']})
        self.assertEqual(self.folder('4')['total'], trash_before + 5)
        self.assertEqual({b.adapter.rows[i]['fid'] for i in ids}, {'4'})  # Nothing deleted with the folder.

    def test_mark_all_read(self):
        b = self.backend
        unread = self.folder('1')['unread']
        self.assertGreater(unread, 0)
        res = b.mark_all_read('1')
        self.assertEqual((res['done'], res['failed']), (unread, 0))
        self.assertEqual(self.folder('1')['unread'], 0)
        self.assertFalse(any(not r['flags']['read'] for r in b.adapter.rows.values() if r['fid'] == '1'))
        self.assertEqual(b.mark_all_read('1')['done'], 0)
        self.assertEqual(self.code(b.mark_all_read, '999'), 'INVALID_FOLDER')
        b.shared_mailboxes()
        self.assertGreater(b.mark_all_read('1@rd')['done'], 0)

    def test_spam_report_and_lists(self):
        b = self.backend
        rows = b.messages('1', 0, 3)['rows']
        res = b.report_spam([rows[0]['id'], rows[1]['id']], True, block=True)
        self.assertEqual(len(res['done']), 2)
        self.assertEqual(b.adapter.reports[-1][0], 'spam')
        self.assertEqual({b.adapter.rows[r['id']]['fid'] for r in rows[:2]}, {'5'})
        self.assertIn(rows[0]['fromEmail'], res['lists']['refuselist'])
        back = b.report_spam(rows[0]['id'], False, block=True)
        self.assertEqual(b.adapter.rows[rows[0]['id']]['fid'], '1')
        self.assertIn(rows[0]['fromEmail'], back['lists']['safelist'])
        self.assertNotIn(rows[0]['fromEmail'], back['lists']['refuselist'])  # Moved over, never on both lists.
        b.shared_mailboxes()
        self.assertEqual(self.code(b.report_spam, ['demo1@rd']), 'SPAM_NOT_ALLOWED')
        self.assertEqual(self.code(b.add_senders, 'refuselist', 'demo@example.invalid'), 'SENDER_IS_SELF')
        self.assertEqual(self.code(b.add_senders, 'refuselist', '@example.invalid'), 'SENDER_IS_SELF')
        self.assertEqual(self.code(b.add_senders, 'refuselist', 'not an address'), 'INVALID_SENDER')
        self.assertEqual(self.code(b.add_senders, 'other', 'a@b.example'), 'INVALID_SENDER')
        lists = b.add_senders('refuselist', ['@Junk.Example', 'X@Y.example'])
        self.assertTrue({'*@junk.example', 'x@y.example'} <= set(lists['refuselist']))
        self.assertNotIn('x@y.example', b.remove_sender('refuselist', 'X@y.example')['refuselist'])
        b.adapter.lists['refuselist'] = [f'u{i}@spam.example' for i in range(500)]
        self.assertEqual(self.code(b.add_senders, 'refuselist', 'one@more.example'), 'SENDER_LIST_LIMIT')

    def test_permanent_deletion_only_in_trash_and_spam(self):
        b = self.backend
        rows = b.messages('1', 0, 4)['rows']
        self.assertEqual(self.code(b.purge, [rows[0]['id']]), 'PURGE_NOT_ALLOWED')
        self.assertEqual(self.code(b.empty_folder, '1'), 'PURGE_NOT_ALLOWED')
        self.assertEqual(self.code(b.empty_folder, '100'), 'PURGE_NOT_ALLOWED')
        b.delete([r['id'] for r in rows[:3]])
        b.report_spam(rows[3]['id'])
        trash = self.folder('4')['total']
        self.assertEqual(self.code(b.purge, [rows[0]['id'], 'demo99']), 'PURGE_NOT_ALLOWED')  # One outside the trash refuses all.
        res = b.purge([rows[0]['id']])
        self.assertEqual(res['done'], [rows[0]['id']])
        self.assertNotIn(rows[0]['id'], b.adapter.rows)
        self.assertIsNone(b.cache.summary(rows[0]['id']))
        self.assertEqual(self.folder('4')['total'], trash - 1)
        self.assertEqual(b.empty_folder('4')['removed'], trash - 1)
        self.assertEqual(self.folder('4')['total'], 0)
        self.assertFalse(b.cache.known_ids('4'))
        b.empty_folder('5')
        self.assertNotIn(rows[3]['id'], b.adapter.rows)
        self.assertEqual(len([r for r in b.adapter.rows.values() if r['fid'] == '1']), 116)  # The inbox is untouched.

    def test_attachment_preview(self):
        b = self.backend
        message = b.open_message('demo1')
        parts = {a['name']: a['part'] for a in message['attachments']}
        image = b.attachment_preview('demo1', parts['进度图表.png'])
        self.assertEqual(image['kind'], 'image')
        self.assertTrue(base64.b64decode(image['data'].split(',', 1)[1]).startswith(b'\x89PNG'))
        self.assertEqual(b.attachment_preview('demo1', parts['评审报告.pdf'])['kind'], 'pdf')
        text = b.attachment_preview('demo1', parts['会议纪要.txt'])
        self.assertEqual((text['kind'], text['text'].strip(), text['truncated']), ('text', '演示附件内容', False))
        self.assertEqual(preview_kind('a.png', b'<svg onload=x>'), None)  # Judged by the bytes, never the name alone.
        self.assertEqual(preview_kind('a.svg', b'<svg/>'), None)
        self.assertEqual(preview_kind('a.html', b'<html>'), None)
        self.assertEqual(preview_kind('a.txt', b'MZ\x00\x00'), None)
        self.assertEqual(preview_kind('x.bin', b'RIFF\x00\x00\x00\x00WEBPVP8 '), 'image/webp')
        self.assertEqual(preview_kind('a.pdf', _demo_pdf()), 'pdf')
        self.assertEqual(preview_kind('a.png', _demo_chart()), 'image/png')


class PdfRenderTest(unittest.TestCase):
    """The shell's QtPdf path: pages come back as PNG images, damaged files as a fixed code."""
    def test_render_pages(self):
        import tests.qt_test_support  # noqa: F401 - releases Qt windows at exit
        from types import SimpleNamespace
        from PySide6.QtWidgets import QApplication
        from coremail_desktop.app.shell import AppShell
        QApplication.instance() or QApplication([])
        files = {'p': ('a.pdf', 'application/pdf', _demo_pdf()), 'bad': ('b.pdf', 'application/pdf', b'%PDF-1.4 broken'),
                 'img': ('c.png', 'image/png', _demo_chart())}
        host = SimpleNamespace(backend=SimpleNamespace(attachment=lambda mid, part: files[part]), _pdf=None)
        for name in ('_pdf_document', 'native_previewPdfPage', 'native_closePreview'):
            setattr(host, name, getattr(AppShell, name).__get__(host))
        first = host.native_previewPdfPage('m', 'p', 0, 600)
        self.assertEqual((first['page'], first['pages'], first['width']), (0, 2, 600))
        self.assertAlmostEqual(first['height'], 849, delta=1)
        self.assertTrue(base64.b64decode(first['data'].split(',', 1)[1]).startswith(b'\x89PNG'))
        self.assertEqual(host.native_previewPdfPage('m', 'p', 9, 99999)['page'], 1)  # Clamped page and width.
        self.assertEqual(host.native_previewPdfPage('m', 'p', 1, 99999)['width'], 3000)
        for part in ('bad', 'img'):
            with self.assertRaises(AppError) as caught:
                host.native_previewPdfPage('m', part)
            self.assertEqual(caught.exception.code, 'PREVIEW_UNAVAILABLE')
        self.assertTrue(host.native_closePreview())
        self.assertIsNone(host._pdf)


class WebAssetsTest(unittest.TestCase):
    WEB = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'

    def test_hooks_and_methods(self):
        html = (self.WEB / 'index.html').read_text(encoding='utf-8')
        self.assertLess(html.index('<script src="rules.js">'), html.index('<script src="manage.js">'))
        self.assertIn('id="side-scroll"', html)
        script = (self.WEB / 'manage.js').read_text(encoding='utf-8')
        for name in ("api('createFolder'", "api('deleteFolder'", "api('markAllRead'", "api('reportSpam'", "api('purge'", "api('emptyFolder'",
                     "api('addSenders'", "api('previewAttachment'", "api('previewPdfPage'", 'confirmTwice'):
            self.assertIn(name, script)
        self.assertNotIn('innerHTML = res.text', script)  # Text previews are set as text, never parsed as HTML.
        from coremail_desktop.app.shell import AppShell, BACKEND_METHODS
        for method in ('createFolder', 'renameFolder', 'deleteFolder', 'markAllRead', 'reportSpam', 'purge', 'emptyFolder', 'senderLists',
                       'addSenders', 'removeSender', 'previewAttachment'):
            self.assertTrue(hasattr(MailBackend, BACKEND_METHODS[method]), method)
        self.assertTrue(hasattr(AppShell, 'native_previewPdfPage'))

    def test_messages_and_packaging(self):
        from coremail_desktop.ui.messages import MESSAGES
        for code in ('INVALID_FOLDER', 'INVALID_FOLDER_NAME', 'FOLDER_EXISTS', 'FOLDER_DEPTH_LIMIT', 'FOLDER_HAS_CHILDREN', 'FOLDER_NOT_EMPTY',
                     'SYSTEM_FOLDER', 'PURGE_NOT_ALLOWED', 'SPAM_NOT_ALLOWED', 'INVALID_SENDER', 'SENDER_IS_SELF', 'SENDER_LIST_LIMIT', 'PREVIEW_UNAVAILABLE'):
            self.assertIn(code, MESSAGES)
        spec = (Path(__file__).resolve().parents[1] / 'packaging' / 'desktop.spec').read_text(encoding='utf-8')
        self.assertIn("'PySide6.QtPdf'", spec.split('hiddenimports=')[1].split('\n')[0])
        self.assertNotIn("'Qt6Pdf.dll'", spec)
        self.assertNotIn("'PySide6.QtPdf',", spec.split('EXCLUDED_MODULES')[1].split(']')[0])


if __name__ == '__main__':
    unittest.main()
