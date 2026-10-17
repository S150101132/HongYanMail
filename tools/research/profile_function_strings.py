"""String profile of a function: every .rdata literal it loads via RIP-relative lea.

For functions whose decompilation is slow to obtain, this gives an immediately
useful picture of what the function handles (error codes, log messages, endpoints)
without a disassembler. Function boundaries come from .pdata.

Self-verification: FUN_140cff190 must reference FA_NEED_BIND_DEVICE (observed by
Ghidra at 0x142105688 from address 0x140d00df0), otherwise no profile is emitted.
"""
from __future__ import annotations

import json
import os
import struct
import sys

EXE = r"C:\Program Files\cmclient\CMClient.exe"
OUT = r"D:\workspace\redocoreemail\analysis\auth-cluster-string-profile.json"

TARGETS = {
    "FUN_140cf74f0": 0x140CF74F0,
    "FUN_140cfca00": 0x140CFCA00,
    "FUN_140cff190": 0x140CFF190,
    "FUN_140d08140": 0x140D08140,
    "FUN_140d08fd0": 0x140D08FD0,
}
CHECK = {"FUN_140cff190": "FA_NEED_BIND_DEVICE"}


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
        sections[name] = {"va": va, "vsize": vsize, "rsize": rsize, "raw": raw,
                          "start": image_base + va}
    return image_base, sections


def read_literal(data, sections, va, limit=96):
    for name in (".rdata", "_RDATA", ".data"):
        sec = sections.get(name)
        if not sec:
            continue
        if sec["start"] <= va < sec["start"] + max(sec["vsize"], sec["rsize"]):
            offset = sec["raw"] + (va - sec["start"])
            end = offset
            while end < len(data) and end - offset < limit and 0x20 <= data[end] <= 0x7E:
                end += 1
            if end > offset and (end >= len(data) or data[end] == 0):
                return data[offset:end].decode("ascii", "replace")
    return None


def function_range(data, sections, entry):
    pdata = sections[".pdata"]
    blob = data[pdata["raw"]:pdata["raw"] + pdata["rsize"]]
    for offset in range(0, len(blob) - 11, 12):
        begin, end, _u = struct.unpack_from("<III", blob, offset)
        if begin and end > begin and (0x140000000 + begin) == entry:
            return 0x140000000 + begin, 0x140000000 + end
    return None, None


def main():
    data = open(EXE, "rb").read()
    image_base, sections = parse_pe(data)
    text = sections[".text"]
    code = data[text["raw"]:text["raw"] + text["rsize"]]
    text_start = text["start"]

    # index every rip-relative lea once
    refs = {}
    index = 0
    while index < len(code) - 7:
        opcode = code[index]
        if (opcode == 0x48 or opcode == 0x4C) and code[index + 1] == 0x8D \
                and (code[index + 2] & 0xC7) == 0x05:
            disp = struct.unpack_from("<i", code, index + 3)[0]
            insn_va = text_start + index
            refs.setdefault(insn_va, text_start + index + 7 + disp)
            index += 7
            continue
        index += 1

    profiles = {}
    for name, entry in TARGETS.items():
        lo, hi = function_range(data, sections, entry)
        if lo is None:
            print(f"{name}: no .pdata range")
            continue
        literals = []
        for insn, target in refs.items():
            if lo <= insn < hi:
                literal = read_literal(data, sections, target)
                if literal:
                    literals.append((insn, literal))
        literals.sort()
        profiles[name] = {"range": f"0x{lo:x}-0x{hi:x}", "bytes": hi - lo,
                          "literals": [{"at": f"0x{i:x}", "value": v} for i, v in literals]}

    for name, expected in CHECK.items():
        values = [item["value"] for item in profiles.get(name, {}).get("literals", [])]
        if expected not in values:
            print(f"REFUSING TO EMIT: {name} does not reference {expected}")
            return 1
        print(f"  OK {name} references {expected} (matches the Ghidra observation)")

    json.dump({"scope": ("string profile of the auth-cluster functions: every .rdata literal "
                         "loaded by RIP-relative lea inside each function"),
               "method": "lea scan in .text + .pdata function boundaries + .rdata literal read",
               "verified": "FUN_140cff190 -> FA_NEED_BIND_DEVICE matches the Ghidra observation",
               "profiles": profiles},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    for name, profile in profiles.items():
        print(f"\n=== {name}  {profile['range']}  {profile['bytes']:,} B  "
              f"literals={len(profile['literals'])}")
        for item in profile["literals"]:
            print(f"   {item['at']}  {item['value'][:96]}")
    print("\nwritten:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
