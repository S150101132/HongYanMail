"""Extract the server capability flags the client receives after login.

The response carries `var.$ajc.app` with boolean capability flags (supportIMAP,
supportPushmail, supportRecallMail, ...). These are server capabilities, not
secrets, so their boolean values are recorded. No mailbox content, address or
token is read.
"""
from __future__ import annotations

import collections
import json
import os
import re

LOGS = r"C:\Users\wangbiao\AppData\Roaming\Cm\CMClient\logs"
OUT = r"D:\workspace\redocoreemail\analysis\server-capabilities.json"

RE_DATE = re.compile(r"!!date\s*'[^']*'")
DECODER = json.JSONDecoder()

flags = collections.Counter()
flag_values = collections.defaultdict(collections.Counter)
scalar_keys = ["primaryDomain", "licenseVersion", "cookieName", "XTFamily"]
scalars = collections.defaultdict(collections.Counter)
samples = 0
files = 0

for name in sorted(os.listdir(LOGS)):
    path = os.path.join(LOGS, name)
    if not os.path.isfile(path):
        continue
    files += 1
    text = open(path, "rb").read().decode("utf-8", "ignore")
    for line in text.splitlines():
        if '"$ajc"' not in line:
            continue
        for pos in [m.start() for m in re.finditer(r'\{"code"', line)]:
            window = RE_DATE.sub('"D"', line[pos:])
            try:
                value, _ = DECODER.raw_decode(window, 0)
            except ValueError:
                continue
            var = value.get("var") if isinstance(value, dict) else None
            app = var.get("$ajc", {}).get("app") if isinstance(var, dict) else None
            if not isinstance(app, dict):
                continue
            samples += 1
            for key, val in app.items():
                if isinstance(val, bool):
                    flags[key] += 1
                    flag_values[key][str(val)] += 1
                elif key in scalar_keys:
                    scalars[key][str(val)[:60]] += 1

summary = {
    key: dict(flag_values[key])
    for key in sorted(flag_values)
}
report = {
    "scope": ("server capability flags observed in post-login responses ($ajc.app); "
              "boolean values only; no mailbox content, addresses or tokens"),
    "files_scanned": files,
    "samples": samples,
    "capability_flags": summary,
    "scalars": {k: dict(v) for k, v in scalars.items()},
}
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print(f"samples={samples} flags={len(summary)}")
for key in sorted(summary):
    print(f"  {key:42} {summary[key]}")
for key, values in scalars.items():
    print(f"  {key:42} {dict(values)}")
print("\nwritten:", OUT)
