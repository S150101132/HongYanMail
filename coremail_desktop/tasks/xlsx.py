"""Workbooks read and written with the standard library (no openpyxl).

read() gives every cell as (kind, value, style) plus the sheet layout that matters for the
task workbooks (column widths, frozen pane, conditional formats, validation lists, table
name and style); write() builds a workbook from rows and such a layout. Output is
deterministic: the same rows give the same bytes (fixed part order and timestamps), so an
unchanged task list leaves git with nothing to commit.
"""
import io
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

from coremail_desktop.errors import AppError

M = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
P = 'http://schemas.openxmlformats.org/package/2006/relationships'
MAX_BYTES = 50 * 1024 * 1024
MAX_ROWS = 10000
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_m = lambda tag: f'{{{M}}}{tag}'


def col_name(index):
    """0 -> 'A'."""
    name = ''
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def col_index(ref):
    found = re.match(r'[A-Z]+', ref or '')
    if not found:
        return None
    value = 0
    for ch in found.group():
        value = value * 26 + ord(ch) - 64
    return value - 1


# Reading ----------------------------------------------------------------
def _part(zf, name):
    try:
        return zf.read(name)
    except KeyError:
        return None


def _rels(zf, source):
    """{rId: target part} of a part (targets resolved against the part's folder)."""
    folder, base = posixpath.split(source)
    data = _part(zf, posixpath.join(folder, '_rels', base + '.rels'))
    found = {}
    if data:
        for rel in ET.fromstring(data):
            target = rel.get('Target') or ''
            found[rel.get('Id')] = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(folder, target))
    return found


def _string(node):
    """Text of an <si> or <is>: plain <t>, or the <t> of each rich-text run (not phonetic hints)."""
    parts = []
    for child in node:
        if child.tag == _m('t'):
            parts.append(child.text or '')
        elif child.tag == _m('r'):
            t = child.find(_m('t'))
            parts.append(t.text or '' if t is not None else '')
    return ''.join(parts)


def read(data):
    """The workbook in data (bytes): {'date1904', 'styles', 'theme', 'sheets': [...]}.
    Each sheet: {'name', 'rows': {r: {'attrs', 'cells': {col: (kind, value, style)}}}, 'layout'}.
    kind: 's' text, 'n' number (raw text), 'd' ISO date, 'b' boolean, 'e' error."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        if sum(i.file_size for i in zf.infolist()) > MAX_BYTES:
            raise AppError('TASK_IMPORT_INVALID')
        book = ET.fromstring(zf.read('xl/workbook.xml'))
        rels = _rels(zf, 'xl/workbook.xml')
        shared = []
        sst = next((t for t in rels.values() if t.endswith('sharedStrings.xml')), None)
        if sst and _part(zf, sst):
            shared = [_string(si) for si in ET.fromstring(zf.read(sst)).iter(_m('si'))]
        pr = book.find(_m('workbookPr'))
        styles = next((t for t in rels.values() if t.endswith('styles.xml')), None)
        theme = next((t for t in rels.values() if 'theme' in t), None)
        result = {'date1904': pr is not None and pr.get('date1904') in ('1', 'true'),
                  'styles': _part(zf, styles) if styles else None, 'theme': _part(zf, theme) if theme else None, 'sheets': []}
        for sheet in book.iter(_m('sheet')):
            path = rels.get(sheet.get(f'{{{R}}}id'))
            if not path or _part(zf, path) is None:
                raise AppError('TASK_IMPORT_LAYOUT')
            result['sheets'].append(_sheet(zf, path, sheet.get('name') or '', shared))
        return result
    except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError, IndexError):
        raise AppError('TASK_IMPORT_INVALID') from None


def _sheet(zf, path, name, shared):
    root = ET.fromstring(zf.read(path))
    rows, count = {}, 0
    data = root.find(_m('sheetData'))
    last_row = 0
    for row in (data if data is not None else []):
        if row.tag != _m('row'):
            continue
        count += 1
        if count > MAX_ROWS:
            raise AppError('TASK_IMPORT_INVALID')
        r = int(row.get('r') or last_row + 1)
        last_row = r
        cells, last_col = {}, -1
        for c in row:
            if c.tag != _m('c'):
                continue
            col = col_index(c.get('r')) if c.get('r') else last_col + 1
            last_col = col
            kind, v = c.get('t') or 'n', c.find(_m('v'))
            value = v.text if v is not None else None
            if kind == 's':
                value = shared[int(value)] if value is not None else None
            elif kind == 'inlineStr':
                node = c.find(_m('is'))
                kind, value = 's', _string(node) if node is not None else None
            elif kind == 'str':
                kind, value = 's', value
            elif kind not in ('n', 'd', 'b', 'e'):
                raise AppError('TASK_IMPORT_INVALID')
            if value not in (None, ''):
                cells[col] = (kind, value, c.get('s') or '0')
            else:
                cells[col] = ('', '', c.get('s') or '0')  # Styled but empty.
        rows[r] = {'attrs': {k: v for k, v in row.attrib.items() if k in ('ht', 'customHeight')}, 'cells': cells}
    return {'name': name, 'rows': rows, 'layout': _layout(zf, path, root)}


def _layout(zf, path, root):
    view = root.find(f'{_m("sheetViews")}/{_m("sheetView")}')
    pane = view.find(_m('pane')) if view is not None else None
    fmt = root.find(_m('sheetFormatPr'))
    margins = root.find(_m('pageMargins'))
    layout = {
        'cols': [dict(c.attrib) for c in root.iter(_m('col'))],
        'view': {k: v for k, v in (view.attrib.items() if view is not None else []) if k in ('showGridLines', 'zoomScale')},
        'pane': dict(pane.attrib) if pane is not None else None,
        'rowHeight': fmt.get('defaultRowHeight') if fmt is not None else '15',
        'cf': [{'col': col_name(col_index(cf.get('sqref'))), 'rules': [dict(rule.attrib) for rule in cf.iter(_m('cfRule'))]}
               for cf in root.iter(_m('conditionalFormatting'))],
        'dv': [{'col': col_name(col_index(dv.get('sqref'))), 'type': dv.get('type') or 'list',
                'formula': dv.findtext(_m('formula1')) or ''} for dv in root.iter(_m('dataValidation'))],
        'margins': dict(margins.attrib) if margins is not None else None,
        'table': None,
    }
    rels = _rels(zf, path)
    for part in root.iter(_m('tablePart')):
        target = rels.get(part.get(f'{{{R}}}id'))
        if target and _part(zf, target):
            table = ET.fromstring(zf.read(target))
            info = table.find(_m('tableStyleInfo'))
            layout['table'] = {'name': table.get('name'), 'displayName': table.get('displayName'), 'ref': table.get('ref'),
                               'columns': [c.get('name') for c in table.iter(_m('tableColumn'))],
                               'filter': table.find(_m('autoFilter')) is not None,
                               'style': dict(info.attrib) if info is not None else None}
            break
    return layout


# Writing ----------------------------------------------------------------
def _x(value):
    return (value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('\r', '&#13;'))


def _a(value):
    return _x(str(value)).replace('"', '&quot;').replace('\n', '&#10;').replace('\t', '&#9;')


def _attrs(attrs):
    return ''.join(f' {k}="{_a(v)}"' for k, v in attrs.items())


def _cell(ref, kind, value, style):
    s = f' s="{style}"' if style and style != '0' else ''
    if kind == 's':
        space = ' xml:space="preserve"' if value != value.strip() or '\n' in value else ''
        return f'<c r="{ref}"{s} t="inlineStr"><is><t{space}>{_x(value)}</t></is></c>'
    if kind in ('n', 'b', 'e', 'd'):
        t = '' if kind == 'n' else f' t="{kind}"'
        return f'<c r="{ref}"{s}{t}><v>{_x(value)}</v></c>'
    return f'<c r="{ref}"{s}/>'


def _row(r, attrs, cells):
    """cells: {col: (kind, value, style)}."""
    body = ''.join(_cell(f'{col_name(c)}{r}', *cells[c]) for c in sorted(cells))
    return f'<row r="{r}"{_attrs(attrs)}>{body}</row>'


def _worksheet(sheet, table_rid):
    """sheet: {'layout', 'rows': {r: {'attrs', 'cells'}}, 'first': first data row, 'last': last data row}."""
    layout, first, last = sheet['layout'], sheet['first'], sheet['last']
    width = max([len(layout['table']['columns'])] if layout.get('table') else [1])
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n',
           f'<worksheet xmlns="{M}" xmlns:r="{R}">',
           f'<dimension ref="A1:{col_name(width - 1)}{max(last, max(sheet["rows"], default=1))}"/>']
    pane = f'<pane{_attrs(layout["pane"])}/>' if layout.get('pane') else ''
    out.append(f'<sheetViews><sheetView{_attrs(layout.get("view") or {})} workbookViewId="0">{pane}</sheetView></sheetViews>')
    out.append(f'<sheetFormatPr defaultRowHeight="{_a(layout.get("rowHeight") or "15")}"/>')
    if layout.get('cols'):
        out.append('<cols>' + ''.join(f'<col{_attrs(c)}/>' for c in layout['cols']) + '</cols>')
    out.append('<sheetData>' + ''.join(_row(r, sheet['rows'][r]['attrs'], sheet['rows'][r]['cells']) for r in sorted(sheet['rows'])) + '</sheetData>')
    for cf in layout.get('cf') or []:
        ref = f'{cf["col"]}{first}:{cf["col"]}{last}'
        rules = ''
        for rule in cf['rules']:
            formula = ''
            if rule.get('type') == 'containsText' and rule.get('text') is not None:
                formula = f'<formula>NOT(ISERROR(SEARCH("{_x(rule["text"])}",{cf["col"]}{first})))</formula>'
            rules += f'<cfRule{_attrs(rule)}>{formula}</cfRule>'
        out.append(f'<conditionalFormatting sqref="{ref}">{rules}</conditionalFormatting>')
    if layout.get('dv'):
        out.append(f'<dataValidations count="{len(layout["dv"])}">' + ''.join(
            f'<dataValidation type="{_a(dv["type"])}" allowBlank="1" sqref="{dv["col"]}{first}:{dv["col"]}{last}">'
            f'<formula1>{_x(dv["formula"])}</formula1></dataValidation>' for dv in layout['dv']) + '</dataValidations>')
    if layout.get('margins'):
        out.append(f'<pageMargins{_attrs(layout["margins"])}/>')
    if table_rid:
        out.append(f'<tableParts count="1"><tablePart r:id="{table_rid}"/></tableParts>')
    out.append('</worksheet>')
    return ''.join(out)


def _table(table, number, header_row, last):
    cols = table['columns']
    ref = f'A{header_row}:{col_name(len(cols) - 1)}{last}'
    columns = ''.join(f'<tableColumn id="{i + 1}" name="{_a(name)}"/>' for i, name in enumerate(cols))
    style = f'<tableStyleInfo{_attrs(table["style"])}/>' if table.get('style') else ''
    autofilter = f'<autoFilter ref="{ref}"/>' if table.get('filter') else ''
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<table xmlns="{M}" id="{number}" name="{_a(table["name"])}" displayName="{_a(table["displayName"] or table["name"])}" '
            f'ref="{ref}" headerRowCount="1" totalsRowShown="0">{autofilter}'
            f'<tableColumns count="{len(cols)}">{columns}</tableColumns>{style}</table>')


def write(sheets, styles, theme, header_row=4):
    """sheets: [{'name', 'layout', 'rows', 'first', 'last'}] (see _worksheet) -> xlsx bytes."""
    parts = {}
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
    book_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n', f'<Relationships xmlns="{P}">']
    entries = []
    table_number = 0
    for i, sheet in enumerate(sheets, 1):
        rid = None
        if sheet['layout'].get('table'):
            table_number += 1
            rid = 'rId1'
            parts[f'xl/tables/table{table_number}.xml'] = _table(sheet['layout']['table'], table_number, header_row, sheet['last'])
            parts[f'xl/worksheets/_rels/sheet{i}.xml.rels'] = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                f'<Relationships xmlns="{P}"><Relationship Id="rId1" Type="{R}/table" Target="../tables/table{table_number}.xml"/></Relationships>')
            ct.append(f'<Override PartName="/xl/tables/table{table_number}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/>')
        parts[f'xl/worksheets/sheet{i}.xml'] = _worksheet(sheet, rid)
        ct.append(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
        book_rels.append(f'<Relationship Id="rId{i}" Type="{R}/worksheet" Target="worksheets/sheet{i}.xml"/>')
        entries.append(f'<sheet name="{_a(sheet["name"])}" sheetId="{i}" r:id="rId{i}"/>')
    n = len(sheets)
    book_rels.append(f'<Relationship Id="rId{n + 1}" Type="{R}/styles" Target="styles.xml"/>')
    if theme:
        book_rels.append(f'<Relationship Id="rId{n + 2}" Type="{R}/theme" Target="theme/theme1.xml"/>')
        ct.append('<Override PartName="/xl/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>')
        parts['xl/theme/theme1.xml'] = theme
    book_rels.append('</Relationships>')
    ct.append('</Types>')
    parts['[Content_Types].xml'] = ''.join(ct)
    parts['_rels/.rels'] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                            f'<Relationships xmlns="{P}"><Relationship Id="rId1" Type="{R}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    parts['xl/workbook.xml'] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                                f'<workbook xmlns="{M}" xmlns:r="{R}"><sheets>{"".join(entries)}</sheets></workbook>')
    parts['xl/_rels/workbook.xml.rels'] = ''.join(book_rels)
    parts['xl/styles.xml'] = styles
    order = ['[Content_Types].xml', '_rels/.rels', 'xl/workbook.xml', 'xl/_rels/workbook.xml.rels', 'xl/styles.xml']
    order += sorted(k for k in parts if k not in order)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as zf:
        for name in order:
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            value = parts[name]
            zf.writestr(info, value.encode('utf-8') if isinstance(value, str) else value)
    return out.getvalue()
