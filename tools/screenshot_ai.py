"""Walk the demo build through 「总结这封」 and the AI settings, and save screenshots.

Not configured (error with 打开 AI 设置), the settings section, a summary in progress
and done (demo client, no network), and the consent dialog for encrypted mail.

Usage: python tools/screenshot_ai.py OUTDIR [light|dark] [width]
HONGYAN_WALK_TRACE=1 prints each step; HONGYAN_DEBUG=1 (set here) prints page errors.
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
from coremail_desktop.app.demo import DemoAdapter, DemoAiClient  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else 'analysis/screens-ai')
theme = sys.argv[2] if len(sys.argv) > 2 else 'light'
width = int(sys.argv[3]) if len(sys.argv) > 3 else 1440
out.mkdir(parents=True, exist_ok=True)

app = QApplication(sys.argv)
import atexit, shutil  # noqa: E401
shot_dir = tempfile.mkdtemp(prefix='hongyan-ai-', dir=str(ROOT / 'build'))
atexit.register(shutil.rmtree, shot_dir, True)
backend = MailBackend(shot_dir, adapter_factory=DemoAdapter)
backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'demo', True)
backend.set_prefs({'theme': theme})
window = shell.AppShell(backend, demo=True)
window.resize(width, 900)
window.show()


def demo_ai():
    backend.ai_client = DemoAiClient()


STEPS = [
    (2500, None, None),
    (1200, "selectMessage('demo1')", None),
    (600, "$('#reader-body [data-act=\"summarize\"]').click()", 'ai-not-configured'),
    (500, "$('#ai-panel [data-ai=\"setup\"]').click()", 'ai-settings'),
    (300, demo_ai, None),
    (500, "$('#settings [data-close]').click(); S.ai.clear(); selectMessage('demo1')", None),
    (900, "$('#reader-body [data-act=\"summarize\"]').click()", None),
    (0, "document.body.offsetWidth", 'ai-loading'),
    (900, "document.body.offsetWidth", 'ai-summary'),
    (500, "S.message.encryption = 'decrypted'; S.ai.clear(); summarizeMessage(S.message)", 'ai-encrypted-consent'),
    (500, "$('#prompt [data-close]').click(); S.message.encryption = 'none'", None),
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
        QTimer.singleShot(300 if name == 'ai-loading' else 700, snap)
    QTimer.singleShot(delay, act)


run()
app.exec()
print('saved to', out)
