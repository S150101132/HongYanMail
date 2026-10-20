"""设置 › 左侧栏显示: the hidden sidebar entries are kept as a clean list of ids."""
import unittest

from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter
from tests.test_app_backend import XorProtector
from tests.test_compose_v05 import scratch


class SidebarPrefsTest(unittest.TestCase):
    def test_hidden_folders_are_validated(self):
        backend = MailBackend(scratch(self), adapter_factory=DemoAdapter, protector=XorProtector())
        backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'demo', True)
        self.addCleanup(backend.logout)
        self.assertEqual(backend.prefs()['hiddenFolders'], [])
        prefs = backend.set_prefs({'hiddenFolders': ['6', 'local:contacts', '6', '', 7, 'x' * 201]})
        self.assertEqual(prefs['hiddenFolders'], ['6', 'local:contacts'])
        self.assertEqual(backend.set_prefs({'hiddenFolders': 'not a list'})['hiddenFolders'], ['6', 'local:contacts'])


if __name__ == '__main__':
    unittest.main()
