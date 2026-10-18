# 共享邮箱 / 部门邮箱（2026-10-18）

分支 `claude/v03-shared-mailbox-7f1dcd`。服务器声明 `supportShareMail:true`，官方客户端日志里 `mbox:listSharedAccessMbox` 被调用 6,487 次。

## 只读探测

脚本 `tools/probes/probe_shared_mailbox.py`，结构记录在 `analysis/shared-mailbox-shape-2026-10-18.json`（只含字段名、类型、计数和布尔值）。

| 检查项 | 结果 |
| --- | --- |
| `mbox:listSharedAccessMbox {showHidden:true, v:2}` | `S_OK`，**列表为空**：用于探测的账号没有任何共享邮箱 |
| `XT/jsp/mail.jsp?func=getAllFolders`（webmail 的文件夹树） | 11 个节点（-5、-8、1–6 和 3 个自建文件夹），**没有共享邮箱节点 -6** |
| `mbox:getShareACL {v:2, email:本人}` | 被拒绝（`RPC_REJECTED`），这是“我把邮箱共享给谁”的设置接口，与本功能无关 |

因此共享邮箱的读写路径**无法在真实账号上核实**，按任务约定只对照 webmail 源码实现，并以演示数据测试。拿到有共享邮箱的账号后，重跑同一个探测脚本即可补上：它会记录每个共享邮箱的权限字段、文件夹列表、`mboxa` 放在请求体与放在 URL 的差别，以及读取一封已读邮件（不改变任何人的已读状态）。

## 依据的 webmail 行为（`analysis/web-login/*.js`）

- 列表行字段：`id`、`email`、`remark`、`send`、`rw`、`recallable`（`C.03931.js` 写信模块：共享邮箱的 `mboxa` 为 `'@'+id`）。
- 每个邮件接口都带 `mboxa`，放在 JSON 请求体里：`listMessages`、`getMessageInfos`、`updateMessageInfos`、`getThreadMessageInfos`、`listDeliveryHistory`、`recallMessage2`、`compose`/`replyMessage` 等；只有 `mbox-data`（原文下载）这类 URL 把 `mboxa` 放在查询串。
- 文件夹 id 写成 `fid@id`（`decomposeFid`/`fullId`），文件夹树中 -6 节点下是共享邮箱及其文件夹。
- 权限：`enableSendMail = !!send`（决定能否以该邮箱身份回复、转发、写信），`enableUpdateMailStatus` 来自 `rw`。

## 实现

- 协议层：每个邮件方法多一个可选的 `mboxa` 参数；为空时请求与原来完全相同（有测试锁定）。`mboxa` 格式不对时在发请求前拒绝。新增 `list_shared_mailboxes()`；共享邮箱的文件夹用 `mbox:getAllFolders {stats, mboxa}` 取得（未核实）。
- 后端：共享邮箱的文件夹和邮件在本机用 `fid@id`、`mid@id` 表示，缓存、正文、本机搜索、联系人建议都与本人邮件分开；邮箱不再共享时，它在本机缓存中的邮件一并删除。
- 权限在后端执行，界面只是不显示：没有 `rw` 时，打开邮件不标为已读，标记、移动、删除一律拒绝（`SHARED_READ_ONLY`）；移动只能在同一个共享邮箱内（`SHARED_MOVE_OUTSIDE`）；有 `send` 时回复和新邮件默认以共享邮箱身份发出，没有时用本人地址，若仍选了共享邮箱地址则在保存、发送前拒绝（`SHARED_SEND_DENIED`）。共享邮箱的邮件不提供撤回。
- 界面：侧栏新增“共享邮箱”分组，每个邮箱可展开为文件夹并显示未读数；阅读时有提示条说明所在邮箱、是否只读、回复以谁的身份发出。演示模式带一个“研发部公共邮箱”。

截图：`tools/screenshot_app.py` 的 `shared`、`shared-reply`、`shared-readonly` 三步。
