"""Reconstruct the client's observed startup/login call sequence from the logs.

Groups request lines by log thread id and prints the ordered sequence of
`func=` operations per thread for the busiest session, so the real login and
initial-sync order is evidence rather than guesswork.
"""
from __future__ import annotations

import collections
import json
import os
import re

LOGS = r"C:\Users\wangbiao\AppData\Roaming\Cm\CMClient\logs"
OUT = r"D:\workspace\redocoreemail\analysis\login-sequence.json"

IDENT = r"[A-Za-z][A-Za-z0-9_]*\.?[A-Za-z0-9_]*:[A-Za-z][A-Za-z0-9_]*"
RE_LINE = re.compile(r"^T:(\d+)\((\d{2}:\d{2}:\d{2})\)")
RE_FUNC = re.compile(r"func=(" + IDENT + r")")

sequences = {}
for name in sorted(os.listdir(LOGS)):
    if not name.startswith("data.log"):
        continue
    path = os.path.join(LOGS, name)
    if not os.path.isfile(path):
        continue
    per_thread = collections.defaultdict(list)
    for line in open(path, "rb").read().decode("utf-8", "ignore").splitlines():
        meta = RE_LINE.match(line)
        if not meta:
            continue
        func = RE_FUNC.search(line)
        if not func:
            continue
        tid, clock = meta.group(1), meta.group(2)
        if per_thread[tid] and per_thread[tid][-1][1] == func.group(1):
            continue
        per_thread[tid].append((clock, func.group(1)))
    for tid, seq in per_thread.items():
        if len(seq) < 6:
            continue
        key = f"{name}#T{tid}"
        sequences[key] = {
            "file": name,
            "thread": tid,
            "operations": len(seq),
            "first_clock": seq[0][0],
            "sequence": [{"clock": c, "func": f} for c, f in seq[:400]],
        }

ranked = sorted(sequences.items(), key=lambda kv: -kv[1]["operations"])[:5]
report = {"scope": "ordered func= sequences per log thread; no parameters or content",
          "threads_analyzed": len(sequences),
          "top_threads": [{"key": k, **v} for k, v in ranked]}
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print(f"threads with >=6 ops: {len(sequences)}")
for key, value in ranked:
    print(f"\n=== {key}  ops={value['operations']}  start={value['first_clock']}")
    print("   " + " -> ".join(item["func"] for item in value["sequence"][:28]))
print("\nwritten:", OUT)
