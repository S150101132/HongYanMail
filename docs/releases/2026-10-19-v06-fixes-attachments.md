# v0.6 · 文件夹删除选项、通讯录拖动分栏、标签折叠、附件打开与应用内查看

日期：2026-10-19　分支：`claude/v06-fixes-attachments-fe2c43`　对应待修复问题 B1、B2、B3、B4、B6（`docs/roadmap/待实现功能.md`）。

## B1 删除非空文件夹

对话框三选一（默认第一项）：

1. **删除文件夹及其中的邮件**：邮件先移到「已删除」，仍可找回，**不彻底删除**；
2. **删除文件夹，把邮件移到「收件箱」**；
3. 保留文件夹和邮件，不删除。

后端 `delete_folder(folder_id, mail='trash' | 'inbox')`；其他取值一律 `FOLDER_NOT_EMPTY`，邮件永远不会随文件夹一起被删除。移完后服务器上仍有邮件则保留文件夹。有子文件夹时仍先拒绝（`FOLDER_HAS_CHILDREN`）。

## B3 通讯录三栏可拖动

组织树与人员列表右侧各有分隔条，复用邮件界面的 `SPLITS` 拖动代码（现在每项写明“同排的其他固定栏”和最后一栏的最小宽度 320 px）。双击分隔条恢复默认（250 / 340 px）；宽度存在 prefs `ctTreeWidth`（160–480）、`ctListWidth`（240–720），后端按同样范围钳制。原来 1180 px 以下的固定窄宽度规则去掉，由详情栏 `minmax(320px, 1fr)` 保底。

## B4 标签默认折叠

左侧「标签」标题变成可点的折叠按钮，默认折叠，状态存 prefs `tagsOpen`。正在查看某个标签时该列表保持展开。

顺带修正：标签原来只在 `enterApp` 的钩子里加载，而网页桥可能在 `rules.js` 载入前就已连上，这时整个会话都不显示标签（演示走查里复现）。现在第一次渲染文件夹列表时也会加载一次。

## B2 打开 Excel 附件慢 —— 测量与修复

工具：`python -m tools.measure_attachment_open`（演示数据）／`--real 账号`（只读，密码框，不标已读）／`--launch`（另外计时交给系统的那一步，会真的打开 Excel）。只记录毫秒、字节数和计数，写到 `analysis/attachment-open-timing-<日期>.json`。

演示数据（本机，冷缓存）：

| 步骤 | 耗时 |
| --- | --- |
| 取原文（演示适配器） | 25–300 ms |
| 解密/解析整封邮件 | 56–284 ms（含首次导入） |
| 取出附件 | < 0.1 ms |
| 写入 `opened/` | 1–3 ms |
| 应用内表格预览（3.5 KB 演示表） | 2–35 ms |
| 应用内表格预览（1 MB、2 万行的合成表，只取前 2000 行） | 约 340 ms |

结论：我们这边真正花时间的是两件事，而且都发生在**窗口线程**上：

1. 旧的 `native_openAttachment` 是 shell 的原生方法，在 Qt 主线程里同步执行——邮件不在内存缓存（最近 8 封）时要在主线程上重新取原文、解密、解析；
2. 随后 `QDesktopServices.openUrl` 在主线程里调用 ShellExecute。Excel 的文件关联走 DDE 启动握手，ShellExecute 要等 Excel 起来并应答，期间窗口卡住；而且 Excel 作为鸿雁的子进程启动，继承了鸿雁进程的环境和限制。

修复：

- `openAttachment` 改为后端方法 `open_attachment`，在 `hongyan-fast` 工作线程执行（不排在列表/同步之后，也不占窗口线程），写入 `opened/<随机>/<安全文件名>` 后立即返回，页面提示“正在用系统中的程序打开…”。
- 交给系统改为 `explorer.exe <文件>`：由已在运行的资源管理器打开文件，程序成为桌面 Explorer 的子进程而不是鸿雁的子进程，鸿雁也不再等待 DDE 握手。`explorer.exe` 无法启动时退回 `os.startfile`。
- 文件名按 Windows 规则清洗（`<>:"/\|?*`、保留名 CON 等、半角逗号换成全角——explorer 会把逗号当参数分隔），保存全部附件也用同一规则。
- 新增：程序和脚本类附件（.exe .bat .cmd .js .vbs .hta .msi .ps1 .lnk .reg 等）不能从邮件里直接「打开」（`ATTACHMENT_BLOCKED`），仍可另存。

**未验证**：真实账号上的计时和 Excel 启动时间需要用户在本机运行 `--real`（只读）和 `--launch`，本次没有运行。

## B6 应用内查看更多附件类型

| 类型 | 做法 |
| --- | --- |
| .xlsx .xlsm .xltx | 每个可见工作表一张表格，底部工作表标签切换；数字格式（日期、百分比、千分位、小数位、¥）、合并单元格、列宽、布尔和错误值；隐藏工作表不显示；每表最多 2000 行 × 100 列、整本 20 万格，超出时提示用系统程序打开 |
| .docx .docm .dotx | 转成带格式的 HTML：标题（含中文版 Word 的数字样式 id）、粗斜下划删除线、颜色字号底色、上下标、编号/项目符号（按 numbering.xml 计数）、对齐、表格（横向合并、纵向合并）、超链接、内嵌图片（PNG/JPEG/GIF/BMP/WebP，转 data: URI） |
| .pptx .pptm .ppsx .potx | 每页一张卡片：标题、正文段落（按级别缩进）、表格、图片 |
| .html .htm .xhtml | 与邮件正文相同的清洗（`sanitize_rich_html`），另外去掉 script/title/iframe/object 等整块内容；允许内嵌 data: 图片；外部图片默认被 CSP 拦截，顶部「显示网络图片」按钮按需放开 |
| BMP、ICO、AVIF | 按文件头识别，Chromium 直接显示 |
| TIFF | Qt 图像插件转 PNG 后显示（`shell._png_from_image`，工作线程） |
| SVG | 先清洗（删 script、foreignObject、on* 属性、外部 href 和 url()），再作为 `<img>` 显示（`<img>` 中的 SVG 本来就不执行脚本、不加载外部资源） |
| 文本/代码 | 新增约 80 个扩展名（py js ts java c/cpp go rs sql sh ps1 css vue toml ics vcf 等）；编码按 BOM → 声明 → UTF-8 → GB18030 |
| .doc .xls .ppt、损坏或加密的 Office 文件、其他类型 | “这个附件不能在鸿雁里预览” + 「用系统中的程序打开」 |

HTML、Word、PowerPoint 和表格都显示在预览框内的 `sandbox="allow-same-origin"`（不允许脚本）iframe 里，CSP 为 `default-src 'none'; style-src 'unsafe-inline'; img-src data:`，与邮件正文一致；链接点击由 shell 交给浏览器。Office 解析只用标准库（`zipfile` + `xml.etree`，新模块 `coremail_desktop/mime/office.py`）：宏部件从不读取，外部关系忽略，每个部件 ≤ 64 MB、整包解压 ≤ 160 MB、图片合计 ≤ 20 MB、HTML ≤ 4 MB；超限或损坏一律 `PREVIEW_UNAVAILABLE`。**没有新增 Python 依赖**，打包配置不变（QtGui 图像插件本来就在包里）。

邮件正文的清洗器也因此受益：`<script>`、`<title>` 等的内容以前会被剥掉标签后作为文字显示出来，现在整块去掉。

### “用系统程序打开 HTML 也失败”的原因

在本机无法用真实附件复现（需要用户启动程序）。代码层面能确认的问题有两个，均已修复：

1. 写入 `opened/` 时直接用附件原名，名字里含 `:` `?` `"` `|` 等 Windows 不允许的字符时写文件就失败，页面只看到“内部错误”；现在先清洗文件名。
2. HTML 的默认程序是基于 Chromium 的浏览器。它作为鸿雁的子进程启动时继承鸿雁进程的环境与限制——正是这些限制让鸿雁自己的 Chromium 沙箱失败、需要兼容模式重启；浏览器在同样条件下可能直接退出。现在改由桌面 Explorer 启动（见 B2），浏览器不再是鸿雁的子进程。

第 2 点是推断，**需要用户在真实程序里点一次 HTML 附件的「打开」确认**；无论结果如何，HTML 附件现在都可以直接在鸿雁里查看。

## 验证

- 单元测试：新增 `tests/test_attachment_viewer.py`（Office 读取、数字格式、截断、损坏包、SVG/HTML 清洗、预览种类、打开写副本和拦截、文件名清洗、TIFF 转换、桥接方法在工作线程），`test_mail_management` 新增删除文件夹移到收件箱，`test_layout_templates_print` 新增通讯录宽度与 `tagsOpen`。全量 `unittest discover -s tests`：**486 项通过，12 项跳过**。
- 走查：`tools/screenshot_v06.py`（`HONGYAN_WALK_TRACE=1`）19 张截图在 `analysis/screens-v06/`：标签折叠/展开、删除文件夹三选一与移到收件箱、通讯录默认/拖宽/双击恢复、表格两个工作表、Word（含缩放）、PPT、HTML（拦截与放开网络图片）、SVG、BMP、旧版 .xls 回退、打开提示。无 JS 错误；唯一的控制台输出是 HTML 演示附件里的外部图片被 CSP 拦截（预期）。`screenshot_app.py`、`screenshot_manage.py` 同样跑过。
- 已有的 `ResizeObserver loop completed with undelivered notifications` 提示在改动前的基线上同样大量出现（阅读区 iframe 自适应高度），不是本次引入，未处理。
- 打包：`packaging\build.ps1` 通过（WebEngine 阅读器、启动冒烟、中性目录启动、显示缩放、隐私审计全部通过；包内含 Qt 的 TIFF/ICO 图像插件）。本工作树没有 Inno Setup，安装包未生成，由主线合并后统一出包。
