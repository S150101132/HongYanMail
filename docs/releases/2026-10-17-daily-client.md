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

探测脚本 `tools/probe_daily_features.py` 只记录字段名与类型（`analysis/daily-features-shape-2026-10-17.json`）：

- `mbox:getAllFolders {stats:true}` → 每个文件夹 `messageCount`/`unreadMessageCount`。
- `mbox:listMessages {summaryWindowSize:120}` → 每行 `summary` 摘要。
- `oab:autoMatch {keyword, limit}` → `[{email, true_name, type}]`，用于收件人补全。
- `user:getAttrs {optionalAttrIds:['true_name']}` → 显示名。
- 发信 `isHtml:true` → HTML 正文。

## 真机验证（本人账号）

`tools/verify_daily_real.py`，临时缓存目录用后删除，打开邮件不改变已读状态，结果见 `analysis/daily-client-real-verification-2026-10-17.json`：

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

- 证书加密**发送**仍被阻止（提交接口未完成互通验证），加密邮件可解锁后阅读。
- 不提供彻底删除；不支持多选批量操作、会话（线程）视图、服务端规则、日历。
- 新邮件为轮询（默认 1 分钟），未接入 Socket.IO 推送（握手参数未验证）。
- 粘贴的图片作为附件添加，不做内嵌图片。
