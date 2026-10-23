#!/usr/bin/env python3
"""s5_try.py — S5 T1/T3 可行性判定：对 11 个 U1 细间距焊盘做
"硬阻挡 A*"（不拆任何异网铜）与"软阻挡 A*"（最小拆段）两种搜索。

用法:
  python s5_try.py --mode hard            # 全部目标，零拆段可行性
  python s5_try.py --mode soft --net VBUS  # 单网软阻挡解 + 拆段清单
输出: reports/_s5_try_<mode>[_<NET>].json
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

TARGETS = [
    ("U1.AD2", "VBUS", "AD2", 1, "outer"),
    ("U1.E24", "DEC456_N", "E24", 2, "outer"),
    ("U1.N24", "DEC5b_N", "N24", 2, "outer"),
    ("U1.AD4", "USB_DM_P", "AD4", 2, "outer"),
    ("U1.A23", "XC2", "A23", 2, "outer"),
    ("U1.B13", "BTN1_N", "B13", 3, "inner"),
    ("U1.B11", "BTN2_N", "B11", 3, "inner"),
    ("U1.D23", "DEC3_N", "D23", 3, "inner"),
    ("U1.AC5", "DECUSB_N", "AC5", 3, "inner"),
    ("U1.AC13", "RESET_N", "AC13", 3, "inner"),
    ("U1.K2", "SCL", "K2", 3, "inner"),
]


def build():
    snap = load_json("_s5_snapshot_pre_escape.json")
    return World(snap), snap


def net_info(world, net):
    pads = [p for p in world.pads if p["net"] == net]
    tr = [t for t in world.tracks if t["net"] == net]
    vi = [v for v in world.vias if v["net"] == net]
    return pads, tr, vi


def solve(world, key, net, num, width, grid_mm, soft, allow_via=True, margin=0.0):
    pads, tr, vi = net_info(world, net)
    tp = None
    for p in pads:
        if p["num"] == num:
            tp = p
            break
    if tp is None:
        return {"status": "no_target_pad"}
    anchors = [(p["cx"], p["cy"], 1 if p["layer"] in (1, 12) else 2)
               for p in pads if p["num"] != num]
    goal_pts = [(t["ax"], t["ay"], t["layer"]) for t in tr]
    goal_pts += [(t["bx"], t["by"], t["layer"]) for t in tr]
    goal_pts += [(v["cx"], v["cy"], 1) for v in vi] + [(v["cx"], v["cy"], 2) for v in vi]
    goal_pts += anchors
    if not goal_pts:
        return {"status": "no_anchor", "members": len(pads)}
    xs = [tp["cx"]] + [g[0] for g in goal_pts]
    ys = [tp["cy"]] + [g[1] for g in goal_pts]
    x0, x1 = min(xs) - 3.0, max(xs) + 3.0
    y0, y1 = min(ys) - 3.0, max(ys) + 3.0
    t0 = time.time()
    grid = Grid(world, x0, y0, x1, y1, grid_mm, net, width / 2)
    tol = width / 2 + 0.02
    skip = (tp["id"],)

    def goal_test(px, py, L):
        return world.same_net_hit(net, px, py, L, tol, skip)

    gx = [g[0] for g in goal_pts]
    gy = [g[1] for g in goal_pts]

    def heur(ix, iy):
        px, py = grid.xy(ix, iy)
        return min(abs(px - a) + abs(py - b) for a, b in zip(gx, gy))

    path, exp = astar(grid, (tp["cx"], tp["cy"], 1), goal_test, heur=heur,
                       soft=soft, allow_via=allow_via)
    dt = time.time() - t0
    if path is None:
        return {"status": "blocked", "expanded": exp, "sec": round(dt, 1),
                "members": len(pads), "anchors": len(anchors)}
    sp = simplify_path(path)
    segs, vias = path_to_segments(sp, width)
    blockers = collect_blockers(world, net, segs, vias)
    length = sum(math.hypot(s["ex"] - s["sx"], s["ey"] - s["sy"]) for s in segs)
    return {"status": "planned", "expanded": exp, "sec": round(dt, 1),
            "members": len(pads), "anchors": len(anchors),
            "segs": segs, "vias": vias, "blockers": blockers,
            "length_mm": round(length, 3),
            "layers": sorted(set(s["layer"] for s in segs)),
            "start": [round(tp["cx"], 4), round(tp["cy"], 4)]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="hard", choices=["hard", "soft"])
    ap.add_argument("--net", default=None)
    ap.add_argument("--width", type=float, default=0.1)
    ap.add_argument("--grid", type=float, default=0.05)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    world, snap = build()
    res = {}
    for key, net, num, prio, ring in TARGETS:
        if a.net and net.upper() != a.net.upper():
            continue
        r = solve(world, key, net, num, a.width, a.grid, soft=(a.mode == "soft"))
        r.update({"net": net, "pad": num, "prio": prio, "ring": ring})
        res[key] = r
        print(f"[{a.mode:4s}] {key:9s} {net:10s} {ring:5s} {r['status']:8s} "
              f"segs={len(r.get('segs', [])):3d} vias={len(r.get('vias', [])):2d} "
              f"len={r.get('length_mm', 0):6.2f} blockers={len(r.get('blockers', [])):3d} "
              f"exp={r.get('expanded')} {r.get('sec')}s")
    out = a.out or f"_s5_try_{a.mode}.json"
    save_json(out, {"meta": {"mode": a.mode, "width_mm": a.width, "grid_mm": a.grid,
                             "snapshot": "_s5_snapshot_pre_escape.json"}, "results": res})
    print("落盘:", out)


if __name__ == "__main__":
    main()
