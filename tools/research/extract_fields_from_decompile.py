"""Extract request-body key constructions from decompiled pseudocode, locally.

Why: reading pseudocode by eye costs a lot of context. This tool does the reading
and prints only the recovered keys, so a batch decompile can be turned into table
rows without pasting the pseudocode anywhere.

Two evidence forms are recovered:
  1. `<len>; ... = 0x<hex>` inline string construction (MSVC short-string form,
     possibly split across several assignments)
  2. `s_<name>_<addr>` named literals

KNOWN LIMITATION (observed, keep it in mind when reading the output): a length
constant that belongs to a neighbouring construction can be picked up, which
truncates a key (e.g. `fid`/`mid` came out as `fi`/`mi`). Treat the output as
leads and sanity-check obviously short or odd tokens before publishing them.

Validation: on FUN_1409a9300 the tool must recover `showHidden`, on FUN_140dddeb0
`localArchive`, and on FUN_140de0b60 `attrs`/`fid`/`reportType`; supplied
procedures that fail refuse emission.
"""
from __future__ import annotations

import json
import os
import re
import struct
import sys

WS = r"D:\workspace\redocoreemail"
CHECK = {
    "0x1409a9300": ["showHidden"],
    "0x140dddeb0": ["localArchive"],
    "0x140de0b60": ["attrs", "fid", "reportType"],
    "0x140c5e980": ["attrs", "fid", "rule"],
    "0x140c37800": ["attrIds"],
    "0x140e5a550": ["briefOnly", "recursive", "order", "desc"],
}


def decode_inline(value, length):
    if length <= 0 or length > 8:
        return None
    raw = value.to_bytes(8, "little")[:length]
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    return text if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", text) else None


def extract(pseudocode):
    """Recover candidate keys.

    MSVC builds a short string as a length assignment followed by one or more
    constant assignments whose bytes concatenate (e.g. `local = 0x72747461` then
    `uStack = 0x73` gives "attrs" for length 5), so the decoder accumulates bytes
    until the declared length is reached.
    """
    text = pseudocode.replace("\\r\\n", "\n")
    keys = set()
    lines = text.splitlines()

    length_re = re.compile(r"\b(?:local|uStack|iStack)_[0-9a-f]+ = (\d+);")
    hex_re = re.compile(r"=\s*[^;]*?(0x[0-9a-fA-F]{2,16})")

    index = 0
    while index < len(lines):
        match = length_re.search(lines[index])
        if not match:
            index += 1
            continue
        length = int(match.group(1))
        if not (1 <= length <= 32):
            index += 1
            continue
        collected = bytearray()
        cursor = index + 1
        while cursor < len(lines) and len(collected) < length and cursor <= index + 12:
            value = hex_re.search(lines[cursor])
            if value:
                raw = int(value.group(1), 16)
                width = 8 if raw > 0xFFFFFFFF else 4 if raw > 0xFFFF else 2
                collected.extend(raw.to_bytes(width, "little"))
            elif ";" in lines[cursor] and length_re.search(lines[cursor]):
                break
            cursor += 1
        candidate = bytes(collected[:length])
        if candidate and re.fullmatch(rb"[A-Za-z][A-Za-z0-9_]*", candidate):
            keys.add(candidate.decode("ascii"))
        index += 1

    # named literals such as s_showHidden_1420e6e58
    keys.update(re.findall(r's_([A-Za-z][A-Za-z0-9_]{2,28})_[0-9a-f]{6,10}\b', text))
    return sorted(keys)


def main():
    paths = sys.argv[1:]
    if not paths:
        print("usage: extract_fields_from_decompile.py <decompile.json> [...]")
        return 2

    results = {}
    for path in paths:
        try:
            payload = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError) as exc:
            results[os.path.basename(path)] = {"error": str(exc)}
            continue
        item = payload.get("normalized_result") or payload
        procedure = (item.get("procedure") or {}).get("address") or item.get("address") or "?"
        pseudocode = item.get("pseudocode") or ""
        if not pseudocode:
            inner = (payload.get("result") or {})
            pseudocode = inner.get("pseudocode") or ""
        results[procedure] = extract(pseudocode)

    # validation on the interfaces whose keys are already known (only those supplied)
    failures = []
    checked = 0
    for procedure, expected in CHECK.items():
        if procedure not in results:
            continue
        checked += 1
        got = set(results.get(procedure, []))
        missing = [key for key in expected if key not in got]
        if missing:
            failures.append(f"{procedure}: missing {missing}")
        else:
            print(f"  OK {procedure}: {', '.join(expected)}")
    if checked == 0:
        print("REFUSING TO EMIT: none of the validation procedures were supplied")
        return 1
    if failures:
        print("REFUSING TO EMIT:")
        for line in failures:
            print("   ", line)
        return 1

    print("\n=== recovered candidate keys per procedure ===")
    for procedure in sorted(results):
        values = results[procedure]
        if isinstance(values, dict):
            print(f"  {procedure:14} ERROR {values['error']}")
        else:
            print(f"  {procedure:14} {', '.join(values) if values else '(no keys found)'}")

    out = os.path.join(WS, "analysis", "decompile-field-keys.json")
    json.dump({"scope": "candidate request-body keys recovered from decompiled pseudocode",
               "method": "inline short-string construction + s_<name>_<addr> literals",
               "caveat": "keys are construction/usage evidence; nesting and value meaning still need reading",
               "keys": results},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\nwritten:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
