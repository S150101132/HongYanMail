"""Pair Coremail request and response lines by log thread id.

Log line grammar (observed):
  T:<tid>(<hh:mm:ss>)[<tag>:<level>] HTTP:<n> reqimpl https:<x> /s/json?func=<m>:<k>&<q>,
      port(443), proxy(...), ssl(...), body({...})
  T:<tid>(<hh:mm:ss>)[<tag>:<level>] ... res({...}) | msg:{"code":...} | <label> : {"code":...}

By keeping the last request seen per thread id and attaching the next JSON
object with a "code" member seen on the same thread, response attribution is
far more reliable than byte proximity across interleaved threads.

Only key names and value types are retained; scalars, addresses, subjects and
bodies are never written to the output.
"""
from __future__ import annotations

import collections
import json
import os
import re

LOGS = r"C:\Users\wangbiao\AppData\Roaming\Cm\CMClient\logs"
OUT = r"D:\workspace\redocoreemail\analysis\runtime-interface-contracts.json"

IDENT = r"[A-Za-z][A-Za-z0-9_]*\.?[A-Za-z0-9_]*:[A-Za-z][A-Za-z0-9_]*"
RE_TID = re.compile(r"^T:(\d+)\(")
RE_FUNC = re.compile(r"func=(" + IDENT + r")")
RE_PARAM = re.compile(r"[&?]([A-Za-z_][A-Za-z0-9_]{1,32})=")
RE_BODY = re.compile(r"body\(")
RE_CODE = re.compile(r'\{"code"')
RE_DATE = re.compile(r"!!date\s*'[^']*'")
DECODER = json.JSONDecoder()
MAX_KEYS, DEPTH, MAX_SHAPE = 60, 7, 4000
MAX_LINE_DISTANCE = 4


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


def parse_json_at(text: str, pos: int):
    window = RE_DATE.sub('"D"', text[pos:pos + 200000])
    try:
        value, _ = DECODER.raw_decode(window, 0)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def is_envelope(value: dict) -> bool:
    keys = set(value)
    return "code" in keys and keys <= {"code", "var", "draftId", "total", "groupings", "keywords"}


def main():
    calls = collections.Counter()
    req_params = collections.defaultdict(collections.Counter)
    req_shapes = collections.defaultdict(collections.Counter)
    res_shapes = collections.defaultdict(collections.Counter)
    paired = collections.Counter()
    files = 0

    for name in sorted(os.listdir(LOGS)):
        path = os.path.join(LOGS, name)
        if not os.path.isfile(path):
            continue
        files += 1
        text = open(path, "rb").read().decode("utf-8", "ignore")
        last_req = {}  # tid -> (func, line_index)

        for index, line in enumerate(text.splitlines()):
            tid_match = RE_TID.match(line)
            tid = tid_match.group(1) if tid_match else None

            func_match = RE_FUNC.search(line)
            if func_match:
                func = func_match.group(1)
                calls[func] += 1
                last_req[tid] = (func, index)
                head = line[func_match.end():].split("), port(")[0]
                for param in RE_PARAM.findall(head):
                    req_params[func][param] += 1
                body_match = RE_BODY.search(line)
                if body_match:
                    value = parse_json_at(line, body_match.end())
                    if value is not None and not is_envelope(value):
                        s = sig(value)
                        if len(s) <= MAX_SHAPE:
                            req_shapes[func][s] += 1

            for code_match in RE_CODE.finditer(line):
                pending = last_req.get(tid)
                if pending is None:
                    continue
                func, req_index = pending
                # a response logged far from its request on the same thread is
                # more likely a different operation's late completion
                if index - req_index > MAX_LINE_DISTANCE:
                    continue
                value = parse_json_at(line, code_match.start())
                if value is None:
                    continue
                s = sig(value)
                if len(s) <= MAX_SHAPE:
                    res_shapes[func][s] += 1
                    paired[func] += 1

    report = {
        "scope": ("client log files; request query-parameter names, request body types and "
                  "thread-paired response envelope types only; no scalar values retained"),
        "method": "response attributed to the last func= seen on the same log thread id (T:<tid>)",
        "files_scanned": files,
        "interfaces": {
            func: {
                "calls": calls[func],
                "query_parameters": dict(req_params[func].most_common(30)),
                "request_shapes": [{"shape": s, "count": c} for s, c in req_shapes[func].most_common(4)],
                "response_shapes": [{"shape": s, "count": c} for s, c in res_shapes[func].most_common(4)],
                "paired_responses": paired[func],
            }
            for func in sorted(calls, key=lambda f: -calls[f])
        },
    }
    json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"files={files} interfaces={len(calls)}")
    total_paired = sum(paired.values())
    print(f"thread-paired responses: {total_paired}")
    for func in sorted(calls, key=lambda f: -paired[f])[:20]:
        params = ",".join(list(req_params[func])[:8])
        res = (res_shapes[func].most_common(1) or [("-", 0)])[0][0]
        print(f"{func:30} calls={calls[func]:6} paired={paired[func]:5} params[{params}]")
        if res != "-":
            print(f"{'':30} res {res[:150]}")
    print("written:", OUT)


if __name__ == "__main__":
    main()
