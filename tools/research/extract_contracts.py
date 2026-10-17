"""Derive per-interface request/response contracts from the client's own logs.

Log grammar observed in data.log (request dump on one line):

  ... HTTP:<x> reqimpl https:<x> /s/json?func=<module>:<method>&<q>), port(443),
      proxy(...), ssl(...), body({"attrIds":...})

Responses are logged separately as `res({...})`, `msg:{"code":...}`,
`prepare fetch : {"code":...}`, `get folder : {"code":...}`, etc.

This script keeps only key names and value types. Scalars, addresses, subjects
and message bodies are never written to the output.
"""
from __future__ import annotations

import collections
import json
import os
import re

LOGS = r"C:\Users\wangbiao\AppData\Roaming\Cm\CMClient\logs"
OUT = r"D:\workspace\redocoreemail\analysis\runtime-interface-contracts.json"

IDENT = r"[A-Za-z][A-Za-z0-9_]*\.?[A-Za-z0-9_]*:[A-Za-z][A-Za-z0-9_]*"
RE_FUNC = re.compile(r"func=(" + IDENT + r")")
RE_BODY = re.compile(r"\bbody\(")
RE_RESP = re.compile(r'\{"code"')
RE_DATE = re.compile(r"!!date\s*'[^']*'")
DECODER = json.JSONDecoder()
MAX_KEYS, DEPTH, MAX_SHAPE = 60, 7, 4000
ATTRIBUTION_WINDOW = 2500


def sig(value, depth=0) -> str:
    if depth > DEPTH:
        return "..."
    if isinstance(value, dict):
        items = sorted(value.items())[:MAX_KEYS]
        more = ",..." if len(value) > MAX_KEYS else ""
        return "{" + ",".join(f"{k}:{sig(v, depth + 1)}" for k, v in items) + more + "}"
    if isinstance(value, list):
        return "[" + (sig(value[0], depth + 1) if value else "") + "]"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if value is None:
        return "null"
    return "str"


def parse_at(text: str, pos: int):
    window = text[pos:pos + 200000]
    window = RE_DATE.sub('"D"', window)
    try:
        value, _ = DECODER.raw_decode(window, 0)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def first_object(text: str, start: int, window: int):
    chunk = RE_DATE.sub('"D"', text[start:start + window])
    pos = 0
    for _ in range(6):
        pos = chunk.find("{", pos)
        if pos < 0:
            return None
        try:
            value, end = DECODER.raw_decode(chunk, pos)
        except ValueError:
            pos += 1
            continue
        if isinstance(value, dict):
            return value
        pos = end
    return None


def is_response_envelope(value: dict) -> bool:
    keys = set(value)
    return "code" in keys and keys <= {"code", "var", "draftId", "total", "groupings", "keywords"}


def main():
    req_shapes = collections.defaultdict(collections.Counter)
    res_shapes = collections.defaultdict(collections.Counter)
    res_unattributed = collections.Counter()
    calls = collections.Counter()
    files = 0

    for name in sorted(os.listdir(LOGS)):
        path = os.path.join(LOGS, name)
        if not os.path.isfile(path):
            continue
        files += 1
        text = open(path, "rb").read().decode("utf-8", "ignore")

        func_at = [(m.start(), m.end(), m.group(1)) for m in RE_FUNC.finditer(text)]
        for index, (_start, end, func) in enumerate(func_at):
            calls[func] += 1
            # The request body follows the call line, but a response body may be
            # logged in between; take the first non-envelope object before the
            # next call. Bodies are only logged for a subset of requests.
            # The first parseable JSON object after the call is labelled as the
            # request body or the response envelope; the client logs both kinds
            # near the call and not always after an explicit `body(` marker.
            found = False
            value = first_object(text, end, 4000)
            if value is not None:
                s = sig(value)
                if len(s) <= MAX_SHAPE:
                    req_shapes[func]["envelope " + s if is_response_envelope(value) else "request  " + s] += 1
                    found = True
            if not found:
                req_shapes[func]["(no body parsed)"] += 1

        for m in RE_RESP.finditer(text):
            value = parse_at(text, m.start())
            if value is None:
                continue
            s = sig(value)
            if len(s) > MAX_SHAPE:
                continue
            # nearest preceding func=; only attribute when nothing intervenes
            owner, best = None, -1
            for start, _end, func in func_at:
                if start < m.start():
                    owner, best = func, start
                else:
                    break
            if owner is not None and m.start() - best <= ATTRIBUTION_WINDOW:
                res_shapes[owner][s] += 1
            else:
                res_unattributed[s] += 1

    report = {
        "scope": ("client log files; request/response key names and value types only; "
                  "no scalar values, addresses, subjects or bodies retained"),
        "files_scanned": files,
        "grammar": {
            "request": ".../s/json?func=<module>:<method>&...), port(...), body({...})",
            "response": "res({...}) | msg:{\"code\":...} | <label> : {\"code\":...}",
        },
        "interfaces": {
            func: {
                "calls": calls[func],
                "request_shapes": [{"shape": s, "count": c} for s, c in req_shapes[func].most_common(4)],
                "response_shapes": [{"shape": s, "count": c} for s, c in res_shapes[func].most_common(4)],
                "response_attribution": ("nearest preceding func= within 2500 bytes; "
                                         "concurrent log interleaving can misattribute"),
            }
            for func in sorted(calls, key=lambda f: -calls[f])
        },
        "unattributed_response_shapes": [
            {"shape": s, "count": c} for s, c in res_unattributed.most_common(20)
        ],
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    print(f"files={files} interfaces={len(calls)}")
    for func in sorted(calls, key=lambda f: -calls[f]):
        reqs = req_shapes[func].most_common(1)
        ress = res_shapes[func].most_common(1)
        print(f"\n{func}  (calls={calls[func]})")
        print("  req:", (reqs[0][0][:220] if reqs else "-"))
        print("  res:", (ress[0][0][:220] if ress else "-"))
    print("\nwritten:", OUT)


if __name__ == "__main__":
    main()
