#!/usr/bin/env python3
"""s5_astar.py — S5 逃逸扇出路由器（DRC 口径一致的双层栅格 A*）。

口径（对应 pitfalls #32/#43）：
- 障碍膨胀 = 规则间距 + 走线半宽（Track↔Track 0.102 / ↔Pad 0.152 / ↔Via 0.152）；
- 求解器与校验器**同口径**（同一膨胀半径、同一采样步长 ≤0.02mm）；
- 8 方向 + 转角代价（90° 代价高）→ 产物以 45° 为主；
- 双层（L1/L2）+ 可换层（过孔 hole12/dia24mil，与板同规则）。

输出：segments（任务系 mm）+ vias。
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
DIR_DEG = [0, 45, 90, 135, 180, 225, 270, 315]


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


class World:
    """画布几何（任务系 mm）。"""

    def __init__(self, snap, ignore_net=None, extra_lines=(), extra_vias=()):
        self.tracks = []   # (net, layer, ax, ay, bx, by, hw)
        self.vias = []     # (net, cx, cy, r)
        self.pads = []     # (net, layer, cx, cy, hw, hh, ellipse)
        for L in snap["lines"]:
            if L.get("err"):
                continue
            self.tracks.append((L.get("net"), L.get("layer"), L["sx"] / MIL, 30 - L["sy"] / MIL,
                                L["ex"] / MIL, 30 - L["ey"] / MIL, L["w"] / MIL / 2))
        for V in snap["vias"]:
            if V.get("err"):
                continue
            self.vias.append((V.get("net"), V["x"] / MIL, 30 - V["y"] / MIL, V["dia"] / MIL / 2))
        for P in snap["pads"]:
            if P.get("err"):
                continue
            sh = P.get("pad") or []
            kind = sh[0] if sh else "ELLIPSE"
            w = sh[1] / MIL if len(sh) > 1 else 0.25
            h = sh[2] / MIL if len(sh) > 2 else w
            lay = P.get("layer")
            if kind == "ELLIPSE":
                self.pads.append((P.get("net"), lay, P["x"] / MIL, 30 - P["y"] / MIL,
                                  0, 0, max(w, h) / 2))
            else:
                self.pads.append((P.get("net"), lay, P["x"] / MIL, 30 - P["y"] / MIL,
                                  w / 2, h / 2, False))
        for e in extra_lines:
            self.tracks.append(e)
        for e in extra_vias:
            self.vias.append(e)
        self.ignore_net = ignore_net

    # ---------- 障碍查询 ----------
    def blocked_by(self, net, px, py, layer, half):
        """返回阻挡描述或 None（净距口径）。"""
        best = None
        for (n, lay, ax, ay, bx, by, hw) in self.tracks:
            if lay != layer or n == net:
                continue
            c = seg_dist(px, py, ax, ay, bx, by) - hw - CLR["track"] - half
            if c < 0 and (best is None or c < best[0]):
                best = (c, "track", n, None)
        for (n, cx, cy, r) in self.vias:
            if n == net:
                continue
            c = max(math.hypot(px - cx, py - cy) - r, 0) - CLR["via"] - half
            if c < 0 and (best is None or c < best[0]):
                best = (c, "via", n, None)
        for (n, lay, cx, cy, hw, hh, ell) in self.pads:
            if lay not in (1, 12) or n == net:
                continue
            if ell:
                d = max(math.hypot(px - cx, py - cy) - hw, 0)
            else:
                d = math.hypot(max(abs(px - cx) - hw, 0.0), max(abs(py - cy) - hh, 0.0))
            c = d - CLR["pad"] - half
            if c < 0 and (best is None or c < best[0]):
                best = (c, "pad", n, None)
        return best

    def via_ok(self, net, px, py):
        """过孔落点合法性（禁 via-in-pad：与异网焊盘/走线净距）。"""
        for (n, cx, cy, r) in self.vias:
            if n == net:
                continue
            if math.hypot(px - cx, py - cy) - r - VIA_R < CLR["via"]:
                return False
        for (n, lay, cx, cy, hw, hh, ell) in self.pads:
            if lay not in (1, 12) or n == net:
                continue
            if ell:
                d = max(math.hypot(px - cx, py - cy) - hw, 0)
            else:
                d = math.hypot(max(abs(px - cx) - hw, 0.0), max(abs(py - cy) - hh, 0.0))
            if d - VIA_R < CLR["pad"]:
                return False
        for (n, lay, ax, ay, bx, by, hw) in self.tracks:
            if lay != 1 or n == net:
                continue
            if seg_dist(px, py, ax, ay, bx, by) - hw - VIA_R < CLR["track"]:
                return False
        return True


class Grid:
    def __init__(self, world, x0, y0, x1, y1, g, net, half, layers=(1, 2)):
        self.w = world
        self.x0, self.y0, self.g = x0, y0, g
        self.nx = int((x1 - x0) / g) + 1
        self.ny = int((y1 - y0) / g) + 1
        self.net = net
        self.half = half
        self.layers = layers
        self.free = {L: bytearray(self.nx * self.ny) for L in layers}
        self.rasterize()

    def rasterize(self):
        w = self.w
        half = self.half
        for L in self.layers:
            row = self.free[L]
            nx = self.nx
            for (n, lay, ax, ay, bx, by, hw) in w.tracks:
                if lay != L or n == self.net:
                    continue
                r = hw + CLR["track"] + half
                self._seg(L, ax, ay, bx, by, r)
            for (n, cx, cy, r) in w.vias:
                if n == self.net:
                    continue
                self._circ(L, cx, cy, r + CLR["via"] + half)
            for (n, lay, cx, cy, hw, hh, ell) in w.pads:
                if lay not in (1, 12) or n == self.net:
                    continue
                if ell:
                    self._circ(L, cx, cy, hw + CLR["pad"] + half)
                else:
                    ex = hw + CLR["pad"] + half
                    ey = hh + CLR["pad"] + half
                    ix0 = int((cx - ex - self.x0) / self.g)
                    ix1 = int((cx + ex - self.x0) / self.g)
                    iy0 = int((cy - ey - self.y0) / self.g)
                    iy1 = int((cy + ey - self.y0) / self.g)
                    for iy in range(max(0, iy0), min(self.ny, iy1 + 1)):
                        base = iy * nx
                        for ix in range(max(0, ix0), min(self.nx, ix1 + 1)):
                            row[base + ix] = 1

    def _seg(self, L, ax, ay, bx, by, r):
        ix0 = int((min(ax, bx) - r - self.x0) / self.g)
        ix1 = int((max(ax, bx) + r - self.x0) / self.g)
        iy0 = int((min(ay, by) - r - self.y0) / self.g)
        iy1 = int((max(ay, by) + r - self.y0) / self.g)
        row = self.free[L]
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
        row = self.free[L]
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

    def ok(self, L, ix, iy):
        if ix < 0 or iy < 0 or ix >
