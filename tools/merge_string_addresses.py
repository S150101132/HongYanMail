"""Merge the RPC string-address sweep into a compact lookup.

Input : analysis/rea-native/string-sweep.json   (rea search --kind strings --mode regex)
Output: analysis/rpc-string-addresses.json      ({"module:method": "0x...", ...})

The sweep is one program load covering every RPC literal, so this table can give
an address-level code location for interfaces that have not been traced yet.
"""
from __future__ import annotations

import json
import os
import re

WS = r"D:\workspace\redocoreemail"
SWEEP = os.path.join(WS, "analysis", "rea-native", "string-sweep.json")
OUT = os.path.join(WS, "analysis", "rpc-string-addresses.json")

RPC = re.compile(r"(?<![A-Za-z0-9_.])([a-z][a-z0-9_]*\.[a-z0-9_]+:[A-Za-z][A-Za-z0-9_]*)")


def load(path):
    raw = open(path, encoding="utf-8", errors="replace").read().strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(raw[start:end + 1])
        except ValueError:
            return None


def collect_matches(node, out):
    """Walk the envelope for {address, value} pairs regardless of nesting."""
    if isinstance(node, dict):
        address = node.get("address")
        value = node.get("value")
        if isinstance(address, str) and isinstance(value, str):
            out.append((address, value))
        for child in node.values():
            collect_matches(child, out)
    elif isinstance(node, list):
        for child in node:
            collect_matches(child, out)


def main():
    if not os.path.exists(SWEEP):
        print("no sweep output yet:", SWEEP)
        return
    envelope = load(SWEEP)
    if not envelope:
        print("sweep output is empty or unparseable:", os.path.getsize(SWEEP), "bytes")
        return
    if envelope.get("ok") is False:
        err = envelope.get("error") or {}
        print("sweep failed:", err.get("code"), (err.get("message") or "")[:200])
        return

    pairs = []
    collect_matches(envelope, pairs)
    table = {}
    for address, value in pairs:
        for name in RPC.findall(value):
            table.setdefault(name, address)

    json.dump({"scope": "RPC literal -> string address, from one search sweep",
               "count": len(table), "addresses": table},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"matched string literals: {len(pairs)}  distinct RPC names: {len(table)}")
    for name in sorted(table)[:15]:
        print(f"  {name:34} {table[name]}")
    print("written:", OUT)


if __name__ == "__main__":
    main()
