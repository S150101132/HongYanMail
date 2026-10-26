# 收信规则重新设计（R5）

2026-10-26。用户反馈：规则条件太长看不清、地址难以使用，要求参考其他邮箱重新设计、开发、测试收信规则模块。

## 参考

| 邮箱 | 借鉴 | 不借鉴 |
| --- | --- | --- |
| Outlook | 从邮件「创建规则」、规则一句话描述、「立即运行」到已有邮件 | 规则向导多步、例外条件 |
| Gmail | 「过滤类似邮件」直接带出发件人；保存时「也应用于 N 个匹配的会话」 | 按搜索语法建过滤器 |
| 公司网页版（S.03931.js） | 字段 from / to / cc / recipients / subject，运算 contains / excludes / = / != / startsWith / endsWith（`ii`）；保存后列出已有匹配邮件（`mbox:listMessages {fid, ruleConditions:'@id'}`）；自动命名「来自 X」 | 正文 / 附件名 / 日期 / 例外（`advanceFilter` 开关，部署上未知）、拒收、短信、自动回复动作 |

用户原则「最简单优先」：不加新概念，规则仍在 设置 → 收信规则，一件事一个地方。

## 改动

1. **从邮件建规则**：邮件「⋯」和列表右键加「用这封邮件创建规则…」。弹出选择：新建规则（发件人 = 这个地址，名称自动「来自 某某」，动作默认移动到）或「加到已有规则」——列出含发件人条件的可编辑规则，选中后把地址追加进它的发件人条件并保存（最常见：给「集团通知」再加一个发件人）。
2. **编辑器**
   - 条件值是标签（chip）：只显示名字，悬停看完整地址，× 移除；地址类字段输入时联想通讯录（复用写信的 `suggest`）。
   - 字段增加「抄送」「收件人或抄送」，运算增加「不包含」「不是」「开头是」（网页版同样的存法）。
   - 「移动到」下拉最后一项「新建文件夹…」，建好后直接选中。
   - 规则名称可不填：按第一个条件自动生成，重名时加序号。
   - 底部实时一句话预览：「来自 A、B 等 6 人的邮件 → 移到「公司通知」」。
3. **对已有邮件执行**：保存后，若规则只含移动 / 标为已读 / 星标动作，查询收件箱已有匹配封数（`listMessages ruleConditions`，只读），有匹配时提示「收件箱已有 N 封符合，对它们执行？」；规则列表每条也有「对收件箱已有邮件执行」。执行由客户端用已验证的批量移动 / 标记完成（每批 100 封），提示里可撤销（移回原文件夹、恢复已读 / 星标）。转发永不对已有邮件执行。
4. 列表：一句话摘要保持；地址只显示名字。

## 后端

- `protocol/rules.py`：`TEXT_FIELDS` 加 `cc`、`recipients`；`TEXT_OPS` 加 `excludes`、`!=`、`startsWith`；地址字段带 `forAddress / extractAddress`。
- `CoremailAdapter.rule_matches(rule_id, folder_id, offset, limit)`：`mbox:listMessages {fid, ruleConditions:'@<id>', start, limit, mode:'count', order:'date', desc:true, returnTotal:true}`。
- `MailBackend.rule_matches(rule_id)`、`apply_rule(rule_id)`（返回 done / 撤销记录）、`undo_rule_apply()`。

## 验证

- 单元测试：编解码新字段 / 运算、`rule_matches` 线上形状、apply / undo 在 demo 适配器上。
- 真机（需用户同意）：`tools/probes/probe_rule_matches.py` 只读——每条规则的匹配封数与收件箱总数对比、不存在的规则号的返回码；`tools/verify/verify_rules_v2_real.py`——临时添加停用的 HY-RULE-TEST-V2（新字段 / 运算），读回后删除。
