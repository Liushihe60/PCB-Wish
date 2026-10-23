#!/usr/bin/env python3
"""s2_pinmap_regen.py — S2 原理图站：从画布实读重生成 spec/pinmap.json（D021 删除后）。

只做解析与落盘，不放置任何图元。复用 sch_wire_from_ir 的解析器（同一套别名表），
画布扫描走 59 幸存器件实读，保证"以原理图实读为准"。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import run_code  # noqa: E402
from sch_wire_from_ir import SCAN_JS, resolve_pins  # noqa: E402


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--ir", default="spec/ir.json")
    ap.add_argument("--pinmap", default="spec/pinmap.json")
    ap.add_argument("--out-report", default="reports/_s2_pinmap_regen.json")
    args = ap.parse_args()

    sid = __import__("os").environ["PCB_WISH_EDA_SESSION"]
    ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    scan = run_code(SCAN_JS, sid, timeout_ms=300000)
    if not scan.get("ok"):
        print(json.dumps(scan, ensure_ascii=False)[:300])
        sys.exit(2)
    canvas = scan["value"]
    print(f"# 画布器件 {len(canvas)} 件", file=sys.stderr)

    taps, pinmap, unresolved = resolve_pins(ir, canvas)
    Path(args.pinmap).write_text(json.dumps(pinmap, ensure_ascii=False, indent=2), encoding="utf-8")
    rep = {"canvas_devices": len(canvas), "taps_resolved": len(taps),
           "pinmap_refs": len(pinmap), "unresolved": unresolved}
    Path(args.out_report).write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"pinmap 重生成: {len(pinmap)} refs / {len(taps)} 挂网 / 未解析 {len(unresolved)} → {args.pinmap}")
    for u in unresolved:
        print(f"  [未解析] {u['member']} @ {u['net']}: {u['why'][:70]}", file=sys.stderr)
    sys.exit(0 if not unresolved else 1)


if __name__ == "__main__":
    main()
