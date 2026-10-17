"""String profile for any interface: literals loaded by the functions that use it.

Generalises the auth-cluster profiling to arbitrary interfaces. For each requested
RPC name it takes the referencing functions (from rpc-string-refs.json, whose
function attribution was validated against 15 Ghidra traces) and lists every
.rdata literal those functions load via RIP-relative lea.

Interpretation rules (learned the hard way, see 04 §3 row 37):
  - log/format strings and error codes are reliable;
  - a literal being present does NOT prove it is a request field name.
"""
from __future__ import annotations

import collections
import json
import os
import struct
import sys

WS = r"D:\workspace\redocoreemail"
EXE = r"C:\Program Files\cmclient\CMClient.exe"
REFS = os.path.join(WS, "analysis", "rpc-string-refs.json")
OUT = os.path.join(WS, "analysis", "interface-string-profiles.json")


def parse_pe(data):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    _machine, nsec = struct.unpack_from("<HH", data, pe + 4)
    size_opt = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    image_base = struct.unpack_from("<Q", data, opt + 24)[0] if magic == 0x20B \
        else struct.unpack_from("<I", data, opt + 28)[0]
    sections = {}
    base = opt + size_opt
    for index in range(nsec):
        off = base + index * 40
        name = data[off:off + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, va, rsize, raw = struct.unpack_from("<IIII", data, off + 8)
        sections[name] = {"start": image_base + va, "vsize": vsize, "rsize": rsize, "raw": raw}
    return image_base, sections


def read_literal(data, sections, va, limit=110):
    for name in (".rdata", "_RDATA", ".data"):
        sec = sections.get(name)
        if sec and sec["start"] <= va < sec["start"] + max(sec["vsize"], sec["rsize"]):
            offset = sec["raw"] + (va - sec["start"])
            end = offset
            while end < len(data) and end - offset < limit and 0x20 <= data[end] <= 0x7E:
                end += 1
            if end > offset and (end >= len(data) or data[end] == 0):
                return data[offset:end].decode("ascii", "replace")
    return None


def main():
    wanted = sys.argv[1:] or ["upload:prepare", "upload:directData", "mbox:searchMessages",
                              "oab:getCacheFile", "oab:getDirectories", "user:listDeliveryHistory"]
    data = open(EXE, "rb").read()
    image_base, sections = parse_pe(data)
    text = sections[".text"]
    code = data[text["raw"]:text["raw"] + text["rsize"]]

    lea = {}
    index = 0
    while index < len(code) - 7:
        if code[index] in (0x48, 0x4C) and code[index + 1] == 0x8D \
                and (code[index + 2] & 0xC7) == 0x05:
            disp = struct.unpack_from("<i", code, index + 3)[0]
            lea[text["start"] + index] = text["start"] + index + 7 + disp
            index += 7
            continue
        index += 1

    pdata = sections[".pdata"]
    blob = data[pdata["raw"]:pdata["raw"] + pdata["rsize"]]
    ranges = []
    for offset in range(0, len(blob) - 11, 12):
        begin, end, _u = struct.unpack_from("<III", blob, offset)
        if begin and end > begin:
            ranges.append((image_base + begin, image_base + end))
    ranges.sort()

    per_function = collections.defaultdict(list)
    for insn, target in lea.items():
        literal = read_literal(data, sections, target)
        if not literal:
            continue
        lo, hi = 0, len(ranges) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            start, end = ranges[mid]
            if insn < start:
                hi = mid - 1
            elif insn >= end:
                lo = mid + 1
            else:
                per_function[start].append(literal)
                break

    refs = json.load(open(REFS, encoding="utf-8"))["references"]
    profiles = {}
    for name in wanted:
        info = refs.get(name)
        if not info:
            profiles[name] = {"status": "no referencing function found"}
            continue
        literals, seen = [], set()
        for func in info.get("function_ranges") or []:
            for literal in per_function.get(int(func, 16), []):
                if literal not in seen:
                    seen.add(literal)
                    literals.append(literal)
        profiles[name] = {"literal": info.get("literal"),
                          "functions": info.get("function_ranges"),
                          "count": len(literals), "literals": literals}

    json.dump({"scope": ("per-interface string profile: literals loaded by the functions that "
                         "reference the RPC name; presence is not proof of a request field"),
               "profiles": profiles},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    for name in wanted:
        profile = profiles[name]
        print(f"\n=== {name}  functions={profile.get('functions')}  literals={profile.get('count')}")
        for literal in (profile.get("literals") or [])[:45]:
            print("   ", literal[:110])
    print("\nwritten:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
