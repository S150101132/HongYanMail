"""粘贴 Excel / Word 表格保留样式: both paste handlers inline the clipboard's <style> rules
(behaviour was checked in a browser; it needs DOMParser and CSSOM)."""
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'


class WiringTest(unittest.TestCase):
    def test_compose_and_signature_paste(self):
        app = (WEB / 'app.js').read_text(encoding='utf-8')
        handler = app[app.index("$('#cmp-editor').addEventListener('paste'"):]
        self.assertIn("inlinePastedStyles(e.clipboardData.getData('text/html'))", handler)
        self.assertIn('function inlinePastedStyles(html)', app)
        sig = (WEB / 'signature.js').read_text(encoding='utf-8')
        self.assertIn('inlinePastedStyles(html)', sig)

    def test_rules_by_specificity_and_own_style_last(self):
        app = (WEB / 'app.js').read_text(encoding='utf-8')
        fn = app[app.index('function inlinePastedStyles'):app.index("$('#cmp-editor').addEventListener('paste'")]
        self.assertIn('.sort((a, b) => a.w - b.w || a.n - b.n)', fn)
        self.assertIn("rule + (el.getAttribute('style') || '')", fn)
        self.assertIn("removeAttribute('class')", fn)

    def test_excel_fragment_inside_the_table_keeps_the_table(self):
        # Excel puts <!--StartFragment--> after the <col>s; once parsed the cut starts with
        # </colgroup>, and taking only that left bare rows that went in as text.
        app = (WEB / 'app.js').read_text(encoding='utf-8')
        fn = app[app.index('function inlinePastedStyles'):app.index("$('#cmp-editor').addEventListener('paste'")]
        self.assertIn(r"!/^\s*<(\/|(tr|td|th|col|colgroup|tbody|thead|tfoot|caption)[\s>])/i.test(frag[1])", fn)
        self.assertIn("replace(/<!--(Start|End)Fragment-->/g, '')", fn)

    def test_excel_tables_are_tidied_with_hex_colours(self):
        app = (WEB / 'app.js').read_text(encoding='utf-8')
        fn = app[app.index('function tidyExcelTables'):app.index('function inlinePastedStyles')]
        self.assertIn("if (!ruled) c.style.border = '1px solid ' + MAIL_GRID;", fn)
        self.assertIn('!shaded', fn)  # A shaded first row only when the user shaded nothing.
        self.assertIn('tidyExcelTables(doc)', app)
        # Outlook ignores rgba: lines and fills that must show are hex, here and in Markdown tables.
        md = (WEB / 'markdown.js').read_text(encoding='utf-8')
        for text in (app[app.index('const MAIL_GRID'):app.index('function tidyExcelTables')], md[md.index('cell:'):md.index('hr:')]):
            self.assertNotIn('rgba', text)
