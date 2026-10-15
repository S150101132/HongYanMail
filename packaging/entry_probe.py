"""Diagnostic entry point: same imports as packaging/entry.py but prints failures.

Used only to diagnose a trimmed bundle; it is not part of the shipped build.
"""
import os
import sys
import traceback
from pathlib import Path

LOG = Path(os.environ.get('COREMAIL_PROBE_LOG', 'coremail-entry-trace.log'))


def main():
    with open(LOG, 'w', encoding='utf-8') as stream:
        stream.write('start\n')
        stream.flush()
        try:
            import PySide6
            stream.write('PySide6 %s\n' % PySide6.__version__)
            from PySide6.QtCore import QSettings
            stream.write('QtCore ok\n')
            from PySide6.QtWidgets import QApplication
            stream.write('QtWidgets ok\n')
            from PySide6.QtWebEngineCore import QWebEngineProfile
            stream.write('QtWebEngineCore ok\n')
            from PySide6.QtWebEngineWidgets import QWebEngineView
            stream.write('QtWebEngineWidgets ok\n')
            from coremail_desktop.bootstrap import main as bootstrap_main
            stream.write('bootstrap ok\n')
            app = QApplication.instance() or QApplication([])
            stream.write('QApplication ok\n')
            from coremail_desktop.ui.main_window import MainWindow
            stream.write('MainWindow import ok\n')
            from coremail_desktop.reader.view import ReaderView
            view = ReaderView()
            stream.write('ReaderView web_view=%s\n' % (view.web_view is not None))
        except Exception:
            stream.write(traceback.format_exc())
            stream.flush()
            return 3
        stream.write('ALL OK\n')
        stream.flush()
    return 0


if __name__ == '__main__':
    sys.exit(main())
