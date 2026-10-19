"""v0.4 reading layout: column-width prefs, local templates, print document, .eml export, pop-out windows."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QTWEBENGINE_DISABLE_SANDBOX', '1')

import shutil
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter, demo_certificate
from coremail_desktop.crypto.service import CryptoService
from coremail_desktop.errors import AppError
from tests.test_app_backend import XorProtector, local_services_stub

BUILD = Path(__file__).resolve().parents[1] / 'build'


def scratch(test):
    BUILD.mkdir(exist_ok=True)
    directory = tempfile.mkdtemp(prefix='hongyan-test-', dir=str(BUILD))
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


class LayoutPrefsAndTemplatesTest(unittest.TestCase):
    def setUp(self):
        self.directory = scratch(self)
        self.backend = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(),
                                   services_factory=local_services_stub)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(self.backend.logout)

    def test_column_widths_are_prefs_kept_within_bounds(self):
        prefs = self.backend.prefs()
        self.assertEqual((prefs['sidebarWidth'], prefs['listWidth']), (232, 400))
        prefs = self.backend.set_prefs({'sidebarWidth': 5, 'listWidth': 99999})
        self.assertEqual((prefs['sidebarWidth'], prefs['listWidth']), (180, 900))
        prefs = self.backend.set_prefs({'sidebarWidth': 300, 'listWidth': '500'})  # Wrong type is ignored.
        self.assertEqual((prefs['sidebarWidth'], prefs['listWidth']), (300, 900))
        self.assertEqual((prefs['ctTreeWidth'], prefs['ctListWidth'], prefs['tagsOpen']), (250, 340, False))  # Contacts columns; tags folded.
        prefs = self.backend.set_prefs({'ctTreeWidth': 10, 'ctListWidth': 5000, 'tagsOpen': True})
        self.assertEqual((prefs['ctTreeWidth'], prefs['ctListWidth'], prefs['tagsOpen']), (160, 720, True))

    def test_templates_round_trip_encrypted_rename_and_delete(self):
        value = {'subject': '本周工作周报', 'html': '<p>本周完成：机密项目</p>', 'to': ['rd@example.invalid'], 'cc': ['x@example.invalid']}
        plain = self.backend.save_template('周报', value)
        self.assertEqual((plain['to'], plain['cc']), ([], []))  # Recipients only when asked.
        kept = self.backend.save_template('会议', value, recipients=True)
        self.assertEqual(kept['to'], ['rd@example.invalid'])
        self.assertEqual([t['name'] for t in self.backend.templates()], ['会议', '周报'])
        # Same name replaces instead of duplicating.
        again = self.backend.save_template('周报', {**value, 'subject': '新主题'})
        self.assertEqual(again['id'], plain['id'])
        self.assertEqual(len(self.backend.templates()), 2)
        # Stored only as ciphertext in the cache database.
        database = next(Path(self.directory, 'cache').glob('*.db')).read_bytes()
        self.assertNotIn('机密项目'.encode('utf-8'), database)
        with self.assertRaises(AppError) as raised:
            self.backend.rename_template(kept['id'], '周报')
        self.assertEqual(raised.exception.code, 'TEMPLATE_EXISTS')
        renamed = self.backend.rename_template(kept['id'], '例会')
        self.assertIn('例会', [t['name'] for t in renamed])
        self.assertEqual([t['name'] for t in self.backend.delete_template(plain['id'])], ['例会'])
        for bad in ('', '  ', 'x' * 61, 'a\nb'):
            with self.assertRaises(AppError):
                self.backend.save_template(bad, value)

    def test_template_count_is_limited(self):
        for i in range(100):
            self.backend.save_template(f'模板{i}', {'subject': '', 'html': ''})
        with self.assertRaises(AppError) as raised:
            self.backend.save_template('再多一个', {'subject': '', 'html': ''})
        self.assertEqual(raised.exception.code, 'TEMPLATE_LIMIT')
        self.backend.save_template('模板7', {'subject': '替换不受限', 'html': ''})

    def test_print_document_has_header_attachments_and_no_remote_by_default(self):
        row = next(r for r in self.backend.messages('1', 0, 50)['rows'] if r['flags'].get('attached'))
        view = self.backend.open_message(row['id'], mark_read=False)
        page = self.backend.print_html(row['id'])
        self.assertIn(f'<h1>{view["subject"]}</h1>', page)
        for a in view['attachments']:
            self.assertIn(a['name'], page)
        self.assertIn('发件人', page)
        self.assertIn("img-src data:;", page)
        self.assertNotIn('<script', page.lower())
        self.assertIn('img-src data: https: http:', self.backend.print_html(row['id'], remote_images=True))
        self.assertFalse(self.backend.cache.summary(row['id'])['flags']['read'] and not row['flags']['read'])

    def test_eml_export_is_the_original_message(self):
        row = self.backend.messages('1', 0, 5)['rows'][0]
        name, data = self.backend.export_eml(row['id'])
        self.assertTrue(name.endswith('.eml'))
        self.assertNotRegex(name[:-4], r'[\\/:*?"<>|]')
        self.assertEqual(data, self.backend.cache.get_body(row['id']))
        self.assertEqual(self.backend.export_eml(row['id'], decrypted=True)[1], data)  # Plain mail: nothing to decrypt.


class EncryptedExportTest(unittest.TestCase):
    def setUp(self):
        self.directory = scratch(self)
        self.backend = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(),
                                   services_factory=local_services_stub, crypto=CryptoService())
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw')
        self.addCleanup(self.backend.logout)
        draft = self.backend.compose('new')
        draft.update(subject='季度预算', html='<p>内部资料 <b>请勿外传</b></p>', to=['zhangxm@example.invalid'], encrypt=True)
        self.assertEqual(self.backend.send(draft)['state'], 'Accepted')
        self.envelope = self.backend.adapter.sent_payloads[-1]
        self.mid = self.backend.messages('1', 0, 5)['rows'][0]['id']
        self.backend.cache.put_body(self.mid, self.envelope)  # An encrypted message in the inbox.

    def test_default_export_stays_encrypted_and_decrypted_copy_needs_the_certificate(self):
        self.assertEqual(self.backend.export_eml(self.mid)[1], self.envelope)
        with self.assertRaises(AppError) as raised:
            self.backend.export_eml(self.mid, decrypted=True)
        self.assertEqual(raised.exception.code, 'CERT_LOCKED')
        self.assertIn('解锁', self.backend.print_html(self.mid))
        _der, key, cert = demo_certificate('demo@example.invalid')
        pfx = Path(self.directory) / 'me.pfx'
        pfx.write_bytes(pkcs12.serialize_key_and_certificates(b'me', key, cert, None, serialization.BestAvailableEncryption(b'secret')))
        self.backend.unlock_certificate(str(pfx), 'secret')
        _name, plain = self.backend.export_eml(self.mid, decrypted=True)
        from email import policy
        from email.parser import BytesParser
        message = BytesParser(policy=policy.default).parsebytes(plain)
        self.assertEqual(message['subject'], '季度预算')
        self.assertNotEqual(message.get_content_type(), 'application/pkcs7-mime')
        self.assertIn('请勿外传', message.get_body(preferencelist=('html',)).get_content())
        self.assertNotIn('请勿外传'.encode('utf-8'), self.backend.export_eml(self.mid)[1])


class PopOutAndPrintTest(unittest.TestCase):
    """Real Qt WebEngine: pop-out windows share the mail thread, refuse unknown events, print to PDF."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        try:
            import qt_test_support  # noqa: F401  Releases windows before interpreter teardown.
        except ImportError:
            from tests import qt_test_support  # noqa: F401
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, predicate, ms=20000):
        from PySide6.QtCore import QEventLoop, QTimer
        loop = QEventLoop()
        timer = QTimer()
        timer.timeout.connect(lambda: loop.quit() if predicate() else None)
        timer.start(50)
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        timer.stop()
        return predicate()

    def test_pop_out_windows_and_pdf_rendering(self):
        from coremail_desktop.app import shell
        backend = MailBackend(scratch(self), adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw')
        window = shell.AppShell(backend, demo=True)
        self.addCleanup(lambda: (window.native_closeReaders(), window.bridge.shutdown(), window.deleteLater()))
        mid = backend.messages('1', 0, 5)['rows'][0]['id']
        self.assertTrue(window.native_openReader(mid))
        self.assertTrue(window.native_openReader(mid))  # The same message reuses its window.
        window.native_openReader(backend.messages('1', 0, 5)['rows'][1]['id'])
        self.assertEqual(len(window.readers), 2)
        reader = window.readers[0]
        self.assertIs(reader.bridge._serial, window.bridge._serial)  # Backend calls stay in order.
        self.assertIsNot(reader.interceptor, window.interceptor)     # Remote images are allowed per window.
        self.assertTrue(self.wait(lambda: reader.page.url().toString().endswith('#reader=' + mid)), reader.page.url().toString())
        with self.assertRaises(AppError):
            reader.native_notifyMain('openMessage', mid)
        self.assertTrue(self.wait(lambda: reader.windowTitle().endswith('· 鸿雁')), reader.windowTitle())
        with self.assertRaises(AppError):
            window.native_openReader('')

        result = {}

        def ready(view, _ok):
            view.page().printToPdf(lambda data: result.update(pdf=bytes(data)))
        window._render_print(mid, False, ready)
        self.assertTrue(self.wait(lambda: 'pdf' in result), 'no PDF produced')
        self.assertTrue(result['pdf'].startswith(b'%PDF'))
        self.assertEqual(shell._PRINT_DOCS, {})  # One-shot document already served and dropped.
        # 打印: the same document onto a QPrinter (a PDF printer stands in for the dialog's choice).
        from PySide6.QtPrintSupport import QPrinter
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        target = Path(scratch(self)) / 'printed.pdf'
        printer.setOutputFileName(str(target))

        def ready_print(view, _ok):
            view.printFinished.connect(lambda done: result.update(printed=done))
            view.print(printer)
        window._render_print(mid, False, ready_print)
        self.assertTrue(self.wait(lambda: 'printed' in result, 60000), 'printing did not finish')
        self.assertTrue(result['printed'])
        self.assertTrue(target.read_bytes().startswith(b'%PDF'))
        window.native_closeReaders()
        self.assertTrue(self.wait(lambda: not window.readers))


if __name__ == '__main__':
    unittest.main()
