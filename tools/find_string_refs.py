"""Locate the code that loads each RPC literal, locally (no disassembler).

On MSVC x64 a string literal is almost always loaded with a RIP-relative
`lea`, encoded as (48|4C) 8D <modrm> <disp32> with modrm mod=00 rm=101. This
script scans .text for such instructions and resolves their targets, so each RPC
literal gains the addresses of the instructions that reference it.

Self-verification: for `mbox:listMessages` the Ghidra-observed referencing
functions are FUN_140c10b90 (0x140c10b90-0x140c11b62) and FUN_140e56030
(0x140e56030-0x140e573a6); every hit for that literal must fall inside one of
those ranges, otherwise this script refuses to emit a table.
"""
from __future__ import annotations

import json
import os
import struct

WS = r"D:\workspace\redocoreemail"
EXE = r"C:\Program Files\cmclient\CMClient.exe"
OUT = os.path.join(WS, "analysis", "rpc-string-refs.json")

KNOWN_CHECK = {
    "mbox:listMessages": [
        (0x1420F9910, [(0x140C10B90, 0x140C11B62), (0x140E56030, 0x140E573A6)]),
    ],
}


def parse_pe(data):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    machine, nsec = struct.unpack_from("<HH", data, pe + 4)
    size_opt = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    image_base = struct.unpack_from("<Q", data, opt + 24)[0] if magic == 0x20B \
        else struct.unpack_from("<I", data, opt + 28)[0]
    sections = []
    base = opt + size_opt
    for index in range(nsec):
        off = base + index * 40
        name = data[off:off + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, va, rsize, raw = struct.unpack_from("<IIII", data, off + 8)
        sections.append({"name": name, "va": va, "vsize": vsize, "raw": raw, "rsize": rsize})
    return image_base, sections


def find_lea_targets(text, text_va):
    """Yield (instruction_va, target_va) for RIP-relative lea instructions."""
    out = []
    length = len(text)
    index = 0
    while index < length - 7:
        opcode = text[index]
        if (opcode == 0x48 or opcode == 0x4C) and text[index + 1] == 0x8D:
            modrm = text[index + 2]
            if (modrm & 0xC7) == 0x05:
                disp = struct.unpack_from("<i", text, index + 3)[0]
                next_va = text_va + index + 7
                out.append((text_va + index, next_va + disp))
                index += 7
                continue
        index += 1
    return out


def parse_pdata(data, sections, image_base):
    """Function boundaries from the x64 exception directory (.pdata)."""
    pdata = next((s for s in sections if s["name"] == ".pdata"), None)
    if pdata is None:
        return []
    blob = data[pdata["raw"]:pdata["raw"] + pdata["rsize"]]
    entries = []
    for offset in range(0, len(blob) - 11, 12):
        begin, end, unwind = struct.unpack_from("<III", blob, offset)
        if begin == 0 or end <= begin:
            continue
        entries.append((image_base + begin, image_base + end))
    entries.sort()
    return entries


def containing_function(entries, va):
    """Binary search for the function range containing va."""
    lo, hi = 0, len(entries) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        start, end = entries[mid]
        if va < start:
            hi = mid - 1
        elif va >= end:
            lo = mid + 1
        else:
            return start, end
    return None, None


def main():
    data = open(EXE, "rb").read()
    image_base, sections = parse_pe(data)
    text = next(s for s in sections if s["name"] == ".text")
    blob = data[text["raw"]:text["raw"] + text["rsize"]]
    text_va = image_base + text["va"]
    print(f".text 0x{text_va:x}..0x{text_va + len(blob):x} ({len(blob):,} bytes)")

    functions = parse_pdata(data, sections, image_base)
    print(f"function ranges from .pdata: {len(functions):,}")

    pairs = find_lea_targets(blob, text_va)
    refs_by_target = {}
    for insn_va, target in pairs:
        refs_by_target.setdefault(target, []).append(insn_va)
    print(f"RIP-relative lea instructions: {len(pairs):,} -> distinct targets: {len(refs_by_target):,}")

    table = json.load(open(os.path.join(WS, "analysis", "rpc-string-addresses.json"), encoding="utf-8"))
    addresses = table["addresses"]

    result, unresolved = {}, []
    for name, addr in addresses.items():
        insns = sorted(refs_by_target.get(int(addr, 16), []))
        if not insns:
            unresolved.append(name)
            continue
        funcs = {}
        for insn in insns:
            start, _end = containing_function(functions, insn)
            funcs.setdefault(start, []).append(insn)
        result[name] = {
            "literal": addr,
            "reference_instructions": [f"0x{a:x}" for a in insns],
            "function_ranges": sorted(f"0x{s:x}" for s in funcs if s is not None),
            "instructions_without_function": [f"0x{a:x}" for a in insns
                                              if containing_function(functions, a)[0] is None],
        }

    # self-verification against Ghidra's referencing functions
    for name, checks in KNOWN_CHECK.items():
        for addr, ranges in checks:
            insns = refs_by_target.get(addr, [])
            if not insns:
                print(f"REFUSING TO EMIT: no lea reference found for {name} @0x{addr:x}")
                return 1
            outside = [a for a in insns if not any(lo <= a < hi for lo, hi in ranges)]
            if outside:
                print(f"REFUSING TO EMIT: {name} has hits outside the Ghidra function ranges: "
                      f"{[hex(a) for a in outside]}")
                return 1
            derived = sorted({containing_function(functions, a)[0] for a in insns})
            expected = sorted(lo for lo, _hi in ranges)
            status = "MATCHES Ghidra function starts" if derived == expected \
                else f"differs from Ghidra starts {[hex(e) for e in expected]}"
            print(f"  OK {name} @0x{addr:x}: {len(insns)} lea hit(s); derived functions "
                  f"{[hex(d) for d in derived]} -> {status}")

    payload = {
        "scope": "RPC literal -> referencing instructions and their containing functions (local scan)",
        "method": ("lea scan: (48|4C) 8D <modrm mod=00 rm=101> disp32 in .text; "
                   "function boundaries from the .pdata exception directory"),
        "caveat": ("only lea-style loads are found; immediate stores, jump tables and indirect "
                   "constructions are not detected; .pdata omits leaf functions without unwind info"),
        "count": len(result),
        "unresolved": unresolved,
        "references": result,
    }
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nreferenced literals: {len(result)}/{len(addresses)}; no lea hit: {len(unresolved)}")
    print("written:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
