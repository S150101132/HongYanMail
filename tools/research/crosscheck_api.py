"""Cross-checks for the client<->server interface inventory.

1. union of func= names actually observed in every runtime log
2. static names never observed / observed names missing from static set
3. evidence for the non-HTTP adapters (EWS, IMAP/SMTP/POP3) and extra paths
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter

LOGS = r"C:\Users\wangbiao\AppData\Roaming\Cm\CMClient\logs"
NATIVE = r"C:\Program Files\cmclient\CMClient.exe"
INV = r"D:\workspace\redocoreemail\analysis\server-api-inventory.json"

IDENT = r"[A-Za-z][A-Za-z0-9_]*\.?[A-Za-z0-9_]*:[A-Za-z][A-Za-z0-9_]*"
RE_FUNC = re.compile(r"func=(" + IDENT + r")")

observed = Counter()
for name in os.listdir(LOGS):
    p = os.path.join(LOGS, name)
    if not os.path.isfile(p):
        continue
    with open(p, "rb") as fh:
        txt = fh.read().decode("utf-8", "ignore")
    for m in RE_FUNC.finditer(txt):
        observed[m.group(1)] += 1

inv = json.load(open(INV, encoding="utf-8"))
static = set(inv["funcs"])
scripted = {k for k, v in inv["funcs"].items() if any(s.startswith("native") for s in v)}

print("observed func names (all logs):", len(observed), " occurrences:", sum(observed.values()))
print("top-20 observed:")
for k, c in observed.most_common(20):
    print("   %-38s %6d   static=%s" % (k, c, "yes" if k in static else "NO"))

print("\nobserved but absent from static inventory:", sorted(set(observed) - static))
print("static Coremail-namespace names never observed in logs:",
      len([k for k in scripted if k not in observed]))

# --- non-HTTP adapters -----------------------------------------------------
data = open(NATIVE, "rb").read()
runs = [m.group().decode("ascii") for m in re.finditer(rb"[\x20-\x7e]{5,}", data)]


def show(title, pattern, limit=18):
    rx = re.compile(pattern)
    hits = sorted({s for s in runs if rx.search(s)})
    print("\n### %s  (%d distinct)" % (title, len(hits)))
    for s in hits[:limit]:
        print("   ", s[:140])


show("EWS / Exchange", r"(?i)(EWS|Exchange\.asmx|ExchangeImpersonation|Autodiscover|OutlookService)")
show("IMAP/POP3/SMTP", r"(?i)^(IMAP|POP3|SMTP|XOAUTH|\* OK|\+OK|EHLO|AUTH )", 30)
show("protocol implementation files", r"^protocol_[a-z0-9_]+\.cpp$", 60)
show("coremail http paths", r"^/coremail/[A-Za-z0-9_\-/\.]+$", 60)

json.dump({"observed": observed, "observed_total": sum(observed.values())},
          open(r"D:\workspace\redocoreemail\analysis\runtime-observed-api.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("\nwritten runtime-observed-api.json")
