"""Tab / Shift+Tab 缩进: the rich editors and the long writing boxes are wired (behaviour was
checked in a browser; it needs a real selection)."""
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'


class WiringTest(unittest.TestCase):
    def test_rich_editors(self):
        js = (WEB / 'compose.js').read_text(encoding='utf-8')
        self.assertIn("wireTabIndent($('#cmp-editor'));", js)
        self.assertIn("wireTabIndent($('#pref-signature'));", js)
        # The table handler comes first and claims Tab inside a cell.
        self.assertLess(js.index('const cell = currentCell(); if (!cell) return;'), js.index('function wireTabIndent'))
        # Lines move by margin, not Chromium's indent <blockquote> (shown as a quote by mail clients).
        self.assertIn("b.style.marginLeft", js)

    def test_long_text_boxes_only(self):
        js = (WEB / 'app.js').read_text(encoding='utf-8')
        areas = js[js.index("const TAB_AREAS = '"):].split('\n', 1)[0]
        for box in ('#ai-note', '#ar-text', 'data-f="history"'):
            self.assertIn(box, areas)
        for box in ('#ask-input', '#me-text', '#tk-answer'):
            self.assertNotIn(box, areas)
