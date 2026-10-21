"""Walk the demo build through 问邮箱 and save screenshots.

The right-hand conversation: not configured (error with 打开 AI 设置), the empty view,
a question in progress, the answer with source cards (demo client, no network), and a
follow-up about the mail open in the reader.

Usage: python tools/screenshot_ask.py OUTDIR [light|dark] [width]
HONGYAN_WALK_TRACE=1 prints each step; HONGYAN_DEBUG=1 (set here) prints page errors.
"""
import json
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
from coremail_desktop.app.demo import DemoAdapter, DemoAiClient  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else 'analysis/screens-ask')
theme = sys.argv[2] if len(sys.argv) > 2 else 'light'
width = int(sys.argv[3]) if len(sys.argv) > 3 else 1440
out.mkdir(parents=True, exist_ok=True)

app = QApplication(sys.argv)
import atexit, shutil  # noqa: E401
shot_dir = tempfile.mkdtemp(prefix='hongyan-ask-', dir=str(ROOT / 'build'))
atexit.register(shutil.rmtree, shot_dir, True)
backend = MailBackend(shot_dir, adapter_factory=DemoAdapter)
backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'demo', True)
backend.set_prefs({'theme': theme})
window = shell.AppShell(backend, demo=True)
window.resize(width, 900)
window.show()
QUESTION = json.dumps(backend.adapter.rows['demo1']['subject'][:2] + ' 这件事最后是怎么定的？', ensure_ascii=False)


def demo_ai():
    backend.ai_client = DemoAiClient()


STEPS = [
    (2500, None, None),
    (600, f"$('#search').value = {QUESTION}; $('#btn-ask').click()", 'ask-not-configured'),
    (300, demo_ai, None),
    (300, "$('#search').value = ''; newChat()", 'ask-empty'),
    (300, f"$('#ask-input').value = {QUESTION}; askMailbox()", None),
    (0, "document.body.offsetWidth", 'ask-loading'),
    (2200, "document.body.offsetWidth", 'ask-answer'),
    (300, "$('#mail-list .row').click()", None),
    (1500, "$('#ask-input').value = '这封说了什么？'; askMailbox()", None),
    (2200, "document.body.offsetWidth", 'ask-follow-up'),
    (300, "$$('#ask-out .ask-process').forEach((d) => d.open = true)", 'ask-process'),
]


def run(index=0):
    if index >= len(STEPS):
        app.quit()
        return
    delay, script, name = STEPS[index]

    def act():
        if os.environ.get('HONGYAN_WALK_TRACE'):
            print(f'step {index}: {name or "-"}', flush=True)
        if callable(script):
            script()
        elif script:
            window.page.runJavaScript(script)

        def snap():
            if name:
                window.grab().save(str(out / f'{name}-{theme}-{width}.png'))
            run(index + 1)
        QTimer.singleShot(900 if name == 'ask-loading' else 700, snap)
    QTimer.singleShot(delay, act)


run()
app.exec()
print('saved to', out)
