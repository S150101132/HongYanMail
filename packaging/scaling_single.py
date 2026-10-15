"""Measure the shell at one display scale factor (run as its own process).

QT_SCALE_FACTOR only takes effect when it is set before QApplication exists, so
the caller launches this once per factor and reads the JSON it prints.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QTextBrowser  # noqa: E402


class Controller(QObject):
    completed = Signal(str, int, object)
    failed = Signal(str, int, object)
    auth_required = Signal()
    supports_reply_headers = False

    def submit(self, operation, params, view_generation=0):
        return '1'


def main():
    factor = float(sys.argv[1])
    app = QApplication.instance() or QApplication([])
    from coremail_desktop.ui.main_window import MainWindow
    window = MainWindow(Controller(), reader_factory=QTextBrowser)
    window.show()
    # Show the workbench, otherwise every pane reports hidden as a child of the
    # non-current stack page.
    window.stack.setCurrentWidget(window.splitter)
    app.processEvents()

    panes = {'folders': window.folders, 'list': window.mail_list, 'reader': window.reader}
    toolbar = window.findChild(__import__('PySide6.QtWidgets', fromlist=['QToolBar']).QToolBar)
    buttons = {'new_mail': window.new_button, 'refresh': window.mail_list.refresh,
               'load_more': window.mail_list.more}
    result = {
        'scale_factor': factor,
        'device_pixel_ratio': round(window.devicePixelRatioF(), 3),
        'applied_ratio': round(app.devicePixelRatio(), 3),
        'window_size': [window.width(), window.height()],
        'minimum_size': [window.minimumWidth(), window.minimumHeight()],
        'minimum_holds': window.width() >= window.minimumWidth() and window.height() >= window.minimumHeight(),
        'panes_visible': {name: bool(pane.isVisible()) for name, pane in panes.items()},
        'pane_widths': {name: pane.width() for name, pane in panes.items()},
        'splitter_sizes': window.splitter.sizes(),
        'all_panes_have_width': all(pane.width() > 0 for pane in panes.values()),
        'all_panes_visible': all(pane.isVisible() for pane in panes.values()),
        'toolbar_present': toolbar is not None,
        'buttons': {name: {'width': button.width(), 'height': button.height(),
                           'usable': button.width() >= 40 and button.height() >= 20}
                    for name, button in buttons.items()},
    }
    result['all_buttons_usable'] = all(item['usable'] for item in result['buttons'].values())
    print(json.dumps(result, ensure_ascii=False))
    window.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
