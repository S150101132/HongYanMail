# U4 · AI 模型可自选（2026-10-28）

## 做了什么

- 设置 → AI 助手的「模型」从文本框改为下拉：已设置 API Key 时，打开设置页就向接口地址请求 `GET {接口地址}/models`（OpenAI 兼容，带 Key），列出服务商提供的模型，当前模型排第一。
- 拉不到列表（服务商没有这个接口、Key 不对、断网）不报错，下拉里保留当前模型；接口地址是 DeepSeek 时另给 `deepseek-chat`、`deepseek-reasoner`。
- 列表里没有的模型：选最后一项「手动填写…」，下面出现文本框，照旧填名称后保存。
- 保存 / 测试连接后重新拉一次列表（换了接口地址或 Key 立即生效）。

## 代码

- `ai/client.py`：`ChatClient.models()`——GET `/models`，只收合 `check_model` 规则的 id，排序，最多 300 个；错误只出固定错误码（与聊天接口同一套）。
- `app/backend.py`：`ai_models()`；未设置 Key 时 `AI_NOT_CONFIGURED`。`app/shell.py`：`aiModels` 走 AI 线程。
- `web/index.html`、`ai.js`、`style.css`：下拉 + 「手动填写…」。

## 验证

- 单元测试：`tests/test_ai_summary.py` 新增 `test_models_lists_valid_ids`（请求形状、过滤非法 id、401 / 404 / 坏 JSON 的错误码不带服务器原文），`ai_models` 未配置时报 `AI_NOT_CONFIGURED`。
- 演示走查：`HONGYAN_WALK_AM=1 python tools/screenshot_app.py build/am-shots`，下拉与「手动填写…」两张截图正常。
- 真机：DeepSeek 的 `/models` 未在本机实测（需用户在设置页打开 AI 助手看下拉是否列出 deepseek-chat / deepseek-reasoner）。
