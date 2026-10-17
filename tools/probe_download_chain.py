"""Where does attachment download actually happen?

Native logs show the upload path but contain no `viewDownloadFile` at all, while
the CEF logs show an `attachmentDownload{fileid,...}` bridge call. This probe
searches the CEF logs for the download URL/parameters to locate the real chain.
"""
from __future__ import annotations

import collections
import json
import os
import re

LOGS = r"C:\Users\wangbiao\AppData\Roaming\Cm\CMClient\logs"
OUT = r"D:\workspace\redocoreemail\analysis\download-chain-evidence.json"

PATTERNS = {
    "viewDownloadFile": re.compile(r"viewDownloadFile", re.I),
    "download_word": re.compile(r"(?i)\bdownload"),
    "key_param": re.compile(r"[?&]key="),
    "fileid": re.compile(r"(?i)fileid|file_id"),
    "attachmentDownload": re.compile(r"attachmentDownload"),
    "blob_or_objecturl": re.compile(r"blob:|URL\.createObjectURL", re.I),
    "jsp_any": re.compile(r"\.jsp", re.I),
}
RE_TOK = re.compile(r"[A-Za-z0-9+/=_\-]{20,}")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

counts = collections.Counter()
shapes = collections.defaultdict(collections.Counter)
files_scanned = 0

for name in sorted(os.listdir(LOGS)):
    if not name.lower().startswith("cmcef"):
        continue
    path = os.path.join(LOGS, name)
    if not os.path.isfile(path):
        continue
    files_scanned += 1
    for line in open(path, "rb").read().decode("utf-8", "ignore").splitlines():
        for label, regex in PATTERNS.items():
            if not regex.search(line):
                continue
            counts[label] += 1
            cleaned = RE_EMAIL.sub("<email>", line)
            cleaned = RE_TOK.sub("<tok>", cleaned)
            shapes[label][re.sub(r"\d+", "N", cleaned)[:170]] += 1

report = {
    "scope": "cmcef.log* download-related keyword evidence; redacted shapes only",
    "files_scanned": files_scanned,
    "counts": dict(counts.most_common()),
    "shapes": {label: [{"shape": s, "count": c} for s, c in counter.most_common(6)]
               for label, counter in shapes.items()},
}
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print(f"cmcef files={files_scanned}")
print("counts:", dict(counts.most_common()))
for label in ("viewDownloadFile", "key_param", "attachmentDownload", "jsp_any", "download_word"):
    if label not in shapes:
        print(f"\n=== {label}: 0 命中 ===")
        continue
    print(f"\n=== {label} ===")
    for s, c in shapes[label].most_common(4):
        print(f"{c:5}  {s}")
print("\nwritten:", OUT)
