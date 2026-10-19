"""v0.6 attachments: in-app viewing of HTML, Office, more image and text types (B6), and
打开 handing files to the system off the window's thread (B2)."""
import base64
import io
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from coremail_desktop.app import demo_office
from coremail_desktop.app.backend import MailBackend, preview_kind, safe_file_name
from coremail_desktop.app.demo import DemoAdapter, _demo_chart
from coremail_desktop.errors import AppError
from coremail_desktop.mime import office
from coremail_desktop.mime.sanitizer import sanitize_rich_html
from tests.test_app_backend import XorProtector, local_services_stub


def _zip(parts):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return out.getvalue()


class OfficeReaderTest(unittest.TestCase):
    def test_workbook_sheets(self):
        sheets = office.xlsx_sheets(demo_office.demo_xlsx())
        self.assertEqual([s['name'] for s in sheets], ['需求', '汇总'])  # The hidden sheet is left out.
        first = sheets[0]
        self.assertEqual((first['rows'], first['cols'], first['truncated']), (6, 6, False))
        for text in ('colspan="6">支持需求清单', '2026-10-20', '32.50', '80%', '100%', '50.75', '<th>F</th>'):
            self.assertIn(text, first['html'])
        self.assertIn('TRUE', sheets[1]['html'])
        self.assertIn('#DIV/0!', sheets[1]['html'])
        self.assertIn('进行中', sheets[1]['html'])  # Inline strings.

    def test_big_sheets_are_cut(self):
        from tools.measure_attachment_open import big_workbook
        sheet = office.xlsx_sheets(big_workbook(rows=office.SHEET_ROWS + 5, cols=3))[0]
        self.assertEqual((sheet['rows'], sheet['cols'], sheet['truncated']), (office.SHEET_ROWS, 3, True))
        self.assertIn(f'<th>{office.SHEET_ROWS}</th>', sheet['html'])

    def test_number_formats(self):
        f = office._format_number
        self.assertEqual(f('45123.5', 'yyyy/m/d h:mm', True, False), '2023-07-16 12:00')
        self.assertEqual(f('0.75', 'h:mm', True, False), '18:00')
        self.assertEqual(f('1', 'yyyy-mm-dd', True, True), '1904-01-02')
        self.assertEqual(f('1234567.891', '#,##0.00', False, False), '1,234,567.89')
        self.assertEqual(f('0.30000000000000004', '', False, False), '0.3')
        self.assertEqual(f('12', '"¥"#,##0', False, False), '¥12')
        self.assertEqual(f('abc', '', False, False), 'abc')

    def test_sheet_text_is_escaped(self):
        S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
        book = _zip({'xl/workbook.xml': f'<workbook xmlns="{S}" xmlns:r="r"><sheets><sheet name="a" r:id="rId1"/></sheets></workbook>',
                     'xl/_rels/workbook.xml.rels': '<Relationships><Relationship Id="rId1" Type="x/worksheet" Target="s.xml"/></Relationships>',
                     'xl/s.xml': f'<worksheet xmlns="{S}"><sheetData><row><c t="inlineStr"><is><t>&lt;script&gt;x&lt;/script&gt;</t></is></c>'
                                 '<c t="str"><v>&lt;b&gt;</v></c></row></sheetData></worksheet>'})
        html = office.xlsx_sheets(book)[0]['html']
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('<th>B</th>', html)  # Cells without references fill in order.

    def test_word_document(self):
        html, images, truncated = office.docx_html(demo_office.demo_docx(_demo_chart(200, 125)))
        self.assertFalse(truncated)
        for text in ('<h1>设计评审纪要', '<h2>待办事项', '1. 完成服务器迁移', '3. 整理邮件归档', '<b>2026 年 10 月 20 日</b>',
                     'color:#C00000', 'rowspan="2"', 'colspan="3"', 'href="https://example.invalid/project"', '<sub>2</sub>', 'text-align:center'):
            self.assertIn(text, html)
        self.assertEqual(len(images), 1)
        self.assertEqual(list(images.values())[0][0], 'image/png')

    def test_slides(self):
        html, _images, _ = office.pptx_html(demo_office.demo_pptx())
        self.assertEqual(html.count('<section class="slide">'), 2)
        self.assertLess(html.index('鸿雁项目周报'), html.index('本周进展'))
        self.assertIn('<h2>本周进展</h2>', html)
        self.assertIn('margin-left:1.5em', html)
        self.assertIn('<td><p>联调</p></td>', html)

    def test_bad_packages_are_refused(self):
        for data in (b'PK\x03\x04 broken', b'\xd0\xcf\x11\xe0 encrypted or legacy', _zip({'other.xml': '<x/>'}),
                     _zip({'word/document.xml': '<w:document'})):
            for reader in (office.xlsx_sheets, office.docx_html, office.pptx_html):
                with self.assertRaises(office.OfficeError):
                    reader(data)
        package = office._Package(_zip({'big.xml': 'x' * 100}))
        package.budget = 50  # Every part counts against one budget for the whole file.
        with self.assertRaises(office.OfficeError):
            package.read('big.xml')

    def test_svg_is_sanitised(self):
        svg = office.sanitize_svg(demo_office.demo_svg()).decode('utf-8')
        for bad in ('script', 'onload', 'alert', 'example.invalid'):
            self.assertNotIn(bad, svg)
        self.assertIn('<rect', svg)
        with self.assertRaises(office.OfficeError):
            office.sanitize_svg(b'<html>not svg</html>')

    def test_html_attachments_lose_scripts(self):
        html = sanitize_rich_html(demo_office.demo_html().decode('utf-8'), {}, data_images=True)
        for bad in ('alert', '<script', 'onload', '<iframe', '<title', '周报</'):
            self.assertNotIn(bad, html)
        self.assertIn('<h1>项目周报', html)
        self.assertIn('<img', sanitize_rich_html('<img src="data:image/png;base64,AAAA">', {}, data_images=True))
        self.assertNotIn('data:', sanitize_rich_html('<img src="data:image/png;base64,AAAA">', {}))  # Mail bodies: cid: only.
        self.assertNotIn('data:text', sanitize_rich_html('<img src="data:text/html;base64,AAAA">', {}, data_images=True))


class BackendViewerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.backend = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        self.opened = []
        self.backend.open_file = self.opened.append
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'x')
        self.backend.folders()
        self.backend.messages('1')
        self.parts = {a['name']: a['part'] for a in self.backend.open_message('demo1')['attachments']}

    def tearDown(self):
        self.backend.logout()
        shutil.rmtree(self.directory, ignore_errors=True)

    def preview(self, name):
        return self.backend.attachment_preview('demo1', self.parts[name])

    def code(self, call, *args):
        with self.assertRaises(AppError) as caught:
            call(*args)
        return caught.exception.code

    def test_kinds(self):
        sheet = self.preview('支持需求清单.xlsx')
        self.assertEqual((sheet['kind'], len(sheet['sheets'])), ('sheet', 2))
        word = self.preview('评审纪要.docx')
        self.assertEqual((word['kind'], word['doc']), ('html', 'docx'))
        self.assertIn('src="data:image/png;base64,', word['html'])  # Pictures inline, nothing fetched.
        self.assertNotIn('cid:', word['html'])
        slides = self.preview('项目周报.pptx')
        self.assertEqual((slides['kind'], slides['doc']), ('html', 'pptx'))
        page = self.preview('周报.html')
        self.assertEqual((page['kind'], page['doc'], page['hasRemoteImages']), ('html', 'html', True))
        self.assertNotIn('alert', page['html'])
        svg = self.preview('标志.svg')
        self.assertTrue(svg['data'].startswith('data:image/svg+xml;base64,'))
        self.assertNotIn(b'script', base64.b64decode(svg['data'].split(',', 1)[1]))
        self.assertTrue(self.preview('截图.bmp')['data'].startswith('data:image/bmp;base64,'))
        self.assertEqual(self.code(self.preview, '旧版报表.xls'), 'PREVIEW_UNAVAILABLE')  # Legacy formats open externally.

    def test_kind_detection(self):
        self.assertEqual(preview_kind('a.svg', b'<svg/>'), 'image/svg+xml')
        self.assertEqual(preview_kind('a.HTM', b'<p>'), 'html')
        self.assertEqual(preview_kind('a.py', b'print(1)'), 'text')
        self.assertEqual(preview_kind('a.xlsx', b'not a zip'), None)
        self.assertEqual(preview_kind('a.docx', b'PK\x03\x04'), 'docx')
        self.assertEqual(preview_kind('a.zip', b'PK\x03\x04'), None)
        self.assertEqual(preview_kind('a.html', b'<p>\x00'), None)
        self.assertEqual(preview_kind('x', b'II*\x00rest'), 'image/tiff')
        self.assertEqual(preview_kind('x', demo_office.demo_bmp()), 'image/bmp')

    def test_tiff_needs_the_converter(self):
        self.backend.attachment = lambda mid, part: ('scan.tif', 'image/tiff', b'II*\x00' + b'\x00' * 32)
        self.assertEqual(self.code(self.backend.attachment_preview, 'm', 'p'), 'PREVIEW_UNAVAILABLE')
        self.backend.convert_image = lambda data: b'\x89PNG converted'
        self.assertEqual(self.backend.attachment_preview('m', 'p')['data'], 'data:image/png;base64,' + base64.b64encode(b'\x89PNG converted').decode())

    def test_open_writes_a_copy_and_hands_it_over(self):
        self.assertTrue(self.backend.open_attachment('demo1', self.parts['支持需求清单.xlsx']))
        target = Path(self.opened[-1])
        self.assertEqual(target.name, '支持需求清单.xlsx')
        self.assertEqual(target.parent.parent, Path(self.directory) / 'opened')
        self.assertEqual(target.read_bytes(), demo_office.demo_xlsx())
        self.backend.attachment = lambda mid, part: ('run me.EXE', 'application/octet-stream', b'MZ')
        self.assertEqual(self.code(self.backend.open_attachment, 'm', 'p'), 'ATTACHMENT_BLOCKED')
        self.backend.attachment = lambda mid, part: ('a.js.', 'text/plain', b'x')
        self.assertEqual(self.code(self.backend.open_attachment, 'm', 'p'), 'ATTACHMENT_BLOCKED')
        self.backend.attachment = lambda mid, part: ('季度:报表?, "终版".html', 'text/html', b'<p>x</p>')
        self.backend.open_attachment('m', 'p')
        self.assertEqual(Path(self.opened[-1]).name, '季度_报表_， _终版_.html')
        self.assertEqual(len(self.opened), 2)

    def test_safe_file_names(self):
        self.assertEqual(safe_file_name('CON.txt'), '_CON.txt')
        self.assertEqual(safe_file_name('a/b\\c.docx'), 'a_b_c.docx')
        self.assertEqual(safe_file_name('...'), '附件')
        self.assertTrue(safe_file_name('长' * 300 + '.xlsx').endswith('.xlsx'))
        self.assertLessEqual(len(safe_file_name('长' * 300 + '.xlsx')), 121)


class WebAndShellTest(unittest.TestCase):
    WEB = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'

    def test_open_runs_on_a_worker(self):
        from coremail_desktop.app.shell import AppShell, BACKEND_METHODS, FAST_METHODS
        self.assertEqual(BACKEND_METHODS['openAttachment'], 'open_attachment')
        self.assertIn('openAttachment', FAST_METHODS)  # Never behind a list fetch, never on the window's thread.
        self.assertFalse(hasattr(AppShell, 'native_openAttachment'))
        from coremail_desktop.ui.messages import MESSAGES
        self.assertIn('ATTACHMENT_BLOCKED', MESSAGES)

    def test_viewer_frame_rules(self):
        script = (self.WEB / 'manage.js').read_text(encoding='utf-8')
        self.assertIn('sandbox="allow-same-origin"', script)
        self.assertNotIn('allow-scripts', script)
        self.assertIn("default-src 'none'", script)
        for name in ('xlsx', 'docx', 'pptx', 'html', 'svg', 'bmp', 'py'):
            self.assertIn(f' {name} ', ' ' + script.split('const PV_EXT = (')[1].split(').split')[0].replace("'", ' ').replace('+', ' ') + ' ')

    def test_tiff_converter(self):
        import tests.qt_test_support  # noqa: F401 - releases Qt windows at exit
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage
        from coremail_desktop.app.shell import _png_from_image
        image = QImage(4, 3, QImage.Format.Format_RGB32)
        image.fill(0x2563EB)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.assertTrue(image.save(buffer, 'TIFF') or image.save(buffer, 'BMP'))
        self.assertTrue(_png_from_image(bytes(buffer.data())).startswith(b'\x89PNG'))
        self.assertIsNone(_png_from_image(b'not an image'))


if __name__ == '__main__':
    unittest.main()
