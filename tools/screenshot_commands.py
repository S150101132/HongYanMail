"""Walk the demo build through the 命令 box (Ctrl+K) and save screenshots.

The box empty, a command by name, the commands for the open mail, search conditions read
from plain words and their search, and a question handed to 问邮箱 (demo client, no network).

Usage: python tools/screenshot_commands.py OUTDIR [light|dark] [width]
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

out = Path(sys.argv[1] if len(sys.argv) > 1 else 'analysis/screens-cmd')
theme = sys.argv[2] if len(sys.argv) > 2 else 'light'
width = int(sys.argv[3]) if len(sys.argv) > 3 else 1440
out.mkdir(parents=True, exist_ok=True)

app = QApplication(sys.argv)
import atexit, shutil  # noqa: E401
shot_dir = tempfile.mkdtemp(prefix='hongyan-cmd-', dir=str(ROOT / 'build'))
atexit.register(shutil.rmtree, shot_dir, True)
backend = MailBackend(shot_dir, adapter_factory=DemoAdapter)
backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'demo', True)
backend.set_prefs({'theme': theme})
window = shell.AppShell(backend, demo=True)
window.resize(width, 900)
window.show()


def typed(text):
    return f"$('#cp-input').value = {json.dumps(text, ensure_ascii=False)}; $('#cp-input').dispatchEvent(new Event('input'))"


def key(name):
    return f"$('#cp-input').dispatchEvent(new KeyboardEvent('keydown', {{ key: '{name}' }}))"


def demo_ai():
    backend.ai_client = DemoAiClient()


STEPS = [
    (2500, demo_ai, None),
    (300, "CP.open()", 'cmd-empty'),
    (300, typed('写'), 'cmd-name'),
    (300, typed('深色'), None),
    (300, key('Enter'), 'cmd-dark'),
    (300, f"applyTheme('{theme}'); api('setPrefs', {{ values: {{ theme: '{theme}' }} }})", None),  # Back to the walk's theme.
    (300, "CP.close(); $('#mail-list .row').click()", None),
    (1500, "CP.open()", 'cmd-this-mail'),
    (300, typed('张三发的 上周 带附件的报价'), 'cmd-conditions'),
    (300, typed('近 30 天 带附件'), None),
    (300, key('Enter'), None),
    (2000, "document.body.offsetWidth", 'cmd-search'),
    (300, "CP.open()", None),
    (300, typed('设计评审的结论是什么？'), 'cmd-question'),
    (300, key('Enter'), None),
    (2500, "document.body.offsetWidth", 'cmd-asked'),
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
        QTimer.singleShot(700, snap)
    QTimer.singleShot(delay, act)


run()
app.exec()
print('saved to', out)
