# 2026-10-19 · 通讯录：上级部门显示下属全部成员

## Problem
Most org-tree nodes showed an empty list. The server lists only a department's *own* people (oab:listEx) and many
departments have nobody registered directly.

## Verified (read-only; counts and booleans only — tools/probes/probe_contacts_subtree{,2}.py, tools/verify/verify_contacts_real.py)
- The tree is complete: `oab:getDirectories {}` = 169 nodes in 3 levels; `{dn, needChildCount, depth:3|6|10}` (webmail's call)
  returns the same nodes plus `childCount` and, with a dn, the root node object itself (not a list). Nothing is truncated.
- The user's own department (`@ou` = `<root>/<id>`, a top-level leaf, unique name) lists 2032–2035 people directly, the user
  among them; through MailBackend.members the view gets 2000 rows (the cap), also from the cache. The user's own node works.
- Sampled through the app's own path: 19/28 leaf nodes and 3/15 parent nodes list nobody directly.
- No server-side subtree listing: listEx `condition {@type = XUL}` → FS_UNKNOWN; `XT/jsp/contact.jsp?func=oabSearch
  {dn, recursive:true, keyword:''}` → empty list (it needs a keyword); `oab:searchEx` at the root → 1 entry.
- 12 department names repeat across 55 nodes.

## Fix
- `backend.members(dn)` gathers, for a node with sub-departments, its own people plus everyone in every department
  below it (each listing cached as before), each extra row carrying `unit` (its sub-department path). Caps: 25
  departments, 3000 people; a failed sub-department or a cap marks the list `partial`. The root (the whole group,
  168 departments) is not gathered — company search covers it. Reply adds `direct`, `departments`, `partial`.
- 通讯录 list: sub-department shown after the address, subtitle "N 人 · 含 k 个子部门，直属 m 人", the card has a
  子部门 field, and empty departments explain that nobody is registered there and point to company search.
- Demo: 销售中心 with people only in 华北区/华南区 and an empty 西部区.
