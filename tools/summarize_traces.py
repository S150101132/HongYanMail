"""Summarize REA trace_feature CLI outputs into a compact code-location table.

Input : analysis/rea-traces/*.json  (one CLI run per interface)
Output: analysis/rea-trace-summary.json  + a short stdout table
"""
from __future__ import annotations

import glob
import json
import os
import re

WS = r"D:\workspace\redocoreemail"
TRACE_DIR = os.path.join(WS, "analysis", "rea-traces")
OUT = os.path.join(WS, "analysis", "rea-trace-summary.json")


def load(path):
    raw = open(path, encoding="utf-8", errors="replace").read().strip()
    if not raw:
        return None
    # CLI prints one JSON envelope; tolerate a trailing progress line
    try:
        return json.loads(raw)
    except ValueError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(raw[start:end + 1])
        except ValueError:
            return None


def summarize(envelope):
    data = envelope.get("normalized_result") or (envelope.get("result") or {}).get("normalized_result")
    if not isinstance(data, dict):
        return None
    matches = [{"address": m.get("address"), "value": m.get("value")}
               for m in data.get("matches", []) if m.get("type") == "string"]
    procs = {}
    for ref in data.get("references", []):
        containing = (ref.get("containing_procedure") or {}).get("procedure") or {}
        address = containing.get("address")
        if not address:
            continue
        entry = procs.setdefault(address, {"address": address, "name": containing.get("name"),
                                           "strings": set()})
        entry["strings"].add(ref.get("target_address"))
    for entry in procs.values():
        entry["strings"] = sorted(s for s in entry["strings"] if s)
    return {"query": data.get("query"), "string_matches": matches, "referencing_functions": list(procs.values())}


def main():
    results = {}
    for path in sorted(glob.glob(os.path.join(TRACE_DIR, "*.json"))):
        name = os.path.basename(path)[:-5]
        envelope = load(path)
        if not envelope:
            continue
        if envelope.get("ok") is False:
            err = envelope.get("error") or {}
            results[name] = {"error": err.get("code"), "message": (err.get("message") or "")[:200]}
            continue
        summary = summarize(envelope)
        if summary:
            results[name] = summary
    json.dump(results, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"traces summarized: {len(results)}")
    for name, value in sorted(results.items()):
        if "error" in value:
            print(f"  {name:28} ERROR {value['error']}")
            continue
        funcs = ", ".join(f"{f['name']}@{f['address']}" for f in value["referencing_functions"]) or "-"
        addrs = ", ".join(f"{m['address']}" for m in value["string_matches"][:4]) or "-"
        print(f"  {name:28} strings[{addrs}] funcs[{funcs}]")
    print("written:", OUT)


if __name__ == "__main__":
    main()
