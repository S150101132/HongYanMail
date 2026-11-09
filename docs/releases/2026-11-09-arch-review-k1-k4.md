# K1–K4 第二轮架构评审的其余四项（2026-11-09）

按[设计](../superpowers/specs/2026-11-09-arch-review-2-design.md)，第二轮 `/improve-codebase-architecture` 评审的候选 2–5（候选 1 是 [F1](2026-11-08-after-send-f1.md)）。四项各自先写失败的测试再改，各自提交。

- **K1 服务器 seam**（`4304553`）：新 `models.MessagePage`，`list_messages` 的结果自带文件夹总数；两个 adapter 去掉 `last_folder_total` / `last_folder_id`，后端从返回值取，另一条线程同时列别的文件夹也不会串号（原来会让总数变成空）。高级搜索过滤 `search_filters` / `filter_match` 移到 `app/searchfilter.py`，演示服务器不再 import 后端；后端仍可导入原名。`tools/verify/verify_rules_v2_real.py` 改读 `.total`。
- **K2 会话**（`86a57d8`）：新 `app/conversation.py`：`conversation_key`（主题归一，不到 6 个字为空、自成一个会话）、`skip_folders`、`THREAD_SKIP`。会话展开、会话计数、关联任务、在等别人都用它。**行为变化**：在等别人原来 4 个字就算同一会话、会话列表原来不限，现在都是 6 个字；页面 `threadKey` 同样处理（短主题的邮件在列表里不再归成一组）。`GLOSSARY.md` 加「会话」。
- **K3 交给 AI**（`e5ab1dd`）：新 `ai/consent.py` 的 `access(state, allowed, auto_ok)` → `ok` / `locked` / `ask`。后端 `_ai_access` / `_ai_gate`、问鸿雁的 `read_mail` 都用它；`_task_mail` 照抄的判断、总结缓存、会话总结的加密计数、理解层和资料整理的筛选、阅读栏的 `aiAllowed` 都改走这一处。规则不变。
- **K4 错误文字**：后端不再导入 `ui.messages`，只返回代码字段（`failureCode`、`serverErrorCode`、`unavailableCode`、`errorCode`、`code`、计划预览项的 `error`）。`ui/messages.with_text` 按代码补上原来的文字字段（`failureMessage`、`serverError`、`unavailable`、`error`、`message`），已有文字的不动、不像代码的不动；`shell.py` 对每个后端结果和 native 结果调用它，本机 MCP 服务对工具结果调用它。页面读的字段名和显示的文字不变。

## 验证

- `tests/test_k_seams.py`（14 个）：列表自带总数、别的文件夹的列表不影响总数、演示服务器不依赖后端；短主题自成会话、后端一份跳过列表、node 里核对页面和后端的会话键一致；`access` 的五种情况、后端和工具里没有别的副本；`with_text` 补文字、离线排队没有失败文字、后端不导入 `ui.messages`。都先红后绿（K1 的第一个用例一开始期望写错了：一页最多 100 封，改为比较整个文件夹）。
- 原来直接读 `last_folder_*` 的测试（`test_desktop_server_semantics`、`test_shared_mailbox`）和直接读后端文字的测试（`test_assistant`、`test_tasks`、`test_news`、`test_today`）改为读 `.total` / 经过 `with_text`。
- 全量 `unittest`：918 个通过（2 个跳过）。K3 中间一次全量里 `test_attachment_viewer` 有一个用例失败（xlsx 压缩包时间戳差一个字节），单独连跑两次和再次全量都通过，是偶发。
- 没有在真窗口里看过。真机验收：打开收件箱看总数；规则「对已有邮件执行」；展开一个会话、看在等别人；对加密邮件做总结 / 转为任务 / 问鸿雁（照旧先问）；断网保存服务器草稿、断网发信，看提示文字。
