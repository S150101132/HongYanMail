"""K1–K4 (第二轮架构评审): the rules that were repeated or leaked across a seam, each in one place.
Demo adapter only; nothing leaves the machine."""
import unittest
from pathlib import Path

from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter
from tests.test_app_backend import XorProtector, local_services_stub
from tests.test_compose_v05 import scratch

ROOT = Path(__file__).resolve().parents[1] / 'coremail_desktop'


def backend(test, adapter=DemoAdapter):
    b = MailBackend(scratch(test), adapter_factory=adapter, protector=XorProtector(), services_factory=local_services_stub)
    b.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
    test.addCleanup(b.logout)
    return b


class Busy(DemoAdapter):
    """Another thread lists a different folder while this list is on its way back."""
    def list_messages(self, folder_id, offset, limit=50, mboxa=''):
        page = super().list_messages(folder_id, offset, limit, mboxa)
        super().list_messages('3', 0, 1)
        return page


class ServerSeamTest(unittest.TestCase):  # K1
    def test_the_list_carries_its_folder_total(self):
        b = backend(self)
        page = b.adapter.list_messages('1', 0, 5)
        self.assertEqual(len(page), 5)
        self.assertEqual(page.total, sum(r['fid'] == '1' for r in b.adapter.rows.values()))  # The folder, not the page.
        self.assertFalse(hasattr(b.adapter, 'last_folder_total'))

    def test_a_list_elsewhere_does_not_change_the_total(self):
        b = backend(self, Busy)
        total = b.messages('1', 0, 10)['total']
        self.assertIsNotNone(total)
        self.assertEqual(total, b.adapter.list_messages('1', 0, 100).total)

    def test_the_demo_server_does_not_depend_on_the_client(self):
        self.assertFalse('app.backend' in (ROOT / 'app' / 'demo.py').read_text(encoding='utf-8'))
        from coremail_desktop.app import backend as backend_module, searchfilter
        self.assertIs(backend_module.filter_match, searchfilter.filter_match)  # Still importable from the backend.


if __name__ == '__main__':
    unittest.main()
