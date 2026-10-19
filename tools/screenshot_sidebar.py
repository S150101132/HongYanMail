"""Screenshot the sidebar of the demo build at several window sizes and sidebar widths.

The demo account gets many folders (some with long names), an expanded shared
mailbox and its tags, so the sidebar has more rows than fit. Each shot also
prints whether any sidebar element scrolls sideways (it never should).

Usage: python tools/screenshot_sidebar.py OUTDIR [light|dark]
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QTWEBENGINE_DISABLE_SANDBOX', '1')  # Demo data only; the sandbox fails on this machine.

from coremail_desktop.app import shell  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from coremail_desktop.app.backend import MailBackend  # noqa: E402
from coremail_desktop.app.demo import DemoAdapter  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else 'analysis/screens-v05/sidebar')
theme = sys.argv[2] if len(sys.argv) > 2 else 'light'
out.mkdir(parents=True, exist_ok=True)

app = QApplication(sys.argv)
import atexit, shutil  # noqa: E401
shot_dir = tempfile.mkdtemp(prefix='hongyan-side-', dir=str(ROOT / 'build'))
atexit.register(shutil.rmtree, shot_dir, True)
backend = MailBackend(shot_dir, adapter_factory=DemoAdapter)
backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'demo', True)
backend.adapter.folders.update({'6': '病毒邮件', '101': 'NG Daily Report', '102': 'NG-OT', '103': '公司通知',
                                '104': '浪潮通信信息系统有限公司 · 质量部周报与月度汇总归档', '105': '客户往来', '106': '采购与合同',
                                '107': 'IT Service', '108': '培训资料', '109': 'HR 通知'})
backend.set_prefs({'theme': theme})
window = shell.AppShell(backend, demo=True)
window.show()

# (window width, height, sidebar width)
SIZES = [(1440, 900, 232), (1440, 700, 232), (1280, 600, 180), (1920, 1040, 232), (1440, 900, 400), (1280, 760, 180)]
PROBE = """JSON.stringify((() => {
  const side = document.querySelector('.sidebar');
  const wide = [...side.querySelectorAll('*')].filter((e) => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== 'visible' && getComputedStyle(e).overflowX !== 'hidden');
  const tall = [...side.querySelectorAll('*'), side].filter((e) => e.scrollHeight > e.clientHeight + 1 && /auto|scroll/.test(getComputedStyle(e).overflowY));
  return { hscroll: wide.map((e) => e.id || e.className), vscroll: tall.map((e) => e.id || e.className) };
})())"""


def run(index=0):
    if index >= len(SIZES):
        app.quit()
        return
    w, h, side = SIZES[index]
    window.resize(w, h)
    js = ("S.openBoxes.add('rd'); S.prefs.sidebarWidth = %d; applyWidths(); renderFolders();" % side)
    window.page.runJavaScript(js)

    def snap():
        name = f'sidebar-{w}x{h}-side{side}-{theme}.png'
        window.grab().save(str(out / name))
        window.page.runJavaScript(PROBE, 0, lambda r: (print(name, json.loads(r) if r else r, flush=True), run(index + 1)))
    QTimer.singleShot(1200, snap)


QTimer.singleShot(3000, run)
app.exec()
print('saved to', out)
