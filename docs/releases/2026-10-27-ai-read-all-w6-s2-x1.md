# W6 学习写作风格、S2 总结读全、X1 本机只读 MCP — 2026-10-27

## 做了什么

### W6 学习写作风格

- 「AI 写信」整理邮件时，附上你最近发给同一类收件人（有外部收件人算「外部」，否则算「同事」）的已发邮件，最多 3 封、每封 600 字，作为风格样例：模仿称呼、开头结尾的习惯、语气和句子长短，不照抄内容和落款。
- 只用已下载到本机、没有加密的已发邮件（最近 40 封里找）；去掉引用的原邮件和签名（鸿雁发出的按标记去掉，别处发出的按「设置 → 签名」的文字去掉），邮箱地址和电话号码换成【邮箱】【电话】。
- 界面没有变化。`ai/writing.py`（`style_sample`、`STYLE_NOTE`）、`backend._style_samples`。

### W7 快捷回复方向：已有，不做

- 阅读栏的「AI 回复」已经是这个功能（先给 2–3 个回复方向，再起草），不另加第二个入口。

### S2 「总结这封」读全

- 回复里引用的早先邮件从最多 2000 字放宽到 6000 字（全文仍最多 12000 字）。整个会话的经过、还没解决、要我做什么照旧用「AI → 总结整个会话」。
- 附件速读：Word / PowerPoint / Excel 用应用内查看用的解析器取文字，文本文件直接解码，PDF 用 Qt 自带的 QtPdf 取文字（不加新依赖）；每个附件最多 4000 字、合计 8000 字、最多 5 个，和正文一起放在随机标记里当资料。图片、压缩包、损坏或加密的附件只给名字。提示词说明「附件才是正文时按附件总结」；`SUMMARY_VERSION = 4`，旧总结自动重新生成。
- 问邮箱 / 助理的 `read_mail` 也返回附件文字（合计 4000 字）。
- `ai/attachments.py`、`shell._pdf_text`、`backend.pdf_text`。扫描版 PDF 没有文字层，读不出来，只给名字。

### X1 本机只读 MCP 服务

- 设置 › AI 助手 加一项「允许本机 AI 工具读取邮箱（MCP，只读）」，默认关。打开后显示一行命令，在终端运行一次就把鸿雁加到 Claude Code：
  `claude mcp add hongyan --scope user -e PYTHONPATH=… -e HONGYAN_DATA_DIR=… -- "…\.venv\Scripts\python.exe" -m coremail_desktop.mcp`
- 工作方式：鸿雁运行时在本机命名管道上监听；Claude Code 启动转发程序 `python -m coremail_desktop.mcp`（只用标准库和 DPAPI，不写文件），把 MCP 请求转进鸿雁，在 AI 线程上执行。连接要用随机密钥，密钥用 DPAPI 存在数据目录的 `mcp.key`，只有本 Windows 用户能解开；关掉即删除密钥、停止监听，再打开换新密钥。
- 工具只有问邮箱那 5 个只读工具：`search_mail`、`read_mail`（含附件文字）、`find_contact`、`list_tasks`、`read_task`。起草、计划、邮件操作等不在名单里，按名字调用也拒绝。加密邮件按「允许 AI 读取加密邮件」的设置，不会弹窗询问。
- 鸿雁没运行或开关关着时，转发程序对每个请求回固定的中文提示。设置里显示本次运行被读取了几次、最近一次的时间。
- 安装版（PyInstaller）暂不支持：窗口程序没有标准输入输出，设置里提示从源码运行。
- `mcp/server.py`（`handle`、`Host`）、`mcp/__main__.py`、`backend.mcp_*`、`shell.Bridge.mcp_sync`。

## 验证

- 全量 787 个测试：4 个协议 / 适配器网络用例在全量运行时报错，单独运行（`test_desktop_adapter_integration`、`test_desktop_protocol`，15 个）通过，和 W5 时相同，与本改动无关。
- 演示走查（`HONGYAN_WALK_X1=1 tools/screenshot_app.py`）：设置 › AI 助手 打开开关后显示命令和「复制」，关掉后隐藏。
- 新增 `tests/test_mcp.py`（协议、只读名单、开关和换密钥、走真实命名管道的转发端到端）；`test_ai_summary`、`test_ai_ask`、`test_ai_v10` 补了附件速读、`read_mail` 附件、风格样例的用例。
- 用 `python -m coremail_desktop.mcp` 子进程实际收发一次：鸿雁未运行时返回固定提示，中文正常。
- QtPdf 取文字：一份 103 页英文 PDF、一份中文周报 PDF 均在 0.03 秒内取到文字。

## 待真机验收

- 总结一封「详见附件」的真实邮件（PDF 或 Word），看要点是否来自附件。
- 回复一封同事邮件、一封外部邮件，只写要点点「AI」，看写出来是否像自己平时的口吻。
- 打开 MCP 开关，在终端运行设置里的命令，在 Claude Code 里问「最近谁给我发了报价」；再关掉开关，确认 Claude Code 里提示未开启。注意：按项目规则，这一步由你自己操作，Claude 不读真实邮件。
