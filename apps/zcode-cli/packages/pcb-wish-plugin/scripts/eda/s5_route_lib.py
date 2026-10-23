#!/usr/bin/env python3
"""s5_route_lib.py — S5 布线引擎底座：DRC 口径一致的栅格 A* 路由器。

设计原则（对应 pitfalls #32/#43）：
- 几何校验器与求解器**同口径**：都用"中轴采样点 → 对方中轴距离 − 半宽和 − 规则间距"；
- 障碍膨胀用**规则间距 + 自身半宽**（不是固定 eps）；
- 8 方向 + 转角代价（90° 代价高），产出后 45° 倒角后处理；
- 层模型：L1/L2 两层，可换层（过孔 hole12/dia24mil，禁 via-in-pad）；
- 同网铜不作为障碍（可合并）。

坐标系：任务系 mm（x 右，y 上），画布 mil → 任务 mm：x=mil/39.37, y=30−mil/39.37。
"""
import heapq
import json
import math
from pathlib import Path

MIL = 39.3700787402

# --- DRC 间距规则（读自 reports/_g5_rules_original.json，copperThickness1oz） ---
CLR = {
    "track": 0.10199878,   # Track ↔ Track
    "pad": 0.15200122,     # Track ↔ SMD/TH Pad
    "via": 0.15200122,     # Track ↔ Via
    "zone": 0.254,         # Track ↔ Copper Zone
    "outline": 0.29972,    # Track ↔ Board Outline
}

VIA_DIA = 0.6096   # 24 mil
VIA_HOLE = 0.3048  # 12 mil
VIA_R = VIA_DIA / 2


def to_task_xy(mx, my):
    return mx / MIL, 30.0 - my / MIL


class Obstacles:
    """按层组织的异网障碍集合（任务系 mm）。"""

    def __init__(self):
        self.tracks = {1: [], 2: []}   # (net, ax, ay, bx, by, halfw)
        self.vias = []                 # (net, cx, cy, r)
        self.pads = {1: [], 2: []}     # (net, cx, cy, hw, hh, is_ellipse)

    def add_track(self, net, layer, ax, ay, bx, by, w):
        if layer not in self.tracks:
            layer = 1
        self.tracks[layer].append((net, ax, ay, bx, by, w / 2))

    def add_via(self, net, cx, cy, dia):
        self.vias.append((net, cx, cy, dia / 2))

    def add_pad(self, net, layer, cx, cy, hw, hh, ellipse):
        # 通孔焊盘(12) 视为两层障碍
        layers = (1, 2) if layer == 12 else ((1,) if layer == 1 else (2,))
        for L in layers:
            self.pads[L].append((net, cx, cy, hw, hh, ellipse))


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


class Grid:
    """两层栅格阻塞图。"""

    def __init__(self, x0, y0, x1, y1, g):
        self.x0, self.y0, self.g = x0, y0, g
        self.nx = int(math.ceil((x1 - x0) / g)) + 1
        self.ny = int(math.ceil((y1 - y0) / g)) + 1
        self.free = {1: bytearray(self.nx * self.ny), 2: bytearray(self.nx * self.ny)}

    def idx(self, ix, iy):
        return iy * self.nx + ix

    def xy(self, ix, iy):
        return self.x0 + ix * self.g, self.y0 + iy * self.g

    def mark_rect(self, layer, ix0, iy0, ix1, iy1):
        row = self.free[layer]
        nx = self.nx
        for iy in range(max(0, iy0), min(self.ny, iy1 + 1)):
            base = iy * nx
            for ix in range(max(0, ix0), min(self.nx, ix1 + 1)):
                row[base + ix] = 1

    def rasterize(self, obs, net, trace_w, layers=(1, 2)):
        """把异网障碍按 (规则间距 + trace 半宽) 膨胀后栅格化。"""
        hw = trace_w / 2
        for L in layers:
            # 走线
            for (n, ax, ay, bx, by, halfw) in obs.tracks[L]:
                if n == net:
                    continue
                r = halfw + CLR["track"] + hw
                self._stamp_seg(L, ax, ay, bx, by, r)
            # 过孔（两层都挡）
            for (n, cx, cy, r) in obs.vias:
                if n == net:
                    continue
                rr = r + CLR["via"] + hw
                self._stamp_circle(L, cx, cy, rr)
            # 焊盘
            for (n, cx, cy, phw, phh, ell) in obs.pads[L]:
                if n == net:
                    continue
                if ell:
                    rr = phw + CLR["pad"] + hw
                    self._stamp_circle(L, cx, cy, rr)
                else:
                    ex = phw + CLR["pad"] + hw
                    ey = phh + CLR["pad"] + hw
                    ix0 = int((cx - ex - self.x0) / self.g)
                    ix1 = int((cx + ex - self.x0) / self.g)
                    iy0 = int((cy - ey - self.y0) / self.g)
                    iy1 = int((cy + ey - self.y0) / self.g)
                    self.mark_rect(L, ix0, iy0, ix1, iy1)

    def _stamp_seg(self, L, ax, ay, bx, by, r):
        xlo = min(ax, bx) - r
        xhi = max(ax, bx) + r
        ylo = min(ay, by) - r
        yhi = max(ay, by) + r
        ix0 = int((xlo - self.x0) / self.g)
        ix1 = int((xhi - self.x0) / self.g)
        iy0 = int((ylo - self.y0) / self.g)
        iy1 = int((yhi - self.y0) / self.g)
        g = self.g
        for iy in range(max(0, iy0), min(self.ny, iy1 + 1)):
            py = self.y0 + iy * g
            base = iy * self.nx
            for ix in range(max(0, ix0), min(self.nx, ix1 + 1)):
                px = self.x0 + ix * g
                if seg_dist(px, py, ax, ay, bx, by) <= r:
                    self.free[L][base + ix] = 1

    def _stamp_circle(self, L, cx, cy, r):
        ix0 = int((cx - r - self.x0) / self.g)
        ix1 = int((cx + r - self.x0) / self.g)
        iy0 = int((cy - r - self.y0) / self.g)
        iy1 = int((cy + r - self.y0) / self.g)
        g = self.g
        r2 = r * r
        for iy in range(max(0, iy0), min(self.ny, iy1 + 1)):
            py = self.y0 + iy * g
            dy = py - cy
            base = iy * self.nx
            for ix in range(max(0, ix0), min(self.nx, ix1 + 1)):
                px = self.x0 + ix * g
                dx = px - cx
                if dx * dx + dy * dy <= r2:
                    self.free[L][base + ix] = 1

    def is_free(self, L, ix, iy):
        if ix < 0 or iy < 0 or ix >= self.nx or iy >= self.ny:
            return False
        return not self.free[L][iy * self.nx + ix]


DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
DIR_ANGLE = [0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0]


def astar(grid, start, goal_fn, layer_start=1, layers=(1, 2),
          via_cost=0.6, turn_cost=0.06, ninety_cost=0.45,
          max_expand=900000, max_nodes=400000):
    """栅格 A*，8 方向 + 转角代价 + 可换层。返回 (path_points, layer_seq) 或 None。"""
    sx, sy = start
    ix0 = int(round((sx - grid.x0) / grid.g))
    iy0 = int(round((sy - grid.y0) / grid.g))
    # 状态： (ix, iy, layer, dir_idx)
    def key(s):
        return (s[0], s[1], s[2], s[3])

    openq = []
    gcost = {}
    came = {}
    s0 = (ix0, iy0, layer_start, None)
    gcost[key(s0)] = 0.0
    heapq.heappush(openq, (0.0, key(s0)))
    expanded = 0
    best_goal = None
    while openq:
        f, s = heapq.heappop(openq)
        g = gcost[key(s)]
        expanded += 1
        if expanded > max_expand:
            break
        ix, iy, L, dprev = s
        px, py = grid.xy(ix, iy)
        if goal_fn(px, py, L):
            best_goal = s
            break
        for di, (dx, dy) in enumerate(DIRS):
            nix, niy = ix + dx, iy + dy
            if not (0 <= nix < grid.nx and 0 <= niy < grid.ny):
                continue
            step = math.hypot(dx, dy) * grid.g
            if dx and dy:
                # 斜向必须两个正交邻格都通
                if not grid.is_free(L, ix + dx, iy) or not grid.is_free(L, ix, iy + dy):
                    continue
            if not grid.is_free(L, nix, niy):
                continue
            tc = 0.0
            if dprev is not None:
                turn = (di - dprev) % 8
                turn = min(turn, 8 - turn)
                if turn == 1:
                    tc = turn_cost
                elif turn == 2:
                    tc = ninety_cost
                elif turn >= 3:
                    tc = turn_cost * 3
            ng = g + step + tc
            ns = (nix, niy, L, di)
            k = key(ns)
            if ng < gcost.get(k, 1e18) - 1e-12:
                gcost[k] = ng
                came[k] = s
                # 启发：到目标的曼哈顿距离
                h = abs(grid.xy(nix, niy)[0] - px) * 0.
