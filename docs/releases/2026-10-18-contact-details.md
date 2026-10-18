# 2026-10-18 · 通讯录员工详细信息 (v0.4)

## What changed
- 通讯录 card shows every directory field the server fills, with labels: 姓名、邮箱、部门（完整路径）、职位、手机、办公电话、传真、地址
  (工号/办公地点/上级 render too if a future server exposes them). Click any field to copy it.
- Head image replaces the coloured initials when the directory has one; initials stay the fallback.
- Reader: clicking the sender or a recipient opens a small popover with the person's details and 写邮件 / 查看往来邮件
  (it used to start a new mail directly; 写邮件 is now the popover's primary button).
- Details are cached in the encrypted cache (`person:<sha256(email)[:24]>`, no address in the key), refreshed after 24 h,
  served stale when offline, never logged. New bridge method `person` (FAST).

## Verified server facts (read-only, tools/probes/probe_contact_details{,2}.py; counts only)
- `oab:get {list:[email], returnAttrs, forEmail:true}` returns one row per address, email matching exactly (20/20).
  Also accepts `@id`s without forEmail.
- Asking for any attribute the deployment does not expose fails the whole call (`FA_FORBIDDEN`), for `oab:get` and `oab:listEx` alike.
  Allowed besides the v0.3 set: `@ou, gender, nick_name, alias_emails, mobile_number, company_phone, home_phone, fax_number,
  address, zipcode, province, city, country, remarks, security_level, sender_security_level`.
  Refused: employee number variants, office/location, manager/superior/leader, title/position/department, head_image, fullPY, `m`, ...
- Filled in a 61-person sample: true_name 61, email 61, @location 61, duty 55, mobile_number 57; every other allowed attribute was empty.
- Site card config `X.O.cardExt` lists mobile_number and home_phone.
- Head images: `oab:getHeadImageData?uid=` answers empty (POST: FS_UNKNOWN) for all 10 sampled; webmail's `avatar/,<id>` serves
  the same-size PNG for everyone (a generic picture), so it is not used. The client keeps calling getHeadImageData and shows
  an image only for real PNG/JPEG/GIF bytes ≤ 256 KB.
