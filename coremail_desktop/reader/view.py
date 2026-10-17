"""WebEngine reader with a private memory profile and deny-by-default IO."""
from html import escape, unescape
import re
import uuid
from urllib.parse import quote
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QUrl, QEvent
from PySide6.QtGui import QDesktopServices, QPalette, QFont
from PySide6.QtWidgets import QApplication,QWidget, QVBoxLayout, QTextBrowser
from PySide6.QtWebEngineCore import (QWebEngineProfile, QWebEnginePage, QWebEngineSettings,
    QWebEngineUrlRequestInterceptor, QWebEngineUrlScheme, QWebEngineUrlSchemeHandler,
    QWebEngineUrlRequestJob)
from PySide6.QtWebEngineWidgets import QWebEngineView
from coremail_desktop.mime.sanitizer import sanitize_html, normalize_cid
from .resources import ReaderResourceStore

_scheme = QWebEngineUrlScheme(b'coremail-cid')
_scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
_scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme)
QWebEngineUrlScheme.registerScheme(_scheme)


class _Interceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, reader):
        super().__init__(reader)
        self.reader = reader

    def interceptRequest(self, info):
        info.block(not self.reader.request_allowed(info.requestUrl().toString()))


class _CidHandler(QWebEngineUrlSchemeHandler):
    def __init__(self, store, parent):
        super().__init__(parent)
        self.store = store

    def requestStarted(self, job):
        resource = self.store.resource_for_url(job.requestUrl().toString())
        if resource is None or job.requestMethod() != b'GET':
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        media_type, data = resource
        buffer = QBuffer(job)
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(media_type.encode('ascii'), buffer)


class _Page(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            if url.scheme().lower() in {'http', 'https'} and url.host():
                QDesktopServices.openUrl(url)
            return False
        return url.scheme() in {'data', 'about', 'coremail-cid'}

    def createWindow(self, window_type):
        return None

    def certificateError(self, error):
        return False


class ReaderView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_document = None
        self.resources = ReaderResourceStore()
        self.web_view = None
        self.profile = None
        self._page_token = ''
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.fallback = QTextBrowser(self)
        font=QFont(self.font()); font.setPointSize(12); self.fallback.setFont(font)
        self.fallback.setOpenLinks(False)
        self.fallback.setOpenExternalLinks(False)
        layout.addWidget(self.fallback)
        try:
            # A profile must outlive its pages. As a first child of the reader
            # it was destroyed before the later page, causing shutdown aborts.
            self.profile = QWebEngineProfile(QApplication.instance())
            self.destroyed.connect(self.profile.deleteLater)
            self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
            self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
            self._interceptor = _Interceptor(self)
            self.profile.setUrlRequestInterceptor(self._interceptor)
            self._cid_handler = _CidHandler(self.resources, self.profile)
            self.profile.installUrlSchemeHandler(b'coremail-cid', self._cid_handler)
            self.profile.downloadRequested.connect(lambda download: download.cancel())
            page = _Page(self.profile, self)
            self.web_view = QWebEngineView(self)
            self.web_view.setPage(page)
            self.web_view.loadFinished.connect(self._html_loaded)
            page.renderProcessTerminated.connect(lambda *_:self._show_fallback())
            settings = self.web_view.settings()
            for attribute in ('JavascriptEnabled', 'JavascriptCanOpenWindows', 'LocalStorageEnabled',
                              'LocalContentCanAccessRemoteUrls', 'LocalContentCanAccessFileUrls',
                              'PluginsEnabled', 'PdfViewerEnabled', 'FullScreenSupportEnabled',
                              'AllowRunningInsecureContent', 'HyperlinkAuditingEnabled', 'DnsPrefetchEnabled'):
                value = getattr(QWebEngineSettings.WebAttribute, attribute, None)
                if value is not None:
                    settings.setAttribute(value, False)
            layout.addWidget(self.web_view)
            self.fallback.hide()
        except (RuntimeError, ImportError):
            if self.web_view:
                self.web_view.hide()
            self.web_view = None
            self.fallback.show()
        self.clear()

    def request_allowed(self, url: str) -> bool:
        scheme = QUrl(url).scheme()
        if scheme == 'coremail-cid':
            return self.resources.resource_for_url(url) is not None
        return scheme in {'data', 'about'}

    def _page_html(self, safe):
        # Only this application-owned stylesheet is authorized by the CSP.
        nonce = uuid.uuid4().hex
        self._page_token = nonce
        palette = self.palette()
        bg = palette.color(QPalette.ColorRole.Base).name()
        ink = palette.color(QPalette.ColorRole.Text).name()
        line = palette.color(QPalette.ColorRole.Mid).name()
        link = '#93c5fd' if palette.color(QPalette.ColorRole.Base).lightness() < 128 else '#1d4ed8'
        csp = f"default-src 'none'; img-src coremail-cid:; style-src 'nonce-{nonce}'; script-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'"
        css = f'''body {{ background:{bg}; color:{ink}; font:16px/1.7 "Microsoft YaHei UI","Segoe UI",sans-serif; margin:18px; overflow-wrap:anywhere; }}
        pre {{ white-space:pre-wrap; font:inherit; }} img {{ max-width:100%; height:auto; }}
        a {{ color:{link}; }} blockquote {{ margin:16px 0; padding:0 16px; border-left:3px solid {line}; }}
        table {{ max-width:100%; border-collapse:collapse; }} td,th {{ border:1px solid {line}; padding:6px 10px; }}'''
        return '<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="' + csp + '"><style nonce="' + nonce + '">' + css + '</style></head><body data-reader-token="' + nonce + '">' + safe + '</body></html>'

    def _show_fallback(self):
        if self.web_view: self.web_view.hide()
        self.fallback.show()

    def _html_loaded(self, succeeded):
        if not succeeded or self.current_document is None: return
        token=self._page_token
        def ready(html):
            # A cancelled older load cannot expose a previous message's page.
            if self.current_document is not None and token==self._page_token and f'data-reader-token="{token}"' in html:
                self.web_view.show(); self.fallback.hide()
        self.web_view.page().toHtml(ready)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, 'web_view'):
            if self.current_document is not None: self.show_document(self.current_document)
            else: self.clear()

    def show_document(self, document):
        self.current_document = document
        scope = uuid.uuid4().hex
        try:
            self.resources.replace(scope, document.cid_resources)
        except ValueError:
            self.resources.clear()
        self.fallback.setPlainText(document.text)
        self._show_fallback()
        if self.web_view:
            safe = sanitize_html(document.html, self.resources._resources) if document.html else '<pre>' + escape(document.text) + '</pre>'
            def rewrite(match):
                cid = normalize_cid(unescape(match.group(1)))
                return 'src="coremail-cid://' + scope + '/' + quote(cid, safe='') + '"'
            safe = re.sub(r'src="cid:([^"]*)"', rewrite, safe, flags=re.I)
            page_html = self._page_html(safe)
            # Qt setHtml uses a data URL with a 2 MiB limit after percent encoding.
            if len(QUrl.toPercentEncoding(page_html)) >= 2 * 1024 * 1024 - 1024:
                self.web_view.setHtml('')
                self.web_view.hide()
                self.fallback.show()
            else:
                self.web_view.setHtml(page_html)

    set_document = show_document

    def clear(self):
        self.current_document = None
        self._page_token = ''
        self.resources.clear()
        self.fallback.setPlainText('选择一封邮件以阅读正文。远程图片已阻止。')
        if self.web_view:
            self._show_fallback()
            self.web_view.setHtml('')
