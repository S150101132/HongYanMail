"""Daily-use regressions with synthetic mail and no server access."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import QApplication, QTextBrowser, QTreeWidgetItem, QWidget
from coremail_desktop.models import MessageSummary
from coremail_desktop.ui.main_window import MainWindow


class Controller(QObject):
    completed = Signal(str, int, object)
    failed = Signal(str, int, object)
    auth_required = Signal()

    def __init__(self):
        super().__init__()
        self.calls = []

    def submit(self, operation, params, view_generation=0):
        self.calls.append((operation, dict(params), view_generation))
        return str(len(self.calls))


class UsabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = Controller()
        self.window = MainWindow(self.controller, reader_factory=QTextBrowser)
        self.window._set_authenticated(True)
        self.window.folder_id = '1'
        self.addCleanup(self.window.close)

    def test_folder_switch_clears_entire_previous_message(self):
        w = self.window
        w.subject.setText('Old subject')
        w.headers.setText('Old headers')
        w.attachments.setText('Old attachment')
        item = QTreeWidgetItem(['New folder'])
        item.setData(0, Qt.ItemDataRole.UserRole, '2')
        w._folder_selected(item, 0)
        self.assertNotIn('Old', w.subject.text())
        self.assertEqual(w.headers.text(), '')
        self.assertEqual(w.attachments.text(), '')

    def test_selecting_message_does_not_invalidate_same_folder_page(self):
        w = self.window
        w.refresh()
        task, generation = str(len(self.controller.calls)), w.generation
        w._select_message(MessageSummary('a', '1', 'A', 'sender', 'date'))
        row = MessageSummary('b', '1', 'B', 'sender', 'date')
        w._completed(task, generation, [row])
        self.assertEqual(w.mail_list.loaded, [row])

    def test_duplicate_page_requests_are_coalesced_until_completion(self):
        w = self.window
        w.load_more()
        w.load_more()
        self.assertEqual(len(self.controller.calls), 1)
        task = str(len(self.controller.calls))
        w._completed(task, w.generation, [])
        w.refresh()
        self.assertEqual(len(self.controller.calls), 2)

    def test_ui_pending_context_does_not_keep_login_password(self):
        self.window.submit('login', {'password': 'synthetic', 'account': object()}, 0)
        self.assertNotIn('password', self.window.pending['1'][1])

    def test_keyboard_folder_selection_loads_selected_folder(self):
        w = self.window
        for name, folder_id in [('Inbox', '1'), ('Sent', '2')]:
            item = QTreeWidgetItem([name])
            item.setData(0, Qt.ItemDataRole.UserRole, folder_id)
            w.folders.addTopLevelItem(item)
        w.folders.setCurrentItem(w.folders.topLevelItem(1))
        self.assertEqual(w.folder_id, '2')
        self.assertEqual(self.controller.calls[-1][1]['folder_id'], '2')

    def test_unread_filter_stays_within_loaded_messages(self):
        pane = self.window.mail_list
        unread = MessageSummary('u', '1', 'Unread', 'sender', 'date', {'read': False})
        read = MessageSummary('r', '1', 'Read', 'sender', 'date', {'read': True})
        pane.set_messages([unread, read])
        self.assertIsNotNone(getattr(pane, 'unread_only', None))
        pane.unread_only.setChecked(True)
        self.assertEqual(pane.model.rows, [unread])
        self.assertIn('已加载', pane.scope.text())

    def test_session_loss_rejects_late_mail_content(self):
        w = self.window
        w._select_message(MessageSummary('a', '1', 'A', 'sender', 'date'))
        task, generation = str(len(self.controller.calls)), w.generation
        w._auth_required()
        before = len(self.controller.calls)
        w._completed(task, generation, b'late synthetic MIME')
        self.assertEqual(len(self.controller.calls), before)
        self.assertEqual(w.headers.text(), '')

    def test_find_reaches_real_reader_web_view(self):
        class Reader(QWidget):
            def __init__(self):
                super().__init__()
                self.web_view = QTextBrowser(self)
                self.web_view.setPlainText('needle in message')
                self.fallback = QTextBrowser(self)
                self.fallback.hide()
            def clear(self): pass
        w = MainWindow(self.controller, reader_factory=Reader)
        self.addCleanup(w.close)
        with patch('coremail_desktop.ui.main_window.QInputDialog.getText', return_value=('needle', True)):
            w.find_body()
        self.assertEqual(w.reader.web_view.textCursor().selectedText(), 'needle')

    def test_old_save_result_is_not_applied_to_replacement_composer(self):
        from coremail_desktop.models import DraftDocument
        def draft(key):
            return DraftDocument(key, 0, 'new', 'a@example.test', (), (), (), '', '', {})
        w = self.window
        w.open_composer(draft('old'))
        old = w.composer
        old.save()
        task = str(len(self.controller.calls))
        old.close()
        w.open_composer(draft('new'))
        w.composer.dirty = True
        w._completed(task, 0, 0)
        self.assertTrue(w.composer.dirty)
        w.composer.dirty = False

    def test_send_preparation_failure_displays_safe_reason(self):
        from coremail_desktop.models import DraftDocument
        from coremail_desktop.ui.messages import user_message
        w=self.window
        w.open_composer(DraftDocument('failed',0,'new','me@example.test',('to@example.test',),(),(),'s','body',{}))
        w.pending['failed']=('request_send',{})
        w._completed('failed',0,{'state':'Failed','failure_code':'REPLY_SOURCE_MISMATCH'})
        self.assertIn(user_message('REPLY_SOURCE_MISMATCH'),w.composer.state_label.text())


if __name__ == '__main__':
    unittest.main()
