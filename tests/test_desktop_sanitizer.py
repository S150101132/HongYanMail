import unittest
from coremail_desktop.mime.sanitizer import sanitize_html


class SanitizerTests(unittest.TestCase):
    def test_malformed_link_is_removed_without_losing_body(self):
        result = sanitize_html('<a href="http://[broken">visible</a>', {})
        self.assertIn('visible', result)
        self.assertNotIn('href=', result)

    def test_script_event_and_css_urls_cannot_escape_reader(self):
        result = sanitize_html('<script>steal()</script><iframe src="file:///x"></iframe><form><input></form><p onclick="steal()" style="background:url(https://evil)">safe</p>', {})
        for forbidden in ('<script', '<iframe', '<form', '<input', 'onclick', 'style=', 'https://evil'):
            self.assertNotIn(forbidden, result)
        self.assertIn('safe', result)

    def test_only_owned_cid_images_and_http_links_survive(self):
        result = sanitize_html('<img src="https://evil/a"><img src="file:///a"><img src="data:image/png;base64,AA"><img src="cid:ok"><img src="cid:missing"><a href="javascript:steal()">bad</a><a href="https://example.com">good</a>', {'ok': b'a'})
        self.assertIn('src="cid:ok"', result)
        self.assertIn('href="https://example.com"', result)
        for forbidden in ('https://evil', 'file:', 'data:', 'cid:missing', 'javascript:'):
            self.assertNotIn(forbidden, result)

