# 2026-10-27 · O1 读取 Outlook .pst（并入本地存档，只读）

起因：公司换 Coremail 前，用户从 Outlook 导出了 `D:\work\email\backup.pst`（4.5 GB），希望在鸿雁里也能读。

## 功能

- 设置 → 本地存档：在「文件夹」下面多一项「Outlook 数据文件」，可以「选择 .pst 文件…」或粘贴路径（带引号也行），点「保存并扫描」。只设 .pst、不设官方客户端文件夹也可以。
- 扫描后和官方客户端的存档在同一个「本地存档」列表里：按时间浏览、普通搜索、问鸿雁、只读阅读、回复 / 转发（作为新邮件发出）、查看附件。没有另加入口。
- 只收邮件（普通邮件、S/MIME 加密和签名邮件、会议邀请、撤回和退信通知）；日程、联系人跳过。
- 扫描进度按封数显示百分比；文件没变时下次扫描直接跳过（0 秒）。「停用」同时清掉文件夹和 .pst。

## 做法

- 解析用 libpff-python（libyal，LGPL，PyPI 上有 Windows cp312 wheel，用户 2026-10-27 同意引入），写入 `pyproject.toml`，`packaging/desktop.spec` 加了 hiddenimport `pypff`。
- `archive/pst.py`：
  - 目录行直接从 MAPI 属性生成（主题、发件人、收件人和抄送的 SMTP 地址、发送时间、Message-ID、纯文本正文、附件名），不读附件内容；Exchange 内部地址（`/O=…`）去掉、只留姓名。
  - 打开时按属性重建为标准邮件：HTML 正文按 `PR_INTERNET_CPID`（936 → GB18030）解码，正文图片作为 `multipart/related` 内嵌，附件和嵌入的邮件（`message/rfc822`）照原样；S/MIME 加密邮件还原为 `application/pkcs7-mime`，多部分签名邮件直接用 Outlook 保存的原始签名体，所以现有的解密和验签照常工作。
  - 用文件对象打开：libpff 自己的打开函数会把 `D:/…` 路径拼错。
- `archive/catalog.py`：.pst 是一个名为 `outlook.pst` 的「包」，行记录它在文件夹树里的位置。打开时核对文件大小、修改时间和路径，再核对节点 ID 和发送时间，不符报 `ARCHIVE_CHANGED`（需要重新扫描）。ID 由 Message-ID 生成，移动文件不会变。
- 后端 `set_archive_folder(folder, pst)`；新错误码 `ARCHIVE_PST_NOT_FOUND`；外壳加了 `pickPst` 文件选择框。
- 原文不复制：目录照旧加密保存在 `cache\<账号>.archive.db`，打开时从 .pst 重新读取。

## 检查

- `tests/test_pst.py`：5 项，使用测试里伪造的 pypff 对象（没有真实 .pst）：属性生成目录行、重建邮件的正文图片和附件、目录增量扫描、位置变动后拒绝、改动后重新扫描、停用；后端设置、错误码、桥接。`tests/test_archive.py` 照旧通过。
- 全量测试：778 项，4 项报错都在传输层集成测试（测试临时目录里的 `hongyan-network.log` 找不到），与本次改动无关，单独重跑通过。
- 真实文件只读复核（`tools/verify/verify_pst_archive.py`，只记录数量和耗时，结果在 `analysis/pst-scan.json`）：

| 项 | 结果 |
| --- | --- |
| 文件 | 4.5 GB，36 个文件夹，3171 个条目 |
| 收录邮件 | 3021 封，0 失败；跳过 149 个非邮件条目（联系人 113、日程 36） |
| 加密邮件 | 454 封（只记录信封信息，阅读时用个人证书解密） |
| 年份 | 2024 年 2799 封，2025 年 220 封，其他 2 封 |
| 首次扫描 | 约 4 分钟（一次性，后台进行；主要花在 libpff 读取约 2.5 万个附件的属性上） |
| 再次扫描 | 文件未变，0 秒跳过 |
| 抽样完整读取 | 150 封全部解析成功（HTML 118、纯文本 32），平均 0.45 秒，最慢 3 秒（大附件） |

## 待真机验收

- 在鸿雁里：设置 → 本地存档 → 选择 `D:\work\email\backup.pst` → 保存并扫描（约 4 分钟），在「本地存档」里打开几封、看附件、搜索一次；打开一封加密邮件确认能解密。
- 安装版还没有用 `build.ps1` 打包验证（确认 pypff 的 .pyd 被打进包）。
