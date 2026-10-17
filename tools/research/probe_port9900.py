"""Characterize the non-443 traffic found in the client logs.

Only structure is reported: the text immediately before `port(9900)`, the shape
of the request body (length + first bytes in hex), and counts. No payload is
reproduced and long tokens are redacted.
"""
from __future__ import annotations

import collections
import json
import os
import re

LOGS = r"C:\Users\wangbiao\AppData\Roaming\Cm\CMClient\logs"
OUT = r"D:\workspace\redocoreemail\analysis\port-9900-probe.json"

RE_PORT9900 = re.compile(r"port\(9900\)")
RE_TOK = re.compile(r"[A-Za-z0-9+/=_\-]{16,}")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

prefixes = collections.Counter()
body_heads = collections.Counter()
body_lengths = collections.Counter()
total = 0

for name in sorted(os.listdir(LOGS)):
    path = os.path.join(LOGS, name)
    if not os.path.isfile(path):
        continue
    text = open(path, "rb").read().decode("utf-8", "ignore")
    for m in RE_PORT9900.finditer(text):
        total += 1
        before = text[max(0, m.start() - 260):m.start()]
        before = RE_EMAIL.sub("<email>", before)
        before = RE_TOK.sub("<tok>", before)
        prefixes[before[-150:]] += 1

        after = text[m.end():m.end() + 4000]
        marker = after.find("body(")
        if marker >= 0:
            body = after[marker + 5:marker + 5 + 64]
            body_heads[body[:24].encode("unicode_escape").decode("ascii")] += 1
        length = re.search(r"Content-Length[:=]\s*(\d+)", after[:400])
        if length:
            body_lengths[length.group(1)] += 1

report = {
    "scope": "lines mentioning port(9900); redacted structure only",
    "occurrences": total,
    "prefix_shapes": [{"prefix": p, "count": c} for p, c in prefixes.most_common(6)],
    "body_head_hexlike": [{"head": h, "count": c} for h, c in body_heads.most_common(6)],
    "content_lengths": dict(body_lengths.most_common(10)),
}
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("occurrences:", total)
print("\n--- context before port(9900) (redacted) ---")
for p, c in prefixes.most_common(6):
    print(f"{c:5}  ...{p!r}")
print("\n--- first bytes of body (escaped) ---")
for h, c in body_heads.most_common(6):
    print(f"{c:5}  {h}")
print("\nContent-Length values:", dict(body_lengths.most_common(10)))
print("\nwritten:", OUT)
