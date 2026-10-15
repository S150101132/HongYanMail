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
