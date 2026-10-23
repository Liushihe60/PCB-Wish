#!/usr/bin/env python3
"""ir2assert.py — 从设计 IR 生成网表断言文件（pcb-wish G1 门产物）。

设计意图唯一来源是 IR（spec/ir.json）；断言不许手抄——手抄会抄进"想要的"
而非"声明的"。本工具把 IR 的 nets[] 翻译成 netlist_assert.py 的断言：

  每个网络 → net_members（op "==" 精确成员集，抓短路多成员 + 漏连缺成员）
  可选 --with-must-connect：每个网络补一条 must_connect（人类可读冗余，可跨网拼接）

短路检测语义（2026-10-19 决策）：以 net_members "=="/"<=" 为准；
must_connect 在短路时天然 PASS，不作为短路证据。

用法:
    python ir2assert.py --ir spec/ir.json --out spec/assertions.json [--with-must-connect]
    python ir2assert.py --ir spec/ir.json --out spec/assertions.json --pinmap spec/pinmap.json
退出码: 0=生成成功, 1=IR 无 nets 或写失败。

--pinmap: 布线产物（sch_wire_from_ir.py 落盘的 ref→{功能名→符号引脚号[]}映射）。
  断言成员展开为"位号.符号引脚号"（与导出网表同命名空间）；未在映射中的成员保持原样。
  同名多引脚（如 J5.VBUS×2 焊盘）展开为全部引脚号——精确成员集断言据此核对"该并网的都并网了"。
"""
import argparse
import json
import sys
from pathlib import Path


def generate(ir, with_must_connect=False, pinmap=None):
    nets = ir.get("nets", [])
    if not nets:
        return None, "IR 中没有 nets[]，拒绝生成空断言"
    out = {
        "$comment": (
            f"由 ir2assert.py 自 {ir.get('meta', {}).get('name', '<unnamed>')} "
            "自动生成——重跑 ir2assert 覆盖本文件，人工修改会被冲掉；"
            "若需手写断言请写到独立文件。"
        ),
        "net_members": [],
    }

    def expand(member):
        if not pinmap or "." not in member:
            return [member]
        ref, _, pin = member.partition(".")
        entry = pinmap.get(ref, {}).get(pin, {})
        nums = entry.get("pinNumbers") if isinstance(entry, dict) else None
        return [f"{ref}.{n}" for n in nums] if nums else [member]

    for n in nets:
        members = []
        for m in n["members"]:
            for expanded in expand(m):
                if expanded not in members:
                    members.append(expanded)
        entry = {
            "net": n["name"],
            "members": members,
            "op": "==",
            "name": f"网络 {n['name']} 成员精确匹配（IR）",
        }
        out["net_members"].append(entry)
        if with_must_connect:
            out.setdefault("must_connect", []).append({
                "pins": list(n["members"]),
                "name": f"网络 {n['name']} 内部连通（IR）",
            })
    return out, None


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="IR → 网表断言生成器")
    ap.add_argument("--ir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--with-must-connect", action="store_true")
    ap.add_argument("--pinmap", default=None, help="sch_wire_from_ir 落盘的引脚映射（功能名→符号引脚号）")
    args = ap.parse_args()
    try:
        ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"FAIL  读取 IR 失败: {e}")
        sys.exit(1)
    pinmap = None
    if args.pinmap:
        try:
            pinmap = json.loads(Path(args.pinmap).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"FAIL  读取 pinmap 失败: {e}")
            sys.exit(1)
    out, err = generate(ir, args.with_must_connect, pinmap)
    if err:
        print(f"FAIL  {err}")
        sys.exit(1)
    Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    n = len(out["net_members"])
    extra = sum(len(v) for k, v in out.items() if k == "must_connect")
    print(f"OK    生成 {args.out}: net_members×{n}" + (f"，must_connect×{extra}" if extra else ""))
    print("      下游: python netlist_assert.py --netlist <导出网表> --assertions " + args.out)
    sys.exit(0)


if __name__ == "__main__":
    main()
