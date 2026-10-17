"""Collect candidate client<->mail-server interface names from local evidence.

Sources (all read-only):
  1. native binary strings   (CMClient.exe)
  2. installed web resources (C:\\Program Files\\cmclient\\app  *.js/*.html)
  3. runtime logs            (data.log*/cmcef.log* under the CMClient data dir)
  4. public webmail bundles  (analysis/web-login/*.js) as a cross-check

Only interface identifiers and HTTP paths are written out. Log lines, sids,
tokens and mail content are never copied into the result file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict

# module:method identifiers, e.g. mbox:getAllFolders, cim.user:getLunkrMailData
IDENT = r"[A-Za-z][A-Za-z0-9_]*\.?[A-Za-z0-9_]*:[A-Za-z][A-Za-z0-9_]*"
RE_FUNC_EQ = re.compile(r"func=(" + IDENT + r")")
RE_FUNC_JS = re.compile(r"""func["']?\s*[:=]\s*["'](""" + IDENT + r""")["']""")
RE_FUNC_JSON = re.compile(r'"(?:func|function)"\s*:\s*"(' + IDENT + r')"')
RE_FUNC_BARE = re.compile(r"^(" + IDENT + r")$")
RE_JSP = re.compile(r"/coremail/[A-Za-z0-9_\-/\.]{2,80}\.jsp")
RE_CM_PATH = re.compile(r"/coremail/[A-Za-z0-9_\-/\.]{2,60}")

ASCII_RUN = re.compile(rb"[\x20-\x7e]{6,}")
UTF16_RUN = re.compile(rb"(?:[\x20-\x7e]\x00){6,}")


def native_strings(path: str):
    with open(path, "rb") as fh:
        data = fh.read()
    for m in ASCII_RUN.finditer(data):
        yield m.group().decode("ascii", "ignore")
    for m in UTF16_RUN.finditer(data):
        yield m.group().decode("utf-16-le", "ignore")


def text_chunks(path: str):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return
    yield raw.decode("utf-8", "ignore")
    if b"\x00" in raw[:4096]:
        yield raw.decode("utf-16-le", "ignore")


def iter_files(root: str, exts=None):
    for base, _dirs, names in os.walk(root):
        for n in names:
            if exts is None or n.lower().endswith(exts):
                yield os.path.join(base, n)


def harvest(chunks, src: str, sinks, bare: bool = True):
    for text in chunks:
        for m in RE_FUNC_EQ.finditer(text):
            sinks["funcs"][m.group(1)].add(src)
        for m in RE_FUNC_JS.finditer(text):
            sinks["funcs"][m.group(1)].add(src)
        for m in RE_FUNC_JSON.finditer(text):
            sinks["funcs"][m.group(1)].add(src)
        for m in RE_JSP.finditer(text):
            sinks["jsp"].add(m.group(0))
        for m in RE_CM_PATH.finditer(text):
            sinks["paths"].add(m.group(0))
        if bare:
            # native code stores the RPC name as a standalone literal
            for m in RE_FUNC_BARE.finditer(text.strip()):
                sinks["funcs"][m.group(1)].add(src)
    return sinks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--native", default=r"C:\Program Files\cmclient\CMClient.exe")
    ap.add_argument("--cefapp", default=r"C:\Program Files\cmclient\CMCefApp.exe")
    ap.add_argument("--app", default=r"C:\Program Files\cmclient\app")
    ap.add_argument("--logs", default=r"C:\Users\wangbiao\AppData\Roaming\Cm\CMClient\logs")
    ap.add_argument("--web", default=os.path.join(os.path.dirname(__file__), "..", "..", "analysis", "web-login"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "..", "analysis", "server-api-inventory.json"))
    args = ap.parse_args()

    sinks = {"funcs": defaultdict(set), "jsp": set(), "paths": set()}

    if os.path.exists(args.native):
        harvest(native_strings(args.native), "native:CMClient.exe", sinks)
    if os.path.exists(args.cefapp):
        harvest(native_strings(args.cefapp), "native:CMCefApp.exe", sinks)
    if os.path.isdir(args.app):
        for p in iter_files(args.app, (".js", ".html", ".htm", ".json")):
            harvest(text_chunks(p), "app:" + os.path.relpath(p, args.app), sinks)
    if os.path.isdir(args.logs):
        for p in iter_files(args.logs):
            harvest(text_chunks(p), "log:" + os.path.basename(p), sinks, bare=False)
    if os.path.isdir(args.web):
        for p in iter_files(args.web, (".js", ".html")):
            harvest(text_chunks(p), "public:" + os.path.basename(p), sinks)

    funcs = {k: sorted(v) for k, v in sorted(sinks["funcs"].items())}
    report = {
        "sources": {
            "native": args.native,
            "app": args.app,
            "logs": args.logs,
            "public_web": os.path.abspath(args.web),
        },
        "counts": {
            "func_names": len(funcs),
            "jsp_paths": len(sinks["jsp"]),
            "coremail_paths": len(sinks["paths"]),
            "modules": len({k.split(":")[0] for k in funcs}),
        },
        "funcs": funcs,
        "jsp_paths": sorted(sinks["jsp"]),
        "coremail_paths": sorted(sinks["paths"]),
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=False)
    print(json.dumps(report["counts"], indent=2))
    print("written:", os.path.abspath(args.out))


if __name__ == "__main__":
    main()
