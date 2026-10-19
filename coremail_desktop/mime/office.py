"""Office Open XML (.xlsx / .docx / .pptx) read for the in-app attachment viewer.

Standard library only: a package is a zip of XML parts. Nothing in it is executed
(macro parts of .xlsm / .docm are never opened), external relationships are ignored,
and every size is bounded, so a hostile file can at worst be refused (OfficeError).
The HTML built here escapes all document text; callers still pass it through the
mail-body sanitizer and show it in the same script-less, network-less frame."""
import datetime
import html
import io
import posixpath
import re
import zipfile
from xml.etree import ElementTree

MAX_PARTS = 5000
MAX_PART_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 160 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024    # All pictures of one document together.
MAX_HTML_CHARS = 4 * 1024 * 1024      # Text beyond this is cut off (truncated=True).
SHEET_ROWS, SHEET_COLS, SHEET_CELLS = 2000, 100, 200_000
IMAGE_MAGIC = ((b'\x89PNG\r\n\x1a\n', 'image/png'), (b'\xff\xd8\xff', 'image/jpeg'), (b'GIF8', 'image/gif'), (b'BM', 'image/bmp'))


class OfficeError(ValueError):
    pass


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def _a(el, name, default=None):
    """An attribute by local name: plain (spreadsheet) or namespaced (w:val)."""
    if el is None:
        return default
    if name in el.attrib:
        return el.attrib[name]
    for key, value in el.attrib.items():
        if key.endswith('}' + name):
            return value
    return default


def _rid(el, name='id'):
    """A relationship id (r:id / r:embed): only the namespaced attribute counts."""
    for key, value in el.attrib.items():
        if key.startswith('{') and key.endswith('}' + name):
            return value
    return None


def _kids(el, name):
    return [c for c in el if _local(c.tag) == name]


def _kid(el, name):
    if el is None:
        return None
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _on(el):
    """A WordprocessingML toggle (<w:b/>, <w:b w:val="0"/>)."""
    return el is not None and str(_a(el, 'val', 'true')).lower() not in ('0', 'false', 'none', 'off')


def _image_type(data):
    head = bytes(data[:16])
    for magic, kind in IMAGE_MAGIC:
        if head.startswith(magic):
            return kind
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    return None


class _Package:
    def __init__(self, data):
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(bytes(data)))
            infos = self.zip.infolist()
        except (zipfile.BadZipFile, OSError, ValueError, EOFError) as error:
            raise OfficeError('not a package') from error  # Also password-protected (OLE) files.
        if len(infos) > MAX_PARTS:
            raise OfficeError('too many parts')
        self.parts = {i.filename.lstrip('/'): i for i in infos}
        self.budget = MAX_TOTAL_BYTES

    def read(self, name, optional=False):
        info = self.parts.get(name)
        if info is None:
            if optional:
                return None
            raise OfficeError('missing part')
        if info.file_size > MAX_PART_BYTES or info.file_size > self.budget:
            raise OfficeError('part too large')
        self.budget -= info.file_size
        try:
            return self.zip.read(info)
        except (zipfile.BadZipFile, OSError, ValueError, EOFError, NotImplementedError, RuntimeError) as error:
            raise OfficeError('damaged part') from error

    def xml(self, name, optional=False):
        data = self.read(name, optional)
        if data is None:
            return None
        try:
            return ElementTree.fromstring(data)
        except ElementTree.ParseError as error:
            raise OfficeError('damaged XML') from error

    def rels(self, part):
        """{rId: (target part, type)} of internal relationships; external ones are ignored."""
        base = posixpath.dirname(part)
        root = self.xml(posixpath.join(base, '_rels', posixpath.basename(part) + '.rels'), optional=True)
        found = {}
        for rel in (root if root is not None else ()):
            if str(rel.get('TargetMode', '')).lower() == 'external' or not rel.get('Id'):
                continue
            target = str(rel.get('Target', ''))
            target = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(base, target))
            found[rel.get('Id')] = (target, str(rel.get('Type', '')).rsplit('/', 1)[-1])
        return found

    def main_part(self, default):
        for target, kind in self.rels('').values():
            if kind == 'officeDocument':
                return target
        return default

    def image(self, target, budget):
        """(media type, bytes) of a picture part, or None (unknown format / over budget)."""
        info = self.parts.get(target)
        if info is None or info.file_size > budget[0]:
            return None
        data = self.read(target)
        kind = _image_type(data)
        if kind:
            budget[0] -= len(data)
        return (kind, data) if kind else None


def _external_links(pkg, part):
    root = pkg.xml(posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels'), optional=True)
    return {rel.get('Id'): rel.get('Target') for rel in (root if root is not None else ())
            if str(rel.get('TargetMode', '')).lower() == 'external' and re.match(r'(?i)https?://', str(rel.get('Target', '')))}


# --------------------------------------------------------------------------- xlsx
_DATE_IDS = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))
_BUILTIN_FORMATS = {1: '0', 2: '0.00', 3: '#,##0', 4: '#,##0.00', 9: '0%', 10: '0.00%', 11: '0.00E+00',
                    37: '#,##0', 38: '#,##0', 39: '#,##0.00', 40: '#,##0.00', 48: '##0.0E+0', 49: '@'}


def _column_number(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def _column_letters(n):
    out = ''
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _cell_ref(ref):
    match = re.fullmatch(r'\$?([A-Z]{1,3})\$?(\d{1,7})', str(ref or '').upper())
    return (int(match.group(2)), _column_number(match.group(1))) if match else None


def _format_number(text, code, is_date, date1904):
    try:
        x = float(text)
    except (TypeError, ValueError):
        return text
    if x != x or x in (float('inf'), float('-inf')):
        return text
    if is_date:
        try:
            base = datetime.datetime(1904, 1, 1) if date1904 else datetime.datetime(1899, 12, 30)
            moment = base + datetime.timedelta(seconds=round(x * 86400))
        except (OverflowError, ValueError):
            return text
        plain = re.sub(r'"[^"]*"|\[(?![hms]\])[^\]]*\]|\\.', '', (code or '').split(';')[0]).lower()
        if plain:  # m is minutes next to h or s, otherwise the month.
            has_time = bool(re.search(r'[hs]', plain))
            has_date = bool(re.search(r'[yd]', plain)) or ('m' in plain and not has_time)
        else:  # A built-in date / time format: decide by the value.
            has_date, has_time = x >= 1, x % 1 != 0
        if has_date and has_time:
            return moment.strftime('%Y-%m-%d %H:%M')
        if has_time:
            return moment.strftime('%H:%M:%S' if 's' in plain else '%H:%M')
        return moment.strftime('%Y-%m-%d')
    section = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.|_.|\*.', '', (code or '').split(';')[0])
    if not section or section.lower() == 'general' or section == '@':
        if x.is_integer() and abs(x) < 1e15:
            return str(int(x))
        return f'{x:.10g}'
    decimals = len(re.search(r'\.([0#?]*)', section).group(1)) if '.' in section else 0
    if 'E+' in section.upper():
        return f'{x:.{decimals}E}'
    if '%' in section:
        return f'{x * 100:.{decimals}f}%'
    grouped = ',' in section.split('.')[0]
    value = f'{x:,.{decimals}f}' if grouped else f'{x:.{decimals}f}'
    currency = ''.join(ch for ch in (code or '').split(';')[0] if ch in '¥$€£￥')
    return currency[:1] + value


def _shared_strings(root):
    strings = []
    for si in (root if root is not None else ()):
        if _local(si.tag) != 'si':
            continue
        parts = []
        for child in si:
            name = _local(child.tag)
            if name == 't':
                parts.append(child.text or '')
            elif name == 'r':
                parts.extend(t.text or '' for t in child if _local(t.tag) == 't')
        strings.append(''.join(parts))
    return strings


def _number_formats(root):
    """Per cell style index: (format code, is a date)."""
    if root is None:
        return []
    custom = {}
    for fmts in _kids(root, 'numFmts'):
        for f in _kids(fmts, 'numFmt'):
            try:
                custom[int(f.get('numFmtId'))] = f.get('formatCode') or ''
            except (TypeError, ValueError):
                continue
    styles = []
    for xfs in _kids(root, 'cellXfs'):
        for xf in _kids(xfs, 'xf'):
            try:
                fid = int(xf.get('numFmtId') or 0)
            except ValueError:
                fid = 0
            code = custom.get(fid, _BUILTIN_FORMATS.get(fid, ''))
            plain = re.sub(r'"[^"]*"|\[(?![hms]\])[^\]]*\]|\\.', '', code).lower()
            is_date = fid in _DATE_IDS or (fid in custom and bool(re.search(r'[ymdhs]', plain)) and 'general' not in plain)
            styles.append((code, is_date))
    return styles


def _read_sheet(pkg, part, strings, styles, date1904, budget):
    """{(row, col): (text, numeric)}, merged ranges, column widths, used size, truncated."""
    info = pkg.parts.get(part)
    if info is None:
        raise OfficeError('missing sheet')
    if info.file_size > MAX_PART_BYTES or info.file_size > pkg.budget:
        raise OfficeError('sheet too large')
    pkg.budget -= info.file_size
    cells, merges, widths, truncated = {}, [], {}, False
    max_row = max_col = row_no = last_col = 0
    try:
        stream = pkg.zip.open(info)
        for event, el in ElementTree.iterparse(stream, events=('start', 'end')):
            name = _local(el.tag)
            if event == 'start':
                if name == 'row':
                    try:
                        row_no = int(el.get('r') or row_no + 1)
                    except ValueError:
                        row_no += 1
                    last_col = 0
                    if row_no > SHEET_ROWS:
                        truncated = True
                        break
                continue
            if name == 'c':
                ref = _cell_ref(el.get('r'))
                row, col = ref if ref else (max(row_no, 1), last_col + 1)
                last_col = col
                kind, value = el.get('t', 'n'), _kid(el, 'v')
                text, numeric = None, False
                if kind == 'inlineStr':
                    text = ''.join(t.text or '' for t in el.iter() if _local(t.tag) == 't')
                elif value is not None and value.text is not None:
                    raw = value.text
                    if kind == 's':
                        try:
                            text = strings[int(raw)]
                        except (ValueError, IndexError):
                            text = ''
                    elif kind == 'b':
                        text = 'TRUE' if raw.strip() == '1' else 'FALSE'
                    elif kind in ('str', 'e'):
                        text = raw
                    else:
                        try:
                            code, is_date = styles[int(el.get('s') or 0)]
                        except (ValueError, IndexError):
                            code, is_date = '', False
                        text, numeric = _format_number(raw, code, is_date, date1904), True
                if text not in (None, '') and row <= SHEET_ROWS and col <= SHEET_COLS:
                    cells[(row, col)] = (text, numeric)
                    max_row, max_col = max(max_row, row), max(max_col, col)
                elif text not in (None, ''):
                    truncated = True
                el.clear()
            elif name == 'row':
                el.clear()
                if len(cells) >= min(SHEET_CELLS, budget[0]):
                    truncated = True
                    break
            elif name == 'col':
                try:
                    low, high, width = int(el.get('min')), int(el.get('max')), float(el.get('width') or 0)
                except (TypeError, ValueError):
                    continue
                for c in range(low, min(high, SHEET_COLS) + 1):
                    widths[c] = max(24, min(int(width * 7 + 5), 600))
            elif name == 'mergeCell':
                bounds = str(el.get('ref') or '').split(':')
                first, last = _cell_ref(bounds[0]), _cell_ref(bounds[-1])
                if first and last and last[0] >= first[0] and last[1] >= first[1]:
                    merges.append((first, last))
    except ElementTree.ParseError as error:
        if not cells:
            raise OfficeError('damaged sheet') from error
        truncated = True
    except (zipfile.BadZipFile, OSError, ValueError, EOFError, NotImplementedError, RuntimeError) as error:
        raise OfficeError('damaged sheet') from error
    for (first, last) in merges:
        if first[0] <= SHEET_ROWS and first[1] <= SHEET_COLS:
            max_row, max_col = max(max_row, min(last[0], SHEET_ROWS)), max(max_col, min(last[1], SHEET_COLS))
    budget[0] -= len(cells)
    return cells, merges, widths, max_row, max_col, truncated


def _sheet_table(cells, merges, widths, rows, cols):
    span, covered = {}, set()
    for (r1, c1), (r2, c2) in merges:
        r2, c2 = min(r2, rows), min(c2, cols)
        if r1 > rows or c1 > cols:
            continue
        span[(r1, c1)] = (r2 - r1 + 1, c2 - c1 + 1)
        covered.update((r, c) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1) if (r, c) != (r1, c1))
    out = ['<table class="xl"><colgroup><col class="xh">']
    out += [f'<col style="width:{widths.get(c, 72)}px">' for c in range(1, cols + 1)]
    out.append('</colgroup><thead><tr><th class="xc"></th>')
    out += [f'<th>{_column_letters(c)}</th>' for c in range(1, cols + 1)]
    out.append('</tr></thead><tbody>')
    for r in range(1, rows + 1):
        out.append(f'<tr><th>{r}</th>')
        for c in range(1, cols + 1):
            if (r, c) in covered:
                continue
            text, numeric = cells.get((r, c), ('', False))
            attrs = ' class="n"' if numeric else ''
            if (r, c) in span:
                rs, cs = span[(r, c)]
                attrs += (f' rowspan="{rs}"' if rs > 1 else '') + (f' colspan="{cs}"' if cs > 1 else '')
            out.append(f'<td{attrs}>{html.escape(text)}</td>')
        out.append('</tr>')
    out.append('</tbody></table>')
    return ''.join(out)


def xlsx_sheets(data):
    """[{'name', 'html', 'rows', 'cols', 'truncated'}] for each visible sheet, in order."""
    pkg = _Package(data)
    book_part = pkg.main_part('xl/workbook.xml')
    book = pkg.xml(book_part)
    rels = pkg.rels(book_part)
    date1904 = any(str(pr.get('date1904', '')).lower() in ('1', 'true') for pr in book.iter() if _local(pr.tag) == 'workbookPr')
    strings, styles = [], []
    for target, kind in rels.values():
        if kind == 'sharedStrings':
            strings = _shared_strings(pkg.xml(target, optional=True))
        elif kind == 'styles':
            styles = _number_formats(pkg.xml(target, optional=True))
    sheets, budget = [], [SHEET_CELLS]
    for sheet in (s for s in book.iter() if _local(s.tag) == 'sheet'):
        if str(sheet.get('state', 'visible')) != 'visible':
            continue
        name = str(sheet.get('name') or f'Sheet{len(sheets) + 1}')
        target, kind = rels.get(_rid(sheet), (None, ''))
        if kind != 'worksheet' or not target:
            sheets.append({'name': name, 'html': '', 'rows': 0, 'cols': 0, 'truncated': False, 'note': 'chart'})
            continue
        if budget[0] <= 0:
            sheets.append({'name': name, 'html': '', 'rows': 0, 'cols': 0, 'truncated': True})
            continue
        cells, merges, widths, rows, cols, truncated = _read_sheet(pkg, target, strings, styles, date1904, budget)
        sheets.append({'name': name, 'html': _sheet_table(cells, merges, widths, rows, cols) if rows else '',
                       'rows': rows, 'cols': cols, 'truncated': truncated})
    if not sheets:
        raise OfficeError('no sheets')
    return sheets


# --------------------------------------------------------------------------- docx
_HIGHLIGHT = {'yellow': '#ffff00', 'green': '#00ff00', 'cyan': '#00ffff', 'magenta': '#ff00ff', 'blue': '#0000ff',
              'red': '#ff0000', 'darkBlue': '#000080', 'darkCyan': '#008080', 'darkGreen': '#008000',
              'darkMagenta': '#800080', 'darkRed': '#800000', 'darkYellow': '#808000', 'darkGray': '#808080',
              'lightGray': '#c0c0c0', 'black': '#000000', 'white': '#ffffff'}
_CN_DIGITS = '零一二三四五六七八九'


def _chinese(n):
    if n <= 0 or n >= 100:
        return str(n)
    tens, ones = divmod(n, 10)
    return (('' if tens == 1 else _CN_DIGITS[tens]) + '十' if tens else '') + (_CN_DIGITS[ones] if ones or not tens else '')


def _roman(n):
    out = ''
    for value, letters in ((1000, 'm'), (900, 'cm'), (500, 'd'), (400, 'cd'), (100, 'c'), (90, 'xc'), (50, 'l'),
                           (40, 'xl'), (10, 'x'), (9, 'ix'), (5, 'v'), (4, 'iv'), (1, 'i')):
        while n >= value:
            out, n = out + letters, n - value
    return out or str(n)


def _counter(n, fmt):
    if fmt in ('lowerLetter', 'upperLetter'):
        text = _column_letters(max(1, n)).lower()
        return text.upper() if fmt == 'upperLetter' else text
    if fmt in ('lowerRoman', 'upperRoman'):
        return _roman(n).upper() if fmt == 'upperRoman' else _roman(n)
    if fmt in ('chineseCounting', 'chineseCountingThousand', 'ideographTraditional', 'japaneseCounting', 'taiwaneseCounting',
               'ideographDigital', 'chineseLegalSimplified'):
        return _chinese(n)
    if fmt == 'decimalEnclosedCircle' and 1 <= n <= 20:
        return chr(0x2460 + n - 1)
    return str(n)


def _style(el):
    return _a(_kid(el, 'pStyle') if _local(el.tag) == 'pPr' else _kid(el, 'rStyle'), 'val')


class _Word:
    def __init__(self, pkg):
        self.pkg = pkg
        self.part = pkg.main_part('word/document.xml')
        self.rels = pkg.rels(self.part)
        self.links = _external_links(pkg, self.part)
        self.styles, self.numbering, self.counters = {}, {}, {}
        self.images, self.budget, self.size, self.truncated = {}, [MAX_IMAGE_BYTES], 0, False
        for target, kind in self.rels.values():
            if kind == 'styles':
                self._load_styles(pkg.xml(target, optional=True))
            elif kind == 'numbering':
                self._load_numbering(pkg.xml(target, optional=True))

    def _load_styles(self, root):
        for st in (root if root is not None else ()):
            if _local(st.tag) != 'style':
                continue
            name = str(_a(_kid(st, 'name'), 'val', '')).lower()
            level = None
            match = re.fullmatch(r'heading ([1-6])', name)
            if match:
                level = int(match.group(1))
            elif name == 'title':
                level = 1
            elif name == 'subtitle':
                level = 2
            outline = _a(_kid(_kid(st, 'pPr'), 'outlineLvl'), 'val')
            if level is None and outline is not None and str(outline).isdigit() and int(outline) < 6:
                level = int(outline) + 1
            self.styles[_a(st, 'styleId')] = (level, _a(_kid(st, 'basedOn'), 'val'))

    def _heading(self, style_id):
        for _ in range(8):
            if style_id not in self.styles:
                return None
            level, base = self.styles[style_id]
            if level:
                return level
            style_id = base
        return None

    def _load_numbering(self, root):
        abstract, nums = {}, {}
        for el in (root if root is not None else ()):
            name = _local(el.tag)
            if name == 'abstractNum':
                levels = {}
                for lvl in _kids(el, 'lvl'):
                    start = _a(_kid(lvl, 'start'), 'val', '1')
                    levels[str(_a(lvl, 'ilvl', '0'))] = (str(_a(_kid(lvl, 'numFmt'), 'val', 'decimal')),
                                                         str(_a(_kid(lvl, 'lvlText'), 'val', '')),
                                                         int(start) if str(start).isdigit() else 1)
                abstract[_a(el, 'abstractNumId')] = levels
            elif name == 'num':
                nums[_a(el, 'numId')] = _a(_kid(el, 'abstractNumId'), 'val')
        self.numbering = {num: abstract.get(ab, {}) for num, ab in nums.items()}

    def _marker(self, num_id, ilvl):
        levels = self.numbering.get(num_id)
        if not levels:
            return '•'
        fmt, text, start = levels.get(str(ilvl), ('bullet', '', 1))
        if fmt == 'bullet' or fmt == 'none':
            return '' if fmt == 'none' else '•'
        counts = self.counters.setdefault(num_id, {})
        counts[ilvl] = counts.get(ilvl, start - 1) + 1
        for deeper in [k for k in counts if k > ilvl]:
            del counts[deeper]

        def level(match):
            n = int(match.group(1)) - 1
            lf = levels.get(str(n), ('decimal', '', 1))
            return _counter(counts.get(n, lf[2]), lf[0] if n != ilvl else fmt)
        return re.sub(r'%([1-9])', level, text) if text else _counter(counts[ilvl], fmt) + '.'

    def _image(self, rid, cx=None):
        target = self.rels.get(rid, (None, ''))[0]
        found = self.pkg.image(target, self.budget) if target else None
        if not found:
            return ''
        cid = f'img{len(self.images) + 1}@office'
        self.images[cid] = found
        width = f' width="{max(8, min(int(cx) // 9525, 1600))}"' if cx and str(cx).isdigit() else ''
        return f'<img src="cid:{cid}"{width} alt="">'

    def _run(self, r):
        pr, styles = _kid(r, 'rPr'), []
        text = []
        for child in r:
            name = _local(child.tag)
            if name == 't':
                text.append(html.escape(child.text or ''))
            elif name == 'tab':
                text.append(' ')
            elif name in ('br', 'cr'):
                text.append('<hr>' if _a(child, 'type') == 'page' else '<br>')
            elif name in ('drawing', 'pict', 'object'):
                cx = None
                for el in child.iter():
                    tag = _local(el.tag)
                    if tag == 'extent':
                        cx = _a(el, 'cx')
                    elif tag == 'blip' and _rid(el, 'embed'):
                        text.append(self._image(_rid(el, 'embed'), cx))
                    elif tag == 'imagedata' and _rid(el, 'id'):
                        text.append(self._image(_rid(el, 'id')))
            elif name == 'sym':
                char = str(_a(child, 'char', ''))
                if re.fullmatch(r'[0-9A-Fa-f]{4}', char) and int(char, 16) < 0xF000:
                    text.append(html.escape(chr(int(char, 16))))
        body = ''.join(text)
        if not body or pr is None:
            return body
        if _on(_kid(pr, 'b')):
            body = f'<b>{body}</b>'
        if _on(_kid(pr, 'i')):
            body = f'<i>{body}</i>'
        underline = _kid(pr, 'u')
        if underline is not None and str(_a(underline, 'val', 'single')) != 'none':
            body = f'<u>{body}</u>'
        if _on(_kid(pr, 'strike')) or _on(_kid(pr, 'dstrike')):
            body = f'<s>{body}</s>'
        align = _a(_kid(pr, 'vertAlign'), 'val')
        if align in ('superscript', 'subscript'):
            body = f'<{align[:3]}>{body}</{align[:3]}>'
        color = str(_a(_kid(pr, 'color'), 'val', ''))
        if re.fullmatch(r'[0-9A-Fa-f]{6}', color) and color.upper() != '000000':
            styles.append(f'color:#{color}')
        size = str(_a(_kid(pr, 'sz'), 'val', ''))
        if size.isdigit() and 8 <= int(size) <= 400 and int(size) != 21 and int(size) != 22:
            styles.append(f'font-size:{int(size) / 2:g}pt')
        mark = _HIGHLIGHT.get(str(_a(_kid(pr, 'highlight'), 'val', '')))
        shade = str(_a(_kid(pr, 'shd'), 'fill', ''))
        if mark or re.fullmatch(r'[0-9A-Fa-f]{6}', shade) and shade.upper() != 'FFFFFF':
            styles.append(f'background:{mark or "#" + shade}')
        return f'<span style="{";".join(styles)}">{body}</span>' if styles else body

    def _inline(self, el):
        out = []
        for child in el:
            name = _local(child.tag)
            if name == 'r':
                out.append(self._run(child))
            elif name == 'hyperlink':
                inner = self._inline(child)
                url = self.links.get(_rid(child))
                out.append(f'<a href="{html.escape(url, quote=True)}">{inner}</a>' if url and inner else inner)
            elif name in ('ins', 'smartTag', 'fldSimple', 'customXml', 'bdo', 'dir'):
                out.append(self._inline(child))
            elif name == 'sdt':
                out.append(self._inline(_kid(child, 'sdtContent') if _kid(child, 'sdtContent') is not None else child))
        return ''.join(out)

    def paragraph(self, p):
        pr = _kid(p, 'pPr')
        body = self._inline(p)
        level = self._heading(_style(pr)) if pr is not None else None
        styles = []
        jc = str(_a(_kid(pr, 'jc'), 'val', ''))
        if jc in ('center', 'right', 'end', 'both', 'distribute'):
            styles.append('text-align:' + {'end': 'right', 'both': 'justify', 'distribute': 'justify'}.get(jc, jc))
        num = _kid(pr, 'numPr')
        if num is not None and _a(_kid(num, 'numId'), 'val') not in (None, '0'):
            ilvl = int(_a(_kid(num, 'ilvl'), 'val', '0')) if str(_a(_kid(num, 'ilvl'), 'val', '0')).isdigit() else 0
            marker = self._marker(_a(_kid(num, 'numId'), 'val'), min(ilvl, 8))
            styles.append(f'margin-left:{1.6 * (ilvl + 1):g}em')
            body = (html.escape(marker) + ' ' if marker else '') + body
        style = f' style="{";".join(styles)}"' if styles else ''
        tag = f'h{level}' if level and not num else 'p'
        return f'<{tag}{style}>{body or "<br>"}</{tag}>'

    def table(self, tbl):
        rows = []
        for tr in _kids(tbl, 'tr'):
            row, col = [], 0
            for tc in _kids(tr, 'tc'):
                pr = _kid(tc, 'tcPr')
                span = _a(_kid(pr, 'gridSpan'), 'val', '1')
                span = max(1, min(int(span), 100)) if str(span).isdigit() else 1
                merge = _kid(pr, 'vMerge')
                state = None if merge is None else ('start' if _a(merge, 'val') == 'restart' else 'continue')
                row.append({'col': col, 'span': span, 'state': state, 'el': tc, 'rows': 1})
                col += span
            rows.append(row)
        for r, row in enumerate(rows):
            for cell in row:
                if cell['state'] != 'continue':
                    continue
                for above in range(r - 1, -1, -1):
                    origin = next((c for c in rows[above] if c['col'] == cell['col']), None)
                    if origin is None or origin['state'] != 'continue':
                        if origin is not None:
                            origin['rows'] += 1
                        break
        out = ['<table class="wt">']
        for row in rows:
            out.append('<tr>')
            for cell in row:
                if cell['state'] == 'continue':
                    continue
                attrs = (f' colspan="{cell["span"]}"' if cell['span'] > 1 else '') + (f' rowspan="{cell["rows"]}"' if cell['rows'] > 1 else '')
                out.append(f'<td{attrs}>{self.blocks(cell["el"])}</td>')
            out.append('</tr>')
        out.append('</table>')
        return ''.join(out)

    def blocks(self, el):
        out = []
        for child in el:
            if self.size > MAX_HTML_CHARS:
                self.truncated = True
                break
            name = _local(child.tag)
            if name == 'p':
                out.append(self.paragraph(child))
            elif name == 'tbl':
                out.append(self.table(child))
            elif name in ('sdt', 'customXml'):
                content = _kid(child, 'sdtContent')
                out.append(self.blocks(content if content is not None else child))
            else:
                continue
            self.size += len(out[-1])
        return ''.join(out)


def docx_html(data):
    """(html, {cid: (media type, bytes)}, truncated) for a Word document."""
    pkg = _Package(data)
    word = _Word(pkg)
    root = pkg.xml(word.part)
    body = _kid(root, 'body')
    if body is None:
        raise OfficeError('no body')
    return word.blocks(body), word.images, word.truncated


# --------------------------------------------------------------------------- pptx
def _slide_paragraphs(body):
    out = []
    for p in _kids(body, 'p'):
        parts = []
        for child in p:
            name = _local(child.tag)
            if name in ('r', 'fld'):
                parts.append(html.escape(''.join(t.text or '' for t in child if _local(t.tag) == 't')))
            elif name == 'br':
                parts.append('<br>')
        text = ''.join(parts)
        if text.strip():
            lvl = str(_a(_kid(p, 'pPr'), 'lvl', '0'))
            indent = f' style="margin-left:{1.5 * int(lvl):g}em"' if lvl.isdigit() and int(lvl) else ''
            out.append(f'<p{indent}>{text}</p>')
    return ''.join(out)


def _slide_shapes(tree, rels, pkg, images, budget, title):
    out = []
    for shape in tree:
        name = _local(shape.tag)
        if name == 'sp':
            ph = next((el for el in shape.iter() if _local(el.tag) == 'ph'), None)
            body = _kid(shape, 'txBody')
            if body is None:
                continue
            if ph is not None and _a(ph, 'type') in ('title', 'ctrTitle') and not title:
                title.append(''.join(html.escape(t.text or '') for t in body.iter() if _local(t.tag) == 't'))
                continue
            out.append(_slide_paragraphs(body))
        elif name == 'grpSp':
            out.append(_slide_shapes(shape, rels, pkg, images, budget, title))
        elif name == 'graphicFrame':
            tbl = next((el for el in shape.iter() if _local(el.tag) == 'tbl'), None)
            if tbl is None:
                continue
            rows = []
            for tr in _kids(tbl, 'tr'):
                cells = []
                for tc in _kids(tr, 'tc'):
                    if _a(tc, 'hMerge') or _a(tc, 'vMerge'):
                        continue
                    body = _kid(tc, 'txBody')
                    span = ''.join(f' {k}="{v}"' for k, v in (('colspan', _a(tc, 'gridSpan')), ('rowspan', _a(tc, 'rowSpan')))
                                   if v and str(v).isdigit() and 1 < int(v) <= 100)
                    cells.append(f'<td{span}>{_slide_paragraphs(body) if body is not None else ""}</td>')
                rows.append('<tr>' + ''.join(cells) + '</tr>')
            out.append('<table class="wt">' + ''.join(rows) + '</table>')
        elif name == 'pic':
            blip = next((el for el in shape.iter() if _local(el.tag) == 'blip'), None)
            target = rels.get(_rid(blip, 'embed'), (None, ''))[0] if blip is not None else None
            found = pkg.image(target, budget) if target else None
            if found:
                cid = f'img{len(images) + 1}@office'
                images[cid] = found
                out.append(f'<p><img src="cid:{cid}" alt=""></p>')
    return ''.join(out)


def pptx_html(data):
    """(html, {cid: (media type, bytes)}, truncated): each slide's title, text, tables and pictures."""
    pkg = _Package(data)
    part = pkg.main_part('ppt/presentation.xml')
    root, rels = pkg.xml(part), pkg.rels(part)
    order = [rels.get(_rid(s), (None, ''))[0] for s in root.iter() if _local(s.tag) == 'sldId']
    images, budget, out, size, truncated = {}, [MAX_IMAGE_BYTES], [], 0, False
    for n, target in enumerate((t for t in order if t), 1):
        if size > MAX_HTML_CHARS:
            truncated = True
            break
        slide = pkg.xml(target)
        tree = next((el for el in slide.iter() if _local(el.tag) == 'spTree'), None)
        title = []
        body = _slide_shapes(tree, pkg.rels(target), pkg, images, budget, title) if tree is not None else ''
        heading = f'<h2>{title[0]}</h2>' if title and title[0].strip() else ''
        out.append(f'<section class="slide"><div class="sn">{n}</div>{heading}{body or ("" if heading else "<p class=\"empty\">（这一页没有文字）</p>")}</section>')
        size += len(out[-1])
    if not out:
        raise OfficeError('no slides')
    return ''.join(out), images, truncated


# --------------------------------------------------------------------------- svg
_SVG_DROP = {'script', 'foreignObject', 'iframe', 'object', 'embed', 'audio', 'video', 'handler', 'listener'}


def sanitize_svg(data):
    """An SVG without scripts, event handlers or references outside itself (bytes),
    shown only as an <img> (which never runs script or loads anything)."""
    try:
        root = ElementTree.fromstring(bytes(data))
    except ElementTree.ParseError as error:
        raise OfficeError('damaged SVG') from error
    if _local(root.tag) != 'svg':
        raise OfficeError('not SVG')
    for parent in list(root.iter()):
        for child in list(parent):
            if _local(child.tag) in _SVG_DROP:
                parent.remove(child)
        for key in list(parent.attrib):
            name, value = _local(key).lower(), str(parent.attrib[key]).strip()
            if name.startswith('on'):
                del parent.attrib[key]
            elif name == 'href' and not (value.startswith('#') or re.match(r'(?i)data:image/(png|jpeg|gif|webp);base64,', value)):
                del parent.attrib[key]
            elif 'url(' in value.lower() and re.search(r'(?i)url\(\s*["\']?(?!#)', value):
                del parent.attrib[key]
    for style in [el for el in root.iter() if _local(el.tag) == 'style']:
        if re.search(r'(?i)@import|url\(\s*["\']?(?!#)', style.text or ''):
            style.text = ''
    ElementTree.register_namespace('', 'http://www.w3.org/2000/svg')
    ElementTree.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
    return ElementTree.tostring(root, encoding='utf-8')
