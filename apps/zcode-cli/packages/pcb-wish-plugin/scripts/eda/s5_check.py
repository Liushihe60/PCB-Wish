#!/usr/bin/env python3
"""s5_check.py — S5 几何校验器：候选路径 vs 画布铜的净距复核（同口径细采样）。

用法:
  python s5_check.py --plan reports/_s5_exec_items.json          # 校验计划
  python s5_check.py --plan ... --snap _s5_snapshot_pre_escape.json
输出: 控制台摘要 + reports/_s5_check_<plan>.json
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s5_lib import load_json, save_json  # noqa: E402

MIL = 39.3700787402
CLR_TRACK = 0.10199878
CLR_PAD = 0.15200122
CLR_VIA = 0.15200122
CLR_OUTLINE = 0.29972


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def rect_dist(px, py, cx, cy, hw, hh):
    dx = max(abs(px - cx) - hw, 0.0)
    dy = max(abs(py - cy) - hh, 0.0)
    return math.hypot(dx, dy)


class Env:
    def __init__(self, snap):
        self.tracks, self.vias, self.pads = [], [], []
        for L in snap["lines"]:
            if L.get("err"):
                continue
            self.tracks.append((L.get("net") or "", L.get("layer"), L["sx"] / MIL, 30 - L["sy"] / MIL,
                                L["ex"] / MIL, 30 - L["ey"] / MIL, L["w"] / MIL / 2, L["id"]))
        for V in snap["vias"]:
            if V.get("err"):
                continue
            self.vias.append((V.get("net") or "", V["x"] / MIL, 30 - V["y"] / MIL,
                              V["dia"] / MIL / 2, V["id"]))
        for P in snap["pads"]:
            if P.get("err"):
                continue
            sh = P.get("pad") or []
            kind = sh[0] if sh else "ELLIPSE"
            w = sh[1] / MIL if len(sh) > 1 else 0.25
            h = sh[2] / MIL if len(sh) > 2 else w
            ell = (kind == "ELLIPSE")
            self.pads.append((P.get("net") or "", P.get("layer"),
                               P["x"] / MIL, 30 - P["y"] / MIL,
                               w / 2, h / 2, ell, P["id"], str(P.get("padNumber"))))

    def seg_hits(self, net, layer, ax, ay, bx, by, half, step=0.01):
        """返回 [(kind, net, id, dist_mm, at)]，dist = 实际净距（<0 为违例量）"""
        out = []
        n = max(2, int(math.hypot(bx - ax, by - ay) / step))
        worst = {}
        for i in range(n + 1):
            t = i / n
            px, py = ax + (bx - ax) * t, ay + (by - ay) * t
            for (n2, lay, tax, tay, tbx, tby, thw, pid) in self.tracks:
                if lay != layer or n2 == net:
                    continue
                gap = seg_dist(px, py, tax, tay, tbx, tby) - thw - half - CLR_TRACK
                if gap < 0 and (pid not in worst or gap < worst[pid][3]):
                    worst[pid] = ("track", n2, pid, gap, (round(px, 3), round(py, 3)))
            for (n2, vx, vy, vr, pid) in self.vias:
                if n2 == net:
                    continue
                gap = math.hypot(px - vx, py - vy) - vr - half - CLR_VIA
                if gap < 0 and (pid not in worst or gap < worst[pid][3]):
                    worst[pid] = ("via", n2, pid, gap, (round(px, 3), round(py, 3)))
            for (n2, lay, pcx, pcy, phw, phh, ell, pid, num) in self.pads:
                if n2 == net:
                    continue
                if lay != 12 and lay != layer:
                    continue
                if ell:
                    d = max(math.hypot(px - pcx, py - pcy) - max(phw, phh), 0)
                else:
                    d = rect_dist(px, py, pcx, pcy, phw, phh)
                gap = d - half - CLR_PAD
                if gap < 0 and (pid not in worst or gap < worst[pid][3]):
                    worst[pid] = ("pad", n2, pid, gap, (round(px, 3), round(py, 3)))
        for v in worst.values():
            out.append(v)
        return out


def check_plan(items, env):
    viol = []
    for it in items:
        if it["kind"] == "line":
            net, layer = it["net"], it["layer"]
            half = it["w"] / 2
            for h in env.seg_hits(net, layer, it["sx"], it["sy"], it["ex"], it["ey"], half):
                viol.append({"item": it.get("name", ""), "obj": h[0], "net": h[1], "id": h[2],
                             "gap_mm": round(h[3], 4), "at": h[4], "layer": layer,
                             "seg": [round(it["sx"], 3), round(it["sy"], 3),
                                     round(it["ex"], 3), round(it["ey"], 3)]})
        else:
            net = it["net"]
            x, y = it["x"], it["y"]
            vr = it["dia"] / 2
            for (n2, vx, vy, v_r, pid) in env.vias:
                if n2 == net:
                    continue
                gap = math.hypot(x - vx, y - vy) - vr - v_r - CLR_VIA
                if gap < 0:
                    viol.append({"item": it.get("name", ""), "obj": "via", "net": n2, "id": pid,
                                 "gap_mm": round(gap, 4), "at": (round(x, 3), round(y, 3))})
            for (n2, lay, pcx, pcy, phw, phh, ell, pid, num) in env.pads:
                if n2 == net:
                    continue
                if lay not in (1, 2, 12):
                    continue
                if ell:
                    d = max(math.hypot(x - pcx, y - pcy) - max(phw, phh), 0)
                else:
                    d = rect_dist(x, y, pcx, pcy, phw, phh)
                gap = d - vr - CLR_PAD
                if gap < 0:
                    viol.append({"item": it.get("name", ""), "obj": "pad", "net": n2, "id": pid,
                                 "gap_mm": round(gap, 4), "at": (round(x, 3), round(y, 3))})
            for (n2, lay, tax, tay, tbx, tby, thw, pid) in env.tracks:
                if n2 == net:
                    continue
                gap = seg_dist(x, y, tax, tay, tbx, tby) - vr - thw - CLR_TRACK
                if gap < 0:
                    viol.append({"item": it.get("name", ""), "obj": "track", "net": n2, "id": pid,
                                 "gap_mm": round(gap, 4), "at": (round(x, 3), round(y, 3))})
    return viol


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--snap", default="_s5_snapshot_pre_escape.json")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    snap = load_json(a.snap)
    env = Env(snap)
    plan = load_json(a.plan)
    items = plan["items"] if isinstance(plan, dict) else plan
    viol = check_plan(items, env)
    print(f"计划 {len(items)} 项；净距违例 {len(viol)} 条")
    for v in viol[:40]:
        print("  ", json.dumps(v, ensure_ascii=False))
    out = a.out or ("_s5_check_" + Path(a.plan).stem + ".json")
    save_json(out, {"plan": a.plan, "snapshot": a.snap, "n_items": len(items),
                    "violations": viol, "violation_count": len(viol)})
    print("落盘:", out)


if __name__ == "__main__":
    main()
