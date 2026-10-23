#!/usr/bin/env python3
"""s5_solve.py — S5 T2/T3 主规划器 v2。

与 s5_plan.py 的差别（关键）：
  1) 异网**焊盘/过孔 = 硬阻挡**（永远不穿越，焊盘不可拆）；
  2) 异网**走线 = 软阻挡**（可穿越，记入拆段清单，代价大）；
  3) 拆段清单按层精确统计（s5_plan.collect_blockers 忽略了层别，是 bug）；
  4) 过孔落点用 via_ok 严格检查（hole12/dia24mil，禁 via-in-pad）；
  5) 目标 = 该网已有同网铜（走线端点/过孔/其它焊盘）。

用法:
  python s5_solve.py --net VBUS [--width 0.1] [--grid 0.05] [--out x.json]
输出: 段 / 过孔 / 需拆异网走线清单（含几何）
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s5_lib import load_json, save_json  # noqa: E402

MIL = 39.3700787402
CLR_TRACK = 0.10199878
CLR_PAD = 0.15200122
CLR_VIA = 0.15200122
VIA_DIA = 0.6096
VIA_HOLE = 0.3048
VIA_R = VIA_DIA / 2
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]

TARGETS = [
    ("U1.AD2", "VBUS", "AD2", 1),
    ("U1.E24", "DEC456_N", "E24", 2),
    ("U1.N24", "DEC5b_N", "N24", 2),
    ("U1.AD4", "USB_DM_P", "AD4", 2),
    ("U1.A23", "XC2", "A23", 2),
]


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


class World:
    def __init__(self, snap):
        self.tracks = []
        self.vias = []
        self.pads = []
        for L in snap["lines"]:
            if L.get("err"):
                continue
            self.tracks.append({"net": L.get("net"), "layer": L.get("layer"), "id": L["id"],
                                "ax": L["sx"] / MIL, "ay": 30 - L["sy"] / MIL,
                                "bx": L["ex"] / MIL, "by": 30 - L["ey"] / MIL,
                                "hw": L["w"] / MIL / 2})
        for V in snap["vias"]:
            if V.get("err"):
                continue
            self.vias.append({"net": V.get("net"), "id": V["id"],
                              "cx": V["x"] / MIL, "cy": 30 - V["y"] / MIL,
                              "r": V["dia"] / MIL / 2, "hole": V["hole"] / MIL})
        for P in snap["pads"]:
            if P.get("err"):
                continue
            sh = P.get("pad") or []
            kind = sh[0] if sh else "ELLIPSE"
            w = sh[1] / MIL if len(sh) > 1 else 0.25
            h = sh[2] / MIL if len(sh) > 2 else w
            self.pads.append({"net": P.get("net"), "layer": P.get("layer"), "id": P["id"],
                              "cx": P["x"] / MIL, "cy": 30 - P["y"] / MIL,
                              "hw": w / 2, "hh": h / 2,
                              "ell": (kind == "ELLIPSE"), "num": str(P.get("padNumber"))})

    # ---- 障碍查询：返回 (hard_hit_list, soft_hit_list) ----
    def probe(self, net, px, py, layer, half):
        hard, soft = [], []
        for t in self.tracks:
            if t["layer"] != layer or t["net"] == net:
                continue
            d = seg_dist(px, py, t["ax"], t["ay"], t["bx"], t["by"]) - t["hw"] - CLR_TRACK - half
            if d < 0:
                soft.append(t)
        for v in self.vias:
            if v["net"] == net:
                continue
            if math.hypot(px - v["cx"], py - v["cy"]) - v["r"] - CLR_VIA - half < 0:
                hard.append(("via", v))
        for p in self.pads:
            if p["net"] == net:
                continue
            lay = p["layer"]
            if lay == 12:
                pass
            elif lay != layer:
                continue
            if p["ell"]:
                d = max(math.hypot(px - p["cx"], py - p["cy"]) - max(p["hw"], p["hh"]), 0)
            else:
                d = math.hypot(max(abs(px - p["cx"]) - p["hw"], 0.0),
                               max(abs(py - p["cy"]) - p["hh"], 0.0))
            if d - CLR_PAD - half < 0:
                hard.append(("pad", p))
        return hard, soft

    def via_ok(self, net, px, py, half=0.0, no_via_in_pad=True):
        for v in self.vias:
            if v["net"] == net:
                continue
            if math.hypot(px - v["cx"], py - v["cy"]) - v["r"] - VIA_R < CLR_VIA:
                return False
        for p in self.pads:
            if p["net"] == net:
                if no_via_in_pad:
                    # 禁 via-in-pad：过孔铜盘不得压住任何同网焊盘
                    pr = max(p["hw"], p["hh"]) if p["ell"] else max(p["hw"], p["hh"])
                    if math.hypot(px - p["cx"], py - p["cy"]) < pr + VIA_R + 0.05:
                        return False
                continue
            lay = p["layer"]
            if lay != 12 and lay not in (1, 2):
                continue
            if p["ell"]:
                d = max(math.hypot(px - p["cx"], py - p["cy"]) - max(p["hw"], p["hh"]), 0)
            else:
                d = math.hypot(max(abs(px - p["cx"]) - p["hw"], 0.0),
                               max(abs(py - p["cy"]) - p["hh"], 0.0))
            if d - VIA_R < CLR_PAD:
                return False
        for t in self.tracks:
            if t["net"] == net:
                continue
            if seg_dist(px, py, t["ax"], t["ay"], t["bx"], t["by"]) - t["hw"] - VIA_R < CLR_TRACK:
                return False
        return True

    def same_net_hit(self, net, px, py, layer, tol, skip_pad_ids=()):
        for t in self.tracks:
            if t["net"] != net or t["layer"] != layer:
                continue
            if seg_dist(px, py, t["ax"], t["ay"], t["bx"], t["by"]) - t["hw"] <= tol:
                return True
        for v in self.vias:
            if v["net"] == net and math.hypot(px - v["cx"], py - v["cy"]) - v["r"] <= tol:
                return True
        for p in self.pads:
            if p["net"] != net or p["id"] in skip_pad_ids:
                continue
            if p["layer"] not in (1, 12, 2):
                continue
            if p["layer"] == 12 or p["layer"] == layer:
                if p["ell"]:
                    d = max(math.hypot(px - p["cx"], py - p["cy"]) - max(p["hw"], p["hh"]), 0)
                else:
                    d = math.hypot(max(abs(px - p["cx"]) - p["hw"], 0.0),
                                   max(abs(py - p["cy"]) - p["hh"], 0.0))
                if d <= tol:
                    return True
        return False


class Grid:
    def __init__(self, world, x0, y0, x1, y1, g, net, half, layers=(1, 2), forbid=()):
        self.w = world
        self.forbid = set(forbid)
        self.x0, self.y0, self.g = x0, y0, g
        self.nx = int((x1 - x0) / g) + 1
        self.ny = int((y1 - y0) / g) + 1
        self.net = net
        self.half = half
        self.layers = layers
        self.hard = {L: bytearray(self.nx * self.ny) for L in layers}
        self.soft = {L: bytearray(self.nx * self.ny) for L in layers}
        self.rasterize()

    def rasterize(self):
        w, half = self.w, self.half
        for L in self.layers:
            hard, soft = self.hard[L], self.soft[L]
            nx = self.nx
            for t in w.tracks:
                if t["layer"] != L or t["net"] == self.net:
                    continue
                if t["net"] in self.forbid:
                    self._stamp(L, t["ax"], t["ay"], t["bx"], t["by"],
                                t["hw"] + CLR_TRACK + half, hard)
                else:
                    self._stamp(L, t["ax"], t["ay"], t["bx"], t["by"],
                                t["hw"] + CLR_TRACK + half, soft)
            for v in w.vias:
                if v["net"] == self.net:
                    continue
                self._circ(L, v["cx"], v["cy"], v["r"] + CLR_VIA + half, hard)
            for p in w.pads:
                if p["net"] == self.net:
                    continue
                lay = p["layer"]
                if lay != 12 and lay != L:
                    continue
                if p["ell"]:
                    self._circ(L, p["cx"], p["cy"], max(p["hw"], p["hh"]) + CLR_PAD + half, hard)
                else:
                    ex = p["hw"] + CLR_PAD + half
                    ey = p["hh"] + CLR_PAD + half
                    ix0 = int((p["cx"] - ex - self.x0) / self.g)
                    ix1 = int((p["cx"] + ex - self.x0) / self.g)
                    iy0 = int((p["cy"] - ey - self.y0) / self.g)
                    iy1 = int((p["cy"] + ey - self.y0) / self.g)
                    for iy in range(max(0, iy0), min(self.ny, iy1 + 1)):
                        base = iy * nx
                        for ix in range(max(0, ix0), min(self.nx, ix1 + 1)):
                            hard[base + ix] = 1

    def _stamp(self, L, ax, ay, bx, by, r, buf):
        ix0 = int((min(ax, bx) - r - self.x0) / self.g)
        ix1 = int((max(ax, bx) + r - self.x0) / self.g)
        iy0 = int((min(ay, by) - r - self.y0) / self.g)
        iy1 = int((max(ay, by) + r - self.y0) / self.g)
        buf = self.soft[L] if buf is None else buf
        row = buf
        nx, g = self.nx, self.g
        for iy in range(max(0, iy0), min(self.ny, iy1 + 1)):
            py = self.y0 + iy * g
            base = iy * nx
            for ix in range(max(0, ix0), min(self.nx, ix1 + 1)):
                px = self.x0 + ix * g
                if seg_dist(px, py, ax, ay, bx, by) <= r:
                    row[base + ix] = 1

    def _circ(self, L, cx, cy, r, buf):
        ix0 = int((cx - r - self.x0) / self.g)
        ix1 = int((cx + r - self.x0) / self.g)
        iy0 = int((cy - r - self.y0) / self.g)
        iy1 = int((cy + r - self.y0) / self.g)
        row = buf
        nx, g = self.nx, self.g
        r2 = r * r
        for iy in range(max(0, iy0), min(self.ny, iy1 + 1)):
            py = self.y0 + iy * g
            dy = py - cy
            base = iy * nx
            for ix in range(max(0, ix0), min(self.nx, ix1 + 1)):
                px = self.x0 + ix * g
                dx = px - cx
                if dx * dx + dy * dy <= r2:
                    row[base + ix] = 1

    def is_hard(self, L, ix, iy):
        if ix < 0 or iy < 0 or ix >= self.nx or iy >= self.ny:
            return True
        return bool(self.hard[L][iy * self.nx + ix])

    def is_soft(self, L, ix, iy):
        if ix < 0 or iy < 0 or ix >= self.nx or iy >= self.ny:
            return False
        return bool(self.soft[L][iy * self.nx + ix])

    def xy(self, ix, iy):
        return self.x0 + ix * self.g, self.y0 + iy * self.g


def astar(grid, start, goal_test, heur, turn_cost=0.05, ninety=0.5,
          soft_penalty=8.0, via_cost=0.8, max_expand=400000):
    import heapq
    sx, sy, slayer = start
    ix0 = int(round((sx - grid.x0) / grid.g))
    iy0 = int(round((sy - grid.y0) / grid.g))
    gcost = {(ix0, iy0, slayer, -1): 0.0}
    came = {}
    pq = [(heur(ix0, iy0), (ix0, iy0, slayer, -1))]
    exp = 0
    goal = None
    while pq:
        f, s = heapq.heappop(pq)
        g = gcost[s]
        ix, iy, L, dprev = s
        px, py = grid.xy(ix, iy)
        if goal_test(px, py, L):
            goal = s
            break
        exp += 1
        if exp > max_expand:
            break
        for di, (dx, dy) in enumerate(DIRS):
            nix, niy = ix + dx, iy + dy
            if nix < 0 or niy < 0 or nix >= grid.nx or niy >= grid.ny:
                continue
            if grid.is_hard(L, nix, niy):
                continue
            if dx and dy and (grid.is_hard(L, ix + dx, iy) or grid.is_hard(L, ix, iy + dy)):
                continue
            step = math.hypot(dx, dy) * grid.g
            tc = 0.0
            if dprev >= 0:
                t = (di - dprev) % 8
                t = min(t, 8 - t)
                if t == 1:
                    tc = turn_cost
                elif t == 2:
                    tc = ninety
                elif t >= 3:
                    tc = turn_cost * 3
            ng = g + step + tc
            if grid.is_soft(L, nix, niy):
                ng += soft_penalty
            ns = (nix, niy, L, di)
            if ng < gcost.get(ns, 1e18) - 1e-12:
                gcost[ns] = ng
                came[ns] = s
                heapq.heappush(pq, (ng + heur(nix, niy), ns))
        for L2 in grid.layers:
            if L2 == L or grid.is_hard(L2, ix, iy):
                continue
            if not grid.w.via_ok(grid.net, px, py):
                continue
            ns = (ix, iy, L2, dprev)
            ng = g + via_cost
            if ng < gcost.get(ns, 1e18) - 1e-12:
                gcost[ns] = ng
                came[ns] = s
                heapq.heappush(pq, (ng + heur(ix, iy), ns))
    if goal is None:
        return None, exp
    path = []
    s = goal
    while s is not None:
        ix, iy, L, d = s
        path.append((grid.xy(ix, iy), L))
        s = came.get(s)
    path.reverse()
    return path, exp


def simplify(path):
    res = []
    for i, (p, L) in enumerate(path):
        if 0 < i < len(path) - 1:
            a = path[i - 1][0]
            c = path[i + 1][0]
            if path[i - 1][1] == L and path[i + 1][1] == L:
                d0 = (p[0] - a[0], p[1] - a[1])
                d1 = (c[0] - p[0], c[1] - p[1])
                cross = d0[0] * d1[1] - d0[1] * d1[0]
                dot = d0[0] * d1[0] + d0[1] * d1[1]
                if abs(cross) < 1e-9 and dot > 0:
                    continue
        res.append((p, L))
    return res


def path_to_segments(path, width):
    """逐相邻顶点出线段（s5_plan.py 原版只取首尾点，会把折线拉直 —— 严重 bug）。"""
    segs, vias = [], []
    for i in range(1, len(path)):
        (p0, L0) = path[i - 1]
        (p1, L1) = path[i]
        if L0 != L1:
            vias.append({"x": p0[0], "y": p0[1], "hole": VIA_HOLE, "dia": VIA_DIA})
            continue
        if abs(p1[0] - p0[0]) < 1e-9 and abs(p1[1] - p0[1]) < 1e-9:
            continue
        segs.append({"layer": L0, "sx": p0[0], "sy": p0[1],
                     "ex": p1[0], "ey": p1[1], "w": width})
    return segs, vias


def crossed_tracks(world, net, segs):
    """精确统计每段穿过的异网走线（按层）。"""
    hits = {}
    for s in segs:
        L = s["layer"]
        n = max(2, int(math.hypot(s["ex"] - s["sx"], s["ey"] - s["sy"]) / 0.01))
        for i in range(n + 1):
            t = i / n
            px = s["sx"] + (s["ex"] - s["sx"]) * t
            py = s["sy"] + (s["ey"] - s["sy"]) * t
            for tr in world.tracks:
                if tr["layer"] != L or tr["net"] == net:
                    continue
                if seg_dist(px, py, tr["ax"], tr["ay"], tr["bx"], tr["by"]) - tr["hw"] - 0.10199878 - 0.05 < 0:
                    hits.setdefault(tr["id"], {"net": tr["net"], "layer": L, "id": tr["id"],
                                               "ax": tr["ax"], "ay": tr["ay"],
                                               "bx": tr["bx"], "by": tr["by"],
                                               "hw": tr["hw"], "samples": 0})
                    hits[tr["id"]]["samples"] += 1
    return list(hits.values())


def solve(world, net, num, width, grid_mm, max_expand=400000, soft_penalty=8.0, forbid=()):
    pads = [p for p in world.pads if p["net"] == net]
    tp = next((p for p in pads if p["num"] == num), None)
    if tp is None:
        return {"status": "no_target_pad"}
    same_tracks = [t for t in world.tracks if t["net"] == net]
    same_vias = [v for v in world.vias if v["net"] == net]
    goal_pts = [(t["ax"], t["ay"], t["layer"]) for t in same_tracks]
    goal_pts += [(t["bx"], t["by"], t["layer"]) for t in same_tracks]
    goal_pts += [(v["cx"], v["cy"], 1) for v in same_vias]
    goal_pts += [(v["cx"], v["cy"], 2) for v in same_vias]
    goal_pts += [(p["cx"], p["cy"], 1 if p["layer"] in (1, 12) else 2)
                 for p in pads if p["num"] != num]
    if not goal_pts:
        return {"status": "no_anchor"}
    xs = [tp["cx"]] + [g[0] for g in goal_pts]
    ys = [tp["cy"]] + [g[1] for g in goal_pts]
    x0, x1 = min(xs) - 3.0, max(xs) + 3.0
    y0, y1 = min(ys) - 3.0, max(ys) + 3.0
    t0 = time.time()
    grid = Grid(world, x0, y0, x1, y1, grid_mm, net, width / 2, forbid=forbid)
    tol = width / 2 + 0.02
    skip = (tp["id"],)

    def goal_test(px, py, L):
        return world.same_net_hit(net, px, py, L, tol, skip)

    gx = [g[0] for g in goal_pts]
    gy = [g[1] for g in goal_pts]

    def heur(ix, iy):
        px, py = grid.xy(ix, iy)
        return min(abs(px - a) + abs(py - b) for a, b in zip(gx, gy))

    path, exp = astar(grid, (tp["cx"], tp["cy"], 1), goal_test, heur, max_expand=max_expand, soft_penalty=soft_penalty)
    dt = time.time() - t0
    if path is None:
        return {"status": "blocked", "expanded": exp, "sec": round(dt, 1),
                "members": len(pads)}
    sp = simplify(path)
    segs, vias = path_to_segments(sp, width)
    crossed = crossed_tracks(world, net, segs)
    length = sum(math.hypot(s["ex"] - s["sx"], s["ey"] - s["sy"]) for s in segs)
    return {"status": "planned", "expanded": exp, "sec": round(dt, 1),
            "members": len(pads), "segs": segs, "vias": vias, "crossed": crossed,
            "length_mm": round(length, 3),
            "layers": sorted(set(s["layer"] for s in segs)),
            "start": [round(tp["cx"], 4), round(tp["cy"], 4)]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", default=None)
    ap.add_argument("--width", type=float, default=0.1)
    ap.add_argument("--grid", type=float, default=0.05)
    ap.add_argument("--out", default="_s5_solve.json")
    ap.add_argument("--penalty", type=float, default=8.0)
    ap.add_argument("--forbid", default="")
    ap.add_argument("--max-expand", type=int, default=400000)
    a = ap.parse_args()
    snap = load_json("_s5_snapshot_pre_escape.json")
    world = World(snap)
    res = {}
    for key, net, num, prio in TARGETS:
        if a.net and net.upper() != a.net.upper():
            continue
        r = solve(world, net, num, a.width, a.grid, max_expand=a.max_expand, soft_penalty=a.penalty,
                   forbid=tuple(x for x in a.forbid.split(",") if x))
        r.update({"net": net, "pad": num, "prio": prio})
        res[key] = r
        print(f"[SOLVE] {key:9s} {net:10s} {r['status']:8s} segs={len(r.get('segs', [])):3d} "
              f"vias={len(r.get('vias', [])):2d} len={r.get('length_mm', 0):6.2f} "
              f"crossed={len(r.get('crossed', [])):2d} exp={r.get('expanded')} {r.get('sec')}s")
    save_json(a.out, {"meta": {"width_mm": a.width, "grid_mm": a.grid,
                               "snapshot": "_s5_snapshot_pre_escape.json"}, "results": res})
    print("落盘:", a.out)


if __name__ == "__main__":
    main()
