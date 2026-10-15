import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QLineEdit

class Controller(QObject):
    completed = Signal(str, int, object)
    failed = Signal(str, int, object)
    auth_required = Signal()
    def __init__(self):
        super().__init__(); self.calls=[]
    def submit(self, operation, params, view_generation=0):
        self.calls.append((operation,params,view_generation)); return str(len(self.calls))

class DesktopUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def test_startup_is_offline_and_password_is_masked(self):
        from coremail_desktop.ui.main_window import MainWindow
        c=Controller(); w=MainWindow(c, reader_factory=lambda: __import__('PySide6.QtWidgets',fromlist=['QTextBrowser']).QTextBrowser())
        self.assertEqual(c.calls,[])
        self.assertEqual(w.login.password.echoMode(),QLineEdit.EchoMode.Password)
        self.assertEqual(w.minimumWidth(),1000)
        w.close()
    def test_filter_label_is_explicit_about_loaded_scope(self):
        from coremail_desktop.ui.mail_list import MailListPane
        w=MailListPane(); w.set_messages([]); w.filter.setText('unmatched')
        self.assertIn('已加载',w.scope.text())
        self.assertIn('已加载邮件中无匹配',w.empty.text())
    def test_unknown_send_is_not_retryable(self):
        from coremail_desktop.ui.composer import Composer
        from coremail_desktop.models import DraftDocument
        w=Composer(DraftDocument(local_id='draft',revision=0,mode='new',from_identity='a@example.test',to=(),cc=(),bcc=(),subject='',body='',reference_headers={}))
        w.set_send_state('Unknown')
        self.assertFalse(w.send_button.isEnabled())
        self.assertIn('待确认',w.state_label.text())
    def test_late_folder_page_cannot_replace_current_folder(self):
        from coremail_desktop.ui.main_window import MainWindow
        from coremail_desktop.models import MessageSummary
        from PySide6.QtWidgets import QTextBrowser
        c=Controller(); w=MainWindow(c,reader_factory=QTextBrowser); w.generation=2; w.pending['old']=('list_messages',{'offset':0})
        w._completed('old',1,[MessageSummary('old','f','stale','sender','today')])
        self.assertEqual(w.mail_list.loaded,[]); w.close()
    def test_refresh_preserves_selection_and_scroll(self):
        from coremail_desktop.ui.mail_list import MailListPane
        from coremail_desktop.models import MessageSummary
        w=MailListPane(); rows=[MessageSummary(str(i),'f',str(i),'sender','date') for i in range(1000)]
        w.set_messages(rows); w.view.setCurrentIndex(w.model.index(500)); w.set_messages(rows)
        self.assertEqual(w.view.currentIndex().data(256).remote_id,'500')

if __name__=='__main__': unittest.main()
