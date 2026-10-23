#!/usr/bin/env python3
"""g5_islands.py — 分析残余 18 个 GND 连接错误项：为何铺铜接不到（邻铜距离/逃逸走廊）。

口径：铺铜对异网铜让出 clearance（当前规则 0.3mm），同网铜合并。
某焊盘能被铺铜触及 ⇔ 存在方向，其异网铜间距 ≥ clearance 且走廊连通。
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import load_json  # noqa: E402
from g5_geom import (load_snapshot, board_rect, seg_seg_dist, dist_point_to_poly,  # noqa: E402
                     pad_poly, mm)

CLR = 0.3 * 39.3700787402

GND_ERR = [
    ("D1_2", 465.0, 579.0, "SMD Pad"), ("e113", 496.06, 622.05, "Via"),
    ("e109", 807.08, 968.9, "Via"), ("X1_4", 840.69, 905.465, "SMD Pad"),
    ("C19_2", 296.04, 746.0, "SMD Pad"), ("J5_B1A12", 332.984, 684.11, "SMD Pad"),
    ("C3_2", 878.04, 434.0, "SMD Pad"), ("C16_2", 950.96, 439.0, "SMD Pad"),
    ("C12_2", 645.96, 701.0, "SMD Pad"), ("e456", 681.1, 872.575, "Track"),
    ("U2_9", 445.0, 965.0, "SMD Pad"), ("U2_3", 470.0, 850.5, "SMD Pad"),
    ("U1_B7", 757.73, 570.95, "SMD Pad"), ("U1_0", 866.0, 630.0, "SMD Pad"),
    ("U1_F23", 806.95, 738.27, "SMD Pad"), ("C34_2", 679.04, 468.0, "SMD Pad"),
    ("J5_A1B12", 81.016, 684.11, "SMD Pad"), ("C18_2", 168.56, 807.0, "SMD Pad"),
]


def main():
    snap = load_snapshot()
    pads = snap["pads"]
    vias = snap["vias"]
    lines = snap["lines"]
    # 找到每个残余项对应的图元
    out = []
    for name, dx, dy, kind in GND_ERR:
        x, y = dx, dy   # 列表里已是画布 mil
        cand = None
        if kind == "SMD Pad":
            best = None
            for p in pads:
                if p.get("net") != "GND":
                    continue
                d = math.hypot(p["x"] - x, p["y"] - y)
                if best is None or d < best[0]:
                    best = (d, p)
            cand = best[1] if best else None
        elif kind == "Via":
            best = None
            for v in vias:
                if v.get("net") != "GND":
                    continue
                d = math.hypot(v["x"] - x, v["y"] - y)
                if best is None or d < best[0]:
                    best = (d, v)
            cand = best[1] if best else None
        else:
            best = None
            for l in lines:
                if l.get("net") != "GND":
                    continue
                d = seg_seg_dist((x, y), (x, y), (l["sx"], l["sy"]), (l["ex"], l["ey"]))
                if best is None or d < best[0]:
                    best = (d, l)
            cand = best[1] if best else None
        if cand is None:
            out.append({"name": name, "err": "not found"})
            continue
        if kind == "SMD Pad":
            layers = {1, 2} if cand["layer"] == 12 else {cand["layer"]}
            poly = pad_poly(cand)
            edge = 0
            rad = 0
            for q in poly:
                rad = max(rad, math.hypot(q[0] - cand["x"], q[1] - cand["y"]))
        elif kind == "Via":
            layers = {1, 2}
            poly = None
            edge = 0
            rad = cand["dia"] / 2.0
        else:
            layers = {cand["layer"]}
            poly = None
            edge = 0
            rad = cand["w"] / 2.0
        # 最近异网铜（逐层）
        if kind == "Track":
            cx = (cand["sx"] + cand["ex"]) / 2.0
            cy = (cand["sy"] + cand["ey"]) / 2.0
        else:
            cx, cy = cand["x"], cand["y"]
        res = {"name": name, "kind": kind, "net": "GND",
               "x_mm": round(mm(cx), 3), "y_mm": round(mm(cy), 3),
               "layers": sorted(layers)}
        for L in sorted(layers):
            worst = None
            for l in lines:
                if l["layer"] != L or l.get("net") in ("GND", ""):
                    continue
                d = seg_seg_dist((cx, cy), (cx, cy),
                                 (l["sx"], l["sy"]), (l["ex"], l["ey"])) - l["w"] / 2.0 - rad
                if worst is None or d < worst[0]:
                    worst = (d, "track", l["net"], l["id"])
            for p in pads:
                if p.get("net") in ("GND", "") or p.get("net") is None:
                    continue
                if p["layer"] != L and p["layer"] != 12:
                    continue
                d = dist_point_to_poly((cx, cy), pad_poly(p)) - rad
                if worst is None or d < worst[0]:
                    worst = (d, "pad", p["net"], p["id"])
            for v in vias:
                if v.get("net") in ("GND", ""):
                    continue
                d = math.hypot(cx - v["x"], cy - v["y"]) - v["dia"] / 2.0 - rad
                if worst is None or d < worst[0]:
                    worst = (d, "via", v["net"], v["id"])
            res[f"L{L}_nearest"] = {"gap_mm": round(mm(worst[0]), 4) if worst else None,
                                    "type": worst[1] if worst else None,
                                    "net": worst[2] if worst else None}
            # 四方向逃逸走廊（到最近异网铜的距离）
            dirs = {}
            for ang, (dx2, dy2) in {"+x": (1, 0), "-x": (-1, 0), "+y": (0, 1), "-y": (0, -1)}.items():
                best = None
                for l in lines:
                    if l["layer"] != L or l.get("net") in ("GND", ""):
                        continue
                    d = seg_seg_dist((cx, cy), (cx + dx2 * 3000, cy + dy2 * 3000),
                                     (l["sx"], l["sy"]), (l["ex"], l["ey"])) - l["w"] / 2.0 - rad
                    if best is None or d < best[0]:
                        best = (d, l["net"])
                for p in pads:
                    if p.get("net") in ("GND", "") or p.get("net") is None:
                        continue
                    if p["layer"] != L and p["layer"] != 12:
                        continue
                    d = dist_point_to_poly((cx + dx2 * 3000, cy + dy2 * 3000), pad_poly(p)) - rad
                    if best is None or d < best[0]:
                        best = (d, p["net"])
                for v in vias:
                    if v.get("net") in ("GND", ""):
                        continue
                    d = math.hypot(cx + dx2 * 3000 - v["x"], cy + dy2 * 3000 - v["y"]) - v["dia"] / 2.0 - rad
                    if best is None or d < best[0]:
                        best = (d, v["net"])
                dirs[ang] = {"gap_mm": round(mm(best[0]), 3) if best else None,
                             "net": best[1] if best else None}
            res[f"L{L}_dirs"] = dirs
        out.append(res)
    Path("E:/agent project/projects/flexiheal-v1/reports/_g5_islands.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in out:
        if "err" in r:
            print(r)
            continue
        print(f"{r['name']:10s} {r['kind']:8s} L{r['layers']} "
              f"near={r.get('L1_nearest',{}).get('gap_mm')}mm({r.get('L1_nearest',{}).get('net')}) "
              f"dirs={ {k: v['gap_mm'] for k, v in r.get('L1_dirs', {}).items()} }")


if __name__ == "__main__":
    main()
