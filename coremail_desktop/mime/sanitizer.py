"""Conservative HTML subset. No CSS, external resources or active content."""
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit
import bleach

TAGS = {'p', 'br', 'div', 'span', 'b', 'strong', 'i', 'em', 'u', 's', 'blockquote',
        'pre', 'code', 'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'td', 'th',
        'hr', 'h1', 'h2', 'h3', 'h4', 'a', 'img'}


def normalize_cid(value: str) -> str:
    return unquote(value).strip().strip('<>')


def sanitize_html(source: str, cid_resources: dict) -> str:
    def attribute(tag, name, value):
        if name in {'title', 'alt'}:
            return True
        if tag == 'a' and name == 'href':
            try:
                parsed = urlsplit(value)
            except ValueError:
                return False
            return parsed.scheme.lower() in {'http', 'https'} and bool(parsed.hostname)
        if tag == 'img' and name == 'src':
            return value.lower().startswith('cid:') and normalize_cid(value[4:]) in cid_resources
        if tag in {'td', 'th'} and name in {'colspan', 'rowspan'}:
            return value.isdigit() and 0 < int(value) <= 100
        return False
    return bleach.clean(source, tags=TAGS, attributes=attribute,
                        protocols={'http', 'https', 'cid'}, strip=True, strip_comments=True)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in {'p', 'div', 'br', 'tr', 'li', 'blockquote'}:
            self.chunks.append('\n')

    def handle_data(self, data):
        self.chunks.append(data)


def html_to_text(source: str) -> str:
    parser = _TextExtractor()
    parser.feed(source)
    return ''.join(parser.chunks).strip()


# Real work mail depends on tables, inline styles and legacy presentational
# attributes. The rich reader keeps them; it renders inside a script-less
# sandboxed frame whose CSP forbids every network fetch unless the user allows
# remote images for that message, so CSS cannot load or execute anything.
RICH_TAGS = TAGS | {'font', 'center', 'small', 'big', 'sub', 'sup', 'h5', 'h6', 'tfoot', 'caption',
                    'col', 'colgroup', 'dl', 'dt', 'dd', 'address', 'cite', 'q', 'abbr', 'mark',
                    'del', 'ins', 'strike', 'tt', 'kbd', 'samp', 'var', 'figure', 'figcaption',
                    'section', 'article', 'header', 'footer', 'main', 'nav', 'aside', 'style'}
_RICH_ATTRS = {'align', 'valign', 'width', 'height', 'bgcolor', 'color', 'face', 'size', 'border',
               'cellpadding', 'cellspacing', 'dir', 'lang', 'start', 'type', 'span', 'nowrap'}
_CSS_BLOCK = ('expression', 'javascript:', 'vbscript:', 'behavior', '-moz-binding', '@import', 'url(')


def _unsafe(css):
    # CSS escapes (\75rl, \u) can spell blocked tokens; decode them before matching.
    import re
    decoded = re.sub(r'\\([0-9a-fA-F]{1,6})\s?', lambda m: chr(int(m.group(1), 16)) if int(m.group(1), 16) < 0x110000 else '', css)
    lowered = decoded.casefold().replace(chr(92), '')
    return any(token in lowered for token in _CSS_BLOCK)


class _CssFilter:
    def sanitize_css(self, style):
        if _unsafe(style):
            style = ';'.join(d for d in style.split(';') if not _unsafe(d))
        return style.replace('position:fixed', '').replace('position: fixed', '')


def _clean_style_blocks(html):
    import re
    def scrub(match):
        body = match.group(2)
        if _unsafe(body):
            body = '}'.join(rule for rule in body.split('}') if not _unsafe(rule))
        return match.group(1) + body + match.group(3)
    return re.sub(r'(<style[^>]*>)(.*?)(</style>)', scrub, html, flags=re.S | re.I)


def sanitize_rich_html(source: str, cid_resources: dict) -> str:
    def attribute(tag, name, value):
        if name in {'title', 'alt', 'style', 'class'} or name in _RICH_ATTRS:
            return True
        if tag == 'a' and name == 'href':
            try:
                parsed = urlsplit(value)
            except ValueError:
                return False
            return (parsed.scheme.lower() in {'http', 'https'} and bool(parsed.hostname)) or parsed.scheme.lower() == 'mailto'
        if tag == 'img' and name == 'src':
            low = value.strip().lower()
            if low.startswith('cid:'):
                return normalize_cid(value.strip()[4:]) in cid_resources
            return low.startswith(('https://', 'http://'))
        if tag in {'td', 'th'} and name in {'colspan', 'rowspan'}:
            return value.isdigit() and 0 < int(value) <= 100
        return False
    cleaned = bleach.clean(source, tags=RICH_TAGS, attributes=attribute, css_sanitizer=_CssFilter(),
                           protocols={'http', 'https', 'cid', 'mailto'}, strip=True, strip_comments=True)
    return _clean_style_blocks(cleaned)
