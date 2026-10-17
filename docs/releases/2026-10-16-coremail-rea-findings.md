# Core Mail 功能吸收与协议验证

日期：2026-10-16。范围：原客户端已安装资源的静态 REA 分析，以及目标 Webmail 的固定接口验证。

## REA 证据

- 目标：`C:\Program Files\cmclient\app`，根目录身份 SHA-256 `8a2feb861a1e206325e6609d5795a54df3f22231fe48e17ab2a70448b3788c3c`。
- 完整父证据：`ev_d3a89b165cc863d9c797dd6e437a5b30400e7561db602f6302c804db333d86da`。
- 本轮选择六个资源：`template/utils/callPC.js`、`template/utils/attachment.js`、`template/utils/hotkey.js`、`template/compose/reply.html`、`template/readMail/static/js/cmx4read.js`、`template/readMail/static/js/dist/index.js`。
- 解析五个 JS，0 解析失败；完整资源清单包含 112 项，本轮未选 106 项。未执行目标脚本。
- 应用关系覆盖为 partial，语义图达到本轮 12000 总节点/每文件 4000 节点限制。因此对 quickReply、attachmentDownload、readMailHadLoaded 的语义追踪没有返回种子匹配，不能据此宣称相关功能不存在。下列功能依据已选文件的具体代码另行核实。
- 未做本轮原生反编译，也未从内部桥接命令推断 HTTP API。

## 可吸收的功能

| 原客户端证据 | 对日常工作的价值 | 当前处理 |
| --- | --- | --- |
| `compose/reply.html` 的 FROM/TIME/TO/CC/SUBJECT 与 `cmc-quote-content` | 回复及转发保留清楚的原邮件背景 | 已把相同信息组织到纯文本引用区；不加入 Bcc |
| `readMail/static/js/dist/index.js` 的 `handleQuickReply` → `callPC("quickReply",{mailId:...})` | 从当前邮件快速进入回复 | 正式回复按钮现已接通，继续沿用防丢稿写信窗口 |
| 同文件的 `attachmentDownload`、`attachmentStartDrag`、`copyAttachments` | 下载、拖动与复制多个附件 | 单附件另存为已实现；批量导出和拖动列入后续，尚未声称支持 |
| 同文件的 `showMailDetail` 切换 | 避免复杂头部占据阅读空间 | 当前固定显示主要头部，折叠完整详情是可继续吸收的交互 |
| `utils/hotkey.js` 的修饰键+命令映射 | 保持高频操作的键盘效率 | 保留 Ctrl+N/Ctrl+S/Ctrl+Enter/查找与刷新，文件夹键盘导航已接通 |
| 阅读页的 `handleRemark` / `handleBacklog` 桥接 | 备注与待办适合日常整理 | 属于后续效率功能，当前基础收发尚有未完成项，不先扩张为新子系统 |

## 独立回复契约

公开 Webmail `X.03931.js` 的 `getInitCompose` 实现使用 `!compose`，`public-login.js` 路由映射将 `!` 展开为 `XT/jsp/`。对应固定表单为 `XT/jsp/compose.jsp`，字段 `ctype=reply|replyall`、`mid`、`isHtml=false`；服务器创建回复会话后，继续经已验证的 `common/mbox/compose.jsp` deliver 提交。服务器会话保留引用头，不需要猜测 extraHeaders 格式。

真实验证仅针对此前向自己发送的测试邮件，收件人由验证代码强制限定为本人。原型和正式实现各验证回复、回复全部各一封，共四封测试邮件，保留在邮箱中。正式实现验证的两个发送均已接受，并在本人收件箱下载确认 `In-Reply-To` 等于原邮件 Message-ID、References 包含原邮件、正文唯一标记存在；重复提交相同本机草稿只返回原发送记录。报告：`analysis/reply-roundtrip-contract-2026-10-16.json` 与 `analysis/reply-production-roundtrip-2026-10-16.json`。未向第三方发信。

身份读取也已实测：`XT/jsp/mail.jsp?func=getAllAccounts` 返回 `var` 列表，成员有 `addr/name`。本次得到一个经过服务器确认的身份；正式实现用这些身份排除回复全部中的本人及别名。

代码审查发现并修复：显式指向本人的 Reply-To 不得被替换为其他原收件人；缺失 Message-ID 不能通过空字符串比较而绕过原邮件绑定检查。

## 鸿雁后续落地

2026-10-16 已接入搜索、可逆移动及写信附件上传，详见 [鸿雁验收记录](2026-10-16-hongyan-workflow.md)。附件上传依据公开 Webmail `X.03931.js` 的 `upload:prepare`、`!upload:directData`、`composeId/attachmentId/offset` 及 2 MiB 分块配置；正式实现通过 3 MiB 分块下载哈希验证，并完成中文名、二进制及空附件的本人收件箱回环验证。原客户端桥接命令仍只作为交互参考，没有被猜测成 HTTP 接口。

加密提交的后续静态线索：公开 `C.03931.js` 写信流程中出现 `pab:getDefaultKeyByEmails`、`encryptMail` 和 `lettercontent=k.mimeNode.build()`。这只是下一阶段的协议线索，尚未证明可直接用它提交鸿雁生成的 S/MIME，也未据此解除加密发送限制。
