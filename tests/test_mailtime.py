"""邮件时间 (Z1): stored as Riyadh text whatever the computer's zone; shown, and 「今天」 counted,
in the computer's zone. Expected values are worked by hand from UTC, not by the code under test."""
import unittest
from datetime import datetime, timedelta, timezone

from coremail_desktop.app.cache import date_ts
from coremail_desktop.protocol import mailtime
from coremail_desktop.tasks.rules import local_today

SHANGHAI = timezone(timedelta(hours=8))
UTC_0850 = datetime(2026, 11, 8, 8, 50, 12, tzinfo=timezone.utc)  # 11:50:12 Riyadh, 16:50:12 Shanghai.


class MailTimeTest(unittest.TestCase):
    def test_a_date_written_in_another_zone_is_stored_as_riyadh_text(self):
        self.assertEqual(mailtime.stored('2026-11-08 16:50:12', SHANGHAI), '2026-11-08 11:50:12')
        self.assertEqual(mailtime.stored('2026-11-08 11:50:12', mailtime.STORE_ZONE), '2026-11-08 11:50:12')
        self.assertEqual(mailtime.stored('昨天', SHANGHAI), '昨天')  # Unreadable: as it was.

    def test_stored_text_is_one_moment_whatever_the_computer_zone(self):
        self.assertEqual(mailtime.moment('2026-11-08 11:50:12'), UTC_0850)
        self.assertEqual(mailtime.moment('2026-11-08 11:50'), UTC_0850.replace(second=0))
        self.assertIsNone(mailtime.moment(''))
        self.assertEqual(date_ts('2026-11-08 11:50:12'), UTC_0850.timestamp())  # Sorting, filters, due checks.
        self.assertEqual(date_ts('2026-11-08'), datetime(2026, 11, 7, 21, tzinfo=timezone.utc).timestamp())

    def test_the_account_zone_is_read_as_before(self):
        self.assertEqual(mailtime.account_zone('Asia/Shanghai'), SHANGHAI)
        self.assertEqual(mailtime.account_zone('GMT+05:30'), timezone(timedelta(hours=5, minutes=30)))

    def test_today_is_the_day_where_the_computer_is(self):
        late = datetime(2026, 11, 8, 22, 30, tzinfo=timezone.utc)
        self.assertEqual(local_today(late, SHANGHAI), '2026-11-09')  # 06:30 next day in Beijing.
        self.assertEqual(local_today(late, mailtime.STORE_ZONE), '2026-11-09')  # 01:30 in Riyadh.
        self.assertEqual(local_today(late, timezone(timedelta(hours=-5))), '2026-11-08')
        self.assertEqual(local_today(datetime(2026, 11, 8, 23, 59).astimezone()), '2026-11-08')  # This computer.


if __name__ == '__main__':
    unittest.main()
