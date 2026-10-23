#!/usr/bin/env python3
"""g5_via_size.py — T5.1：14 条 Via Size（外径 16/20mil < 项目规则 24mil）逐条可行性核算。

规则：Via 外径 ≥24mil（项目规则 viaSize）；净距 Track↔Via / Pad↔Via / Via↔Via ≥0.254mm。
改几何只走"删除+重建"（pitfalls #42/#43）。

用法: python g5_via_size.py
产出: reports/_g5_via_size_plan.json
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import load_json, save_json  # noqa: E402
from g5_geom import (load_snapshot, seg_seg_dist, dist_point_to_poly, pad_poly, mm)  # noqa: E402

MIL = 39.3700787402
NEW_DIA = 24.0
CLR = 0.254 * MIL
VIA_R = NEW_DIA / 2.0


def main():
    snap = load_snapshot()
    small = [v for v in snap["vias"] if (v.get("dia") or 0) < 24]
    tracks = [l for l in snap["lines"]]
    pads = [p for p in snap["pads"]]
    vias = [v for v in snap["vias"]]
    out = []
    for v in small:
        x, y, old_r = v["x"], v["y"], v["dia"] / 2.0
        worst = None
        who = None
        for l in tracks:
            if l["net"] == v["net"]:
                continue
            d = seg_seg_dist((x, y), (x, y), (l["sx"], l["sy"]), (l["ex"], l["ey"])) - l["w"] / 2.0 - NEW_R if False else 0
            d = seg_seg_dist((x, y), (x, y), (l["sx"], l["sy"]), (l["ex"], l["ey"])) - l["w"] / 2.0 - VIA_R
            if worst is None or d < worst:
                worst, who = d, ("track", l["net"], l["id"], l["layer"])
        for p in pads:
            if p.get("net") == v["net"]:
                continue
            d = dist_point_to_poly((x, y), pad_poly(p)) - VIA_R
            if worst is None or d < worst:
                worst, who = d, ("pad", p.get("net"), p["id"], p["layer"])
        for o in vias:
            if o["id"] == v["id"] or o["net"] == v["net"]:
                continue
            d = math.hypot(x - o["x"], y - o["y"]) - o["dia"] / 2.0 - VIA_R
            if worst is None or d < worst:
                worst, who = d, ("via", o["net"], o["id"], None)
        out.append({
            "id": v["id"], "net": v["net"], "x": x, "y": y,
            "x_mm": round(mm(x), 3), "y_mm": round(mm(y), 3),
            "old_dia": v["dia"], "old_hole": v["hole"],
            "new_dia": NEW_DIA,
            "gap_with_new_dia_mm": round(mm(worst), 4) if worst is not None else None,
            "nearest": who,
            "feasible": bool(worst is not None and worst >= CLR),
        })
    feas = [o for o in out if o["feasible"]]
    infeas = [o for o in out if not o["feasible"]]
    res = {"rule_min_via_dia_mil": 24, "clr_mm": 0.254, "items": out,
           "feasible": [o["id"] for o in feas], "infeasible": [o["id"] for o in infeas],
           "counts": {"total": len(out), "feasible": len(feas), "infeasible": len(infeas)}}
    save_json("_g5_via_size_plan.json", res)
    print("小孔 14 条 → 可行", len(feas), "/ 不可行", len(infeas))
    for o in out:
        print(f"  {o['net']:10s} ({o['x_mm']:6.2f},{o['y_mm']:6.2f}) dia {o['old_dia']}->24 "
              f"gap={o['gap_with_new_dia_mm']}mm nearest={o['nearest'][:2] if o['nearest'] else None} "
              f"{'OK' if o['feasible'] else 'X'}")


if __name__ == "__main__":
    main()
