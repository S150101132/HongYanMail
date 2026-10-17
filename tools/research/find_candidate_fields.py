"""Candidate request/response field names per RPC, derived locally.

**NEGATIVE RESULT (2026-10-16): this approach does not work and the script refuses
to emit.** The premise was that the function loading an RPC literal also builds
that call's body, so the identifier literals it references would be candidate
field names. Measured on this binary that premise is false: field-name literals
are referenced by *different* functions than the RPC-name literal. Examples:

    attrIds          referenced by FUN_1407991d0   while user:getAttrs  -> FUN_140c37800
    localArchive     referenced by FUN_140dddeb0   while mbox:deleteMessages -> other
    extraHeaders     referenced by FUN_140eb5a90   while mbox:replyMessage   -> other

Only 1 of the 5 ground-truth interfaces passed, so the script exits non-zero.
Kept in the repository to document the rejected method and to make the ground-truth
check reusable if the premise is ever revisited. See docs/research/04-接口证据与验证记录.md §6.

Original intent follows.

Idea: the function that loads an RPC literal is the one that builds or handles
that call, so the short identifier literals it references are candidate field
names. This is derived evidence, not a parsed request body, so every entry is
labelled as a candidate.

Self-verification: for the interfaces whose field names are already known from
client logs, the known names must appear among the candidates; the script refuses
to emit otherwise.
"""
from __future__ import annotations

import json
import os
import re
import struct

WS = r"D:\workspace\redocoreemail"
EXE = r"C:\Program Files\cmclient\CMClient.exe"
OUT = os.path.join(WS, "analysis", "rpc-candidate-fields.json")

IDENT = re.compile(r"^[a-z][A-Za-z0-9_]{2,29}$")

# ground truth from client logs / contract runs (see 04 §3 rows 28, 31)
KNOWN = {
    "user:getAttrs": ["attrIds", "optionalAttrIds"],
    "mbox:updateMessageInfos": ["attrs", "ids", "flags", "read", "flagged"],
    "mbox:deleteMessages": ["ids", "localArchive"],
    "mbox:listSharedAccessMbox": ["showHidden"],
    "mbox:replyMessage": ["action", "attrs", "bcc", "cc", "ctrls"],
}


def parse_pe(data):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    _machine, nsec = struct.unpack_from("<HH", data, pe + 4)
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


def section_for_va(sections, image_base, va):
    for sec in sections:
        start = image_base + sec["va"]
        if start <= va < start + max(sec["vsize"], sec["rsize"]):
            return sec
    return None


def read_literal(data, sections, image_base, va, limit=64):
    sec = section_for_va(sections, image_base, va)
    if sec is None or sec["name"] not in (".rdata", ".data", "_RDATA"):
        return None
    offset = sec["raw"] + (va - (image_base + sec["va"]))
    if not (0 <= offset < len(data)):
        return None
    end = offset
    while end < len(data) and end - offset < limit and 0x20 <= data[end] <= 0x7E:
        end += 1
    if end == offset or (end < len(data) and data[end] != 0):
        return None
    return data[offset:end].decode("ascii", "replace")


def main():
    data = open(EXE, "rb").read()
    image_base, sections = parse_pe(data)

    # function ranges from .pdata
    pdata = next(s for s in sections if s["name"] == ".pdata")
    blob = data[pdata["raw"]:pdata["raw"] + pdata["rsize"]]
    ranges = []
    for offset in range(0, len(blob) - 11, 12):
        begin, end, _unwind = struct.unpack_from("<III", blob, offset)
        if begin and end > begin:
            ranges.append((image_base + begin, image_base + end))
    ranges.sort()
    print(f"function ranges: {len(ranges):,}")

    # every RIP-relative lea in .text, with its literal when it points at one
    text = next(s for s in sections if s["name"] == ".text")
    code = data[text["raw"]:text["raw"] + text["rsize"]]
    text_va = image_base + text["va"]
    per_function = {}
    index = 0
    while index < len(code) - 7:
        opcode = code[index]
        if (opcode == 0x48 or opcode == 0x4C) and code[index + 1] == 0x8D:
            modrm = code[index + 2]
            if (modrm & 0xC7) == 0x05:
                disp = struct.unpack_from("<i", code, index + 3)[0]
                insn_va = text_va + index
                literal = read_literal(data, sections, image_base, insn_va + 7 + disp)
                if literal and IDENT.match(literal):
                    lo, hi = 0, len(ranges) - 1
                    owner = None
                    while lo <= hi:
                        mid = (lo + hi) // 2
                        start, end = ranges[mid]
                        if insn_va < start:
                            hi = mid - 1
                        elif insn_va >= end:
                            lo = mid + 1
                        else:
                            owner = start
                            break
                    if owner is not None:
                        per_function.setdefault(owner, set()).add(literal)
                index += 7
                continue
        index += 1
    print(f"functions with identifier literals: {len(per_function):,}")

    refs = json.load(open(os.path.join(WS, "analysis", "rpc-string-refs.json"), encoding="utf-8"))["references"]
    result = {}
    for name, info in refs.items():
        candidates = set()
        for func in info.get("function_ranges") or []:
            candidates |= per_function.get(int(func, 16), set())
        # drop the RPC name itself and its own path pieces
        candidates = {c for c in candidates if ":" not in c and "/" not in c and c != name}
        if candidates:
            result[name] = sorted(candidates)[:24]

    failures = 0
    for name, expected in KNOWN.items():
        got = set(result.get(name, []))
        missing = [e for e in expected if e not in got]
        if missing:
            print(f"  MISS {name}: expected {missing} not among candidates")
            failures += 1
        else:
            print(f"  OK   {name}: all {len(expected)} known field name(s) present "
                  f"({len(got)} candidates)")
    if failures:
        print(f"REFUSING TO EMIT: {failures}/{len(KNOWN)} ground-truth checks failed")
        return 1
    print(f"all {len(KNOWN)} ground-truth checks passed")

    json.dump({
        "scope": ("candidate identifier literals referenced by the functions that load each "
                  "RPC literal; DERIVED - not a parsed request body"),
        "method": "lea scan in .text + .pdata function boundaries + .rdata literals matching ^[a-z][A-Za-z0-9_]{2,29}$",
        "caveat": ("candidates may include response fields, log tags or error codes used by the "
                   "same function; treat as leads to verify, never as a confirmed schema"),
        "count": len(result),
        "candidates": result,
    }, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\ninterfaces with candidates: {len(result)}/{len(refs)}")
    print("written:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
