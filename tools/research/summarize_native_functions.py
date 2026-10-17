"""Summarize REA `function` / `decompile` CLI outputs without dumping pseudocode.

Reads analysis/rea-native/*.json and reports, per function:
  - identity (name/address/body size)
  - counts of protocol/security keywords found in the pseudocode
  - up to N short excerpt lines containing the most interesting keywords
This keeps the analyst's context small while preserving pointers to the raw file.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from collections import Counter

WS = r"D:\workspace\redocoreemail"
DIR = os.path.join(WS, "analysis", "rea-native")
EXCERPT_LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 6

KEYWORDS = [
    "device", "Device", "nonce", "Nonce", "signature", "Signature", "accessToken",
    "accessSecret", "accessNonce", "getPasswordKey", "checkPasswordEncryptType",
    "triggerSecondAuth", "getTempSession", "verifyCode", "tempSid", "vc_uid",
    "FA_", "S_OK", "sid", "mboxa", "composeId", "attachmentId", "offset",
    "Authorization", "Content-Type", "x-cm", "X-CM", "json", "http", "https",
]


def load(path):
    raw = open(path, encoding="utf-8", errors="replace").read().strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(raw[start:end + 1])
        except ValueError:
            return None


def pseudocode(envelope):
    data = envelope.get("normalized_result")
    if isinstance(data, dict):
        for key in ("pseudocode", "decompiled", "code", "text"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        # function dossier shape
        proc = data.get("procedure")
        if isinstance(proc, dict):
            for key in ("pseudocode", "decompiled", "text"):
                value = proc.get(key)
                if isinstance(value, str) and value:
                    return value
    result = envelope.get("result")
    if isinstance(result, dict):
        return pseudocode({"normalized_result": result})
    return ""


def main():
    files = sorted(glob.glob(os.path.join(DIR, "*.json")))
    if not files:
        print("no native function outputs yet:", DIR)
        return
    for path in files:
        envelope = load(path)
        name = os.path.basename(path)
        if not envelope:
            print(f"{name}: unreadable/empty")
            continue
        if envelope.get("ok") is False:
            err = envelope.get("error") or {}
            print(f"{name}: ERROR {err.get('code')} {(err.get('message') or '')[:120]}")
            continue
        data = envelope.get("normalized_result") or {}
        proc = data.get("procedure") if isinstance(data.get("procedure"), dict) else data
        ident = proc.get("address") or proc.get("entry") or "?"
        body = proc.get("body") or {}
        size = body.get("total_bytes") if isinstance(body, dict) else None
        code = pseudocode(envelope)
        counts = Counter({k: code.count(k) for k in KEYWORDS if code.count(k)})
        print(f"\n=== {name}  addr={ident}  body_bytes={size}  pseudocode_chars={len(code)}")
        if counts:
            print("   keywords:", ", ".join(f"{k}×{v}" for k, v in counts.most_common(12)))
        shown = 0
        for line in code.splitlines():
            if shown >= EXCERPT_LIMIT:
                break
            if any(k in line for k in ("device", "nonce", "signature", "accessToken", "FA_", "getPasswordKey")):
                print("   |", line.strip()[:200])
                shown += 1
        calls = data.get("calls") or proc.get("calls")
        if isinstance(calls, list) and calls:
            print(f"   calls: {len(calls)} (first: {', '.join(str(c.get('name') or c.get('address')) for c in calls[:8])})")


if __name__ == "__main__":
    main()
