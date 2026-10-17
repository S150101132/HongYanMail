"""Produce the curated client<->server interface inventory.

Classification rule (evidence based):
  * Coremail RPC  = identifier whose module is a Coremail namespace (mbox, user,
    cal, pab, nf, xnf, oab, upload, global, cert, site, sms, pub, lp, cim.*)
    and that occurs as a standalone literal in CMClient.exe or in a server URL.
  * observed      = the same identifier also occurs as `func=<id>` in runtime logs.
  * web-only      = only present in the public webmail bundles (not the desktop client).
Excluded: EWS SOAP element/QName prefixes (t:, m:, item:, folder:, message:,
persona:, contacts:, calendar:, default:), iCalendar keywords (BEGIN:, END:,
METHOD:, ACTION:, DESCRIPTION:), and short obfuscated blobs.
"""
from __future__ import annotations

import collections
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
A = os.path.join(HERE, "..", "analysis")

inv = json.load(open(os.path.join(A, "server-api-inventory.json"), encoding="utf-8"))
obs = json.load(open(os.path.join(A, "runtime-observed-api.json"), encoding="utf-8"))["observed"]

NOISE = set("t m item folder message persona contacts calendar default BEGIN END METHOD ACTION "
            "DESCRIPTION transport connectivity recovery std xlink about blob data P D F c f n "
            "save pub".split())
NOISE |= {"B.D", "D6F", "D8Y", "D8a", "H0H", "H9wPu", "I9", "L9", "L9q0t", "LXN", "P5R", "PpRXT",
          "Rh", "T", "TCQC9D", "U", "V", "W", "YWQa", "Z", "c0i", "d", "fWjGjwj", "g", "hJ9h", "j",
          "l", "o6q8q9q", "p", "q", "u", "u4v8v", "uo9", "w", "xrJ", "A7", "F", "tA8_", "tN",
          "tQD8c", "uW"}
# `action:*` are values of an nf/upload request field, not RPC names.
PARAMS_ONLY = {"action:createfolder", "action:delete", "action:movetofolder"}

rpc = {}
for name, srcs in inv["funcs"].items():
    if name in PARAMS_ONLY:
        continue
    mod = name.split(":", 1)[0]
    if mod in NOISE:
        continue
    native = any(s.startswith("native") for s in srcs)
    web = [s for s in srcs if s.startswith("public")]
    rpc[name] = {
        "module": mod,
        "method": name.split(":", 1)[1],
        "in_client_binary": native,
        "public_web_client": bool(web),
        "observed_calls": obs.get(name, 0),
    }

by_mod = collections.defaultdict(list)
for name, rec in rpc.items():
    by_mod[rec["module"]].append(rec)

total = len(rpc)
observed = [n for n, r in rpc.items() if r["observed_calls"]]
native_only = [n for n, r in rpc.items() if r["in_client_binary"] and not r["observed_calls"]]
web_only = [n for n, r in rpc.items() if not r["in_client_binary"]]

summary = {
    "rpc_total": total,
    "modules_total": len(by_mod),
    "observed_in_runtime_logs": len(observed),
    "supported_not_observed": len(native_only),
    "web_client_only": len(web_only),
    "observed_call_occurrences": sum(r["observed_calls"] for r in rpc.values()),
}
json.dump({"summary": summary, "by_module": {m: sorted(v, key=lambda r: r["method"]) for m, v in sorted(by_mod.items())}},
          open(os.path.join(A, "server-api-final.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("SUMMARY", json.dumps(summary, ensure_ascii=False))
print()
for mod in sorted(by_mod, key=lambda m: -len(by_mod[m])):
    recs = sorted(by_mod[mod], key=lambda r: r["method"])
    print(f"### {mod}  ({len(recs)})")
    cells = []
    for r in recs:
        star = "*" if r["observed_calls"] else ""
        cells.append(f"`{r['method']}`{star}")
    print("| " + " | ".join(cells) + " |")
    print()
print("OBSERVED(count)")
for n, c in sorted(obs.items(), key=lambda kv: -kv[1]):
    if n in rpc:
        print(f"{n}\t{c}")
