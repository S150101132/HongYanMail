"""T2 表格: the 12 acceptance samples pasted into a real Qt WebEngine compose editor with the real
table scripts (tools/table_preview.TablePage). Content and merges never change; structure and
column kinds are found; meaningful colours stay; the sent HTML suits Outlook; results are stable;
撤销整理, the user's own choices, learning and stale AI answers behave."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QTWEBENGINE_DISABLE_SANDBOX', '1')

import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

import qt_test_support  # noqa: F401  (Qt teardown)
from tools.table_preview import FIXTURES, TablePage, paste

EXPECTED = json.loads((FIXTURES / 'expected.json').read_text(encoding='utf-8'))
RGB = lambda hexa: 'rgb(%d, %d, %d)' % tuple(int(hexa[i:i + 2], 16) for i in (1, 3, 5))


class Cells(HTMLParser):
    """Each td / th of the sent HTML: its attributes."""
    def __init__(self, html):
        super().__init__()
        self.cells, self.tables = [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag in ('td', 'th'):
            self.cells.append(dict(attrs))
        if tag == 'table':
            self.tables.append(dict(attrs))


class SamplesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = TablePage()
        cls.results = {name: paste(cls.page, name) for name in EXPECTED}

    def test_content_and_merges_never_change(self):
        for name, r in self.results.items():
            self.assertEqual(r['before'], r['after'], name)

    def test_structure_and_column_kinds(self):
        for name, r in self.results.items():
            self.assertEqual(r['roles'], EXPECTED[name]['roles'], name)
            self.assertEqual(r['types'], EXPECTED[name]['types'], name)

    def test_fits_the_mail_or_scrolls(self):
        for name, r in self.results.items():
            if EXPECTED[name].get('overflow'):
                self.assertTrue(r['overflow'], name)  # Kept readable, scrolls sideways in the editor.
            else:
                self.assertFalse(r['overflow'], name)
                self.assertLessEqual(r['width'], 641, name)

    def test_meaningful_colours_stay_and_spreadsheet_greys_go(self):
        html = self.results['08-colors']['mail'].lower()  # What is drawn and sent (the editor keeps the source's own record).
        for colour in EXPECTED['08-colors']['keep']:
            self.assertTrue(colour in html or RGB(colour) in html, colour)
        for colour in EXPECTED['08-colors']['drop']:
            self.assertFalse(colour in html or RGB(colour) in html, colour)
        for r in self.results.values():
            self.assertNotIn('font-family', r['html'])  # The spreadsheet's fonts go; the mail's are used.
            self.assertNotIn('mso-', r['html'])

    def test_sent_html_suits_outlook(self):
        for name, r in self.results.items():
            mail = r['mail']
            self.assertNotIn('data-hy-', mail, name)
            self.assertNotRegex(mail, r'rgba\(|min-width', name)
            parsed = Cells(mail)
            for table in parsed.tables:
                self.assertEqual((table.get('cellpadding'), table.get('cellspacing'), table.get('border')), ('0', '0', '0'), name)
            for cell in parsed.cells:
                style = cell.get('style', '')
                align = re.search(r'text-align: ?(\w+)', style)
                if align:
                    self.assertEqual(cell.get('align'), align.group(1), name)
                bg = re.search(r'background-color: ?([^;]+)', style)
                if bg:
                    self.assertTrue(cell.get('bgcolor', '').startswith('#'), name)
                if re.search(r'(?:^|;) ?width: ?\d+px', style):
                    self.assertTrue(cell.get('width', '').isdigit(), name)
        self.assertNotIn('data-hy-scroll', self.results['07-wide']['mail'])  # The editor's scroll frame stays home.
        self.assertIn('nowrap', self.results['04-mixed']['mail'])

    def test_stable_and_quick(self):
        for name, r in self.results.items():
            self.assertTrue(r['stable'], name)  # The same paste gives the same HTML.
            self.assertLess(r['ms'], 600 if name == '10-large' else 250, name)

    def test_undo_layout_brings_the_table_back_as_pasted(self):
        r = paste(self.page, '02-multihead')
        back = self.page.run('''(() => { tbUndoPaste(%s, %s); const t = $('#cmp-editor table');
          return { rows: t.rows.length, styled: !!t.querySelector('td[style]'), text: t.rows[3].cells[3].textContent }; })()''' % (json.dumps(r['id']), json.dumps(r['raw'])))
        self.assertEqual(back, {'rows': 7, 'styled': False, 'text': '3,250.00'})

    def test_undo_layout_never_drops_later_edits(self):
        r = paste(self.page, '02-multihead')
        out = self.page.run('''(() => { const t = $('#cmp-editor table'); toasts.length = 0;
          t.rows[3].cells[1].textContent = '赵六';
          const ok = tbUndoPaste(%s, %s), now = $('#cmp-editor table');
          return { ok, kept: now.rows[3].cells[1].textContent, laidOut: !!now.dataset.hyLook, told: toasts.length }; })()''' % (json.dumps(r['id']), json.dumps(r['raw'])))
        self.assertEqual(out, {'ok': False, 'kept': '赵六', 'laidOut': True, 'told': 1})

    def test_filled_cells_get_dark_text_and_pictures_keep_their_size(self):
        out = self.page.run('''(() => {
          const ed = $('#cmp-editor');
          const p = tbPaste('<table><tr><td style="background:#ffeb9c">待处理</td><td><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" style="width:40px;height:20px"></td></tr>' +
            '<tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>');
          ed.innerHTML = p.html;
          const t = ed.querySelector('table'), img = t.querySelector('img');
          return { color: t.rows[0].cells[0].style.color, img: [img.style.width, img.style.height] };
        })()''')
        self.assertEqual(out['color'], 'rgb(29, 41, 57)')
        self.assertEqual(out['img'], ['40px', '20px'])

    def test_the_users_choices_stay_and_teach_once(self):
        out = self.page.run('''(() => {
          const ed = $('#cmp-editor'); toasts.length = 0;
          ed.innerHTML = '<table><tr><td>项目</td><td>日期</td></tr><tr><td>a</td><td>2026/10/1</td></tr><tr><td>b</td><td>2026/10/2</td></tr></table>' +
            '<table><tr><td>项目</td><td>日期</td></tr><tr><td>c</td><td>2026/11/1</td></tr><tr><td>d</td><td>2026/11/2</td></tr></table>';
          const [a, b] = $$('#cmp-editor table');
          tbSet(a, 'align', a.rows[1].cells[1], 'left');
          tbSet(a, 'role', a.rows[2].cells[0], 'total');
          tbFormat(a);
          const kept = [a.rows[1].cells[1].style.textAlign, a.rows[0].cells[1].style.textAlign, a.rows[2].dataset.hyRow];
          const offersAfterOne = toasts.length;
          tbSet(b, 'align', b.rows[1].cells[1], 'left');
          const offer = toasts.map((t) => t.text);
          tbClearManual(a);
          return { kept, offersAfterOne, offer, cleared: [a.rows[1].cells[1].style.textAlign, a.rows[2].dataset.hyRow] };
        })()''')
        self.assertEqual(out['kept'], ['left', 'left', 'total'])  # The column's header follows the user's alignment.
        self.assertEqual(out['offersAfterOne'], 0)
        self.assertEqual(out['offer'], ['以后粘贴的表格都用日期靠左？'])  # The same choice on a second table offers, once.
        self.assertEqual(out['cleared'], ['center', 'data'])

    def test_a_marked_header_cell_keeps_its_colours(self):
        out = self.page.run('''(() => {
          const ed = $('#cmp-editor');
          ed.innerHTML = '<table><tr><td data-hy-src="b">项目</td><td data-hy-src="b;bg=#ffeb9c;fg=#c00000">金额</td></tr><tr><td>a</td><td>1.00</td></tr><tr><td>b</td><td>2.00</td></tr></table>';
          const t = ed.querySelector('table'); tbFormat(t);
          const c = (el) => [getComputedStyle(el).backgroundColor, getComputedStyle(el).color];
          return { plain: c(t.rows[0].cells[0]), marked: c(t.rows[0].cells[1]) };
        })()''')
        self.assertEqual(out['plain'], ['rgb(242, 244, 247)', 'rgb(29, 41, 57)'])  # The theme's header.
        self.assertEqual(out['marked'], ['rgb(255, 235, 156)', 'rgb(192, 0, 0)'])  # The user's yellow and red.

    def test_the_ai_is_asked_about_the_same_summary_after_answering(self):
        out = self.page.run('''(() => {
          const ed = $('#cmp-editor');
          ed.innerHTML = '<table><tr><td>项目</td><td>金额</td></tr><tr><td>a</td><td>1.00</td></tr><tr><td>b</td><td>2.00</td></tr><tr><td>c</td><td>3.00</td></tr></table>';
          const t = ed.querySelector('table'), before = JSON.stringify(tbAiPayload(t));
          tbApplyAi(t, { rows: [{ i: 3, role: 'total', conf: 0.95 }], cols: [{ i: 1, type: 'money', conf: 0.95 }], emphasis: [] });
          return { same: JSON.stringify(tbAiPayload(t)) === before, applied: t.rows[3].dataset.hyRow };
        })()''')
        self.assertEqual(out, {'same': True, 'applied': 'total'})  # So the backend's cache answers the second click.

    def test_drag_borders_to_size_columns_and_rows(self):
        out = self.page.run('''(() => {
          const ed = $('#cmp-editor');
          ed.innerHTML = '<table><tr><td>项目</td><td>金额</td><td>说明</td></tr><tr><td>住宿</td><td>1,800.00</td><td>客户现场附近</td></tr><tr><td>交通</td><td>3,250.00</td><td>往返机票</td></tr></table>';
          const t = ed.querySelector('table'); tbFormat(t);
          const fire = (target, type, x, y) => target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 }));
          const width = (c) => Math.round(t.rows[1].cells[c].getBoundingClientRect().width);
          const before = [0, 1, 2].map(width);
          // Column 0: grab its right border and drag 60px to the right.
          let cell = t.rows[1].cells[0], r = cell.getBoundingClientRect();
          fire(cell, 'mousedown', r.right - 1, r.top + 5); fire(document, 'mousemove', r.right + 59, r.top + 5); fire(document, 'mouseup', r.right + 59, r.top + 5);
          const dragged = [0, 1, 2].map(width), stored = t.rows[1].cells[0].dataset.hyWpx;
          // Row 1: grab its bottom border and drag 30px down.
          cell = t.rows[1].cells[1]; r = cell.getBoundingClientRect();
          const h0 = Math.round(t.rows[1].getBoundingClientRect().height);
          fire(cell, 'mousedown', r.left + 10, r.bottom - 1); fire(document, 'mousemove', r.left + 10, r.bottom + 29); fire(document, 'mouseup', r.left + 10, r.bottom + 29);
          const h1 = Math.round(t.rows[1].getBoundingClientRect().height);
          tbFormat(t);  // Kept through another layout.
          const kept = [width(0), Math.round(t.rows[1].getBoundingClientRect().height)];
          const mail = tbMailHtml(ed.innerHTML);
          // Double-click the column border: automatic again.
          cell = t.rows[1].cells[0]; r = cell.getBoundingClientRect();
          fire(cell, 'dblclick', r.right - 1, r.top + 5);
          return { before, dragged, stored, h0, h1, kept, mailWidth: /<td[^>]*width="\\d+"/.test(mail), mailHeight: /<td[^>]*height="\\d+"/.test(mail),
            mailClean: !/data-hy-/.test(mail), reset: [width(0), t.rows[1].cells[0].dataset.hyWpx || ''] };
        })()''')
        self.assertAlmostEqual(out['dragged'][0], out['before'][0] + 60, delta=2)
        self.assertEqual(out['dragged'][1:], out['before'][1:])  # The other columns do not move.
        self.assertEqual(out['stored'], str(out['dragged'][0]))
        self.assertAlmostEqual(out['h1'], out['h0'] + 30, delta=2)
        self.assertAlmostEqual(out['kept'][0], out['dragged'][0], delta=1)
        self.assertAlmostEqual(out['kept'][1], out['h1'], delta=1)
        self.assertTrue(out['mailWidth'] and out['mailHeight'] and out['mailClean'])
        self.assertEqual(out['reset'], [out['before'][0], ''])

    def test_ai_answer_is_dropped_when_the_table_changed(self):
        out = self.page.run('''(() => {
          const ed = $('#cmp-editor');
          ed.innerHTML = '<table><tr><td>项目</td><td>金额</td></tr><tr><td>a</td><td>1.00</td></tr><tr><td>b</td><td>2.00</td></tr></table>';
          const t = ed.querySelector('table');
          tbApplyAi(t, { rows: [{ i: 2, role: 'total', conf: 0.95 }], cols: [], emphasis: [1], theme: 'report' });
          const applied = [t.rows[2].dataset.hyRow, t.dataset.hyLook];
          t.rows[1].cells[0].textContent = 'changed';
          tbFormat(t);
          return { applied, after: [t.rows[2].dataset.hyRow, t.dataset.hyLook] };
        })()''')
        self.assertEqual(out['applied'], ['total', 'report'])
        self.assertEqual(out['after'], ['data', 'business'])  # Made for other content: no longer used.


class WiringTest(unittest.TestCase):
    WEB = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'

    def test_page_loads_the_table_scripts_and_sends_through_the_mail_layer(self):
        index = (self.WEB / 'index.html').read_text(encoding='utf-8')
        order = [index.index(f'table/{n}.js') for n in ('model', 'types', 'structure', 'layout', 'source', 'render', 'mail', 'controller', 'resize')]
        self.assertEqual(order, sorted(order))
        self.assertIn('return tbMailHtml(html)', (self.WEB / 'mention.js').read_text(encoding='utf-8'))
        app = (self.WEB / 'app.js').read_text(encoding='utf-8')
        self.assertIn("const pasted = tbPaste(e.clipboardData.getData('text/html'));", app)
        self.assertIn("label: '撤销整理'", app)
        self.assertNotIn('function inlinePastedStyles', app)  # One way in for pasted tables.
        self.assertIn('box-sizing: content-box', (self.WEB / 'style.css').read_text(encoding='utf-8'))

    def test_table_menu(self):
        comp = (self.WEB / 'compose.js').read_text(encoding='utf-8')
        menu = comp[comp.index('function tableMenu('):comp.index('function tablePicker(')]
        for label in ('本列对齐', '本列宽度', '本行是', '表头对齐', '底色', '原表颜色', '表格样式', '清除我的调整', 'AI 优化这张表', '让 AI 重新考虑这张表'):
            self.assertIn(label, menu)
        self.assertIn("tbSet(t, kind, target, value)", menu)
