#!/usr/bin/env python3
"""g5_place.py — G5 放置搜索器：在画布上搜索满足 DRC 净距的 GND 过孔位 / GND 逃逸连线。

净距模型（项目规则 copperThickness1oz，1oz 表）：
  Track↔Track 0.102mm / Track↔SMD Pad 0.152mm / Track↔Via 0.254mm
  Pad↔Pad 0.152mm / Pad↔Via 0.254mm / Via↔Via 0.254mm
  Copper Zone↔各 0.254mm（铺铜按 0.3mm 间隙留裕量）
  Track↔Board Outline 0.29972mm / Via↔Board Outline 0.29972mm
本脚本用保守值：异网 Track 0.152mm、异网 Pad 0.254mm、异网 Via 0.254mm、板边 0.3mm。

用法: python g5_place.py --mode stitch --out _g5_stitch_plan.json
      python g5_place.py --mode escape --out _g5_escape_plan.json
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import load_json, save_json  # noqa: E402
from g5_geom import (load_snapshot, board_rect, seg_seg_dist, dist_point_to_poly,  # noqa: E402
                     pad_poly, mm, point_copper_dist_to_edge)

MIL = 39.3700787402
CLR_TRACK_PAD = 0.152 * MIL   # 异网 track↔pad
CLR_VIA = 0.35 * MIL          # 异网 via↔任何（须落在铺铜让出带外，铺铜间隙 0.3mm）
CLR_TRACK = 0.102 * MIL       # 异网 track↔track
EDGE_MIN = 0.3 * MIL          # 板边铜距
VIA_DIA = 24.0
VIA_HOLE = 12.0
VIA_R = VIA_DIA / 2.0
TRK_W = 8.0                   # 0.2mm 走线宽（8mil）


class Board:
    def __init__(self):
        self.snap = load_snapshot()
        self.rect = board_rect(self.snap)
        self.tracks = [l for l in self.snap["lines"] if l.get("net") not in ("GND", "")]
        self.pads = [p for p in self.snap["pads"] if p.get("net") not in ("GND", "")]
        self.vias = [v for v in self.snap["vias"] if v.get("net") not in ("GND", "")]
        self.gnd_vias = [v for v in self.snap["vias"] if v.get("net") == "GND"]
        self.gnd_lines = [l for l in self.snap["lines"] if l.get("net") == "GND"]

    def via_clearance(self, x, y, layer_set=(1, 2)):
        """过孔心到异网铜的最小净距（mil，已扣半径）"""
        best = 1e9
        who = None
        for l in self.tracks:
            if l["layer"] not in layer_set:
                continue
            d = seg_seg_dist((x, y), (x, y), (l["sx"], l["sy"]), (l["ex"], l["ey"])) - l["w"] / 2.0 - VIA_R
            if d < best:
                best, who = d, ("track", l["net"], l["id"])
        for p in self.pads:
            if p["layer"] not in layer_set and p["layer"] != 12:
                continue
            d = dist_point_to_poly((x, y), pad_poly(p)) - VIA_R
            if d < best:
                best, who = d, ("pad", p["net"], p["id"])
        for v in self.vias:
            d = math.hypot(x - v["x"], y - v["y"]) - v["dia"] / 2.0 - VIA_R
            if d < best:
                best, who = d, ("via", v["net"], v["id"])
        for v in self.gnd_vias:
            d = math.hypot(x - v["x"], y - v["y"]) - v["dia"] / 2.0 - VIA_R
            if d < best:
                best, who = d, ("gndvia", "GND", v["id"])
        # 通孔焊盘的孔（孔↔孔 0.29972mm / 孔↔过孔铜 0.254mm）
        for p in self.snap["pads"]:
            hole = p.get("hole")
            if not hole:
                continue
            _ = p
            try:
                hr = max(float(hole[1]), float(hole[2])) / 2.0
            except Exception:
                continue
            d = math.hypot(x - p["x"], y - p["y"]) - hr - VIA_R
            if d < best:
                best, who = d, ("thhole", p.get("net"), p["id"])
        return best, who

    def track_clearance(self, a, b, layer):
        """线段 a-b（宽 TRK_W）到异网铜的最小净距（mil）——沿段细采样，不只测端点"""
        best = 1e9
        who = None
        for l in self.tracks:
            if l["layer"] != layer:
                continue
            d = seg_seg_dist(a, b, (l["sx"], l["sy"]), (l["ex"], l["ey"])) - l["w"] / 2.0 - TRK_W / 2.0
            if d < best:
                best, who = d, ("track", l["net"], l["id"])
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(2, int(L / 1.0) + 1)
        samples = [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]
        for p in self.pads:
            if p["layer"] != layer and p["layer"] != 12:
                continue
            poly = pad_poly(p)
            d = min(dist_point_to_poly(s, poly) for s in samples) - TRK_W / 2.0
            if d < best:
                best, who = d, ("pad", p["net"], p["id"])
        for v in self.vias:
            d = min(math.hypot(s[0] - v["x"], s[1] - v["y"]) for s in samples) - v["dia"] / 2.0 - TRK_W / 2.0
            if d < best:
                best, who = d, ("via", v["net"], v["id"])
        for v in self.gnd_vias:
            d = min(math.hypot(s[0] - v["x"], s[1] - v["y"]) for s in samples) - v["dia"] / 2.0 - TRK_W / 2.0
            if d < best:
                best, who = d, ("gndvia", "GND", v["id"])
        return best, who


def perim_point(rect, t):
    x0, y0, x1, y1 = rect
    W, H = x1 - x0, y1 - y0
    if t < W:
        return x0 + t, y0
    if t < W + H:
        return x1, y0 + (t - W)
    if t < 2 * W + H:
        return x1 - (t - W - H), y1
    return x0, y1 - (t - 2 * W - H)


def mode_stitch(b, args):
    """沿板周长搜索缝合孔位（板边内缩 args.inset），间距目标 args.spacing"""
    rect = b.rect
    PER = 2 * ((rect[2] - rect[0]) + (rect[3] - rect[1]))
    # 现有 GND 孔的周长位置
    have = []
    for v in b.gnd_vias:
        de = point_copper_dist_to_edge((v["x"], v["y"]), rect) - v["dia"] / 2.0
        have.append((de, v["x"], v["y"]))
    have.sort()
    edge_have = [(round(mm(d), 2), round(mm(x), 2), round(mm(y), 2)) for d, x, y in have if d <= 3 * MIL]
    # 候选：沿周长采样，板边内缩 inset
    inset = args.inset * MIL
    cands = []
    t = 0.0
    step = 0.5 * MIL
    while t < PER:
        px, py = perim_point(rect, t)
        # 内缩到板内
        if abs(px - rect[0]) < 1e-6:
            px = rect[0] + inset
        elif abs(px - rect[2]) < 1e-6:
            px = rect[2] - inset
        if abs(py - rect[1]) < 1e-6:
            py = rect[1] + inset
        elif abs(py - rect[3]) < 1e-6:
            py = rect[3] - inset
        d, who = b.via_clearance(px, py)
        # 到板边
        de = point_copper_dist_to_edge((px, py), rect) - VIA_R
        cands.append({"perim_mm": round(mm(t), 3), "x": round(px, 2), "y": round(py, 2),
                      "x_mm": round(mm(px), 3), "y_mm": round(mm(py), 3),
                      "clr_mm": round(mm(d), 3), "clr_nearest": who,
                      "edge_mm": round(mm(de), 3),
                      "ok": d >= CLR_VIA and de >= EDGE_MIN})
        t += step
    return {"edge_gnd_vias": edge_have, "candidates": cands,
            "params": {"inset_mm": args.inset, "via_dia": VIA_DIA, "via_hole": VIA_HOLE}}


ISOLATED = [
    # (名称, 类型, canvas_x, canvas_y)
    ("D1_2", "pad", 465.0, 579.0), ("e113", "via", 496.06, 622.05),
    ("e109", "via", 807.08, 968.9), ("X1_4", "pad", 840.69, 905.465),
    ("C19_2", "pad", 296.04, 746.0), ("J5_B1A12", "pad", 332.984, 684.11),
    ("C3_2", "pad", 878.04, 434.0), ("C16_2", "pad", 950.96, 439.0),
    ("C12_2", "pad", 645.96, 701.0), ("e456", "track", 681.1, 872.575),
    ("U2_9", "pad", 445.0, 965.0), ("U2_3", "pad", 470.0, 850.5),
    ("U1_B7", "pad", 757.73, 570.95), ("U1_0", "pad", 866.0, 630.0),
    ("U1_F23", "pad", 806.95, 738.27), ("C34_2", "pad", 679.04, 468.0),
    ("J5_A1B12", "pad", 81.016, 684.11), ("C18_2", "pad", 168.56, 807.0),
]


def mode_escape(b, args):
    """为每个孤立 GND 图元搜索：短走线（45° 折线）+ 落地过孔"""
    out = []
    for name, kind, x, y in ISOLATED:
        rec = {"name": name, "kind": kind, "x": round(x, 2), "y": round(y, 2),
               "x_mm": round(mm(x), 3), "y_mm": round(mm(y), 3), "options": []}
        # 起点若是焊盘，取其边缘方向；这里直接从焊盘中心出发
        layer = 1
        if kind == "pad":
            # 找到该 GND 焊盘
            best = None
            for p in b.snap["pads"]:
                if p.get("net") != "GND":
                    continue
                d = math.hypot(p["x"] - x, p["y"] - y)
                if best is None or d < best[0]:
                    best = (d, p)
            p = best[1]
            x, y = p["x"], p["y"]
            rec["x"], rec["y"] = round(x, 2), round(y, 2)
            rec["x_mm"], rec["y_mm"] = round(mm(x), 3), round(mm(y), 3)
            layer = 1 if p["layer"] == 1 else 2
        for ang in range(0, 360, 45):
            a = math.radians(ang)
            for L in range(20, 320, 5):
                ex = x + math.cos(a) * L
                ey = y + math.sin(a) * L
                # 走线段
                dtrk, wtrk = b.track_clearance((x, y), (ex, ey), layer)
                if dtrk < CLR_TRACK_PAD:
                    continue
                # 终点过孔
                dvia, wvia = b.via_clearance(ex, ey)
                de = point_copper_dist_to_edge((ex, ey), b.rect) - VIA_R
                if dvia < CLR_VIA or de < EDGE_MIN:
                    continue
                if kind == "pad":
                    poly = pad_poly(best[1])
                    if dist_point_to_poly((ex, ey), poly) < 0.2 * MIL:
                        continue   # 禁止 via-in-pad
                rec["options"].append({"kind": "newvia", "ang": ang, "len_mil": L,
                                       "ex": round(ex, 2), "ey": round(ey, 2),
                                       "ex_mm": round(mm(ex), 3), "ey_mm": round(mm(ey), 3),
                                       "track_clr_mm": round(mm(dtrk), 3),
                                       "via_clr_mm": round(mm(dvia), 3),
                                       "via_edge_mm": round(mm(de), 3),
                                       "layer": layer})
                break
        # 方案二：直连到既有 GND 孔（不加新孔）
        for gv in b.gnd_vias:
            d = math.hypot(gv["x"] - x, gv["y"] - y)
            if d < 20 or d > 300:
                continue
            dtrk, wtrk = b.track_clearance((x, y), (gv["x"], gv["y"]), layer)
            if dtrk < CLR_TRACK_PAD:
                continue
            rec.setdefault("to_existing", []).append({
                "kind": "existingvia", "gv_id": gv["id"],
                "gx": gv["x"], "gy": gv["y"], "dist_mm": round(mm(d), 3),
                "track_clr_mm": round(mm(dtrk), 3), "layer": layer})
        out.append(rec)
    return {"items": out, "params": {"via_dia": VIA_DIA, "via_hole": VIA_HOLE,
                                      "track_w_mil": TRK_W,
                                      "clr_track_pad_mm": round(CLR_TRACK_PAD / MIL, 3),
                                      "clr_via_mm": round(CLR_VIA / MIL, 3)}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="stitch")
    ap.add_argument("--out", default="_g5_place.json")
    ap.add_argument("--inset", type=float, default=1.0, help="板边内缩 mm")
    ap.add_argument("--spacing", type=float, default=7.0)
    args = ap.parse_args()
    b = Board()
    data = mode_stitch(b, args) if args.mode == "stitch" else mode_escape(b, args)
    save_json(args.out, data)
    if args.mode == "stitch":
        ok = [c for c in data["candidates"] if c["ok"]]
        print("贴边 GND 孔:", data["edge_gnd_vias"])
        print("可行候选:", len(ok), "/", len(data["candidates"]))
        # 打印周长覆盖
        print("可行周长位置(mm):", [c["perim_mm"] for c in ok])
    else:
        for r in data["items"]:
            best = r["options"][0] if r["options"] else None
            ex = r.get("to_existing") or []
            print(f"{r['name']:10s} {r['kind']:6s} newvia={len(r['options']):3d} existing={len(ex):3d}",
                  json.dumps(best, ensure_ascii=False) if best else (json.dumps(ex[0], ensure_ascii=False) if ex else "NO SOLUTION"))


if __name__ == "__main__":
    main()
