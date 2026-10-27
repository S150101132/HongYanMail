"""Outlook .pst in the local archive: archive/pst.py against a stand-in for pypff (items
with MAPI properties built here), the catalog's .pst pack, and the backend settings. No
real .pst file."""
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from coremail_desktop.archive import pst
from coremail_desktop.archive.catalog import PST_PACK, ArchiveCatalog
from coremail_desktop.errors import AppError
from coremail_desktop.mime.parser import parse_mime
from tests.test_app_backend import XorProtector
from tests.test_compose_v05 import scratch


class Entry:
    def __init__(self, tag, value):
        self.entry_type, self.value = tag, value

    data_as_string = property(lambda self: self.value if isinstance(self.value, str) else None)
    data_as_integer = property(lambda self: self.value)
    data_as_datetime = property(lambda self: self.value)
    data = property(lambda self: self.value)


class Records:
    def __init__(self, props):
        self.entries = [Entry(tag, value) for tag, value in props.items()]


class Item:
    def __init__(self, props, identifier=1, attachments=(), recipients=(), plain=None, html=None, inner=None, data=b''):
        self.record_sets = [Records(props)]
        self.identifier, self.attachments = identifier, list(attachments)
        self.recipients = mock.Mock(record_sets=[Records(r) for r in recipients])
        self.plain_text_body, self.html_body = plain, html
        self._inner, self._data, self.size = inner, data, len(data)

    def seek_offset(self, offset):
        pass

    def read_buffer(self, size):
        return self._data[:size]

    def get_sub_item(self, index):
        return self._inner


class Folder:
    def __init__(self, messages=(), folders=()):
        self.messages, self.folders = list(messages), list(folders)
        self.number_of_sub_messages, self.number_of_sub_folders = len(self.messages), len(self.folders)

    def get_sub_message(self, index):
        return self.messages[index]

    def get_sub_folder(self, index):
        return self.folders[index]


def attachment(name, kind, data=b'', cid='', method=pst.BY_VALUE, inner=None):
    props = {pst.A_LONG: name, pst.A_MIME: kind, pst.A_METHOD: method}
    if cid:
        props[pst.A_CID] = cid
    return Item(props, attachments=(), data=data, inner=inner)


def note(i=1, kind='IPM.Note', subject=None, **extra):
    props = {pst.CLASS: kind, pst.SUBJECT: subject or f'BI UAT Sign-Off {i}', pst.SENT_NAME: '张三',
             pst.SENT_SMTP: f'z{i}@inspur.com', pst.MESSAGE_ID: f'<p{i}@example.invalid>', pst.SIZE: 2048,
             pst.SUBMITTED: datetime(2024, 3, 5, 6, 30, i), pst.CPID: 936}
    recipients = [{pst.R_TYPE: 1, pst.R_NAME: 'Bowen', pst.R_SMTP: 'me@inspur.com'},
                  {pst.R_TYPE: 2, pst.R_NAME: '李四', pst.R_ADDR: '/O=INSPUR/OU=EXCHANGE/CN=LISI'}]
    return Item(props, identifier=100 + i, recipients=recipients, **extra)


def mailbox():
    picture = attachment('logo.png', 'image/png', b'\x89PNG-1', cid='logo@x')
    sheet = attachment('计划.xlsx', 'application/vnd.ms-excel', b'XLSX-DATA')
    forwarded = attachment('原邮件', '', method=pst.EMBEDDED, inner=note(9, subject='Inner', plain='里面的正文'))
    rich = note(1, html='<p>请签收 UAT</p><img src="cid:logo@x">'.encode('gb18030'), plain='请签收 UAT',
                attachments=[sheet, picture, forwarded])
    plain = note(2, subject='CRM 网络', plain='DMZ 网络')
    sealed = note(3, kind='IPM.Note.SMIME', attachments=[attachment('smime.p7m', 'application/pkcs7-mime', b'\x30\x80CMS')])
    contact = Item({pst.CLASS: 'IPM.Contact', pst.SUBJECT: '王五'}, identifier=7)
    return Folder(folders=[Folder([rich, contact], [Folder([plain, sealed])])])


class FakePypff:
    tree = None

    def file(self):
        tree = self.tree
        return mock.Mock(open_file_object=lambda handle: None, root_folder=tree, close=lambda: None)


class PstTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(scratch(self))
        self.path = self.root / 'backup.pst'
        self.path.write_bytes(b'!BDN' + b'\0' * 60)
        self.fake = FakePypff()
        self.fake.tree = mailbox()
        patcher = mock.patch.object(pst, '_pypff', lambda: self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rows_come_from_the_properties(self):
        with pst.PstFile(self.path) as source:
            items = list(source.messages())
        self.assertEqual([place for place, _ in items], [[0, 0], [0, 1], [0, 0, 0], [0, 0, 1]])
        rich = items[0][1]
        props = pst._props(rich)
        row = pst.describe(rich, props, 'arc:x')
        self.assertEqual((row['subject'], row['from'], row['to'], row['cc']),
                         ('BI UAT Sign-Off 1', '张三 <z1@inspur.com>', 'Bowen <me@inspur.com>', '李四'))  # Exchange address dropped.
        self.assertEqual(row['attachments'], ['计划.xlsx', '原邮件'])  # The picture in the body is not listed.
        self.assertEqual((row['text'], row['messageId'], row['size'], row['encrypted']), ('请签收 UAT', 'p1@example.invalid', 2048, False))
        self.assertTrue(row['date'].startswith('2024-03-05'))
        self.assertFalse(pst.is_mail(pst._props(items[1][1])))
        self.assertTrue(pst.describe(items[3][1], pst._props(items[3][1]), 'arc:y')['encrypted'])

    def test_rebuilt_message_opens_like_any_other(self):
        with pst.PstFile(self.path) as source:
            raw = pst.to_eml(source.message([0, 0]))
            sealed = pst.to_eml(source.message([0, 0, 1]))
        doc = parse_mime(raw, 'arc:x')
        self.assertIn('请签收 UAT', doc.html)  # The HTML body was GB18030 (code page 936).
        self.assertIn('logo@x', doc.cid_resources)  # The picture shows in the body, not as an attachment.
        self.assertEqual([(a.name, a.media_type) for a in doc.attachments],
                         [('计划.xlsx', 'application/vnd.ms-excel'), ('原邮件', 'message/rfc822')])
        self.assertIn(b'application/pkcs7-mime', sealed)
        self.assertIn(b'Message-ID: <p1@example.invalid>', raw)

    def test_catalog_scans_the_pst_as_one_pack(self):
        catalog = ArchiveCatalog(self.root / 'c.archive.db', XorProtector())
        self.addCleanup(catalog.close)
        steps = []
        result = catalog.scan('', lambda done, total: steps.append((done, total)), pst=str(self.path))
        self.assertEqual((result['messages'], result['failed'], catalog.packs[PST_PACK]['other']), (3, [], 1))
        self.assertEqual(steps[-1], (4, 4))
        self.assertEqual(catalog.search(['dmz'])[1], 1)
        mid = catalog.search(['dmz'])[0][0]['id']
        self.assertIn('DMZ', parse_mime(catalog.raw('', mid, pst=str(self.path)), mid).text)
        self.assertEqual(catalog.scan('', pst=str(self.path))['skipped'], 1)  # Unchanged: not read again.
        self.fake.tree.folders[0].folders[0].messages.reverse()  # Moved within the file.
        with self.assertRaises(AppError) as raised:
            catalog.raw('', mid, pst=str(self.path))
        self.assertEqual(raised.exception.code, 'ARCHIVE_CHANGED')
        self.path.write_bytes(b'!BDN' + b'\0' * 61)  # Changed: rescanned, found at its new place.
        catalog.scan('', pst=str(self.path))
        self.assertIn('DMZ', parse_mime(catalog.raw('', mid, pst=str(self.path)), mid).text)
        catalog.scan('', pst='')  # The .pst turned off: its rows go.
        self.assertEqual(len(catalog.rows), 0)


class BackendPstTest(unittest.TestCase):
    def setUp(self):
        from coremail_desktop.app.backend import MailBackend
        from coremail_desktop.app.demo import DemoAdapter
        from tests.test_app_backend import local_services_stub
        self.root = Path(scratch(self))
        self.path = self.root / 'backup.pst'
        self.path.write_bytes(b'!BDN' + b'\0' * 60)
        fake = FakePypff()
        fake.tree = mailbox()
        patcher = mock.patch.object(pst, '_pypff', lambda: fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.backend = MailBackend(self.root / 'data', adapter_factory=DemoAdapter, protector=XorProtector(),
                                   services_factory=local_services_stub)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(self.backend.logout)

    def test_pst_alone_turns_the_archive_on(self):
        for bad in (str(self.root / 'nowhere.pst'), str(self.root)):
            with self.assertRaises(AppError) as raised:
                self.backend.set_archive_folder('', pst=bad)
            self.assertEqual(raised.exception.code, 'ARCHIVE_PST_NOT_FOUND')
        status = self.backend.set_archive_folder('', pst=f'"{self.path}"')  # Pasted with quotes from Explorer.
        self.assertEqual((status['folder'], status['pst']), ('', str(self.path)))
        self.assertEqual(self.backend.archive_scan()['messages'], 3)
        self.assertEqual(self.backend.archive_view()['total'], 3)
        mid = self.backend.search_archive('dmz')['rows'][0]['id']
        message = self.backend.open_message(mid)
        self.assertEqual((message['archived'], message['subject']), (True, 'CRM 网络'))
        self.assertIn('DMZ', message['text'])
        self.assertEqual(self.backend.set_archive_folder('')['pst'], str(self.path))  # pst None keeps it.
        self.assertEqual(self.backend.set_archive_folder('', pst='')['messages'], 0)
        self.assertEqual(self.backend.archive_view(), {'rows': [], 'total': 0})

    def test_bridge(self):
        from coremail_desktop.app import shell
        from coremail_desktop.ui.messages import MESSAGES
        self.assertIn('ARCHIVE_PST_NOT_FOUND', MESSAGES)
        self.assertTrue(any(hasattr(cls, 'native_pickPst') for cls in vars(shell).values() if isinstance(cls, type)))
        web = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'
        self.assertIn("api('pickPst')", (web / 'archive.js').read_text(encoding='utf-8'))
        self.assertIn('id="archive-pst"', (web / 'index.html').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
