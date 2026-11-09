# F1 发后跟进收进后端（2026-11-08）

按[设计](../superpowers/specs/2026-11-08-after-send-design.md)：回复 / 转发被服务器收下后的三件事——「要我做」卡片标已回复 / 已转发、提议关闭关联任务、助理起草的邮件提议记进展——改由后端决定，随发送结果返回，页面只显示。来自第二轮 `/improve-codebase-architecture` 评审的候选 1。

- **`app/aftersend.py`**（新）：`after_send(draft, answered, tasks_for, progress)` → `{how, answered, tasks, plan}`。某一步出错只让那一项为空并记日志，不影响「已发出」。
- **草稿记住来源**：`DraftDocument` 加 `source_mid`（回复或转发的那封）、`assist_plan`（起草它的助理计划），随草稿存盘，撤销期满、离线补发、弹出窗口都拿得到。`assistant_draft_compose` 返回 `assistPlan`。
- **`_dispatch`**：只在 Accepted 时带 `after`；进离线发件箱（Queued）不带，补发成功时再带（`flushOutbox` 每项）。原来页面在 Queued 时就标卡片、提示。
- **没用过「任务」的账号**：发信不会建任务库，`tasks` 为空。
- **页面**：`tasks.js`、`understand.js`、`ai.js` 三处覆盖 `sendOutcome` 删掉，换成 `tkShowClose`、`ucShowAnswered`、`assistShowProgress` 三个显示函数；`compose.js` 的 `showAfter(after)` 调用它们，只在主窗口显示，弹出窗口用 `notifyMain('afterSend')` 转给主窗口（`POPOUT_EVENTS` 加 `afterSend`）。去掉 `S.assistDrafts` 和发送 `meta` 里的 `source` / `how`。
- **桥接**：`tasksForReply`、`understandAnswered`、`assistantSent` 不再对页面开放（后端自己调）。

## 验证

- `tests/test_aftersend.py`（9 个）：直接发送、转发、撤销期满、离线补发（Queued 时无 `after`，补发后有）、理解层关闭仍提示任务、新邮件无跟进、助理草稿经存盘仍提示进展且只一次、没用过任务不建任务库；页面不再覆盖 `sendOutcome`。前 8 个先红后绿；「不建任务库」是全量测试发现测试目录残留后补的，写时修复已在，没有先红。
- `test_assistant.py`、`test_today.py` 里查旧写法的源码字符串断言改为新写法。
- JS 用 `node --check` 查过语法。
- 全量 `unittest`：905 个通过（2 个跳过）。中间一次 `test_notes` 的 `test_mail_writes_at_once…` 失败一次，单独连跑 3 次和再次全量都通过，与本次改动无关的偶发。
- 没有在真窗口里点过：弹出写信窗口转给主窗口、三种提示的显示都只靠代码和源码断言，等真机验收。
