# 2026-10-18 · 通讯录与高级搜索 (v0.3)

## What ships

- **通讯录** (sidebar 本机 section): department tree on the left, members with a filter box (Enter searches the whole
  company through `oab:autoMatch`), a person card with 写邮件 / 查看往来邮件 / 复制邮箱. 个人通讯录 appears above the
  tree only when it has entries. Read-only: nothing is created or edited.
- The tree, each opened department's members and the personal list are kept in the encrypted cache (`kv`: `directory`,
  `pab`, `members:<sha256 of dn>` — no department id in plaintext). They show instantly and refresh in the background;
  offline the cached copy stays.
- **高级搜索**: filter button in the search box opens a panel — 全文/仅主题, 发件人, 收件人, date range (with quick
  ranges), 文件夹, 有附件, 未读, 星标. Active conditions show as chips under the title row (× removes one, 修改条件
  reopens the panel). Filters work without search words. Server results page with 加载更多结果.
- A filter the server does not apply is applied to the returned rows instead and its chip is marked 本机 (dashed,
  amber); the panel lists such filters. On the deployment every filter is server-side, so this path is only exercised
  by tests and other adapters.

## Verified server facts (read-only, 2026-10-18)

Evidence: `analysis/contacts-search-shape-2026-10-18.json`, `analysis/contacts-search-shape2-2026-10-18.json`
(probes `tools/probes/probe_contacts_search.py`, `probe_contacts_search2.py`; counts and booleans only).

- `oab:getDirectories {}` → one root `{id, name, ou}`; 169 departments in 3 levels (118 / 48 / 3); ids unique, no `/`.
  The webmail variant `{dn, attrIds, needChildCount, depth}` returned nothing here.
- `oab:listEx {dn, returnAttrs, start, limit}`: the dn for a department at **any** depth is `<root id>/<department id>`
  (webmail module 49809 builds it the same way); the bare id and the full path return nothing. The root itself is
  listed by its own id (3 people). Rows carry `@id, @type ('U'), true_name, email, duty, @location`; `total` is the
  department's full count (one top-level department has 5610), `start/limit` page disjointly, `limit 1000` is
  accepted (~1.3 s). Most people sit in top-level departments; 2 of 30 sampled second-level departments had members.
  `@location` has one `/` segment per level plus the root. The client fetches pages of 1000 up to 2000 people and says
  when a department has more.
- `pab:searchContacts {}` lists the whole personal address book (57 rows: `id, FN, 'EMAIL;PREF', groups, rev`);
  `pab:getAllGroups` is empty and `pab:getContacts` needs ids, so neither is used.
- Advanced search goes through webmail's form route (`XT/jsp/mail.jsp?func=searchMessages`, the `!mail:searchMessages`
  field list in X.03931.js). Each field narrowed the unfiltered total (533) and every returned row satisfied it:
  `subject` (subject only; `pattern` is full text), `from` / `to` (address substring), `dealings` (whole address:
  mail between me and that person — a domain matched nothing), `sentDate` / `receivedDate` as `YYYY-MM-DD:YYYY-MM-DD
  23:59:59`, `YYYY-MM-DD:YYYY-MM-DD` or `YYYY-MM-DD:`, `attachType=attached|noAttach`, `flagType=flagged|noflagged`,
  `read=false|true`, `fid`. Fields combine, `start/limit` page with filters, an unknown value (`attachType=zzz`) is
  ignored. `fromAddress` with a partial address matched nothing (not used).
- Until-only date ranges are sent as `1970-01-01:<until> 23:59:59` (the verified form) rather than the unverified
  `:<until>`.

## Not verified on the real account

- The UI itself on the real directory (run `run_desktop.cmd` from Explorer and open 通讯录).
- `dealings` with a colleague's address (only verified with the account's own address).
