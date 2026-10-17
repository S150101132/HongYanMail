"""Extract internal protocol-layer naming from CMClient.exe string literals.

Targets:
  1. CoremailEngine internal operation names shaped like `A->B`
     (seen in REA trace: `_uploadToFolder->mbox:listMessages`, `mbox:listMessages->HandleResult`)
  2. CoremailEngine / engine namespace class names recovered from MSVC RTTI strings
  3. protocol implementation unit names

Read-only; no execution, no server access.
"""
from __future__ import annotations

import collections
import json
import re

BIN = r"C:\Program Files\cmclient\CMClient.exe"
OUT = r"D:\workspace\redocoreemail\analysis\native-internal-names.json"

ASCII = re.compile(rb"[\x20-\x7e]{5,}")
ARROW = re.compile(r"^_?[A-Za-z][A-Za-z0-9_]*(?:->[A-Za-z0-9_:]+)+$")
RTTI = re.compile(r"\.\?AV([A-Za-z_][A-Za-z0-9_@?$]*?)@@")
PROTO = re.compile(r"protocol_[a-z0-9_]+\.cpp")

data = open(BIN, "rb").read()
runs = [m.group().decode("ascii") for m in ASCII.finditer(data)]

arrows = collections.Counter(r for r in runs if ARROW.match(r))
proto = sorted({m for r in runs for m in PROTO.findall(r)})

# RTTI: keep the leading class name of the mangled type
classes = collections.Counter()
for r in runs:
    for m in RTTI.finditer(r):
        name = m.group(1).split("@")[0]
        name = re.sub(r"^\?\$?[A-Za-z_]*", "", name)
        if len(name) > 2 and re.match(r"^[A-Za-z_]", name):
            classes[name] += 1

report = {
    "target_sha256": "a2cc47c5efb74524dd82e447204d0cc9587a971f9cda9552187eee19abb37c5b",
    "scope": "ASCII string literals only; counts are literal occurrences, not call counts",
    "internal_arrow_names": dict(sorted(arrows.items())),
    "internal_arrow_name_count": len(arrows),
    "protocol_units": proto,
    "rtti_class_names": dict(classes.most_common(400)),
    "rtti_class_name_count": len(classes),
}
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("arrow names:", len(arrows))
for name, count in arrows.most_common(40):
    print(f"  {count:4}  {name}")
print("\nprotocol units:", len(proto))
print("\nRTTI class names (top 40 of %d):" % len(classes))
for name, count in classes.most_common(40):
    print(f"  {count:4}  {name}")
print("\nwritten:", OUT)
