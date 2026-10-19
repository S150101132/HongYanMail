"""Walk the demo build through the v0.5 mail-management screens and save screenshots.

Folder menu and dialogs, sub-folders, deleting a folder that still holds mail,
全部标为已读, spam report and banner, permanent deletion (both confirmations),
清空, the sender lists in 设置, and the attachment preview (image, PDF, text).

Usage: python tools/screenshot_manage.py OUTDIR [light|dark] [width]
HONGYAN_WALK_TRACE=1 prints each step.
"""
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

out = Path(sys.argv[1] if len(sys.argv) > 1 else 'analysis/screens-v05')
theme = sys.argv[2] if len(sys.argv) > 2 else 'light'
width = int(sys.argv[3]) if len(sys.argv) > 3 else 1440
out.mkdir(parents=True, exist_ok=True)

app = QApplication(sys.argv)
import atexit, shutil  # noqa: E401
shot_dir = tempfile.mkdtemp(prefix='hongyan-manage-', dir=str(ROOT / 'build'))
atexit.register(shutil.rmtree, shot_dir, True)
backend = MailBackend(shot_dir, adapter_factory=DemoAdapter)
backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'demo', True)
backend.set_prefs({'theme': theme})
# A small tree: 客户往来 > 华东区 > 上海, and some mail in 华东区.
top = backend.create_folder('客户往来')['id']
east = backend.create_folder('华东区', top)['id']
backend.create_folder('上海', east)
backend.messages('1', 0, 50)
backend.move(['demo40', 'demo41', 'demo42'], east)
backend.move(['demo60', 'demo61', 'demo63', 'demo64'], '4')
window = shell.AppShell(backend, demo=True)
window.resize(width, 900)
window.show()


def ctx(selector, index=0):
    return (f"(() => {{ const a = document.querySelectorAll({selector!r})[{index}]; const r = a.getBoundingClientRect(); "
            "a.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: r.left + 40, clientY: r.top + r.height / 2 })); })()")


CLOSE = "$('#prompt').classList.add('hidden'); hideMenu()"
STEPS = [
    (2500, None, None),
    (600, "selectFolder('1')", None),
    (900, ctx('#folder-list .folder[data-id="%s"]' % top), 'folder-menu-user'),
    (500, "hideMenu();" + ctx('#folder-list .folder[data-id="4"]'), 'folder-menu-trash'),
    (500, "hideMenu(); MG.createFolder('%s')" % top, 'folder-create'),
    (500, "$('#mg-fname').value = 'a/b'; $('#prompt-ok').click()", 'folder-create-invalid'),
    (900, CLOSE + "; selectFolder('%s')" % east, 'folder-tree'),
    (900, "MG.deleteFolder(folderById('%s'))" % east, None),
    (400, "MG.deleteFolder(folderById('%s'))" % top, 'folder-delete-children'),
    (400, CLOSE + "; MG.deleteFolder(folderById('%s'))" % [f for f in backend.folders(False) if f['name'] == '上海'][0]['id'], 'folder-delete-empty'),
    (500, CLOSE + "; selectFolder('1')", None),
    (1200, "document.body.offsetWidth", 'list-allread'),
    (500, "MG.markAllRead('1')", None),
    (1200, "document.body.offsetWidth", 'list-allread-done'),
    (500, "selectMessage(S.rows[3].id)", None),
    (900, ctx('#mail-list .row', 3), 'row-menu-spam'),
    (500, "hideMenu(); MG.report([S.rows[3].id], true)", 'spam-report'),
    (500, "$('#mg-block').checked = true; $('#prompt-ok').click()", None),
    (900, "selectFolder('5')", None),
    (1200, "selectMessage(S.rows[0].id)", 'spam-folder'),
    (900, "selectFolder('4')", None),
    (1200, "selectMessage(S.rows[0].id)", None),
    (900, "deleteMessage(S.message.id)", 'purge-confirm-1'),
    (500, "$('#prompt-ok').click()", 'purge-confirm-2'),
    (500, "$('#prompt-ok').click()", 'purge-done'),
    (900, "toggleItem(S.rows[0].id); toggleItem(S.rows[1].id)", 'trash-batch'),
    (700, "clearSelection(false); $('#btn-empty').click()", 'empty-confirm-1'),
    (500, "$('#prompt-ok').click()", 'empty-confirm-2'),
    (500, CLOSE + "; openSettings(); RL.show('senders')", None),
    (900, "document.body.offsetWidth", 'settings-senders'),
    (500, "$('#settings').classList.add('hidden'); selectFolder('1')", None),
    (1200, "selectMessage('demo1')", 'reader-attachments'),
    (900, "PV.open(S.message, S.message.attachments.find((a) => a.name.endsWith('.png')))", 'preview-image'),
    (700, "PV.zoomBy(1); PV.zoomBy(1)", 'preview-image-zoom'),
    (700, "PV.close(); PV.open(S.message, S.message.attachments.find((a) => a.name.endsWith('.pdf')))", 'preview-pdf'),
    (900, "PV.go(1)", 'preview-pdf-page2'),
    (700, "PV.close(); PV.open(S.message, S.message.attachments.find((a) => a.name.endsWith('.txt')))", 'preview-text'),
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
