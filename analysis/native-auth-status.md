# Native authentication analysis — bounded partial result

## Outcome

No native authentication algorithm was decompiled. One independent REA 6.5.0/Ghidra 12.1.4 MCP session admitted the executable successfully, but its first `search_strings` triggered default cold analysis and returned `provider_timeout` after 900,618 ms (configured startup budget 900,000 ms). No retry or alternate analysis profile was attempted, following the root task's instruction. The read-only static leads below are suitable for a future focused investigation; they do not establish a working request or server behavior.

## Target and execution boundary

- Target: `C:\Program Files\cmclient\CMClient.exe`.
- SHA-256: `a2cc47c5efb74524dd82e447204d0cc9587a971f9cda9552187eee19abb37c5b`.
- Native PE x86-64; admitted size 40,818,224 bytes.
- REA profile digest: `ab1af6a0e002de5d4940f7e3d2cfd844cfe45f918fcd3678b343002640413374`.
- REA run ID: `f85487f6-bdd5-4c14-b4a1-921501ea1f14`.
- Only REA/Ghidra tooling executed. CMClient was not executed or attached. User credentials, account databases, and application logs were not read. No requests were sent to a mail server by this task.
- The MCP tool schemas were obtained using `tools/list` before requests. All native requests shared one independent stdio MCP server, distinct from the root task's MCP connection. Only process-local environment was set; no shared configuration changed.

## Observed byte-level leads

The file contains `/s/json?func=user:login` at file offset `0x2105268`, mapped by the PE section headers to VA `0x142106068`. A candidate RIP-relative LEA/MOV reference occurs at VA `0x140cfcb05` (file offset `0xcfbf05`). The PE exception-directory entry encompassing this candidate spans `[0x140cfca00, 0x140cff190)`.

The same runtime-function span contains byte-pattern candidates referencing `authType`, `accessToken=`, `signature`, and `Coremail Login: uid:%v %v`. These are raw instruction-pattern candidates, not Ghidra-confirmed cross-references or recovered source function boundaries.

Nearby static strings include `supportBind2FA`, `dynamicPwd`, `verifyCellCode`, `verifyCode`, `&timestamp=`, `accessNonce`, `supportAuthDevice`, and `authDevice`. Nearby error names include `FA_INVALID_BIND_DEVICE`, `FA_INVALID_DYNAMIC_PWD`, `FA_INVALID_VERIFY_CODE`, `FA_INVALID_VERIFY_CELL`, and `FA_NEED_BIND_TWO_FACTOR_AUTH`. This establishes literal presence only; adjacency does not prove field direction, requiredness, encoding, or the server response schema.

The `accessNonce` string at VA `0x1421061f0` has candidate references inside PE runtime-function extents `[0x140d08140, 0x140d08fce)` and `[0x140d08fd0, 0x140d0a039)`. This may be useful for distinguishing response parsing from request construction in a later successful analysis.

## Unknowns

- Exact password transformation, request serialization, HTTP method, token/nonce/signature derivation and response-to-session handling remain unverified.
- The binary's literals alone do not establish that an independently constructed login is accepted by the current `newmail.inspur.com` deployment.
- Device authorization, MFA, captcha and policy checks must follow the normal server flow. No bypass was investigated or attempted.
- No claim is made that the candidate functions have been decompiled, that Ghidra completed analysis, or that its typed-string search returned matches.

## Retained evidence and cleanup

- `native-auth-initialize.json`: separate MCP initialization identity.
- `native-auth-tool-schemas.json`: actual tool inventory and schemas.
- `native-auth-jobs/001-open.result.json`: successful target admission.
- `native-auth-jobs/002-search.result.json`: complete timeout and diagnostic record. Ghidra imported the file and started analyzing all memory/code; the captured tail reports absent PDB and malformed embedded PNG/GIF data, but it does not establish a unique timeout cause.
- `native-auth-raw-strings.json`, `native-auth-login-adjacent-strings.json`: literal byte offsets. These contain program protocol constants and should not be mistaken for observed user credentials or live requests.
- `native-auth-raw-xrefs.json`: PE mappings, candidate references, exception-directory extents. Reproducible helper: `.tools/native-auth-raw-xrefs.mjs`.
- `native-auth-evidence.json`: REA export from the bounded session; failed native analysis does not create a successful decompilation record.
- `native-auth-jobs/006-close.result.json`: successful `close_binary` result. The stdio controller exited with status 0; no Java process remained. REA's private runtime directory `rea-ghidra-f8a83756b374d5c362f4429f2d51b022` no longer exists after cleanup. No pending xref request was executed after the timeout.
