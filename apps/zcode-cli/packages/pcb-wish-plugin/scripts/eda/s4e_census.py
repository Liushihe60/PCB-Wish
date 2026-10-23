#!/usr/bin/env python3
"""s4e_census.py — T1 网级归属账（各网线/孔/总长）+ 孔位同位簇扫描。

输入: reports/_s4e_snapshot_pre_auto.json
输出: reports/_s4e_pre_cleanup_census.json
口径:
  - 几何总长 = 各段端点欧氏长（canvas mil），非 pcb_Net 长度口径（后者高估连通）
  - 同位簇: 同网同坐标 (x,y) 多于 1 颗孔 = 重复孔
  - 孤立孔: 该网除 GND 外其所有孔在同网走线几何上无任何端点/重合（粗判）
"""
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path("E:/agent project/projects/flexiheal-v1")
SNAP = HERE / "reports/_s4e_snapshot_pre_auto.json"
OUT = HERE / "reports/_s4e_pre_cleanup_census.json"
PLAN = HERE / "spec/route_plan.json"


def main():
    snap = json.loads(SNAP.read_text(encoding="utf-8"))
    lines = [l for l in snap["lines"] if not l.get("err")]
    vias = [v for v in snap["vias"] if not v.get("err")]

    plan_nets = [n["name"] for n in json.loads(PLAN.read_text(encoding="utf-8"))["nets"]]
    plan_nets = sorted(set(plan_nets))

    by_net_lines = defaultdict(list)
    for l in lines:
        by_net_lines[l.get("net")].append(l)
    by_net_vias = defaultdict(list)
    for v in vias:
        by_net_vias[v.get("net")].append(v)

    nets = sorted(set(list(by_net_lines) + list(by_net_vias)))
    census = {}
    for n in nets:
        ls = by_net_lines[n]
        vs = by_net_vias[n]
        geo_len = sum(math.hypot(l["ex"] - l["sx"], l["ey"] - l["sy"]) for l in ls)
        layers = Counter(l.get("layer") for l in ls)
        widths = Counter(round(l.get("w") or 0, 3) for l in ls)
        census[n] = {
            "lines": len(ls),
            "vias": len(vs),
            "geo_len_mil": round(geo_len, 2),
            "geo_len_mm": round(geo_len / 39.37, 3),
            "pcb_Net_len": snap.get("netLen", {}).get(n, 0),
            "layers": dict(layers),
            "widths": {str(k): v for k, v in widths.items()},
            "in_route_plan": n in plan_nets,
            "line_ids": [l["id"] for l in ls],
            "via_ids": [v["id"] for v in vs],
            "via_pos": [{"id": v["id"], "x": v.get("x"), "y": v.get("y"),
                         "hole": v.get("hole"), "dia": v.get("dia")} for v in vs],
        }

    # 同位重复孔簇（几何判定，pitfalls #20）
    dup_clusters = []
    for n, vs in by_net_vias.items():
        g = defaultdict(list)
        for v in vs:
            g[(round(v.get("x", 0), 2), round(v.get("y", 0), 2))].append(v)
        for (x, y), gv in g.items():
            if len(gv) > 1:
                dup_clusters.append({
                    "net": n, "x": x, "y": y, "count": len(gv),
                    "ids": [v["id"] for v in gv],
                    "hole": gv[0].get("hole"), "dia": gv[0].get("dia"),
                })

    # GND 地基识别：GND 网全部线/孔 → 保留
    gnd = census.get("GND", {})
    gnd_lines = by_net_lines.get("GND", [])
    gnd_vias = by_net_vias.get("GND", [])

    # GND 孔是否落在板边 5~8mm 带（G5 缝合口径参考）
    gnd_edge = []
    for v in gnd_vias:
        gnd_edge.append({"id": v["id"], "x": v.get("x"), "y": v.get("y")})

    out = {
        "generated": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "canvas_counts": snap.get("counts"),
        "probe_consistent": snap.get("probe_consistent"),
        "route_plan_nets": plan_nets,
        "route_plan_net_count": len(plan_nets),
        "nets_on_canvas": nets,
        "census": census,
        "dup_via_clusters": dup_clusters,
        "gnd_foundation": {
            "lines": len(gnd_lines),
            "vias": len(gnd_vias),
            "line_ids": [l["id"] for l in gnd_lines],
            "via_ids": [v["id"] for v in gnd_vias],
            "via_pos": gnd_edge,
        },
        "totals": {
            "lines": sum(c["lines"] for c in census.values()),
            "vias": sum(c["vias"] for c in census.values()),
            "non_gnd_lines": sum(c["lines"] for k, c in census.items() if k != "GND"),
            "non_gnd_vias": sum(c["vias"] for k, c in census.items() if k != "GND"),
            "gnd_lines": gnd.get("lines", 0),
            "gnd_vias": gnd.get("vias", 0),
        },
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("落盘:", OUT)
    print("totals:", json.dumps(out["totals"], ensure_ascii=False))
    print("dup_via_clusters:", json.dumps(dup_clusters, ensure_ascii=False))
    print("nets on canvas:", len(nets))
    missing = [n for n in plan_nets if n not in nets]
    print("plan nets not on canvas:", missing)


if __name__ == "__main__":
    main()
