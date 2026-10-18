# 定时发送 · 撤回 · 已读回执与投递状态（2026-10-17）

分支 `claude/v02-send-features-33f789`。三项功能都先用只读探测或给自己发信的方式核实接口，再接入界面。
探测脚本放在 `tools/probes/probe_send_features{,2,3}.py`，结构记录在 `analysis/send-features-shape{,2,3,4}-2026-10-17.json`（只含代码、字段名、计数、布尔值和分钟偏移），真实账号验证脚本是 `tools/verify/verify_send_features_real.py`。

## 已核实的服务器行为

| 能力 | 结论 | 证据 |
| --- | --- | --- |
| 定时发送 | `%mbox/compose` 用 `action:'schedule'`，`attrs.scheduleDate` 写成 `YYYY-MM-DD HH:MM:SS`，**按账号属性 `time_zone`（Asia/Shanghai）理解**，与请求头里的 `tz="Asia/Riyadh"` 无关。按 UTC+3 写的时间会被当成已经过去的时间，返回 `FA_INVALID_DATE`。成功的响应里带 `scheduledSent`。 | shape、shape2 |
| 定时邮件存放位置 | 草稿箱（fid 2），带 `flags.scheduleDelivery`。行内的 `sentDate`（按响应时区 UTC+3 理解）就是预定时间，与请求时间误差为 0 分钟。发出后从草稿箱进入已发送。 | shape2：到达时间比预定晚约 1.5 分钟 |
| 取消定时 | 用 `XT/jsp/compose.jsp ctype=draft&mid=…` 重开草稿，再以 `action:'save'` 保存且不带 `scheduleDate`，邮件就成为普通草稿（不再带 `scheduleDelivery`）。webmail 的“再次编辑会取消定时”也是这个做法。 | shape2 C 项 |
| 加密邮件定时 | `mbox:composeF`（预编码 S/MIME 路径）收到 `action=schedule` 后，无论日期放在表单、会话还是 URL，用文本、毫秒、ISO 哪种格式，或加上 `stringifyScheduleDate`，**一律返回 `FA_INVALID_DATE`**。webmail 自己的 lettercontent 路径也从不定时。结论：**服务器不支持加密邮件定时**。 | shape3、shape4 |
| 已读回执 | `requestReadReceipt:true` 必须同时放在 continue 和 deliver 两步的 attrs 里，信件才会带 `Disposition-Notification-To`；只放在 deliver 那一步则不生效（`mbox:sendMDN` 返回 `FA_NO_RECEIPT`）。加密邮件由本机在外层头里加上同样的头。 | shape、shape2 |
| 投递状态 | `user:listDeliveryHistory {mid}` 返回 `[{to, to_name, result, rclResult, recallable, modtime, …}]`。给自己发的信先是 `result` 100（已投递），读过后变为 509（个位为 9，表示对方已阅读），已发送行同时带上 `flags.rcptRead`。 | shape |
| 撤回 | 参数取自 webmail（`173.03931.js` 模块 5524）：先调 `mbox:cancelDeliver {tinfo, mid}`（中止仍在队列中的投递），返回 `FA_MAIL_NOT_FOUND` 时再调 `mbox:recallMessage2 {mid, tid}`（`tid` 取 `tinfo` 最后一个冒号之后的部分），`var` 为 `{收件人: mail_rcl_state}`。**给自己发的信 `recallable:false`，按 webmail 的方式调用也返回 `FA_UNSUPPORT_RECALL`**，因此撤回成功的那条路径还需要一位同事配合验证。 | shape、shape2 |

## 界面

- 写信窗口底部新增“定时发送”（可选 1 小时后、今天 18:00、明天 09:00 / 14:00，或自定义时间；至少在 2 分钟之后、一年以内）和“请求已读回执”复选框。设了定时后，发送按钮变为“定时发送”，时间以标签显示。
- 若邮件开启了加密又设了定时，定时标签会变成黄色提示；点发送时会让你选择“立即加密发送”或返回修改，**绝不自动改为明文**。后端也会拒绝（`SCHEDULE_ENCRYPTED_UNSUPPORTED`）。
- 侧栏新增“定时发送”列表（取自服务器草稿箱中带 `scheduleDelivery` 的邮件），显示预定时间；阅读这类邮件时有提示条和“取消定时”按钮。取消后会重新列一次草稿箱，确认这封邮件不再是定时邮件，否则报告“未确认”。
- 打开“已发送”中的邮件时会自动显示投递状态面板（共几位收件人、各自的状态和时间，状态文字来自 webmail 自己的代码表），列表中对方已读的邮件带“已读”标记。
- 阅读“已发送”中的邮件时，工具栏有“撤回”。结果对话框逐个列出服务器返回的收件人状态；没有逐人结果时一律报“未确认”，不会显示为成功。

## 真实账号验证

`tools/verify/verify_send_features_real.py`，2026-10-17 22:19，全部经由 `MailBackend`（应用自己的发信路径），只发给账号本人（`analysis/send-features-real-verification-2026-10-17.json`）：

| 检查项 | 结果 |
| --- | --- |
| 定时 4 分钟后发送（带已读回执） | Accepted；“定时发送”列表中出现，列出的时间与所选时间相差 0 秒；阅读时显示为定时邮件 |
| 到达 | 提交后 5.4 分钟到达收件箱（服务器约晚 1.4 分钟发出）；发出后不再出现在定时列表；信件带回执头 |
| 定时 1 小时后再取消 | `unschedule` 成功；不再出现在定时列表；作为普通草稿保留 |
| 加密邮件 + 定时 | 提交前即被拒绝：`SCHEDULE_ENCRYPTED_UNSUPPORTED` |
| 立即发送（带已读回执） | 带 `Disposition-Notification-To`；投递状态从 `ok`（已投递）变为 `read`（已阅读） |
| 撤回给自己的信 | `RECALL_UNSUPPORTED`，如实报告，没有当作成功 |
| 清理 | 5 封测试邮件已移到“已删除” |

自动化测试：`tests/test_send_features.py` 新增 16 项；全套测试 `unittest discover -s tests` 共 314 项通过（12 项跳过）。

## 尚未完成

- 回应别人的已读回执请求（`mbox:sendMDN`）没有做：这会代表用户向他人发送消息，需要用户先确定交互方式。

## 撤回：本邮箱未开放（2026-10-18 结论）

用户向同事 hanbiao 发送了两封测试邮件（一封加密签名、一封不加密），对方均未阅读，鸿雁撤回均被拒绝（`FA_UNSUPPORT_RECALL`）。只读核查（`tools/probes/probe_recall_state.py`、`tools/probes/probe_recall_fields.py`，结果在 `analysis/recall-*-2026-10-17.json`，只记布尔值与代码）：

| 证据 | 结果 |
| --- | --- |
| 投递历史 `user:listDeliveryHistory` | 两封给同事的邮件都是 `result 100`（已投递、未读），但 `recallable: false`；给自己的邮件同样为 `false` |
| 跟踪号 `tinfo` | 加密与不加密邮件都有，不是缺少跟踪号的问题 |
| 加密的影响 | 无：不加密邮件同样不可撤回 |
| 网页版撤回条件（`M.03931.js`） | 只有消息信息带 `dsIcon` 且开启 `supportRecallMail` 时才显示撤回按钮；本服务器对已发送邮件不返回 `sndStatus`/`rclStatus`/`recallable`/`dsIcon`，网页版在这些邮件上也不会提供撤回 |

结论：服务器虽然全局声明 `supportRecallMail: true`，但没有为本邮箱开放撤回（服务器策略），不是鸿雁的请求问题。用户决定不再测试。

处理：已发送邮件加载投递状态后，若服务器标记所有收件人都不可撤回，“撤回”按钮置灰，悬停与投递状态面板说明原因——收件人均已阅读时提示“收件人均已阅读”，否则提示“本邮箱未开放撤回功能（服务器策略）”；仍有撤回请求被拒时按钮立即置灰，错误文字改为同一说明。服务器日后开放撤回时（投递历史出现 `recallable: true`），按钮自动恢复可用。
