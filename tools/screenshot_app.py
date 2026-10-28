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
from coremail_desktop.ai.trending import demo_get  # noqa: E402
backend.trending_get = demo_get  # G1: never the real GitHub.
from coremail_desktop.app import news  # noqa: E402
backend.news_get = news.demo_get  # G2: nor 36氪.
from coremail_desktop.app.demo import DemoNetProbe  # noqa: E402
backend.net_probe = DemoNetProbe(backend)  # 网络检测 (N1): nor the real network.
backend.set_prefs({'theme': theme, 'signature': '<b>演示用户</b><br>研发中心 · 产品部<br><span style="color:#2563eb">demo@example.invalid</span>'})
window = shell.AppShell(backend, demo=True)
window.resize(width, 900)
window.show()

STEPS = [
    (2500, None, None),
    (600, "selectMessage(visibleRows()[0].id)", 'main'),
    # 精简工具栏 (A4): the folded buttons in ⋯, then the same screen with 完整.
    (600, "$('#reader-body [data-act=\"more\"]').click()", 'lean-more'),
    (500, "hideMenu(); document.documentElement.classList.remove('tb-lean')", 'full-main'),
    (300, "document.documentElement.classList.add('tb-lean')", None),
    (900, "startCompose('reply', S.selected)", None),
    (900, "(() => { const i=$('.chips[data-field=\"to\"] input'); i.value='zh'; i.dispatchEvent(new Event('input')); })()", 'composer'),
    (500, "$('#suggest').classList.add('hidden'); $('#cmp-format').click()", 'lean-format'),
    (500, "hideMenu(); $('#cmp-send-more').click()", 'lean-send'),
    (300, "hideMenu()", None),
    (700, "closeComposer(true); openSettings()", "settings"),
    (600, "$('#settings').classList.add('hidden'); startCompose('new')", "compose-new"),
    (600, "$('#settings').classList.add('hidden'); runSearch('预算')", 'search'),
    (600, "closeComposer(true); clearSearch(); S.prefs.threads = true; renderThreadToggle()", None),
    (900, "renderList(); selectMessage(S.items[0].id)", 'threads'),
    (900, "toggleThread(S.items.find((it) => it.ids.length > 2).id, true)", 'thread-open'),
    (700, "$$('#mail-list .row.child.mine')[0].click()", 'thread-message'),
    (700, "S.open.clear(); S.prefs.threads = false; renderThreadToggle(); renderList(); selectMessage(S.rows[1].id); "
          "toggleItem(S.rows[2].id); toggleItem(S.rows[4].id); toggleItem(S.rows[5].id)", 'batch'),
    (700, "batchMove(null, { delete: true })", 'batch-undo'),
    # Undo it, so later steps (tags, invitations) still find this mail outside the trash.
    (400, "[...document.querySelectorAll('#toasts .toast button')].filter((b) => b.textContent === '撤销').forEach((b) => b.click())", None),
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
    (900, "$$('#ct-list .ct-row')[1].click()", 'contacts-details'),
    (900, "CT.open.add('d/sales'); openDepartment('d/sales', '销售中心')", None),
    (700, "$$('#ct-list .ct-row')[2].click()", 'contacts-subtree'),
    (900, "openDepartment('d/sales-w', '西部区')", 'contacts-empty'),
    (700, "$('#ct-search').value = '李'; $('#ct-search').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))", 'contacts-search'),
    (900, "mailsWith({ name: '张晓明', email: 'zhangxm@example.invalid' })", None),
    (900, "selectMessage(S.rows[0].id)", 'contacts-mails'),
    (900, "$('#r-from-name').click()", 'reader-person'),
    (300, "closePersonPop()", None),
    (900, "S.message && writeFrame && 0; document.body.offsetWidth", None),
    # v0.4: meeting invitations, tags, rules and holiday auto-reply.
    (900, "selectFolder('1')", None),
    (1200, "selectMessage('demo10')", 'invite'),
    (700, "$('#iv-people').click()", 'invite-people'),
    (700, "$('[data-answer=\"accept\"]').click()", 'invite-confirm'),
    (900, "$('#prompt-ok').click()", 'invite-answered'),
    (900, "selectMessage('demo14')", 'invite-cancelled'),
    (900, "selectMessage('demo5')", None),
    (700, "openSettings(); RL.show('rules')", None),
    (900, "document.body.offsetWidth", 'settings-rules'),
    (700, "RL.edit(RL.rules[1])", 'rule-editor'),
    (700, "$('#prompt').classList.add('hidden'); RL.show('autoreply')", None),
    (900, "document.body.offsetWidth", 'settings-autoreply'),
    (700, "RL.show('tags')", None),
    (900, "document.body.offsetWidth", 'settings-tags'),
    (500, "$('#settings').classList.add('hidden')", None),
    # v0.4: resizable columns, print/export, pop-out reader.
    (900, "selectFolder('1')", None),
    (1200, "S.prefs.sidebarWidth = 190; S.prefs.listWidth = 560; applyWidths(); selectMessage(S.rows[2].id)", 'resized'),
    (700, "S.prefs.sidebarWidth = 400; S.prefs.listWidth = 280; applyWidths()", 'resized-narrow-list'),
    (700, "$('#split-list').dispatchEvent(new MouseEvent('dblclick')); $('#split-side').dispatchEvent(new MouseEvent('dblclick'))", None),
    (700, "moreMenu($('#reader-body [data-act=more]'), S.message)", 'export-menu'),
    (500, "hideMenu(); selectMessage(S.rows[3].id)", None),
    (900, lambda: window.native_openReader('demo3'), None),
    (2500, None, 'popout', 'reader'),
    (600, lambda: render_pdf('demo3'), None),
    (2500, "S.message && writeFrame && 0; document.body.offsetWidth", None),
]
# v0.5 compose and send (HONGYAN_WALK_V05=1 runs only these).
V05 = len(STEPS)
SHOT_PNG = ("(() => { const c = document.createElement('canvas'); c.width = 280; c.height = 110; const g = c.getContext('2d');"
            " g.fillStyle = '#e8efff'; g.fillRect(0, 0, 280, 110); g.strokeStyle = '#2563eb'; g.strokeRect(1, 1, 278, 108);"
            " g.fillStyle = '#1d4ed8'; g.font = '18px Microsoft YaHei'; g.fillText('粘贴的截图（演示）', 24, 62);"
            " insertImageData([{ name: '截图.png', type: 'image/png', size: 2000, data: c.toDataURL().split(',')[1] }]); })()")
STEPS += [
    (900, "api('closeReaders'); selectFolder('1')", None),
    (900, "startCompose('new', null, { to: ['partner@external.example'], subject: '本周进展（含表格和截图）' })", None),
    (700, "$('#cmp-editor').focus(); document.execCommand('insertHTML', false, '<p>各位好，本周进展如下：</p>' + tableHtml(3, 3));"
          " $$('#cmp-editor td').forEach((td, i) => { td.textContent = ['事项', '负责人', '截止', '接口联调', '张晓明', '10-22', '验收', '李华', '10-24'][i]; });"
          " const s = getSelection(); s.selectAllChildren($('#cmp-editor')); s.collapseToEnd();", None),
    (700, SHOT_PNG, None),
    (600, "(() => { const td = $$('#cmp-editor td')[4]; const r = td.getBoundingClientRect(); caretIn(td); tableMenu(r.left + 20, r.bottom, td); })()", 'compose-table'),
    (600, "hideMenu(); tablePicker($('#cmp-table')); const cell = $('#tbl-picker [data-r=\"3\"][data-c=\"4\"]'); cell.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))", 'table-picker'),
    (500, "$('#tbl-picker').classList.add('hidden'); sendMail()", None),
    (1200, "document.body.offsetWidth", 'undo-send'),
    (600, "$('.toast.hold button').click()", None),
    (900, "document.body.offsetWidth", 'undo-restored'),
    (300, lambda: (setattr(backend.adapter, 'offline', True), backend.set_prefs({'undoSeconds': 0})), None),
    (600, "sendMail()", None),
    (1200, "document.body.offsetWidth", 'offline-queued'),
    (600, "$('#nav-outbox').click()", None),
    (900, "document.body.offsetWidth", 'outbox'),
    (300, lambda: (setattr(backend.adapter, 'offline', False), backend.set_prefs({'undoSeconds': 10})), None),
    (600, "selectFolder('2')", None),
    (1200, "selectMessage('demo-draft0')", 'server-draft'),
    (900, "editServerDraft('demo-draft0')", 'server-draft-edit'),
    (700, "closeComposer(true); openSettings(); setTimeout(() => $('#pref-undo').scrollIntoView({ block: 'center' }), 100)", 'settings-undo'),
    (600, "$('#settings').classList.add('hidden'); selectFolder('1')", None),
    (1200, "selectMessage(S.rows[0].id)", None),
    (900, "startCompose('forward_attach', S.selected)", 'forward-attach'),
    (600, "closeComposer(true); $('#nav-outbox').click()", None),
    (900, "flushOutboxNow()", 'outbox-flushed'),
    (700, lambda: window.native_openReader(backend.import_eml(backend._raw('demo3'))), None),
    (2500, None, 'eml-reader', 'reader'),
]
# v0.7 signature logo, recipient groups, @mentions (HONGYAN_WALK_V07=1 runs only these).
V07 = len(STEPS)
LOGO_PNG = ("(() => { const c = document.createElement('canvas'); c.width = 720; c.height = 180; const g = c.getContext('2d');"
            " g.fillStyle = '#ffffff'; g.fillRect(0, 0, 720, 180); g.fillStyle = '#1d4ed8'; g.beginPath(); g.arc(90, 90, 62, 0, 7); g.fill();"
            " g.fillStyle = '#ffffff'; g.font = 'bold 64px Microsoft YaHei'; g.fillText('雁', 58, 112);"
            " g.fillStyle = '#1d4ed8'; g.font = 'bold 66px Microsoft YaHei'; g.fillText('演示公司', 180, 112);"
            " g.fillStyle = '#64748b'; g.font = '26px Microsoft YaHei'; g.fillText('DEMO CORPORATION', 184, 152);"
            " const ed = $('#pref-signature'); ed.focus(); const s = getSelection(); s.selectAllChildren(ed); s.collapseToStart();"
            " insertSignatureImages([{ name: '公司LOGO.png', type: 'image/png', size: 9000, data: c.toDataURL().split(',')[1] }], s.getRangeAt(0));"
            " document.execCommand('insertHTML', false, '<br>'); })()")
TYPE_AT = ("(() => { const ed = $('#cmp-editor'); ed.focus(); const s = getSelection(); s.selectAllChildren(ed.firstChild || ed); s.collapseToStart();"
           " document.execCommand('insertText', false, '请 '); document.execCommand('insertText', false, '@李'); })()")
STEPS += [
    (900, "api('closeReaders'); selectFolder('1')", None),
    (900, "openSettings()", None),
    (700, LOGO_PNG, None),
    (700, "$('#pref-signature img').click(); $('#pref-signature').scrollIntoView({ block: 'center' })", 'signature-logo'),
    (500, "$('#btn-save-settings').click()", None),
    (900, lambda: (backend.set_prefs({'undoSeconds': 0}),
                   backend.save_recipient_group({'name': '项目周会', 'members': [
                       {'name': '张晓明', 'email': 'zhangxm@example.invalid', 'field': 'to'},
                       {'name': '李华', 'email': 'lihua@example.invalid', 'field': 'to'},
                       {'name': '产品协作组', 'email': 'product@example.invalid', 'field': 'cc'}]}),
                   backend.save_recipient_group({'name': '前端组', 'members': [
                       {'name': '张晓明', 'email': 'zhangxm@example.invalid', 'field': 'cc'},
                       {'name': '王芳', 'email': 'wangfang@example.invalid', 'field': 'to'},
                       {'name': '赵磊', 'email': 'zhaolei@example.invalid', 'field': 'cc'}]})), None),
    (600, "startCompose('new', null, { subject: '项目周会纪要' })", 'compose-signature'),
    (700, "groupMenu($('#cmp-groups'))", 'group-menu'),
    (700, "hideMenu(); applyGroup(S.groups.find((g) => g.name === '项目周会'))", None),
    (700, "applyGroup(S.groups.find((g) => g.name === '前端组'))", 'groups-applied'),
    (700, TYPE_AT, None),
    (1000, "document.body.offsetWidth", 'mention-suggest'),
    (600, "pickSuggest(sug.items.findIndex((p) => p.email === 'lihua@example.invalid'))", None),
    (600, "document.execCommand('insertText', false, '确认接口时间；'); $('#cmp-editor').focus()", None),
    (600, TYPE_AT.replace('@李', '@陈'), None),
    (1000, "pickSuggest(0)", 'mention-inserted'),
    (700, "(() => { const m = $$('#cmp-editor .hy-mention').pop(); const r = document.createRange(); r.selectNode(m); const s = getSelection(); s.removeAllRanges(); s.addRange(r); document.execCommand('delete'); })()", None),
    (900, "document.body.offsetWidth", 'mention-removed-ask'),
    (600, "$('#prompt-ok').click()", None),
    (600, "C.encrypt = false; C.sign = false; renderEncrypt(); sendMail()", None),
    (1200, "selectFolder('3')", None),
    (1200, "selectMessage(S.rows[0].id)", 'sent-logo-mention'),
    (700, "groupFromMessage(S.message)", None),
    (900, "document.body.offsetWidth", 'group-from-message'),
    (600, "$('#grp-name').value = '周会纪要收件人'; $('#grp-name').dispatchEvent(new Event('input')); $('#prompt-ok').click()", None),
    (900, "openContacts(); showGroups(); setTimeout(() => showGroup(S.groups[0] && S.groups[0].id), 300)", 'contacts-groups'),
    (700, "editGroup(S.groups.find((g) => g.name === '项目周会')); setTimeout(() => { const i = $('#grp-input'); i.value = '王'; i.dispatchEvent(new Event('input')); }, 100)", None),
    (1000, "document.body.offsetWidth", 'group-editor'),
    (500, "hideSuggest(); $('#prompt').classList.add('hidden'); selectFolder('1')", None),
]
# v1.0 AI: 会话摘要, AI 回复, 写信助手, 发送前检查 (HONGYAN_WALK_V10=1 runs only these; demo AI, no network).
V10 = len(STEPS)
FIND_THREAD = "selectMessage(S.rows.find((r) => r.subject.includes('接口联调')).id)"
STEPS += [
    (900, lambda: (setattr(backend, 'ai_client', __import__('coremail_desktop.app.demo', fromlist=['DemoAiClient']).DemoAiClient()),
                   backend.set_prefs({'undoSeconds': 0})), None),
    (600, "api('closeReaders'); selectFolder('1')", None),
    (1200, FIND_THREAD, None),
    (700, "aiMenu($('#reader-body [data-act=summarize]'), S.message)", 'ai-menu'),
    (500, "hideMenu(); summarizeThread(S.message)", None),
    (3500, "document.body.offsetWidth", 'ai-thread'),
    (500, "replyAssist(S.message)", None),
    (1500, "document.body.offsetWidth", 'ai-reply-ideas'),
    (500, "$('.ai-idea').click()", None),
    (1200, "document.body.offsetWidth", 'ai-reply-streaming'),
    (2500, "document.body.offsetWidth", 'ai-reply-draft'),
    (600, "closeComposer(true); startCompose('new', null, { to: ['李华 <lihua@example.invalid>'], subject: '接口联调安排' })", None),
    (900, "$('#cmp-editor').focus(); const s = getSelection(); s.selectAllChildren($('#cmp-editor').firstChild); s.collapseToStart();"
          " document.execCommand('insertText', false, '1. 联调10月15日前完成'); document.execCommand('insertParagraph');"
          " document.execCommand('insertText', false, '2. 有问题可以联系 vendor@partner.example')", None),
    (600, "localStorage.setItem('hy-ai-write', '1'); $('#cmp-ai').click()", None),
    (2500, "document.body.offsetWidth", 'ai-write'),
    (500, "$('#cmp-ai-want').value = '更正式'; $('[data-cai=revise]').click()", None),
    (2500, "document.body.offsetWidth", 'ai-write-revised'),
    (500, "$('[data-cai=replace]').click()", None),
    (700, "document.body.offsetWidth", 'ai-write-replaced'),
    (500, "C.encrypt = false; C.sign = false; renderEncrypt(); sendMail()", None),
    (1200, "document.body.offsetWidth", 'precheck'),
    (500, "$('#prompt').classList.add('hidden'); closeComposer(true)", None),
]
# 理解层 M1: 要点, 待我处理 / 需要知道, the reader's strip, corrections (HONGYAN_WALK_U1=1 runs only these; demo AI).
U1 = len(STEPS)
STEPS += [
    (900, lambda: (setattr(backend, 'ai_client', __import__('coremail_desktop.app.demo', fromlist=['DemoAiClient']).DemoAiClient()),
                   backend.set_understand('on')), None),
    (600, "S.prefs.understand = 'on'; S.prefs.memoryFacts = true; api('closeReaders'); selectFolder('1'); renderFolders(); ucRound()", None),
    (9000, "document.body.offsetWidth", 'uc-list'),
    (500, "selectMessage(S.rows.find((r) => (UC.cards.get(r.id) || {}).kind === 'act' && (UC.cards.get(r.id) || {}).due).id)", 'uc-reader-act'),
    (500, "selectMessage(S.rows.find((r) => (UC.cards.get(r.id) || {}).kind === 'know').id)", 'uc-reader-know'),
    (500, "selectFolder('1')", None),
    (900, "const r = $$('#mail-list .row')[1].getBoundingClientRect(); showMenu(r.left + 80, r.top + 20, UC.menu(rowOf($$('#mail-list .row')[1].dataset.id)))", 'uc-menu'),
    (500, "hideMenu(); ucRuleDialog({ sender: 'rd@example.invalid', subject: '研发周报 · 第 42 周' }, 'know')", 'uc-rule'),
    (500, "$('#prompt').classList.add('hidden'); openSettings(); $('#st-nav [data-tab=ai]').click(); setTimeout(() => $('#uc-on').scrollIntoView({ block: 'center' }), 300)", None),
    (1200, "document.body.offsetWidth", 'uc-settings'),
    (500, "$('#settings').classList.add('hidden'); S.prefs.understand = ''; ucAsk()", None),
    (900, "document.body.offsetWidth", 'uc-ask'),
]
# Reader security icons: decrypted + valid signature beside the date, a warning still as a banner (HONGYAN_WALK_SEC=1 runs only these).
SEC = len(STEPS)
SIG_OK = "{ status: 'valid', issuer: 'INSPUR-CA', directory: true, subject: 'CN=演示', digest: 'sha256' }"
STEPS += [
    (2500, "selectFolder('1')", None),
    (1200, "selectMessage(S.rows[0].id)", None),
    (900, "renderMessage({ ...S.message, encryption: 'decrypted', signature: " + SIG_OK + " })", 'sec-icons'),
    (700, "renderMessage({ ...S.message, encryption: 'decrypted', signature: { ...(" + SIG_OK + "), timeValid: false } })", 'sec-warn'),
]

# 工作记忆 M2, 本周需要知道 / 等对方回复 / 人物卡片 M3, 任务关系 P1 (HONGYAN_WALK_M2=1 runs only these; demo AI).
M2 = len(STEPS)


def m2_setup():
    import time
    from coremail_desktop.app.demo import DemoAiClient
    from tests.test_tasks import book, task_row
    folder = Path(shot_dir) / '任务'
    folder.mkdir(exist_ok=True)
    rows = [task_row(401, '进行中', system='B/OSS', content='NG BOSS 部署上线', start='2026-10-08', due='2026-11-06'),
            task_row(402, '进行中', system='BI', content='服务器到货与上架', start='2026-10-10', due='2026-10-20', blockers='上级 #401'),
            task_row(403, '受阻', system='BI', content='安装与接口联调', start='2026-10-21', due='2026-10-30', blockers='上级 #401；依赖 #402'),
            task_row(404, '未开始', system='BI', content='用户培训', due='2026-11-04', blockers='上级 #401；依赖 #403'),
            task_row(405, '进行中', system='CRM', content='CRM 接口对接', start='2026-10-01', due='2026-10-27')]
    (folder / '未完成任务.xlsx').write_bytes(book({'未完成任务': rows}))
    (folder / '已完成任务.xlsx').write_bytes(book({'已完成任务': [task_row(390, '已完成', system='BI', content='需求调研', start='2026-09-20', end='2026-10-05', blockers='上级 #401')],
                                                '已取消任务': []}))
    backend.tasks_set_folder(str(folder))
    backend.tasks_import('trial')
    backend.ai_client = DemoAiClient()
    backend.set_prefs({'undoSeconds': 0})
    backend.set_understand('on')
    backend.set_prefs({'memoryFacts': True})  # R3c: off by default; the walk shows the project memory.
    backend.memory_project('B/OSS', rename='NG BOSS')
    backend.memory_project('BI', parent='NG BOSS')
    backend.memory_project('CRM', parent='NG BOSS')
    backend.messages('3', 0, 50)
    sent = next(r for r in backend.cache.matching(lambda r: r['fid'] == '3'))
    backend.cache.update(sent['id'], ts=time.time() - 5 * 86400, subject='请确认接口文档 v2 的字段', to='"李华" <lihua@example.invalid>', preview='请在周五前确认')


KNOW_MAIL = "selectMessage(S.rows.find((r) => (UC.cards.get(r.id) || {}).kind === 'know' && (UC.cards.get(r.id) || {}).project).id)"
STEPS += [
    (900, m2_setup, None),
    (600, "S.prefs.understand = 'on'; S.prefs.memoryFacts = true; api('closeReaders'); selectFolder('1'); renderFolders(); ucRound()", None),
    (12000, "ucRound()", None),
    (12000, "UC.cards.clear(); memReset(); renderList()", None),
    (1500, KNOW_MAIL, 'm2-reader'),
    (900, "memTimeline(UC.cards.get(S.message.id).project)", 'm2-timeline'),
    (500, "$('#prompt [data-close]').click(); MEM.weekOpen = true; MEM.week = 1; showToday('week')", None),
    (1500, "document.body.offsetWidth", 'm3-week'),  # The AI brief of the week arrives after the facts.
    (900, "ucOpen('wait')", None),
    (900, "selectMessage(S.rows[0].id)", 'm3-wait'),
    (500, "selectFolder('1')", None),
    (1200, KNOW_MAIL, None),
    (900, "$('#r-from-name').click()", 'm3-person'),
    (500, "closePersonPop(); openSettings(); $('#st-nav [data-tab=ai]').click(); setTimeout(() => $('#mem-projects').scrollIntoView({ block: 'center' }), 300)", None),
    (1200, "document.body.offsetWidth", 'm2-settings'),
    (1200, "$('#settings').classList.add('hidden'); openTasks()", None),
    (900, "showTask(403)", 'p1-relations'),
]

# R1/R2: 今天, the task card, 在等别人, 本周概括, the one strip, the task list's filter and the one
# assistant (HONGYAN_WALK_R2=1 runs only these; demo AI).
R2 = len(STEPS)
STEPS += [
    (900, m2_setup, None),
    (600, "S.prefs.understand = 'on'; S.prefs.memoryFacts = true; api('closeReaders'); selectFolder('1'); renderFolders(); ucRound()", None),
    (12000, "ucRound()", None),
    (12000, "UC.cards.clear(); memReset(); showToday()", None),
    (1500, "document.body.offsetWidth", 'g2-daily'),  # G2: 每日速览, a page of its own.
    (500, "$('#daily-view .dv-story').click()", 'g2-daily-open'),
    (900, "showAsk()", 'r2-ask-today'),
    (500, "hideAsk(); showToday('wait')", 'r2-wait'),
    (500, "$('#search').value = 'hongyan-walk-no-match'; $('#search').dispatchEvent(new Event('input'))", 'r3-search-from-today'),  # V1
    (500, "clearSearch(); $('#search').value = ''", None),
    (900, "MEM.week = 0; showToday('week')", 'r2-week'),
    (1500, "showToday('confirm')", 'r2-confirm'),
    (900, "selectFolder('1')", None),
    (1200, KNOW_MAIL, 'r2-strip'),
    (700, "$('#uc-strip .uc-project').click()", 'r2-strip-open'),
    (900, "$('#uc-strip .uc-project').click(); openTasks()", None),
    (1200, "showTask(403)", 'r2-tasks'),
    (900, "$('#tk-ask').click()", 'r2-ask-tasks'),
]

# 界面打磨：the five daily screens plus a menu and a toast, before/after (HONGYAN_WALK_POLISH=1 runs only these).
POLISH = len(STEPS)
STEPS += [
    (900, m2_setup, None),
    (600, "S.prefs.understand = 'on'; api('closeReaders'); selectFolder('1'); renderFolders(); ucRound()", None),
    (12000, "UC.cards.clear(); renderList()", None),
    (1500, "selectMessage(S.rows[1].id)", 'p-inbox'),
    (600, "moreMenu($('#reader-body [data-act=more]'), S.message)", 'p-menu'),
    (500, "hideMenu(); startCompose('reply', S.selected)", None),
    (900, "document.body.offsetWidth", 'p-compose'),
    (500, "closeComposer(true); showToday()", None),
    (1500, "document.body.offsetWidth", 'p-today'),
    (900, "openTasks()", None),
    (1200, "showTask(403)", 'p-tasks'),
    (700, "selectFolder('1')", None),
    (1200, "selectMessage(S.rows[0].id); batchMove(null, { delete: true })", 'p-toast'),
    (500, "[...document.querySelectorAll('#toasts .toast button')].filter((b) => b.textContent === '撤销').forEach((b) => b.click()); openSettings()", None),
    (900, "document.body.offsetWidth", 'p-settings'),
]

# 收信规则重新设计 (R5): list, editor with chips and suggestions, run on existing mail, from a mail (HONGYAN_WALK_R5=1).
CLOSE_PROMPT = "$$('#prompt [data-close]')[0].click()"
R5 = len(STEPS)
STEPS += [
    (2500, "selectFolder('1')", None),
    (900, "openSettings('rules')", None),
    (1200, "document.body.offsetWidth", 'r5-list'),
    (500, "RL.edit(RL.rules[0])", None),
    (900, "document.body.offsetWidth", 'r5-edit'),
    (300, "(() => { const i = $$('#prompt .rl-chips input')[0]; i.focus(); i.value = 'zh'; i.dispatchEvent(new Event('input')); })()", None),
    (900, "document.body.offsetWidth", 'r5-suggest'),
    (300, f"hideSuggest(); {CLOSE_PROMPT}", None),
    (500, "RL.offerApply(RL.rules[0], true)", None),
    (1200, "document.body.offsetWidth", 'r5-apply'),
    (300, f"{CLOSE_PROMPT}; $('#settings').classList.add('hidden'); selectMessage(S.rows[2].id)", None),
    (1200, "ruleFromMail(S.message)", None),
    (1200, "document.body.offsetWidth", 'r5-from-mail'),
    (300, "$('#prompt [data-pick=new]').click()", None),
    (1200, "document.body.offsetWidth", 'r5-new-from-mail'),
    (300, "$('#prompt-ok').click()", None),
    (1500, "document.body.offsetWidth", 'r5-saved-offer'),
    (300, "$('#prompt-ok').click()", None),
    (1500, "document.body.offsetWidth", 'r5-applied'),
    (300, "openSettings('rules')", None),
    (1200, "document.body.offsetWidth", 'r5-list-after'),
]

# 网络检测 (N1): server unreachable -> the offline bar says which link failed (HONGYAN_WALK_N1=1).
N1 = len(STEPS)
STEPS += [
    (2500, "selectFolder('1')", None),
    (300, lambda: setattr(backend.adapter, 'offline', True), None),
    (300, "loadList(true)", None),
    (1500, "document.body.offsetWidth", 'n1-offline'),
    (300, lambda: setattr(backend.adapter, 'offline', False), None),
    (300, "$('.net-again') && $('.net-again').click()", None),
    (1200, "document.body.offsetWidth", 'n1-online'),
]
# X1 本机只读 MCP: the setting off and on (HONGYAN_WALK_X1=1 runs only these).
X1 = len(STEPS)
STEPS += [
    (2500, "openSettings(); $('#st-nav [data-tab=ai]').click(); setTimeout(() => $('#mcp-on').scrollIntoView({ block: 'center' }), 300)", None),
    (1200, "document.body.offsetWidth", 'x1-off'),
    (300, "$('#mcp-on').click()", None),
    (1200, "document.body.offsetWidth", 'x1-on'),
    (300, "$('#mcp-on').click()", None),
    (900, "$('#settings').classList.add('hidden')", None),
]
# AI 模型下拉: the service's list, then 「手动填写…」 (HONGYAN_WALK_AM=1 runs only these).
AM = len(STEPS)
STEPS += [
    (2500, "openSettings(); $('#st-nav [data-tab=ai]').click()", None),
    (1200, "document.body.offsetWidth", 'am-list'),
    (300, "const l = $('#ai-model-list'); l.value = ''; l.dispatchEvent(new Event('change'))", None),
    (900, "document.body.offsetWidth", 'am-manual'),
    (300, "$('#settings').classList.add('hidden')", None),
]
# 关于我 (Me1): the empty page, the AI's first draft to review, the kept lines (HONGYAN_WALK_ME=1; demo AI).
ME = len(STEPS)
STEPS += [
    (2500, lambda: (setattr(backend, 'ai_client', __import__('coremail_desktop.app.demo', fromlist=['DemoAiClient']).DemoAiClient()),
                    backend.memory_project('NG BI'), backend.memory_project('SAP', role='watch')), None),
    (300, "openSettings(); $('#st-nav [data-tab=me]').click()", None),
    (1200, "document.body.offsetWidth", 'me-empty'),
    (300, "$('#me-draft').click()", 'me-busy'),
    (5000, "document.body.offsetWidth", 'me-draft'),
    (300, "$$('[data-mi]')[1].checked = false; const t = $$('[data-mt]')[0]; t.value = t.value.replace('演示：', ''); $('#prompt-ok').click()", None),
    (1500, "document.body.offsetWidth", 'me-kept'),
    (300, "$('[data-me-add=ways]').click()", None),
    (700, "$('#me-text').value = 'DBA、网络、AD 的专业操作我只协调，不亲自做'", 'me-add'),
    (300, "$('#prompt-ok').click()", None),
    (1200, "$('#me-ways').scrollIntoView()", 'me-saved'),
    (300, "$('#settings').classList.add('hidden')", None),
    # Me2: a mail's proposal waits on top (鸿雁想记住), 记住 keeps it; 告诉鸿雁为什么 from the reading strip.
    (300, lambda: me2_propose(), None),
    (300, "openSettings(); $('#st-nav [data-tab=me]').click()", None),
    (1200, "document.body.offsetWidth", 'me2-pending'),
    (300, "$('#me-pending [data-me-act=keep]').click()", None),
    (1200, "document.body.offsetWidth", 'me2-kept'),
    (300, "$('[data-me-all=people]').click()", None),
    (700, "$('#me-bulk').scrollIntoView({ block: 'nearest' })", 'me-bulk'),
    (300, "$('#me-bulk-none').click()", None),
    (300, "$('#settings').classList.add('hidden'); UC.cards.clear(); selectMessage(window._me2)", None),
    (1500, "$('#uc-strip [data-uc=menu]').click()", 'me2-menu'),
    (500, "hideMenu(); meWhy(window._me2, UC.cards.get(window._me2))", None),
    (700, "$('#me-why').value = '联调时间类邮件我只需确认一个时间，不用写长回复'", 'me2-why'),
    (300, "$('#prompt-ok').click()", None),
    (900, "document.body.offsetWidth", None),
]


def me2_propose():
    """理解新邮件 on one 联调 mail with the demo AI: its card proposes a 关于我 line."""
    backend.set_understand('on')
    mid = next(r['id'] for r in backend.cache.summaries('1', 0, 300)[0] if '联调' in (r.get('subject') or ''))
    backend.understand_fetch([mid])
    backend.understand_mail(mid)
    window.page.runJavaScript(f"window._me2 = '{mid}'; S.prefs.understand = 'on'")


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
        if os.environ.get('HONGYAN_WALK_TRACE'):
            print(f'step {index}: {name or "-"}', flush=True)
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


run(ME if os.environ.get("HONGYAN_WALK_ME") else AM if os.environ.get("HONGYAN_WALK_AM") else X1 if os.environ.get('HONGYAN_WALK_X1') else N1 if os.environ.get('HONGYAN_WALK_N1') else R5 if os.environ.get('HONGYAN_WALK_R5') else POLISH if os.environ.get('HONGYAN_WALK_POLISH') else R2 if os.environ.get('HONGYAN_WALK_R2') else M2 if os.environ.get('HONGYAN_WALK_M2') else SEC if os.environ.get('HONGYAN_WALK_SEC') else U1 if os.environ.get('HONGYAN_WALK_U1') else V10 if os.environ.get('HONGYAN_WALK_V10') else V07 if os.environ.get('HONGYAN_WALK_V07') else V05 if os.environ.get('HONGYAN_WALK_V05') else 0)
app.exec()
print('saved to', out)
