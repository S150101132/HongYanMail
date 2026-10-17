"""Compliance check for the interface inventory deliverable.

Verifies that every interface row in 02 carries the fields the objective requires:
名称 / 用途 / 协议 / 端点或命令 / 请求参数 / 响应结构 / 认证依赖 / 调用代码位置 /
证据 / 可信度 / 验证状态
"""
from __future__ import annotations

import os
import re
import sys

WS = r"D:\workspace\redocoreemail"
DOC = os.path.join(WS, "02-通信协议与接口清单.md")

REQUIRED = ["名称", "用途", "协议", "端点或命令", "请求参数", "响应结构",
            "认证依赖", "调用代码位置", "证据", "可信度", "验证状态"]

DELIVERABLES = ["01-程序结构.md", "02-通信协议与接口清单.md", "03-认证与同步流程.md",
                "04-接口证据与验证记录.md", "05-客户端MVP设计.md", "06-待验证问题.md"]


def main():
    print("=== 交付物存在性 ===")
    for name in DELIVERABLES:
        path = os.path.join(WS, name)
        size = os.path.getsize(path) if os.path.exists(path) else 0
        status = "OK" if size > 2000 else "MISSING/TOO SMALL"
        print(f"  {name:32} {size:>8,} B  {status}")

    lines = open(DOC, encoding="utf-8").read().splitlines()
    header = next((l for l in lines if l.startswith("| 名称 |")), None)
    print("\n=== 表头字段 ===")
    if not header:
        print("  FAIL: 未找到接口总表表头")
        return 1
    columns = [c.strip().strip("*") for c in header.strip("|").split("|")]
    print("  实际列:", columns)
    missing = [c for c in REQUIRED if c not in columns]
    print("  必需字段缺失:", missing or "无")
    width = len(columns)

    bad_rows = []
    interface_rows = 0
    in_section = False
    for line in lines:
        if line.startswith("## 2. 接口总表"):
            in_section = True
            continue
        if line.startswith("## 3."):
            in_section = False
        if not in_section:
            continue
        if not re.match(r"^\| `[a-z][a-z0-9_.]*:[A-Za-z]", line):
            continue
        interface_rows += 1
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != width:
            bad_rows.append((cells[0], len(cells)))
        elif any(c == "" for c in cells):
            bad_rows.append((cells[0], "empty cell"))
    print(f"\n=== 接口行校验 ===\n  行数={interface_rows}  列宽={width}  异常={len(bad_rows)}")
    for row in bad_rows[:10]:
        print("   ", row)

    # how many rows carry real (non-placeholder) evidence, for EVERY required column
    filled = {name: 0 for name in REQUIRED}
    idx = {name: columns.index(name) for name in filled if name in columns}
    in_section = False
    for line in lines:
        if line.startswith("## 2. 接口总表"):
            in_section = True
            continue
        if line.startswith("## 3."):
            in_section = False
        if not in_section:
            continue
        if not re.match(r"^\| `[a-z][a-z0-9_.]*:[A-Za-z]", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != width:
            continue
        for name, position in idx.items():
            value = cells[position]
            if value and not value.startswith("未知") and "未可靠记录" not in value \
                    and value != "未定位" and "待确认" not in value:
                filled[name] += 1
    print("\n=== 逐列填充率（全部要求列）===")
    for name in REQUIRED:
        count = filled.get(name, 0)
        flag = "" if count == interface_rows else ("  <-- 缺口" if count * 2 < interface_rows else "")
        print(f"  {name:14} {count:>3}/{interface_rows}{flag}")

    # stronger signal: rows whose request-parameter cell carries real field evidence
    # (decompiled key construction or a real interactive call), not just `sid`
    strong = 0
    for line in lines:
        if not re.match(r"^\| `[a-z][a-z0-9_.]*:[A-Za-z]", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != width:
            continue
        value = cells[idx["请求参数"]]
        if "反编译实证" in value or "真机验证" in value or "精确格式" in value:
            strong += 1
    print(f"\n  请求参数中带「字段级实证」（反编译键构造 / 真机验证 / 精确 URL 格式）的行: "
          f"{strong}/{interface_rows}")

    ok = not missing and not bad_rows
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
