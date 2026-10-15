"""WebEngine reader with a private memory profile and deny-by-default IO."""
from html import escape, unescape
import re
import uuid
from urllib.parse import quote
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget, QVBoxLayout, QTextBrowser
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.fallback = QTextBrowser(self)
        self.fallback.setOpenLinks(False)
        self.fallback.setOpenExternalLinks(False)
        layout.addWidget(self.fallback)
        try:
            self.profile = QWebEngineProfile(self)
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

    def show_document(self, document):
        self.current_document = document
        scope = uuid.uuid4().hex
        try:
            self.resources.replace(scope, document.cid_resources)
        except ValueError:
            self.resources.clear()
        self.fallback.setPlainText(document.text)
        if self.web_view:
            self.web_view.show()
            self.fallback.hide()
            safe = sanitize_html(document.html, self.resources._resources) if document.html else '<pre>' + escape(document.text) + '</pre>'
            def rewrite(match):
                cid = normalize_cid(unescape(match.group(1)))
                return 'src="coremail-cid://' + scope + '/' + quote(cid, safe='') + '"'
            safe = re.sub(r'src="cid:([^"]*)"', rewrite, safe, flags=re.I)
            csp = "default-src 'none'; img-src coremail-cid:; style-src 'none'; script-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'"
            page_html = '<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="' + csp + '"></head><body>' + safe + '</body></html>'
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
        self.resources.clear()
        self.fallback.setPlainText('选择一封邮件以阅读正文。远程图片已阻止。')
        if self.web_view:
            self.web_view.show()
            self.fallback.hide()
            self.web_view.setHtml('<p>选择一封邮件以阅读正文。远程图片已阻止。</p>')
