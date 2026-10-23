#!/usr/bin/env python3
"""g5_stitch_select.py — 从候选池选板边缝合孔位（间距 5~8mm 目标），并生成放置计划。

用法: python g5_stitch_select.py [--spacing 6.5] [--out _g5_stitch_selected.json]
产出: reports/_g5_stitch_selected.json
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import load_json, save_json  # noqa: E402
from g5_geom import load_snapshot, mm  # noqa: E402

MIL = 39.3700787402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spacing", type=float, default=6.5)
    ap.add_argument("--max", type=int, default=24)
    ap.add_argument("--out", default="_g5_stitch_selected.json")
    args = ap.parse_args()
    plan = load_json("_g5_stitch_plan.json")
    snap = load_snapshot()
    ok = [c for c in plan["candidates"] if c["ok"]]
    # 已有 GND 孔（全部 22 颗既有 + 画布上新增的）
    gnd = [(v["x"], v["y"], v["id"]) for v in snap["vias"] if v.get("net") == "GND"]
    PER = plan["params"] and 150.0
    cands = sorted(ok, key=lambda c: c["perim_mm"])
    picked = []
    # 以 0 为起点，按目标间距推进；每步取最近的可行候选，且与已选孔间距 ≥ 4mm
    t = 0.0
    while t <= PER and len(picked) < args.max:
        best = None
        for c in cands:
            if abs(c["perim_mm"] - t) <= 3.0:
                if best is None or abs(c["perim_mm"] - t) < abs(best["perim_mm"] - t):
                    best = c
        if best is not None:
            # 与已选孔/既有孔的最小间距检查
            good = True
            for (px, py, pid) in gnd:
                d = math.hypot(best["x"] - px, best["y"] - py)
                if d < 4 * MIL:
                    good = False
                    break
            if good:
                picked.append(best)
                gnd.append((best["x"], best["y"], "NEW"))
                t = best["perim_mm"] + args.spacing
                continue
        t += 0.5
    res = {"spacing_target_mm": args.spacing, "count": len(picked), "vias": picked}
    save_json(args.out, res)
    print("选中缝合孔位:", len(picked))
    for c in picked:
        print(f"  perim={c['perim_mm']:6.2f}mm  ({c['x_mm']:6.2f},{c['y_mm']:6.2f})mm  "
              f"clr={c['clr_mm']}mm edge={c['edge_mm']}mm nearest={c['clr_nearest']}")


if __name__ == "__main__":
    main()
