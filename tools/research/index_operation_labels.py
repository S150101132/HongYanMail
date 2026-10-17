"""Index the client's internal operation labels locally.

Coremail's per-operation implementations log a label of the form `<Name>:%v`.
Those labels are string literals, so scanning for the pattern yields an internal
feature index that is independent of the RPC name list: it exposes operations
that are not RPCs (e.g. getRcptSMimePublicKey, RestoreDraftAfterFetchMailBody)
and maps each to the function that emits it.

Method: reuse the .text RIP-relative lea scan to find which literals are actually
referenced, then keep those matching the label pattern.
"""
from __future__ import annotations

import collections
import json
import os
import re
import struct

WS = r"D:\workspace\redocoreemail"
EXE = r"C:\Program Files\cmclient\CMClient.exe"
OUT = os.path.join(WS, "analysis", "internal-operation-labels.json")

LABEL = re.compile(r"^([A-Za-z][A-Za-z0-9_]{2,40}):(%v|%d|%s|%u|%x|%I64d)([ ,].*)?$")
CHECK = {"getRcptSMimePublicKey", "SendRecallMailReq", "reqRestoreDraft"}


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


def read_literal(data, sections, va, limit=140):
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
    data = open(EXE, "rb").read()
    image_base, sections = parse_pe(data)
    text = sections[".text"]
    code = data[text["raw"]:text["raw"] + text["rsize"]]

    pdata = sections[".pdata"]
    blob = data[pdata["raw"]:pdata["raw"] + pdata["rsize"]]
    ranges = []
    for offset in range(0, len(blob) - 11, 12):
        begin, end, _u = struct.unpack_from("<III", blob, offset)
        if begin and end > begin:
            ranges.append((image_base + begin, image_base + end))
    ranges.sort()

    def owner(va):
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

    labels = collections.defaultdict(set)
    index = 0
    while index < len(code) - 7:
        if code[index] in (0x48, 0x4C) and code[index + 1] == 0x8D \
                and (code[index + 2] & 0xC7) == 0x05:
            disp = struct.unpack_from("<i", code, index + 3)[0]
            insn = text["start"] + index
            literal = read_literal(data, sections, text["start"] + index + 7 + disp)
            if literal:
                match = LABEL.match(literal)
                if match:
                    func = owner(insn)
                    labels[match.group(1)].add(func)
            index += 7
            continue
        index += 1

    missing = [name for name in CHECK if name not in labels]
    if missing:
        print(f"REFUSING TO EMIT: expected labels not found: {missing}")
        return 1
    print(f"  OK all {len(CHECK)} control labels found (e.g. getRcptSMimePublicKey)")
    for name in CHECK:
        print(f"     {name:28} -> {sorted('0x%x' % f for f in labels[name] if f)}")

    payload = {"scope": ("internal operation labels (`<Name>:%v`) actually referenced by code, "
                         "with the function that emits each"),
               "method": "lea scan + literal read + .pdata function attribution",
               "count": len(labels),
               "labels": {name: sorted(f"0x{f:x}" for f in funcs if f)
                          for name, funcs in sorted(labels.items())}}
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\ninternal operation labels: {len(labels)}")
    for name in sorted(labels)[:40]:
        funcs = [f"0x{f:x}" for f in labels[name] if f]
        print(f"   {name:38} {', '.join(funcs[:2])}")
    print("written:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
