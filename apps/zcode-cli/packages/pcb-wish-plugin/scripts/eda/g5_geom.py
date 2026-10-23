#!/usr/bin/env python3
"""g5_geom.py — G5 几何工具库（净距/距离/铺铜可行性核算）。

口径（pitfalls #43）：线段-线段距离用中轴细采样（步长 ≤0.03mm=1.18mil），不用包围盒近似。
坐标：canvas mil（1mm = 39.3700787 mil）。
"""
import math
from pathlib import Path

MIL = 39.3700787402
HERE = Path("E:/agent project/projects/flexiheal-v1")
SNAP = HERE / "reports/_s5_snapshot_pre_pour.json"


def mm(v):
    return v / MIL


def to_mm(v):
    return round(v / MIL, 4)


# ---------- 基础几何 ----------

def seg_point_dist(p, a, b, step_mil=1.0):
    """点到线段的最短距离（解析解）。"""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / L2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def seg_seg_dist(a, b, c, d, step_mil=1.0):
    """两线段最短距离：沿 a-b 中轴细采样求到 c-d 的距离，取最小（对称再算一次）。"""
    ax, ay = a
    bx, by = b
    cx, cy = c
    dx, dy = d
    L = math.hypot(bx - ax, by - ay)
    n = max(2, int(L / step_mil) + 1)
    best = float("inf")
    for i in range(n + 1):
        t = i / n
        p = (ax + (bx - ax) * t, ay + (by - ay) * t)
        v = seg_point_dist(p, (cx, cy), (dx, dy))
        if v < best:
            best = v
            if best == 0:
                break
    if best == 0:
        return 0.0
    L2 = math.hypot(dx - cx, dy - cy)
    n2 = max(2, int(L2 / step_mil) + 1)
    for i in range(n2 + 1):
        t = i / n2
        p = (cx + (dx - cx) * t, cy + (dy - cy) * t)
        v = seg_point_dist(p, (ax, ay), (bx, by))
        if v < best:
            best = v
            if best == 0:
                break
    return best


def rect_corners(cx, cy, w, h, rot_deg):
    """旋转矩形四角（rot 顺时针度，canvas y 向下）。"""
    a = math.radians(rot_deg)
    ca, sa = math.cos(a), math.sin(a)
    hw, hh = w / 2.0, h / 2.0
    pts = []
    for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
        pts.append((cx + dx * ca - dy * sa, cy + dx * sa + dy * ca))
    return pts


def point_in_poly(p, poly):
    x, y = p
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


def dist_point_to_poly(p, poly):
    """点到多边形边界最短距离（点在内部则返回 0）。"""
    if point_in_poly(p, poly):
        return 0.0
    best = float("inf")
    n = len(poly)
    for i in range(n):
        d = seg_point_dist(p, poly[i], poly[(i + 1) % n])
        best = min(best, d)
    return best


def seg_poly_dist(a, b, poly, step_mil=1.0):
    """线段到多边形边界最短距离。"""
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    n = max(2, int(L / step_mil) + 1)
    best = float("inf")
    for i in range(n + 1):
        t = i / n
        p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        d = dist_point_to_poly(p, poly)
        if d < best:
            best = d
            if best == 0:
                break
    return best


def pad_poly(pad):
    """焊盘 → 多边形（外接矩形近似，保守）。"""
    x, y = pad["x"], pad["y"]
    shape = pad.get("pad") or ["RECT", 0, 0]
    w = float(shape[1] or 0)
    h = float(shape[2] or 0)
    rot = pad.get("rotation") or 0
    if shape[0] == "ELLIPSE":
        r = max(w, h) / 2.0
        w = h = 2 * r
    return rect_corners(x, y, w, h, rot)


def pad_layers(pad):
    """焊盘占据的铜层集合。"""
    l = pad.get("layer")
    if l == 12:
        return {1, 2}
    return {l}


# ---------- 快照载入 ----------

def load_snapshot(path=SNAP):
    import json
    return json.loads(Path(path).read_text(encoding="utf-8"))


def board_rect(snap):
    """板框矩形（canvas mil）。polygon 形如 [x0,y0,"L",x1,y1,x2,y2,...]。"""
    poly = snap["polylines"][0]["polygon"]["polygon"]
    nums = [p for p in poly if isinstance(p, (int, float))]
    xs = nums[0::2]
    ys = nums[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def objs_by_layer(snap):
    """按铜层归集图元。"""
    out = {1: {"lines": [], "vias": [], "pads": []}, 2: {"lines": [], "vias": [], "pads": []}}
    for l in snap["lines"]:
        if l.get("layer") in out:
            out[l["layer"]]["lines"].append(l)
    for v in snap["vias"]:
        for L in (1, 2):
            out[L]["vias"].append(v)
    for p in snap["pads"]:
        for L in pad_layers(p):
            if L in out:
                out[L]["pads"].append(p)
    return out


def track_copper_dist_to_edge(seg, rect):
    """走线铜边到板框（4 条边线）的最小距离（mil，正值=在板内）。"""
    x0, y0, x1, y1 = rect
    s = (seg["sx"], seg["sy"])
    e = (seg["ex"], seg["ey"])
    hw = (seg.get("w") or 0) / 2.0
    edges = [((x0, y0), (x0, y1)), ((x1, y0), (x1, y1)),
             ((x0, y0), (x1, y0)), ((x0, y1), (x1, y1))]
    d = min(seg_seg_dist(s, e, a, b) for a, b in edges)
    return d - hw


def point_copper_dist_to_edge(pt, rect):
    """点到板框 4 条边线的最短距离（mil）。"""
    x0, y0, x1, y1 = rect
    edges = [((x0, y0), (x0, y1)), ((x1, y0), (x1, y1)),
             ((x0, y0), (x1, y0)), ((x0, y1), (x1, y1))]
    return min(seg_point_dist(pt, a, b) for a, b in edges)
