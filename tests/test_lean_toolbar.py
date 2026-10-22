"""界面精简 (A4): the 精简 / 完整 preference, and every button 精简 folds away is still
reachable — from ⋯, 格式, the ▾ beside 发送, the search panel's folded fields or Ctrl+K."""
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

from coremail_desktop.app.backend import DEFAULT_PREFS, MailBackend
from coremail_desktop.app.demo import DemoAdapter
from tests.test_app_backend import XorProtector, local_services_stub
from tests.test_compose_v05 import scratch

WEB = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'


class Folded(HTMLParser):
    """Elements marked lean-hide / lean-only: their tag and attributes."""
    def __init__(self):
        super().__init__()
        self.hide, self.only = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get('class') or '').split()
        if 'lean-hide' in classes:
            self.hide.append((tag, a))
        if 'lean-only' in classes:
            self.only.append((tag, a))


def page():
    folded = Folded()
    folded.feed((WEB / 'index.html').read_text(encoding='utf-8'))
    return folded


class PrefTest(unittest.TestCase):
    def test_lean_by_default_and_checked(self):
        self.assertEqual(DEFAULT_PREFS['toolbar'], 'lean')
        backend = MailBackend(scratch(self), adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(backend.logout)
        self.assertEqual(backend.prefs()['toolbar'], 'lean')
        self.assertEqual(backend.set_prefs({'toolbar': 'full'})['toolbar'], 'full')
        self.assertEqual(backend.set_prefs({'toolbar': 'tiny'})['toolbar'], 'lean')
        self.assertEqual(backend.set_prefs({'toolbar': 1})['toolbar'], 'lean')


class ReachTest(unittest.TestCase):
    def setUp(self):
        self.folded = page()
        self.app = (WEB / 'app.js').read_text(encoding='utf-8')
        self.compose = (WEB / 'compose.js').read_text(encoding='utf-8')

    def test_reader_buttons_are_in_the_more_menu(self):
        acts = {a['data-act']: a for tag, a in self.folded.hide if 'data-act' in a}
        self.assertEqual(set(acts), {'move', 'unread', 'recall', 'status', 'unschedule', 'summarize', 'aireply', 'find', 'print'})
        in_menu = {'print': "label: '打印…'"}  # moreMenu has had these.
        for act, a in acts.items():
            self.assertTrue(a.get('data-label') or in_menu[act] in self.app, act)
        self.assertIn("foldedItems($('#reader-body .reader-toolbar'))", self.app)
        for kept in ('reply', 'reply_all', 'forward', 'delete', 'flag', 'ask', 'more'):
            self.assertNotIn(kept, acts)
        self.assertIn("ask: () => { const here = chatHere();", self.app)

    def test_composer_formats_and_send_options(self):
        hidden = [a.get('data-cmd') or a.get('id') or a.get('title') for tag, a in self.folded.hide if tag in ('button', 'select', 'label')
                  and (a.get('data-cmd') or a.get('id', '').startswith(('fmt', 'cmp')) or 'color-btn' in a.get('class', ''))]
        for item in hidden:
            if item in ('fmt-size',):
                self.assertIn("'fontSize'", self.compose)
            elif item == 'cmp-table':
                self.assertIn("$('#cmp-table').onclick", self.compose)
            elif item == 'cmp-schedule':
                self.assertIn("$('#cmp-schedule').click()", self.compose)
            elif item == 'cmp-template':
                self.assertIn('templateMenu(b)', self.compose)
            elif item in ('文字颜色', '高亮'):
                self.assertIn({'文字颜色': "$('#fmt-color').click()", '高亮': "$('#fmt-hilite').click()"}[item], self.compose)
            else:
                self.assertIn(f"'{item}'", self.compose[self.compose.index('function formatMenu'):], item)
        self.assertEqual(len(hidden), 12)  # 字号, 下划线, 删除线, 颜色, 高亮, 缩进 ×2, 分隔线, 表格, 清除格式, 定时, 模板.
        only = {a.get('id') for tag, a in self.folded.only}
        self.assertTrue({'cmp-format', 'cmp-send-more', 'sp-quick-sel'} <= only)
        for kept in ('bold', 'italic', 'insertUnorderedList', 'insertOrderedList', 'link'):
            self.assertNotIn(kept, hidden)

    def test_search_panel(self):
        contacts = (WEB / 'contacts.js').read_text(encoding='utf-8')
        self.assertIn("$('#sp-subject').onchange", contacts)
        self.assertIn("$('#sp-quick-sel').onchange", contacts)
        quick = set(re.findall(r'data-days="(\d+)"', (WEB / 'index.html').read_text(encoding='utf-8')))
        options = set(re.findall(r'<option value="(\d+)">近', (WEB / 'index.html').read_text(encoding='utf-8')))
        self.assertEqual(quick, options)  # Each quick range is in the dropdown.

    def test_switch_and_commands(self):
        self.assertIn('id="toolbar-seg"', (WEB / 'index.html').read_text(encoding='utf-8'))
        self.assertIn("toolbar: $('#toolbar-seg .on').dataset.toolbar", self.app)
        self.assertIn("classList.toggle('tb-lean'", self.app)
        commands = (WEB / 'commands.js').read_text(encoding='utf-8')
        self.assertIn('visible(b) || folded(b)', commands)  # Ctrl+K still finds the folded buttons.
        self.assertIn("bars('full', '完整工具栏'", commands)
        css = (WEB / 'style.css').read_text(encoding='utf-8')
        self.assertIn('.tb-lean .lean-hide { display: none !important; }', css)
        self.assertIn('html:not(.tb-lean) .lean-only { display: none !important; }', css)


if __name__ == '__main__':
    unittest.main()
