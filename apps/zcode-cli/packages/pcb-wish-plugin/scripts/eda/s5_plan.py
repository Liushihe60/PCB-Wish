#!/usr/bin/env python3
"""s5_plan.py — S5 逃逸扇出路由器（DRC 口径一致的双层 A*）+ 拆段清单生成。

设计口径（对应 pitfalls #32/#43）：
- 障碍膨胀 = 规则间距 + 走线半宽（Track↔Track 0.102 / ↔Pad 0.152 / ↔Via 0.152）；
- 求解器与校验器**同口径**（同一膨胀半径、同一采样步长 ≤0.02mm）；
- 8 方向 + 转角代价（90° 代价高）→ 产物以 45° 为主；
- 双层（L1/L2）+ 可换层（过孔 hole12/dia24mil，与板同规则）；
- 异网铜"软阻挡"：可穿越但记巨额代价 → A* 自然产出**最小拆段清单**。

输出：
  reports/_s5_route_plans.json   每网：路径段 / 过孔 / 需拆图元 / 是否 blocked
"""
import heapq
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s5_lib import load_json, save_json  # noqa: E402

MIL = 39.3700787402
CLR = {"track": 0.10199878, "pad": 0.15200122, "via": 0.15200122}
VIA_DIA = 0.6096
VIA_HOLE = 0.3048
VIA_R = VIA_DIA / 2
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
SOFT_PENALTY = 6.0     # 穿越异网铜的代价（mm 当量）
VIA_COST = 0.8


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


class World:
    """画布几何（任务系 mm）。同网铜不作为障碍（可合并）。"""

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
                              "cx": V["x"] / MIL, "cy": 30 - V["y"] / MIL, "r": V["dia"] / MIL / 2})
        for P in snap["pads"]:
            if P.get("err"):
                continue
            sh = P.get("pad") or []
            kind = sh[0] if sh else "ELLIPSE"
            w = sh[1] / MIL if len(sh) > 1 else 0.25
            h = sh[2] / MIL if len(sh) > 2 else w
            lay = P.get("layer")
            self.pads.append({"net": P.get("net"), "layer": lay, "id": P["id"],
                              "cx": P["x"] / MIL, "cy": 30 - P["y"] / MIL,
                              "hw": w / 2, "hh": h / 2,
                              "ell": (kind == "ELLIPSE"), "num": str(P.get("padNumber"))})

    def blocking(self, net, px, py, layer, half):
        """返回本点被哪些异网图元压到阈值以下：[(kind, net, id)]"""
        out = []
        for t in self.tracks:
            if t["layer"] != layer or t["net"] == net:
                continue
            if seg_dist(px, py, t["ax"], t["ay"], t["bx"], t["by"]) - t["hw"] - CLR["track"] - half < 0:
                out.append(("track", t["net"], t["id"]))
        for v in self.vias:
            if v["net"] == net:
                continue
            if math.hypot(px - v["cx"], py - v["cy"]) - v["r"] - CLR["via"] - half < 0:
                out.append(("via", v["net"], v["id"]))
        for p in self.pads:
            if p["layer"] not in (1, 12) or p["net"] == net:
                continue
            if p["ell"]:
                d = max(math.hypot(px - p["cx"], py - p["cy"]) - max(p["hw"], p["hh"]), 0)
            else:
                d = math.hypot(max(abs(px - p["cx"]) - p["hw"], 0.0),
                               max(abs(py - p["cy"]) - p["hh"], 0.0))
            if d - CLR["pad"] - half < 0:
                out.append(("pad", p["net"], p["id"]))
        return out

    def via_ok(self, net, px, py, half=0.0):
        for v in self.vias:
            if v["net"] == net:
                continue
            if math.hypot(px - v["cx"], py - v["cy"]) - v["r"] - VIA_R < CLR["via"]:
                return False
        for p in self.pads:
            if p["layer"] not in (1, 12) or p["net"] == net:
                continue
            if p["ell"]:
                d = max(math.hypot(px - p["cx"], py - p["cy"]) - max(p["hw"], p["hh"]), 0)
            else:
                d = math.hypot(max(abs(px - p["cx"]) - p["hw"], 0.0),
                               max(abs(py - p["cy"]) - p["hh"], 0.0))
            if d - VIA_R < CLR["pad"]:
                return False
        for t in self.tracks:
            if t["layer"] != 1 or t["net"] == net:
                continue
            if seg_dist(px, py, t["ax"], t["ay"], t["bx"], t["by"]) - t["hw"] - VIA_R < CLR["track"]:
                return False
        return True

    def same_net_hit(self, net, px, py, layer, tol, skip_pad_ids=()):
        """是否触到同网铜（可合并落点）。skip_pad_ids：排除的焊盘 id（如起点焊盘本身）。"""
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
            if p["layer"] not in (1, 12):
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
    """两层栅格。异网铜为"软阻挡"：占据的格仍可走，但要付代价并记入拆段清单。"""

    def __init__(self, world, x0, y0, x1, y1, g, net, half, layers=(1, 2)):
        self.w = world
        self.x0, self.y0, self.g = x0, y0, g
        self.nx = int((x1 - x0) / g) + 1
        self.ny = int((y1 - y0) / g) + 1
        self.net = net
        self.half = half
        self.layers = layers
        self.block = {L: bytearray(self.nx * self.ny) for L in layers}
        self.rasterize()

    def rasterize(self):
        w = self.w
        half = self.half
        for L in self.layers:
            row = self.block[L]
            nx = self.nx
            for t in w.tracks:
                if t["layer"] != L or t["net"] == self.net:
                    continue
                self._seg(L, t["ax"], t["ay"], t["bx"], t["by"], t["hw"] + CLR["track"] + half)
            for v in w.vias:
                if v["net"] == self.net:
                    continue
                self._circ(L, v["cx"], v["cy"], v["r"] + CLR["via"] + half)
            for p in w.pads:
                if p["layer"] not in (1, 12) or p["net"] == self.net:
                    continue
                if p["ell"]:
                    self._circ(L, p["cx"], p["cy"], max(p["hw"], p["hh"]) + CLR["pad"] + half)
                else:
                    ex = p["hw"] + CLR["pad"] + half
                    ey = p["hh"] + CLR["pad"] + half
                    ix0 = int((p["cx"] - ex - self.x0) / self.g)
                    ix1 = int((p["cx"] + ex - self.x0) / self.g)
                    iy0 = int((p["cy"] - ey - self.y0) / self.g)
                    iy1 = int((p["cy"] + ey - self.y0) / self.g)
                    for iy in range(max(0, iy0), min(self.ny, iy1 + 1)):
                        base = iy * nx
                        for ix in range(max(0, ix0), min(self.nx, ix1 + 1)):
                            row[base + ix] = 1

    def _seg(self, L, ax, ay, bx, by, r):
        ix0 = int((min(ax, bx) - r - self.x0) / self.g)
        ix1 = int((max(ax, bx) + r - self.x0) / self.g)
        iy0 = int((min(ay, by) - r - self.y0) / self.g)
        iy1 = int((max(ay, by) + r - self.y0) / self.g)
        row = self.block[L]
        nx = self.nx
        g = self.g
        for iy in range(max(0, iy0), min(self.ny, iy1 + 1)):
            py = self.y0 + iy * g
            base = iy * nx
            for ix in range(max(0, ix0), min(self.nx, ix1 + 1)):
                px = self.x0 + ix * g
                if seg_dist(px, py, ax, ay, bx, by) <= r:
                    row[base + ix] = 1

    def _circ(self, L, cx, cy, r):
        ix0 = int((cx - r - self.x0) / self.g)
        ix1 = int((cx + r - self.x0) / self.g)
        iy0 = int((cy - r - self.y0) / self.g)
        iy1 = int((cy + r - self.y0) / self.g)
        row = self.block[L]
        nx = self.nx
        g = self.g
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

    def is_blocked(self, L, ix, iy):
        if ix < 0 or iy < 0 or ix >= self.nx or iy >= self.ny:
            return True
        return bool(self.block[L][iy * self.nx + ix])

    def xy(self, ix, iy):
        return self.x0 + ix * self.g, self.y0 + iy * self.g


def astar(grid, start, goal_test, turn_cost=0.05, ninety=0.5, allow_via=True,
          max_expand=900000, soft=True, heur=None):
    """A*。soft=True 时异网铜软阻挡（付代价并记录），否则硬阻挡。"""
    sx, sy, slayer = start
    ix0 = int(round((sx - grid.x0) / grid.g))
    iy0 = int(round((sy - grid.y0) / grid.g))
    gcost = {(ix0, iy0, slayer, -1): 0.0}
    came = {}
    pq = [(heur(ix0, iy0) if heur else 0.0, (ix0, iy0, slayer, -1))]
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
            blocked = grid.is_blocked(L, nix, niy)
            if blocked and not soft:
                continue
            if dx and dy:
                if grid.is_blocked(L, ix + dx, iy) or grid.is_blocked(L, ix, iy + dy):
                    if not soft:
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
            ns = (nix, niy, L, di)
            ng = g + step + tc
            if blocked:
                ng += SOFT_PENALTY
            if ng < gcost.get(ns, 1e18) - 1e-12:
                gcost[ns] = ng
                came[ns] = s
                h = heur(nix, niy) if heur else 0.0
                heapq.heappush(pq, (ng + h, ns))
        if allow_via and len(grid.layers) > 1:
            for L2 in grid.layers:
                if L2 == L:
                    continue
                if grid.is_blocked(L2, ix, iy):
                    continue
                if not grid.w.via_ok(grid.net, px, py):
                    continue
                ns = (ix, iy, L2, dprev)
                ng = g + VIA_COST
                if ng < gcost.get(ns, 1e18) - 1e-12:
                    gcost[ns] = ng
                    came[ns] = s
                    heapq.heappush(pq, (ng, ns))
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


def simplify_path(path):
    """合并同层共线点，产出折线顶点（按层切段）。"""
    out = []
    for (p, L) in path:
        if out and out[-1][1] == L:
            out.append((p, L))
        else:
            out.append((p, L))
    # 去掉共线中间点
    res = []
    for i, (p, L) in enumerate(out):
        if 0 < i < len(out) - 1:
            a = out[i - 1][0]
            b = p
            c = out[i + 1][0]
            if out[i - 1][1] == L and out[i + 1][1] == L:
                d0 = (b[0] - a[0], b[1] - a[1])
                d1 = (c[0] - b[0], c[1] - b[1])
                cross = d0[0] * d1[1] - d0[1] * d1[0]
                dot = d0[0] * d1[0] + d0[1] * d1[1]
                if abs(cross) < 1e-9 and dot > 0:
                    continue
        res.append((p, L))
    return res


def path_to_segments(path, width):
    segs = []
    vias = []
    layer = path[0][1]
    pts = [path[0][0]]
    for i in range(1, len(path)):
        p, L = path[i]
        if L != layer:
            segs.append({"layer": layer, "sx": pts[0][0], "sy": pts[0][1],
                         "ex": pts[-1][0], "ey": pts[-1][1], "w": width})
            vias.append({"x": pts[-1][0], "y": pts[-1][1], "hole": VIA_HOLE, "dia": VIA_DIA})
            layer = L
            pts = [p]
        else:
            pts.append(p)
    if len(pts) >= 2:
        segs.append({"layer": layer, "sx": pts[0][0], "sy": pts[0][1],
                     "ex": pts[-1][0], "ey": pts[-1][1], "w": width})
    return segs, vias


def collect_blockers(world, net, segs, vias):
    """路径穿过的异网图元集合（拆段清单）。"""
    seen = {}
    half_pts = []
    for s in segs:
        n = max(2, int(math.hypot(s["ex"] - s["sx"], s["ey"] - s["sy"]) / 0.02))
        for i in range(n + 1):
            t = i / n
            half_pts.append((s["sx"] + (s["ex"] - s["sx"]) * t,
                             s["sy"] + (s["ey"] - s["sy"]) * t))
    for v in vias:
        half_pts.append((v["x"], v["y"]))
    for (px, py) in half_pts:
        for (kind, n, pid) in world.blocking(net, px, py, 1, 0.05):
            key = (kind, n, pid)
            seen[key] = seen.get(key, 0) + 1
    return [{"kind": k, "net": n, "id": p, "samples": c}
            for (k, n, p), c in sorted(seen.items())]
