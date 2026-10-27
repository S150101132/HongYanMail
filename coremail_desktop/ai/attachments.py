"""附件速读: the text inside a mail's attachments, for 「总结这封」 and 问邮箱.

Word / PowerPoint / Excel go through the in-app viewer's parsers (mime/office.py), plain
text files are decoded, PDF text comes from pdf_text (the shell's QtPdf; None without it).
Pictures, archives and anything damaged or protected are skipped, named only. Attachment
text is mail text: untrusted, cleaned to visible text, and bounded per file and in all.
"""
from coremail_desktop.ai.summary import _tidy, visible_text

FILE_CHARS, TOTAL_CHARS = 4000, 8000
FILE_BYTES = 30 * 1024 * 1024
FILES = 5


def _text(name, data, pdf_text):
    from coremail_desktop.app.backend import _decode_text, preview_kind
    from coremail_desktop.mime import office
    kind = preview_kind(name, data)
    try:
        if kind == 'pdf':
            return pdf_text(data) if pdf_text else None
        if kind == 'text':
            return _decode_text(bytes(data[:FILE_CHARS * 4]))
        if kind in ('docx', 'pptx'):
            return visible_text((office.docx_html if kind == 'docx' else office.pptx_html)(data)[0])
        if kind == 'xlsx':
            return '\n'.join(f'【{s["name"]}】\n{visible_text(s["html"])}' for s in office.xlsx_sheets(data) if s['html'])
    except Exception:
        pass  # Damaged, password-protected or a reader failure: the name is all the AI gets.
    return None


def attachment_texts(document, pdf_text=None, total=TOTAL_CHARS):
    """[(name, text, truncated)] for the attachments that hold readable text, in order."""
    out, left, payloads = [], total, getattr(document, 'attachment_payloads', None) or {}
    for info in document.attachments:
        data = payloads.get(getattr(info, 'part_id', None))
        if left <= 0 or len(out) >= FILES:
            break
        if not data or len(data) > FILE_BYTES:
            continue
        text = _tidy(_text(info.name, data, pdf_text) or '')
        if not text:
            continue
        cap = min(FILE_CHARS, left)
        out.append((info.name, text[:cap], len(text) > cap))
        left -= min(len(text), cap)
    return out


def material(texts):
    """The attachments' text as it goes into the prompt after the body."""
    return ''.join(f'\n\n附件「{name}」的内容：\n{text}' + ('\n……（附件后面的内容已省略）' if cut else '')
                   for name, text, cut in texts)
