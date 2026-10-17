"""Drive the demo build through key screens and save window screenshots.

Usage: python tools/screenshot_app.py OUTDIR [light|dark]
"""
import os
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

out = Path(sys.argv[1] if len(sys.argv) > 1 else 'analysis/screens')
theme = sys.argv[2] if len(sys.argv) > 2 else 'light'
width = int(sys.argv[3]) if len(sys.argv) > 3 else 1440
out.mkdir(parents=True, exist_ok=True)

app = QApplication(sys.argv)
import atexit, shutil  # noqa: E401
shot_dir = tempfile.mkdtemp(prefix='hongyan-shot-', dir=str(ROOT / 'build'))
atexit.register(shutil.rmtree, shot_dir, True)
backend = MailBackend(shot_dir, adapter_factory=DemoAdapter)
backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'demo', True)
backend.set_prefs({'theme': theme, 'signature': '<b>演示用户</b><br>研发中心 · 产品部<br><span style="color:#2563eb">demo@example.invalid</span>'})
window = shell.AppShell(backend, demo=True)
window.resize(width, 900)
window.show()

STEPS = [
    (2500, None, None),
    (600, "selectMessage(visibleRows()[0].id)", 'main'),
    (900, "startCompose('reply', S.selected)", None),
    (900, "(() => { const i=$('.chips[data-field=\"to\"] input'); i.value='zh'; i.dispatchEvent(new Event('input')); })()", 'composer'),
    (700, "closeComposer(true); openSettings()", "settings"),
    (600, "$('#settings').classList.add('hidden'); startCompose('new')", "compose-new"),
    (600, "$('#settings').classList.add('hidden'); runSearch('预算')", 'search'),
    (600, "closeComposer(true); clearSearch(); S.prefs.threads = true; renderThreadToggle()", None),
    (900, "selectMessage(S.items[0].id)", 'threads'),
    (700, "$('#r-thread-older .t-card:nth-child(2)').click()", 'thread-expanded'),
    (700, "S.prefs.threads = false; renderThreadToggle(); renderList(); selectMessage(S.rows[1].id); "
          "toggleItem(S.rows[2].id); toggleItem(S.rows[4].id); toggleItem(S.rows[5].id)", 'batch'),
    (700, "batchMove(null, { delete: true })", 'batch-undo'),
    (900, "clearSelection(false); startCompose('new', null, { to: ['rd@example.invalid'], subject: '周会材料' })", None),
    (600, "C.draft.scheduleAt = new Date(2026, 9, 18, 9, 0).getTime(); $('#cmp-receipt').checked = true; renderSchedule()", 'compose-schedule'),
    (600, "pickSchedule()", 'schedule-picker'),
    (600, "$('#prompt').classList.add('hidden'); closeComposer(true); selectFolder('3')", None),
    (1200, "selectMessage(S.rows[0].id)", None),
    (900, "loadDeliveryStatus(S.message, true)", 'sent-status'),
    (900, "recallMessage(S.message); setTimeout(() => $('#prompt-ok').click(), 200)", 'recall-result'),
    (900, "$('#prompt').classList.add('hidden'); $('#nav-scheduled').click()", None),
    (1000, "selectMessage(S.rows[0].id)", 'scheduled'),
    (900, "S.message && writeFrame && 0; document.body.offsetWidth", None),
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
