"""Synthetic attachments for --demo and the tests: small but real .xlsx / .docx / .pptx
packages, an HTML page, an SVG and a BMP. All content is made up."""
import io
import struct
import zipfile
from xml.sax.saxutils import escape

_CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
_REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
_DOC_REL = _R + '/officeDocument'
XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def _package(parts, types, main):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', XML + f'<Types xmlns="{_CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
                   + ''.join(f'<Override PartName="/{name}" ContentType="{kind}"/>' for name, kind in types.items()) + '</Types>')
        z.writestr('_rels/.rels', XML + f'<Relationships xmlns="{_REL}"><Relationship Id="rId1" Type="{_DOC_REL}" Target="{main}"/></Relationships>')
        for name, data in parts.items():
            z.writestr(name, data)
    return out.getvalue()


def _rels(items):
    return XML + f'<Relationships xmlns="{_REL}">' + ''.join(
        f'<Relationship Id="{rid}" Type="{_R}/{kind}" Target="{target}"' + (' TargetMode="External"' if external else '') + '/>'
        for rid, kind, target, external in items) + '</Relationships>'


def demo_xlsx():
    """Two sheets: a list with numbers, dates, a percentage and a merged title; and a summary."""
    S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    strings = ['支持需求清单（合成演示数据）', '序号', '需求', '负责人', '截止日期', '工时', '完成度',
               '服务器迁移', '张晓明', '邮件归档', '李华', '证书更新', '王芳', '合计', '汇总', '项目', '状态', '进行中']
    s = {text: i for i, text in enumerate(strings)}

    def c(ref, value, style=0):
        if isinstance(value, str):
            return f'<c r="{ref}" t="s"{f" s=\"{style}\"" if style else ""}><v>{s[value]}</v></c>'
        return f'<c r="{ref}"{f" s=\"{style}\"" if style else ""}><v>{value}</v></c>'
    rows = [[c('A1', '支持需求清单（合成演示数据）')],
            [c('A2', '序号'), c('B2', '需求'), c('C2', '负责人'), c('D2', '截止日期'), c('E2', '工时'), c('F2', '完成度')]]
    data = [('服务器迁移', '张晓明', 46315, 32.5, 0.8), ('邮件归档', '李华', 46322, 12, 0.35), ('证书更新', '王芳', 46330, 6.25, 1)]
    for i, (need, who, day, hours, done) in enumerate(data, 3):
        rows.append([c(f'A{i}', i - 2), c(f'B{i}', need), c(f'C{i}', who), c(f'D{i}', day, 1), c(f'E{i}', hours, 2), c(f'F{i}', done, 3)])
    rows.append([c('D6', '合计'), f'<c r="E6" s="2"><f>SUM(E3:E5)</f><v>50.75</v></c>'])
    sheet1 = (XML + f'<worksheet xmlns="{S}"><cols><col min="2" max="2" width="18" customWidth="1"/><col min="4" max="4" width="12" customWidth="1"/></cols><sheetData>'
              + ''.join(f'<row r="{n}">{"".join(r)}</row>' for n, r in enumerate(rows, 1))
              + '</sheetData><mergeCells count="1"><mergeCell ref="A1:F1"/></mergeCells></worksheet>')
    sheet2 = (XML + f'<worksheet xmlns="{S}"><sheetData><row r="1">{c("A1", "项目")}{c("B1", "状态")}</row>'
              f'<row r="2">{c("A2", "服务器迁移")}<c r="B2" t="inlineStr"><is><t>进行中</t></is></c></row>'
              f'<row r="3"><c r="A3" t="b"><v>1</v></c><c r="B3" t="e"><v>#DIV/0!</v></c></row></sheetData></worksheet>')
    styles = (XML + f'<styleSheet xmlns="{S}"><numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy/m/d"/></numFmts>'
              '<cellXfs count="4"><xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="2"/><xf numFmtId="9"/></cellXfs></styleSheet>')
    shared = XML + f'<sst xmlns="{S}" count="{len(strings)}" uniqueCount="{len(strings)}">' + ''.join(f'<si><t>{escape(t)}</t></si>' for t in strings) + '</sst>'
    book = (XML + f'<workbook xmlns="{S}" xmlns:r="{_R}"><sheets><sheet name="需求" sheetId="1" r:id="rId1"/>'
            '<sheet name="隐藏" sheetId="3" state="hidden" r:id="rId4"/><sheet name="汇总" sheetId="2" r:id="rId2"/></sheets></workbook>')
    ml = 'application/vnd.openxmlformats-officedocument.spreadsheetml.'
    return _package({'xl/workbook.xml': book, 'xl/worksheets/sheet1.xml': sheet1, 'xl/worksheets/sheet2.xml': sheet2,
                     'xl/worksheets/sheet3.xml': sheet2, 'xl/styles.xml': styles, 'xl/sharedStrings.xml': shared,
                     'xl/_rels/workbook.xml.rels': _rels([('rId1', 'worksheet', 'worksheets/sheet1.xml', False),
                                                         ('rId2', 'worksheet', 'worksheets/sheet2.xml', False),
                                                         ('rId4', 'worksheet', 'worksheets/sheet3.xml', False),
                                                         ('rId3', 'styles', 'styles.xml', False),
                                                         ('rId5', 'sharedStrings', 'sharedStrings.xml', False)])},
                    {'xl/workbook.xml': ml + 'sheet.main+xml', 'xl/worksheets/sheet1.xml': ml + 'worksheet+xml',
                     'xl/worksheets/sheet2.xml': ml + 'worksheet+xml', 'xl/worksheets/sheet3.xml': ml + 'worksheet+xml',
                     'xl/styles.xml': ml + 'styles+xml', 'xl/sharedStrings.xml': ml + 'sharedStrings+xml'}, 'xl/workbook.xml')


def demo_docx(picture=None):
    """A heading, formatted runs, a numbered list, a link, a table with merged cells and a picture."""
    W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
    WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
    PIC = 'http://schemas.openxmlformats.org/drawingml/2006/picture'
    p = lambda body, ppr='': f'<w:p>{f"<w:pPr>{ppr}</w:pPr>" if ppr else ""}{body}</w:p>'
    r = lambda text, rpr='': f'<w:r>{f"<w:rPr>{rpr}</w:rPr>" if rpr else ""}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'
    item = lambda text: p(r(text), '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>')
    cell = lambda text, pr='': f'<w:tc>{f"<w:tcPr>{pr}</w:tcPr>" if pr else ""}{p(r(text))}</w:tc>'
    drawing = ('<w:r><w:drawing><wp:inline><wp:extent cx="1905000" cy="1190625"/><a:graphic><a:graphicData uri="' + PIC + '">'
               '<pic:pic><pic:blipFill><a:blip r:embed="rId9"/></pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>')
    body = ''.join([
        p(r('设计评审纪要（合成演示数据）'), '<w:pStyle w:val="1"/>'),
        p(r('会议时间：') + r('2026 年 10 月 20 日', '<w:b/>') + r('，地点：研发中心。')),
        p(r('结论：', '<w:b/><w:color w:val="C00000"/>') + r('方案通过，', '<w:i/>') + r('细节见下表。', '<w:u w:val="single"/>')),
        p(r('待办事项'), '<w:pStyle w:val="2"/>'),
        item('完成服务器迁移'), item('更新签名证书'), item('整理邮件归档'),
        p('<w:hyperlink r:id="rId8">' + r('项目说明（外部链接）', '<w:color w:val="0563C1"/><w:u w:val="single"/>') + '</w:hyperlink>'),
        f'<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid>'
        f'<w:tr>{cell("事项", "")}{cell("负责人", "")}{cell("状态", "")}</w:tr>'
        f'<w:tr>{cell("服务器迁移", "<w:vMerge w:val=\"restart\"/>")}{cell("张晓明")}{cell("进行中")}</w:tr>'
        f'<w:tr>{cell("", "<w:vMerge/>")}{cell("李华")}{cell("待开始")}</w:tr>'
        f'<w:tr>{cell("合计 3 项", "<w:gridSpan w:val=\"3\"/>")}</w:tr></w:tbl>',
        p(drawing, '<w:jc w:val="center"/>') if picture else '',
        p(r('H') + r('2', '<w:vertAlign w:val="subscript"/>') + r('O 与 E=mc') + r('2', '<w:vertAlign w:val="superscript"/>')),
    ])
    document = XML + f'<w:document xmlns:w="{W}" xmlns:r="{_R}" xmlns:a="{A}" xmlns:wp="{WP}" xmlns:pic="{PIC}"><w:body>{body}<w:sectPr/></w:body></w:document>'
    styles = (XML + f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph" w:styleId="1"><w:name w:val="heading 1"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="2"><w:name w:val="heading 2"/><w:basedOn w:val="1"/></w:style></w:styles>')
    numbering = (XML + f'<w:numbering xmlns:w="{W}"><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/>'
                 '<w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>')
    parts = {'word/document.xml': document, 'word/styles.xml': styles, 'word/numbering.xml': numbering,
             'word/_rels/document.xml.rels': _rels([('rId1', 'styles', 'styles.xml', False), ('rId2', 'numbering', 'numbering.xml', False),
                                                   ('rId8', 'hyperlink', 'https://example.invalid/project', True)]
                                                  + ([('rId9', 'image', 'media/image1.png', False)] if picture else []))}
    if picture:
        parts['word/media/image1.png'] = picture
    wml = 'application/vnd.openxmlformats-officedocument.wordprocessingml.'
    return _package(parts, {'word/document.xml': wml + 'document.main+xml', 'word/styles.xml': wml + 'styles+xml',
                            'word/numbering.xml': wml + 'numbering+xml'}, 'word/document.xml')


def demo_pptx():
    """Two slides: a title slide and a bulleted slide with a table."""
    P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
    A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
    para = lambda text, lvl=0: f'<a:p>{f"<a:pPr lvl=\"{lvl}\"/>" if lvl else ""}<a:r><a:t>{escape(text)}</a:t></a:r></a:p>'
    shape = lambda paras, ph='': (f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="s"/><p:cNvSpPr/><p:nvPr>{f"<p:ph type=\"{ph}\"/>" if ph else ""}</p:nvPr></p:nvSpPr>'
                                  f'<p:txBody>{"".join(paras)}</p:txBody></p:sp>')
    slide = lambda shapes: XML + f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{_R}"><p:cSld><p:spTree>{shapes}</p:spTree></p:cSld></p:sld>'
    table = ('<p:graphicFrame><a:graphic><a:graphicData><a:tbl><a:tr><a:tc><a:txBody>' + para('阶段') + '</a:txBody></a:tc><a:tc><a:txBody>'
             + para('时间') + '</a:txBody></a:tc></a:tr><a:tr><a:tc><a:txBody>' + para('联调') + '</a:txBody></a:tc><a:tc><a:txBody>'
             + para('11 月') + '</a:txBody></a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>')
    s1 = slide(shape([para('鸿雁项目周报')], 'ctrTitle') + shape([para('合成演示数据 · 2026 年 10 月')]))
    s2 = slide(shape([para('本周进展')], 'title') + shape([para('完成附件预览'), para('Word、Excel、PPT', 1), para('准备打包测试')]) + table)
    pres = (XML + f'<p:presentation xmlns:p="{P}" xmlns:r="{_R}"><p:sldIdLst><p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId3"/></p:sldIdLst></p:presentation>')
    pml = 'application/vnd.openxmlformats-officedocument.presentationml.'
    return _package({'ppt/presentation.xml': pres, 'ppt/slides/slide1.xml': s1, 'ppt/slides/slide2.xml': s2,
                     'ppt/_rels/presentation.xml.rels': _rels([('rId2', 'slide', 'slides/slide1.xml', False), ('rId3', 'slide', 'slides/slide2.xml', False)])},
                    {'ppt/presentation.xml': pml + 'presentation.main+xml', 'ppt/slides/slide1.xml': pml + 'slide+xml',
                     'ppt/slides/slide2.xml': pml + 'slide+xml'}, 'ppt/presentation.xml')


def demo_html():
    return ('<!doctype html><html><head><meta charset="utf-8"><title>周报</title><script>alert("x")</script>'
            '<style>h1{color:#2563eb} td{border:1px solid #ccc;padding:4px 8px}</style></head>'
            '<body onload="steal()"><h1>项目周报（合成演示数据）</h1><p>本周完成附件预览，<b>下周</b>打包测试。</p>'
            '<table><tr><td>进度</td><td>80%</td></tr></table><p><img src="https://tracker.example.invalid/pixel.gif" alt="远程图片"></p>'
            '<iframe src="https://example.invalid"></iframe></body></html>').encode('utf-8')


def demo_svg():
    return (b'<svg xmlns="http://www.w3.org/2000/svg" width="240" height="120" viewBox="0 0 240 120" onload="alert(1)">'
            b'<script>alert(2)</script><rect width="240" height="120" rx="12" fill="#2563eb"/>'
            b'<circle cx="60" cy="60" r="36" fill="#fff"/><image href="https://example.invalid/x.png" width="10" height="10"/>'
            b'<rect x="120" y="36" width="90" height="48" rx="8" fill="#93c5fd"/></svg>')


def demo_bmp(width=64, height=40):
    rows = b''
    for y in range(height):
        row = b''.join(bytes((235, 99, 37) if (x // 8 + y // 8) % 2 else (255, 255, 255)) for x in range(width))
        rows += row + b'\x00' * ((4 - len(row) % 4) % 4)
    header = b'BM' + struct.pack('<IHHI', 54 + len(rows), 0, 0, 54)
    info = struct.pack('<IiiHHIIiiII', 40, width, height, 1, 24, 0, len(rows), 2835, 2835, 0, 0)
    return header + info + rows
