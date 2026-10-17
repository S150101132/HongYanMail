"""Generate docs/research/02-通信协议与接口清单.md from the collected evidence.

Columns required by the objective: 名称 / 用途 / 协议 / 端点或命令 / 请求参数 /
响应结构 / 认证依赖 / 调用代码位置 / 证据 / 可信度 / 验证状态.
Values are filled from local evidence only; anything unproven stays explicitly
"未验证" or "未知".
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

WS = r"D:\workspace\redocoreemail"
A = os.path.join(WS, "analysis")

final = json.load(open(os.path.join(A, "server-api-final.json"), encoding="utf-8"))
obs = json.load(open(os.path.join(A, "runtime-observed-api.json"), encoding="utf-8"))["observed"]
contracts = json.load(open(os.path.join(A, "runtime-interface-contracts.json"), encoding="utf-8"))["interfaces"]

PURPOSE = {
    "mbox:getAllFolders": "列出邮箱文件夹及计数",
    "mbox:listMessages": "按文件夹分页列出邮件摘要",
    "mbox:getMessageData": "取原始邮件（MIME）",
    "mbox:readMessage": "读信渲染与安全信息（含加密/水印标记）",
    "mbox:getMessageInfos": "批量取邮件详细信息",
    "mbox:updateMessageInfos": "修改邮件属性（已读/星标/所在文件夹）",
    "mbox:updateMessageTags": "修改邮件标签",
    "mbox:listTags": "列出标签",
    "mbox:manageTags": "管理标签定义",
    "mbox:searchMessages": "服务端搜索邮件",
    "mbox:compose": "发信/存草稿（continue 建会话、deliver 发送）",
    "mbox:getComposeInfo": "取写信会话信息",
    "mbox:getComposeData": "取写信会话数据/附件",
    "mbox:replyMessage": "回复；**未见独立的\"回复全部\"接口**，回复全部很可能是同一接口的参数（未验证）",
    "mbox:forwardMessages": "转发",
    "mbox:restoreDraft": "恢复草稿",
    "mbox:deleteMessages": "删除邮件（可归档）",
    "mbox:undeleteMessages": "恢复已删除邮件",
    "mbox:listDeletedMessages": "列出已删除邮件",
    "mbox:emptyFolder": "清空文件夹",
    "mbox:recallMessage2": "邮件召回",
    "mbox:validateRcpts": "收件人校验 / MailTips",
    "mbox:listSharedAccessMbox": "列出共享邮箱/共享文件夹",
    "mbox:getShareACL": "读取共享权限",
    "mbox:putShareACL": "设置共享权限",
    "user:login": "账号登录",
    "user:getPasswordKey": "取密码加密公钥",
    "user:getPasswordURL": "取改密/密码服务地址",
    "user:getTempSession": "取二次认证临时会话",
    "user:triggerSecondAuth": "触发第二因素认证",
    "user:setDeviceSecret": "设置/注册设备密钥",
    "user:getAliasWithSeed": "取别名（带种子）",
    "user:listDevices": "列出已绑定设备",
    "user:authenticate2": "二次认证/安全文件夹解锁（用途待定）",
    "user:validatePassword": "校验密码",
    "user:getAttrs": "取账号属性/配额/配置",
    "user:setAttrs": "写账号属性（含黑名单等）",
    "user:getSchema": "取属性 schema",
    "user:getSecurityLevelMap": "取密级映射",
    "user:getSignatures": "取签名",
    "user:getMailRules": "取邮件规则",
    "user:addMailRules": "新增邮件规则",
    "user:updateMailRules": "修改邮件规则",
    "user:removeMailRules": "删除邮件规则",
    "user:adjustMailRuleOrder": "调整规则顺序",
    "user:listLoginHistory": "登录历史",
    "user:listDeliveryHistory": "投递状态历史",
    "user:listDelmsgHistory": "删除记录历史",
    "user:listTrsFileDownloadHistory": "文件中转下载历史",
    "user:addAppPwd": "新增应用专用密码",
    "user:listAppPwds": "列出应用专用密码",
    "user:deleteAppPwds": "删除应用专用密码",
    "user:generateVerifyCodeImage": "生成图形验证码",
    "user:getCACBannerOption": "取 CA 提示横幅配置",
    "user:sendFeedbackOnCACBanner": "上报 CA 横幅反馈",
    "user:authenticateSmimeKeyPass": "校验 S/MIME 密钥口令",
    "upload:prepare": "申请附件上传槽位",
    "upload:directData": "分片上传附件数据",
    "upload:directDataNF": "分片上传到网盘",
    "upload:prepareNetfolder": "申请网盘上传槽位",
    "upload:moveToNetDisk": "移动到网盘",
    "upload:moveToNetFolder": "移动到网盘目录",
    "oab:get": "取组织通讯录条目",
    "oab:listEx": "列出组织通讯录",
    "oab:searchEx": "搜索组织通讯录",
    "oab:autoMatch": "通讯录自动匹配",
    "oab:getDirectories": "取组织目录树",
    "oab:getCacheFile": "取组织通讯录缓存文件",
    "oab:getHeadImageData": "取联系人头像",
    "oab:validateAccessibility": "校验组织通讯录可用性",
    "pab:getContacts": "取个人联系人",
    "pab:createContacts": "新增联系人",
    "pab:updateContacts": "修改联系人",
    "pab:deleteContacts": "删除联系人",
    "pab:searchContacts": "搜索联系人",
    "pab:getAllGroups": "取联系人分组",
    "pab:updateAllGroups": "更新联系人分组",
    "pab:importContacts": "导入联系人",
    "nf:getAllFolders": "列出网盘/文件中转目录",
    "nf:listFiles": "列出网盘文件",
    "nf:searchFiles": "搜索网盘文件",
    "nf:getFileData": "取网盘文件数据",
    "nf:deleteFiles": "删除网盘文件",
    "nf:createUserFolders": "创建网盘目录",
    "nf:downloadTrsFile": "下载中转文件",
    "nf:prepareTrsFileDownloadSelf": "申请中转文件下载",
    "nf:copyFileToTrs": "复制文件到中转区",
    "xnf:getConfig": "取共享文件配置",
    "xnf:list": "列出共享文件",
    "xnf:search": "搜索共享文件",
    "xnf:createFolder": "创建共享目录",
    "xnf:rename": "重命名共享文件",
    "xnf:delete": "删除共享文件",
    "xnf:getShareableUrl": "取共享链接",
    "xnf:attachToCompose": "把共享文件附到写信",
    "xnf:saveFromMessage": "把邮件存为共享文件",
    "cal:listCalFolder": "列出日历目录",
    "cal:listEventsNew": "列出日程",
    "cal:createEventsNew": "新建日程",
    "cal:updateEventsNew": "修改日程",
    "cal:deleteEvents": "删除日程",
    "cal:readEvent": "读取日程",
    "cal:searchEventsNew": "搜索日程",
    "cal:compose": "日程转发/写信",
    "cal:getUserFreeBusyStatus": "查询忙闲",
    "cal:doResponseMeetingRequest": "响应会议邀请",
    "cal:replyMeetingRequest": "回复会议邀请",
    "cal:addToMyCal": "加入我的日历",
    "cal:options": "日历配置",
    "global:verify": "账号存在性校验",
    "global:options": "全局配置",
    "global:getWelcomePageBulletin": "取欢迎页公告",
    "cert:get": "取证书",
    "cert:listPublicKey": "批量取收件人公钥",
    "site:getTrustedDomains": "取受信域",
    "sms:authSendValidCode": "发送短信验证码",
    "cim.common:checkVersion": "客户端版本检查",
    "cim.common:dlFeedback": "反馈上报",
    "cim.common:clickLog": "点击埋点上报",
    "cim.user:getLunkrMailData": "取 Lunkr/AI 相关邮件数据",
    "mbox:createUserFolders": "创建用户文件夹（处理函数标签 `createFolder`/`CreateFolder`，参数 `parentid`/`name`）",
    "mbox:deleteFolders": "删除文件夹（标签 `deleteFolder`；区分 `pstfolder` 与 `delcoremailfolder`，跟踪 `serverfid`/`localfid`）",
    "mbox:updateFolders": "重命名/移动文件夹（标签 `folderRename`、`ParentUpdate`）",
    "mbox:composeF": "发信变体（`compose` 的 F 变体；**仅按命名推断**，无标签或契约证据）",
    "mbox:shareBindMessage": "分享邮件的绑定消息（**仅按命名推断**）",
    "mbox:editAttach": "编辑邮件附件（**仅网页端存在，无二进制证据**；按命名推断）",
    "cal:createCalFolder": "创建日历文件夹",
    "cal:updateCalFolder": "修改日历文件夹",
    "cal:deleteCalFolder": "删除日历文件夹",
    "cal:deleteAccessedCalFolder": "移除已访问（共享）日历文件夹",
    "cal:listAccessedCalFolder": "列出已访问的共享日历文件夹（标签 `SyncAccessedCalFolderInfo`）",
    "cal:listEventsByUIDList": "按 UID 列表同步日程（标签 `SyncCalendarInfoByFidNew`）",
    "cal:editEvent": "编辑日程（处理函数标签含 `AccId`/`Trans`）",
    "cal:updateInRecurrenceEvent": "更新重复日程中的单次（标签 `SynUpdateCalendarToServer`）",
    "pab:addContactAvatarStd": "为标准联系人添加头像（**仅网页端，按命名推断**）",
    "pab:addContactKeyStd": "为标准联系人添加证书/密钥（**仅网页端，按命名推断**）",
    "pab:downloadContactSingleKey": "下载单个联系人的证书/密钥（**仅网页端，按命名推断**）——与 S/MIME 加密相关",
    "lp:getImg": "登录插件相关图片（模块 `lp`，**仅网页端，按命名推断**）",
}

# code locations proven by REA trace_feature (see docs/research/04-接口证据与验证记录.md)
TRANSPORT_NOTE = ("通用 RPC 构造 `/s/json?func=` @`0x142105990`，被 `FUN_140ce70a0`、`FUN_140d06190`、"
                  "`FUN_140d06b70`、`FUN_140d0a340` 引用（共享传输层）")
TRACED = {
    "mbox:listMessages": "字符串 `0x1420f9910`；引用 `FUN_140c10b90`、`FUN_140e56030`",
    "user:login": "字符串 `0x142106068`；引用 `FUN_140cfca00`（10,128 B 登录状态机）",
    "user:getPasswordKey": "字符串 `0x1421059a8`；引用 `FUN_140ce8300`",
    "user:getPasswordURL": "字符串 `0x1420fad30`；引用 `FUN_140c2e200`、`FUN_140c3a930`",
    "user:getTempSession": "字符串 `0x1420fab90`；引用 `FUN_140c2aa30`",
    "user:generateVerifyCodeImage": "字符串 `0x1420fae28`；引用 `FUN_140c2aa30`",
    "user:getAttrs": "字符串 `0x1420fb5c8`；引用 `FUN_140c37800`",
    "user:addAppPwd": "字符串 `0x1420fb0a8`；引用 `FUN_140c39e60`",
    "user:deleteAppPwds": "字符串 `0x1420fb130`；引用 `FUN_140c397a0`",
    "user:listAppPwds": "字符串 `0x1420fb668`；引用 `FUN_140c36d40`",
    "global:verify": "字符串 `0x1420fb308` / `0x1420fb460`；引用 `FUN_140c32e30`、`FUN_140c34db0`",
    "global:options": "字符串 `0x1420fb530`；引用 `FUN_140c33ff0`",
    "cim.common:checkVersion": "字符串 `0x142103770`；引用 `FUN_140ccf140`",
    "cim.common:dlFeedback": "字符串 `0x142103610`；引用 `FUN_140cd4e00`",
    "cim.user:getLunkrMailData": "字符串 `0x1421118d8`；引用 `FUN_140e994c0`",
}

# merge CLI batch trace results (analysis/rea-trace-summary.json) when present
_SUMMARY = os.path.join(A, "rea-trace-summary.json")
if os.path.exists(_SUMMARY):
    try:
        for _key, _value in json.load(open(_SUMMARY, encoding="utf-8")).items():
            _query = _value.get("query")
            if not _query or "error" in _value:
                continue
            _funcs = _value.get("referencing_functions") or []
            if not _funcs:
                continue
            _addrs = ", ".join(f"`{m['address']}`" for m in (_value.get("string_matches") or [])[:3]) or "未记录"
            _names = ", ".join(f"`{f['name']}`" for f in _funcs[:4])
            TRACED.setdefault(_query, f"字符串 {_addrs}；引用 {_names}（批量 trace）")
    except (ValueError, OSError):
        pass

# merge locally derived literal addresses + referencing functions (verified against
# Ghidra on 11 observations; see 04 §3 row 35/36)
_SWEEP = os.path.join(A, "rpc-string-addresses.json")
if os.path.exists(_SWEEP):
    try:
        for _name, _addr in (json.load(open(_SWEEP, encoding="utf-8")).get("addresses") or {}).items():
            TRACED.setdefault(_name, f"字符串 `{_addr}`（引用函数未定位）")
    except (ValueError, OSError):
        pass

_REFS = os.path.join(A, "rpc-string-refs.json")
if os.path.exists(_REFS):
    try:
        for _name, _info in (json.load(open(_REFS, encoding="utf-8")).get("references") or {}).items():
            _funcs = _info.get("function_ranges") or []
            _insns = _info.get("reference_instructions") or []
            if not _funcs:
                continue
            _shown = "、".join(f"`FUN_{f[2:]}`" for f in _funcs[:3])
            _more = f" 等 {len(_funcs)} 个" if len(_funcs) > 3 else ""
            TRACED[_name] = (f"字符串 `{_info.get('literal')}`；引用 {_shown}{_more}"
                             f"（{len(_insns)} 处 `lea` 加载，本地 .pdata 映射）")
    except (ValueError, OSError):
        pass


_capability_flags = {}


# coverage matrix: (label, matcher(module, method))
DOMAINS = [
    ("认证 / 登录 / 2FA", lambda m, x: m in ("user", "global", "sms") and any(
        k in x.lower() for k in ("login", "logout", "password", "auth", "device", "tempsession",
                                 "verify", "apppwd", "secondauth", "unlock"))),
    ("账号属性 / 配置", lambda m, x: m in ("user", "global") and any(
        k in x.lower() for k in ("getattrs", "getsignatures", "options", "getconfig", "logo", "quota"))),
    ("收信 / 取信", lambda m, x: m == "mbox" and any(
        k in x.lower() for k in ("listmessages", "getmessagedata", "getmessageinfos", "getnewmail",
                                 "listsummary", "fetch"))),
    ("发信 / 草稿", lambda m, x: m == "mbox" and any(
        k in x.lower() for k in ("compose", "send", "draft", "getcomposeinfo", "save"))),
    ("附件 / 上传", lambda m, x: m == "upload" or "attach" in x.lower()),
    ("同步 / 文件夹", lambda m, x: m == "mbox" and any(
        k in x.lower() for k in ("folder", "delmsg", "shared", "listtags", "topic", "fid"))),
    ("搜索", lambda m, x: "search" in x.lower()),
    ("邮件状态 / 召回 / 投递", lambda m, x: m == "mbox" and any(
        k in x.lower() for k in ("updatemessageinfos", "recall", "deletemessages", "undelete",
                                 "listdeleted", "move")) or "delivery" in x.lower()),
    ("通讯录（个人）", lambda m, x: m == "pab"),
    ("通讯录（组织）", lambda m, x: m == "oab"),
    ("日历", lambda m, x: m == "cal"),
    ("网盘 / 共享文件", lambda m, x: m in ("nf", "xnf")),
    ("证书 / S-MIME", lambda m, x: m == "cert" or any(
        k in x.lower() for k in ("smime", "cert", "publickey", "encrypt"))),
    ("站点 / 域管理", lambda m, x: m in ("site", "admin", "domain")),
]


def _ajc_shape():
    """Observed getAttrs response shape (40 identical samples)."""
    path = os.path.join(A, "ajc-response-shapes.json")
    if not os.path.exists(path):
        return "未知"
    try:
        shapes = json.load(open(path, encoding="utf-8")).get("shapes") or []
    except (ValueError, OSError):
        return "未知"
    for row in shapes:
        shape = row.get("shape", "")
        if shape.startswith("{code:str,var:{$ajc:"):
            return f"`{shape[:300]}…`（{row.get('count')} 次一致；完整类型树见 `analysis/ajc-response-shapes.json`）"
    return "未知"


def deploy_state(name, rec):
    """Availability in THIS deployment, from the server capability declaration."""
    if name in VERIFIED_AVAILABLE:
        return "已实测可用"
    flag = SPECIFIC_FLAG.get(name)
    if flag is None:
        flag = MODULE_FLAG.get(rec["module"])
    if flag is None:
        return "—（无对应开关）"
    values = _capability_flags.get(flag)
    if not values:
        return f"—（`{flag}` 未采集到）"
    state = "启用" if "True" in values else "关闭"
    return f"能力声明:{state}（`{flag}`）"


MODULE_FLAG = {
    # `nf:*` (net-disk file operations) are absent from every observed session, which
    # matches supportNetfolder=false. `xnf:*` is deliberately NOT mapped here:
    # xnf:getConfig was observed being called (106 times) even though
    # supportNetfolder=false, so xnf is governed by some other switch.
    "nf": "supportNetfolder",
    "cal": "supportCalendar",
    "oab": "supportOrgAddressBook",
    "sms": "supportSMS",
}
SPECIFIC_FLAG = {
    "upload:prepareNetfolder": "supportNetfolder",
    "upload:moveToNetDisk": "supportNetfolder",
    "upload:moveToNetFolder": "supportNetfolder",
    "upload:directDataNF": "supportNetfolder",
    "mbox:recallMessage2": "supportRecallMail",
    "mbox:searchMessages": "supportFulltextSearch",
    "pab:importContacts": "supportBatchAddContacts",
}
VERIFIED_AVAILABLE = {
    "mbox:getAllFolders", "mbox:listMessages", "mbox:getMessageData", "user:getAttrs",
    "mbox:updateMessageInfos", "mbox:compose", "upload:prepare", "upload:directData",
    "mbox:searchMessages", "mbox:replyMessage", "mbox:deleteMessages",
}


def endpoint_of(rec):
    """Route each RPC name to the host actually observed in the client logs."""
    name = f"{rec['module']}:{rec['method']}"
    if rec["module"] == "cim.user":
        return f"`http://127.0.0.1:9900/adm_api/s/json?func={name}`（本机代理，非邮箱服务器）"
    if rec["module"] == "cim.common":
        return f"`https://pcmail.coremail.cn/lunkr/s/json?func={name}`（Coremail 云侧）"
    return f"`/coremail/s/json?func={name}`"


# verification levels used consistently across the inventory and the coverage matrix
MACHINE_VERIFIED = {           # 真机实测通过（本项目自己的客户端跑通）
    "user:login",
    "mbox:getAllFolders", "mbox:listMessages", "mbox:getMessageData",
    "mbox:compose", "upload:prepare", "upload:directData",
    "mbox:updateMessageInfos", "mbox:replyMessage",
}
CONTRACT_VERIFIED = {          # 契约验证：结构按契约走通，未做完整业务闭环
    "mbox:searchMessages", "user:getAttrs", "user:listDelmsgHistory",
    "mbox:listSharedAccessMbox", "oab:getHeadImageData", "mbox:listTags",
}


# parameters observed in the native logs but not covered by the threaded contract
# extractor (which only reads the request line's query string)
OBSERVED_PARAMS = {
    # --- 反编译实证（batch_decompile，函数地址见 04 第 47 项）：最强证据，优先于日志字段 ---
    "mbox:listSharedAccessMbox": ("**反编译实证**：请求体键 `showHidden`（长度 10）；当第二参数非零时追加一个 6 字节键，"
                                  "原始字节 `72 6f 6c 65 61 64`（语义**未确认**，非常见词，不臆测）"),
    "mbox:deleteMessages": ("**反编译实证**：请求体键 `localArchive`（长度 12），值为标志字节；另一处键 `ids`（长度 3，数组）"),
    "mbox:searchMessages": ("**反编译实证**：请求体 `briefOnly = 1`、`recursive = 0`、`order = \"receivedDate\"`、`desc = 1`"
                            "（键长与字节逐一核对；不是启发式推测）"),
    "mbox:updateMessageInfos": ("**反编译实证**：`FUN_140c5e980` → `attrs`、`fid`、`rule`；"
                                "`FUN_140de0b60` → **`attrs` 内嵌 `fid`**（`{attrs:{fid:…}}`），"
                                "且当模式参数为 5 时追加 `reportType`（长度 10）"),
    "mbox:listMessages": ("**反编译实证**（`FUN_140c10b90`，属 `_uploadToFolder->mbox:listMessages` 路径）："
                          "请求体含 `fid`（长度 3）、`filter`（长度 6，字符串）、`subject`（长度 7，字符串）、"
                          "`receivedDate`（长度 12，排序键）——即「按 fid + 过滤/主题定位刚上传的邮件」"),
    "user:getSignatures": "**反编译实证**：请求体 `isPublic = 1`（长度 8）",
    "mbox:getComposeInfo": "**反编译实证**：请求体 `id`（长度 2）",
    "mbox:getMessageInfos": "**反编译实证**：请求体 `ids`（长度 3，数组，逐个追加）",
    "mbox:getAllFolders": ("query `sid`；**反编译实证请求体为空对象**，与真机 plain 调用被接受一致（双重印证）"),
    "mbox:compose": ("**反编译实证**：请求体 `action = \"continue\"`（长度 6）、`attrs`（长度 5，子映射）、`id`（长度 2）；"
                     "**`extraHeaders` 是 `attrs` 的子字段**（由 `FUN_140eb5a90` 注入）——此前只能从日志看到字段名，现已定位其归属层级"),
    "upload:directData": ("**反编译实证**：三种 URL 在代码中逐字出现（长度 0x38/0x45/0x31）："
                          "`upload:directData&composeId=%v&attachmentId=%v&offset=%v`、"
                          "`…&inlined=true&…`、`upload:directDataNF&mid=%v&composeId=%v&offset=%v`；"
                          "分块大小 = 剩余字节，**上限 0x2800000（40 MiB）**，受配置项约束，特定条件下降到 **0x100000（1 MiB）**"),
    "user:getAttrs": ("**反编译实证**：请求体 `attrIds`（长度 7，字节 61 74 74 72 49 64 73）；"
                      "请求头 `Content-Type: text/x-json;charset=UTF-8;tz=UTC;useTypeCastExtension=1`（长度 0x37）"),
    "upload:prepare": "先于 `upload:directData` 调用；同族函数引用 `composeId`/`attachmentId`/`fileName`/`contentType`/`size`（见 03 §3.3）",
    "mbox:compose": ("引用字段（7 个函数、74 条字面量）：`content`、`charset`、`isHtml`、`type`、`action`、`deliver`、"
                     "`fid`、`save`、`returnInfo`、`schedule`、`name`、`displayName`、`size`、`id`、`deleted`、`order`、"
                     "`fileName`、`ContentType`、`AttachType`、**`encryptPassword`**、**`savePassword`**、`skipMTAValidateRcpt`；"
                     "另含日历内嵌字段 `eventId`/`invite`/`inform`/`userAttendeeStatus`/`exceptionDate`（见 03 §3.1）"),
    "oab:getCacheFile": ("字面量给出的查询参数：`&syncState=`、`&lastCache=`、**`&If-Modified-Since=`**；"
                         "并引用缓存名/时间字段 `comporesOrgUserOabCacheName`、`compressOrgUserLastModiried`"),
    "oab:getDirectories": "引用时间戳字段 `orgLastModified`、`siteLastModified`（原文另见拼写 `orgLastModiried`）与 `email`",
    "user:listDeliveryHistory": "同族函数含 `GetDeliveryStatusInfo:%v` 与 `GetSendLetterLogInfo:%v` 两条日志标签（投递状态 + 发信日志）",
    "mbox:createUserFolders": "引用字段 `parentid`、`name`（建文件夹）与日志 `CreateFolder:%v`、`SyncFolder:%v`",
    "pab:createContacts": ("引用选项字段 `overflowAction`、`emailExistsAction`、`dontEnforceEmail`、`returnItemInfo`"
                           "（联系人导入/创建的冲突与溢出策略）；同族含 `SyncPersonalAddr`、`_uploadLocalContacts:%v`"),
    "mbox:listMessages": "引用 `returnTotal`、`receivedDate`、`modifiedDate`、日期格式 `%Y%m%d%H%M%S` 与 `FA_UPLOAD_SIZE_EXCEEDED`、`S_PARTIAL_OK`",
}

# request field names seen on the same log line as the interface (counts from
# analysis/runtime-protocol-field-clues.json); high-count entries are unambiguous,
# low-count ones are marked as such because adjacent-line noise is possible
# interfaces whose request body was read out of the decompiled request builder
DECOMPILED_PARAMS = {
    "mbox:listSharedAccessMbox": OBSERVED_PARAMS["mbox:listSharedAccessMbox"],
    "mbox:deleteMessages": OBSERVED_PARAMS["mbox:deleteMessages"],
    "mbox:searchMessages": OBSERVED_PARAMS["mbox:searchMessages"],
    "mbox:updateMessageInfos": OBSERVED_PARAMS["mbox:updateMessageInfos"],
    "user:getSignatures": OBSERVED_PARAMS["user:getSignatures"],
    "mbox:getComposeInfo": OBSERVED_PARAMS["mbox:getComposeInfo"],
    "mbox:getMessageInfos": OBSERVED_PARAMS["mbox:getMessageInfos"],
    "mbox:getAllFolders": OBSERVED_PARAMS["mbox:getAllFolders"],
    "mbox:compose": OBSERVED_PARAMS["mbox:compose"],
    "upload:directData": OBSERVED_PARAMS["upload:directData"],
    "user:getAttrs": OBSERVED_PARAMS["user:getAttrs"],
}

REQUEST_FIELDS = {    "mbox:replyMessage": ("action", "attrs", "account", "autoDel", "bcc", "cc", "ctrls", "extraHeaders"),
    "mbox:updateMessageInfos": ("ids", "attrs", "flags", "read", "flagged", "fid", "label0"),
    "mbox:deleteMessages": ("ids", "localArchive"),
    "mbox:listSharedAccessMbox": ("showHidden", "v"),
    "user:listLoginHistory": ("limit", "start"),
}

# login-family response observed 51 times (analysis/runtime-protocol-json-shapes.json);
# the sample does not distinguish user:login from global:verify, so it is stated as such
LOGIN_RESPONSE_NOTE = ("`{code,var:{nonce:str,primaryEmail:str,sid:str,uid:str,webname:str}}`"
                       "（日志 51 次一致；样本未区分 `user:login` 与 `global:verify`）")


CONTRACT_RESPONSES = {
    # response shapes verified by this project's own client against the live server
    "mbox:getAllFolders": "`var:[{flags:{system:bool},id:int,keepPeriod:int,name:str}]`"
                          "（真机 9 个文件夹，各字段 9/9 出现；`analysis/real-folder-shape.json`）",
    "mbox:searchMessages": "`{code,total,keywords,groupings,var:[{id,fid,hmid,from,to,subject,sender,"
                           "size,sentDate,receivedDate,modifiedDate,flags,priority,ref,label0,"
                           "backgroundColor,antiVirusStatus}]}`（`analysis/mail-search-contract-*.json`）",
    "mbox:listMessages": "`{code,total,midoffset,var:[{id,fid,hmid,from,to,subject,size,sentDate,"
                         "receivedDate,flags,ctrls,backgroundColor,antiVirusStatus,…}]}`"
                         "（`analysis/real-message-shape.json`）",
    "mbox:updateMessageInfos": "`{code,originalMsgInfos:[…]}`（真机改已读/星标/移动；`var` 为空；"
                               "`analysis/mail-flags-contract-*.json`、`mail-move-contract-*.json`）",
    "upload:prepare": "`{attachmentId,contentType,fileName,size}`（`analysis/attachment-upload-contract-*.json`）",
    "upload:directData": "`{attachmentId,contentType,fileName,size,actualSize}`（同上；该合约未调用 `deliver`）",
    "mbox:compose": "`{\"code\":\"S_OK\",\"draftId\":\"<N>:<token>\",\"var\":…}`；真机发信返回 job 且 `state=Accepted`"
                    "（`analysis/real-send-verification.json`）",
}


def shape_of(func, kind):
    if kind == "response_shapes" and func == "user:login":
        return LOGIN_RESPONSE_NOTE
    if kind == "query" and func in DECOMPILED_PARAMS:
        # strongest evidence: key construction read out of the decompiled request builder
        base = DECOMPILED_PARAMS[func]
        if func in REQUEST_FIELDS:
            fields = "、".join(f"`{f}`" for f in REQUEST_FIELDS[func])
            return f"{base}；日志另同现：{fields}"
        return base
    if kind == "query" and func in REQUEST_FIELDS:
        fields = "、".join(f"`{f}`" for f in REQUEST_FIELDS[func])
        return f"body 字段（日志同现）：{fields}"
    if kind == "response_shapes" and func in CONTRACT_RESPONSES:
        return CONTRACT_RESPONSES[func]
    if kind == "query" and func in OBSERVED_PARAMS:
        return OBSERVED_PARAMS[func]
    if kind == "response_shapes" and func == "user:getAttrs":
        return _ajc_shape()
    if kind == "response_shapes" and func == "mbox:compose":
        return "`{\"code\":\"S_OK\",\"draftId\":\"<N>:<token>\",\"var\":…}`（日志直接印证）"
    entry = contracts.get(func)
    if not entry:
        return "未知"
    if kind == "query":
        params = entry.get("query_parameters") or {}
        named = [k for k in params if k not in ("sid", "func")]
        body = shape_of(func, "request_shapes")
        parts = []
        if named:
            parts.append("query: " + ", ".join(f"`{k}`" for k in named[:10]))
        if body != "未知":
            parts.append("body: " + body)
        if params.get("sid"):
            parts.append("`sid`")
        return "<br>".join(parts) if parts else "未知"
    rows = entry.get(kind) or []
    if not rows:
        return "未知"
    if kind == "response_shapes":
        # Thread-proximity pairing still mixes concurrent operations; only the
        # shapes that were independently verified are trusted here.
        if func not in TRUSTED_RESPONSE:
            return "日志未可靠记录（见 04 §6）"
    best = rows[0]["shape"]
    if best.startswith("(no"):
        return "日志未记录"
    return f"`{best[:220]}`"


# response envelopes that are independently corroborated (own client experiments
# or unambiguous single-shape pairing); everything else stays untrusted
TRUSTED_RESPONSE = {
    "mbox:getAllFolders",
    "mbox:compose",
    "upload:prepare",
    "upload:directData",
    "user:getAttrs",
    "mbox:listMessages",
    "mbox:searchMessages",
    "mbox:updateMessageInfos",
    "user:login",
}


def main():
    by_mod = final["by_module"]
    summary = final["summary"]
    out = []
    out.append("# 02 通信协议与接口清单\n")
    out.append("分析对象：`C:\\Program Files\\cmclient\\CMClient.exe`（INSPURCMClient 4.2.x，x86_64 PE，"
               "SHA-256 `a2cc47c5efb74524dd82e447204d0cc9587a971f9cda9552187eee19abb37c5b`）、"
               "安装目录 `C:\\Program Files\\cmclient\\app` 网页层、客户端运行日志、公司公开 Webmail 脚本。\n")
    out.append("生成方式：`tools/research/build_interface_doc.py` 依据 `analysis/server-api-final.json`、"
               "`analysis/runtime-observed-api.json`、`analysis/runtime-interface-contracts.json` 生成。\n")
    out.append("## 0. 协议判定（先说结论）\n")
    out.append("本客户端对当前公司部署（`https://newmail.inspur.com/coremail/`）使用 **Coremail 私有 HTTP JSON-RPC**，"
               "不是标准 IMAP/SMTP。二进制内同时存在 IMAP4/POP3/SMTP/EWS 四套适配器代码，但只有 Coremail 分支被本账号使用。\n")
    out.append("| 协议 | 判定 | 依据 |\n| --- | --- | --- |\n"
               "| Coremail 私有 HTTP JSON-RPC | **已确认使用** | `/coremail/s/json?func=<模块>:<方法>` 字符串；"
               "真实登录/收信/发信实测通过；运行日志 57,498 次 `func=` 命中 |\n"
               "| Socket.IO over WebSocket 推送 | **通道已实测** | `wss://<host>/socket.io/<N>/websocket/?EIO=<N>&transport=websocket`；"
               "日志含 `[Websocket]` 会话/心跳/SSL 与 95 次 `lost socket.io heartbeat`（见 03 §2.4） |\n"
               "| IMAP4 | **服务端提供，客户端未使用** | 服务端能力声明 `supportIMAP:true`（02 §1.2）；"
               "但 73 个日志文件中**零** IMAP 会话（词边界匹配，见 04 §3 第 15 项） |\n"
               "| POP3 | 代码存在，服务端未声明，客户端未使用 | `Pop3Engine`、`Pop3[%v]` 字面量；能力清单中无对应开关 |\n"
               "| SMTP | 代码存在，客户端未使用 | `SmtpHost/SmtpPort/SmtpUseSSL/SmtpUser`、`EHLO`、`Smtp rcpt:`；日志零会话 |\n"
               "| EWS (SOAP) | 代码存在，客户端未使用 | `EWSExchangeContext`、`ews/exchange.asmx`、`m:SyncFolderItems`、`GetStreamingEvents` |\n"
               "\n> 反例提示：早先以 `Select-String` 子串统计得到的 Imap/Smtp/EWS 命中数全部是假阳性"
               "（`Newsletter` 内含 `ews`、base64 块内含 `imap`/`smtp`）。协议判定必须以词边界证据为准。\n")
    out.append("## 1. 传输层常量\n")
    out.append("| 项 | 值 |\n| --- | --- |\n"
               "| RPC 入口 | `POST https://<host>/coremail/s/json?func=<模块>:<方法>&sid=<会话>` |\n"
               "| 第二入口 | `GET/POST /coremail/s?func=mbox:getComposeData&sid=&composeId=&attachId=` |\n"
               "| 附件上传 | `/coremail/XT/jsp/upload.jsp?func=directData&sid=&composeId=&attachmentId=&offset=`（Web 路由，见 §3 注） |\n"
               "| 内容类型 | 请求 `application/x-cm-json;charset=UTF-8;tz=UTC`（`global:options` 处理函数实测）；"
               "**部分接口（如 `user:getAttrs`）请求用 `text/x-json;charset=UTF-8;tz=UTC;useTypeCastExtension=1`**（反编译实证）；"
               "响应 `text/x-json;charset=UTF-8;tz=UTC;useTypeCastExtension=1` |\n"
               "| 会话载体 | Cookie `Coremail.sid` 或 Header `X-CM-AUTH-SID` |\n"
               "| 应答外壳 | `{\"code\":\"S_OK\",\"var\":...}`；扩展日期类型 `!!date '<YYYY-MM-DD HH:mm:ss>'` |\n"
               "| 共享邮箱上下文 | 查询参数 `&mboxa=` |\n"
               "| **条件请求头（本轮新增，函数字面量）** | `X-CM-SYNCSTATE`、`X-CM-OABCACHE`、**`X-CM-CHECKSUM`**、`Last-Modified`；"
               "客户端处理 **`304 Not Modified`**，并可用查询参数 `&If-Modified-Since=` |\n"
               "> 含义：取信/取通讯录缓存不是简单 GET，而是**带同步状态与校验和的增量条件请求**；"
               "自研客户端若忽略这些头，会退化为每次全量拉取（功能可用但流量与延迟显著变差）。\n")
    out.append("### 1.1 三类主机（实测路由，重要）\n")
    out.append("客户端日志显示，同名风格的 `func=` 调用实际落在**三个不同主机**上，互不可混：\n")
    out.append("| 主机 | 路径 | 观察 | 说明 |\n| --- | --- | --- | --- |\n"
               "| 邮箱服务器（远程，443） | `/coremail/s/json?func=…` | 8,332 次请求日志 | mbox/user/cal/pab/nf/xnf/oab/upload/cert/site/sms/global 全部走这里 |\n"
               "| 本机代理（`127.0.0.1:9900`） | `/adm_api/s/json?func=cim.user:…` | 40 次端口日志、80 次 `cim.user:getLunkrMailData` 调用 | **不是邮箱服务器**；Lunkr/AI 本地集成通道 |\n"
               "| Coremail 云侧（`pcmail.coremail.cn`） | `/lunkr/s/json?func=cim.common:…` | 二进制字面量 | `checkVersion`、`dlFeedback`；与公司邮箱不同域 |\n")
    out.append("> 因此下表中 `cim.user:*` 与 `cim.common:*` 的端点列按实际主机标注；其余接口均指向邮箱服务器。\n")
    _caps_path = os.path.join(A, "server-capabilities.json")
    if os.path.exists(_caps_path):
        try:
            _caps = json.load(open(_caps_path, encoding="utf-8"))
            global _capability_flags
            _capability_flags = _caps.get("capability_flags", {})
            out.append(f"### 1.2 服务器能力清单（实测，{_caps.get('samples', 0)} 次响应一致）\n")
            out.append("登录后 `getAttrs`（prepare fetch）响应携带 `var.$ajc.app`，其中是服务端能力开关。"
                       "这直接决定哪些接口在本部署可用：\n")
            out.append("| 能力开关 | 值 | 对本项目的意义 |\n| --- | --- | --- |")
            _meaning = {
                "supportIMAP": "**服务器提供 IMAP**（但客户端未使用私有协议以外的通道）",
                "supportPushmail": "支持推送邮件",
                "supportWSMsgNotify": "**WebSocket 消息通知**——即 02 §0 中推送通道的实现基础",
                "supportWSMsgNotifyFolder": "支持按文件夹的 WS 通知",
                "supportRecallMail": "支持邮件召回（对应 `mbox:recallMessage2`）",
                "supportFulltextSearch": "支持服务端全文搜索（对应 `mbox:searchMessages`）",
                "supportThreadingView": "支持会话线程视图",
                "supportOrgAddressBook": "支持组织通讯录（对应 `oab:*`）",
                "supportAuthorizedDevice": "**启用设备授权**——登录需处理设备绑定",
                "enableAuth2Lock": "**启用二次认证锁**——登录需处理 2FA",
                "supportAlternativePassword": "支持备用密码",
                "supportNetfolder": "**网盘关闭**——`nf:*` 在所有观测会话中均未出现，与该开关一致；"
                                    "但 `xnf:getConfig` 仍被调用，说明 `xnf:*` 不由该开关控制，故未对其作关闭判定",
                "supportTrs": "**文件中转站关闭**——相关接口排除出 MVP",
                "supportSecurityLevel": "**密级关闭**——`ctrls`/CM-SECURITY 路径可能为空",
                "supportCalendar": "支持日历（`cal:*`）",
                "supportCalendar30": "新版日历关闭",
                "supportSMS": "支持短信验证码（`sms:authSendValidCode`）",
                "supportSMSLoginNotify": "支持短信登录提醒",
                "supportShareMail": "支持邮件分享",
                "supportMailFlagged": "支持星标",
                "supportTopMail": "支持置顶邮件",
                "supportImportantMail": "重要邮件关闭",
                "supportAntivirus": "支持杀毒",
                "supportAntispam": "支持反垃圾",
                "supportAttachDetect": "支持附件检测",
                "allowSelfUnlock": "不允许自助解锁",
                "defaultNeedVerifyCode": "默认不需要图形验证码",
                "enableAliasRegister": "不允许别名注册",
                "enablePasswordAge": "启用密码有效期",
                "enableLockScreen": "启用锁屏",
                "supportAJAX": "AJAX 支持关闭（不影响 JSON-RPC）",
            }
            for _key, _val in sorted(_caps.get("capability_flags", {}).items()):
                _shown = ", ".join(f"{k}×{v}" for k, v in _val.items())
                out.append(f"| `{_key}` | {_shown} | {_meaning.get(_key, '—')} |")
            _scalars = _caps.get("scalars", {})
            for _key, _val in sorted(_scalars.items()):
                _shown = ", ".join(f"`{k}`×{v}" for k, v in _val.items())
                _note = {"licenseVersion": "服务端版本族 **XT6**（对应二进制中的 `/coremail/XT6/`）",
                         "primaryDomain": "主域",
                         "cookieName": "会话 Cookie 名"}.get(_key, "—")
                out.append(f"| `{_key}` | {_shown} | {_note} |")
            out.append("")
            out.append("> 全部 60 个开关在 40 次响应中取值完全一致，说明这是稳定的服务端能力声明。"
                       "原始数据（仅布尔值与短字符串）：`analysis/server-capabilities.json`。\n")
        except (ValueError, OSError):
            pass
    out.append("## 2. 接口总表\n")
    out.append(f"共 **{summary['rpc_total']}** 个 Coremail RPC 接口名，覆盖 {summary['modules_total']} 个模块。"
               f"其中 {summary['observed_in_runtime_logs']} 个在本机运行日志中被实际调用（合计 "
               f"{summary['observed_call_occurrences']} 次 `func=` 命中），"
               f"{summary['supported_not_observed']} 个为二进制内置但本机日志未观察到，"
               f"{summary['web_client_only']} 个仅出现在公司 Webmail 网页脚本中。\n")
    out.append("列说明：**协议**除注明外均为 Coremail HTTP JSON-RPC；"
               "**本部署可用性**依据服务端能力声明（§1.2）或真机实测——`能力声明:关闭` 表示该功能在本部署未启用，"
               "`已实测可用` 表示本项目已真机验证，`—（无对应开关）` 表示无法由能力声明判断；"
               "**端点或命令**即 `func=` 取值；"
               "**请求参数**来自客户端日志中真实观测到的 query 参数名与请求体类型（`query:` / `body:` 前缀）；"
               "**响应结构**只有经独立验证或单一形状无歧义的接口才采信，其余标注「日志未可靠记录（见 04 §6）」；"
               "**调用代码位置**为 REA/Ghidra 定位的函数或字符串地址，未定位者为 `未定位`；"
               "**可信度**：`已观察`=运行日志或真机实测、`静态`=仅二进制字面量、`Web脚本`=仅网页端；"
               "**验证状态**：`已验证`/`契约验证`/`未验证`。\n")
    coverage = []
    for mod in sorted(by_mod, key=lambda m: (-len(by_mod[m]), m)):
        rows = sorted(by_mod[mod], key=lambda r: r["method"])
        out.append(f"### 2.{mod} 模块（{len(rows)}）\n")
        out.append("| 名称 | 用途 | 协议 | 本部署可用性 | 端点或命令 | 请求参数 | 响应结构 | 认证依赖 | 调用代码位置 | 证据 | 可信度 | 验证状态 |")
        out.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for rec in rows:
            name = f"{rec['module']}:{rec['method']}"
            calls = rec["observed_calls"]
            if name in MACHINE_VERIFIED:
                evidence = f"运行日志 {calls} 次" if calls else "真机实测"
                confidence = "已观察"
                status = "已验证"
            elif name in CONTRACT_VERIFIED:
                evidence = f"运行日志 {calls} 次" if calls else "契约验证"
                confidence = "已观察"
                status = "契约验证"
            elif calls:
                evidence = f"运行日志 {calls} 次"
                confidence = "已观察"
                status = "未验证"
            elif rec["in_client_binary"]:
                evidence = "CMClient.exe 字面量"
                confidence = "静态"
                status = "未验证"
            else:
                evidence = "Webmail 脚本"
                confidence = "Web脚本"
                status = "未验证"
            if rec["in_client_binary"] and calls:
                evidence = "CMClient.exe 字面量 + 运行日志"
            req = shape_of(name, "query")
            res = shape_of(name, "response_shapes")
            out.append("| `{}` | {} | Coremail HTTP JSON-RPC | {} | {} | {} | {} | sid（登录后） | {} | {} | {} | {} |".format(
                name, PURPOSE.get(name, "用途待确认"), deploy_state(name, rec), endpoint_of(rec), req, res,
                TRACED.get(name, "未定位"), evidence, confidence, status))
            coverage.append({"name": name, "module": rec["module"], "method": rec["method"],
                             "status": status, "confidence": confidence})
        out.append("")
    out.append("### 2.x 按目标功能域的覆盖矩阵\n")
    out.append("把 138 个接口按本项目目标要求的功能域归并（按模块与方法名规则自动归类），"
               "并用同一套验证状态口径统计，便于直接看出缺口：\n")
    out.append("| 功能域 | 接口数 | 已验证 | 契约验证 | 未验证 | 代表接口 |\n| --- | ---: | ---: | ---: | ---: | --- |")
    for label, matcher in DOMAINS:
        members = [c for c in coverage if matcher(c["module"], c["method"])]
        if not members:
            continue
        verified = sum(1 for m in members if m["status"] == "已验证")
        contract = sum(1 for m in members if m["status"] == "契约验证")
        unverified = len(members) - verified - contract
        samples = "、".join(f"`{m['name']}`" for m in members[:4])
        if len(members) > 4:
            samples += " 等"
        out.append(f"| {label} | {len(members)} | {verified} | {contract} | {unverified} | {samples} |")
    out.append("")
    out.append("> 说明：归类规则基于模块名与方法名关键词，属于**分析口径**而非服务端语义；"
               "一个接口可能同时服务多个功能域（如 `mbox:updateMessageInfos` 同时用于已读、星标与移动）。\n")
    out.append("## 3. 非 RPC 端点（18）\n")
    out.append("| # | 端点 | 用途 | 证据 |\n| --- | --- | --- | --- |\n"
               "| 1 | `POST /coremail/index.jsp?cus=1` | Webmail 表单登录入口 | 二进制 + 公开登录页 |\n"
               "| 2 | `GET /login.jsp?...&onlyValidate2FA=true&preset=1` | 二次验证/设备登录跳转 | 二进制 |\n"
               "| 3 | `GET /coremail/lookup.jsp` | 账号/域查找 | 二进制 |\n"
               "| 4 | `GET /coremail/main.jsp?` | 主页面跳转 | 二进制 |\n"
               "| 5 | `GET /coremail/viewDownloadFile.jsp?key=` | 附件/文件下载 | 二进制 |\n"
               "| 6 | `GET /coremail/sso/entry.jsp?...&to=file.preview` | SSO 跳转文件预览 | 二进制 |\n"
               "| 7 | `GET /coremail/sso/entry.jsp?to=arch\\|setting.delivery.ai` | SSO 跳转架构/投递设置 | 二进制 |\n"
               "| 8 | `GET /coremail/cmcu_addon/sso.jsp` | 定制 SSO 插件入口 | 二进制 |\n"
               "| 9 | `GET /cmcu_addon/getImids.jsp`、`/cmcu_addon/requestToken.jsp?appId=` | 定制插件取 IM 标识/申请 token | 二进制 |\n"
               "| 10 | `GET /coremailclient/clientVersionCheck.jsp` | 客户端版本/部署校验 | 二进制 |\n"
               "| 11 | `GET /coremailclient/lunkrserverconfig.jsp` | Lunkr/AI 配置下发 | 二进制 |\n"
               "| 12 | `GET /coremailclient/sync/coremailfile/user/alluser.jsp` | 组织人员增量同步 | 二进制 + 日志 `[sync_org_user_v2]` |\n"
               "| 13 | `GET /coremailclient/sync/coremailfile/user/siteorg.jsp` | 组织架构增量同步 | 二进制 + 日志 `[sync_org_site_v2]` |\n"
               "| 14 | `POST <host>/ews/exchange.asmx` + `/autodiscover/autodiscover.xml` | EWS SOAP 适配器 | 二进制 |\n"
               "| 15 | `cmsso://fasoncm.rd.mt/coremail/ssologin.html?uid=` | 自定义 URL Scheme SSO | 二进制 |\n"
               "| 16 | `https://pcmail.coremail.cn/lunkr/s/json?func=cim.common:checkVersion` | Coremail 公网云侧接口 | 二进制 |\n"
               "| 17 | `https://pcmail.coremail.cn/lunkr/s/json?func=cim.common:dlFeedback&u=` | 云侧反馈上报 | 二进制 |\n"
               "| 18 | `/coremail/XT/jsp/{compose,mail,upload}.jsp` | **Web 端路由**，现有自研实现使用；原生二进制中未见这些字面量 | 公开 Webmail 脚本 |\n")
    out.append("> 注：第 18 条是路由族而非单端点；它与原生客户端走的 `/coremail/s/json` 不是同一条入口，"
               "跨服务器版本稳定性未验证，列为待验证项（见 06）。\n")
    out.append("### 3.1 AI / LLM 集成端点（二进制字面量，与邮件协议无关）\n")
    out.append("客户端内置多家大模型服务的接入点，含**本地推理**与**云端**两类。"
               "对「AI 原生邮箱客户端」这一目标而言，这是既有产品已经覆盖的能力面：\n")
    out.append("| 提供方 / 用途 | 字面量 |\n| --- | --- |\n"
               "| 本地推理（Ollama 默认端口） | `http://localhost:11434`，路径 `/api/generate`、`/api/embeddings`、`/api/tags` |\n"
               "| DeepSeek | `https://api.deepseek.com` |\n"
               "| 月之暗面 Kimi | `https://api.moonshot.cn` |\n"
               "| OpenAI | `https://api.openai.com` |\n"
               "| 硅基流动 | `https://api.siliconflow.cn/` |\n"
               "| 阿里云百炼（兼容模式） | `https://dashscope.aliyuncs.com/compatible-mode` |\n"
               "| 讯飞星火 | `https://spark-api-open.xf-yun.com` |\n"
               "| 本地全文索引 | `/XAPIANDB`（Xapian 数据库路径） |\n")
    out.append("> 说明：这些是**客户端自身**的 AI 能力接入点，不等于任何邮件服务器接口；"
               "对应的协议与鉴权方式未分析（见 06）。\n")
    out.append("### 3.1.1 AI 功能面（实测：`app/locale/webai.zh_CN.js`，5,695 B）\n")
    out.append("客户端内置 **WebAI 提示词配置**，定义了 AI 的具体操作面（每个操作一个提示词模板）：\n")
    out.append("| 操作键 | 用途（据提示词正文） | 输出格式约束 |\n| --- | --- | --- |\n"
               "| `compose` | 按标题+大纲写整封邮件 | HTML（禁 Markdown），限 $words 字 |\n"
               "| `invite` | 写会议邀请邮件 | HTML |\n"
               "| `collate` | 全文校对（拼写/语法） | JSON `{\"targetMessage\":…}` |\n"
               "| `simpleCollate` | 简化校对 | 纯文本 |\n"
               "| `copilot` | 行内补全（`<mask />` 处） | `ANSWER: …` |\n"
               "| `copilotMiddle` | 句中补全（限 1–2 词） | `ANSWER: …` |\n"
               "| `operate` / `operateInLimitedWords` / `operateTone` | 对选中文本做改写（含限字数、指定语调） | HTML |\n"
               "| `operateTranslate` / `translate` | 将文本翻译为 `$locale` | 保留原格式 |\n"
               "| `generate` | 按标签生成 HTML 内容 | HTML |\n"
               "| `summary` | 分点总结 | 纯文本 |\n")
    out.append("**提供方选择规则（同一文件，实测）**：`deepseek` 档案带 `condition` 数组——\n"
               "先 `{name: ollama, model: [\"/deepseek.*/\"]}`，再 `{name: deepseek}`。\n"
               "即：**若本地 Ollama 上存在匹配 `/deepseek.*/` 的模型则走本地，否则回落到云端 DeepSeek**。\n"
               "这与二进制中的 `http://localhost:11434`（Ollama 端口）互相印证。\n")
    out.append("> 意义：自研的「AI 原生」客户端若要与之对标，需要覆盖的不只是"
               "「接一个大模型」，而是**这 13 个操作 + 输出格式约束 + 本地/云端回退**这一整套能力面。\n")
    out.append("### 3.2 其他字面量 URL（含第三方与签名校验）\n")
    out.append("| 字面量 | 用途 |\n| --- | --- |\n"
               "| `https://cloud.icoremail.net`、`https://mt.icoremail.net`、`https://www.coremail.cn` | Coremail 官方/云侧域名 |\n"
               "| `https://lunkrcm07.rd.mt`、`https://help-lunkr.coremail.cn/`、`https://lunkr.coremail.cn/log/report` | Lunkr 内部/帮助/上报 |\n"
               "| `lunkr-plugins/weixin/createGroup`、`lunkr-plugins/weixin/getUserIdByEmail` | 微信插件接口 |\n"
               "| `http://cmail.`、`https://cmail.`、`http://mail.`、`https://mail.` | 主机名前缀（用于拼接部署地址） |\n"
               "| `/coremail_plugin/config/client_expirydate.ini` | 客户端到期配置 |\n"
               "| `http://cacerts.digicert.com/...`、`http://ocsp.digicert.com`、`http://crl3/4.digicert.com/...` | **代码签名证书链校验**（DigiCert），非业务接口 |\n"
               "| `http://schemas.microsoft.com/exchange/...`、`http://schemas.xmlsoap.org/soap/envelope/` | EWS/SOAP 命名空间 |\n"
               "\n> 注意：本机代理路径 `/adm_api/s/json` **在二进制中没有字面量**，是运行时与 `127.0.0.1:9900` 拼接而成；"
               "这说明仅靠字符串扫描会漏掉动态构造的端点。\n")
    out.append("## 4. 共享传输层与已定位的代码位置\n")
    out.append("REA `trace_feature` 已确认：\n")
    out.append(f"- {TRANSPORT_NOTE}。\n")
    out.append("- 其余已定位接口：\n")
    out.append("| 接口 | 字符串地址与引用函数 |\n| --- | --- |")
    for name in sorted(TRACED):
        out.append(f"| `{name}` | {TRACED[name]} |")
    out.append("")
    out.append("未列出的接口其「调用代码位置」仍为未定位；批量定位由 `tools/research/trace_batch.ps1` 在后台逐个执行，"
               "结果汇总见 `analysis/rea-trace-summary.json`。\n")
    out.append("**推断（非已验证）**：`FUN_140cfca00`（10,128 B，引用 `user:login` 与 `/s/json?func=` 系列）"
               "规模最大，符合「登录状态机 + 设备/2FA 分支」的形态；需反编译确认后才能作为结论。\n")
    with open(os.path.join(WS, "docs/research/02-通信协议与接口清单.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    print("written docs/research/02-通信协议与接口清单.md, lines:", len(out))


if __name__ == "__main__":
    main()
