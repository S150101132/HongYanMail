"""Reading pane: the sender's time is shown only when the mail was held on the way."""
import unittest

from coremail_desktop.app.backend import _sent_date


class SentDateTest(unittest.TestCase):
    def test_held_mail_shows_sent_time_in_server_zone(self):
        # 16:11:15 Beijing = 11:11:15 Riyadh; the server received it at 12:12.
        self.assertEqual(_sent_date('Tue, 27 Oct 2026 16:11:15 +0800', '2026-10-27 12:12:03'), '2026-10-27 11:11:15')

    def test_small_gap_shows_nothing(self):
        self.assertEqual(_sent_date('Tue, 27 Oct 2026 11:50:00 +0300', '2026-10-27 12:12:03'), '')

    def test_bad_or_missing_dates_show_nothing(self):
        self.assertEqual(_sent_date('', '2026-10-27 12:12:03'), '')
        self.assertEqual(_sent_date('not a date', '2026-10-27 12:12:03'), '')
        self.assertEqual(_sent_date('Tue, 27 Oct 2026 16:11:15 +0800', 'yesterday'), '')


if __name__ == '__main__':
    unittest.main()
