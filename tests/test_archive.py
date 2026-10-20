"""本地存档: the .cmpack decoder against synthetic packs built here (same layout as the
official client's, see archive/reader.py), and the encrypted catalog. No real archive."""
import hashlib
import os
import struct
import unittest
from pathlib import Path

import zstandard

from coremail_desktop.archive import reader
from coremail_desktop.archive.catalog import ArchiveCatalog, archive_dir
from coremail_desktop.errors import AppError
from tests.test_app_backend import XorProtector
from tests.test_compose_v05 import scratch


def mail(i=1, body='正文内容 UAT 签收', subject=None, date='Tue, 10 Feb 2026 14:17:45 +0300', extra=''):
    subject = subject or f'BI UAT Sign-Off {i}'
    return (f'From: "Zeeshan" <z{i}@inspur.com>\r\nTo: "Bowen" <me@inspur.com>\r\nSubject: {subject}\r\n'
            f'Date: {date}\r\nMessage-ID: <m{i}@example.invalid>\r\nMIME-Version: 1.0\r\n{extra}'
            f'Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: 8bit\r\n\r\n{body}\r\n').encode('utf-8')


def record(raw, key=0x5a, chunk=40):
    """cmms framing: masked original in Zstandard chunks, a zero chunk, the CMCT table."""
    masked = raw.translate(bytes(b ^ key for b in range(256)))
    padded = masked + b'\0' * 7  # The client pads; the header's length cuts it off.
    out, table = bytearray(b'cmms' + b'\0' * 12 + struct.pack('<Q', len(masked)) + b'\0' * 40), []
    for start in range(0, len(padded), chunk):
        piece = padded[start:start + chunk]
        packed = zstandard.ZstdCompressor().compress(piece)
        table.append((len(out), len(piece), len(packed)))
        out += b'CMCD' + struct.pack('<II', len(piece), len(packed)) + b'\0' * 20 + packed
    out += b'CMCD' + b'\0' * 28
    for row in table:
        out += struct.pack('<QII', *row)
    out += b'CMCT' + struct.pack('<I', len(table) * 16)
    return bytes(out)


def pack(records, names=None):
    """A CMDR pack: directory page 0, an extension page past 15 entries, then for each
    record a PGFN page and its data pages."""
    pages = {}
    count = len(records)
    extension = 1 if count > 15 else 0
    next_page = 2 if extension else 1
    directory = []
    for i, data in enumerate(records):
        pgfn, first = next_page, next_page + 1
        n = max(1, -(-len(data) // 1024))
        page = bytearray(1024)
        page[:4] = b'PGFN'
        struct.pack_into('<Q', page, 24, len(data))
        struct.pack_into('<QQ', page, 64, first, n)
        pages[pgfn] = bytes(page)
        for k in range(n):
            pages[first + k] = data[k * 1024:(k + 1) * 1024].ljust(1024, b'\0')
        next_page = first + n
        directory.append((pgfn, (names or {}).get(i, f'{i:02x}.data')))
    head = bytearray(512)
    head[:4] = b'CMDR'
    struct.pack_into('<I', head, 260, 1024)
    body = bytearray(1024 * next_page)
    for number, content in pages.items():
        body[number * 1024:(number + 1) * 1024] = content
    out = head + body
    struct.pack_into('<QQQ', out, 512, extension, 1 if extension else 0, count)
    for i, (block, name) in enumerate(directory):
        at = 576 + i * 64 if i < 15 else 512 + extension * 1024 + (i - 15) * 64
        struct.pack_into('<Q', out, at, block)
        out[at + 8:at + 8 + len(name)] = name.encode()
    return bytes(out)


class ReaderTest(unittest.TestCase):
    def test_record_round_trip_and_mask(self):
        raw = mail()
        for key in (0, 0x5a, 0xff):
            self.assertEqual(reader.decode(record(raw, key)), raw)

    def test_bare_frame_record(self):
        raw = mail()
        bare = b'junk' + zstandard.ZstdCompressor().compress(raw.translate(bytes(b ^ 7 for b in range(256))))
        self.assertEqual(reader.decode(bare), raw)

    def test_damaged_records_are_refused(self):
        good = record(mail())
        for bad in (good[:-6], good.replace(b'CMCT', b'XXXX'), b'cmms' + good[4:60],
                    good[:16] + struct.pack('<Q', 10 ** 6) + good[24:],  # Length beyond the chunks.
                    record(b'\x00\x01 not a mail at all ' * 10), b'no frame here'):
            with self.assertRaises(AppError) as raised:
                reader.decode(bad)
            self.assertEqual(raised.exception.code, 'ARCHIVE_UNSUPPORTED')
        with self.assertRaises(AppError):
            reader.decode(good, limit=10)

    def test_directory_with_extension_page(self):
        raws = [mail(i) for i in range(17)]
        data = pack([record(r) for r in raws])
        found = reader.entries(data)
        self.assertEqual(len(found), 17)
        self.assertEqual([reader.decode(data[e['offset']:e['offset'] + e['length']]) for e in found], raws)

    def test_bad_directories(self):
        data = bytearray(pack([record(mail())]))
        for change in (lambda d: d.__setitem__(slice(0, 4), b'XXXX'), lambda d: struct.pack_into('<I', d, 260, 4096),
                       lambda d: struct.pack_into('<Q', d, 528, 40)):
            copy = bytearray(data)
            change(copy)
            with self.assertRaises(AppError):
                reader.entries(bytes(copy))
        copy = bytearray(data)
        struct.pack_into('<Q', copy, 576, 999)  # Entry page out of range: that entry only.
        self.assertIn('error', reader.entries(bytes(copy))[0])
        named = pack([record(mail())], names={0: 'x.bin'})
        self.assertIn('error', reader.entries(named)[0])

    def test_read_record_checks_the_stamp(self):
        directory = Path(scratch(self))
        path = directory / '00.cmpack'
        raw = mail()
        path.write_bytes(pack([record(raw)]))
        data, stamp = reader.read_pack(path)
        entry = reader.entries(data)[0]
        self.assertEqual(reader.read_record(path, entry['offset'], entry['length'], stamp), raw)
        with self.assertRaises(AppError) as raised:
            reader.read_record(path, entry['offset'], entry['length'], (stamp[0] + 1, stamp[1]))
        self.assertEqual(raised.exception.code, 'ARCHIVE_CHANGED')
        with self.assertRaises(AppError) as raised:
            reader.read_record(directory / 'gone.cmpack', 0, 10, stamp)
        self.assertEqual(raised.exception.code, 'ARCHIVE_NOT_FOUND')


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(scratch(self))
        self.account = self.root / 'me@inspur.com'
        (self.account / 'archmails').mkdir(parents=True)
        self.db = self.root / 'cache' / 'acct.archive.db'

    def catalog(self):
        value = ArchiveCatalog(self.db, XorProtector())
        self.addCleanup(value.close)
        return value

    def write(self, name, raws, **kwargs):
        (self.account / 'archmails' / name).write_bytes(pack([record(r, **kwargs) for r in raws]))

    def test_scan_search_read_and_rescan(self):
        self.write('00.cmpack', [mail(1), mail(2, body='网络申请 DMZ', subject='CRM 网络')])
        self.write('01.cmpack', [mail(3, date='Mon, 05 Jan 2026 09:00:00 +0300')])
        catalog = self.catalog()
        steps = []
        result = catalog.scan(self.account, progress=lambda d, t: steps.append((d, t)))
        self.assertEqual((result['packs'], result['scanned'], result['messages'], result['failed']), (2, 2, 3, []))
        self.assertEqual(steps[-1], (2, 2))
        rows, total = catalog.search(['uat', '签收'])
        self.assertEqual(total, 2)
        self.assertTrue(all(r['id'].startswith('arc:') and r['archived'] for r in rows))
        self.assertEqual(catalog.search(['dmz'])[1], 1)
        self.assertEqual(catalog.search(['网络'], subject_only=True)[1], 1)
        self.assertEqual(catalog.search([], sender='z3@')[1], 1)
        from coremail_desktop.app.cache import date_ts
        self.assertEqual(catalog.search([], since=date_ts('2026-02-01'), until=date_ts('2026-03-01'))[1], 2)
        listing, total = catalog.listing()
        self.assertEqual((total, listing[-1]['subject']), (3, 'BI UAT Sign-Off 3'))
        self.assertEqual(catalog.raw(self.account, rows[0]['id']), mail(int(rows[0]['subject'][-1])))
        # Unchanged packs are skipped; a new one is read; nothing is written to the archive.
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.account / 'archmails').iterdir()}
        self.write('02.cmpack', [mail(4)])
        again = catalog.scan(self.account)
        self.assertEqual((again['scanned'], again['skipped'], again['messages']), (1, 2, 4))
        for name, digest in before.items():
            self.assertEqual(hashlib.sha256((self.account / 'archmails' / name).read_bytes()).hexdigest(), digest)

    def test_catalog_is_encrypted_and_reloads(self):
        self.write('00.cmpack', [mail(1, body='机密正文 ABCDEF')])
        catalog = self.catalog()
        catalog.scan(self.account)
        catalog.close()
        raw = self.db.read_bytes() + (self.db.with_suffix('.db-wal').read_bytes() if self.db.with_suffix('.db-wal').exists() else b'')
        for secret in ('机密正文'.encode(), b'ABCDEF', b'BI UAT', b'inspur.com'):
            self.assertNotIn(secret, raw)
        reopened = self.catalog()
        self.assertEqual(len(reopened.rows), 1)
        self.assertEqual(reopened.scan(self.account)['skipped'], 1)

    def test_removed_and_changed_packs(self):
        self.write('00.cmpack', [mail(1)])
        self.write('01.cmpack', [mail(2)])
        catalog = self.catalog()
        catalog.scan(self.account)
        mid = catalog.search(['sign-off 1'])[0][0]['id']
        os.remove(self.account / 'archmails' / '01.cmpack')
        self.write('00.cmpack', [mail(1), mail(5)])
        result = catalog.scan(self.account)
        self.assertEqual(result['messages'], 2)
        self.assertEqual(sorted(r['subject'] for r in catalog.listing()[0]), ['BI UAT Sign-Off 1', 'BI UAT Sign-Off 5'])
        self.assertEqual(catalog.raw(self.account, mid), mail(1))

    def test_failures_are_reported_not_fatal(self):
        good = record(mail(1))
        self.write('00.cmpack', [mail(1)])
        (self.account / 'archmails' / '01.cmpack').write_bytes(pack([good, b'cmms' + b'\0' * 70]))
        (self.account / 'archmails' / '02.cmpack').write_bytes(b'not a pack' * 200)
        (self.account / 'archmails' / 'notes.txt').write_bytes(b'ignored')
        catalog = self.catalog()
        result = catalog.scan(self.account)
        self.assertEqual(result['messages'], 1)  # Same original twice: one row.
        self.assertEqual(sorted((f['pack'], f['reason']) for f in result['failed']),
                         [('01.cmpack', 'ARCHIVE_UNSUPPORTED'), ('02.cmpack', 'ARCHIVE_UNSUPPORTED')])
        self.assertEqual(catalog.get('lastScan')['failedCount'], 2)
        again = catalog.scan(self.account)  # Unchanged packs are not retried; their failures still count.
        self.assertEqual((again['skipped'], again['scanned'], catalog.get('lastScan')['failedCount']), (2, 0, 2))

    def test_changed_original_is_refused(self):
        self.write('00.cmpack', [mail(1)])
        catalog = self.catalog()
        catalog.scan(self.account)
        mid = next(iter(catalog.rows))
        self.write('00.cmpack', [mail(1, body='改过')])
        with self.assertRaises(AppError) as raised:
            catalog.raw(self.account, mid)
        self.assertEqual(raised.exception.code, 'ARCHIVE_CHANGED')

    def test_missing_folder_and_paths(self):
        with self.assertRaises(AppError) as raised:
            self.catalog().scan(self.root / 'nowhere')
        self.assertEqual(raised.exception.code, 'ARCHIVE_NOT_FOUND')
        self.assertEqual(archive_dir(self.account / 'archmails'), self.account / 'archmails')
        self.assertEqual(archive_dir(self.account), self.account / 'archmails')

    def test_encrypted_mail_keeps_its_envelope_only(self):
        smime = mail(1, body='MIIBencryptedpayload', extra='').replace(
            b'Content-Type: text/plain; charset=utf-8', b'Content-Type: application/pkcs7-mime; smime-type=enveloped-data; name=smime.p7m')
        self.write('00.cmpack', [smime])
        catalog = self.catalog()
        catalog.scan(self.account)
        row = next(iter(catalog.rows.values()))
        self.assertTrue(row['encrypted'])
        self.assertEqual((row['text'], row['subject']), ('', 'BI UAT Sign-Off 1'))


class BackendArchiveTest(unittest.TestCase):
    def setUp(self):
        from coremail_desktop.app.backend import MailBackend
        from coremail_desktop.app.demo import DemoAdapter
        from tests.test_app_backend import local_services_stub
        self.root = Path(scratch(self))
        self.account = self.root / 'email' / 'me@inspur.com'
        (self.account / 'archmails').mkdir(parents=True)
        (self.account / 'archmails' / '00.cmpack').write_bytes(pack([record(mail(1)), record(mail(2, body='DMZ 网络', subject='CRM 网络'))]))
        self.backend = MailBackend(self.root / 'data', adapter_factory=DemoAdapter, protector=XorProtector(),
                                   services_factory=local_services_stub)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(self.backend.logout)

    def test_off_until_a_folder_is_set(self):
        self.assertEqual(self.backend.archive_status()['folder'], '')
        self.assertEqual(self.backend.archive_view(), {'rows': [], 'total': 0})
        self.assertEqual(self.backend.search_archive('uat')['total'], 0)
        for call in (self.backend.archive_scan, lambda: self.backend.set_archive_folder(str(self.root / 'nowhere'))):
            with self.assertRaises(AppError) as raised:
                call()
            self.assertEqual(raised.exception.code, 'ARCHIVE_NOT_FOUND')

    def test_scan_view_search_and_read_only_open(self):
        self.backend.set_archive_folder(str(self.account))
        steps = []
        status = self.backend.archive_scan(progress=steps.append)
        self.assertEqual((status['messages'], status['failed'], steps[-1]), (2, [], {'done': 1, 'total': 1}))
        self.assertIsNone(self.backend.archive_status()['scanning'])
        view = self.backend.archive_view()
        self.assertEqual(view['total'], 2)
        self.assertEqual(self.backend.search_archive('dmz')['total'], 1)
        self.assertEqual(self.backend.search_archive('', {'since': '2026-02-01', 'until': '2026-02-28'})['total'], 2)
        self.assertEqual(self.backend.search_archive('uat', {'unread': True})['total'], 0)
        mid = self.backend.search_archive('dmz')['rows'][0]['id']
        message = self.backend.open_message(mid)
        self.assertEqual((message['imported'], message['archived'], message['subject']), (True, True, 'CRM 网络'))
        self.assertIn('DMZ', message['text'])
        self.assertEqual(self.backend.compose('reply', mid)['mode'], 'new')  # Not on the server: no threading there.
        # A new backend session reads the encrypted catalog back without scanning.
        self.backend.logout()
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.assertEqual(self.backend.archive_status()['messages'], 2)

    def test_ask_mailbox_searches_the_archive(self):
        from coremail_desktop.app.demo import DemoAiClient
        self.backend.set_archive_folder(str(self.account))
        self.backend.archive_scan()
        self.backend.ai_client = DemoAiClient()
        result = self.backend.ask_mailbox('CRM')
        self.assertTrue(any(s['mid'].startswith('arc:') for s in result['sources']))
        self.assertTrue(self.backend.set_archive_folder('')['folder'] == '')
        self.assertFalse(any(s['mid'].startswith('arc:') for s in self.backend.ask_mailbox('CRM')['sources']))

    def test_bridge(self):
        from coremail_desktop.app import shell
        self.assertEqual(shell.ARCHIVE_METHODS, {'archiveScan'})
        for name in ('archiveStatus', 'setArchiveFolder', 'archiveScan', 'archiveView', 'searchArchive'):
            self.assertIn(name, shell.BACKEND_METHODS)
        self.assertTrue({'archiveStatus', 'archiveView', 'searchArchive'} <= shell.FAST_METHODS)
        from coremail_desktop.ui.messages import MESSAGES
        for code in ('ARCHIVE_NOT_FOUND', 'ARCHIVE_UNSUPPORTED', 'ARCHIVE_CHANGED', 'ARCHIVE_SCANNING'):
            self.assertIn(code, MESSAGES)
        web = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'
        self.assertIn('<script src="archive.js"></script>', (web / 'index.html').read_text(encoding='utf-8'))


class RankingTest(unittest.TestCase):
    def test_subject_and_attachment_hits_come_first(self):
        root = Path(scratch(self))
        account = root / 'me@inspur.com'
        (account / 'archmails').mkdir(parents=True)
        reports = [mail(10 + i, subject=f'Weekly report {i}', body='BI UAT status: done', date=f'Mon, {i + 1:02d} Jun 2026 09:00:00 +0300')
                   for i in range(25)]
        target = mail(1, subject='BI UAT Sign-Off Confirmation', body='signed document attached', date='Tue, 10 Feb 2026 14:17:45 +0300')
        (account / 'archmails' / '00.cmpack').write_bytes(pack([record(r) for r in reports[:13]]))
        (account / 'archmails' / '01.cmpack').write_bytes(pack([record(r) for r in reports[13:]] + [record(target)]))
        catalog = ArchiveCatalog(root / 'cache' / 'a.archive.db', XorProtector())
        self.addCleanup(catalog.close)
        catalog.scan(account)
        rows, total = catalog.search(['bi', 'uat'], limit=20)
        self.assertEqual((total, rows[0]['subject']), (26, 'BI UAT Sign-Off Confirmation'))  # Not 21st by date.
        self.assertEqual(rows[1]['subject'], 'Weekly report 24')  # The rest newest first.


if __name__ == '__main__':
    unittest.main()
