"""Qt host for the web interface: window, bridge, tray, notifications, file dialogs.

The interface is served from the private hongyan://app/ scheme. Mail bodies are
rendered by the page inside a script-less sandboxed frame; all network access
from the web view is blocked except remote images the user allowed per message.
"""
import base64
import json
import mimetypes
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import QBuffer, QByteArray, QFile, QIODevice, QMarginsF, QObject, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QPageLayout, QPageSize, QShortcut
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile, QWebEngineSettings,
                                     QWebEngineUrlRequestInfo, QWebEngineUrlRequestInterceptor,
                                     QWebEngineUrlRequestJob, QWebEngineUrlScheme, QWebEngineUrlSchemeHandler)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMainWindow, QMenu, QSystemTrayIcon

from coremail_desktop.errors import AppError
from coremail_desktop.ui.branding import app_icon
from coremail_desktop.ui.messages import login_message, user_message

WEB_ROOT = Path(__file__).resolve().parent / 'web'
SCHEME = b'hongyan'
APP_URL = 'hongyan://app/index.html'

_scheme = QWebEngineUrlScheme(SCHEME)
_scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
_scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme | QWebEngineUrlScheme.Flag.LocalAccessAllowed)
QWebEngineUrlScheme.registerScheme(_scheme)

# Methods the page may call. Everything else is refused.
BACKEND_METHODS = {
    'savedLogin': 'saved_login', 'login': 'login', 'autoLogin': 'auto_login', 'logout': 'logout',
    'profile': 'profile', 'prefs': 'prefs', 'setPrefs': 'set_prefs', 'folders': 'folders',
    'cachedView': 'cached_view', 'messages': 'messages', 'openMessage': 'open_message',
    'setFlags': 'set_flags', 'move': 'move', 'delete': 'delete', 'searchLocal': 'search_local',
    'searchServer': 'search_server', 'suggest': 'suggest', 'compose': 'compose', 'saveDraft': 'save_draft',
    'listDrafts': 'list_drafts', 'restoreDraft': 'restore_draft', 'deleteDraft': 'delete_draft',
    'send': 'send', 'poll': 'poll', 'prefetch': 'prefetch', 'encryptionCheck': 'encryption_check',
    'certificateStatus': 'certificate_status', 'forgetCertificate': 'forget_certificate',
    'deliveryStatus': 'delivery_status', 'recall': 'recall', 'scheduled': 'scheduled', 'unschedule': 'unschedule',
    'sharedMailboxes': 'shared_mailboxes',
    'directory': 'directory', 'members': 'members', 'personalContacts': 'personal_contacts', 'findPeople': 'find_people',
    'searchCapabilities': 'search_capabilities',
    'templates': 'templates', 'saveTemplate': 'save_template', 'renameTemplate': 'rename_template', 'deleteTemplate': 'delete_template',
}
# Read-only calls may run beside a long send or list fetch.
FAST_METHODS = {'savedLogin', 'profile', 'prefs', 'cachedView', 'searchLocal', 'suggest', 'encryptionCheck', 'certificateStatus',
                'searchCapabilities', 'directory', 'members', 'personalContacts', 'findPeople', 'templates'}
# Events a pop-out reader may pass on to the main window.
POPOUT_EVENTS = {'mailChanged', 'authRequired'}
# One-shot documents for printing, served at hongyan://app/print/<token>.html.
_PRINT_DOCS = {}
BACKGROUND_METHODS = {'prefetch'}


class _SchemeHandler(QWebEngineUrlSchemeHandler):
    def requestStarted(self, job):
        url = job.requestUrl()
        name = url.path().lstrip('/') or 'index.html'
        if url.host() != 'app' or job.requestMethod() != b'GET':
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        if name.startswith('print/'):
            data = _PRINT_DOCS.pop(name[6:].removesuffix('.html'), None)
            if data is None:
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
        elif name == 'qwebchannel.js':
            source = QFile(':/qtwebchannel/qwebchannel.js')
            source.open(QIODevice.OpenModeFlag.ReadOnly)
            data = bytes(source.readAll())
        else:
            path = (WEB_ROOT / name).resolve()
            if WEB_ROOT not in path.parents or not path.is_file():
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
            data = path.read_bytes()
        kind = {'.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml'}.get(
            Path(name).suffix, 'application/octet-stream')
        buffer = QBuffer(job)
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(kind.encode('ascii'), buffer)


class _Interceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, parent):
        super().__init__(parent)
        self.allow_remote_images = False

    def interceptRequest(self, info):
        url = info.requestUrl()
        scheme = url.scheme().lower()
        if scheme in ('hongyan', 'data', 'about', 'blob', 'qrc'):
            return
        image = info.resourceType() == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeImage
        if scheme in ('http', 'https') and image and self.allow_remote_images:
            return
        info.block(True)


class _Page(QWebEnginePage):
    link_requested = Signal(QUrl)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        scheme = url.scheme().lower()
        if navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            if scheme in ('http', 'https', 'mailto'):
                self.link_requested.emit(url)
            return False
        if is_main_frame:
            return url.toString().startswith('hongyan://app/')
        return scheme in ('about', 'data')

    def createWindow(self, window_type):
        return None

    def javaScriptConsoleMessage(self, level, message, line, source):
        if os.environ.get('HONGYAN_DEBUG'):
            print(f'[js] {source}:{line} {message}')


def _lock_down(page, javascript=True):
    settings = page.settings()
    for attribute, value in ((QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False),
                             (QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False),
                             (QWebEngineSettings.WebAttribute.PluginsEnabled, False),
                             (QWebEngineSettings.WebAttribute.PdfViewerEnabled, False),
                             (QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False),
                             (QWebEngineSettings.WebAttribute.JavascriptEnabled, javascript),
                             (QWebEngineSettings.WebAttribute.AutoLoadImages, True)):
        settings.setAttribute(attribute, value)


def _make_web(owner):
    """An off-the-record profile serving the private scheme, with its own request
    interceptor (so remote images are allowed per window), and a locked-down page."""
    profile = QWebEngineProfile(owner)  # Off-the-record: nothing persisted by Chromium.
    profile.installUrlSchemeHandler(SCHEME, _SchemeHandler(profile))
    interceptor = _Interceptor(profile)
    profile.setUrlRequestInterceptor(interceptor)
    page = _Page(profile, owner)
    _lock_down(page)
    return profile, interceptor, page


class Bridge(QObject):
    reply = Signal(str, bool, str)
    event = Signal(str, str)
    _done = Signal(str, bool, str)

    def __init__(self, shell, host=None, share=None):
        """host: the window whose page this bridge serves (natives are looked up on it
        first). share: another bridge whose worker threads to use, so the backend is
        still called from one mail thread in order."""
        super().__init__(host or shell)
        self.shell = shell
        self.host = host or shell
        self.backend = shell.backend
        self._owns_pools = share is None
        if share is None:
            self._serial = ThreadPoolExecutor(max_workers=1, thread_name_prefix='hongyan-mail')
            self._fast = ThreadPoolExecutor(max_workers=1, thread_name_prefix='hongyan-fast')
            self._background = ThreadPoolExecutor(max_workers=1, thread_name_prefix='hongyan-prefetch')
        else:
            self._serial, self._fast, self._background = share._serial, share._fast, share._background
        self._done.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def emit_event(self, name, value=None):
        self.event.emit(name, json.dumps(value, ensure_ascii=False))

    @Slot(str, bool, str)
    def _deliver(self, request_id, ok, payload):
        if request_id.startswith('internal:'):
            self.shell.internal_result(request_id, ok, payload)
        else:
            self.reply.emit(request_id, ok, payload)

    def run(self, request_id, name, params):
        def work():
            try:
                result = getattr(self.backend, BACKEND_METHODS[name])(**params)
                self._done.emit(request_id, True, json.dumps(result, ensure_ascii=False, default=str))
            except Exception as error:  # Only fixed codes and fixed text reach the page.
                code = getattr(error, 'code', 'INTERNAL_ERROR') if isinstance(error, AppError) or hasattr(error, 'code') else 'INTERNAL_ERROR'
                if code == 'INTERNAL_ERROR':
                    self.shell.log_internal_error(name, error)
                text = login_message(code) if name in ('login', 'autoLogin') else user_message(code)
                if code in ('SESSION_EXPIRED', 'NOT_AUTHENTICATED') and name not in ('login', 'autoLogin'):
                    self.emit_event('authRequired')
                self._done.emit(request_id, False, json.dumps({'code': code, 'message': text}, ensure_ascii=False))
        pool = self._background if name in BACKGROUND_METHODS else self._fast if name in FAST_METHODS else self._serial
        pool.submit(work)

    @Slot(str, str, str)
    def call(self, request_id, method, args_json):
        try:
            params = json.loads(args_json or '{}')
            if not isinstance(params, dict):
                raise ValueError
        except ValueError:
            self.reply.emit(request_id, False, json.dumps({'code': 'INVALID_REQUEST', 'message': '请求无效。'}))
            return
        native = getattr(self.host, 'native_' + method, None) or getattr(self.shell, 'native_' + method, None)
        if native is not None:
            try:
                result = native(**params)
                self.reply.emit(request_id, True, json.dumps(result, ensure_ascii=False))
            except Exception as error:
                code = error.code if isinstance(error, AppError) else 'WRITE_DENIED' if isinstance(error, PermissionError) else 'INTERNAL_ERROR'
                if code == 'INTERNAL_ERROR':
                    self.shell.log_internal_error(method, error)
                self.reply.emit(request_id, False, json.dumps({'code': code, 'message': user_message(code)}, ensure_ascii=False))
            return
        if method not in BACKEND_METHODS:
            self.reply.emit(request_id, False, json.dumps({'code': 'OPERATION_NOT_ALLOWED', 'message': user_message('OPERATION_NOT_ALLOWED')}))
            return
        self.run(request_id, method, params)

    def shutdown(self):
        if not self._owns_pools:
            return
        self._serial.shutdown(wait=False, cancel_futures=True)
        self._fast.shutdown(wait=False, cancel_futures=True)
        self._background.shutdown(wait=False, cancel_futures=True)


class _PrintHost:
    """Print and export for a window with a profile, a bridge and the backend.
    The document is built by the backend and laid out by a hidden, script-less view."""

    def _render_print(self, mid, remote, ready):
        token = os.urandom(12).hex()
        _PRINT_DOCS[token] = self.backend.print_html(mid, bool(remote)).encode('utf-8')
        view = QWebEngineView()  # Never shown.
        page = _Page(self.profile, view)
        _lock_down(page, javascript=False)
        view.setPage(page)
        self._printing = getattr(self, '_printing', set())
        self._printing.add(view)

        def loaded(ok):
            page.loadFinished.disconnect(loaded)
            _PRINT_DOCS.pop(token, None)
            ready(view, ok) if ok else self._print_done(view, False)
        page.loadFinished.connect(loaded)
        page.load(QUrl(f'hongyan://app/print/{token}.html'))

    def _print_done(self, view, ok, code='PRINT_FAILED', kind='print'):
        self._printing.discard(view)
        view.deleteLater()
        self.bridge.emit_event('printed', {'kind': kind, 'ok': bool(ok), 'message': '' if ok else user_message(code)})

    def native_printMessage(self, mid='', remote=False):
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(self.backend.file_stem(mid))
        if QPrintDialog(printer, self).exec() != QDialog.DialogCode.Accepted:
            return False

        def ready(view, _ok):
            view.printFinished.connect(lambda done: self._print_done(view, done))
            view.print(printer)
        self._print_printer = printer  # Kept alive until printing finishes.
        self._render_print(mid, remote, ready)
        return True

    def native_exportPdf(self, mid='', remote=False):
        name = self.backend.file_stem(mid) + '.pdf'
        target, _ = QFileDialog.getSaveFileName(self, '导出为 PDF', str(Path.home() / 'Downloads' / name), 'PDF 文件 (*.pdf)')
        if not target:
            return False

        def ready(view, _ok):
            def written(data):
                try:
                    if not data:
                        raise ValueError
                    Path(target).write_bytes(bytes(data))
                except OSError:
                    self._print_done(view, False, 'WRITE_DENIED', 'pdf')
                except ValueError:
                    self._print_done(view, False, 'PRINT_FAILED', 'pdf')
                else:
                    self._print_done(view, True, kind='pdf')
            layout = QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait,
                                 QMarginsF(15, 15, 15, 15), QPageLayout.Unit.Millimeter)
            view.page().printToPdf(written, layout)
        self._render_print(mid, remote, ready)
        return True

    def native_exportEml(self, mid='', decrypted=False):
        name, data = self.backend.export_eml(mid, decrypted is True)
        target, _ = QFileDialog.getSaveFileName(self, '导出邮件', str(Path.home() / 'Downloads' / name), '邮件文件 (*.eml)')
        if not target:
            return False
        Path(target).write_bytes(data)
        return True


class ReaderWindow(_PrintHost, QMainWindow):
    """One message in its own window (双击弹出): the same page in reader-only layout,
    its own locked-down profile and interceptor, and a bridge that shares the main
    window's mail thread. Pop-outs stay usable while the main window is in the tray."""

    def __init__(self, shell, mid):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.shell, self.backend, self.mid = shell, shell.backend, mid
        self.setWindowTitle('鸿雁 · 邮件')
        self.setWindowIcon(app_icon())
        self.resize(980, 820)
        self.setMinimumSize(560, 420)
        self.profile, self.interceptor, self.page = _make_web(self)
        self.page.link_requested.connect(shell._open_link)
        self.page.titleChanged.connect(lambda title: self.setWindowTitle(title or '鸿雁 · 邮件'))
        self.view = QWebEngineView(self)
        self.view.setPage(self.page)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setCentralWidget(self.view)
        self.bridge = Bridge(shell, host=self, share=shell.bridge)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject('bridge', self.bridge)
        self.page.setWebChannel(self.channel)
        self.page.load(QUrl(APP_URL + '#reader=' + quote(mid, safe='')))
        QShortcut(QKeySequence('Ctrl+W'), self, activated=self.close)

    def native_allowRemoteImages(self, allow=False):
        self.interceptor.allow_remote_images = bool(allow)
        return True

    def native_findText(self, text='', backward=False):
        flags = QWebEnginePage.FindFlag.FindBackward if backward else QWebEnginePage.FindFlag(0)
        self.page.findText(text, flags)
        return True

    def native_notifyMain(self, name='', value=None):
        if name not in POPOUT_EVENTS:
            raise AppError('OPERATION_NOT_ALLOWED')
        self.shell.bridge.emit_event(name, value)
        return True

    def native_closeWindow(self):
        QTimer.singleShot(0, self.close)
        return True

    def native_setUnread(self, count=0):
        return True  # The main window owns the tray count.

    def closeEvent(self, event):
        self.page.setWebChannel(None)
        event.accept()


class AppShell(_PrintHost, QMainWindow):
    push_changed = Signal(str)
    push_state = Signal(str, str)
    PUSH_FALLBACK_SECONDS = 300  # Polling interval while the push channel is up.

    def __init__(self, backend, demo=False, sandboxed=True, relaunch_without_sandbox=None):
        super().__init__()
        self.backend = backend
        self.demo = demo
        self.sandboxed = sandboxed
        self._relaunch_without_sandbox = relaunch_without_sandbox
        self._page_loaded = False
        self._renderer_restarts = 0
        self._quitting = False
        self._unread = 0
        self._poll_busy = False
        self._last_new = None
        self.setWindowTitle('鸿雁 · 工作邮箱')
        self.setWindowIcon(app_icon())
        self.resize(1360, 860)
        self.setMinimumSize(960, 600)

        self.readers = []  # Pop-out reader windows.
        self.profile, self.interceptor, self.page = _make_web(self)
        self.page.link_requested.connect(self._open_link)
        self.page.loadFinished.connect(self._load_finished)
        self.page.renderProcessTerminated.connect(self._renderer_ended)
        self.view = QWebEngineView(self)
        self.view.setPage(self.page)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setCentralWidget(self.view)

        self.bridge = Bridge(self)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject('bridge', self.bridge)
        self.page.setWebChannel(self.channel)
        self.page.load(QUrl(APP_URL))

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_now)
        self._poll_seconds = 60
        self.push = None
        self._push_pending = False
        self._push_debounce = QTimer(self)
        self._push_debounce.setSingleShot(True)
        self._push_debounce.setInterval(1500)  # One refresh for a burst of events.
        self._push_debounce.timeout.connect(self.poll_now)
        self.push_changed.connect(self._push_event, Qt.ConnectionType.QueuedConnection)
        self.push_state.connect(self._push_state_changed, Qt.ConnectionType.QueuedConnection)
        self._make_tray()
        QShortcut(QKeySequence('Ctrl+Shift+I'), self, activated=self._devtools)

    # Renderer health ---------------------------------------------------------
    def _load_finished(self, ok):
        self._page_loaded = self._page_loaded or ok

    def _renderer_ended(self, status, code):
        if status == QWebEnginePage.RenderProcessTerminationStatus.NormalTerminationStatus:
            return
        if not self._page_loaded and self.sandboxed and self._relaunch_without_sandbox:
            # The sandboxed renderer never came up on this machine: restart once without it.
            relaunch, self._relaunch_without_sandbox = self._relaunch_without_sandbox, None
            relaunch()
            self.quit_app()
        elif self._renderer_restarts < 3:
            self._renderer_restarts += 1
            QTimer.singleShot(500, lambda: self.page.load(QUrl(APP_URL)))

    def log_internal_error(self, method, error):
        """Local diagnostics: exception type and code locations only. Messages are
        never written, because they can carry URLs, session ids or file paths."""
        frames = traceback.extract_tb(error.__traceback__)
        lines = [f'{time.strftime("%Y-%m-%d %H:%M:%S")} {method}: {type(error).__module__}.{type(error).__name__}']
        lines += [f'    {Path(f.filename).name}:{f.lineno} in {f.name}' for f in frames[-12:]]
        if os.environ.get('HONGYAN_DEBUG'):
            print('\n'.join(lines))
        try:
            with open(self.backend.data_dir / 'hongyan-errors.log', 'a', encoding='utf-8') as stream:
                stream.write('\n'.join(lines) + '\n')
        except OSError:
            pass

    def native_runtimeStatus(self):
        return {'sandboxed': self.sandboxed, 'dataDir': str(self.backend.data_dir)}

    # Tray and notifications ------------------------------------------------
    def _make_tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        for text, handler in (('打开鸿雁', self.bring_to_front), ('写邮件', lambda: self._to_page('compose')),
                              ('立即收信', self.poll_now), (None, None), ('退出', self.quit_app)):
            if text is None:
                menu.addSeparator()
                continue
            action = QAction(text, menu)
            action.triggered.connect(handler)
            menu.addAction(action)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.setToolTip('鸿雁 · 工作邮箱')
        self.tray.activated.connect(lambda reason: self.bring_to_front()
                                    if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                                                  QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.messageClicked.connect(self._notification_clicked)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _to_page(self, name, value=None):
        self.bring_to_front()
        self.bridge.emit_event(name, value)

    def bring_to_front(self):
        self.showNormal() if self.isMinimized() else self.show()
        self.raise_()
        self.activateWindow()

    def _notification_clicked(self):
        self._to_page('openMessage', self._last_new)

    def _set_unread(self, count):
        self._unread = count
        title = '鸿雁 · 工作邮箱'
        self.setWindowTitle(f'({count}) {title}' if count else title)
        self.tray.setToolTip(f'{title} · {count} 封未读' if count else title)

    def closeEvent(self, event):
        if self._quitting or not self.tray.isVisible() or self.demo:
            self.native_closeReaders()
            self._stop_push()
            self.bridge.shutdown()
            event.accept()
            QApplication.instance().quit()
            return
        event.ignore()
        self.hide()
        if not getattr(self, '_told_tray', False):
            self._told_tray = True
            self.tray.showMessage('鸿雁仍在后台运行', '新邮件会在这里提醒。右键托盘图标可退出。', app_icon(), 4000)

    def quit_app(self):
        self._quitting = True
        self.close()

    def _devtools(self):
        if os.environ.get('HONGYAN_DEBUG'):
            self._dev = QWebEngineView()
            self.page.setDevToolsPage(self._dev.page())
            self._dev.show()

    # Polling -----------------------------------------------------------------
    def start_polling(self, seconds):
        self._poll_seconds = max(30, int(seconds))
        pushed = self.push is not None and self.push.state == 'connected'
        self.poll_timer.start(max(self._poll_seconds, self.PUSH_FALLBACK_SECONDS if pushed else 0) * 1000)

    def start_push(self):
        """Real-time refresh over the mail server's socket.io channel; polling stays as the fallback."""
        if self.demo:
            return
        from coremail_desktop.protocol.push import PushClient
        self._stop_push()
        adapter = self.backend.adapter
        self.push = PushClient(lambda: getattr(adapter, 'push_credentials', lambda: None)(), self.push_changed.emit,
                               lambda state, code=None: self.push_state.emit(state, code or ''))
        self.push.start()

    def _stop_push(self):
        if self.push is not None:
            self.push.stop()
            self.push = None

    def _push_log(self, line):
        # States, failure codes and event arrival times only.
        try:
            with open(self.backend.data_dir / 'hongyan-push.log', 'a', encoding='utf-8') as stream:
                stream.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {line}\n')
        except OSError:
            pass

    def _push_event(self, name):
        self._push_log('event -> refresh')
        self._push_pending = self._poll_busy
        self._push_debounce.start()

    def _push_state_changed(self, state, code):
        self._push_log(f'{state} {code}'.rstrip())
        if state in ('connected', 'waiting'):
            self.start_polling(self._poll_seconds)
        if state == 'connected':
            self._push_debounce.start()  # Catch up on anything missed while disconnected.

    def poll_now(self):
        if self._poll_busy or self.backend.cache is None:
            return
        self._poll_busy = True
        self.bridge.run('internal:poll', 'poll', {})

    def internal_result(self, request_id, ok, payload):
        if request_id == 'internal:poll':
            self._poll_busy = False
            if self._push_pending:  # A push arrived while this poll was running.
                self._push_pending = False
                self._push_debounce.start()
            if not ok:
                self.bridge.emit_event('syncError', json.loads(payload))
                return
            value = json.loads(payload)
            inbox = next((f for f in value['folders'] if f['id'] == value['inbox']), None)
            self._set_unread((inbox or {}).get('unread') or 0)
            self.bridge.emit_event('synced', value)
            fresh = value['new']
            if fresh and self.backend.prefs().get('notify', True):
                self._last_new = fresh[0]['id']
                if len(fresh) == 1:
                    self.tray.showMessage(f"新邮件 · {fresh[0]['fromName']}", fresh[0]['subject'] or '（无主题）', app_icon(), 8000)
                else:
                    self.tray.showMessage(f'{len(fresh)} 封新邮件', '\n'.join(r['subject'] for r in fresh[:3]), app_icon(), 8000)
                QApplication.alert(self, 0)

    # Native calls from the page (run on the UI thread) ---------------------
    def native_startSync(self, seconds=60):
        self.start_polling(seconds)
        self.start_push()
        QTimer.singleShot(200, self.poll_now)
        return True

    def native_syncNow(self):
        self.poll_now()
        return True

    def native_setUnread(self, count=0):
        self._set_unread(int(count))
        return True

    def native_allowRemoteImages(self, allow=False):
        self.interceptor.allow_remote_images = bool(allow)
        return True

    def native_openExternal(self, url=''):
        self._open_link(QUrl(url))
        return True

    def _open_link(self, url):
        if url.scheme().lower() == 'mailto':
            self._to_page('mailto', url.path())
        elif url.scheme().lower() in ('http', 'https') and url.host():
            QDesktopServices.openUrl(url)

    def _parent(self):
        """Dialogs open over the window the user is in: the main window or a pop-out."""
        return QApplication.activeWindow() or self

    def native_pickFiles(self):
        paths, _ = QFileDialog.getOpenFileNames(self._parent(), '添加附件')
        result = []
        for path in paths:
            data = Path(path).read_bytes() if Path(path).stat().st_size <= 10 * 1024 * 1024 else None
            if data is None:
                raise AppError('ATTACHMENT_TOO_LARGE')
            result.append({'name': Path(path).name, 'type': mimetypes.guess_type(path)[0] or 'application/octet-stream',
                           'size': len(data), 'data': base64.b64encode(data).decode('ascii')})
        return result

    def native_saveAttachment(self, mid='', part=''):
        name, _kind, data = self.backend.attachment(mid, part)
        target, _ = QFileDialog.getSaveFileName(self._parent(), '保存附件', str(Path.home() / 'Downloads' / name))
        if not target:
            return False
        Path(target).write_bytes(data)
        return True

    def native_saveAllAttachments(self, mid=''):
        document = self.backend._document(mid)
        folder = QFileDialog.getExistingDirectory(self._parent(), '保存全部附件到', str(Path.home() / 'Downloads'))
        if not folder:
            return 0
        count = 0
        for info in document.attachments:
            data = document.attachment_payloads.get(info.part_id)
            if data is None:
                continue
            target = Path(folder) / info.name
            stem, suffix, n = target.stem, target.suffix, 1
            while target.exists():
                target = target.with_name(f'{stem} ({n}){suffix}')
                n += 1
            target.write_bytes(data)
            count += 1
        return count

    def native_openAttachment(self, mid='', part=''):
        name, _kind, data = self.backend.attachment(mid, part)
        # The data folder is known to be writable; some machines forbid TEMP for scripts.
        folder = self.backend.data_dir / 'opened' / os.urandom(6).hex()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        target.write_bytes(data)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        return True

    def native_unlockCertificate(self, password='', remember=False):
        path, _ = QFileDialog.getOpenFileName(self._parent(), '选择个人证书', str(Path.home()), '证书 (*.pfx *.p12)')
        if not path:
            return False
        try:
            return self.backend.unlock_certificate(path, password, bool(remember))
        except Exception as error:
            raise AppError(getattr(error, 'code', 'CERT_UNLOCK_FAILED')) from None

    def native_findText(self, text='', backward=False):
        flags = QWebEnginePage.FindFlag.FindBackward if backward else QWebEnginePage.FindFlag(0)
        self.page.findText(text, flags)
        return True

    def native_openReader(self, mid=''):
        if not isinstance(mid, str) or not mid or len(mid) > 300:
            raise AppError('INVALID_MAIL_ACTION')
        window = next((w for w in self.readers if w.mid == mid), None)
        if window is None:
            window = ReaderWindow(self, mid)
            self.readers.append(window)
            window.destroyed.connect(lambda _=None, w=window: self.readers.remove(w) if w in self.readers else None)
            offset = 28 * (len(self.readers) % 6)
            window.move(self.x() + 160 + offset, self.y() + 40 + offset)
        window.showNormal() if window.isMinimized() else window.show()
        window.raise_()
        window.activateWindow()
        return True

    def native_closeReaders(self):
        for window in list(self.readers):
            window.close()
        return True

    def native_quit(self):
        QTimer.singleShot(0, self.quit_app)
        return True
