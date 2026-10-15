# Coremail 独立连接验证与桌面预览版

本仓库包含两件东西：一次性兼容性验证原型（`probe/`）和在其证据上开发的桌面客户端预览版（`coremail_desktop/`）。

**2026-10-15 已完成真实独立登录与自发自收验证**：读取 9 个文件夹、5 条列表和 MIME 邮件，发送到自己后在收件箱下载并核对唯一标记。详见 `analysis/收发可行性验证结果.md` 与 `analysis/connection-probe-result.json`。

**桌面预览版当前状态：中间预览版，不是完整 V0.1。** 界面、协议适配、草稿保护、发送状态机、正文隔离与打包已实现并有 99 项自动化测试通过；真实登录/读取/发送、回复引用头契约与证书双向互通尚未验证。逐项状态见 [V0.1 验收记录](docs/releases/v0.1-acceptance.md)，证书结论见 [证书兼容性报告](analysis/certificate-compatibility.md)。

目标服务器：`https://newmail.inspur.com/coremail/`。`mail.inspur.com` 当前是另一套 Outlook OWA 入口。

## 桌面客户端（预览版）

```powershell
.venv\Scripts\python.exe -m coremail_desktop            # 启动工作台
.venv\Scripts\python.exe -m coremail_desktop --demo     # 合成演示数据，不联网
powershell -File packaging\build.ps1                    # 打包 Windows 启动包
```

界面为简体中文三栏工作台：登录、文件夹、邮件列表（每页 50 条）、阅读区，外加纯文本写信窗口。快捷键：Ctrl+N 写邮件、Ctrl+S 保存本机草稿、Ctrl+K 筛选已加载邮件、Ctrl+F 正文内查找、F5 刷新、Ctrl+Enter 发送。

数据目录：`%LOCALAPPDATA%\CoremailDesktopPreview`（草稿与发送记录均由 Windows 用户级 DPAPI 加密，磁盘上不出现明文主题、正文或收件人）。

明确阻止的行为（界面会说明原因，不会静默降级）：缺收件人证书时的加密发送、未验证的加密提交接口、未验证的回复引用头，以及发送结果不明时的自动重发。

## 连接验证原型

需要 Python 3.12，无第三方 Python 依赖。在此目录打开 PowerShell：

```powershell
python -m probe --preflight
python -m probe --send-self
```

第二条命令提示在本地输入密码，输入不会回显；也可双击 `run_probe.cmd`。如需要系统密码输入对话框：

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

当前基线：99 项通过（Python 3.12.10 / PySide6 6.12.0 / cryptography 50.0.2），见 `analysis/unittest-result.json`。

两点执行环境要求：

- 草稿与发送记录测试会做真实 Windows DPAPI 加解密和临时目录写入，不能在只读或受限沙箱中运行。
- `tests/test_desktop_reader.py` 使用真实 Qt WebEngine 渲染器；Chromium 渲染子进程无法在被限制的沙箱内创建，这类运行需要在放宽沙箱下执行，并仅在测试驱动器里设置 `QTWEBENGINE_DISABLE_SANDBOX=1`（产品自身从不关闭 Chromium 沙箱）。

离线测试验证协议封装、同源凭据边界、错误脱敏、日期扩展，以及服务器接收与实际投递的区别。离线测试通过不能代替真实公司的服务器验证。
