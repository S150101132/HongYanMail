"""Release test-owned Qt windows before Python/native-library finalization.

Widget tests intentionally retain QApplication on several test classes. Windows
Qt 6.12's VSync service aborts if that application's surviving windows are left
to interpreter teardown. This follows the same normal QObject destruction path;
it never bypasses exit handling or changes a test result.
"""
import atexit
import shiboken6
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication

def cleanup_qt():
    app=QApplication.instance()
    if app is None: return
    for widget in list(app.topLevelWidgets()):
        if shiboken6.isValid(widget): shiboken6.delete(widget)
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    app.shutdown()

atexit.register(cleanup_qt)
