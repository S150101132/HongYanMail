"""Establish the performance baseline the design document asks to measure.

All input is synthetic. These are single-machine numbers, not budgets: the design
document says baselines must be recorded before any budget is promised, and this
script records them so the acceptance record stops saying "not measured".
"""
import json
import os
import statistics
import sys
import time
from email.message import EmailMessage
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / 'analysis' / 'performance-baseline.json'
LIST_ROWS = 1000
SAMPLE_REPEATS = 5


def synthetic_mime(text_bytes=64 * 1024, html_bytes=96 * 1024, attachments=3) -> bytes:
    message = EmailMessage()
    message['Subject'] = '=?utf-8?B?5oCn6IO95Z+65YeG5qC35pys?='
    message['From'] = 'sender@example.test'
    message['To'] = 'reader@example.test'
    message['Date'] = 'Thu, 15 Oct 2026 12:00:00 +0300'
    message.set_content('纯文本正文\n' * (text_bytes // 16))
    message.add_alternative('<p>HTML 正文</p>' * (html_bytes // 20), subtype='html')
    for index in range(attachments):
        message.add_attachment(b'\x00' * (8 * 1024), maintype='application',
                               subtype='octet-stream', filename=f'synthetic-{index}.bin')
    return message.as_bytes()


def timed(function, repeats=SAMPLE_REPEATS):
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        function()
        samples.append((time.perf_counter() - started) * 1000)
    return {'median_ms': round(statistics.median(samples), 2),
            'min_ms': round(min(samples), 2),
            'max_ms': round(max(samples), 2),
            'samples': [round(value, 2) for value in samples]}


def measure_offline():
    from coremail_desktop.mailbox.service import MailboxService
    from coremail_desktop.mime.parser import parse_mime
    from coremail_desktop.models import MessageSummary
    from coremail_desktop.reader.resources import ReaderResourceStore

    results = {}

    small = synthetic_mime(16 * 1024, 24 * 1024, 1)
    large = synthetic_mime(256 * 1024, 512 * 1024, 5)
    results['sample_sizes'] = {'small_bytes': len(small), 'large_bytes': len(large)}

    results['parse_mime_small'] = timed(lambda: parse_mime(small, 'm1'))
    results['parse_mime_large'] = timed(lambda: parse_mime(large, 'm2'))

    from coremail_desktop.mime.sanitizer import sanitize_html
    html = '<p>正文</p><img src="https://example.test/x.png"><a href="https://example.test">链接</a>' * 2000
    results['sanitize_html_120kb'] = timed(lambda: sanitize_html(html, {}))

    summaries = [MessageSummary(str(i), 'inbox', f'主题 {i}', 'sender@example.test', '2026-10-15') for i in range(LIST_ROWS)]
    service = MailboxService()
    results['merge_1000_summaries'] = timed(lambda: service.merge_page('inbox', list(summaries), True))

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from coremail_desktop.ui.mail_list import MailListPane
    pane = MailListPane()
    results['list_widget_set_1000_rows'] = timed(lambda: pane.set_messages(list(summaries)))
    results['list_filter_1000_rows'] = timed(lambda: pane.filter.setText(f'主题 {time.perf_counter()}'))
    pane.filter.setText('主题 5')
    results['list_filter_match_count'] = pane.model.rowCount()
    pane.close()

    store = ReaderResourceStore()
    payload = {f'cid-{i}': ('image/png', b'\x89PNG' + b'x' * 4096) for i in range(50)}
    results['cid_store_50_images'] = timed(lambda: store.replace('scope', dict(payload)))

    # Reader construction includes the Qt WebEngine profile; report it separately
    # because it is a one-off cost, not a per-message cost.
    from coremail_desktop.reader.view import ReaderView
    started = time.perf_counter()
    reader = ReaderView()
    results['reader_view_construction_ms'] = round((time.perf_counter() - started) * 1000, 2)
    results['reader_webengine_available'] = reader.web_view is not None
    reader.close()
    return results


def main():
    report = {
        'measured_at': '2026-10-15',
        'machine_note': 'Single development machine, offscreen platform; not a budget and not a clean-user baseline.',
        'python': sys.version.split()[0],
        'list_rows': LIST_ROWS,
        'repeats_per_measurement': SAMPLE_REPEATS,
        'offline': measure_offline(),
    }
    report['interpretation'] = {
        'ui_thread_never_awaits_network': 'proved by architecture tests, not by these numbers',
        'virtual_list': 'QListView with one model; no per-message widgets are created',
        'what_is_not_measured': [
            'live server latency and first-body time',
            'packaged cold start on a clean Windows user profile',
            'HTML render time for real-world mail inside the packaged build',
            '125% / 150% / 200% display scaling behaviour',
        ],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
