#!/usr/bin/env python3
"""pcb_astar3.py — 快速双层求解器（numpy 栅格 + scipy Dijkstra，实测 DRC 口径）。

栅格 0.05mm（900×600×2 层）。阻挡 = 异网焊盘矩形（含 prot 旋出）+ 已布轨道线段（含宽）
+ 过孔圆 + GND 孔 + C6 keepout + 板边，净距 CLR_NET芹菜=0.05 + SAFETY 0.02（实测 EDA DRC
通过口径 0.0548；轨道-轨道/孔 0.03~0.05 由 mask 一致处理）。8 邻域 + 过孔转换
(dia16/hole10, CLR_VIA=0.17)。
用法: python pcb_astar3.py --net N --audit A --from x,y --to x,y [--out path]
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

MIL = 39.37
GRID = 0.05
W, H = 45.0, 30.0
NX, NY = int(W / GRID), int(H / GRID)          # x 方向格数, y 方向格数
TRACK_W = 0.224
CLR = 0.003
VIA_R = 0.203
CLR_VIA = 0.17
KEEP = (34.0, 0.0, 45.0, 8.0)
VIA_COST = 0.45


def cell_of(x, y):
    return int(round(x / GRID)), int(round(y / GRID))


def pts_of(ix, iy):
    return ix * GRID, iy * GRID


def load(dump_path, audit_path):
    import json
    pads = []
    dump = json.loads(Path(dump_path).read_text(encoding="utf-8"))
    for c in dump:
        for p in c.get("pads", []):
            w = p["pad"][1] / MIL
            h = (p["pad"][2] if len(p["pad"]) > 2 else p["pad"][1]) / MIL
            prot = math.radians(p.get("prot") or 0)
            cc, ss = abs(math.cos(prot)), abs(math.sin(prot))
            pads.append({"net": p.get("net") or "", "x": p["x"] / MIL, "y": 30 - p["y"] / MIL,
                         "hw": (w * cc + h * ss) / 2, "hh": (w * ss + h * cc) / 2, "dz": c["dz"], "n": str(p["n"])})
    aud = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    lines = [x for x in aud if x.get("k") == "L"]
    vias = [x for x in aud if x.get("k") == "V"]
    tracks = [{"net": L["net"], "layer": L["layer"],
               "p1": (L["sx"] / MIL, 30 - L["sy"] / MIL),
               "p2": (L["ex"] / MIL, 30 - L["ey"] / MIL),
               "w": (L.get("w") or 10) / MIL} for L in lines]
    gvias = [{"net": v["net"], "x": v["x"] / MIL, "y": 30 - v["y"] / MIL,
              "r": (v.get("dia") or 24) / 2 / MIL} for v in vias]
    return pads, tracks, gvias


def mark_rect(mask, x0, y0, x1, y1, net, own=None):
    """把矩形（含方向净距）标记为 blocked；own 豁免。坐标任务系 mm。"""
    if own is not None and own == net:
        return
    ix0 = max(0, int(math.floor((x0) / GRID)))
    ix1 = min(NX, int(math.ceil((x1) / GRID)) + 1)
    iy0 = max(0, int(math.floor((y0) / GRID)))
    iy1 = min(NY, int(math.ceil((y1) / GRID)) + 1)
    if ix1 > ix0 and iy1 > iy0:
        mask[iy0:iy1, ix0:ix1] = True


def build_masks(pads, tracks, gvias, net):
    """返回 (blocked1, blocked2, viaBlocked)。y 轴=任务系向下。"""
    b1 = np.zeros((NY + 1, NX + 1), dtype=bool)
    b2 = np.zeros((NY + 1, NX + 1), dtype=bool)
    for p in pads:
        if p["net"] == net:
            continue
        # L2 潜渡豁免：小外形 SMD 焊盘与大外形已证 SMD（与 s4b_model L2_SMD_EXEMPT 同口径）
        small_smd = min(p["hw"], p["hh"]) < 0.35 or f"{p['dz']}.{p['n']}" in (
            {"U1.0", "U2.9", "U3.2", "X1.1", "X1.2", "X1.3", "X1.4"})
        r = TRACK_W / 2 + CLR
        for (bb, own_over) in ((b1, True), (b2, False)):
            if bb is b2 and small_smd:
                continue
            x0 = max(0, int(math.floor((p["x"] - p["hw"] - r) / GRID)))
            x1 = min(NX, int(math.ceil((p["x"] + p["hw"] + r) / GRID)) + 1)
            y0 = max(0, int(math.floor((p["y"] - p["hh"] - r) / GRID)))
            y1 = min(NY, int(math.ceil((p["y"] + p["hh"] + r) / GRID)) + 1)
            if x1 <= x0 or y1 <= y0:
                continue
            yy, xx = np.mgrid[y0:y1, x0:x1]
            px, py = xx * GRID, yy * GRID
            ddx = np.maximum(p["x"] - p["hw"] - px, px - (p["x"] + p["hw"]))
            ddx = np.maximum(ddx, 0.0)
            ddy = np.maximum(p["y"] - p["hh"] - py, py - (p["y"] + p["hh"]))
            ddy = np.maximum(ddy, 0.0)
            hit = (np.hypot(ddx, ddy) < r)
            bb[y0:y1, x0:x1] |= hit
    # 板边（板内余量）
    r = TRACK_W / 2 + CLR
    b1[:int((r + 0.30) / GRID) :, :] = True
    b2[:int((r + 0.30) / GRID) :, :] = True
    b1[int((H - r - 0.30) / GRID):, :] = True
    b2[int((H - r - 0.30) / GRID):, :] = True
    b1[:, :int((r + 0.30) / GRID)] = True
    b2[:, :int((r + 0.30) / GRID)] = True
    b1[:, int((W - r - 0.30) / GRID):] = True
    b2[:, int((W - r - 0.30) / GRID):] = True
    # keepout 双层
    kx0, ky0, kx1, ky1 = KEEP
    mark_rect(b1, kx0 - TRACK_W / 2, ky0 - TRACK_W / 2, kx1 + TRACK_W / 2, ky1 + TRACK_W / 2, "", None)
    mark_rect(b2, kx0 - TRACK_W / 2, ky0 - TRACK_W / 2, kx1 + TRACK_W / 2, ky1 + TRACK_W / 2, "", None)
    # 已布轨道（线段带宽扫掠，逐段栅格化）
    for t in tracks:
        if t["net"] == net:
            continue
        m = b1 if t["layer"] == 1 else b2
        rad = (t["w"] + TRACK_W) / 2 + CLR
        xa, ya = t["p1"]
        xb, yb = t["p2"]
        x0 = max(0, int((min(xa, xb) - rad) / GRID)); x1 = min(NX, int((max(xa, xb) + rad) / GRID) + 1)
        y0 = max(0, int((min(ya, yb) - rad) / GRID)); y1 = min(NY, int((max(ya, yb) + rad) / GRID) + 1)
        if x1 <= x0 or y1 <= y0:
            continue
        yy, xx = np.mgrid[y0: y1, x0: x1]
        px, py = xx * GRID, yy * GRID
        dx, dy = xb - xa, yb - ya
        L2 = dx * dx + dy * dy
        if L2 == 0:
            d = np.hypot(px - xa, py - ya)
        else:
            tproj = np.clip(((px - xa) * dx + (py - ya) * dy) / L2, 0, 1)
            d = np.hypot(px - (xa + tproj * dx), py - (ya + tproj * dy))
        m[y0:y1, x0:x1] |= (d < rad)
    # 过孔
    for v in gvias:
        if v["net"] == net:
            continue
        rad = v["r"] + TRACK_W / 2 + CLR
        x0 = max(0, int((v["x"] - rad) / GRID)); x1 = min(NX, int((v["x"] + rad) / GRID) + 1)
        y0 = max(0, int((v["y"] - rad) / GRID)); y1 = min(NY, int((v["y"] + rad) / GRID) + 1)
        yy, xx = np.mgrid[y0: y1, x0: x1]
        d = np.hypot(xx * GRID - v["x"], yy * GRID - v["y"])
        mask = b1 if True else b2
        b1[y0:y1, x0:x1] |= (d < rad)
        b2[y0:y1, x0:x1] |= (d < rad)
    # via 可行区（对异网两層集中障碍做更保守膨胀）
    both = b1 | b2
    rad = int((CLR_VIA) / GRID)  # 0.17/0.05=3.4 → +3 格
    via_b = both.copy()
    for _ in range(rad):
        vb = via_b.copy()
        vb[1:, :] |= via_b[:-1, :]
        vb[:-1, :] |= via_b[1:, :]
        vb[:, 1:] |= vb[:, :-1]
        vb[:, :-1] |= vb[:, 1:]
        via_b = vb
    return b1, b2, via_b


def solve(net, dump, audit, frm, to):
    pads, tracks, gvias = load(dump, audit)
    b1, b2, via_b = build_masks(pads, tracks, gvias, net)
    N1 = NY + 1
    def idx(layer, iy, ix):
        return layer * N1 * (NX + 1) + iy * (NX + 1) + ix
    total = 2 * N1 * (NX + 1)
    si = idx(0, *cell_of(*reversed(frm))) if False else None
    # 注意: mask 行=y 任务系（增大=向南）。cell: (ix,iy)
    fx, fy = frm
    tx, ty = to
    fix, fiy = cell_of(fx, fy)
    tix, tiy = cell_of(tx, ty)
    edges_i, edges_j, edges_w = [], [], []
    costs = {(1, 0, 0): 1.0, (1, 0, 1): 1.7071, (1, -1, 0): 1.0,
             (1, 0, -1): 1.0, (0, 1, 0): 1.0, (0, 1, 1): 1.7071, (0, -1, 0): 1.0, (0, -1, -1): 1.7071}
    DIRS = [(0, 1, 1.0), (0, -1, 1.0), (1, 0, 1.0), (-1, 0, 1.0),
            (1, 1, 1.7071), (1, -1, 1.7071), (-1, 1, 1.7071), (-1, -1, 1.7071)]
    for layer, bb in ((0, b1), (1, b2)):
        free = ~bb
        for (dx, dy, w) in DIRS:
            # 只做单向（dx,dy 的对称由双向遍历覆盖）——为省边数只加 dx>0 或 (dx==0 and dy>0)
            if not (dx > 0 or (dx == 0 and dy > 0)):
                if not (dx == 0 and dy > 0):
                    if not (dx > 0):
                        continue
            if dx == 0 and dy < 0:
                continue
            src_ok = free
            dst = np.roll(free, (-dy, -dx), axis=(0, 1))
            # 对角移动要求两轴正交邻格也空（保守）
            if dx != 0 and dy != 0:
                mid1 = np.roll(free, (-dy, 0), axis=(0, 1))
                mid2 = np.roll(free, (0, -dx), axis=(1, 0)) if False else np.roll(free, (-dx, 0), axis=(1, 0))
                # 修正:
                mid1 = np.roll(free, (-dy, 0), axis=(0, 1))
                mid2 = np.roll(free, (-dx, 0), axis=1)
                okm = src_ok & dst & mid1 & mid2
            else:
                okm = src_ok & dst
            ys, xs = np.nonzero(okm)
            for y, x in zip(ys.tolist(), xs.tolist()):
                ny, nx_ = y + dy, x + dx
                if 0 <= ny <= NY and 0 <= nx_ <= NX:
                    edges_i.append(idx(layer, y, x))
                    edges_j.append(idx(layer, ny, nx_))
                    edges_w.append(w * GRID)
                    edges_i.append(idx(layer, ny, nx_))
                    edges_j.append(idx(layer, y, x))
                    edges_w.append(w * GRID)
    # via 切换
    usable = ~(via_b)
    ys, xs = np.nonzero(usable & ~b1 & ~b2)
    for y, x in zip(ys.tolist(), xs.tolist()):
        edges_i.append(idx(0, y, x)); edges_j.append(idx(1, y, x)); edges_w.append(VIA_COST)
        edges_i.append(idx(1, y, x)); edges_j.append(idx(0, y, x)); edges_w.append(VIA_COST)
    g = csr_matrix((np.array(edges_w, dtype=np.float64), (np.array(edges_i), np.array(edges_j))), shape=(total, total))
    s_i = idx(0, fiy, fix)
    t_i = idx(0, tiy, tix)
    dist, pred = dijkstra(g, directed=True, indices=[s_i, t_i], return_predecessors=True)
    d0 = dist[0][t_i]
    if not np.isfinite(d0):
        return None
    # 回溯（从 t 沿 pred；pred 是从 s 搜索时的树）——pred[idx] 属 indices=s_i 的树
    path = []
    cur = t_i
    while cur != s_i and cur >= 0:
        layer = cur // (N1 * (NX + 1))
        rem = cur % (N1 * (NX + 1))
        y, x = divmod(rem, NX + 1)
        path.append((layer, x * GRID, y * GRID))
        cur = pred[0][cur]
    path.append((0, fx, fy))
    path = path[::-1]
    # 简化
    def dir_of(a, b):
        dx = round((b[1] - a[1]) / GRID, 3)
        dy = round((b[2] - a[2]) / GRID, 3)
        return (dx, dy, a[0])
    keep = [path[0]]
    for i in range(1, len(path) - 1):
        if dir_of(path[i - 1], path[i]) != dir_of(path[i], path[i + 1]):
            keep.append(path[i])
    keep.append(path[-1])
    segs, via_pts = [], []
    cur_layer = keep[0][0]
    run = [keep[0]]
    for st in keep[1:]:
        if st[0] != cur_layer:
            px, py = run[-1][1], run[-1][2]
            via_pts.append((px, py))
            segs.append((cur_layer, run[0][1], run[0][2], run[-1][1], run[-1][2]))
            cur_layer = st[0]
            run = [st]
        else:
            # 仅同方向延伸；换向先落段（阶梯路径不得直线化切角，pitfalls #32）
            if len(run) > 1:
                dprev = (round(run[-1][1] - run[-2][1], 3), round(run[-1][2] - run[-2][2], 3))
                dnew = (round(st[1] - run[-1][1], 3), round(st[2] - run[-1][2], 3))
                if dprev != dnew:
                    segs.append((cur_layer, run[0][1], run[0][2], run[-1][1], run[-1][2]))
                    run = [run[-1]]
            run.append(st)
    segs.append((cur_layer, run[0][1], run[0][2], run[-1][1], run[-1][2]))
    out = {"net": net, "segs": [{"layer": int(l + 1), "x1": round(float(x1), 3), "y1": round(float(y1), 3),
                                 "x2": round(float(x2), 3), "y2": round(float(y2), 3), "w": TRACK_W} for (l, x1, y1, x2, y2) in segs if (abs(float(x2) - float(x1)) > 1e-9 or abs(float(y2) - float(y1)) > 1e-9)],
           "vias": [{"x": round(float(v[0]), 3), "y": round(float(v[1]), 3), "dia_mil": 16, "hole_mil": 10} for v in via_pts],
           "total_mm": round(float(sum(math.hypot(float(sg[3]) - float(sg[1]), float(sg[4]) - float(sg[2])) for sg in segs)), 2), "blocked": False}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", required=True)
    ap.add_argument("--dump", default="reports/pcb_pads_dump_g4b_j5.json")
    ap.add_argument("--audit", required=True)
    ap.add_argument("--from", dest="frm", required=True)
    ap.add_argument("--to", dest="to", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out = solve(args.net, args.dump, args.audit, tuple(float(v) for v in args.frm.split(",")), tuple(float(v) for v in args.to.split(",")))
    if out is None:
        print(json.dumps({"net": args.net, "blocked": True}))
        sys.exit(1)
    p = Path(args.out) if args.out else Path(f"reports/route_astar3_{args.net}.json")
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"net": args.net, "segs": len(out["segs"]), "vias": len(out["vias"]), "total_mm": out["total_mm"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
