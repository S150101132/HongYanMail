"""Walk the demo build through 本地存档 and save screenshots.

A synthetic archive (built with the test helpers, under build/) stands in for the
official client's archmails: the settings section, the archive list, an archived
message open read-only, and a search that mixes server and archived mail.

Usage: python tools/screenshot_archive.py OUTDIR [light|dark] [width]
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('HONGYAN_DEBUG', '1')
os.environ.setdefault('QTWEBENGINE_DISABLE_SANDBOX', '1')  # Demo data only; the sandbox fails on this machine.

from coremail_desktop.app import shell  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from coremail_desktop.app.backend import MailBackend  # noqa: E402
from coremail_desktop.app.demo import DemoAdapter  # noqa: E402

import struct  # noqa: E402
import zstandard  # noqa: E402


# The synthetic pack builders of tests/test_archive.py (importing the tests here would
# pull in their fixtures).
def mail(i=1, body='正文内容 UAT 签收', subject=None, date='Tue, 10 Feb 2026 14:17:45 +0300', extra=''):
    subject = subject or f'BI UAT Sign-Off {i}'
    return (f'From: "Zeeshan" <z{i}@inspur.com>\r\nTo: "Bowen" <me@inspur.com>\r\nSubject: {subject}\r\n'
            f'Date: {date}\r\nMessage-ID: <m{i}@example.invalid>\r\nMIME-Version: 1.0\r\n{extra}'
            f'Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: 8bit\r\n\r\n{body}\r\n').encode('utf-8')


def record(raw, key=0x5a, chunk=40):
    """cmms framing: masked original in Zstandard chunks, a zero chunk, the CMCT table."""
    masked = raw.translate(bytes(b ^ key for b in range(256)))
    padded = masked + b'\0' * 7  # The client pads; the header's length cuts it off.
    out, table = bytearray(b'cmms' + b'\0' * 12 + struct.pack('<Q', len(masked)) + b'\0' * 40), []
    for start in range(0, len(padded), chunk):
        piece = padded[start:start + chunk]
        packed = zstandard.ZstdCompressor().compress(piece)
        table.append((len(out), len(piece), len(packed)))
        out += b'CMCD' + struct.pack('<II', len(piece), len(packed)) + b'\0' * 20 + packed
    out += b'CMCD' + b'\0' * 28
    for row in table:
        out += struct.pack('<QII', *row)
    out += b'CMCT' + struct.pack('<I', len(table) * 16)
    return bytes(out)


def pack(records, names=None):
    """A CMDR pack: directory page 0, an extension page past 15 entries, then for each
    record a PGFN page and its data pages."""
    pages = {}
    count = len(records)
    extension = 1 if count > 15 else 0
    next_page = 2 if extension else 1
    directory = []
    for i, data in enumerate(records):
        pgfn, first = next_page, next_page + 1
        n = max(1, -(-len(data) // 1024))
        page = bytearray(1024)
        page[:4] = b'PGFN'
        struct.pack_into('<Q', page, 24, len(data))
        struct.pack_into('<QQ', page, 64, first, n)
        pages[pgfn] = bytes(page)
        for k in range(n):
            pages[first + k] = data[k * 1024:(k + 1) * 1024].ljust(1024, b'\0')
        next_page = first + n
        directory.append((pgfn, (names or {}).get(i, f'{i:02x}.data')))
    head = bytearray(512)
    head[:4] = b'CMDR'
    struct.pack_into('<I', head, 260, 1024)
    body = bytearray(1024 * next_page)
    for number, content in pages.items():
        body[number * 1024:(number + 1) * 1024] = content
    out = head + body
    struct.pack_into('<QQQ', out, 512, extension, 1 if extension else 0, count)
    for i, (block, name) in enumerate(directory):
        at = 576 + i * 64 if i < 15 else 512 + extension * 1024 + (i - 15) * 64
        struct.pack_into('<Q', out, at, block)
        out[at + 8:at + 8 + len(name)] = name.encode()
    return bytes(out)


out = Path(sys.argv[1] if len(sys.argv) > 1 else 'analysis/screens-archive')
theme = sys.argv[2] if len(sys.argv) > 2 else 'light'
width = int(sys.argv[3]) if len(sys.argv) > 3 else 1440
out.mkdir(parents=True, exist_ok=True)

app = QApplication(sys.argv)
import atexit  # noqa: E402
work = tempfile.mkdtemp(prefix='hongyan-archive-', dir=str(ROOT / 'build'))
atexit.register(shutil.rmtree, work, True)
account = Path(work) / 'email' / 'demo@example.invalid'
(account / 'archmails').mkdir(parents=True)
SAMPLES = [
    ('BI UAT Sign-Off Confirmation（演示）', 'Tue, 10 Feb 2026 14:17:45 +0300', '各位：BI 系统 UAT 已完成，签收文件见附件。'),
    ('【会议纪要】扩容项目立项会议（演示）', 'Wed, 11 Feb 2026 10:00:00 +0300', '会议确认了扩容范围和里程碑。'),
    ('Data Enrichment across B/OSS Project（演示）', 'Mon, 09 Feb 2026 16:20:00 +0300', '请评审数据补全方案。'),
    ('Re: 网络开通申请（演示）', 'Thu, 15 Jan 2026 09:12:00 +0300', 'DMZ 到数据库的端口申请已提交。'),
]
for index, (subject, date, body) in enumerate(SAMPLES):
    (account / 'archmails' / f'{index:02x}.cmpack').write_bytes(pack([record(mail(index + 1, body=body, subject=subject, date=date))]))
backend = MailBackend(Path(work) / 'data', adapter_factory=DemoAdapter)
backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'demo', True)
backend.set_prefs({'theme': theme})
window = shell.AppShell(backend, demo=True)
window.resize(width, 900)
window.show()
FOLDER = str(account).replace('\\', '\\\\')

STEPS = [
    (2500, None, None),
    (300, "openSettings(); setTimeout(() => $('#archive-section').scrollIntoView({block: 'start'}), 50)", None),
    (300, f"$('#archive-folder').value = '{FOLDER}'", None),
    (600, "$('#archive-save').click()", None),
    (1500, "document.body.offsetWidth", 'archive-settings'),
    (300, "$('#settings [data-close]').click(); $('#nav-archive').click()", None),
    (900, "document.body.offsetWidth", 'archive-list'),
    (300, "selectMessage(S.rows[0].id)", None),
    (1200, "document.body.offsetWidth", 'archive-message'),
    (300, "$('#search').value = '会议'; runSearch('会议')", None),
    (1500, "document.body.offsetWidth", 'archive-search'),
]


def run(index=0):
    if index >= len(STEPS):
        app.quit()
        return
    delay, script, name = STEPS[index]

    def act():
        if script:
            window.page.runJavaScript(script)

        def snap():
            if name:
                window.grab().save(str(out / f'{name}-{theme}-{width}.png'))
            run(index + 1)
        QTimer.singleShot(700, snap)
    QTimer.singleShot(delay, act)


run()
app.exec()
print('saved to', out)
