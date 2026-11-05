# 超大邮件分段读取（2026-11-03）

打开附件很大的邮件（原文超过 64 MB）时，以前会先下载约 64 MB，然后报「服务器响应超过大小限制」。现在照网页版的方式分段读取：先取正文和正文里显示的图片，附件等用户打开或保存时再单独下载。

- **接口（2026-11-03 只读实测，`analysis/large-message-shape-2026-11-03.json`，`tools/probes/probe_large_message.py`）**：
  - `mbox:readMessage {id, markRead:false}`：只返回结构，不带内容：`var {subject, from[], to[], cc[], sentDate, flags, priority, html/text {id, contentType, encoding, contentLength, estimateSize}, attachments[{id, filename, contentType, contentId?, encoding, contentLength, estimateSize}], inlineAttachments[]}`。`estimateSize` 是解码后的大小。读完未读邮件仍是未读。
  - `mbox:getMessageData {mid, part}`（GET，`mboxa` 放在查询串）：返回这一段解码后的内容，`Content-Type` 是该段的类型，带 `Content-Disposition`。
- **何时分段**：列表里记录的大小超过 64 MB 时直接分段读；下载整封原文时报 `RESPONSE_TOO_LARGE` 也改为分段读。本机文件和本地存档不受影响。
- **正文**：html 和 text 两段分别取。`getMessageData` 不带字符集，所以先按 UTF-8 解码，不行就用 HTML 里声明的字符集，再不行用 GB18030。html 和普通邮件一样经过 `sanitize_rich_html` 清理。
- **图片和附件**：正文用 `cid:` 引用、5 MB 以内的图片当时取下来内嵌显示，不放进附件列表；其余都列为附件，打开或保存时才下载。
- **限制**：加密邮件只能整封解密，所以仍然报大小超限；单个附件超过 64 MB 仍会失败。另存为 .eml、AI 理解邮件的批量预取仍然要下载整封原文，大邮件在这两处照旧失败。分段读到的邮件不检查签名，也不识别会议邀请。

## 验证

- `tests/test_large_message.py`：正文加内嵌图片、附件按需下载、没有正文时仍报超限。
- 真实账号只读探测：收件箱里最大的两封（约 79 MB 和 18 MB）结构读取成功，两张内嵌图片都能单独下载，未读状态没有变。还没有在真实客户端里打开过这封邮件。
