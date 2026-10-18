# 2026-10-18 · v0.4 收信规则、休假自动回复、会议邀请、标签

Branch `claude/v04-rules-invites-tags-e6627b`. Code: `protocol/rules.py` (server shapes ↔ page views),
`mime/icalendar.py` (invitation parser), adapter/backend sections "Mail rules, auto-reply, tags" and
"Meeting invitations", `web/rules.js` (all UI), demo data in `app/demo.py`.

## Sources

- Webmail bundles (`analysis/web-login/`, public static files): `X.03931.js` (tags, listTags, readMessage,
  cal:respond), `173.03931.js` (tag manager), `public-login.js` (user:getAttrs/setAttrs, tag colour names), and
  **`S.03931.js`** (settings chunk 950, fetched 2026-10-18 from `/coremail/bundle/S.03931.js`,
  sha256 `2ac466b1…`): the rule editor and auto-reply form.
- Probes (read-only, shapes only): `tools/probes/probe_rules_tags_cal{,2,3}.py` →
  `analysis/rules-tags-cal-shape-2026-10-18.json`, `rules-tags-shape2-2026-10-18.json`,
  `rules-cal-sample-shape-2026-10-18.json` (a rule the user made in webmail).
- Writes, approved by the user and undone in the same run: `tools/verify/verify_rules_tags_real.py` →
  `analysis/rules-tags-real-verification-2026-10-18.json`.

## Verified on the deployment

### Mail rules
| Call | Shape | Status |
| --- | --- | --- |
| `user:getMailRules {}` | var `[{id:int, name, continue, operator:'or'?, disabled?, condictions:[…], actions:[…]}]` (server spelling *condictions*) | read 2026-10-18 |
| `user:addMailRules {items:[rule]}` | rule sent with `conditions`; stored back as `condictions` | add/read-back verified |
| `user:updateMailRules {items:[rule]}` | full rule with id; also partial `{id, disabled}` (webmail's switch) | verified |
| `user:adjustMailRuleOrder {id, ref, value:0}` | swaps the rule with neighbour `ref` (webmail up/down) | up + back verified; `{ids}`, `{items}`, `{order}` → RPC_REJECTED |
| `user:removeMailRules {ids:[id]}` | | verified |

Condition / action shapes (all stored and read back on the test rule):
- sender/recipient: `{field:'from'|'to', operator:'contains'|'='|'endsWith', operand:'a,b', ignoreCase, listOfValues, forAddress, extractAddress}`
- subject: `{field:'subject', operator:'contains', operand, ignoreCase, listOfValues}`
- has attachment: `{field:'flags', operator:'contains', operand:['attached','linkAttached']}`
- size: `{field:'size', operator:'>'|'<', operand:<KB number>}` (10 MB stored as 10240)
- move `{type:'move', target:<fid int>}`; mark read `{type:'flags', value:{read:true}}`; flag `{type:'flags', value:{flagged:true}}`
  (webmail may add `label0`); tag `{type:'tags', value:[tagId]}`; forward `{type:'forward', target:'addr', keepLocal:true}`.
- Rules with anything else (e.g. `securityLevel`, exceptions, `##VIP`) are shown read-only: switch/order/delete only.

### Holiday auto-reply
- `user:getAttrs {optionalAttrIds:[autorepmode, autoreptext, autorep_condition, autorep_welcometip]}`:
  `autorepmode` 0/1, `autoreptext`, `autorep_condition {begintime, endtime ('YYYY-MM-DD HH:MM:SS' in the account's
  time_zone, Asia/Shanghai), instation 0/1 = 只对本单位用户发送回复}`, `autorep_welcometip` 0/1.
- `user:setAttrs {attrs:{…}}` verified by saving the current settings back unchanged (stays off). Titles are off on
  this server (`enableAutoRepTitle:false`). Turning the reply **on** was not exercised on the real account.

### Tags
- `mbox:listTags {}` → `[[id, {color, name?}]]`; the account has 9 default tags, ids '11'–'19', colour only. An
  unnamed tag is named after its colour (红色标签 …) exactly as webmail does; colour is an index into webmail's palette
  or a hex value. Limit 100 (`limitTagCount`).
- `mbox:manageTags {add:[[id,{name,color}]]}` (id `'x'+hex(ms)`), `{update:[[id,{…}]]}`, `{delete:[id]}` — verified.
- `mbox:updateMessageTags {ids, add:[…]}` / `{ids, delete:[…]}` — verified on a send-to-self mail.
- `mbox:listMessages` with `returnTag:true` carries `tag:[ids]` on tagged rows; a tag view is
  `{fids:[all but trash], skipLockedFolders:true, tags:[id]}` — verified (1 row after tagging, 0 before).

### Meeting invitations
- Card from the mail's own `text/calendar` part (METHOD REQUEST/CANCEL/…), times converted from the invitation's
  VTIMEZONE (no tz database on this PC), shown in the viewer's zone; `.ics` saved via attachment part `invite.ics`.
- Verified with an invitation the user sent from their own Gmail calendar (analysis/invite-card-shape-2026-10-18.json):
  the mail is `multipart/mixed` → `multipart/alternative` (text/plain, text/html, `text/calendar; method=REQUEST`) +
  `application/ics`; the card parsed it fully (IANA TZID resolved from its VTIMEZONE, 2 attendees, own PARTSTAT
  NEEDS-ACTION) and the .ics saved.
- Server record: `mbox:readMessage {id}` → `var.calendar {calFid:1, calUid, isAttendee, isExpired, deletedStatus,
  inRecurrence, meetingRequestMethod:'request', meetingRequestStatus, meetingRequestPartId, meetingRequest{…},
  attendees[{email, status, type}]}`; webmail's `XT/jsp/readMessage.jsp` (form `mid`) returns the same under
  `var.mail.calendar`. `mail.jsp?func=readMessage` is RPC_REJECTED.
- Answer as webmail does: `cal:respond {fid:calFid, eventId:calUid, type:2 暂定|3 接受|4 拒绝, inform:true}`, then
  `mbox:updateMessageInfos {attrs:{partStat}}`. **Verified 2026-10-18** with the user's approval: 暂定 on the Gmail test
  invitation → S_OK, and `meetingRequestStatus` went 5 → 2 (the reply went to the user's own Gmail; analysis/invite-respond-real-verification-2026-10-18.json).
- An invitation created in webmail to the organizer's own address produces no mail (Coremail does not invite the
  organizer). `cal:listCalFolder` answered an empty list.
- The card remembers an answer given in Hongyan; it does not yet read `meetingRequestStatus` to show answers given
  elsewhere (that call can mark the mail read, so it is left for the answer path only).

## Not verified / open
- Answers given outside Hongyan are not shown on the card (see above).
- Turning auto-reply on (only the unchanged save was exercised).
- `forward` variants `attachForward` / `quoteForward` are shown read-only.
- The sample rule HY-SAMPLE the user created in webmail is still on the account (see the session report).
