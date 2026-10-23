#!/usr/bin/env python3
"""g5_analyze_drc.py — 解析 S4E 净 DRC（基线），定位 Via Size / Clearance 条目的具体图元。

用法: python g5_analyze_drc.py
产出: reports/_g5_drc_baseline_analysis.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import load_json, save_json  # noqa: E402
from g5_geom import (load_snapshot, board_rect, objs_by_layer, mm,  # noqa: E402
                     track_copper_dist_to_edge, seg_seg_dist, pad_poly, dist_point_to_poly)

drc = load_json("_s4e_drc.json")
snap = load_snapshot()
rect = board_rect(snap)
by_id = {}
for l in snap["lines"]:
    by_id[l["id"]] = ("line", l)
for v in snap["vias"]:
    by_id[v["id"]] = ("via", v)
for p in snap["pads"]:
    by_id[p["id"]] = ("pad", p)

out = {"board_rect_mil": rect, "board_rect_mm": [mm(rect[0]), mm(rect[1]), mm(rect[2]), mm(rect[3])],
       "items": []}

for it in drc["items"]:
    if it["category"] == "Connection Error":
        continue
    rec = {"category": it["category"], "type": it["type"], "net": it.get("net"),
           "objs": it.get("objs"), "pos": it.get("pos"), "detail": []}
    for oid in (it.get("objs") or []):
        k = by_id.get(oid)
        if k is None:
            rec["detail"].append({"id": oid, "kind": "outline/unknown"})
            continue
        kind, o = k
        if kind == "line":
            d_edge = track_copper_dist_to_edge(o, rect)
            rec["detail"].append({"id": oid, "kind": "line", "net": o["net"], "layer": o["layer"],
                                  "sx": o["sx"], "sy": o["sy"], "ex": o["ex"], "ey": o["ey"],
                                  "w": o["w"], "w_mm": round(mm(o["w"]), 4),
                                  "edge_dist_mil": round(d_edge, 2),
                                  "edge_dist_mm": round(mm(d_edge), 4)})
        elif kind == "via":
            rec["detail"].append({"id": oid, "kind": "via", "net": o["net"], "x": o["x"], "y": o["y"],
                                  "dia": o["dia"], "dia_mm": round(mm(o["dia"]), 4),
                                  "hole": o["hole"], "hole_mm": round(mm(o["hole"]), 4)})
        elif kind == "pad":
            rec["detail"].append({"id": oid, "kind": "pad", "net": o["net"], "x": o["x"], "y": o["y"],
                                  "layer": o["layer"], "pad": o.get("pad")})
    out["items"].append(rec)

# 板边最近的 12 条走线
edge = []
for l in snap["lines"]:
    d = track_copper_dist_to_edge(l, rect)
    edge.append({"id": l["id"], "net": l["net"], "layer": l["layer"], "w_mm": round(mm(l["w"]), 4),
                 "edge_mm": round(mm(d), 4),
                 "sx": l["sx"], "sy": l["sy"], "ex": l["ex"], "ey": l["ey"]})
edge.sort(key=lambda x: x["edge_mm"])
out["tracks_closest_to_board_edge"] = edge[:15]

# 所有过孔尺寸分布
from collections import Counter
out["via_dia_hist"] = {str(k): v for k, v in Counter(
    (v.get("dia"), v.get("hole")) for v in snap["vias"]).items()}
small_vias = [v for v in snap["vias"] if (v.get("dia") or 0) < 24]
out["vias_dia_lt_24mil"] = [{"id": v["id"], "net": v["net"], "x": v["x"], "y": v["y"],
                              "dia": v["dia"], "hole": v["hole"],
                              "x_mm": mm(v["x"]), "y_mm": mm(v["y"])} for v in small_vias]
out["small_via_count"] = len(small_vias)

save_json("_g5_drc_baseline_analysis.json", out)
print("非连接类条目:", len(out["items"]))
for r in out["items"]:
    print(json.dumps(r, ensure_ascii=False)[:400])
print("--- 板边最近走线 ---")
for e in out["tracks_closest_to_board_edge"][:8]:
    print(json.dumps(e, ensure_ascii=False))
print("--- 小过孔 ---", out["small_via_count"], json.dumps(out["vias_dia_lt_24mil"][:20], ensure_ascii=False)[:1200])
