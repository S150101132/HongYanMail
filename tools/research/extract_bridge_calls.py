"""Runtime inventory of web->native bridge calls, from the CEF logs.

The pages log every bridge call as `callPC--> <command> {args}`. This yields the
commands the UI actually invokes (as opposed to the ones merely present in the
shipped scripts), plus the argument key names for each command.
"""
from __future__ import annotations

import collections
import json
import os
import re

LOGS = r"C:\Users\wangbiao\AppData\Roaming\Cm\CMClient\logs"
OUT = r"D:\workspace\redocoreemail\analysis\bridge-calls-runtime.json"

RE_CALL = re.compile(r"callPC-->\s*([A-Za-z][A-Za-z0-9_]{0,40})\s*(\{.*)?")
RE_KEY = re.compile(r'"([A-Za-z_][A-Za-z0-9_]{0,40})"\s*:')

commands = collections.Counter()
arg_keys = collections.defaultdict(collections.Counter)
with_args = collections.Counter()

for name in sorted(os.listdir(LOGS)):
    if not name.lower().startswith("cmcef"):
        continue
    path = os.path.join(LOGS, name)
    if not os.path.isfile(path):
        continue
    text = open(path, "rb").read().decode("utf-8", "ignore")
    for line in text.splitlines():
        match = RE_CALL.search(line)
        if not match:
            continue
        command = match.group(1)
        commands[command] += 1
        args = match.group(2) or ""
        if args:
            with_args[command] += 1
            for key in RE_KEY.findall(args[:400]):
                arg_keys[command][key] += 1

report = {
    "scope": "cmcef.log* bridge call lines; command names and argument key names only",
    "distinct_commands": len(commands),
    "total_calls": sum(commands.values()),
    "commands": {
        cmd: {"calls": count,
              "with_args": with_args[cmd],
              "arg_keys": dict(arg_keys[cmd].most_common(12))}
        for cmd, count in commands.most_common()
    },
}
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print(f"distinct={len(commands)} total={sum(commands.values())}")
for cmd, count in commands.most_common(45):
    keys = ",".join(list(arg_keys[cmd])[:6])
    print(f"  {count:6}  {cmd:28} args[{keys}]")
print("\nwritten:", OUT)
