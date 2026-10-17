"""Explicit, offline startup. Services access the mailbox only after login."""
import argparse
import os
import sys
from pathlib import Path

def main(argv=None):
    parser=argparse.ArgumentParser(description='鸿雁 · 工作邮箱')
    parser.add_argument('--demo',action='store_true',help='show explicitly synthetic data; no network')
    args=parser.parse_args(argv)
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
