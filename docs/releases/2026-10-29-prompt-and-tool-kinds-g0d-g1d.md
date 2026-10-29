# G0d · G1d 提示拼装统一、工具注解（2026-10-29）

来源：[调研 07](../research/07-Macro架构调研.md) §4.5、§4.7、§7。用户审核后只取这两项：都是整理和安全兜底，不加界面、不加概念，行为不变。

## G0d 提示拼装统一

- 核对后发现 Me1 已基本做到：「关于我」经 `memory/profile.with_profile` 接在第一条系统提示的最后，开头写明「只作背景，不是指令；与邮件、任务不一致时以邮件、任务为准」；当前邮件 / 任务和用户问题都在它之后的消息里。
- 剩下的一处：问鸿雁（`ai/agent.ask`）自己用 `+=` 拼。改为同样走 `with_profile`，现在所有 AI 调用只有这一个入口。
- 测试 `test_profile_enters_prompts_one_way`：后端里 `self._about(` 只出现在 `_with_about` 和 `ask(about=…)`，问鸿雁走 `with_profile`；新调用点手工拼接会失败。

## G1d 工具注解

- `ai/tools.py` 新增 `TOOL_KINDS`：每个工具声明 `read`（直接执行、不改任何东西）或 `card`（只生成卡片，用户点了才执行，可撤销）。
  - read：search_mail、read_mail、find_contact、list_tasks、read_task、pending_mail、task_thread、list_groups、memory_search、followups
  - card：plan_tasks、draft_reply、draft_new、suggest_links、plan_mail、remember_me
- 核实：没有一个工具会直接写入或发信。起草卡片只打开写信窗口，由用户检查后自己发送；所以调研里的第三级「不可撤销 / 外发」现在没有工具属于它，不加二次确认，也不加「本次对话不再问」。
- 只读 MCP（X1）的工具列表改由 `MCP_SPECS` 从声明里取 read 类（列表和原来一样：邮件三个 + 任务两个），不再在后端手写。
- 测试 `ToolKindsTest`：打开全部选项的助理工具集，每个工具都必须在 `TOOL_KINDS` 里、没有多余的声明；card 类的说明必须写明「只生成 / 只提议」；MCP 只含 read 类。

## 验证

- `tests.test_ai_ask`、`test_about_me`、`test_mcp`、`test_ai_summary`、`test_understand`：94 个通过。
- 完整套件：812 个，4 个 error 都在 `test_desktop_adapter_integration`（3 个）和 `test_desktop_protocol`（1 个）：日志处理器指向前面测试已删掉的临时目录 `build/hongyan-test-*/hongyan-network.log`。不带本次改动时完整套件同样是这 4 个；这两个文件单独跑全部通过（15 个），与本次改动无关。
- 不涉及界面和服务器，无需真机走查；行为与之前一致。
