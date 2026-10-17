"""Completeness sweep for URL/path literals in the client binary.

The endpoint table in 02 was built from targeted patterns (`/coremail/`, `.jsp`).
This sweep looks for any other web-looking literal so that missing endpoints are
found by evidence rather than by assumption.
"""
from __future__ import annotations

import collections
import json
import re

BIN = r"C:\Program Files\cmclient\CMClient.exe"
OUT = r"D:\workspace\redocoreemail\analysis\endpoint-sweep.json"

ASCII = re.compile(rb"[\x20-\x7e]{6,}")
EXTLESS_HOST = re.compile(r"^https?://[A-Za-z0-9.\-]+(:[0-9]+)?(/|$)")

CANDIDATES = [
    ("jsp", re.compile(r"\.jsp\b")),
    ("do_or_action", re.compile(r"\.(do|action)\b")),
    ("php", re.compile(r"\.php\b")),
    ("json_path", re.compile(r"/[\w\-./]*(json|api)[\w\-./]*", re.I)),
    ("xt_versioned", re.compile(r"/XT[0-9]?/")),
    ("coremail_path", re.compile(r"/coremail[\w\-./]*")),
    ("client_path", re.compile(r"/coremailclient[\w\-./]*")),
    ("adm_api", re.compile(r"/adm_api[\w\-./]*")),
    ("lunkr", re.compile(r"lunkr[\w\-./]*", re.I)),
    ("absolute_url", re.compile(r"https?://[\w\-.:]+(?:/[\w\-./?=&%]*)?")),
    ("ws_url", re.compile(r"(wss?)://[\w\-.:]+(?:/[\w\-./?=&%]*)?")),
]

data = open(BIN, "rb").read()
runs = [m.group().decode("ascii") for m in ASCII.finditer(data)]

found = collections.defaultdict(set)
for run in runs:
    for label, regex in CANDIDATES:
        for match in regex.finditer(run):
            value = match.group(0)
            if len(value) <= 160:
                found[label].add(value)

report = {"scope": "ASCII literals in CMClient.exe matching URL/path patterns",
          "target_sha256": "a2cc47c5efb74524dd82e447204d0cc9587a971f9cda9552187eee19abb37c5b"}
for label, _ in CANDIDATES:
    values = sorted(found[label])
    report[label] = values
    print(f"\n### {label} ({len(values)})")
    for value in values[:40]:
        print("   ", value)
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("\nwritten:", OUT)
