"""B7: what the main window's ✕ does, and the check for a tray icon Windows refused."""
import unittest
from unittest import mock

from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter
from coremail_desktop.app.tray_check import _same_program
from tests.test_app_backend import XorProtector, local_services_stub
from tests.test_compose_v05 import scratch


class TrayRecordTest(unittest.TestCase):
    def test_paths_match_literally_or_in_known_folder_form(self):
        exe = r'd:\workspace\hongyan\hongyan.exe'
        self.assertTrue(_same_program(r'D:\workspace\Hongyan\Hongyan.exe', exe))
        self.assertFalse(_same_program(r'D:\Program Files\Tencent\Weixin\Weixin.exe', exe))
        self.assertTrue(_same_program(r'{6D809377-6AF0-444B-8957-A3773F02200E}\Hongyan\Hongyan.exe', r'c:\program files\hongyan\hongyan.exe'))
        self.assertFalse(_same_program('', exe))


class ClosePrefTest(unittest.TestCase):
    def test_only_known_choices_are_stored(self):
        b = MailBackend(scratch(self), adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        b.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw')
        self.addCleanup(b.logout)
        self.assertEqual(b.prefs()['closeAction'], '')
        self.assertEqual(b.set_prefs({'closeAction': 'tray'})['closeAction'], 'tray')
        self.assertEqual(b.set_prefs({'closeAction': 'reboot'})['closeAction'], '')


class CloseEventTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        try:
            import qt_test_support  # noqa: F401
        except ImportError:
            from tests import qt_test_support  # noqa: F401
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from coremail_desktop.app import shell
        self.shell = shell
        self.backend = MailBackend(scratch(self), adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw')
        self.window = shell.AppShell(self.backend, demo=False)
        self.quits = []
        app = type('A', (), {'quit': staticmethod(lambda: self.quits.append(1))})()
        patcher = mock.patch.object(shell, 'QApplication', type('Q', (), {'instance': staticmethod(lambda: app), 'alert': staticmethod(lambda *a: None)}))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: (self.window.bridge.shutdown(), self.window.deleteLater(), self.backend.logout()))

    def close(self, action=None, asked=None, tray=True):
        from PySide6.QtGui import QCloseEvent
        if action is not None:
            self.backend.set_prefs({'closeAction': action})
        self.window.show()
        event = QCloseEvent()
        with mock.patch.object(self.window, '_ask_close', return_value=asked) as ask, \
                mock.patch.object(self.window, '_tray_shown', return_value=tray):
            self.window.closeEvent(event)
        return event, ask

    def test_first_close_asks_and_cancel_keeps_the_window(self):
        event, ask = self.close(asked=None)
        ask.assert_called_once()
        self.assertFalse(event.isAccepted())
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.quits, [])

    def test_tray_choice_hides_and_keeps_running(self):
        event, ask = self.close('tray')
        ask.assert_not_called()
        self.assertFalse(event.isAccepted())
        self.assertFalse(self.window.isVisible())
        self.assertEqual(self.quits, [])

    def test_without_a_tray_icon_the_window_is_only_minimized(self):
        event, _ = self.close('tray', tray=False)
        self.assertFalse(event.isAccepted())
        self.assertTrue(self.window.isMinimized())

    def test_quit_choice_and_signed_out_exit(self):
        event, _ = self.close('quit')
        self.assertTrue(event.isAccepted())
        self.assertEqual(self.quits, [1])
        self.backend.logout()
        event, ask = self.close()
        ask.assert_not_called()
        self.assertTrue(event.isAccepted())


if __name__ == '__main__':
    unittest.main()
