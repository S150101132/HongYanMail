# 2026-10-18 · v0.4 阅读布局、模板、打印与导出

Local UI work only; no new server API and no server writes.

## 双击弹出（pop-out reader）
- Double-click a list row, press Enter, or use the toolbar's 新窗口 button: the message opens in its own resizable window (`ReaderWindow` in `app/shell.py`), loading `hongyan://app/index.html#reader=<mid>`; `app.js` renders a reader-only layout.
- Each pop-out has its own off-the-record profile, scheme handler and request interceptor (same classes as the main window), so the remote-image allowance is per window. Its `Bridge` shares the main bridge's worker threads: backend calls stay on one mail thread, in order.
- Natives of a pop-out are looked up on the window first (find, remote images, `notifyMain`, `closeWindow`), then on the shell. `notifyMain` accepts only `mailChanged` / `authRequired`.
- Read state, star, delete and move made in a pop-out update the main list (`mailChanged`); delete/move close the pop-out and the main window offers 撤销.
- Closing the main window to the tray **keeps pop-outs open and usable**; quitting (tray 退出, or demo mode) and logout close them. Escape / Ctrl+W close a pop-out. Replies from a pop-out open the composer inside that window.

## 会话视图
- Thread mode lists one row per conversation (participants, count, newest time, unread dot). Clicking a multi-message conversation replaces the list with its messages, newest first, under 「← 返回会话列表」 (Backspace/Escape go back); the newest is opened. Single-message conversations open directly. The in-reader thread cards were removed.

## 可调整栏宽
- Drag handles between sidebar|list and list|reader; double-click resets. Prefs `sidebarWidth` (180–400, default 232) and `listWidth` (280–900, default 400), clamped by `set_prefs`. The reading pane keeps at least 420 px.
- Narrow (<1180 px) layout keeps the 64 px icon sidebar (its handle hidden); list title row wraps below 380 px; reader toolbar drops labels below 640 px.

## 打印和导出
- 打印 (Ctrl+P / toolbar / ⋯ menu): the backend builds a standalone document (`print_html`: subject, from/to/cc/date, attachment names, sanitized body; CSP forbids scripts and allows remote images only when the user allowed them for that message). A hidden, script-less view loads it once from `hongyan://app/print/<token>.html` and prints via `QPrintDialog` + `QWebEngineView.print`.
- 导出为 PDF: same document, `printToPdf` (A4, 15 mm margins), bytes written by Python so a blocked location reports `WRITE_DENIED`.
- 导出为 .eml: the original message from the cache/server (`export_eml`). For encrypted mail the default is the original envelope; a decrypted copy needs an explicit choice plus a confirmation that it is saved unencrypted (also asked before a PDF of decrypted mail). Locked encrypted mail cannot be printed/exported as PDF.
- Packaging: `QtPrintSupport` was never excluded and `Qt6PrintSupport.dll` is not in `PROVEN_REMOVABLE`; `Qt6WebEngineWidgets.dll` already depends on it. `QtPdf` stays excluded (not used). No spec change was needed.

## 邮件模板
- Templates (name, subject, body, optional to/cc/bcc) live in the encrypted cache kv (`templates`), at most 100, name ≤ 60 chars, body ≤ 500 KB; attachments are not stored. Saving under an existing name replaces it (the UI asks first).
- Composer 「模板」 button: apply a template, 存为模板, 管理模板. 写邮件 ▾ → 从模板新建. 设置 → 邮件模板: rename / delete.

## Checks
- `tests/test_layout_templates_print.py`: width prefs, templates (encrypted at rest, rename/delete/limit), print document, .eml export (plain, encrypted original, decrypted copy), pop-out windows (shared mail thread, separate interceptor, event allow-list) and real PDF rendering + printing to a PDF printer.
- Screenshots: `tools/screenshot_app.py` (light/dark 1440 and light 1100) cover conversation view, resized columns, export menu, encrypted-export choice, templates, compose menu and a pop-out window.
