"""Map every server status code to the function that handles it, locally.

For each FA_*/FS_* code: find its .rdata address, find the .text lea instructions
that load it, resolve the containing functions via .pdata, and collect the other
literals those functions use (log messages, response fields, UI hints).

Self-verification: FA_NEED_BIND_DEVICE must resolve to FUN_140cff190 (0x140cff190),
the function Ghidra reported for that string.
"""
from __future__ import annotations

import collections
import json
import os
import struct

WS = r"D:\workspace\redocoreemail"
EXE = r"C:\Program Files\cmclient\CMClient.exe"
OUT = os.path.join(WS, "analysis", "error-code-handlers.json")
CHECK = {"FA_NEED_BIND_DEVICE": 0x140CFF190}


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


def read_literal(data, sections, va, limit=80):
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


def owner(ranges, va):
    lo, hi = 0, len(ranges) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        start, end = ranges[mid]
        if va < start:
            hi = mid - 1
        elif va >= end:
            lo = mid + 1
        else:
            return start
    return None


def main():
    data = open(EXE, "rb").read()
    image_base, sections = parse_pe(data)

    pdata = sections[".pdata"]
    blob = data[pdata["raw"]:pdata["raw"] + pdata["rsize"]]
    ranges = []
    for offset in range(0, len(blob) - 11, 12):
        begin, end, _u = struct.unpack_from("<III", blob, offset)
        if begin and end > begin:
            ranges.append((image_base + begin, image_base + end))
    ranges.sort()

    text = sections[".text"]
    code = data[text["raw"]:text["raw"] + text["rsize"]]
    # instruction -> target
    lea = {}
    index = 0
    while index < len(code) - 7:
        if (code[index] in (0x48, 0x4C)) and code[index + 1] == 0x8D \
                and (code[index + 2] & 0xC7) == 0x05:
            disp = struct.unpack_from("<i", code, index + 3)[0]
            lea[text["start"] + index] = text["start"] + index + 7 + disp
            index += 7
            continue
        index += 1

    # literals per function (needed for sibling context)
    per_function = collections.defaultdict(list)
    for insn, target in lea.items():
        literal = read_literal(data, sections, target)
        if literal:
            per_function[owner(ranges, insn)].append(literal)

    codes = json.load(open(os.path.join(WS, "analysis", "native-auth-evidence-2026-10-16.json"),
                           encoding="utf-8"))
    names = codes.get("fa_error_codes") or codes.get("fa_codes") or codes.get("codes") or []
    if isinstance(names, dict):
        names = list(names)
    names = [n for n in names if isinstance(n, str) and (n.startswith("FA_") or n.startswith("FS_"))]
    print(f"codes from the auth evidence artifact: {len(names)}")

    result = {}
    for code in sorted(set(names)):
        needle = code.encode("ascii")
        pos = data.find(needle + b"\0")
        if pos < 0:
            result[code] = {"status": "literal not found"}
            continue
        # map file offset -> va
        va = None
        for name, sec in sections.items():
            if sec["raw"] <= pos < sec["raw"] + sec["rsize"]:
                va = sec["start"] + (pos - sec["raw"])
                break
        if va is None:
            result[code] = {"status": "offset unmapped"}
            continue
        funcs = sorted({owner(ranges, insn) for insn, target in lea.items()
                        if target == va and owner(ranges, insn)})
        siblings = []
        for func in funcs:
            for literal in per_function.get(func, []):
                if literal != code and literal not in siblings:
                    siblings.append(literal)
        result[code] = {"literal_va": f"0x{va:x}", "handlers": [f"0x{f:x}" for f in funcs],
                        "sibling_literals": siblings[:14]}

    failures = 0
    for code, expected in CHECK.items():
        handlers = [int(h, 16) for h in result.get(code, {}).get("handlers", [])]
        if expected not in handlers:
            print(f"  MISMATCH {code}: handlers {[hex(h) for h in handlers]}, expected 0x{expected:x}")
            failures += 1
        else:
            print(f"  OK {code} -> FUN_{expected:x} (matches the Ghidra observation)")
    if failures:
        print("REFUSING TO EMIT")
        return 1

    json.dump({"scope": "server status code -> handling function(s) and sibling literals (local)",
               "method": "literal address + lea xrefs + .pdata function boundaries",
               "count": len(result), "codes": result},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    handled = sum(1 for v in result.values() if v.get("handlers"))
    print(f"\ncodes with a resolved handler: {handled}/{len(result)}")
    for code in sorted(result):
        info = result[code]
        if info.get("handlers"):
            print(f"  {code:34} -> {', '.join('FUN_' + h[2:] for h in info['handlers'][:2])}"
                  f"   siblings={len(info['sibling_literals'])}")
    print("\nwritten:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
