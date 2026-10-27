# 鸿雁 · 工作邮箱

本仓库包含两件东西：一次性兼容性验证原型（`probe/`）和在其证据上开发的桌面客户端预览版（`coremail_desktop/`）。

**2026-10-15 已完成真实独立登录与自发自收验证**：读取 9 个文件夹、5 条列表和 MIME 邮件，发送到自己后在收件箱下载并核对唯一标记。详见 `analysis/收发可行性验证结果.md` 与 `analysis/connection-probe-result.json`。

**2026-10-17：日常使用版界面。** 默认界面已重做为三栏 Web 工作台，带加密本机缓存、自动收信与托盘通知、富文本写信与签名、公司通讯录补全，并已用本人账号完成只读检查与 HTML 自发自收验证（见下文）。发给同事的邮件默认证书加密（S/MIME，已真机自发自收验证），含外部收件人时默认不加密；旧版界面以 `--classic` 保留。

目标服务器：`https://newmail.inspur.com/coremail/`。`mail.inspur.com` 当前是另一套 Outlook OWA 入口。

## 开发环境搭建

需要 Windows 10/11 和 Python 3.12（3.11 起可用）。程序依赖 Windows DPAPI，只能在 Windows 上运行。

```powershell
git clone https://github.com/S150101132/HongYanMail.git
cd HongYanMail
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[build]"     # PySide6、cryptography、bleach、zstandard，以及打包用的 PyInstaller
.venv\Scripts\python.exe -m coremail_desktop --demo       # 先用合成数据跑起来，不需要账号、不联网
.venv\Scripts\python.exe -u -m unittest discover -s tests # 全部测试
```

- 打包生成安装包还需要 [Inno Setup 6](https://jrsoftware.org/isinfo.php)（装到常见位置，或放在 `.tools\InnoSetup`，或设置 `$env:ISCC`）；没有它时 `build.ps1` 只生成免安装文件夹。
- 连接真实邮箱需要公司 Coremail 账号；证书加密收发还需要本人 `.pfx` 证书。密码、证书、`HongyanData/`、`analysis/*.json` 等均已在 `.gitignore` 中，不要提交。
- 开发约定、目录说明和真实账号测试规则见 `CLAUDE.md`；下一步做什么见 `docs/roadmap/待实现功能.md` 的「开发顺序」表。

## 桌面客户端

```powershell
.venv\Scripts\python.exe -m coremail_desktop            # 启动鸿雁
.venv\Scripts\python.exe -m coremail_desktop --demo     # 合成演示数据，不联网
.venv\Scripts\python.exe -m coremail_desktop --classic  # 旧版 Qt Widgets 界面
powershell -File packaging\build.ps1                   # 打包、校验，并生成安装包 dist\Hongyan-Setup-<版本>.exe
```

2026-10-17 起默认界面重做为 Web 界面（Qt WebEngine 承载，经 QWebChannel 调用已真机验证的 Python 协议层）。架构：

- `coremail_desktop/app/backend.py`：无 Qt 的应用后端（登录、同步、阅读、标记、移动、搜索、通讯录、写信发送、轮询）。
- `coremail_desktop/app/cache.py`：本机缓存。SQLite 只存 AES-GCM 密文，密钥由 Windows DPAPI 保护；启动先显示缓存，再与服务器同步，断网时可读已缓存邮件。
- `coremail_desktop/app/shell.py`：窗口、私有 `hongyan://` 页面、系统托盘、新邮件通知、附件打开/另存为、证书解锁。页面除用户逐封允许的远程图片外禁止一切网络请求。
- `coremail_desktop/app/web/`：界面（三栏工作台、浅色/深色、紧凑密度）。

日常功能：

- **收信**：文件夹未读数、按日分组列表与摘要预览、无限滚动、按需自动收信（默认 1 分钟）、托盘新邮件通知、关闭窗口后驻留托盘。
- **阅读**：保留表格与内联样式的 HTML 正文（无脚本沙箱 + CSP，远程图片默认阻止）、附件打开/另存为/全部保存、证书加密邮件解锁后阅读、正文查找、自动标为已读（可关）、后台预取最新邮件正文。
- **处理**：回复/全部回复/转发（转发带原附件）、删除到「已删除」并可撤销（不提供彻底删除）、移动（含拖到文件夹）、已读/未读、星标、右键菜单。
- **写信**：同事间默认加密发送（公司通讯录证书，缺证书时由你决定，绝不静默降级）、富文本（字号、颜色、高亮、列表、缩进、链接）、签名、收件人补全（最近联系人 + 公司通讯录 `oab:autoMatch`）、拖放/粘贴添加附件、自动保存本机草稿、忘记附件提醒、发送结果不明时不自动重发。
- **搜索**：输入即显示本机缓存结果，回车合并服务器结果。
- **会话**：可选“保持登录”（DPAPI 保存密码），会话失效自动重连；离线启动先显示缓存。
- **常驻**：只运行一个鸿雁（再次启动会激活已有窗口）；设置中可开启“开机自动启动（最小化到托盘）”（写入 `HKCU\...\Run`，以 `--minimized` 启动，后台照常收信）。

快捷键：Ctrl+N / C 写邮件，J/K 或 ↑/↓ 切换，R 回复，A 全部回复，F 转发，Delete 删除，U 标为未读，S 星标，V 移动，/ 或 Ctrl+F 搜索，F5 收信，写信时 Ctrl+Enter 发送、Ctrl+S 保存草稿。

数据目录：`%LOCALAPPDATA%\CoremailDesktopPreview`（缓存、草稿、发送记录均为密文）。

安装包：`packaging/hongyan.iss`（Inno Setup 6，按用户安装、无需管理员，可选开始菜单与桌面快捷方式）。`build.ps1` 在所有检查通过后调用 `ISCC.exe`（依次查找 `$env:ISCC`、PATH、`.tools\InnoSetup`、常见安装位置），找不到时跳过并提示。卸载默认保留本机数据，勾选后才删除。**免安装使用（推荐用于受管控的电脑）**：把整个 `dist\Hongyan` 文件夹复制到 `dist` 之外任何可写的位置（例如 `D:\workspace\Hongyan`）再运行其中的 `Hongyan.exe`。不要长期直接使用 `dist\Hongyan`：每次打包都会删除并重建它，`build.ps1` 发现其中有 `HongyanData` 时会拒绝打包；数据保存在程序旁的 `HongyanData`（均为密文）。受管控电脑上若程序不能写入 `%TEMP%`、用户目录或磁盘根目录，安装包需用 `dist\Install-Hongyan.cmd` 启动（它把临时目录和默认安装位置都放在脚本旁），卸载用安装目录中的 `Uninstall-Hongyan.cmd`；这类电脑上安装包不写注册表卸载项，开始菜单快捷方式可能无法创建。真机验证结果见 [日常客户端验证](docs/releases/2026-10-17-daily-client.md)。

## 连接验证原型

需要 Python 3.12，无第三方 Python 依赖。在此目录打开 PowerShell：

```powershell
python -m probe --preflight
python -m probe --send-self
```

第二条命令提示在本地输入密码，输入不会回显；也可双击 `tools/legacy/run_probe.cmd`。如需要系统密码输入对话框：

```powershell
python -m probe --password-dialog --send-self
```

默认账号为本次用户指定的 `wangbiao@inspur.com`。`--account` 可明确指定自己的账号。默认只读，只有加 `--send-self` 才向同一账号发送一封固定内容的测试邮件。没有设置第三方收件人的选项。

`--folder-id` 仅选择只读验证的文件夹；自发自收验证始终检查收件箱（fid=0），避免误将已发送副本作为收到的证据。如果终端无法关闭密码回显，原型停止并提示改用本地密码对话框。

## 结果

默认将脱敏验证状态保存到 `analysis/connection-probe-result.json`。报告不保存密码、cookie、sid、普通邮件主题或正文；下载的邮件只在内存中检查 MIME 结构并计算大小与哈希。

- `login.status=passed`：独立会话成功调用邮件接口。
- `read.status=passed`：读取并识别了一封 MIME 邮件。
- `roundtrip.send.status=accepted`：服务器接受发送请求，**不等于已投递**。
- `roundtrip.delivery.status=passed`：在收件箱找到本次唯一标记，并下载 MIME 核对发件人、收件人、主题和正文标记。
- 退出码 0 表示本次请求范围通过；2 表示认证/协议等未通过；3 表示发送被接受但观察窗口内未确认收到；130 表示用户取消。

`--preflight` 仅检查公开登录表单，不验证认证、读取或发送。空收件箱会记录 `skipped_empty_inbox`，不会声称验证了正文读取。

如果服务器要求验证码、二次认证或设备绑定，当前原型停止，不自动重试、不复用已有浏览器/客户端令牌。只有完成真实独立登录与收发验证后，才能决定如何扩展完整客户端。测试邮件会保留在邮箱中，原型不自动删除。

## 实现依据与测试

请求来自公司公开 Webmail 脚本与现有客户端的脱敏协议证据，见 `analysis/protocol-feasibility.md`。发送按公开写信代码先 `continue` 创建写信会话，再 `deliver`；未使用破解或伪造现存设备身份。

```powershell
.venv\Scripts\python.exe -u -m unittest discover -s tests -v
```

2026-10-16 最新回归和构建结果见 [鸿雁验收记录](docs/releases/2026-10-16-hongyan-workflow.md)。运行环境：Python 3.12.10 / PySide6 6.12.0 / cryptography 50.0.2；本机 OpenSSL 无法构造的两项 RC2 样本测试仍跳过。

两点执行环境要求：

- 草稿与发送记录测试会做真实 Windows DPAPI 加解密和临时目录写入，不能在只读或受限沙箱中运行。
- `tests/test_desktop_reader.py` 使用真实 Qt WebEngine 渲染器；Chromium 渲染子进程无法在被限制的沙箱内创建，这类运行需要在放宽沙箱下执行，并仅在测试驱动器里设置 `QTWEBENGINE_DISABLE_SANDBOX=1`（产品自身从不关闭 Chromium 沙箱）。

离线测试验证协议封装、同源凭据边界、错误脱敏、日期扩展，以及服务器接收与实际投递的区别。离线测试通过不能代替真实公司的服务器验证。
