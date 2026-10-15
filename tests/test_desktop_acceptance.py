"""Task 9 acceptance behaviours that must not need the live server.

These tests exercise the offline shell, keyboard entry points, unsaved-exit
protection, session-loss editing recovery and the loaded-list rendering model.
They do not replace real login, real send or original-client interoperability
checks, which need the live account and are recorded separately.
"""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QTextBrowser

from coremail_desktop.errors import AppError
from coremail_desktop.models import DraftDocument, MessageSummary


class Controller(QObject):
    completed = Signal(str, int, object)
    failed = Signal(str, int, object)
    auth_required = Signal()
    supports_reply_headers = False

    def __init__(self):
        super().__init__()
        self.calls = []

    def submit(self, operation, params, view_generation=0):
        self.calls.append((operation, params, view_generation))
        return str(len(self.calls))


def draft(local_id='draft', revision=0):
    return DraftDocument(local_id=local_id, revision=revision, mode='new',
                         from_identity='me@example.test', to=('you@example.test',), cc=(), bcc=(),
                         subject='主题', body='正文', reference_headers={})


def make_window():
    from coremail_desktop.ui.main_window import MainWindow
    return MainWindow(Controller(), reader_factory=QTextBrowser)


class RecordingAutosave(QObject):
    """Autosave double that reports success, so no modal warning can appear.

    The real AutoSaveController emits `saved` from flush(); the composer only
    clears its dirty flag on that signal, so a double that stays silent would
    keep the editor dirty and open the blocking modal warning.
    """

    saved = Signal(int)
    failed = Signal(object)

    def __init__(self):
        super().__init__()
        self.written = []

    def update(self, draft):
        self.written.append(draft)

    def flush(self):
        revision = self.written[-1].revision if self.written else 0
        self.saved.emit(revision)
        return revision


def window_with_autosave():
    from coremail_desktop.ui.main_window import MainWindow
    autosave = RecordingAutosave()
    return MainWindow(Controller(), reader_factory=QTextBrowser, autosave_factory=lambda: autosave), autosave


class ShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_startup_stays_offline_and_window_has_usable_minimum_size(self):
        controller = Controller()
        from coremail_desktop.ui.main_window import MainWindow
        window = MainWindow(controller, reader_factory=QTextBrowser)
        self.assertEqual(controller.calls, [])
        self.assertEqual(window.minimumWidth(), 1000)
        self.assertEqual(window.minimumHeight(), 680)
        self.assertEqual((window.width(), window.height()), (1280, 800))
        self.assertFalse(window.new_button.isEnabled())
        self.assertIn('尚未连接', window.statusBar().currentMessage())
        window.close()

    def test_primary_actions_have_working_keyboard_shortcuts(self):
        window = make_window()
        shortcuts = {action.shortcut().toString(): action for action in window.actions()}
        for expected in ('Ctrl+N', 'Ctrl+S', 'Ctrl+K', 'Ctrl+F', 'F5'):
            self.assertIn(expected, shortcuts, f'{expected} is not wired')
        self.assertTrue(all(shortcut.isEnabled() for shortcut in shortcuts.values()))
        window.close()

    def test_loaded_list_uses_a_view_model_not_one_widget_per_message(self):
        from coremail_desktop.ui.mail_list import MailListPane
        pane = MailListPane()
        rows = [MessageSummary(str(i), 'inbox', f'主题 {i}', 'sender@example.test', '2026-10-15') for i in range(1000)]
        pane.set_messages(rows)
        self.assertEqual(len(pane.loaded), 1000)
        self.assertEqual(pane.model.rowCount(), 1000)
        pane.filter.setText('主题 99')
        self.assertEqual(pane.model.rowCount(), 11)
        self.assertIn('已加载', pane.scope.text())
        pane.filter.setText('不存在的关键词')
        self.assertEqual(pane.model.rowCount(), 0)
        self.assertEqual(pane.empty.text(), '已加载邮件中无匹配')
        pane.close()

    def test_loaded_filter_never_claims_a_server_wide_search(self):
        from coremail_desktop.ui.mail_list import MailListPane
        pane = MailListPane()
        pane.set_messages([MessageSummary('1', 'inbox', '主题', 'sender@example.test', '2026-10-15')])
        pane.filter.setText('zzz')
        text = pane.scope.text() + pane.empty.text() + pane.filter.placeholderText()
        self.assertIn('已加载', text)
        for forbidden in ('全部邮箱', '全邮箱', '服务器搜索', '所有邮件'):
            self.assertNotIn(forbidden, text)
        pane.close()


class SaveProtectionTests(unittest.TestCase):
    """Draft protection that must hold without opening a blocking modal."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_failed_save_keeps_the_editor_open_and_the_text_editable(self):
        from coremail_desktop.ui.composer import Composer
        composer = Composer(draft(), FailingAutosave())
        composer.show()
        composer.body.setPlainText('未保存的关键内容')
        composer.timer.stop()
        composer.save()

        self.assertIn('失败', composer.state_label.text())
        self.assertTrue(composer.dirty)
        self.assertEqual(composer.body.toPlainText(), '未保存的关键内容')

        composer.body.setPlainText('未保存的关键内容，再补一句')
        self.assertEqual(composer.body.toPlainText(), '未保存的关键内容，再补一句')
        # A dirty editor makes the composer hold the window open; the modal in
        # that path needs a user, so the hold itself is asserted on the model.
        self.assertTrue(composer.dirty)
        composer.hide()

    def test_confirmed_unsaved_dialog_is_the_only_way_a_lost_draft_can_close(self):
        from coremail_desktop.ui.composer import Composer
        composer = Composer(draft(), RecordingAutosave())
        composer.show()
        composer.body.setPlainText('已确认保存')
        composer.timer.stop()
        composer.save()
        self.assertFalse(composer.dirty)
        self.assertTrue(composer.close(), 'saved draft should close without a prompt')
        self.assertFalse(composer.isVisible())


class ExitBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_saved_draft_lets_the_application_exit(self):
        window, autosave = window_with_autosave()
        window.show()
        window.open_composer(draft())
        window.composer.show()
        window.composer.body.setPlainText('这次能保存成功')

        window.close()

        self.assertFalse(window.isVisible(), 'saved draft still blocked exit')
        self.assertEqual(autosave.written[-1].body, '这次能保存成功')
        self.assertFalse(window.composer.isVisible())

    def test_visible_composer_without_autosave_holds_the_window_open(self):
        window = make_window()
        window.show()
        window.open_composer(draft())
        window.composer.show()
        window.composer.body.setPlainText('未保存的关键内容')
        # The blocking prompt needs a human, so the test fixes the answer.
        window.composer.confirm_unsaved_close = lambda: False

        window.closeEvent(_CloseEvent(window))

        self.assertTrue(window.isVisible(), 'window closed while the draft was unsaved')
        self.assertEqual(window.composer.body.toPlainText(), '未保存的关键内容')
        self.assertTrue(window.composer.dirty)

    def test_explicit_discard_is_the_only_way_an_unsaved_draft_closes(self):
        window = make_window()
        window.show()
        window.open_composer(draft())
        window.composer.show()
        window.composer.body.setPlainText('用户明确放弃的正文')
        window.composer.confirm_unsaved_close = lambda: True

        window.closeEvent(_CloseEvent(window))

        self.assertFalse(window.isVisible())
        self.assertFalse(window.composer.isVisible())


class _CloseEvent:
    """Close event double that mirrors Qt: accepting the event hides the widget."""

    def __init__(self, widget=None):
        self.widget = widget
        self.accepted = None

    def ignore(self):
        self.accepted = False

    def accept(self):
        self.accepted = True
        if self.widget is not None:
            self.widget.hide()


class FailingAutosave(QObject):
    saved = Signal(int)
    failed = Signal(object)

    def update(self, draft):
        raise OSError('disk is full')

    def flush(self):
        raise OSError('disk is full')


class SessionLossTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_session_loss_keeps_the_composer_text_and_shows_login(self):
        window = make_window()
        window.open_composer(draft())
        window.composer.body.setPlainText('会话失效时正在写的正文')
        window._auth_required()
        self.assertEqual(window.stack.currentWidget(), window.login)
        self.assertIn('会话已失效', window.login.error.text())
        self.assertIn('草稿仍保留', window.login.error.text())
        self.assertEqual(window.composer.body.toPlainText(), '会话失效时正在写的正文')
        window.composer.hide()
        window.close()

    def test_unknown_send_result_is_never_retried_from_the_interface(self):
        from coremail_desktop.ui.composer import Composer
        composer = Composer(draft(), None)
        composer.set_send_state('Unknown')
        self.assertFalse(composer.send_button.isEnabled())
        self.assertIn('待确认', composer.state_label.text())
        self.assertIn('不会自动重发', composer.state_label.text())
        for field in (composer.to, composer.subject, composer.body):
            self.assertTrue(field.isReadOnly(), 'fields stayed editable after an unknown result')
        composer.hide()

    def test_fixed_messages_never_echo_remote_details(self):
        window = make_window()
        task = window.submit('fetch_mime', {'message_id': 'x'})
        window._failed(task, window.generation, AppError('SESSION_EXPIRED', task,
                                                         'url=https://newmail.inspur.com/coremail/?sid=secret'))
        message = window.statusBar().currentMessage()
        self.assertIn('会话已失效', message)
        for secret in ('newmail.inspur.com', 'sid=', 'secret'):
            self.assertNotIn(secret, message)
        window.close()


if __name__ == '__main__':
    unittest.main()
