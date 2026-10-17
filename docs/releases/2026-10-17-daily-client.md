# 日常使用版客户端（2026-10-17）

## 改了什么

Codex 版本的问题集中在界面简陋、日常功能缺失、每次操作都要等网络、真实 HTML 邮件被剥成纯文本。本轮保留已经真机验证过的协议、加解密和本机加密存储层，重做了应用层与界面：

| 层 | 文件 | 说明 |
| --- | --- | --- |
| 界面 | `coremail_desktop/app/web/` | 三栏工作台，浅色/深色，紧凑密度，窄窗口自动折叠侧栏 |
| 宿主 | `coremail_desktop/app/shell.py` | Qt WebEngine + QWebChannel；私有 `hongyan://` 方案；托盘、通知、文件对话框 |
| 后端 | `coremail_desktop/app/backend.py` | 无 Qt，可单元测试；串行网络线程 + 只读快速线程 + 后台预取线程 |
| 缓存 | `coremail_desktop/app/cache.py` | SQLite 仅存 AES-GCM 密文，密钥经 DPAPI 保护；正文缓存上限 300 MB |
| 正文净化 | `mime/sanitizer.py::sanitize_rich_html` | 保留表格、内联样式、`<style>`；去除脚本、事件、`url()`/CSS 转义绕过 |

## 本轮新接入的服务器能力（均先做只读形状探测）

探测脚本 `tools/probes/probe_daily_features.py` 只记录字段名与类型（`analysis/daily-features-shape-2026-10-17.json`）：

- `mbox:getAllFolders {stats:true}` → 每个文件夹 `messageCount`/`unreadMessageCount`。
- `mbox:listMessages {summaryWindowSize:120}` → 每行 `summary` 摘要。
- `oab:autoMatch {keyword, limit}` → `[{email, true_name, type}]`，用于收件人补全。
- `user:getAttrs {optionalAttrIds:['true_name']}` → 显示名。
- 发信 `isHtml:true` → HTML 正文。

## 真机验证（本人账号）

`tools/verify/verify_daily_real.py`，临时缓存目录用后删除，打开邮件不改变已读状态，结果见 `analysis/daily-client-real-verification-2026-10-17.json`：

| 检查 | 结果 |
| --- | --- |
| 登录 | 通过 |
| 文件夹与未读数 | 9 个文件夹，6 个系统角色，未读数齐全 |
| 收件箱第 1/2 页 | 各 50 封，48/50 有摘要，总数可知 |
| 缓存首屏 | 50 封，0 ms |
| 打开 8 封邮件 | 全部成功，含 4 个附件；已读状态未改变 |
| 已发送、本机搜索、服务器搜索 | 通过 |
| 通讯录补全 | 8 条，全部来自公司通讯录 |
| 轮询 | 通过 |
| HTML 自发自收 | 服务器接受，约 7 秒后到达收件箱，HTML 与标记完整 |

未打开的邮件首次加载约 1 秒/封，因此列表加载后会在后台预取最新 15 封正文。

## 自动化测试

`tests/test_app_backend.py` 新增 19 项（缓存不落明文、篡改行忽略、服务器已删除邮件清理、HTML 回复与签名、转发带原附件、草稿往返、删除不彻底清除、轮询只报新未读、会话失效自动重连、离线启动、CSS 转义绕过等）。全部 239 项通过，11 项按原因跳过（RC2 样本与需真实环境的项）。

## 仍未做 / 已知限制

- 证书加密发送已在同日接通，见下文“加密发送”。
- 不提供彻底删除；不支持多选批量操作、会话（线程）视图、服务端规则、日历。
- 新邮件为轮询（默认 1 分钟），未接入 Socket.IO 推送（握手参数未验证）。
- 粘贴的图片作为附件添加，不做内嵌图片。

## 加密发送（同日追加）

规则：收件人全部是同事（账号所在域，当前为 `inspur.com`）时默认加密；只要有外部收件人默认不加密；写信窗口标题旁的开关可手动切换。开启加密但有人在公司通讯录中没有证书时，弹窗列出这些人，由用户选择移除或“改为不加密发送”，程序从不自行降级。

实现与证据：

| 环节 | 方式 | 证据 |
| --- | --- | --- |
| 服务端代加密 | 不可用：本部署 `X.M.SMIME=false`，`smimeEncrypt:true` 被忽略（测试信以明文到达本人） | `analysis/smime-send-probe-2026-10-17.json` |
| 收件人证书 | `cert:listPublicKey {userList:[...]}` → `{邮箱: {publicKey(base64 DER), publicKeyHash, id, isDynamic}}`；只接受证书内邮箱与请求地址一致的证书 | 官方客户端日志请求形状；只读探测 |
| 加密 | 本地 CMS EnvelopedData，AES-256-CBC + RSA；同时加密给发件人自己，已发送副本可读 | 官方客户端支持 AES128/192/256/DES3 |
| 提交 | `continue` 取 composeId → `XT/jsp/mformdata.jsp?func=mbox:composeF&uploadMode=preEncodedData`，表单字段 `lettercontent` 为完整 S/MIME 邮件 | 网页端 CA 加密路径源码；`extraHeaders`/附件方式实测均被服务器改写为 multipart/mixed |

真机验证（仅发给本人，`analysis/encrypted-send-real-verification-2026-10-17.json`）：生产代码路径发送被接受（2.7 秒）；收件箱与已发送两份副本均为顶层 `application/pkcs7-mime`，服务器标记为加密，用本人证书解密后 HTML 与标记完整。探测阶段共向本人发送 7 封测试信。

阅读加密邮件需解锁个人证书，可选“记住证书”（路径与密码经 DPAPI 加密保存，启动时自动解锁）；发送加密邮件只需要收件人公钥，不需要解锁。新增测试 `tests/test_app_encryption.py`（9 项）。

互通验证：用户向一位同事发送加密邮件，同事在官方客户端中正常打开（用户确认，2026-10-17）。签名（smimeSign）未实现。

## 本机运行环境问题（同日）

- **Chromium 沙箱不可用**：在本机，带沙箱的渲染进程启动即退出（AbnormalTermination，代码 18），窗口空白。鸿雁现在先带沙箱启动，若首个页面加载前渲染进程退出，则记录标记并自动以关闭沙箱的兼容模式重启一次；7 天后再次尝试沙箱。邮件正文仍在无脚本、禁联网、CSP 限制的框架中显示。设置页会显示当前处于兼容模式。
- **Python 无法写入用户目录**：本机策略禁止 Python 写入 AppData、文档、临时目录等用户目录，导致登录信息无法保存（INTERNAL_ERROR）。数据目录改为优先 `%LOCALAPPDATA%\CoremailDesktopPreview`，不可写时自动使用程序旁的 `HongyanData`（已加入 .gitignore）；内容仍为 DPAPI/AES-GCM 密文。打开附件的临时文件也放在数据目录下；写入被拒绝时提示 `WRITE_DENIED`，不再静默失败。
- 本地错误日志 `hongyan-errors.log` 只记录异常类型与代码位置，不记录异常消息。
