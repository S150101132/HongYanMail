"""Map every Coremail RPC literal to its virtual address locally (no Ghidra).

Method: parse the PE section table, scan the file for each RPC name, then map the
byte offset through the containing section (VA = ImageBase + VirtualAddress +
(offset - PointerToRawData)). The mapping is self-verifying: the address computed
for `mbox:listMessages` must equal the Ghidra-reported string address
0x1420f9910, otherwise this script refuses to emit a table.
"""
from __future__ import annotations

import json
import os
import struct

WS = r"D:\workspace\redocoreemail"
EXE = r"C:\Program Files\cmclient\CMClient.exe"
OUT = os.path.join(WS, "analysis", "rpc-string-addresses.json")
KNOWN_CHECK = {
    "mbox:listMessages": 0x1420F9910,
    "user:login": 0x142106068,
    "user:getAttrs": 0x1420FB5C8,
    "global:verify": 0x1420FB308,
    "user:getPasswordKey": 0x1421059A8,
    "cim.user:getLunkrMailData": 0x1421118D8,
    "cim.common:checkVersion": 0x142103770,
    "user:getTempSession": 0x1420FAB90,
    "user:addAppPwd": 0x1420FB0A8,
    "user:getPasswordURL": 0x1420FAD30,
}


def parse_sections(data):
    if data[:2] != b"MZ":
        raise ValueError("not a PE image")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("missing PE signature")
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


def offset_to_va(offset, sectors, image_base):
    for sec in sectors:
        if sec["raw"] <= offset < sec["raw"] + sec["rsize"]:
            return image_base + sec["va"] + (offset - sec["raw"]), sec["name"]
    return None, None


def literal_bounds(data, pos, limit=4096):
    """Expand from pos to the enclosing printable-ASCII run (the whole literal)."""
    start = pos
    while start > 0 and pos - start < limit and 0x20 <= data[start - 1] <= 0x7E:
        start -= 1
    end = pos
    while end < len(data) and end - start < limit and 0x20 <= data[end] <= 0x7E:
        end += 1
    return start, end


def main():
    data = open(EXE, "rb").read()
    image_base, sections = parse_sections(data)
    print(f"image_base=0x{image_base:x} sections=" +
          ", ".join(f"{s['name']}@0x{image_base + s['va']:x}" for s in sections))

    final = json.load(open(os.path.join(WS, "analysis", "server-api-final.json"), encoding="utf-8"))
    by_mod = final.get("by_module") or final
    names = []
    for module, items in by_mod.items():
        for item in items:
            method = item.get("method") if isinstance(item, dict) else str(item)
            names.append(f"{module}:{method}")

    addresses, methods = {}, {}
    missing = []
    for name in names:
        needle = name.encode("ascii")
        hits = []
        start = 0
        while True:
            pos = data.find(needle, start)
            if pos < 0:
                break
            lit_start, lit_end = literal_bounds(data, pos)
            lit_va, section = offset_to_va(lit_start, sections, image_base)
            name_va, _ = offset_to_va(pos, sections, image_base)
            if lit_va is not None:
                hits.append({"literal_va": lit_va, "name_va": name_va, "section": section,
                             "file_offset": lit_start,
                             "literal": data[lit_start:lit_end].decode("ascii", "replace")[:120]})
            start = pos + 1
        if not hits:
            missing.append(name)
            continue
        # Prefer the endpoint-form literal (what Ghidra reports), then a bare literal,
        # then any .rdata occurrence; several occurrences are common.
        endpoint = [h for h in hits if "func=" in h["literal"]]
        bare = [h for h in hits if h["literal"].strip() == name]
        rdata = [h for h in hits if h["section"] == ".rdata"]
        best = (endpoint or bare or rdata or hits)[0]
        addresses[name] = f"0x{best['literal_va']:x}"
        methods[name] = {**best, "occurrences": len(hits),
                         "all": [{"literal_va": h["literal_va"], "section": h["section"],
                                  "literal": h["literal"]} for h in hits[:6]]}

    # self-verification against Ghidra-observed string addresses
    failures = 0
    for name, expected in KNOWN_CHECK.items():
        got = addresses.get(name)
        if got is None or int(got, 16) != expected:
            print(f"  MISMATCH {name}: computed {got}, Ghidra 0x{expected:x}")
            failures += 1
        else:
            print(f"  OK       {name} = {got} (matches Ghidra)")
    if failures:
        print(f"REFUSING TO EMIT: {failures}/{len(KNOWN_CHECK)} cross-checks failed")
        return 1
    print(f"all {len(KNOWN_CHECK)} Ghidra cross-checks passed")

    payload = {
        "scope": ("RPC literal -> virtual address, computed locally from PE section mapping; "
                  "cross-verified against a Ghidra-reported string address"),
        "method": "PE section table offset->RVA->VA; prefers the .rdata occurrence",
        "image_base": f"0x{image_base:x}",
        "count": len(addresses),
        "unmatched": missing,
        "addresses": addresses,
        "details": methods,
    }
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nmapped {len(addresses)}/{len(names)} interfaces; unmatched={len(missing)}")
    for name in missing[:10]:
        print("   unmatched:", name)
    print("written:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
