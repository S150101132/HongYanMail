# Coremail 独立客户端协议可行性：静态与既有日志证据

日期：2026-10-15。范围：只读安装程序、安装网页资源、已有客户端日志；未连接公司服务器，未读取 security.sdat、账户密钥或导出邮件内容。未实现或运行网络客户端。

## 结论

**收发协议存在且已有成功使用痕迹；补充公开网页代码已证明一条可实现的普通密码登录路线。** 原生 token/nonce/sign 登录仍未完全恢复，但无需先复制原生设备密钥才开始验证：当前公开 Webmail 页面采用正常 HTTPS 表单登录，设备初次注册和 2FA 挑战仍应由服务器决定。尚未运行独立登录原型，因此不能声称其已成功。客户端包含原生 `MailClientEngine` 的 Coremail HTTP 分支，真实日志出现 `mbox:getAllFolders`、`mbox:listMessages`、`mbox:getMessageData`、`mbox:compose`。

## 补充：当前公司公开登录页面恢复结果

此节基于 root 已经公开读取并保存的资源，本研究仅离线读取这些文件：

| 本地资源 | SHA-256 |
|---|---|
| `analysis/web-login/public-login.html`（18,720 字节） | `e8f57fdf0452eed72404cc5327db6ee805dfa8483d7b27285a12ecf27c7b31e1` |
| `analysis/web-login/public-login.js`（974,850 字节，共享 bundle） | `b8086c96f9cd89368d36816fd4a911ff6ff0a62a91ed9d53483aa80ca837a394` |
| `analysis/web-login/login.03931.js`（14,911 字节，登录 chunk 966） | `256f0f40d7cbf47167658617196900ac1aa2c2313c845c00a585a321e866824a` |

观察到的普通登录路径：

1. HTML 模板明确 `<form action="/coremail/index.jsp?cus=1" method="post" class="j-login-form u-form">`。登录 chunk 的 `ye()` 最终调用 `pe.submit()`，`pe` 是该表单。它是正常浏览器表单 POST，编码按表单默认 `application/x-www-form-urlencoded`，不是 `user:login` JSON 密码登录。
2. 输入 `uid/password`；`uid` 去首尾空格并规范 @ 周围空格。`primaryDomain` 为 `inspur.com`，完整 UID 辅助函数在无 @ 时补此域。
3. 表单有 `locale=zh_CN`、`nodetect=false`、`destURL=`、`supportLoginDevice=true`、`supportDynamicPwd=true`、`supportBind2FA=true`、`action:login=`；另有初始为空的 `accessToken/timestamp/signature/nonce/device/authorizeDevice/loginType/lookupCallback`，以及按可见登录类别使用的 `smsaddr/verifyCode/verifyCellCode`。
4. 当前 HTML 只有 `#password`，全文不存在 `#fakePassword`。共享 module31692（字符偏移 318950）的 `action()` **仅在存在 fakePassword 元素时**调用 `user:passKey`，使用响应 `key.q` 加密密码并更新 sid。此模板不满足该条件。不能将其他模板的密码加密分支强加到当前登录页。
5. 公开设置 `authDeviceKeep=true`、`encKeyQ=''`、`secondAuthMeta={config:'custom',needToAuth:false}`。登录 chunk 在提交前调用 module55401.F 收集 Webmail 设备描述。描述字段为 `{uuid,imie,friendlyName,model,os,osLanguage,deviceType}`，其中拼写确实是 `imie`。`deviceType` 由 `toAdmin` 中的 hxphone/webadmin/archadm 选择 Hxphone/Webadmin/Archadm，默认 Webmail。令 `model = device.name==='na' || device.name==='pc' ? os.name : device.name`；`uuid=imie=deviceType.toLowerCase()+model`；`friendlyName` 为浏览器名，版本不为 `-1` 时追加空格和版本；`os=os.name`、`osLanguage=navigator.language`。仅本地存储能力可用时填写该 JSON。
6. module55401（共享 bundle 字符偏移 321920）将 `nonce` 置空；如无已有注册签名结果，`accessToken/timestamp/signature` 均置空。存在旧授权设备时会填写其正常签名。**独立新登录不需要读取既有客户端/浏览器 secret，不应声称已有设备身份。** 服务端若要求绑定、验证码、2FA，应保留响应页面并交由用户完成合法步骤。
7. 成功后需由自己的 HTTP 会话保留响应 cookie 和重定向，并由认证完成的响应确定会话。不能把空的公开 `X.s` 当成会话，也不应依赖用户当前已登录浏览器的 sid。

因此可复现方案是：独立 cookie jar GET 登录页 → 取当前表单配置 → 用户提供账号密码 → 按页面字段及真实设备描述 POST 同一表单 → 处理服务器重定向/正常认证挑战 → 验证有效会话。这里“可复现”表示所需动作已有代码锚点，尚未实际提交验证，挑战处理仍需按实际返回实现。

公开共享 bundle module3927（约字符 329098）还直接恢复了 Webmail RPC：`wmCall(func,data)` 默认 POST，经 `wmURL` 形成 `/coremail/s/json?sid=...&func=...`，`Accept: text/x-json`，对象 data 的 Content-Type 为 `text/x-json; tz="<user timezone>"`。module12568（字符 82409）序列化基本 JSON 数据，Date/Moment 另有 `!!date '...'` 扩展。成功时以 `code==='S_OK'` 解析并向调用者返回 `var`。`!name:func` 别名映射 `/coremail/XT/jsp/name.jsp?func=...`，`%name:func` 映射 `/coremail/common/name.jsp?func=...`。这些是当前公司公开代码的直接证据，强于前述安装日程脚本的通用推断。

发信具体 `action` 值已在后续 C chunk 恢复，见下节。共享 bundle 本身没有 `mbox:compose` 字面值，不足以单独恢复发送协议。

### 补充：X/M 邮件模块的可实现接口

root 另保存了公开 `X.03931.js`（925,858 字节）与 `M.03931.js`（60,397 字节）。X 的 loader 字符偏移 512204 明确 `mail.c*` 页面加载 `173.03931.js`、`400.03931.js`、`C.03931.js`；普通 mail 页加载 `173.03931.js` 和 `M.03931.js`。

- **文件夹（X 字符约 622000）**：`X.wmCall('!mail:getAllFolders', $.param({stats:true,threads:De()}))`，即 POST `/coremail/XT/jsp/mail.jsp?func=getAllFolders`，表单 body `stats=true&threads=...`。这与原生 `mbox:getAllFolders` 是两个观察到的入口，不应混同。
- **列表（X 字符约 623000）**：`mbox:listMessages` 的构造字段为 `{start:e.start||0,limit:e.limit||user.maxlist,mode:'count',order:...,desc:...,returnTotal:true,returnTag:setting.supportMailTagged,summaryWindowSize: user.list_brief_text?limit:0}`，再选取 `fid` 或 `fids`、`mboxa/topFirst/tags/filter/filterFlags/ruleConditions`。普通文件夹的默认 order 是 `date`，若 `user.index_time_category` 开启则为 `receivedDate`；普通 desc 默认 true。特殊文件夹另有 deferredDate、skipLockedFolders 等分支。线程配置会改用 listThreads，最小列表不必依赖线程分支。
- **正文渲染（X 字符 627772）**：`X.wmCall({url:'!readMessage',sid:[]}, $.param(e))`，即 POST `/coremail/XT/jsp/readMessage.jsp`；通过 `sid:[]` 避免 URL 中的 sid 参数，依赖正常会话 cookie。响应 `var` 含 `mail` 与 `mailInfo`。参数 e 来自具体读信调用，包含 `mid` 及可选 `mboxa/part/markRead`；避免仅为验证读信而改变已读状态。
- **原始邮件（M 字符 39109）**：真实代码 `$.get(X.wmURL({func:'mbox:getMessageData',mode:'text',mid:s}), callback)`，证明 GET `/coremail/s/json?...&func=mbox:getMessageData&mode=text&mid=...`；该调用用于 SMIME 原文加载，不能断言同一 mode 会返回 JSON envelope。
- **创建 compose 会话（X 字符 629704）**：`X.wmCall({url:'!compose',uid:e.uid||[]}, $.param(...,true))`，body 为 `composeId`（已有会话）或 `ctype`（默认 `normal`），可选 `subject/content/isHtml/mid/part/mboxa/isDecodeMail/to/expandThreadMid/mailCipherPassword/stringifyScheduleDate/switchAccount`。这对应 `/coremail/XT/jsp/compose.jsp`，返回对象有 id 等会话数据。
- **发送状态**：X 字符 598824/599031 证明快速回复调用 `action:'deliver'`，但是目标为 `mbox:replyMessage`；字符 516525 是 `cal:compose`。71日志中492条compose邻近±1行的限定枚举抽取未命中 action。新邮件发送由下面的 C chunk 直接证明。

### C chunk：新邮件 continue → deliver 的直接证据

root 保存的 `analysis/web-login/C.03931.js` 中 module94035 是完整写信界面。以下符号名是压缩后的局部符号，字符偏移按 Python UTF-8 解码后的 string 计算。

字符约 86700 的 `N(e,t,n,a)` 构造：

```js
a = assign({id:t, attrs:n=n||{}, returnInfo:true}, a,
           del(n, ['encryptPassword','savePassword']));
if (e === 'send') {
  e = n.scheduleDate ? 'schedule' : 'deliver';
  a.autosaveHitCounter = true;
}
a.action = e || 'continue';
const query = del(a, ['stringifyScheduleDate']);
if (n.lettercontent) {
  // composeF / multipart 路径
} else {
  return X.wmCall(assign({url:'%mbox/compose?isUserConfirmed=true'},query), a);
}
```

结合共享 `wmURL` 的 `%` 路由规则，**普通正文实际使用 POST `/coremail/common/mbox/compose.jsp?isUserConfirmed=true&sid=...`，body 为 Coremail JSON。** `encryptPassword/savePassword` 从 attrs 移到顶层；不应给普通无加密邮件添加密码控制字段。

新信初始化 `F()` 对没有 mid/id 的输入调用 `N(null,null,attrs)`，即相同入口的 `action:'continue'`。初始化 attrs 从 subject、attachments、to/cc/bcc 取值，字符串收件人会被解析成数组。响应的 `var` 既可能是字符串 composeId，也可能是带 id 的对象：代码明确用 `_.isSTR(e)?e:e.id`。**按已证明的完整流程应先 continue 获取 composeId，再 deliver；未证实服务端允许省略初始化直接 deliver。**

发送函数 `un`（字符 133560）调用 `fn('send',attrs,...)`，fn 调用 `N('send',de,attrs,...)` 并加 `reorder:true`；正文来自编辑器 `n.xDoc()`。只读静态代码足以确认 action=deliver 的来源，不是根据接口名猜的。

attrs 的真实构造：

- `la()`（字符 150353）：默认继承当前 account；收件人为 `to/cc/bcc` 数组，数组成员是普通邮件地址或格式化显示名地址字符串，**不是对象**；有发件账号选择器时补 `from/account`。默认 account 的确切来源在 `F()`（约字符 85900）：从 `X.U.allemails` 取第一项 `addr/name`，当 `user.displaysender != 1` 且有 name 时格式化为显示名地址，否则用 addr；因此是地址字符串，**不是账户 ID**。`X.U.allemails` 由 X 的 `Ke()` 调用 `!mail:getAllAccounts`（`/coremail/XT/jsp/mail.jsp?func=getAllAccounts`）获取，continue返回对象中的account可以覆盖默认值。
- `da()`（字符 151095）：填 `showOneRcpt/priority/smimeSign/smimeEncrypt/smimeEnvelopId/saveSentCopy/requestReadReceipt/scheduleDate/autoDel/forbidDownload/subject/isHtml/content/attachments`；还可能有 `ctrls`（安全级别 `CM-SECURITY` 与 at 信息）。`saveSentCopy` 的拼写确如此，值直接来自 checkbox `.is(':checked')`，是 boolean；`isHtml` 是编辑器配置 boolean，`content` 是正文 string，三者均在 attrs 内。发送 fn 会把 content 替换为编辑器 `xDoc()` 的 string。这些是 UI 总构造字段，**不等于全部是服务器必填字段**。
- 不含附件、纯文本的最小验证 attrs 候选是 `{account:<当前账户>,to:[<本人地址>],cc:[],bcc:[],subject:<测试标记>,content:<测试正文>,isHtml:false,saveSentCopy:true,attachments:[]}`。应保留初始化返回的账户/安全控制默认值，不能用空值覆盖服务器要求的密级。
- 候选发送 body：`{id:<continue返回ID>,attrs:<上式>,returnInfo:true,action:'deliver',autosaveHitCounter:true,reorder:true}`。这是由 UI 控制流缩减的实现方案；是否可再删字段需要真实测试，尚不声称 server schema 已穷尽。
- 发送成功 UI 回调读取 envelope 的 `savedSent.mid/msid` 用于已发送定位；`S_OK` 仅代表服务器接受本次操作，收件与最终投递应继续用测试标记/Message-ID验证。

此证据已足够实现用户授权的自发自收连接性试验，但本报告没有执行任何发送。

### `!!date` 的安全兼容处理

直接静态证据：共享 module12568 序列化 Date/Moment 为 `!!date '<text>'`；module43942.I 明确 `<text>` 格式为 `YYYY-MM-DD HH:mm:ss`，使用用户时区，不能一律标成 UTC。共享、X、M 中没有找到对该类型扩展的响应解析器；浏览器 jQuery 中直接使用 `JSON.parse`。因此“扩展响应如何由服务器按 Accept 转换”仍未知。

短期 probe 如遇非标准 JSON，可实现受限 lexer：只在双引号 JSON 字符串**之外**识别 `!!date`，接受单引号日期 token 并替换为 JSON 字符串，再交给标准 `json.loads`；遇到未知 tag/格式应明确报解析不兼容。不能 `eval` 响应，也不能用无状态全局替换改动邮件正文中的相同文字。此处理是兼容设计建议，不是已找到的官方解析实现。

一个重要纠正：**`user:authenticate2` 不能直接解释为登录第二因素接口。** 其原生字符串位置靠近 `unlockFolder` 及 `protocol_updatemailflags_coremail.cpp`，更可能服务于安全文件夹解锁。该结论目前是邻近证据推断，未有反编译控制流佐证。

## 证据身份与限制

- `C:\Program Files\cmclient\CMClient.exe`，40,818,224 字节，SHA-256 `a2cc47c5efb74524dd82e447204d0cc9587a971f9cda9552187eee19abb37c5b`。下表的偏移均为**文件偏移**，不是 RVA/虚拟地址。
- 既有 REA JavaScript 结果：`ev_2f18404c2ce69640a605a87fb07eed298970bf584454ddf78f1dcb719534c77e`，摘要位于 `analysis/rea-javascript-summary.json`；覆盖 112 个相关文件、90 个 JS、8 个解析失败。没有重新加载 229 MB 原始结果。
- 本次 REA `binary_session` 读取得到未打开原生目标；Ghidra `not_configured`（要求 Ghidra 12.1.x），因此本报告**没有反编译、交叉引用或真实控制流证据**。未安装分析引擎或改动配置。
- 安装文件中 ASCII 字符串与其物理邻近布局，只能证明字符串存在；不能证明调用顺序、必填参数、每个版本都使用相同字段。
- 71 个已有日志文件仅自动提取固定标记计数、函数名、路径、字段名和 JSON 类型树。原始日志行、会话标识、令牌、签名、地址、邮件内容未写入报告。邻近日志行可能混入并发任务，因此邻近键名不可直接视为一个完整请求。

## HTTP 封装的直接证据

安装脚本 `app/template/bookmeeting/static/js/calendar_utils.js`（SHA-256 `d1fde156bd186e9862584fb168833988757e0c491961556f143a692d1f6aacec`）第 1 行包含如下真实代码：

```js
function getUrl(e) {
  return app.apiServer + "/s/json?func=" + encodeURIComponent(e) + "&sid=" + app.sid;
}
// 原文压缩格式，以上仅重新排版
```

同文件字符偏移 2393 开始的 `jQuery._post` 使用 `type:"post"`、`data:JSON.stringify(t)`、`contentType:"text/x-json"`、`dataType:"json"`，并用 `e.code === "S_OK"` 判断成功。这是**日程页面**的具体 HTTP 实现，不能自动推广为原生登录和发信的全部格式。

二进制补充字符串：

| 文件偏移 | 观察 |
|---|---|
| `0x2104b90` / `0x2104ba0` | `/s/json?func=` / `&sid=` |
| `0x21048f8` | `application/x-cm-json` |
| `0x21049a8` | `text/x-json,application/x-cm-json,application/json` |
| `0x2104a10` | `application/x-cm-json;charset=UTF-8;tz=UTC` |
| `0x2104a40` | `text/x-json;charset=UTF-8;tz=UTC;useTypeCastExtension=1` |
| `0x2104a78` | `application/json;charset=UTF-8` |
| `0x2104ac8` / `0x2104ae8` | `Coremail.sid` / `X-CM-AUTH-SID` |
| `0x2104f28` | `&mboxa=`（共享邮箱/邮箱上下文的候选参数，未恢复语义） |

因此建议设计可配置的 Coremail HTTP transport，并明确兼容普通 JSON 与 Coremail 扩展类型。日志没有 `GET`、`POST`、上述 MIME 字面值，**不能由日志确定每条接口的 HTTP method、Content-Type、cookie/header 选择**。

## 登录：观察、候选流程和缺口

| 证据 | 可以确认 | 不能确认 |
|---|---|---|
| `0x2104ba8` `/s/json?func=user:getPasswordKey`；`0x2105588` `checkPasswordEncryptType` | 存在密码密钥/密码加密类型相关实现线索 | 是否必调；公钥结构；加密算法、padding、编码、时间戳拼接 |
| `0x2105268` `/s/json?func=user:login` | 原生程序含此登录路由 | 当前公司实际首次登录的完整载荷 |
| `0x2104fc8–0x2105050` `authType/supportPermDomains/supportBind2FA/dynamicPwd/verifyCellCode/verifyCode` | 登录附近存在认证选项与验证码字段 | 字段是否同处一请求、必填值及顺序 |
| `0x21046d8–0x2104700` `accessToken/accessSecret/user:setDeviceSecret/DeviceSecret` | 存在设备/访问凭据机制 | 合法注册协商和密钥保管策略 |
| `0x21053f0–0x21054f0` `accessNonce/supportAuthDevice/&nonce=/signature/authDevice`、token/nonce/sign 日志格式 | 存在设备认证和签名分支 | 签名摘要、原文拼接、有效期、重放保护具体规则 |
| `0x20f9d90` `/s/json?func=user:getTempSession`；`0x20f9ef0` `user:triggerSecondAuth`；`0x20f9f08–0x20f9f18` `vc_uid/tempSid/trans` | 存在临时会话和触发第二认证的机制 | 发起/轮询/提交验证码的完整调用协议 |
| `0x205a730/0x205b6c0/0x205c618` | 存在 App 授权、手机、图形码登录对话框类别 | 公司启用的具体类别和独立应用可获权限 |
| `0x2104820–0x2104888` | 有 `FA_NEED_VERIFY_CODE/FA_NEED_DYNAMIC_PWD/FA_NEED_VERIFY_EMAIL/FA_NEED_VERIFY_AUTH_CODE/FA_NEED_BIND_DEVICE` | 各错误完整响应字段及后续认证参数 |
| `0x21051f8/0x2105210` | 有 `FA_INVALID_TEMP_SESSION/FA_NEED_BIND_TWO_FACTOR_AUTH` | 如何合法完成绑定 |

既有日志的更强证据：

- 51 条登录结果包含成功码 `S_OK`；其 `res:` 后 JSON 的相同类型树是：

```text
{code: string,
 var: {nonce: string, primaryEmail: string, sid: string,
       uid: string, webname: string}}
```

- 52 条登录摘要同时包含固定标记 `token:`、`nonce:`、`sign:`（只统计标记，不读取或复制值）。这支持“已有设备访问令牌 + nonce + 签名”的自动登录分支，但**不证明首次输入密码也走相同分支**。
- 71 文件中 `dologin` 60 次，`Coremail Login` 113 次；`user:login/getPasswordKey/getTempSession/triggerSecondAuth/authenticate2/commonlogin` 字面值均未命中。缺少 HTTP 路由日志可能只是登录的日志级别/格式不同，不能证明某条接口未执行。

待验证的合理流程候选：确定服务 base URL → 读取所需公共能力/密码加密资料 → 正常登录 → 按服务器返回完成图形码/动态密码/短信/App 授权/设备绑定 → 获得有效会话 → 收发。已有设备登录可能改用 accessToken + nonce + signature。**这些箭头是工作假设，尚非恢复的调用链，不能直接据此写生产认证代码。**

## 收取与发送：最小可行协议范围

| 功能 | 静态锚点 | 已有日志证据与参数/结果形状 |
|---|---|---|
| 文件夹 | `0x20f8bf8` `mbox:getAllFolders`；邻近日志明确结果为 array | 函数出现 9402 次；query 键 `func/sid`。相邻响应可解析为 `{code:string,var:[{id:int,name:string,flags:{system:bool},keepPeriod:int,stats:{messageCount:int,messageSize:int,unreadMessageCount:int,unreadMessageSize:int}}]}` |
| 列表 | `0x20f8b10` `mbox:listMessages`；`0x210e3b0` HandleResult；`0x210e420` `mail list result var is not array` | 函数出现 9561 次；query 键 `func/sid`。邻近请求键候选：`fid/start/limit/order/desc/detailLevel/returnTag/returnTotal/filter`，其中 `modifiedDate` 可能在 filter 中；完整嵌套未恢复 |
| 原始邮件数据 | `0x2102690` `mbox:getMessageData&mid=`；`0x21055e0` `mbox:getMessageData`；`0x2110660` `protocol_fetchmailbody_coremail.cpp` | 出现 11110 次；query 键 `mid/mode/func`。下载日志含 ContentRange 候选；没有证明返回正文/EML 的 Content-Type 和 `mode` 值 |
| 读信渲染/安全信息 | `0x20b8ba0` `mbox:readMessage`，邻近 `encryptInfo/waterMark/bNeedPassword` | 本次聚合日志无该函数，不能当作收取 EML 的唯一入口 |
| 发信 | `0x20f49b8` `mbox:compose`；`0x2112940` `protocol_sendmail_coremail.cpp` | 出现 492 次；query 键 `func/sid`。相邻结果出现 `{code:string,var:string}` 及 `{code:string,draftId:string,var:string}`；未恢复完整发信 payload，也未证明每个结果表示最终投递成功 |
| 回复/准备 | `mbox:getComposeInfo`；日志中的 `mbox:replyMessage` | replyMessage 观察键 `action/attrs/id`；attrs 邻近键 `to/cc/bcc/subject/account/ctrls/extraHeaders/localDate/localTZ/priority/requestReadReceipt/scheduleDate/showOneRcpt/charset/isHtml/autoDel`。这不能代替新邮件 compose 的 schema |
| 附件/分片 | `0x2111d70` `mbox:composeF&composeId=`；`0x21127f0` `upload:directData&inlined=true&composeId=%v&attachmentId=%v&offset=%v` | 静态有 composeId/attachmentId/offset 及 MIME multipart 实现；初版可先不处理附件，避免扩大验证范围 |
| 收件人检查 | `0x2114258` `mbox:validateRcpts`，邻近 `rcpts/enableMailTips/mailSize` | 参数名仍是邻近推断，无实际日志载荷证明 |

不要把程序同时支持的 IMAP/SMTP/EWS 路径当作公司开放的协议。安装程序中明确包含多个适配器，只有 Coremail 分支与当前目标条件相符。

## 最小验证应具备的证据

1. 首次合法认证的 HTTP route、method、Content-Type、字段名/类型，以及设备注册和服务器挑战分支；必须由新的用户授权正常登录观察或管理员文档确定。旧 token 的成功自动登录不能替代此项。
2. 一个有效会话下 getAllFolders/listMessages 的请求 JSON 类型树与返回 envelope；确认分页、filter 和排序。
3. getMessageData 的 mode 与原始邮件返回方式、是否需要独立 cookie/header。
4. 一封明确授权的自发自收测试邮件，得到 compose 成功、已发送或收件箱中同一 Message-ID，以及服务器回执/投递状态。不得以仅 HTTP 200 或草稿成功判定发信完成。
5. 会话超时和需要再次认证时停止并提示用户走正常认证，保留全部 2FA、设备控制和服务器拒绝语义。

下一阶段应优先补齐认证/请求 schema，再做最小 CLI 原型：登录 → 文件夹 → 限量列表 → 明确选定测试邮件 → 经授权发送到自己。当前证据没有支持直接复用或提取已存设备 secret，也无需这样做。

## 官方资料核对

仅搜索公开官方 primary 来源。官网[邮箱客户端产品页](https://www.coremail.cn/product_6.html)确认支持二次验证；[客户端下载页](https://www.coremail.cn/download.html)确认 Air 多平台客户端定位。官方[帮助中心写信说明](https://mail.coremail.cn/coremail/help/compose_zh_CN.jsp)为用户操作说明，不提供 HTTP schema。本次有界官方搜索未找到 `user:getPasswordKey` 或 `mbox:compose` 的可用公开接口规范；这不证明官方客户/集成文档不存在。

## 生成的脱敏辅助证据

- `analysis/protocol-focused-strings.json`：协议相关 ASCII 字符串及文件偏移，限定本次关注区间。
- `analysis/runtime-protocol-field-clues.json`：同一日志行中的 query/JSON 字段名计数。
- `analysis/runtime-protocol-neighborhood-clues.json`：附近日志的字段名、固定认证标记计数；并发混入限制已标注。
- `analysis/runtime-protocol-json-shapes.json`：可解析响应的键/类型树，所有 scalar 值省略。
- `analysis/runtime-route-clues.json`：root 已生成的 origin/接口计数。

这些计数包含请求/响应/重复摘要，不应解释为唯一网络请求次数。
