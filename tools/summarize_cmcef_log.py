"""Structural summary of the CEF (web layer) logs, which have not been examined yet.

Only log tags, counts and redacted line shapes are reported; no page content,
addresses, tokens or user data.
"""
from __future__ import annotations

import collections
import json
import os
import re

LOGS = r"C:\Users\wangbiao\AppData\Roaming\Cm\CMClient\logs"
OUT = r"D:\workspace\redocoreemail\analysis\cmcef-log-summary.json"

RE_TAG = re.compile(r"\[([A-Za-z0-9_.\- ]{1,40}):(Info|Warning|Error|Debug|Notice|Trace)\]")
RE_TOK = re.compile(r"[A-Za-z0-9+/=_\-]{16,}")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
RE_LEVEL = re.compile(r"\[[^\]]*:(Info|Warning|Error|Debug|Notice|Trace)\]")

tags = collections.Counter()
levels = collections.Counter()
shapes = collections.Counter()
bridge_mentions = collections.Counter()
files = 0
lines_total = 0

KEYWORDS = ["callPC", "CoreMail", "window.CoreMail", "postMessage", "onError", "Uncaught",
            "net::", "ERR_", "Failed to load", "blocked", "CORS", "mixed content",
            "javascript", "TypeError", "SyntaxError"]

for name in sorted(os.listdir(LOGS)):
    if not name.lower().startswith("cmcef"):
        continue
    path = os.path.join(LOGS, name)
    if not os.path.isfile(path):
        continue
    files += 1
    text = open(path, "rb").read().decode("utf-8", "ignore")
    for line in text.splitlines():
        lines_total += 1
        for tag, level in RE_TAG.findall(line):
            tags[tag] += 1
            levels[level] += 1
        cleaned = RE_EMAIL.sub("<email>", line)
        cleaned = RE_TOK.sub("<tok>", cleaned)
        shape = re.sub(r"\d+", "N", cleaned)[:150]
        shapes[shape] += 1
        for keyword in KEYWORDS:
            if keyword in line:
                bridge_mentions[keyword] += 1

report = {
    "scope": "cmcef.log* only; tags, levels, redacted line shapes, keyword counts",
    "files": files,
    "lines": lines_total,
    "tags": dict(tags.most_common(30)),
    "levels": dict(levels.most_common()),
    "keyword_mentions": dict(bridge_mentions.most_common()),
    "top_shapes": [{"shape": s, "count": c} for s, c in shapes.most_common(20)],
}
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print(f"files={files} lines={lines_total}")
print("levels:", dict(levels.most_common()))
print("\ntags:", dict(tags.most_common(15)))
print("\nkeywords:", dict(bridge_mentions.most_common()))
print("\n--- top line shapes ---")
for s, c in shapes.most_common(12):
    print(f"{c:6}  {s}")
print("\nwritten:", OUT)
