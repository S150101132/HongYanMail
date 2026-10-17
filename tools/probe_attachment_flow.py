"""Attachment / recall / draft evidence probe over the native logs.

P0-6 asks where the `viewDownloadFile.jsp?key=` value comes from. This probe
looks for the download, recall and draft operations in the runtime logs and
reports the redacted shape of the lines around them, so the source of the key
can be identified from evidence instead of guessed.
"""
from __future__ import annotations

import collections
import json
import os
import re

LOGS = r"C:\Users\wangbiao\AppData\Roaming\Cm\CMClient\logs"
OUT = r"D:\workspace\redocoreemail\analysis\attachment-recall-draft-evidence.json"

PATTERNS = {
    "viewDownloadFile": re.compile(r"viewDownloadFile"),
    "download_key": re.compile(r"(?i)\b(?:dl)?key\s*[:=]"),
    "attachment_id": re.compile(r"(?i)attachmentId|attachId|\bfileid\b|\bfileId\b"),
    "upload_prepare": re.compile(r"upload:prepare|upload:directData"),
    "recall": re.compile(r"(?i)recall"),
    "draft": re.compile(r"(?i)saveDraft|draftId|draft_id"),
    "compose_session": re.compile(r"(?i)composeId|compose_id"),
}

RE_TOK = re.compile(r"[A-Za-z0-9+/=_\-]{20,}")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

counts = collections.Counter()
shapes = collections.defaultdict(collections.Counter)
contexts = collections.defaultdict(list)
files = 0

for name in sorted(os.listdir(LOGS)):
    if not name.startswith("data.log"):
        continue
    path = os.path.join(LOGS, name)
    if not os.path.isfile(path):
        continue
    files += 1
    lines = open(path, "rb").read().decode("utf-8", "ignore").splitlines()
    for index, line in enumerate(lines):
        for label, regex in PATTERNS.items():
            if not regex.search(line):
                continue
            counts[label] += 1
            cleaned = RE_EMAIL.sub("<email>", line)
            cleaned = RE_TOK.sub("<tok>", cleaned)
            shape = re.sub(r"\d+", "N", cleaned)[:170]
            shapes[label][shape] += 1
            if len(contexts[label]) < 6:
                contexts[label].append({
                    "file": name,
                    "line": index,
                    "this": shape,
                    "prev": re.sub(r"\d+", "N", RE_TOK.sub("<tok>", lines[index - 1]))[:150] if index else "",
                })

report = {
    "scope": "data.log* attachment/recall/draft keyword evidence; redacted shapes only",
    "files_scanned": files,
    "counts": dict(counts.most_common()),
    "shapes": {label: [{"shape": s, "count": c} for s, c in counter.most_common(5)]
               for label, counter in shapes.items()},
    "contexts": contexts,
}
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print(f"files={files}")
print("counts:", dict(counts.most_common()))
for label in PATTERNS:
    if label not in shapes or label not in ("viewDownloadFile", "download_key", "upload_prepare", "recall", "draft"):
        continue
    print(f"\n=== {label} ===")
    for s, c in shapes[label].most_common(3):
        print(f"{c:5}  {s}")
print("\nwritten:", OUT)
