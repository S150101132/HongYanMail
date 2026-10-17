"""Cluster bare `module:method` literals in CMClient.exe by string-table locality.

Coremail's engine keeps its JSON-RPC names as standalone literals. Grouping them
by physical proximity separates the RPC table from unrelated `a:b`-shaped data
(EWS XML element names, iCalendar keywords, obfuscated blobs).
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict

PATH = sys.argv[1] if len(sys.argv) > 1 else r"C:\Program Files\cmclient\CMClient.exe"
OUT = sys.argv[2] if len(sys.argv) > 2 else r"D:\workspace\redocoreemail\analysis\native-api-clusters.json"
GAP = int(sys.argv[3]) if len(sys.argv) > 3 else 256

ASCII_RUN = re.compile(rb"[\x20-\x7e]{5,}")
IDENT = re.compile(r"^([a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*):([A-Za-z][A-Za-z0-9_]*)$")

with open(PATH, "rb") as fh:
    data = fh.read()

hits = []
for m in ASCII_RUN.finditer(data):
    s = m.group().decode("ascii")
    im = IDENT.match(s)
    if im:
        hits.append((m.start(), s, im.group(1), im.group(3)))

hits.sort()
clusters = []
cur = []
for off, s, _mod, _meth in hits:
    if cur and off - cur[-1][0] > GAP:
        clusters.append(cur)
        cur = []
    cur.append((off, s))
if cur:
    clusters.append(cur)

report = []
for c in clusters:
    mods = Counter(s.split(":")[0] for _o, s in c)
    report.append({
        "start": c[0][0],
        "end": c[-1][0],
        "size": len(c),
        "modules": dict(mods.most_common()),
        "names": [s for _o, s in c],
    })
report.sort(key=lambda r: -r["size"])

with open(OUT, "w", encoding="utf-8") as fh:
    json.dump({"target": PATH, "gap": GAP, "count": len(report), "clusters": report},
              fh, ensure_ascii=False, indent=2)

print(f"clusters={len(report)} hits={len(hits)}")
for r in report[:25]:
    print(f"\n@0x{r['start']:x}-0x{r['end']:x} n={r['size']} {r['modules']}")
    print("   ", ", ".join(r["names"][:24]) + (" ..." if r["size"] > 24 else ""))
print("\nwritten:", OUT)
