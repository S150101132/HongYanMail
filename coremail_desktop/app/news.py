"""每日速览 (G2): 36氪's morning brief 「9点1氪」 (formerly 8点1氪) from its public RSS feed.

The user page (36kr.com/user/5652071) sits behind a bot check, so the brief is taken from
www.36kr.com/feed, which carries each day's issue in full. Only plain text leaves here; the
page shows it escaped, with a link to the original.
"""
import html
import re
import urllib.request

from coremail_desktop.errors import AppError

FEED_URL = 'https://www.36kr.com/feed'
TITLE_RE = re.compile(r'^\s*[89]点1氪')  # Renamed from 8点1氪 to 9点1氪 in 2026.
TEXT_CHARS, ITEMS = 1200, 60
_UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Hongyan'}


def _get(url, timeout=15):
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=timeout) as response:
        return response.read(3_000_000).decode('utf-8', 'replace')


def _text(fragment):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', fragment or ''))).strip()


def parse_brief(body):
    """The issue's HTML -> {guide: [...], sections: [{title, items: [{title, text}]}]}. A <h2>
    starts a section, a paragraph that is all <strong> starts a story, other paragraphs are
    its text; 今日热点导览's list is the guide."""
    guide, sections, story = [], [], None
    for tag, inner in re.findall(r'<(h2|li|p)\b[^>]*>(.*?)</\1>', body, re.S):
        text = _text(inner)
        if not text:
            continue
        if tag == 'h2':
            sections.append({'title': text, 'items': []})
            story = None
        elif tag == 'li':
            if not sections or '导览' in sections[-1]['title']:
                guide.append(text)
        elif re.fullmatch(r'\s*<strong>.*</strong>\s*', inner, re.S):
            if not sections:
                sections.append({'title': '', 'items': []})
            story = {'title': text, 'text': ''}
            sections[-1]['items'].append(story)
        elif story is not None and len(story['text']) < TEXT_CHARS:
            story['text'] = (story['text'] + '\n' + text).strip()[:TEXT_CHARS]
    sections = [s for s in sections if s['items']]
    count = 0
    for s in sections:
        s['items'] = s['items'][:max(0, ITEMS - count)]
        count += len(s['items'])
    return {'guide': guide[:10], 'sections': [s for s in sections if s['items']]}


def parse_feed(feed):
    """The newest 9点1氪 in the feed -> {title, url, date, guide, sections}, or None."""
    for item in re.findall(r'<item>(.*?)</item>', feed, re.S):
        title = _text(re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', (re.search(r'<title>(.*?)</title>', item, re.S) or [None, ''])[1], flags=re.S))
        if not TITLE_RE.match(title):
            continue
        link = re.search(r'<link>(?:<!\[CDATA\[)?\s*(https?://[^\s<\]]+)', item)
        date = re.search(r'<pubDate>\s*(\d{4}-\d{2}-\d{2})', item)
        body = re.search(r'<description>(?:<!\[CDATA\[(.*?)\]\]>|(.*?))</description>', item, re.S)
        url = re.sub(r'\?.*$', '', link.group(1)) if link else ''
        brief = parse_brief((body.group(1) or html.unescape(body.group(2) or '')) if body else '')
        if not brief['sections']:
            continue
        return {'title': title, 'url': url if url.startswith('https://36kr.com/') else '', 'date': date.group(1) if date else '', **brief}
    return None


def fetch(get=None):
    """The newest issue; NEWS_UNAVAILABLE when the feed cannot be read. None when the feed
    has no issue just now (it keeps about a day of articles)."""
    try:
        return parse_feed((get or _get)(FEED_URL))
    except (OSError, ValueError):
        raise AppError('NEWS_UNAVAILABLE')


# Demo mode and tests: a feed shaped like 36氪's, no network.
DEMO_FEED = '''<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>36氪</title>
<item><title>演示文章：与简报无关</title><link><![CDATA[https://36kr.com/p/1?f=rss]]></link><pubDate>2026-10-24 18:50:23  +0800</pubDate>
<description><![CDATA[<p><strong>不是简报</strong></p><p>正文</p>]]></description></item>
<item><title>9点1氪丨演示：大模型均价上涨；示例公司发布新品</title><link><![CDATA[https://36kr.com/p/4039?f=rss]]></link><pubDate>2026-10-24 09:06:50  +0800</pubDate>
<description><![CDATA[<h2>今日热点导览</h2><ul><li>演示热点一</li><li>演示热点二</li></ul>
<h2>TOP3大新闻</h2><p><strong>演示：大模型均价上涨</strong></p><p>演示模式下的示例新闻正文，没有联网。</p><p>第二段正文。</p>
<p><strong>演示：示例公司发布新品</strong></p><p>示例正文。</p>
<h2>AI最前沿</h2><p><strong>演示：某模型发布新版本</strong></p><p>示例正文。</p>
<p class="image-wrapper"><img src="https://img.example.invalid/a.jpg"></p>]]></description></item></channel></rss>'''


def demo_get(url, timeout=15):
    return DEMO_FEED
