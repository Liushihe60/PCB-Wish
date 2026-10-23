#!/usr/bin/env python3
"""s5_route_plan.py — S5 T2/T3 规划器：为 11 个 U1 细间距焊盘规划定向扇出路径。

流程：
  1) 载入画布快照 → World（任务系 mm）；
  2) 对每个目标焊盘：以 0.1mm 细线做双层 A*（异网铜软阻挡），
     起点=焊盘中心，终点=该网**已连通的其它焊盘**（或最近的同网铜）；
  3) 产出：路径段 / 过孔 / 需拆异网图元清单；
  4) 输出 reports/_s5_route_plans.json（T2 拆段清单的来源）。

用法: python s5_route_plan.py [--width 0.1] [--grid 0.05] [--net NET]
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s5_lib import load_json, save_json  # noqa: E402
from s5_plan import World, Grid, astar, simplify_path, path_to_segments, collect_blockers  # noqa: E402

MIL = 39.3700787402

# (焊盘键, 网名, 焊盘号, 优先级, 目标锚点)
TARGETS = [
    ("U1.AD2", "VBUS", "AD2", 1),
    ("U1.E24", "DEC456_N", "E24", 2),
    ("U1.N24", "DEC5B_N", "N24", 2),
    ("U1.AD4", "USB_DM_P", "AD4", 2),
    ("U1.A23", "XC2", "A23", 2),
    ("U1.B13", "BTN1_N", "B13", 3),
    ("U1.B11", "BTN2_N", "B11", 3),
    ("U1.D23", "DEC3_N", "D23", 3),
    ("U1.AC5", "DECUSB_N", "AC5", 3),
    ("U1.AC13", "RESET_N", "AC13", 3),
    ("U1.K2", "SCL", "K2", 3),
]


def build_world():
    snap = load_json("_s5_snapshot_pre_escape.json")
    return World(snap), snap


def net_anchors(world, snap, net):
    """该网除目标焊盘外的其它焊盘 + 已有同网铜的 bbox。"""
    pads = []
    for p in world.pads:
        if p["net"] == net:
            pads.append(p)
    # 同网已有走线/过孔
    tr = [t for t in world.tracks if t["net"] == net]
    vi = [v for v in world.vias if v["net"] == net]
    return pads, tr, vi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=float, default=0.1)
    ap.add_argument("--grid", type=float, default=0.05)
    ap.add_argument("--net", default=None)
    ap.add_argument("--out", default="_s5_route_plans.json")
    a = ap.parse_args()

    world, snap = build_world()
    results = {}

    for key, net, num, prio in TARGETS:
        if a.net and net.upper() != a.net.upper():
            continue
        pads, tr, vi = net_anchors(world, snap, net)
        # 目标焊盘中心
        tp = None
        for p in pads:
            if p["num"] == num:
                tp = p
                break
        if tp is None:
            results[key] = {"net": net, "status": "no_target_pad"}
            continue
        # 锚点：同网其它焊盘中心（同层）
        anchors = [(p["cx"], p["cy"], 1 if p["layer"] in (1, 12) else 2)
                   for p in pads if p["num"] != num]
        # 同网已有铜（终点可并入）
        goal_pts = [(t["ax"], t["ay"], t["layer"]) for t in tr]
        goal_pts += [(t["bx"], t["by"], t["layer"]) for t in tr]
        goal_pts += [(v["cx"], v["cy"], 1) for v in vi] + [(v["cx"], v["cy"], 2) for v in vi]
        goal_pts += anchors
        if not goal_pts:
            results[key] = {"net": net, "status": "no_anchor"}
            continue

        # 搜索范围：以起终点包围盒外扩
        xs = [tp["cx"]] + [g[0] for g in goal_pts]
        ys = [tp["cy"]] + [g[1] for g in goal_pts]
        x0, x1 = min(xs) - 3.0, max(xs) + 3.0
        y0, y1 = min(ys) - 3.0, max(ys) + 3.0

        t0 = time.time()
        grid = Grid(world, x0, y0, x1, y1, a.grid, net, a.width / 2)

        # 终点测试：碰到同网铜（同层）或同网其它焊盘（排除起点焊盘本身）
        tol = a.width / 2 + 0.02
        skip = (tp["id"],)

        def goal_test(px, py, L, _w=world, _net=net, _tol=tol, _skip=skip):
            return _w.same_net_hit(_net, px, py, L, _tol, _skip)

        # 启发：到最近锚点的曼哈顿距离
        gx = [g[0] for g in goal_pts]
        gy = [g[1] for g in goal_pts]

        def heur(ix, iy, _g=grid, _gx=gx, _gy=gy):
            px, py = _g.xy(ix, iy)
            return min(abs(px - gx0) + abs(py - gy0) for gx0, gy0 in zip(_gx, _gy))

        path, exp = astar(grid, (tp["cx"], tp["cy"], 1), goal_test, heur=heur)
        dt = time.time() - t0
        if path is None:
            results[key] = {"net": net, "pad": num, "prio": prio,
                            "status": "blocked", "expanded": exp,
                            "note": "A* 未找到路径（含软阻挡惩罚）"}
            print(f"[BLOCKED] {key:9s} {net:10s} exp={exp} {dt:.1f}s")
            continue
        sp = simplify_path(path)
        segs, vias = path_to_segments(sp, a.width)
        blockers = collect_blockers(world, net, segs, vias)
        # 长度
        length = sum(math.hypot(s["ex"] - s["sx"], s["ey"] - s["sy"]) for s in segs)
        layers = sorted(set(s["layer"] for s in segs))
        results[key] = {
            "net": net, "pad": num, "prio": prio, "status": "planned",
            "width_mm": a.width, "segs": segs, "vias": vias,
            "blockers": blockers, "length_mm": round(length, 3),
            "layers": layers, "expanded": exp,
            "start": [round(tp["cx"], 4), round(tp["cy"], 4)],
        }
        nb = len(blockers)
        print(f"[PLAN   ] {key:9s} {net:10s} segs={len(segs):3d} vias={len(vias)} "
              f"len={length:6.2f}mm layers={layers} blockers={nb} {dt:.1f}s")

    save_json(a.out, {"meta": {"width_mm": a.width, "grid_mm": a.grid},
                      "plans": results})
    print("落盘:", a.out)


if __name__ == "__main__":
    main()
