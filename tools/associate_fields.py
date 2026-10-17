"""Associate candidate field names with interfaces using a validated rule.

Rule (validated below): an interface's field literals are the identifier literals
loaded by ANY function that references ANY occurrence of the interface name,
including the bare `module:method` literal — not just the URL-form literal, whose
referencing function is a generic HTTP helper rather than the feature code.

Ground truth from client logs / contract runs must all appear, otherwise the
script refuses to emit.
"""
from __future__ import annotations

import collections
import json
import os
import re
import struct

WS = r"D:\workspace\redocoreemail"
EXE = r"C:\Program Files\cmclient\CMClient.exe"
OUT = os.path.join(WS, "analysis", "interface-candidate-fields.json")

IDENT = re.compile(r"^[a-z][A-Za-z0-9_]{2,29}$")
KNOWN = {
    "user:getAttrs": ["attrIds", "optionalAttrIds"],
    "mbox:deleteMessages": ["localArchive"],
    "mbox:listSharedAccessMbox": ["showHidden"],
    "mbox:replyMessage": ["extraHeaders", "autoDel"],
}


def parse_pe(data):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    _m, nsec = struct.unpack_from("<HH", data, pe + 4)
    size_opt = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    image_base = struct.unpack_from("<Q", data, opt + 24)[0] if magic == 0x20B \
        else struct.unpack_from("<I", data, opt + 28)[0]
    sections = {}
    base = opt + size_opt
    for i in range(nsec):
        off = base + i * 40
        name = data[off:off + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, va, rsize, raw = struct.unpack_from("<IIII", data, off + 8)
        sections[name] = {"start": image_base + va, "vsize": vsize, "rsize": rsize, "raw": raw}
    return image_base, sections


def read_literal(data, sections, va, limit=110):
    for name in (".rdata", "_RDATA", ".data"):
        sec = sections.get(name)
        if sec and sec["start"] <= va < sec["start"] + max(sec["vsize"], sec["rsize"]):
            off = sec["raw"] + (va - sec["start"])
            end = off
            while end < len(data) and end - off < limit and 0x20 <= data[end] <= 0x7E:
                end += 1
            if end > off and (end >= len(data) or data[end] == 0):
                return data[off:end].decode("ascii", "replace")
    return None


def main():
    data = open(EXE, "rb").read()
    image_base, sections = parse_pe(data)
    text = sections[".text"]
    code = data[text["raw"]:text["raw"] + text["rsize"]]

    pdata = sections[".pdata"]
    blob = data[pdata["raw"]:pdata["raw"] + pdata["rsize"]]
    ranges = []
    for off in range(0, len(blob) - 11, 12):
        b, e, _u = struct.unpack_from("<III", blob, off)
        if b and e > b:
            ranges.append((image_base + b, image_base + e))
    ranges.sort()

    def owner(va):
        lo, hi = 0, len(ranges) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            s, e = ranges[mid]
            if va < s:
                hi = mid - 1
            elif va >= e:
                lo = mid + 1
            else:
                return s
        return None

    # index: lea target -> loading functions ; function -> identifier literals
    target_funcs = collections.defaultdict(set)
    func_idents = collections.defaultdict(set)
    index = 0
    while index < len(code) - 7:
        if code[index] in (0x48, 0x4C) and code[index + 1] == 0x8D \
                and (code[index + 2] & 0xC7) == 0x05:
            disp = struct.unpack_from("<i", code, index + 3)[0]
            insn = text["start"] + index
            target = text["start"] + index + 7 + disp
            func = owner(insn)
            if func:
                target_funcs[target].add(func)
                literal = read_literal(data, sections, target)
                if literal and IDENT.match(literal):
                    func_idents[func].add(literal)
            index += 7
            continue
        index += 1

    # every occurrence of every interface name, across all sections
    final = json.load(open(os.path.join(WS, "analysis", "server-api-final.json"), encoding="utf-8"))
    by_mod = final.get("by_module") or final
    names = []
    for module, items in by_mod.items():
        for item in items:
            method = item.get("method") if isinstance(item, dict) else str(item)
            names.append(f"{module}:{method}")

    result = {}
    for name in names:
        needle = name.encode("ascii")
        literals = []
        start = 0
        while True:
            pos = data.find(needle, start)
            if pos < 0:
                break
            # walk back to the start of the enclosing printable run
            begin = pos
            while begin > 0 and 0x20 <= data[begin - 1] <= 0x7E:
                begin -= 1
            for sec in sections.values():
                if sec["raw"] <= begin < sec["raw"] + sec["rsize"]:
                    literals.append(sec["start"] + (begin - sec["raw"]))
                    break
            start = pos + 1
        funcs = set()
        for literal in set(literals):
            funcs |= target_funcs.get(literal, set())
        fields = set()
        for func in funcs:
            fields |= func_idents.get(func, set())
        fields = {f for f in fields if ":" not in f and "/" not in f and f != name}
        if fields:
            result[name] = sorted(fields)[:30]

    failures = 0
    for name, expected in KNOWN.items():
        got = set(result.get(name, []))
        missing = [e for e in expected if e not in got]
        if missing:
            print(f"  MISS {name}: {missing} absent from candidates")
            failures += 1
        else:
            print(f"  OK   {name}: all {len(expected)} known field(s) present "
                  f"({len(got)} candidates)")
    if failures:
        print(f"REFUSING TO EMIT: {failures}/{len(KNOWN)} ground-truth checks failed")
        return 1
    print(f"all {len(KNOWN)} ground-truth checks passed")

    json.dump({"scope": ("candidate field names per interface: identifier literals loaded by any "
                         "function that references any occurrence of the interface name"),
               "validated_on": {k: v for k, v in KNOWN.items()},
               "caveat": ("candidates may include response fields, log tags or flags from the same "
                          "feature; association rule is validated but individual values still need "
                          "contract confirmation"),
               "count": len(result), "candidates": result},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\ninterfaces with candidates: {len(result)}/{len(names)}")
    for name in sorted(result)[:25]:
        print(f"  {name:32} {', '.join(result[name][:10])}")
    print("written:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
