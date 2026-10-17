"""Read-only header inspection of the client's local account storage.

Only the first bytes and any short readable strings in the first 4 KiB are read;
no database content, no decryption, no modification.
"""
from __future__ import annotations

import json
import os
import re

ACCOUNT = r"D:\work\email\wangbiao@inspur.com"
ROAMING = r"C:\Users\wangbiao\AppData\Roaming\Cm\CMClient"
OUT = r"D:\workspace\redocoreemail\analysis\local-store-headers.json"

SQLITE_MAGIC = b"SQLite format 3\x00"
PRINTABLE = re.compile(rb"[\x20-\x7e]{6,}")

targets = [
    os.path.join(ACCOUNT, "mail.edbx2"),
    os.path.join(ACCOUNT, "address.edbx2"),
    os.path.join(ACCOUNT, "calendar.edbx2"),
    os.path.join(ACCOUNT, "setting.edbx2"),
    os.path.join(ACCOUNT, "security.sdat"),
    os.path.join(ACCOUNT, "account.sdat"),
    os.path.join(ACCOUNT, "cache.dat"),
    os.path.join(ROAMING, "mail"),
]

report = {"scope": "first 4096 bytes of each local store file; read-only; no content extraction"}
entries = []
for path in targets:
    if os.path.isdir(path):
        for name in os.listdir(path):
            entries.append(os.path.join(path, name))
        continue
    if os.path.exists(path):
        entries.append(path)

results = []
for path in entries:
    if not os.path.isfile(path):
        continue
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(4096)
    strings = [s.decode("ascii") for s in PRINTABLE.findall(head[:512])][:6]
    results.append({
        "path": os.path.relpath(path, os.path.dirname(ACCOUNT)) if ACCOUNT in path else os.path.basename(path),
        "size": size,
        "first16_hex": head[:16].hex(),
        "sqlite_header": head.startswith(SQLITE_MAGIC),
        "leading_strings": strings,
    })

report["files"] = results
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

for item in results:
    print(f"{item['path']:28} size={item['size']:>12,} sqlite={item['sqlite_header']} head={item['first16_hex']}")
    if item["leading_strings"]:
        print(f"{'':28} strings: {item['leading_strings']}")
print("\nwritten:", OUT)
