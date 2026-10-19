"""Walk the demo build through the v0.6 fixes and save screenshots.

Deleting a folder that still holds mail (已删除 / 收件箱 / keep), the 标签 section folded
and unfolded, the contacts columns resized, and the attachment viewer for workbooks
(sheet tabs), Word, PowerPoint, HTML (remote pictures blocked), SVG, BMP and a legacy
.xls that falls back to 用系统中的程序打开.

Usage: python tools/screenshot_v06.py OUTDIR [light|dark] [width]
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
from coremail_desktop.app.demo import DemoAdapter  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else 'analysis/screens-v06')
theme = sys.argv[2] if len(sys.argv) > 2 else 'light'
width = int(sys.argv[3]) if len(sys.argv) > 3 else 1440
out.mkdir(parents=True, exist_ok=True)

app = QApplication(sys.argv)
import atexit, shutil  # noqa: E401
shot_dir = tempfile.mkdtemp(prefix='hongyan-v06-', dir=str(ROOT / 'build'))
atexit.register(shutil.rmtree, shot_dir, True)
backend = MailBackend(shot_dir, adapter_factory=DemoAdapter)
backend.open_file = lambda path: print('open_file', Path(path).name.encode('unicode_escape').decode('ascii'), flush=True)
backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'demo', True)
backend.set_prefs({'theme': theme})
backend.messages('1', 0, 50)
folder = backend.create_folder('旧项目')['id']
backend.move(['demo40', 'demo41', 'demo42'], folder)
window = shell.AppShell(backend, demo=True)
window.resize(width, 900)
window.show()

CLOSE = "$('#prompt').classList.add('hidden'); hideMenu()"
ATT = "PV.close(); PV.open(S.message, S.message.attachments.find((a) => a.name.endsWith(%r)))"
STEPS = [
    (2500, None, None),
    (600, "selectFolder('1')", 'tags-folded'),
    (500, "$('#tag-head').click()", 'tags-open'),
    (500, "$('#tag-head').click()", None),
    (900, "MG.deleteFolder(folderById(%r))" % folder, 'folder-delete-choices'),
    (500, "$('input[name=\"mg-mail\"][value=\"inbox\"]').checked = true; $('#prompt-ok').click()", None),
    (1500, "document.body.offsetWidth", 'folder-deleted-to-inbox'),
    (600, "openContacts()", None),
    (1200, "CT.open.add('d/rd'); openDepartment('d/rd-fe', '前端组')", None),
    (900, "$('#ct-list .ct-row').click()", 'contacts-default'),
    (700, "S.prefs.ctTreeWidth = 190; S.prefs.ctListWidth = 520; applyWidths()", 'contacts-resized'),
    (500, "$('#split-ct-tree').dispatchEvent(new MouseEvent('dblclick')); $('#split-ct-list').dispatchEvent(new MouseEvent('dblclick'))", 'contacts-reset'),
    (600, "selectFolder('1')", None),
    (1200, "selectMessage('demo1')", 'v06-attachments'),
    (1200, ATT % '.xlsx', 'preview-xlsx'),
    (700, "PV.showSheet(1)", 'preview-xlsx-sheet2'),
    (700, ATT % '.docx', 'preview-docx'),
    (700, "PV.zoomBy(1)", 'preview-docx-zoom'),
    (700, ATT % '.pptx', 'preview-pptx'),
    (700, ATT % '.html', 'preview-html'),
    (700, "PV.act('remote')", 'preview-html-remote'),
    (700, ATT % '.svg', 'preview-svg'),
    (700, ATT % '.bmp', 'preview-bmp'),
    (700, ATT % '.xls', 'preview-legacy-xls'),
    (500, "PV.act('open')", 'open-toast'),
    (700, "PV.close()", None),
]


def run(index=0):
    if index >= len(STEPS):
        app.quit()
        return
    delay, script, name = STEPS[index]

    def act():
        if os.environ.get('HONGYAN_WALK_TRACE'):
            print(f'step {index}: {name or "-"}', flush=True)
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
