#!/usr/bin/env python3
"""g5_stitch.py — GND 缝合孔分析：现有 22 颗的板边分布 + 沿板周边长间隙 + 候选补孔位（含净距校核）。

口径：
- 板框 = 45×30mm（canvas mil 0..1772 × 0..1181）
- 候选孔：GND 网，dia 24mil / hole 12mil（与既有 22 颗同规格，满足项目 Via Size ≥24mil）
- 净距校核：到异网铜（走线/焊盘/过孔）中心距离 ≥ 半径 + 0.254mm(Zone/Track 间距)；
  到板边 ≥ 0.3mm + 半径
- 候选孔必须落在 GND 铺铜区域内（否则不与铺铜连通）

用法: python g5_stitch.py [--pour-inset 15.75]
产出: reports/_g5_stitch_analysis.json
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import load_json, save_json  # noqa: E402
from g5_geom import (load_snapshot, board_rect, mm, seg_seg_dist, seg_point_dist,  # noqa: E402
                     pad_poly, dist_point_to_poly, point_copper_dist_to_edge)

CLR = 0.254 * 39.3700787402  # 0.254mm → mil
VIA_R = 12.0                  # 24mil 直径 → 半径 12mil
EDGE_MIN = 0.3 * 39.3700787402  # 板边铜距 ≥0.3mm
HOLE = 12.0


def perim_pos(x, y, rect):
    """沿板周长的位置（mil），顺时针从左上角 (x0,y0) 起。"""
    x0, y0, x1, y1 = rect
    W, H = x1 - x0, y1 - y0
    if abs(y - y0) <= abs(y - y1) and abs(y - y0) <= abs(x - x0) and abs(y - y0) <= abs(x - x1):
        return x - x0, "top"
    if abs(x - x1) <= abs(x - x0) and abs(y - y0) <= abs(y - y1):
        return W + (y - y0), "right"
    if abs(y - y1) <= abs(y - y0) and abs(y - y1) <= abs(x - x0) and abs(y - y1) <= abs(x - x1):
        return W + H + (x1 - x), "bottom"
    return 2 * W + H + (y1 - y), "left"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pour-inset", type=float, default=15.75, help="铺铜内缩(mil)")
    ap.add_argument("--out", default="_g5_stitch_analysis.json")
    args = ap.parse_args()

    snap = load_snapshot()
    rect = board_rect(snap)
    x0, y0, x1, y1 = rect
    W, H = x1 - x0, y1 - y0
    PER = 2 * (W + H)

    gnd_vias = [v for v in snap["vias"] if v["net"] == "GND"]
    lines = [l for l in snap["lines"] if l.get("net") != "GND"]
    pads = [p for p in snap["pads"] if p.get("net") != "GND" and p.get("net") != ""]
    vias = [v for v in snap["vias"] if v["net"] != "GND"]

    info = []
    for v in gnd_vias:
        de = point_copper_dist_to_edge((v["x"], v["y"]), rect) - v["dia"] / 2.0
        pp, side = perim_pos(v["x"], v["y"], rect)
        info.append({"id": v["id"], "x": v["x"], "y": v["y"],
                     "x_mm": round(mm(v["x"]), 3), "y_mm": round(mm(v["y"]), 3),
                     "edge_mm": round(mm(de), 3), "perim_mm": round(mm(pp), 2), "side": side})
    info.sort(key=lambda r: r["perim_mm"])

    # 沿板周的间隙（仅统计贴边孔）
    edge_vias = [r for r in info if r["edge_mm"] <= 3.0]
    gaps = []
    for i in range(len(edge_vias)):
        a = edge_vias[i]
        b = edge_vias[(i + 1) % len(edge_vias)]
        g = (b["perim_mm"] - a["perim_mm"]) if i + 1 < len(edge_vias) else (PER / 39.3701 - a["perim_mm"] + b["perim_mm"])
        gaps.append({"from": a["id"], "to": b["id"], "gap_mm": round(g, 2),
                     "from_side": a["side"], "to_side": b["side"]})

    # 候选补孔位：沿板周长采样，找空当
    def free_ok(px, py, net_exempt=("GND",)):
        """返回 (ok, detail)"""
        det = {}
        # 板边
        de = point_copper_dist_to_edge((px, py), rect) - VIA_R
        det["edge_mm"] = round(mm(de), 3)
        if de < EDGE_MIN:
            return False, det
        # 异网走线
        worst = None
        for l in lines:
            d = seg_seg_dist((px, py), (px, py), (l["sx"], l["sy"]), (l["ex"], l["ey"])) \
                - l["w"] / 2.0 - VIA_R
            if worst is None or d < worst[0]:
                worst = (d, l["net"], l["id"])
        det["track_min_mm"] = round(mm(worst[0]), 3) if worst else None
        det["track_net"] = worst[1] if worst else None
        if worst and worst[0] < CLR:
            return False, det
        # 异网焊盘
        worstp = None
        for p in pads:
            poly = pad_poly(p)
            d = dist_point_to_poly((px, py), poly) - VIA_R
            if worstp is None or d < worstp[0]:
                worstp = (d, p["net"], p["id"])
        det["pad_min_mm"] = round(mm(worstp[0]), 3) if worstp else None
        det["pad_net"] = worstp[1] if worstp else None
        if worstp and worstp[0] < CLR:
            return False, det
        # 异网过孔
        worstv = None
        for v in vias:
            d = math.hypot(px - v["x"], py - v["y"]) - v["dia"] / 2.0 - VIA_R
            if worstv is None or d < worstv[0]:
                worstv = (d, v["net"], v["id"])
        det["via_min_mm"] = round(mm(worstv[0]), 3) if worstv else None
        det["via_net"] = worstv[1] if worstv else None
        if worstv and worstv[0] < CLR:
            return False, det
        # GND 同网孔间距（孔-孔 0.254mm）
        for v in gnd_vias:
            d = math.hypot(px - v["x"], py - v["y"]) - VIA_R - v["dia"] / 2.0
            if d < CLR:
                det["gnd_via_conflict"] = v["id"]
                return False, det
        # 必须落在铺铜内
        inset = args.pour_inset
        if not (x0 + inset <= px <= x1 - inset and y0 + inset <= py <= y1 - inset):
            det["outside_pour"] = True
            return False, det
        return True, det

    cands = []
    step = 4.0
    s = 0.0
    while s < PER:
        # 沿周长反算点
        t = s
        if t < W:
            px, py = x0 + t, y0
        elif t < W + H:
            px, py = x1, y0 + (t - W)
        elif t < 2 * W + H:
            px, py = x1 - (t - W - H), y1
        else:
            px, py = x0, y1 - (t - 2 * W - H)
        # 内缩到板内 1.0mm 处
        inset_edge = 39.3700787402
        if px <= x0 + 0.5:
            px = x0 + inset_edge
        elif px >= x1 - 0.5:
            px = x1 - inset_edge
        if py <= y0 + 0.5:
            py = y0 + inset_edge
        elif py >= y1 - 0.5:
            py = y1 - inset_edge
        ok, det = free_ok(px, py)
        cands.append({"perim_mm": round(mm(t), 2), "x": round(px, 2), "y": round(py, 2),
                      "x_mm": round(mm(px), 3), "y_mm": round(mm(py), 3),
                      "ok": ok, "detail": det})
        s += step

    out = {"board_rect_mil": rect, "perimeter_mm": round(mm(PER), 2),
           "gnd_via_count": len(gnd_vias),
           "gnd_vias": info,
           "edge_via_count": len(edge_vias),
           "edge_gaps": gaps,
           "candidates": cands,
           "free_candidates": [c for c in cands if c["ok"]],
           "params": {"via_dia": 24, "via_hole": HOLE, "clr_mil": round(CLR, 2),
                      "edge_min_mil": round(EDGE_MIN, 2), "pour_inset_mil": args.pour_inset}}
    save_json(args.out, out)
    print("GND 缝合孔:", len(gnd_vias), "；贴边(≤3mm):", len(edge_vias))
    print("贴边间隙(mm):", [g["gap_mm"] for g in gaps])
    print("自由候选位:", len(out["free_candidates"]))
    for c in out["free_candidates"][:40]:
        print(json.dumps(c, ensure_ascii=False))


if __name__ == "__main__":
    main()
