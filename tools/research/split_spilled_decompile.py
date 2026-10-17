import json
import os

spill = (r"C:\Users\wangbiao\AppData\Local\Temp\dsh-spill-AtIUVu\session-3b10b52e429b"
         r"\86bec8f82dfe-mcp__rea__batch_decompile.txt")
raw = open(spill, encoding="utf-8", errors="replace").read()
print("first 120 chars:", raw[:120].replace("\n", " "))
try:
    payload = json.loads(raw)
    print("parsed JSON, top keys:", list(payload)[:10])
except ValueError as exc:
    print("not plain JSON:", exc)
    payload = None

if payload:
    items = None
    for key in ("normalized_result", "result"):
        node = payload.get(key)
        if isinstance(node, dict) and "items" in node:
            items = node["items"]
            break
    print("items:", len(items) if items else 0)
    outdir = r"D:\workspace\redocoreemail\analysis\rea-native\spill-split"
    os.makedirs(outdir, exist_ok=True)
    for item in items or []:
        addr = (item.get("procedure") or {}).get("address", "unknown")
        path = os.path.join(outdir, addr.replace("0x", "") + ".json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"normalized_result": {"procedure": item.get("procedure"),
                                             "pseudocode": item.get("pseudocode", "")}},
                      handle, ensure_ascii=False, indent=2)
        print("wrote", os.path.basename(path), len(item.get("pseudocode") or ""), "bytes")
