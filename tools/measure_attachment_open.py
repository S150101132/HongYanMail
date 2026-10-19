"""Where does 打开 of an Excel attachment spend its time? (B2)

Times each step of opening an attachment: fetching the raw message, decrypting and
parsing it, extracting the attachment, writing the copy into opened/, building the
in-app preview, and (only with --launch) handing the file to the system.

    python -m tools.measure_attachment_open                 # demo data, no network
    python -m tools.measure_attachment_open --real ACCOUNT  # read-only, password dialog
    ... --launch                                            # also time the hand-off (opens Excel)

Records only timings, sizes and counts (analysis/attachment-open-timing-<date>.json);
never names, addresses or content. Real mail is opened without marking it read. The
cache of a real run lives in build/ and is deleted afterwards.
"""
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coremail_desktop.app.backend import MailBackend, safe_file_name, shell_open  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402

EXCEL = ('.xlsx', '.xlsm', '.xls')


def ms(start):
    return round((time.perf_counter() - start) * 1000, 1)


def measure(backend, mid, launch=False):
    """Step timings (ms) for every Excel attachment of one message, cold cache."""
    backend._docs.clear()  # The run's cache starts empty, so fetchMs includes the download.
    start = time.perf_counter()
    raw = backend._raw(mid)
    fetch = ms(start)
    start = time.perf_counter()
    document = backend._document(mid)
    parse = ms(start)
    rows = []
    for info in document.attachments:
        if not info.name.lower().endswith(EXCEL):
            continue
        start = time.perf_counter()
        name, _kind, data = backend.attachment(mid, info.part_id)
        extract = ms(start)
        start = time.perf_counter()
        folder = backend.data_dir / 'opened' / os.urandom(6).hex()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / safe_file_name(name)
        target.write_bytes(data)
        write = ms(start)
        start = time.perf_counter()
        try:
            preview = backend.attachment_preview(mid, info.part_id)
            sheets, preview_ms = len(preview.get('sheets', ())), ms(start)
        except AppError as error:
            sheets, preview_ms = error.code, ms(start)
        row = {'rawBytes': len(raw), 'attachmentBytes': len(data), 'encrypted': document.encryption_state,
               'fetchMs': fetch, 'decryptParseMs': parse, 'extractMs': extract, 'writeMs': write,
               'previewMs': preview_ms, 'sheets': sheets}
        if launch:
            # The old path called ShellExecute on the window's thread and waited for it.
            start = time.perf_counter()
            os.startfile(str(target))  # noqa: S606 -- the measured file is a synthetic or the user's own attachment.
            row['startfileReturnMs'] = ms(start)
            time.sleep(8)
            start = time.perf_counter()
            shell_open(target)
            row['explorerHandoffReturnMs'] = ms(start)
        rows.append(row)
    return rows


def big_workbook(rows=20000, cols=12):
    """A larger synthetic workbook (numbers and shared strings) for the parse timing."""
    import io
    import zipfile
    from coremail_desktop.app.demo_office import _R, _package, _rels, XML
    S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    words = [f'项目{i}' for i in range(200)]
    sheet = io.StringIO()
    sheet.write(XML + f'<worksheet xmlns="{S}"><sheetData>')
    for r in range(1, rows + 1):
        sheet.write(f'<row r="{r}">')
        for c in range(cols):
            ref = chr(65 + c) + str(r)
            sheet.write(f'<c r="{ref}" t="s"><v>{(r + c) % 200}</v></c>' if c % 3 == 0 else f'<c r="{ref}"><v>{r * 1.5 + c}</v></c>')
        sheet.write('</row>')
    sheet.write('</sheetData></worksheet>')
    ml = 'application/vnd.openxmlformats-officedocument.spreadsheetml.'
    return _package({'xl/workbook.xml': XML + f'<workbook xmlns="{S}" xmlns:r="{_R}"><sheets><sheet name="大表" sheetId="1" r:id="rId1"/></sheets></workbook>',
                     'xl/worksheets/sheet1.xml': sheet.getvalue(),
                     'xl/sharedStrings.xml': XML + f'<sst xmlns="{S}">' + ''.join(f'<si><t>{w}</t></si>' for w in words) + '</sst>',
                     'xl/_rels/workbook.xml.rels': _rels([('rId1', 'worksheet', 'worksheets/sheet1.xml', False),
                                                         ('rId2', 'sharedStrings', 'sharedStrings.xml', False)])},
                    {'xl/workbook.xml': ml + 'sheet.main+xml'}, 'xl/workbook.xml')


def demo(data, launch):
    from coremail_desktop.app.demo import DemoAdapter
    from tests.test_app_backend import XorProtector, local_services_stub
    backend = MailBackend(data, adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
    backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'x')
    backend.folders()
    backend.messages('1')
    report = {'demo': measure(backend, 'demo1', launch) + measure(backend, 'demo3')}
    from coremail_desktop.mime import office
    book = big_workbook()
    times = []
    for _ in range(3):
        start = time.perf_counter()
        sheets = office.xlsx_sheets(book)
        times.append(ms(start))
    report['bigWorkbook'] = {'bytes': len(book), 'previewMs': statistics.median(times), 'truncated': sheets[0]['truncated']}
    backend.logout()
    return report


def real(data, account, launch):
    import tkinter
    from tkinter import simpledialog
    root = tkinter.Tk(); root.withdraw(); root.attributes('-topmost', True)
    try:
        password = simpledialog.askstring('鸿雁 · 附件打开计时', f'{account} 的密码（仅本次内存使用）:\n只读：找最近带 Excel 附件的邮件并计时，不标记已读',
                                          show='*', parent=root)
    finally:
        root.destroy()
    if not password:
        return None
    backend = MailBackend(data)
    backend.login('https://newmail.inspur.com/coremail/', account, password, False)
    backend.folders()
    rows, checked = [], 0
    for offset in (0, 100, 200):
        for summary in backend.messages('1', offset, 100)['rows']:
            if len(rows) >= 3:
                break
            if not summary.get('flags', {}).get('attached'):
                continue
            checked += 1
            try:
                found = measure(backend, summary['id'], launch and not rows)
            except AppError as error:
                found = [{'error': error.code}]
            rows += found
        if len(rows) >= 3:
            break
    backend.logout()
    return {'real': rows, 'messagesChecked': checked}


def main():
    launch = '--launch' in sys.argv
    account = sys.argv[sys.argv.index('--real') + 1] if '--real' in sys.argv else None
    (ROOT / 'build').mkdir(exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix='hongyan-measure-', dir=str(ROOT / 'build')))
    try:
        report = real(data, account, launch) if account else demo(data, launch)
    finally:
        shutil.rmtree(data, ignore_errors=True)
    if report is None:
        return 130
    report['date'] = time.strftime('%Y-%m-%d %H:%M')
    out = ROOT / 'analysis' / f'attachment-open-timing-{time.strftime("%Y-%m-%d")}{"-real" if account else ""}.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
