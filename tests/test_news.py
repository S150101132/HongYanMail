"""每日速览 (G2): 36氪's 9点1氪 read from the RSS feed, kept and refreshed. Stand-in feed; nothing leaves."""
import json
import unittest

from coremail_desktop.app import news
from coremail_desktop.app.backend import MailBackend
from coremail_desktop.errors import AppError
from tests.test_app_backend import XorProtector
from tests.test_compose_v05 import scratch


class NewsTest(unittest.TestCase):
    def test_the_newest_brief_from_the_feed(self):
        brief = news.parse_feed(news.DEMO_FEED)
        self.assertEqual((brief['url'], brief['date']), ('https://36kr.com/p/4039', '2026-10-24'))
        self.assertTrue(brief['title'].startswith('9点1氪'))  # The other article is passed over.
        self.assertEqual(brief['guide'], ['演示热点一', '演示热点二'])
        self.assertEqual([(s['title'], [i['title'] for i in s['items']]) for s in brief['sections']],
                         [('TOP3大新闻', ['演示：大模型均价上涨', '演示：示例公司发布新品']), ('AI最前沿', ['演示：某模型发布新版本'])])
        self.assertEqual(brief['sections'][0]['items'][0]['text'], '演示模式下的示例新闻正文，没有联网。\n第二段正文。')
        old = news.DEMO_FEED.replace('9点1氪', '8点1氪')
        self.assertTrue(news.parse_feed(old)['title'].startswith('8点1氪'))  # Its old name still reads.
        self.assertIsNone(news.parse_feed(news.DEMO_FEED.replace('9点1氪', '早报')))
        evil = news.DEMO_FEED.replace('https://36kr.com/p/4039', 'https://evil.example/p')
        self.assertEqual(news.parse_feed(evil)['url'], '')  # 阅读原文 only ever opens 36氪.
        with self.assertRaises(AppError):
            news.fetch(lambda url, timeout=15: (_ for _ in ()).throw(OSError()))

    def test_kept_and_looked_at_again(self):
        b = MailBackend(scratch(self), protector=XorProtector())
        gets = []
        b.news_get = lambda url, timeout=15: gets.append(url) or news.DEMO_FEED
        view = b.daily_news()
        self.assertEqual((view['brief']['date'], view['error'], gets), ('2026-10-24', '', [news.FEED_URL]))
        b.daily_news()
        self.assertEqual(len(gets), 1)  # Within 30 minutes: not again.
        b.daily_news(refresh=True)
        self.assertEqual(len(gets), 2)
        b.news_get = lambda url, timeout=15: (_ for _ in ()).throw(OSError())
        view = b.daily_news(refresh=True)  # Offline: the last issue, with a note.
        self.assertEqual(view['brief']['date'], '2026-10-24')
        self.assertIn('36氪', view['error'])
        saved = json.loads((b.data_dir / 'news' / 'latest.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['brief'], view['brief'])
        fresh = MailBackend(scratch(self), protector=XorProtector())
        fresh.news_get = b.news_get
        with self.assertRaises(AppError) as caught:
            fresh.daily_news()
        self.assertEqual(caught.exception.code, 'NEWS_UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
