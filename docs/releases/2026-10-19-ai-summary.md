# 2026-10-19 · AI 助手：总结这封邮件（DeepSeek 接入，最小版）

## 功能

- 阅读区工具栏「总结」：在正文上方显示 AI 摘要，包括一句话结论、要点、需要我做什么。摘要可以「重新生成」或关闭。弹出的阅读窗口里也能用。
- 设置 → AI 助手：接口地址（默认 `https://api.deepseek.com`）、模型（默认 `deepseek-chat`）、API Key（只能写入，不能查看，可以清除）、测试连接、本月调用次数和 token 数。
- 演示模式（`--demo`）使用本机生成的假摘要，不联网。

## 安全与隐私

- API Key 用 DPAPI 保存在数据目录的 `ai.bin`，与 `account.bin` 并列。页面拿不到 Key。
- 只允许 HTTPS，不跟随重定向（避免 Key 被带到别的地址）。
- 正文先转成纯文本，去掉脚本、样式、注释，以及用 `display:none`、`hidden`、0 字号或 0 透明度隐藏的文字。引用的早先邮件最多保留 2000 字，全文最多 12000 字。
- 邮件资料放在每次随机生成的分隔符之间，系统提示声明其中的指令一律不执行。只发送显示名和域名，不发送完整邮箱地址，附件只发送文件名。
- 加密邮件：每次总结都要用户单独确认（`AI_ENCRYPTED_CONFIRM`），摘要不保存。证书未解锁时返回 `CERT_LOCKED`。
- 普通邮件的摘要保存在加密缓存的 kv `aiSummaries` 里（最多 300 条）。再次点「总结」直接显示已保存的结果，不再调用 AI。
- 模型输出必须是 JSON（`response_format: json_object`），经过校验和截断后，界面只按纯文本显示（`esc`）。
- 错误只显示固定错误码：`AI_NOT_CONFIGURED`、`INVALID_AI_SETTINGS`、`AI_NOT_FOUND`、`AI_AUTH_FAILED`、`AI_QUOTA`、`AI_RATE_LIMITED`、`AI_TIMEOUT`、`AI_UNAVAILABLE`、`AI_CONTEXT_TOO_LONG`、`AI_BAD_RESPONSE`、`AI_MAIL_UNAVAILABLE`。429 和 5xx 错误按 1 秒、2 秒退避，最多重试 2 次。
- 用量只记录每月的调用次数和 token 数，不记录 prompt 和回复内容。

## 代码

- `coremail_desktop/ai/client.py`：OpenAI 兼容的 chat 客户端（基于 urllib）。
- `coremail_desktop/ai/summary.py`：正文清洗、提示词、结果解析。
- `app/backend.py`：`ai_settings`、`set_ai_settings`、`ai_test`、`summarize`。`summarize` 只从缓存读邮件；正文还没下载时返回 `AI_MAIL_UNAVAILABLE`。
- `app/shell.py`：`summarize` 和 `aiTest` 在单独的 `hongyan-ai` 线程上运行，不会让收发信等待。
- `app/web/ai.js`：摘要面板和设置。`renderMessage` 的钩子写在 `ai.js` 里，因为弹出窗口可能在这个文件加载完之前就渲染邮件。
- `app/demo.py`：`DemoAiClient`。

## 检查

- `tests/test_ai_summary.py`：22 项。覆盖请求格式、HTTP 和网络错误映射与重试、设置校验、隐藏文字、引用折叠与截断、分隔符与地址隐藏、JSON 解析、Key 加密存储、缓存与用量统计、加密邮件确认、桥接线程。
- 全量测试：536 项通过，11 项跳过。
- 走查截图：`tools/screenshot_ai.py`（浅色和深色，1440 宽）保存在 `analysis/screens-ai/`，包括未设置、设置页、生成中、摘要、加密确认。

## 尚未验证

- 没有在真实的 DeepSeek Key 和真实邮箱上运行过：需要用户在设置里填入自己的 Key，从资源管理器启动鸿雁后再试。
