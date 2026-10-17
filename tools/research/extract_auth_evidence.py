"""Collect authentication and session evidence from CMClient.exe string literals.

Static only: counts occurrences of each auth-related literal and enumerates the
FA_* server error codes. No server interaction, no credential material.
"""
from __future__ import annotations

import collections
import json
import re

BIN = r"C:\Program Files\cmclient\CMClient.exe"
OUT = r"D:\workspace\redocoreemail\analysis\native-auth-evidence-2026-10-16.json"

LITERALS = [
    # routes
    "/s/json?func=user:login", "user:login", "user:getPasswordKey", "user:getPasswordURL",
    "user:getTempSession", "user:triggerSecondAuth", "user:setDeviceSecret",
    "user:getAliasWithSeed", "user:listDevices", "user:authenticate2",
    "user:validatePassword", "user:generateVerifyCodeImage", "sms:authSendValidCode",
    "user:getAttrs", "user:setAttrs", "/coremail/index.jsp", "/login.jsp",
    # credential / device material
    "accessToken", "accessSecret", "accessNonce", "authorizeDevice", "deviceSecret",
    "authDevice", "supportAuthDevice", "supportBind2FA", "supportDynamicPwd",
    "checkPasswordEncryptType", "dynamicPwd", "verifyCode", "verifyCellCode",
    "tempSid", "vc_uid", "primaryDomain", "loginType", "commonlogin", "dologin",
    "X-CM-AUTH-SID", "Coremail.sid", "sid", "nonce", "signature", "timestamp",
    "fakePassword", "passKey",
]

FA = re.compile(rb"\bFA_[A-Z0-9_]{3,60}\b")
ASCII = re.compile(rb"[\x20-\x7e]{4,}")

data = open(BIN, "rb").read()
runs = [m.group().decode("ascii") for m in ASCII.finditer(data)]

counts = collections.Counter()
for run in runs:
    for literal in LITERALS:
        if literal in run:
            counts[literal] += 1

fa = collections.Counter(m.group().decode("ascii") for m in FA.finditer(data))

report = {
    "target": {"path": BIN, "sha256": "a2cc47c5efb74524dd82e447204d0cc9587a971f9cda9552187eee19abb37c5b"},
    "auth_literal_occurrences": dict(counts.most_common()),
    "fa_error_codes": dict(sorted(fa.items())),
    "fa_error_code_count": len(fa),
    "scope": ("ASCII string literals in the shipped executable; presence and count only, "
              "no control flow, no credential values"),
}
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("auth literal occurrences:")
for k, v in counts.most_common():
    print(f"  {k:34} {v}")
print(f"\nFA_* codes: {len(fa)}")
for k in sorted(fa):
    print("  ", k)
print("\nwritten:", OUT)
