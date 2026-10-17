"""Extract the observed response shape of the high-frequency account/attribute calls.

Lines of the form `... prepare fetch : {"code":"S_OK","var":{"$ajc":{...` carry the
getAttrs-style payload. Only key names and value types are kept.
"""
from __future__ import annotations

import collections
import json
import os
import re

LOGS = r"C:\Users\wangbiao\AppData\Roaming\Cm\CMClient\logs"
OUT = r"D:\workspace\redocoreemail\analysis\ajc-response-shapes.json"

RE_DATE = re.compile(r"!!date\s*'[^']*'")
DECODER = json.JSONDecoder()
MAX_KEYS, DEPTH = 80, 8


def sig(value, depth=0):
    if depth > DEPTH:
        return "..."
    if isinstance(value, dict):
        items = sorted(value.items())[:MAX_KEYS]
        more = ",..." if len(value) > MAX_KEYS else ""
        return "{" + ",".join(f"{k}:{sig(v, depth + 1)}" for k, v in items) + more + "}"
    if isinstance(value, list):
        return "[" + (sig(value[0], depth + 1) if value else "") + "]"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if value is None:
        return "null"
    return "str"


shapes = collections.Counter()
keys = collections.Counter()
files = 0

for name in sorted(os.listdir(LOGS)):
    path = os.path.join(LOGS, name)
    if not os.path.isfile(path):
        continue
    files += 1
    text = open(path, "rb").read().decode("utf-8", "ignore")
    for line in text.splitlines():
        if '"$ajc"' not in line and "ajcAttrIds" not in line:
            continue
        for pos in [m.start() for m in re.finditer(r'\{"code"', line)]:
            window = RE_DATE.sub('"D"', line[pos:])
            try:
                value, _ = DECODER.raw_decode(window, 0)
            except ValueError:
                shapes["(line truncated before JSON completed)"] += 1
                continue
            if not isinstance(value, dict):
                continue
            shapes[sig(value)] += 1
            var = value.get("var")
            if isinstance(var, dict):
                for key in var:
                    keys[key] += 1

report = {
    "scope": "log lines carrying $ajc/ajcAttrIds; response key/type shapes only",
    "files_scanned": files,
    "shapes": [{"shape": s, "count": c} for s, c in shapes.most_common(8)],
    "var_top_level_keys": dict(keys.most_common(40)),
}
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

for s, c in shapes.most_common(4):
    print(f"\n[{c}]\n{s[:1200]}")
print("\nvar keys:", dict(keys.most_common(25)))
print("written:", OUT)
