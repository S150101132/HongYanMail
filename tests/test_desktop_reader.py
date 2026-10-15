import os

# A real Qt WebEngine renderer is required here. The platform must be offscreen
# for headless runs, and Chromium's renderer subprocess must be able to start;
# the harness never disables the product's own Chromium sandbox.
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QTWEBENGINE_DISABLE_SANDBOX', '1')

import unittest
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEngineProfile
from PySide6.QtCore import QEventLoop, QTimer, QCoreApplication, QEvent
from PySide6.QtTest import QSignalSpy
from coremail_desktop.models import MessageDocument
from coremail_desktop.reader.resources import ReaderResourceStore
from coremail_desktop.reader.view import ReaderView


class ResourceTests(unittest.TestCase):
    def test_cid_is_scoped_to_current_document(self):
        store = ReaderResourceStore(max_bytes=4)
        store.replace('one', {'x': ('image/png', b'1234')})
        self.assertEqual(store.resolve('one', 'x'), b'1234')
        self.assertIsNone(store.resolve('two', 'x'))
        store.replace('two', {'x': ('image/png', b'5678')})
        self.assertIsNone(store.resolve('one', 'x'))
        with self.assertRaises(ValueError):
            store.replace('three', {'x': ('image/png', b'12345')})


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_renderer_really_loads_safe_document(self):
        reader = ReaderView()
        settle = QSignalSpy(reader.web_view.loadFinished)
        if settle.count() == 0:
            settle.wait(10000)
        spy = QSignalSpy(reader.web_view.loadFinished)
        reader.show_document(MessageDocument('x', 'digest', '中文正文', '<p>中文正文</p><img src="https://127.0.0.1/pixel">', (), {}))
        self.assertTrue(spy.wait(15000), 'Renderer did not complete a page load')
        # loadFinished reports false when the only blocked subresource is the
        # remote image, so readable DOM text is the real load evidence here.
        values = []
        loop = QEventLoop()
        reader.web_view.page().toPlainText(lambda text: (values.append(text), loop.quit()))
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        self.assertTrue(values, 'Renderer produced no readable text')
        self.assertIn('中文正文', values[0])
        reader.close()
        reader.deleteLater()

    def test_real_interceptor_blocks_network_even_if_sanitizer_is_bypassed(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        reader = ReaderView()
        try:
            spy = QSignalSpy(reader.web_view.loadFinished)
            reader.web_view.setHtml(f'<p>safe</p><img src="http://127.0.0.1:{server.server_port}/leak"><iframe src="file:///C:/Windows/win.ini"></iframe>')
            self.assertTrue(spy.wait(15000))
            self.assertEqual(requests, [])
        finally:
            reader.close()
            reader.deleteLater()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_large_html_uses_readable_plaintext_fallback(self):
        reader = ReaderView()
        reader.show_document(MessageDocument('x', 'digest', 'large body fallback', '<p>' + '中' * 250000 + '</p>', (), {}))
        self.assertFalse(reader.fallback.isHidden())
        self.assertEqual(reader.fallback.toPlainText(), 'large body fallback')
        reader.show_document(MessageDocument('small', 'digest', 'small', '<p>small</p>', (), {}))
        self.assertTrue(reader.fallback.isHidden())
        reader.close()
        reader.deleteLater()

    def test_webengine_profile_has_no_business_authority(self):
        reader = ReaderView()
        self.assertIsNotNone(reader.web_view, 'WebEngine must really construct in isolation test')
        profile = reader.web_view.page().profile()
        self.assertTrue(profile.isOffTheRecord())
        self.assertEqual(profile.httpCacheType(), QWebEngineProfile.MemoryHttpCache)
        self.assertEqual(profile.persistentCookiesPolicy(), QWebEngineProfile.NoPersistentCookies)
        self.assertFalse(reader.web_view.settings().testAttribute(QWebEngineSettings.JavascriptEnabled))
        self.assertFalse(reader.web_view.settings().testAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls))
        self.assertIsNone(reader.web_view.page().webChannel())
        reader.close()
        reader.deleteLater()

    def test_file_url_and_remote_fetch_are_blocked(self):
        reader = ReaderView()
        for url in ('file:///C:/secret', 'https://example.com/pixel', 'http://localhost/', 'ftp://example.com/a'):
            self.assertFalse(reader.request_allowed(url))
        reader.show_document(MessageDocument('x', 'digest', 'safe', '<p>safe</p>', (), {}))
        reader.clear()
        self.assertIsNone(reader.current_document)
        reader.close()
        reader.deleteLater()

