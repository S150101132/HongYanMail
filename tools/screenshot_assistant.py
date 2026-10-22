"""Walk the demo build through the 个人工作助理 (W1–W4) and 操作类工具 (A3); save screenshots.

"有什么要我做" in the conversation (tasks with [tN] links and the task cards), a task
opened from its card in 任务, a question about the task brought along, then "这个任务已完成"
as a plan card and after 执行, then a reply to a recipient group drafted and sent; last, a task
without mail gets link candidates, is completed and the reply reminder offers a draft. Demo mail and
a small synthetic task list (the task tests' helpers), demo AI client, no network.

Usage: python tools/screenshot_assistant.py OUTDIR [light|dark] [width]
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

out = Path(sys.argv[1] if len(sys.argv) > 1 else 'analysis/screens-w1')
theme = sys.argv[2] if len(sys.argv) > 2 else 'light'
width = int(sys.argv[3]) if len(sys.argv) > 3 else 1440
out.mkdir(parents=True, exist_ok=True)

app = QApplication(sys.argv)
import atexit, shutil  # noqa: E401
shot_dir = tempfile.mkdtemp(prefix='hongyan-w1-', dir=str(ROOT / 'build'))
atexit.register(shutil.rmtree, shot_dir, True)
backend = MailBackend(shot_dir, adapter_factory=DemoAdapter)
backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'demo', True)
backend.set_prefs({'theme': theme})
window = shell.AppShell(backend, demo=True)
window.resize(width, 900)
window.show()


class _Cleanup:  # The task tests' folder helper wants a test case to clean up after.
    def addCleanup(self, fn, *args):
        atexit.register(fn, *args)


from tests.test_tasks import folder_with, task_row  # noqa: E402

TASKS = folder_with(_Cleanup(), open_rows=[
    task_row(12, '进行中', system='CRM', content='LDAP 对接：确认客户 AD 参数并完成联调', owner='用户本人', due='2026-10-19', next='等客户回复端口和 Base DN'),
    task_row(15, '受阻', system='BI', content='报表权限按部门拆分', owner='', due='2026-10-24', blockers='等待数据字典'),
    task_row(18, '未开始', system='OT', content='UAT 签收文档整理', owner='用户本人'),
    task_row(21, '进行中', system='CRM', content='短信网关切换', owner='张三')])
backend.tasks_set_folder(str(TASKS))
backend.tasks_import('live')


def demo_ai():
    backend.ai_client = DemoAiClient()
    backend.task_link(18, backend.messages('1', 0, 5)['rows'][0]['id'])  # W3 drafts from this task's conversation.
    backend.save_recipient_group({'name': '沙特项目组', 'members': [{'email': 'ali@customer.invalid', 'name': 'Ali', 'field': 'to'},
                                                                 {'email': 'wangfang@example.invalid', 'name': '王芳', 'field': 'cc'}]})


STEPS = [
    (2500, demo_ai, None),
    (300, "localStorage.setItem('hy-tk-ai', '1'); showAsk()", 'w1-empty'),  # The task center's AI consent, given once.
    (300, "$('#ask-input').value = '现在有什么任务，哪些需要我做？'; askMailbox()", None),
    (2500, "document.body.offsetWidth", 'w1-mine'),
    (300, "$('#ask-out .ask-task').click()", None),
    (1500, "document.body.offsetWidth", 'w1-task-open'),
    (300, "$('#chat-here').click()", None),
    (300, "$('#ask-input').value = '这个任务现在卡在哪？'; askMailbox()", None),
    (2500, "document.body.offsetWidth", 'w1-this-task'),
    # W2: a change proposed as a plan card, then 执行.
    (300, "$('#ask-input').value = '这个任务已完成'; askMailbox()", None),
    (3000, "document.body.offsetWidth", 'w2-plan'),
    (300, "$('[data-plan-act=\"run\"]').click()", None),
    (2500, "document.body.offsetWidth", 'w2-done'),
    # W3: a reply drafted from task #18's conversation, to a recipient group, then sent.
    (300, "openTask(18)", None),
    (1200, "$('#ask-input').value = '这个任务回给沙特项目组，问问具体信息'; askMailbox()", None),
    (3500, "document.body.offsetWidth", 'w3-card'),
    (300, "$('[data-plan-act=\"draft\"]').click()", None),
    (4000, "document.body.offsetWidth", 'w3-composer'),
    (300, "S.prefs.precheck = false; sendMail()", None),
    (2000, "document.body.offsetWidth", 'w3-sending'),
    (8000, "document.body.offsetWidth", 'w3-sent'),
    # W4: task #15 has no mail — candidates to link — then 已完成 and the reply reminder.
    (300, "newChat(); openTask(15)", None),
    (1200, "$('#ask-input').value = '这个任务回个邮件问问具体信息'; askMailbox()", None),
    (3500, "document.body.offsetWidth", 'w4-link'),
    (300, "$('[data-draft=\"link\"]').click()", None),
    (300, "$('[data-plan-act=\"link\"]').click()", None),
    (1500, "document.body.offsetWidth", 'w4-linked'),
    (300, "$('#ask-input').value = '这个任务已完成'; askMailbox()", None),
    (3000, "$$('[data-plan-act=\"run\"]').pop().click()", None),
    (3500, "document.body.offsetWidth", 'w4-nudge'),
    (300, "$('[data-plan-act=\"nudge-draft\"]').click()", None),
    (800, "document.body.offsetWidth", 'w4-nudge-draft'),
    # A3: a mail operation proposed on a card, run, then undone — from the inbox.
    (300, "newChat(); $('#app .sidebar a.folder').click(); showAsk()", None),
    (1200, "$('#ask-input').value = '把「评审」的邮件移到项目归档'; askMailbox()", None),
    (3000, "document.body.offsetWidth", 'a3-plan'),
    (300, "$('[data-plan-act=\"mail\"]').click()", None),
    (2000, "document.body.offsetWidth", 'a3-done'),
    (300, "$('[data-plan-act=\"undo\"]').click()", None),
    (2000, "document.body.offsetWidth", 'a3-undone'),
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
