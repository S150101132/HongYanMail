# v0.5 写信与发送：撤销发送、服务器草稿、正文图片、表格、离线发件箱、附件转发与 .eml

Branch `claude/v05-compose-send-7bab9c`. Probe: `tools/probes/probe_compose_v05.py`
(shapes in `analysis/compose-v05-shape-2026-10-18.json`). Real check through the app's
own path: `tools/verify/verify_compose_real.py`
(`analysis/compose-v05-real-verification-2026-10-18.json`). Both are send-to-self
only, and record booleans, counts and content types. Every test message was moved
to the trash afterwards.

## Verified server facts (2026-10-18/19, send-to-self)

| Question | Answer |
| --- | --- |
| `mbox:compose` action `save` on a fresh compose | `S_OK` + `draftId` (equals the id the drafts folder lists). The flags are `draft`, `read`. |
| Reopen a draft: `XT/jsp/compose.jsp ctype=draft` | `var` = `{id (new compose id), account, to[], subject, content, isHtml, charset, deliveryMsgType, modifiedDate, saveSentCopy}`. The content round-trips. |
| `save` again from the reopened session | Exactly **one** copy is left, under a **new** message id. The old id is gone and the subject is updated. |
| `deliver` from the reopened session | `S_OK`; the draft is **removed** from the drafts folder and the mail arrives. |
| Inline image upload | Use `upload:prepare` + `directData` with `inlined`. The body `<img src>` = `s/json?sid=…&func=mbox:getComposeData&composeId=…&attachId=N` (absolute or relative). On deliver, the server rewrites it to `cid:`: the result is multipart/related, the cid resolves, the image is intact with disposition inline, and no sid or `getComposeData` is left in the body. |
| `message/rfc822` through the normal upload | It arrives as a real `message/rfc822` part, with the inner headers intact. |

Through MailBackend (verify_compose_real): see the "Real check" section below.

## How each feature works

**撤销发送 (undo send).** The 发送 button calls `send(hold=True)`. It runs the same
checks as before: recipients, schedule, shared-mailbox rights, the signing key, and
missing recipient certificates. These prompts come *before* the delay. It then saves
the draft durably in 本机草稿 and only holds it in memory, returning `Held`. The
default delay is 10 s; settings offer off/5/10/20/30. The countdown bar says
"正在发送「…」… N 秒后发出 [撤销]". The release timer runs in the **main window**
(`AppShell.hold_send`), not in the page.
- 撤销 → `cancelSend` (on the fast thread, so it never waits behind mail work). The
  draft comes back from the store with everything: recipients, subject, HTML, tables,
  pictures, attachments, encrypt/sign, receipt, schedule, server-draft link. A lock in
  the backend decides between undo and release, so a mail is never both undone and
  sent, and never sent twice. An undo after the release started gets `SEND_NOT_HELD`.
- Closing to the tray, or closing the pop-out that sent it: the timer still fires and
  the mail is sent. While hidden, a tray message says how it ended.
- Quitting or logging out during the delay: `abandon_held()` drops the holds and
  stops the timers. **Nothing is sent.** The drafts stay in 本机草稿, and the next
  start shows "上次退出时有 N 封邮件还在撤销等待中，没有发出". A crash or kill gives
  the same result, because the hold exists only in memory.
- The outbox is untouched until the release: the send job (`OutboxService`) is created
  only then. So all of its guarantees still hold, including never resending an
  Unknown outcome. Encrypted/signed mail is built at release time.
- Scheduled sends are not held: the server keeps them, and they can be cancelled in
  定时发送. Receipt requests travel with the draft.

**离线发件箱 (offline outbox).** A send is queued only when it failed with
`NETWORK_ERROR`/`TLS_ERROR` **before** anything was submitted:
- during preparation (`continue`, uploads, certificate lookup), which the outbox
  records as `Failed`;
- or while signing in again.

The queue (ids and subjects) is in the encrypted cache, and the drafts are in the
DPAPI draft store. After every successful sync (poll, or push reconnect), the shell
calls `flush_outbox`. It sends oldest first and stops at the first draft that is
still offline.

A submission that may have reached the server (state `Unknown`) is never queued and
never resent. It appears in 发件箱 as "发送结果待确认", with 查看“已发送” /
打开草稿 / 已核对 (`Reviewed`, still non-replayable).

Offline encrypted mail: the certificate check is skipped at send time and runs when
the message is built. A missing certificate then fails the mail (`RECIPIENT_CERT_MISSING`);
it is never sent unencrypted.

**服务器草稿箱 (server drafts).** Opening a drafts-folder message shows the banner
"这是服务器草稿箱中的草稿 [继续编辑]". You can also double-click it, or use 继续编辑草稿
in the right-click menu. `edit_server_draft` loads it from the MIME: recipients,
subject, HTML (cid images become data: URIs), attachments and the receipt request.
The draft value carries `serverDraftId`.
- 保存草稿 / Ctrl+S / closing the composer: saves locally, then
  `save_server_draft` reopens the draft (`open_draft`), uploads the current
  attachments and pictures into that session, and runs `save`. The server keeps one
  copy under a new id. Then the local copy is removed: the server copy is the record.
  Autosave (every 20 s) stays local only.
- Sending reopens the same session, so the server consumes the draft. This holds on
  the encrypted pre-encoded path too.
- Encrypted or signed drafts are **never** written to the server
  (`DRAFT_ENCRYPTED_LOCAL`). They stay in 本机草稿, encrypted at rest.
- Scheduled drafts: use 取消定时 first (`DRAFT_SCHEDULED`). Shared-mailbox drafts
  stay in webmail.

How 本机草稿 and 草稿箱 relate: 本机草稿 holds local working copies only (autosaves,
new mail, encrypted drafts); nothing goes to the server by itself. 草稿箱 is the
server's folder. A server draft you are editing has a local copy only while it has
unsaved changes.

**写信插入图片 (pictures in the body).** A pasted screenshot, a picture dropped onto
the text, or one picked with the 图片 button goes into the body as a data: URI. A
clipboard that also carries text/html (Excel/Word) still uses the normal paste, so
tables stay tables. Non-image files still become attachments. Pictures count toward
the attachment limits.

At send time, `compose/inline.py` extracts them (an identical picture is sent once):
- the normal path uploads them `inlined` and points the body at the compose-data URL,
  which the server turns into multipart/related (verified above);
- the S/MIME path (`inner_entity`) writes `multipart/alternative[text, related[html, images]]`
  with cid references inside the envelope / signed part.

Pictures in quoted originals stay in replies and forwards.

**表格 (tables).** The 表格 button opens an 8×10 size picker. When the caret is in a
table, it (or a right-click on a cell) offers:
- insert a row above/below, or a column left/right;
- delete a row or column;
- merge with the cell to the right;
- set the border to solid, dashed or none;
- delete the table.

Tab moves to the next cell, Shift+Tab to the previous one, and Tab in the last cell
adds a row. Tables use inline styles, so they survive sending (HTML), local and
server drafts, undo-send, and quoting in replies. The composer shows a faint dotted
guide that is never sent.

**作为附件转发 (forward as attachment).** It is in the ⋯ menu and in the list's
right-click menu. The original is attached exactly as stored, as `<subject>.eml`
(`message/rfc822`). An encrypted original stays encrypted, and the composer tells the
user that recipients need a certificate that can decrypt it. The S/MIME builder
attaches it as a message part, not base64.

**打开 .eml (open an .eml file).** In the 写邮件 ▾ menu, 打开 .eml 邮件文件… opens
the file (up to 40 MiB) in a pop-out reader. The file is kept in memory under an
`eml:` id and is read-only: no flag, move, delete or tag actions, and it is never
uploaded. Encrypted files decrypt when the certificate is unlocked.

Reply and forward work. A reply cannot be threaded to a message the server does not
have, so it goes out as new mail with the quote and Re: subject.

## Code map

- `compose/inline.py`: new.
- `compose/smime.py`: related parts and rfc822 attachments.
- `protocol/coremail.py`: `upload_attachment(inlined)`, `compose_data_url`,
  `create_compose`/`_prepared` with inline content, server-draft sessions,
  `save_server_draft`.
- `models.py`: `DraftDocument.server_draft_id`.
- `outbox/service.py`: `review_jobs` (read-only), `dismiss`. These are wired in
  `storage/services.py`.
- `app/backend.py`:
  - `send(hold)`, `_dispatch`;
  - `release_send`/`cancel_send`/`held_sends`/`abandon_held`;
  - the outbox calls;
  - `edit_server_draft`, `save_draft(server)`;
  - `compose('forward_attach')`, `import_eml`;
  - the `undoSeconds` pref.
- `app/shell.py`:
  - `send_held` signal and timers;
  - `INTERNAL_METHODS` (the page cannot call `releaseSend`);
  - `broadcast`;
  - outbox flush after a good poll;
  - quit abandons holds;
  - `native_importEml`, `native_pickFiles(images)`.
- `web/compose.js`: new; holds the v0.5 UI. `app.js` only gained small hooks in the
  composer, send flow, list and reader.
- `index.html`: 发件箱 nav, 图片/表格 buttons, 撤销发送 setting.
- `rules.js`: no tags on imported files.
- Demo:
  - two server drafts (one with a table and an attachment);
  - an `offline` switch;
  - sent mail keeps its HTML and pictures;
  - drafts are consumed on send.
- Tests: `tests/test_compose_v05.py` (37). Full suite: 442 OK.
- Screens: `tools/screenshot_app.py` (`HONGYAN_WALK_V05=1` runs only the v0.5 steps).
