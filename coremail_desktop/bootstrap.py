"""Explicit, offline startup. Services access the mailbox only after login."""
import argparse
import os
import sys
from pathlib import Path

def main(argv=None):
    parser=argparse.ArgumentParser(description='鸿雁 · 工作邮箱')
    parser.add_argument('--demo',action='store_true',help='show explicitly synthetic data; no network')
    parser.add_argument('--classic',action='store_true',help='the earlier Qt Widgets interface')
    parser.add_argument('--minimized',action='store_true',help='start hidden in the tray (used by 开机自动启动)')
    parser.add_argument('file',nargs='?',help='a .eml file to open read-only (双击 .eml 文件)')
    args=parser.parse_args(argv)
    if not args.classic:
        return run_app(args.demo,args.minimized,args.file)
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QSettings
    from .ui.theme import apply_theme
    from .ui.main_window import MainWindow
    app=QApplication.instance() or QApplication(sys.argv); app.setApplicationName('鸿雁'); app.setApplicationDisplayName('鸿雁 · 工作邮箱'); app.setOrganizationName('CoremailPreview')
    apply_theme(app,QSettings('CoremailPreview','Desktop').value('dark',False,type=bool))
    if args.demo:
        from .ui.demo import DemoController
        controller=DemoController()
    else:
        from .session.controller import SessionController
        from .storage.services import local_services
        from .mime.parser import parse_mime
        from .crypto.service import CryptoService
        crypto=CryptoService()
        data=Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'CoremailDesktopPreview'
        def services(adapter):
            local=local_services(adapter,data)
            def parse_message(raw,message_id):
                from .crypto.formats import detect_envelope_format
                # Crypto service refuses unverified formats; normal MIME is independent.
                if detect_envelope_format(raw):
                    from dataclasses import replace
                    from email import policy
                    from email.parser import BytesHeaderParser
                    result=crypto.decrypt(raw); document=parse_mime(raw=result.mime_bytes,message_id=message_id)
                    outer=BytesHeaderParser(policy=policy.default).parsebytes(raw)
                    headers=dict(document.headers)
                    for name in ('from','to','cc','subject','reply-to','message-id','references','date'):
                        if outer.get_all(name): headers[name]=tuple(str(v) for v in outer.get_all(name))
                    return replace(document,headers=headers,encryption_state='decrypted')
                return parse_mime(raw=raw,message_id=message_id)
            return {**local,'parse_message':parse_message,'unlock_certificate':crypto.unlock,'lock_certificate':crypto.lock}
        controller=SessionController(services=services)
    window=MainWindow(controller,demo=args.demo)
    if not args.demo:
        def certificate_dialog():
            from .ui.certificate_dialog import CertificateDialog
            dialog=CertificateDialog(crypto,window); dialog.certificate_locked.connect(window._lock); dialog.exec()
        window.certificate_requested.connect(certificate_dialog)
        window.lock_requested.connect(lambda:controller.submit('lock_certificate',{},0))
    app.aboutToQuit.connect(controller.close)
    window.show()
    if args.demo: controller.populate(window)
    return app.exec()


SANDBOX_RETRY_SECONDS=7*24*3600


def _writable(folder):
    try:
        folder.mkdir(parents=True,exist_ok=True)
        probe=folder / ('.write-test-%d' % os.getpid())
        probe.write_bytes(b'x'); probe.unlink()
        return True
    except OSError:
        return False


_DATA_ROOT=None


def _data_root():
    """Per-user data folder. Some managed machines forbid script interpreters from
    writing anywhere in the user profile; then the data lives beside the program.
    Everything stored there is ciphertext (DPAPI / AES-GCM), wherever it is."""
    global _DATA_ROOT
    if _DATA_ROOT is None:
        preferred=Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'CoremailDesktopPreview'
        beside=(Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]) / 'HongyanData'
        override=os.environ.get('HONGYAN_DATA_DIR')
        candidates=[Path(override)] if override else [preferred,beside]
        _DATA_ROOT=next((c for c in candidates if _writable(c)),candidates[0])
    return _DATA_ROOT


def _relaunch_without_sandbox():
    """Start a fresh copy of this program with the Chromium sandbox off, once."""
    import subprocess
    try:
        marker=_data_root() / 'sandbox-unavailable'
        marker.parent.mkdir(parents=True,exist_ok=True); marker.write_text('renderer exited before the first page load\n',encoding='utf-8')
    except OSError:
        pass
    command=[sys.executable,*sys.argv[1:]] if getattr(sys,'frozen',False) else [sys.executable,'-m','coremail_desktop',*sys.argv[1:]]
    subprocess.Popen(command,env={**os.environ,'HONGYAN_NO_SANDBOX':'1'},close_fds=True)


def run_app(demo=False,minimized=False,eml=None):
    """The daily-use interface: web UI in Qt WebEngine over the verified protocol layer."""
    import time
    # Some managed Windows machines stop Chromium's sandboxed renderer from starting
    # (it exits before the first page load). Then the app runs without that sandbox;
    # mail bodies stay in a script-less, network-blocked, CSP-restricted frame.
    marker=_data_root() / 'sandbox-unavailable'
    try: recent=time.time()-marker.stat().st_mtime<SANDBOX_RETRY_SECONDS
    except OSError: recent=False
    if os.environ.get('HONGYAN_NO_SANDBOX')=='1' or recent:
        os.environ['QTWEBENGINE_DISABLE_SANDBOX']='1'
    sandboxed=os.environ.get('QTWEBENGINE_DISABLE_SANDBOX')!='1'
    from .app import shell  # Registers the private URL scheme before QApplication exists.
    from PySide6.QtWidgets import QApplication, QMessageBox
    from .ui.branding import set_app_id
    set_app_id()
    app=QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName('鸿雁'); app.setApplicationDisplayName('鸿雁 · 工作邮箱'); app.setOrganizationName('CoremailPreview')
    app.setQuitOnLastWindowClosed(False)
    # One copy per user: a second launch shows the running window and exits. Demo runs
    # use their own name so they never hand off to (or block) the real client.
    from .app.single_instance import SingleInstance, eml_path
    guard=SingleInstance('demo' if demo else '')
    eml=eml_path(eml) if eml else None  # A double-clicked .eml file; the owner opens it.
    if not guard.acquire():
        # A sandbox relaunch takes over from its dying parent instead of activating it.
        if os.environ.get('HONGYAN_NO_SANDBOX')!='1' and guard.activate_existing(path=eml):
            return 0
        guard.listen()  # The owner did not answer (it is exiting): carry on as the new owner.
    from .app.backend import MailBackend
    from .ui.branding import app_icon
    app.setWindowIcon(app_icon())
    if demo:
        from .app.demo import DemoAdapter, DemoAiClient
        # Synthetic data only, in a fixed per-user folder that is wiped at start and exit,
        # so demo runs never leave files beside the executable or the working directory.
        import atexit,shutil
        demo_dir=_data_root() / 'demo'
        shutil.rmtree(demo_dir,ignore_errors=True)
        atexit.register(shutil.rmtree,demo_dir,True)
        backend=MailBackend(demo_dir,adapter_factory=DemoAdapter)
        backend.ai_client=DemoAiClient()
        from .ai.trending import demo_get
        backend.trending_get=demo_get
        backend.login('https://demo.invalid/coremail/','demo@example.invalid','demo',True)
    else:
        from .crypto.service import CryptoService
        try:
            backend=MailBackend(_data_root(),crypto=CryptoService())
        except PermissionError:
            QMessageBox.critical(None,'鸿雁 · 无法启动','无法写入本机数据目录：\n'+str(_data_root())+
                                 '\n\n请从“开始”菜单或文件资源管理器启动鸿雁（例如双击 run_desktop.cmd），'
                                 '不要从受限的终端或其他程序中启动。')
            return 1
        from .app.autostart import Autostart
        backend.autostart=Autostart()
        try: backend.autostart.refresh()
        except OSError: pass
    def relaunch():
        # Hand over ownership first, so the relaunched copy does not activate this dying one.
        guard.release(); _relaunch_without_sandbox()
    window=shell.AppShell(backend,demo=demo,sandboxed=sandboxed,relaunch_without_sandbox=None if not sandboxed else relaunch)
    guard.activated.connect(window.bring_to_front)
    guard.open_requested.connect(window.open_eml_file)
    from PySide6.QtWidgets import QSystemTrayIcon
    if minimized and QSystemTrayIcon.isSystemTrayAvailable():
        window.start_in_background()
    else:
        window.show()  # Minimized: only the tray icon; the page still loads, signs in and polls.
    if eml:
        window.open_eml_file(eml)
    return app.exec()
