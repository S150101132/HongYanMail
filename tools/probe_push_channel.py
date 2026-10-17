"""Look for real push-channel evidence in the native logs.

The server declares supportWSMsgNotify/supportPushmail, and the binary contains
text/event-stream and engine.io polling strings. This probe checks whether the
runtime logs actually show any push activity, and in what shape.
"""
from __future__ import annotations

import collections
import json
import os
import re

LOGS = r"C:\Users\wangbiao\AppData\Roaming\Cm\CMClient\logs"
OUT = r"D:\workspace\redocoreemail\analysis\push-channel-evidence.json"

PATTERNS = {
    "websocket": re.compile(r"(?i)\bwebsocket\b|wss?://|Sec-WebSocket|Upgrade:\s*websocket"),
    "engineio": re.compile(r"EIO=\d|transport=polling|socket\.io|engine\.io", re.I),
    "sse": re.compile(r"text/event-stream|EventSource", re.I),
    "notify": re.compile(r"(?i)\bnotify(?:NewMail|Mail|Message)?\b"),
    "push": re.compile(r"(?i)\bpush(mail|notification|message|channel)?\b"),
    "unread": re.compile(r"(?i)unreadcount|unread_count"),
    "longpoll": re.compile(r"(?i)long[_-]?poll|keepalive|heartbeat"),
    "stream": re.compile(r"(?i)\bstreaming\b|GetStreamingEvents"),
}

RE_TOK = re.compile(r"[A-Za-z0-9+/=_\-]{20,}")
RE_TS = re.compile(r"^T:\d+\(\d{2}:\d{2}:\d{2}\)")

counts = collections.Counter()
files_matched = collections.Counter()
shapes = collections.defaultdict(collections.Counter)
files = 0

for name in sorted(os.listdir(LOGS)):
    if not name.startswith("data.log"):
        continue
    path = os.path.join(LOGS, name)
    if not os.path.isfile(path):
        continue
    files += 1
    text = open(path, "rb").read().decode("utf-8", "ignore")
    for line in text.splitlines():
        for label, regex in PATTERNS.items():
            if regex.search(line):
                counts[label] += 1
                files_matched[label] += 1
                cleaned = RE_TOK.sub("<tok>", line)
                cleaned = re.sub(r"\d+", "N", cleaned)
                shapes[label][cleaned[:150]] += 1

report = {
    "scope": "data.log* keyword evidence for push channels; redacted shapes only",
    "files_scanned": files,
    "counts": dict(counts.most_common()),
    "files_matched": dict(files_matched.most_common()),
    "top_shapes": {label: [{"shape": s, "count": c} for s, c in counter.most_common(5)]
                   for label, counter in shapes.items()},
}
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print(f"files={files}")
print("counts:", dict(counts.most_common()))
print("files matched:", dict(files_matched.most_common()))
for label, counter in shapes.items():
    print(f"\n--- {label} ---")
    for s, c in counter.most_common(3):
        print(f"{c:6}  {s}")
print("\nwritten:", OUT)
