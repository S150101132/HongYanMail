"""Two small evidence checks that do not need the disassembler.

1. Transport details from request lines: port, proxy type, and the shape of the
   ssl(...) field (length / charset / distinct count only - never the value).
2. The process-internal bridge command inventory (callPC), which must NOT be
   confused with server interfaces.
"""
from __future__ import annotations

import collections
import json
import os
import re

LOGS = r"C:\Users\wangbiao\AppData\Roaming\Cm\CMClient\logs"
WS = r"D:\workspace\redocoreemail"
OUT = os.path.join(WS, "analysis", "transport-and-bridge.json")

RE_TRANSPORT = re.compile(
    r"port\((\d+)\), proxy\(type:(\d+) host:([^)]*?) ip:\(([^)]*)\)\), ssl\(([^)]*)\)")
RE_CALLPC = re.compile(r"""callPC\s*\(\s*['"]([A-Za-z0-9_]+)['"]""")
RE_CALLPC_ALT = re.compile(r"""["'](?:funcName|func|cmd)["']\s*:\s*["']([A-Za-z0-9_]+)["']""")

ports = collections.Counter()
proxies = collections.Counter()
ssl_lengths = collections.Counter()
ssl_hex = collections.Counter()
ssl_values = set()

for name in sorted(os.listdir(LOGS)):
    path = os.path.join(LOGS, name)
    if not os.path.isfile(path):
        continue
    text = open(path, "rb").read().decode("utf-8", "ignore")
    for m in RE_TRANSPORT.finditer(text):
        port, ptype, host, ip, ssl = m.groups()
        ports[port] += 1
        proxies[f"type={ptype} host={'set' if host else 'empty'} ip={'set' if ip and ip != '0' else 'empty'}"] += 1
        ssl_lengths[len(ssl)] += 1
        ssl_hex["hex" if re.fullmatch(r"[0-9a-fA-F]*", ssl or "") and ssl else "other"] += 1
        if len(ssl_values) < 5000:
            ssl_values.add(ssl)

# bridge commands from the installed web resources
bridge = collections.Counter()
app = r"C:\Program Files\cmclient\app"
for base, _dirs, names in os.walk(app):
    for n in names:
        if not n.lower().endswith((".js", ".html")):
            continue
        p = os.path.join(base, n)
        try:
            text = open(p, "rb").read().decode("utf-8", "ignore")
        except OSError:
            continue
        for rx in (RE_CALLPC, RE_CALLPC_ALT):
            for cmd in rx.findall(text):
                bridge[cmd] += 1

report = {
    "transport": {
        "ports": dict(ports.most_common()),
        "proxy_shapes": dict(proxies.most_common()),
        "ssl_field_lengths": dict(ssl_lengths.most_common()),
        "ssl_field_charset": dict(ssl_hex),
        "ssl_distinct_values": len(ssl_values),
        "note": "ssl(...) values are never written out; only length/charset/distinct counts",
    },
    "bridge_commands": {
        "distinct": len(bridge),
        "occurrences": dict(bridge.most_common()),
        "warning": "process-internal callPC commands, NOT server interfaces",
    },
}
json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("ports:", dict(ports.most_common(5)))
print("proxy:", dict(proxies.most_common()))
print("ssl field lengths:", dict(ssl_lengths.most_common(5)), "charset:", dict(ssl_hex), "distinct:", len(ssl_values))
print(f"\nbridge commands distinct={len(bridge)}")
for cmd, count in bridge.most_common(25):
    print(f"  {count:4}  {cmd}")
print("\nwritten:", OUT)
