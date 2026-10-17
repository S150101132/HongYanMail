"""Minimal read-only ASAR extractor: list or dump one member without unpacking the archive."""
from __future__ import annotations

import json
import struct
import sys

PATH = r"D:\work\DeepSeek-Harness\APP\resources\app.asar"


def read_header(path: str):
    with open(path, "rb") as fh:
        prelude = fh.read(16)
        _, header_size, _, json_size = struct.unpack("<4I", prelude)
        header = json.loads(fh.read(json_size).decode("utf-8"))
    return header, 8 + header_size


def walk(node, prefix=""):
    for name, value in node.get("files", {}).items():
        current = f"{prefix}/{name}" if prefix else name
        if "files" in value:
            yield from walk(value, current)
        else:
            yield current, value


def main():
    header, base = read_header(PATH)
    members = dict(walk(header))
    needle = sys.argv[1] if len(sys.argv) > 1 else None
    if needle is None:
        for name in sorted(members):
            if "mcp-client" in name:
                print(name)
        return
    if needle not in members:
        print("NOT FOUND:", needle)
        return
    entry = members[needle]
    with open(PATH, "rb") as fh:
        fh.seek(base + int(entry["offset"]))
        data = fh.read(int(entry["size"]))
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(data.decode("utf-8", "replace"))


if __name__ == "__main__":
    main()
