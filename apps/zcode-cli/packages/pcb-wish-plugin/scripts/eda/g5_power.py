#!/usr/bin/env python3
"""g5_power.py — T2.6 C2 电源等效宽度核算（走线 + 同网铜皮联合等效宽度账）。

口径：同层同网铜皮与走线贴合时，等效宽度 ≈ 走线宽 + 铜皮宽（铜皮边到异网铜/板边须 ≥ 间距规则）。
规则（spec/ir.json C2）：VBUS/VBAT ≥1.0mm、VDD ≥0.6mm、VREG_IN ≥0.6mm、信号 0.25mm、ADC 链 0.3mm。

用法: python g5_power.py
产出: reports/_g5_c2_power_ledger.json
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import load_json, save_json  # noqa: E402
from g5_geom import (load_snapshot, board_rect, seg_seg_dist, dist_point_to_poly,  # noqa: E402
                     pad_poly, mm, point_copper_dist_to_edge)

MIL = 39.3700787402
CLR = 0.254 * MIL
RULE = {"VBUS": 1.0, "VBAT": 1.0, "VDD": 0.6, "VREG_IN": 0.6}
POUR_MIN = 0.2 * MIL   # 最小铜皮宽（工艺下限 0.2mm）


def main():
    snap = load_snapshot()
    rect = board_rect(snap)
    tracks = snap["lines"]
    pads = snap["pads"]
    vias = snap["vias"]

    def side_clear(x0, y0, x1, y1, nx, ny, net, layer):
        """从线段侧向法线方向到最近异网铜的距离（mil，已扣异网铜半宽）"""
        best = 1e9
        who = None
        # 沿法线取样
        for s in range(1, 200):
            d = s * 2.0
            p = (x0 + nx * d, y0 + ny * d)
            q = (x1 + nx * d, y1 + ny * d)
            if point_copper_dist_to_edge(p, rect) < 0:
                best = min(best, max(0.0, d - 2.0))
                who = ("board", "", "")
                break
            hit = False
            for l in tracks:
                if l["layer"] != layer or l["net"] in (net, ""):
                    continue
                dd = min(seg_seg_dist(p, q, (l["sx"], l["sy"]), (l["ex"], l["ey"])) - l["w"] / 2.0,
                         seg_seg_dist(p, q, (l["sx"], l["sy"]), (l["ex"], l["ey"])))
                if dd < d:
                    best = min(best, dd)
                    who = ("track", l["net"], l["id"])
                    hit = True
            for p2 in pads:
                if p2.get("net") in (net, "") or p2.get("net") is None:
                    continue
                if p2["layer"] != layer and p2["layer"] != 12:
                    continue
                dd = dist_point_to_poly(p, pad_poly(p2))
                if dd < d:
                    best = min(best, dd)
                    who = ("pad", p2["net"], p2["id"])
                    hit = True
            for v in vias:
                if v.get("net") in (net, ""):
                    continue
                dd = math.hypot(p[0] - v["x"], p[1] - v["y"]) - v["dia"] / 2.0
                if dd < d:
                    best = min(best, dd)
                    who = ("via", v["net"], v["id"])
                    hit = True
            if hit:
                break
        return min(best, 400.0), who

    ledger = {}
    for net, rule in RULE.items():
        segs = [l for l in tracks if l["net"] == net]
        rows = []
        for l in segs:
            dx = l["ex"] - l["sx"]
            dy = l["ey"] - l["sy"]
            L = math.hypot(dx, dy)
            if L < 1:
                continue
            nx, ny = -dy / L, dx / L
            c1, w1 = side_clear(l["sx"], l["sy"], l["ex"], l["ey"], nx, ny, net, l["layer"])
            c2, w2 = side_clear(l["sx"], l["sy"], l["ex"], l["ey"], -nx, -ny, net, l["layer"])
            usable1 = max(0.0, c1 - CLR)
            usable2 = max(0.0, c2 - CLR)
            best_side = max(usable1, usable2)
            rows.append({"id": l["id"], "layer": l["layer"], "len_mm": round(mm(L), 3),
                         "w_mm": round(mm(l["w"]), 4),
                         "side1_clear_mm": round(mm(c1), 3), "side1_obj": w1,
                         "side2_clear_mm": round(mm(c2), 3), "side2_obj": w2,
                         "side1_usable_mm": round(mm(usable1), 3),
                         "side2_usable_mm": round(mm(usable2), 3),
                         "best_usable_mm": round(mm(best_side), 3),
                         "equiv_if_poured_mm": round(mm(l["w"] + best_side), 3),
                         "meets_rule_with_pour": (l["w"] + best_side) >= rule * MIL})
        widths = [r["w_mm"] for r in rows]
        ledger[net] = {
            "rule_mm": rule,
            "segments": len(rows),
            "track_width_hist_mm": {str(round(w, 4)): widths.count(w) for w in sorted(set(widths))},
            "min_track_width_mm": round(min(widths), 4) if widths else None,
            "max_track_width_mm": round(max(widths), 4) if widths else None,
            "segments_meeting_rule_now": sum(1 for r in rows if r["w_mm"] >= rule),
            "segments_meeting_rule_with_pour": sum(1 for r in rows if r["meets_rule_with_pour"]),
            "best_equiv_mm": round(max((r["equiv_if_poured_mm"] for r in rows), default=0), 3),
            "detail": rows,
        }
    res = {"params": {"clr_mm": 0.254, "pour_min_mm": 0.2},
           "nets": {k: {kk: vv for kk, vv in v.items() if kk != "detail"} for k, v in ledger.items()},
           "ledger": ledger}
    save_json("_g5_c2_power_ledger.json", res)
    for net, v in ledger.items():
        print(f"{net:8s} 规则 {v['rule_mm']}mm  段数 {v['segments']}  "
              f"现达标 {v['segments_meeting_rule_now']}  加铜皮后可达标 {v['segments_meeting_rule_with_pour']}  "
              f"最佳等效 {v['best_equiv_mm']}mm  线宽分布 {v['track_width_hist_mm']}")


if __name__ == "__main__":
    main()
