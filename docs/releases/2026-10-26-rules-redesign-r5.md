# 2026-10-26 · R5 收信规则重新设计

设计：[specs/2026-10-26-mail-rules-redesign.md](../superpowers/specs/2026-10-26-mail-rules-redesign.md)。参考 Outlook（从邮件创建规则、立即运行）、Gmail（过滤类似邮件、应用于已有匹配）和公司网页版（`analysis/web-login/S.03931.js`）。

## 做了什么

- **从邮件建规则**：阅读栏「⋯」和列表右键「用这封邮件创建规则…」→ 新建（发件人 = 该地址，名称自动「来自 某某 → 文件夹」）或加到已有规则的发件人里（已包含的规则灰掉）。共享邮箱、只读 .eml 不显示。
- **编辑器**：条件值是标签（只显示名字，悬停看完整值，× / Backspace 删，回车、逗号或粘贴多个添加）；地址类字段联想通讯录；字段加「抄送」「收件人或抄送」，运算加「不包含」「不是」「开头是」；「移动到」最后一项「新建文件夹…」；规则名称可不填；底部实时一句话预览；顺序改为 条件 → 动作 → 选项。
- **对已有邮件执行**：保存后（规则启用、只有移动 / 已读 / 星标动作时）查询收件箱已有匹配，有则列出前 5 封并询问；规则列表每条有 ▶ 按钮。服务器用 `mbox:listMessages {fid, ruleConditions:'@id'}` 给出匹配（只读），客户端用已验证的批量移动 / 标记执行（每批 100，最多 500 封），提示里「撤销」把文件夹、已读、星标恢复原样。转发永不对已有邮件执行（`RULE_NOT_APPLICABLE`）。
- 列表摘要：地址只显示名字；网页版规则写成「1 个网页版条件 → 移动到…」。

代码：`protocol/rules.py`（`ADDRESS_FIELDS`、`TEXT_OPS`、`APPLY_ACTIONS`）、`CoremailAdapter.rule_matches`、`MailBackend.rule_matches / apply_rule / undo_rule_apply`、`web/rules.js`（`RL`、`ruleFromMail`）、`app.js` 两处菜单、`style.css`（`.rl-*`）、demo 适配器按规则匹配。

## 验证

- 单元测试：`tests/test_rules_tags.py` 新增 `test_webmail_fields_and_operators`、`test_rule_matches_lists_by_rule`、`test_apply_rule_to_existing_mail_and_undo`，页面钩子检查；全量 757 通过（2 跳过）。
- 演示走查：`HONGYAN_WALK_R5=1 python tools/screenshot_app.py build/r5-shots`——规则列表、编辑、通讯录联想、对已有邮件执行、从邮件创建、保存后询问、执行后撤销提示、新规则出现在列表，无脚本错误。走查发现并修复：demo 匹配没有从 `"名字" <地址>` 中取地址。

## 待真机（需用户同意后运行）

| 检查 | 工具 | 写入 |
| --- | --- | --- |
| `ruleConditions:'@id'` 是否真的按规则过滤（每条规则的匹配数 < 收件箱总数、不存在的规则号返回什么） | `tools/probes/probe_rule_matches.py` → `analysis/rule-matches-shape-*.json` | 无（只读） |
| 新字段 / 运算服务器是否接受并原样存回 | `tools/verify/verify_rules_v2_real.py` → `analysis/rules-v2-real-verification-*.json` | 临时添加停用规则 HY-RULE-TEST-V2，读回后删除 |

### 探针结果 2026-10-26 23:04（`analysis/rule-matches-shape-2026-10-26.json`）

- `user:getMailRules` 返回 `S_OK`、空列表：账号当前**没有收信规则**（用户此前编辑的「集团通知」等不在服务器上；本轮没有任何真机写入），所以无法验证按真实规则过滤。`mr:list`（网页版的列表名）→ `RPC_NOT_ALLOWED`。
- `ruleConditions:'@999999999'`（不存在的规则）返回整个收件箱（398 / 398），服务器不报错。因此加了保护：匹配数 ≥ 收件箱总数时报 `RULE_MATCH_UNVERIFIED`，不列出、不执行（测试 `test_apply_rule_to_existing_mail_and_undo` 末段）。
### 写入验证 2026-10-26 23:11（`analysis/rules-v2-real-verification-2026-10-26.json`，用户同意）

- 停用规则 HY-RULE-TEST-V2（主题 `startsWith`、抄送 `excludes`、收件人或抄送 `!=`、发件人 `contains`）**存入并原样读回**：字段 / 运算 / `forAddress`、`extractAddress` 与网页版一致，读回 `editable`、条件相同、停用。规则 id 为 100（账号此前无规则）。
- 按该规则列收件箱：**0 封**，收件箱共 398 封——服务器对存在的规则（停用的也算）按条件过滤；只有不存在的规则号才返回整个收件箱，`RULE_MATCH_UNVERIFIED` 保护保留。
- 清理：规则已删除，规则顺序不变。
- 未覆盖：命中若干封的真实规则上「对已有邮件执行」+ 撤销（需要用户建一条规则后在鸿雁里点一次，执行的是已验证的批量移动 / 标记）。

在两项真机结果出来前，「对已有邮件执行」的匹配数以服务器为准但未经核实；若探针显示服务器忽略 `ruleConditions`（匹配数等于收件箱总数），应先关闭该入口。
