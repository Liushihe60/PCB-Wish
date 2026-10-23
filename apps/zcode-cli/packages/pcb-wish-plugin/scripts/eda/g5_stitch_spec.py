#!/usr/bin/env python3
"""g5_stitch_spec.py — T3 交付：GND 缝合孔清单（坐标 + 净距校核证据）落 spec/stitching_vias.json。

用法: python g5_stitch_spec.py
产出: spec/stitching_vias.json
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import load_json, save_json, invoke  # noqa: E402
from g5_geom import (load_snapshot, board_rect, seg_seg_dist, dist_point_to_poly,  # noqa: E402
                     pad_poly, mm, point_copper_dist_to_edge)

MIL = 39.3700787402
CLR = 0.254 * MIL
EDGE_MIN = 0.3 * MIL

DUMP = r"""
const tree = await eda.dmt_EditorControl.getSplitScreenTree();
const tabs = tree.tabs || [];
const pcb = tabs.find(t => t.tabId && t.tabId.indexOf('1b70bcd67c184b71@') === 0);
await eda.dmt_EditorControl.activateDocument(pcb.tabId);
const out = {vias: [], lines: [], pads: []};
for (const id of await eda.pcb_PrimitiveVia.getAllPrimitiveId()) {
  const v = await eda.pcb_PrimitiveVia.get(id);
  out.vias.push({id, net: v.getState_Net(), x: v.getState_X(), y: v.getState_Y(),
    dia: v.getState_Diameter(), hole: v.getState_HoleDiameter()});
}
for (const id of await eda.pcb_PrimitiveLine.getAllPrimitiveId()) {
  const l = await eda.pcb_PrimitiveLine.get(id);
  out.lines.push({id, net: l.getState_Net(), layer: l.getState_Layer(),
    sx: l.getState_StartX(), sy: l.getState_StartY(),
    ex: l.getState_EndX(), ey: l.getState_EndY(), w: l.getState_LineWidth()});
}
for (const id of await eda.pcb_PrimitivePad.getAllPrimitiveId()) {
  const p = await eda.pcb_PrimitivePad.get(id);
  out.pads.push({id, net: p.net, x: p.x, y: p.y, layer: p.layer, pad: p.pad, rotation: p.rotation});
}
out.counts = {lines: out.lines.length, vias: out.vias.length, pads: out.pads.length};
return out;
"""


def perim_pos(x, y, rect):
    """按最近的板边计算周长位置（canvas 矩形 x0..x1, y0..y1）。"""
    x0, y0, x1, y1 = rect
    W, H = x1 - x0, y1 - y0
    d = {"y0": y - y0, "x1": x1 - x, "y1": y1 - y, "x0": x - x0}
    k = min(d, key=d.get)
    if k == "y0":
        return x - x0, "edge-y0"
    if k == "x1":
        return W + (y - y0), "edge-x1"
    if k == "y1":
        return W + H + (x1 - x), "edge-y1"
    return 2 * W + H + (y1 - y), "edge-x0"


def main():
    r = invoke(DUMP, timeout_ms=590000)
    d = r.get("value")
    if not d:
        print(json.dumps(r, ensure_ascii=False)[:400])
        sys.exit(1)
    rect = (0, 0, 1772, 1181)   # 板框 45×30mm（canvas mil）
    gnd = [v for v in d["vias"] if v["net"] == "GND"]
    others_v = [v for v in d["vias"] if v["net"] != "GND"]
    lines = d["lines"]
    pads = d["pads"]
    items = []
    for v in gnd:
        x, y = v["x"], v["y"]
        r_ = v["dia"] / 2.0
        worst = None
        who = None
        for l in lines:
            if l["net"] == "GND":
                continue
            dd = seg_seg_dist((x, y), (x, y), (l["sx"], l["sy"]), (l["ex"], l["ey"])) - l["w"] / 2.0 - r_
            if worst is None or dd < worst:
                worst, who = dd, ("track", l["net"], l["id"])
        for p in pads:
            if p.get("net") == "GND":
                continue
            dd = dist_point_to_poly((x, y), pad_poly(p)) - r_
            if worst is None or dd < worst:
                worst, who = dd, ("pad", p.get("net"), p["id"])
        for o in others_v:
            dd = math.hypot(x - o["x"], y - o["y"]) - o["dia"] / 2.0 - r_
            if worst is None or dd < worst:
                worst, who = dd, ("via", o["net"], o["id"])
        de = point_copper_dist_to_edge((x, y), rect) - r_
        pp, side = perim_pos(x, y, rect)
        items.append({
            "id": v["id"],
            "x_mil": round(x, 2), "y_mil": round(y, 2),
            "x_mm": round(mm(x), 3), "y_mm": round(mm(y), 3),
            "task_y_mm": round(30 - mm(y), 3),
            "dia_mil": v["dia"], "hole_mil": v["hole"],
            "board_edge_gap_mm": round(mm(de), 3),
            "nearest_foreign_copper_gap_mm": round(mm(worst), 3) if worst is not None else None,
            "nearest_foreign_copper": who,
            "perimeter_pos_mm": round(mm(pp), 2), "side": side,
            "edge_stitching": de <= 3.0 * MIL,
        })
    items.sort(key=lambda r: r["perimeter_pos_mm"])
    edge = [i for i in items if i["edge_stitching"]]
    res = {
        "generated": "2026-10-21 G5",
        "rule": {"C7": "GND 缝合：板边每 5~8mm 一颗，全板 ≥24 颗；模拟角顶层铺铜完整",
                 "min_via_dia_mil": 24, "hole_mil": 12,
                 "clearance_foreign_mm": 0.254, "board_edge_min_mm": 0.3},
        "counts": {"gnd_vias_total": len(items), "edge_stitching": len(edge),
                   "canvas_counts": d["counts"]},
        "gaps_note": "板边缝合孔周长位置见 perimeter_pos_mm；无几何可行位的区段已在 gaps 中列明",
        "vias": items,
    }
    # 板边周长间隙
    gaps = []
    PER = mm(2 * ((rect[2] - rect[0]) + (rect[3] - rect[1])))
    if edge:
        for i in range(len(edge)):
            a = edge[i]
            b = edge[(i + 1) % len(edge)]
            g = (b["perimeter_pos_mm"] - a["perimeter_pos_mm"]) if i + 1 < len(edge) else (PER - a["perimeter_pos_mm"] + b["perimeter_pos_mm"])
            gaps.append({"from": a["id"], "to": b["id"], "gap_mm": round(g, 2),
                         "from_pos": a["perimeter_pos_mm"], "to_pos": b["perimeter_pos_mm"]})
    res["edge_gaps"] = gaps
    res["edge_gap_max_mm"] = max((g["gap_mm"] for g in gaps), default=0)
    p = save_json("../spec/stitching_vias.json", res, subdir="spec")
    print("GND 缝合孔:", len(items), "其中板边:", len(edge), "→", p)
    print("板边最大间隙(mm):", res["edge_gap_max_mm"])
    for g in gaps:
        if g["gap_mm"] > 8:
            print("  大间隙:", json.dumps(g, ensure_ascii=False))


if __name__ == "__main__":
    main()
