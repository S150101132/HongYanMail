# REA 接入状态

2026-10-15：已安装并验证 REA 6.5.0。

- Codex MCP 注册：`C:\Users\wangbiao\.codex\config.toml` 中的 `mcp_servers.rea`，固定版本 `rea-agents@6.5.0`。
- 启动命令：`cmd.exe /d /c npx -y rea-agents@6.5.0 mcp`。
- 配置备份：`C:\Users\wangbiao\.codex\config.toml.rea.backup`。
- 配套技能：`C:\Users\wangbiao\.agents\skills\reverse-engineer-anything`。
- 安装器返回 ready，所选 Codex 注册及技能检查 healthy；没有可用的原生分析 provider。
- 独立 MCP 连接 smoke check 成功：完成 initialize，tools/list 返回 207 个工具。结果见 `rea-mcp-check.json`。这不等于当前 Codex 聊天已经加载新工具，需要重启或重新连接后确认。

## 首次实际分析

已通过 REA CLI 静态分析 `C:\Program Files\cmclient\app`，进程退出码 0。

- 处理所选 112 个资源文件；统计报告 parsed_javascript_files 为 90、parse_failures 为 8。因此进程成功结束不代表每个文件均完整解析。
- 应用图 490 个节点、868 条边。
- 原始 Evidence：`rea-javascript.json`（约 229 MB），不宜直接在聊天中展开。
- 精简结果：`rea-javascript-summary.json`。
- Evidence ID：`ev_2f18404c2ce69640a605a87fb07eed298970bf584454ddf78f1dcb719534c77e`。

这验证网页资源静态分析可运行，不代表已逆向原生程序或认证协议。静态关系不能证明运行时行为；welcome.html 中一个内联 JS 体未被分析，其他未知范围详见 Evidence 的 limitations。

## 执行环境限制

普通执行环境中，REA 在 source-transfer 阶段写 .source 临时文件时返回 EPERM。将临时目录改到工作区后仍可重现。使用工具审批允许的正常 Windows 权限运行相同静态分析后成功。没有修改 Codex 全局审批策略或沙盒设置。

此现象支持权限环境是失败因素，但没有进一步确定 Windows 文件限制的具体机制。未来如果 MCP 分析重现 EPERM，应检查该次调用的执行权限与错误，而不应重新安装 REA。

## 下一项前提

后续已将 Ghidra 12.1.4 安装到工作区 `.tools`，并通过 REA 就绪检查。原生目标完整冷分析达到 900 秒时限，已停止清理；没有原生反编译结果，详见 `native-engine-status.md` 与 `native-auth-status.md`。另外，基于公司公开 Webmail 协议的独立登录和自发自收已真实通过，详见 `收发可行性验证结果.md`。
