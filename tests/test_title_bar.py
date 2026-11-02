import datetime
import unittest

from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.daily_english import BY_ID, ORDER, SENTENCES, fetch, today_line, today_sentence
from coremail_desktop.app.demo import DemoAdapter
from tests.test_app_backend import XorProtector, local_services_stub
from tests.test_compose_v05 import scratch
from coremail_desktop.app.shell import _colorref


DAY = datetime.date(2026, 11, 1)
ANSWER = ('{"date": "2026-11-01", "en": "Cold air sharpens the sky, and the heart follows.", "zh": "冷空气擦亮了天空，心也跟着清亮。",'
          ' "audio": "https://x.test/a.mp3", "image": "https://x.test/a.png", "share_image": "https://x.test/b.png"}')


class SentenceFeedTests(unittest.TestCase):
    def test_the_real_answer_and_nothing_about_the_user_goes_out(self):
        asked = []
        self.assertEqual(fetch(DAY, lambda url: asked.append(url) or ANSWER), {'en': 'Cold air sharpens the sky, and the heart follows.', 'zh': '冷空气擦亮了天空，心也跟着清亮。'})
        self.assertEqual(asked, ['https://api.qhsou.com/api/ciba.php'])

    def test_the_date_decides_whether_it_is_todays(self):
        self.assertIsNotNone(fetch(DAY - datetime.timedelta(days=1), lambda url: ANSWER))  # The server's day is already ahead.
        self.assertIsNone(fetch(DAY + datetime.timedelta(days=1), lambda url: ANSWER))  # Still yesterday's: try again later.

    def test_anything_else_is_no_sentence(self):
        def refused(url):
            raise OSError
        for get in (refused, lambda url: 'not json', lambda url: '[]', lambda url: '{}', lambda url: '{"date":"2026-11-01"}',
                    lambda url: '{"date":"soon","en":"Hi","zh":"好"}', lambda url: '{"date":20261101,"en":"Hi","zh":"好"}', lambda url: '{"en":"Hi","zh":"好"}',
                    lambda url: '{"date":"2026-11-01","en":"Hi"}', lambda url: '{"date":"2026-11-01","en":"Hi","zh":3}',
                    lambda url: '{"date":"2026-11-01","en":"' + 'x' * 201 + '","zh":"好"}'):
            self.assertIsNone(fetch(DAY, get))

    def test_outside_text_stays_one_plain_line(self):
        value = fetch(DAY, lambda url: '{"date":"2026-11-01","en":"Hi\\r\\n  there\\u202e\\u0007","zh":"你\\n好"}')
        self.assertEqual(value, {'en': 'Hi there', 'zh': '你 好'})


class SentenceRecordTests(unittest.TestCase):
    def make(self):
        directory = scratch(self)
        backend = MailBackend(directory, adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(backend.logout)
        return backend

    def test_built_in_until_the_feed_answers_then_kept_for_the_day(self):
        backend = self.make()
        self.assertEqual(backend.daily_line(DAY), today_line(DAY))
        backend.sentence_get = lambda url: 'not json'
        self.assertFalse(backend.fetch_sentence(DAY))
        self.assertEqual(backend.daily_line(DAY), today_line(DAY))
        backend.sentence_get = lambda url: ANSWER
        self.assertTrue(backend.fetch_sentence(DAY))
        self.assertIn('Cold air sharpens', backend.daily_line(DAY))
        self.assertEqual(backend.daily_line(DAY + datetime.timedelta(days=1)), today_line(DAY + datetime.timedelta(days=1)))

    def test_asks_at_most_every_half_hour_and_not_once_saved(self):
        backend = self.make()
        self.assertTrue(backend.sentence_due(10_000))
        self.assertFalse(backend.sentence_due(10_000 + 1799))
        self.assertTrue(backend.sentence_due(10_000 + 1800))
        backend.sentence_get = lambda url: ANSWER.replace('2026-11-01', datetime.date.today().isoformat())
        self.assertTrue(backend.fetch_sentence())
        self.assertFalse(backend.sentence_due(99_999))

    def test_no_account_open_is_built_in_and_asks_nothing(self):
        backend = self.make()
        cache, backend.cache = backend.cache, None
        self.addCleanup(setattr, backend, 'cache', cache)
        self.assertEqual(backend.daily_line(DAY), today_line(DAY))
        self.assertFalse(backend.sentence_due(10_000))
        backend.sentence_get = lambda url: ANSWER
        self.assertFalse(backend.fetch_sentence(DAY))


class TitleBarTests(unittest.TestCase):
    def test_daily_app_sets_no_display_name(self):
        # Qt appends it to what Windows shows (windowTitle() does not say so): 句子 - 鸿雁 · 工作邮箱.
        import inspect
        from coremail_desktop import bootstrap
        source = inspect.getsource(bootstrap)
        daily = source[source.index('app.setQuitOnLastWindowClosed(False)') - 200:source.index('app.setQuitOnLastWindowClosed(False)')]
        self.assertIn("app.setApplicationName('鸿雁')", daily)
        self.assertNotIn('app.setApplicationDisplayName', daily)

    def test_one_sentence_a_day_each_once_per_round(self):
        day = datetime.date(2026, 11, 1)
        self.assertEqual(today_sentence(day), today_sentence(day))
        round_ids = [today_sentence(day + datetime.timedelta(days=n))['id'] for n in range(len(ORDER))]
        self.assertEqual(sorted(round_ids), sorted(BY_ID))
        self.assertEqual(today_sentence(day + datetime.timedelta(days=len(ORDER))), today_sentence(day))

    def test_every_sentence_fits_the_title(self):
        self.assertGreaterEqual(len(SENTENCES), 60)
        self.assertEqual(len(BY_ID), len(SENTENCES))
        for s in SENTENCES:
            self.assertTrue(s['en'].isascii() and len(s['en']) <= 60 and s['zh'], s['id'])
        line = today_line(datetime.date(2026, 11, 1))
        self.assertIn('\u3000', line)
        self.assertNotIn('\n', line)

    def test_only_plain_hex_colours_reach_windows(self):
        self.assertEqual(_colorref('#dfe6ff'), 0xffe6df)
        for bad in ('', 'linear-gradient(#fff, #000)', '#fff', '#zzzzzz', None):
            self.assertIsNone(_colorref(bad))

    def test_own_colour_for_the_colour_theme_is_kept_and_checked(self):
        backend = MailBackend(scratch(self), adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(backend.logout)
        self.assertEqual(backend.prefs()['colorAccent'], '')  # The built-in violet.
        self.assertEqual(backend.set_prefs({'colorAccent': '#0EA5E9'})['colorAccent'], '#0ea5e9')
        self.assertEqual(backend.set_prefs({'colorAccent': 'red; x'})['colorAccent'], '')


if __name__ == '__main__':
    unittest.main()
