import datetime
import unittest

from coremail_desktop.app.quotes import QUOTES, today_quote
from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter
from tests.test_app_backend import XorProtector, local_services_stub
from tests.test_compose_v05 import scratch
from coremail_desktop.app.shell import _colorref


class TitleBarTests(unittest.TestCase):
    def test_one_line_a_day(self):
        day = datetime.date(2026, 10, 28)
        self.assertEqual(today_quote(day), today_quote(day))
        self.assertNotEqual(today_quote(day), today_quote(day + datetime.timedelta(days=1)))
        self.assertTrue(all(len(q) <= 40 and '——' in q for q in QUOTES))

    def test_only_plain_hex_colours_reach_windows(self):
        self.assertEqual(_colorref('#dfe6ff'), 0xffe6df)
        for bad in ('', 'linear-gradient(#fff, #000)', '#fff', '#zzzzzz', None):
            self.assertIsNone(_colorref(bad))

    def test_own_colour_is_kept_and_checked(self):
        backend = MailBackend(scratch(self), adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(backend.logout)
        self.assertEqual(backend.prefs()['titleBar'], '')  # Follows the theme.
        self.assertEqual(backend.set_prefs({'titleBar': '#7C3AED'})['titleBar'], '#7c3aed')
        self.assertEqual(backend.set_prefs({'titleBar': 'red; x'})['titleBar'], '')


if __name__ == '__main__':
    unittest.main()
