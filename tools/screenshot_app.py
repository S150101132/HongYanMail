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
    (900, "renderList(); selectMessage(S.items[0].id)", 'threads'),
    (700, "openConversation(S.items.find((it) => it.ids.length > 2))", 'thread-open'),
    (700, "$$('#mail-list .row')[1].click()", 'thread-message'),
    (700, "closeConversation(); S.prefs.threads = false; renderThreadToggle(); renderList(); selectMessage(S.rows[1].id); "
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
    (900, "selectFolder('1@rd')", None),
    (1200, "selectMessage(S.rows[0].id)", 'shared'),
    (800, "startCompose('reply', S.selected)", 'shared-reply'),
    # Read-only view, drawn client-side only: the demo mailbox itself has full rights.
    (800, "closeComposer(true); renderMessage({ ...S.message, mailbox: { ...S.message.mailbox, rw: false, send: false } })", 'shared-readonly'),
    (900, "selectFolder('1')", None),
    (900, "$('#search').value = '演示'; $('#btn-search-filters').click()", None),
    (500, "$('#sp-attachment').checked = true; $('#sp-quick button[data-days=\"30\"]').click()", 'search-panel'),
    (600, "$('#sp-run').click()", None),
    (900, "selectMessage(S.rows[0].id)", 'search-filters'),
    (800, "openContacts()", None),
    (1200, "CT.open.add('d/rd'); openDepartment('d/rd-fe', '前端组')", None),
    (900, "$('#ct-list .ct-row').click()", 'contacts'),
    (700, "$('#ct-search').value = '李'; $('#ct-search').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))", 'contacts-search'),
    (900, "mailsWith({ name: '张晓明', email: 'zhangxm@example.invalid' })", None),
    (900, "selectMessage(S.rows[0].id)", 'contacts-mails'),
    (900, "S.message && writeFrame && 0; document.body.offsetWidth", None),
    # v0.4: meeting invitations, tags, rules and holiday auto-reply.
    (900, "selectFolder('1')", None),
    (1200, "selectMessage('demo10')", 'invite'),
    (700, "$('#iv-people').click()", 'invite-people'),
    (700, "$('[data-answer=\"accept\"]').click()", 'invite-confirm'),
    (900, "$('#prompt-ok').click()", 'invite-answered'),
    (900, "selectMessage('demo14')", 'invite-cancelled'),
    (900, "selectMessage('demo5')", None),
    (700, "TG.pick(['demo5'])", 'tag-picker'),
    (700, "$('#prompt').classList.add('hidden'); TG.open('x19a1f0c0001')", None),
    (1200, "selectMessage(S.rows[0].id)", 'tag-view'),
    (700, "openSettings(); RL.show('rules')", None),
    (900, "document.body.offsetWidth", 'settings-rules'),
    (700, "RL.edit(RL.rules[1])", 'rule-editor'),
    (700, "$('#prompt').classList.add('hidden'); RL.show('autoreply')", None),
    (900, "document.body.offsetWidth", 'settings-autoreply'),
    (700, "RL.show('tags')", None),
    (900, "document.body.offsetWidth", 'settings-tags'),
    (500, "$('#settings').classList.add('hidden')", None),
    # v0.4: resizable columns, print/export, templates, pop-out reader.
    (900, "selectFolder('1')", None),
    (1200, "S.prefs.sidebarWidth = 190; S.prefs.listWidth = 560; applyWidths(); selectMessage(S.rows[2].id)", 'resized'),
    (700, "S.prefs.sidebarWidth = 400; S.prefs.listWidth = 280; applyWidths()", 'resized-narrow-list'),
    (700, "$('#split-list').dispatchEvent(new MouseEvent('dblclick')); $('#split-side').dispatchEvent(new MouseEvent('dblclick'))", None),
    (700, "moreMenu($('#reader-body [data-act=more]'), S.message)", 'export-menu'),
    (500, "hideMenu(); exportEml({ ...S.message, encryption: 'decrypted' })", 'export-encrypted'),
    (500, "$('#prompt').classList.add('hidden'); api('saveTemplate', { name: '周报', recipients: true, value: { subject: '本周工作周报', "
          "html: '<p>各位好，</p><p>本周完成：</p><ul><li>…</li></ul><p>下周计划：</p>', to: ['rd@example.invalid'] } })"
          ".then(() => api('saveTemplate', { name: '会议通知', value: { subject: '会议通知：', html: '<p>时间：</p><p>地点：</p><p>议程：</p>' } }))", None),
    (700, "openSettings(); setTimeout(() => $('#tpl-section').scrollIntoView(), 100)", 'settings-templates'),
    (600, "$('#settings').classList.add('hidden'); startCompose('new')", None),
    (900, "templateMenu($('#cmp-template'))", 'template-menu'),
    (700, "hideMenu(); applyTemplate(S.templates.find((t) => t.name === '周报'))", 'template-applied'),
    (700, "closeComposer(true); $('#btn-compose-more').click()", 'compose-menu'),
    (500, "hideMenu(); newFromTemplate()", 'template-pick'),
    (500, "$('#prompt').classList.add('hidden'); selectMessage(S.rows[3].id)", None),
    (900, lambda: window.native_openReader('demo3'), None),
    (2500, None, 'popout', 'reader'),
    (600, lambda: render_pdf('demo3'), None),
    (2500, "S.message && writeFrame && 0; document.body.offsetWidth", None),
]


def render_pdf(mid):
    """The same hidden-view path as 导出为 PDF, written into the screenshot folder."""
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize
    target = out / f'export-{theme}.pdf'

    def ready(view, _ok):
        layout = QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait, QMarginsF(15, 15, 15, 15), QPageLayout.Unit.Millimeter)
        view.page().printToPdf(lambda data: (target.write_bytes(bytes(data)), print('pdf bytes', len(data)), window._print_done(view, bool(data), kind='pdf')), layout)
    window._render_print(mid, False, ready)


def run(index=0):
    if index >= len(STEPS):
        app.quit()
        return
    delay, script, name, *target = STEPS[index] + (None,)

    def act():
        if callable(script):
            script()
        elif script:
            window.page.runJavaScript(script)

        def snap():
            if name:
                shot = window.readers[-1] if target[0] == 'reader' and window.readers else window
                shot.grab().save(str(out / f'{name}-{theme}-{width}.png'))
            run(index + 1)
        QTimer.singleShot(700, snap)
    QTimer.singleShot(delay, act)


run()
app.exec()
print('saved to', out)
