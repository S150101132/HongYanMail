# 2026-10-19 · v0.5 文件夹管理、全部已读、垃圾邮件与黑白名单、彻底删除、附件预览

Branch `claude/v05-mail-management-575144`. Code: adapter section "Folder management" ff. in
`protocol/coremail.py`, backend sections "Folder management" / "Spam" / "Permanent deletion" /
"Sender blocklist" and `attachment_preview` in `app/backend.py`, PDF rendering in `app/shell.py`
(`native_previewPdfPage`), all UI in the new `web/manage.js` (wraps app.js the way rules.js does;
app.js itself is unchanged), demo data in `app/demo.py`.

## Sidebar layout fix

The folder list was its own scroll box (`#folder-list { overflow: auto }`) inside a column whose
other sections were fixed, so it was squeezed and — because its grid track grew to the longest
folder name — scrolled sideways too (the bar across 病毒邮件 in the user's screenshot). Now the
whole sidebar body (`#side-scroll`: folders, 定时发送, 共享邮箱, 标签, 本机) scrolls as one area,
nothing scrolls sideways (`grid-template-columns: minmax(0, 1fr)`, ellipsized names), and the
compose button and account footer stay fixed. `tools/screenshot_sidebar.py` checks six window /
sidebar-width combinations (180–400 px wide, 600–1040 px tall, 16 folders, shared mailbox open,
11 tags) and reports any element that scrolls sideways: before = `folder-list`, `shared-list`;
after = none. Screens: `analysis/screens-v05/sidebar-before/`, `sidebar-after/`.

## Sources

- Webmail bundles (`analysis/web-login/`): `X.03931.js` module 88812 (createFolders →
  `mbox:createUserFolders {items:[{name, parent}]}`, updateFolders → `mbox:updateFolders {items}`,
  doEmptyFolder → `mbox:emptyFolder {id, permanently}`, trimName), `S.03931.js` (folder settings:
  `mbox:deleteFolders {ids}`, empties a non-empty folder first), `173.03931.js` (Ze.report /
  Ze.notJunk: `updateMailAttrs(ids, {fid:5}, {reportType:'spam'})` and `{fid:1}, 'notspam'`;
  realDeleteMails → `mbox:deleteMessages {ids}`), `M.03931.js` (全部设为已读 lists the unread ids,
  then marks them), `public-login.js` (user attrs `safelist`, `refuselist`).
- Read-only probes: `tools/probes/probe_mail_management.py` →
  `analysis/mail-management-shape-2026-10-19.json`; `tools/probes/probe_folder_tree.py` →
  `analysis/folder-tree-shape-2026-10-19-*.json`.
- Writes, approved by the user, on self-created objects only:
  `tools/verify/verify_mail_management_real.py` → `analysis/mail-management-real-verification-2026-10-19.json`.

## Verified on the deployment

Read-only (2026-10-19, `mail-management-shape-2026-10-19.json`, `folder-tree-shape-2026-10-19-0913.json`):
- index.jsp: `enableReportSpam: true`, `enableRealDelete: true`, `enableDelete: true`, `maxFolderNestLevel: 5`
  (also present: `limitFolderCount`, `folderCount`, `enablejunknotify`).
- `mbox:getAllFolders {stats:true}`: each folder `{id, name, flags{system?}, keepPeriod, stats{messageCount, unreadMessageCount, …}}`;
  system folders are ids 1–6 with `flags.system: true`, user folders have large ids (e.g. 975274) and `flags: {}`.
  **A sub-folder is not a top-level item: it is nested in its parent's `children` list**, with no parent-id field
  (the RPC and `mail.jsp?func=getAllFolders` agree). `list_folders` now flattens the tree and records the parent.
- `mbox:listMessages {fid, filter:{flags:{read:false}}}` returns only unread mail; its `total` equals the folder's
  `unreadMessageCount` (3 = 3). `filter:{read:false}` is ignored (returns everything).
- `user:getAttrs {optionalAttrIds:['safelist','refuselist']}` → comma-separated strings (counts at the time: 1 / 0).

Writes (user's OK, 2026-10-19 09:14, `mail-management-real-verification-2026-10-19.json`; only self-created objects):

| Call | Result |
| --- | --- |
| `mbox:createUserFolders {items:[{name, parent:0}]}` | top folder created, `flags.system` false |
| `mbox:createUserFolders {items:[{name, parent:<id>}]}` | sub-folder created, listed under the parent's `children`, parent recognised |
| `mbox:updateFolders {items:[{id, name}]}` | renamed, read back |
| `mbox:deleteFolders {ids:[parent]}` while it has a child | refused by the server (`RPC_REJECTED`, seen in the first, interrupted run); the backend refuses earlier with `FOLDER_HAS_CHILDREN` |
| `mbox:deleteFolders {ids:[id]}` | child, then parent deleted; both gone |
| `user:setAttrs {attrs:{refuselist}}` / `{safelist, refuselist}` | test address added to the blocklist, then moved to the allowlist (gone from the blocklist), then removed; both lists identical to before |
| `mbox:updateMessageInfos {ids, attrs:{fid:5}, reportType:'spam'}` | self-sent mail moved to 垃圾邮件, confirmed in `originalMsgInfos` |
| `… {attrs:{fid:1}, reportType:'notspam'}` | moved back to the inbox |
| `mbox:deleteMessages {ids}` (after moving to 已删除) | gone from 已删除 and the inbox |
| mark all read in a test folder (filter list + multi-id update) | 1 unread → 0 |
| delete a folder still holding mail (`mail='trash'`) | 1 message moved to 已删除 first, then the folder deleted; the mail was in 已删除 |
| `mbox:emptyFolder {id, permanently:true}` on a test folder holding one self-sent mail | folder count 0; the mail is in neither 已删除 nor the inbox (permanent) |
| cleanup | the remaining test copies (incl. 已发送) purged; 0 test mails and 0 test folders left |

The real inbox, 已删除 and 垃圾邮件 were never emptied or marked read. The first run (09:09) stopped at the
sub-folder step because the RPC nests sub-folders (fixed above); its two empty test folders were found with
`probe_folder_tree.py` and deleted with `--cleanup` (children first, only verifiably empty `HY测试…` folders).

## Design notes

- **Deleting a folder never deletes mail.** If it still holds mail the dialog offers 「先把 N 封邮件
  移到已删除，再删除文件夹」 or 「保留」; the backend moves every message with the verified multi-id
  move, re-reads the folder, and only deletes it when the server reports it empty
  (`FOLDER_NOT_EMPTY` otherwise). Folders with sub-folders are refused (`FOLDER_HAS_CHILDREN`;
  the server refuses them too). Only the user's own, non-system folders can be renamed or deleted;
  shared mailboxes' folders are their owner's.
- **Names** follow webmail's trimName: 1–24 width units (CJK = 2), none of `% ' " \ / : * ? < > | = , ^`;
  same-name siblings are refused locally (`FOLDER_EXISTS`). Nesting is capped at 5 levels
  (`maxFolderNestLevel` in index.jsp).
- **全部标为已读** uses the server's own unread filter, so mail beyond the loaded page is included;
  it works in shared mailboxes with write access.
- **Spam:** 举报 moves to 垃圾邮件 with `reportType:'spam'`; 这不是垃圾邮件 moves back to the inbox
  with `'notspam'`. The optional 「同时加入黑名单 / 白名单」 writes the sender to the list; an
  address moves between the lists rather than sitting on both. The user's own addresses and
  `*@` own domain are refused for the blocklist. Not offered for shared mailboxes (webmail neither).
- **彻底删除 / 清空** only in 已删除 and 垃圾邮件; every path (menu, toolbar, Delete key in 已删除,
  pop-out reader, list-header 清空) goes through two dialogs that state the count and that it
  cannot be undone. Delete in 垃圾邮件 still moves to 已删除.
- **Attachment preview** (reader: 👁 button or click the name; ←/→ pages, +/−/0 zoom, Ctrl+wheel):
  - The kind is decided from the bytes, not the name: PNG/JPEG/GIF/WebP signatures, `%PDF-`,
    or a text extension without NUL bytes. SVG/HTML are never previewed (they can carry script).
    Office files keep 打开 (external app) — no safe in-app renderer.
  - Images reach the page as `data:` URIs (allowed by the page CSP for `img-src`), text is set with
    `textContent`.
  - PDF: the Chromium PDF viewer stays disabled (`PdfViewerEnabled` False): it would run as its own
    extension page inside the profile that hosts the bridge. Instead `QPdfDocument` (QtPdf/PDFium,
    built without PDF JavaScript and XFA) loads the bytes from memory and renders one page at a
    time into a QImage composited on white; the page receives only a PNG. No PDF links, forms,
    scripts or embedded files ever reach the web view. Damaged or password-protected PDFs return
    `PREVIEW_UNAVAILABLE`. Limits: images 20 MB, PDF 50 MB, text 2 MB shown.
  - Packaging: `PySide6.QtPdf` moved from the excluded modules to `hiddenimports`, `Qt6Pdf.dll`
    is no longer removed (`Qt6PdfQuick.dll` still is). `packaging/build.ps1` passed (WebEngine
    reader, startup smoke, neutral directory, display scaling, privacy audit); Inno Setup is not
    installed on this PC, so no installer was built.

## Screens

`analysis/screens-v05/` (light 1440, dark 1280; `tools/screenshot_manage.py`): folder menus, create
dialog and invalid name, folder tree, delete dialogs, 全部标为已读, spam report and folder banner,
彻底删除 (both confirmations), batch toolbar in 已删除, 清空, 设置 › 黑白名单, attachment row,
image / PDF (page 1 and 2) / text previews.

## Tests

`tests/test_mail_management.py`: adapter payloads (folders, nested `children`, purge, empty,
reportType, unread filter paging, list attrs, ALLOWED), backend rules (tree order and depth, limits,
system/shared refusals, delete-with-mail moving to trash, mark-all-read, spam + lists, purge only in
trash/spam, empty), preview kinds by content, the shell's QtPdf rendering (pages, clamping, bad
files), web hooks, messages and the packaging spec. Full suite: 418 tests OK (3 skipped).

## Not done / follow-ups

- Folders are shown expanded; there is no collapse toggle and no drag-to-reparent (webmail's
  `updateFolders {parent, before|after}` exists but was not verified).
- 全部标为已读 / folder actions in shared mailboxes follow webmail and were exercised on demo data
  only (the account has no shared mailbox).
