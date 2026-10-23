#!/usr/bin/env python3
"""pcb_astar2.py — 双层 A* 布线（G4 s7 残网专用，实测 DRC 口径）。

模型：状态=(格, 层)；8 邻域；过孔转换（dia 16mil/hole 10mil，via-in-pad 允许于自网焊盘）；
阻挡=异网焊盘/已布轨道/过孔/GND 缝合孔/C6 keepout/板边，净距 CLR_NET=0.05（实测 EDA
DRC 通过口径）+ 余量 SAFETY=0.02；过孔对异网净距按 DRC 规则 0.152+0.01。
输出 reports/route_astar2_<net>.json：{segs: [{layer,x1,y1,x2,y2,w}], vias:[{x,y}]}。
"""
import argparse
import heapq
import os
import json
import math
import sys
from pathlib import Path

MIL = 39.37
BOARD = (0.0, 0.0, 45.0, 30.0)
KEEPOUT = (34.0, 0.0, 45.0, 8.0)
CLR = 0.03            # 轨道对异网净距目标（实测 DRC 通过口径 0.0396）
CLR_VIA = 0.17        # 过孔对异网净距目标（0.152+0.018）
TRACK_W = 0.224
GRID = 0.05
VIA_DIA_MIL, VIA_HOLE_MIL = 16, 10
VIA_R = 16 / 2 / MIL  # 0.203

# S4B 补丁点（--no-keepout/--clr-track/--clr-pad/--width 运行期覆盖）
_ACTIVE_KEEP = True


def set_keepout(on):
    global _ACTIVE_KEEP, KEEPOUT
    _ACTIVE_KEEP = on
    if not on:
        KEEPOUT = (-1.0, -1.0, -1.0, -1.0)


def set_rules(clr_track, clr_pad, clr_via, width):
    global CLR, CLR_VIA, TRACK_W
    CLR = clr_track          # 轨道↔轨道
    CLR_VIA = clr_via
    TRACK_W = width


CLR_PAD_ACT = 0.152      # 轨道↔焊盘（JLC）
PAD_LAYERS = {}          # 焊盘层别映射（顶层1/通孔12），供 L2 潜渡豁免判定


def load_state(dump_path, audit_path):
    pads = []
    dump = json.loads(Path(dump_path).read_text(encoding="utf-8"))
    for c in dump:
        for p in c.get("pads", []):
            w = p["pad"][1] / MIL
            h = (p["pad"][2] if len(p["pad"]) > 2 else p["pad"][1]) / MIL
            prot = math.radians(p.get("prot") or 0)
            cc, ss = abs(math.cos(prot)), abs(math.sin(prot))
            hw, hh = (w * cc + h * ss) / 2, (w * ss + h * cc) / 2
            pads.append({"dz": c["dz"], "n": str(p["n"]), "net": p.get("net") or "",
                         "x": p["x"] / MIL, "y": 30 - p["y"] / MIL, "hw": hw, "hh": hh,
                         "layer": PAD_LAYERS.get(f"{c['dz']}.{p['n']}"),
                         "th": str(p["pad"][0]).upper() in ("ELLIPSE", "OVAL", "ROUND", "CIRCLE") and hw == hh})
    PAD_LAYERS.clear()
    _pl = os.environ.get("S4B_ASTAR_PAD_LAYERS")
    if _pl and Path(_pl).exists():
        _d = json.loads(Path(_pl).read_text(encoding="utf-8"))
        for _k, _v in _d.items():
            if isinstance(_v, dict):
                PAD_LAYERS[_k] = _v.get("layer")
            else:
                PAD_LAYERS[_k] = _v
    aud = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    lines = [x for x in aud if x.get("k") == "L"]
    vias = [x for x in aud if x.get("k") == "V"]
    tracks = []
    for L in lines:
        tracks.append({"net": L["net"], "layer": L["layer"],
                       "p1": (L["sx"] / MIL, 30 - L["sy"] / MIL),
                       "p2": (L["ex"] / MIL, 30 - L["ey"] / MIL),
                       "w": (L.get("w") or 10) / MIL})
    vias_g = [{"net": v["net"], "x": v["x"] / MIL, "y": 30 - v["y"] / MIL,
               "r": (v.get("dia") or 24) / 2 / MIL} for v in vias]
    return pads, tracks, vias_g


def seg_sample(p1, p2, step=0.06):
    L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    n = max(1, int(L / step))
    for k in range(n + 1):
        yield (p1[0] + (p2[0] - p1[0]) * k / n, p1[1] + (p2[1] - p1[1]) * k / n)


def pt_seg(px, py, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - a[0], py - a[1])
    t = max(0, min(1, ((px - a[0]) * dx + (py - a[1]) * dy) / L2))
    return math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy))


class Model:
    def __init__(self, pads, tracks, vias, net):
        self.pads = pads
        self.tracks = tracks
        self.vias = vias          # [{net,x,y,r}]
        self.net = net
        # 目标端点集：自网焊盘中心（用于 via-in-pad/同网豁免）
        self.own = [p for p in pads if p["net"] == net]

    def seg_ok(self, p1, p2, layer):
        w = TRACK_W
        for (px, py) in seg_sample(p1, p2):
            if px < BOARD[0] + w / 2 or py < BOARD[1] + w / 2 or px > BOARD[2] - w / 2 or py > BOARD[3] - w / 2:
                return "board"
            if _ACTIVE_KEEP:
                kx0, ky0, kx1, ky1 = KEEPOUT
                if kx0 - w / 2 < px < kx1 + w / 2 and ky0 - w / 2 < py < ky1 + w / 2:
                    return "keepout"
        for p in self.pads:
            if p["net"] == self.net:
                continue
            # L2 潜渡豁免：仅限"顶层 SMD 焊盘"（不同层无铜-铜间距），
            # 通孔（layer 12）焊盘在 L2 仍需间距 —— 层别未知时保持旧口径。
            if layer == 2 and (min(p["hw"], p["hh"]) < 0.35 or
                               f"{p['dz']}.{p['n']}" in ("U1.0", "U2.9", "U3.2",
                                                         "X1.1", "X1.2", "X1.3", "X1.4")):
                if not PAD_LAYERS or p.get("layer") == 1:
                    continue
            for (px, py) in seg_sample(p1, p2):
                ddx = max(p["x"] - p["hw"] - px, 0, px - (p["x"] + p["hw"]))
                ddy = max(p["y"] - p["hh"] - py, 0, py - (p["y"] + p["hh"]))
                d = math.hypot(ddx, ddy) - w / 2
                if d < CLR_PAD_ACT:
                    if d < -0.005:
                        return f"pad-in-{p['dz']}.{p['n']}"
                    return f"pad-{p['dz']}.{p['n']}({p['net']}){d:.3f}"
        for t in self.tracks:
            if t["net"] == self.net or t["layer"] != layer:
                continue
            for (px, py) in seg_sample(p1, p2):
                d = pt_seg(px, py, t["p1"], t["p2"]) - (t["w"] + w) / 2
                if d < CLR:
                    return f"track-{t['net']}{d:.3f}"
        for v in self.vias:
            if v["net"] == self.net:
                continue
            for (px, py) in seg_sample(p1, p2):
                d = math.hypot(px - v["x"], py - v["y"]) - w / 2 - v["r"]
                if d < 0.152:
                    return f"via-{v['net']}{d:.3f}"
        return None

    def via_ok(self, x, y):
        if x < BOARD[0] + VIA_R + 0.30 or y < BOARD[1] + VIA_R + 0.30 or \
           x > BOARD[2] - VIA_R - 0.30 or y > BOARD[3] - VIA_R - 0.30:
            return "edge"
        kx0, ky0, kx1, ky1 = KEEPOUT
        if kx0 - VIA_R < x < kx1 + VIA_R and ky0 - VIA_R < y < ky1 + VIA_R:
            return "keepout"
        for p in self.pads:
            if p["net"] == self.net:
                continue
            ddx = max(p["x"] - p["hw"] - x, 0, x - (p["x"] + p["hw"]))
            ddy = max(p["y"] - p["hh"] - y, 0, y - (p["y"] + p["hh"]))
            d = math.hypot(ddx, ddy) - VIA_R
            if d < CLR_VIA - 0.02:
                return f"v-pad-{p['dz']}.{p['n']}{d:.3f}"
        for layer in (1, 2):
            for t in self.tracks:
                if t["net"] == self.net or t["layer"] != layer:
                    continue
                d = pt_seg(x, y, t["p1"], t["p2"]) - VIA_R - t["w"] / 2
                if d < CLR_VIA - 0.02:
                    return f"v-track-{t['net']}{d:.3f}"
        for v in self.vias:
            if v["net"] == self.net:
                continue
            d = math.hypot(x - v["x"], y - v["y"]) - VIA_R - v["r"]
            if d < CLR_VIA - 0.02:
                return f"v-via-{v['net']}{d:.3f}"
        return None


def astar2(model, start, goal, grid=0.05, max_expand=1500000, eps=1.0, via_cost=0.45):
    def hh(state):
        (ix, iy), layer = state
        dx, dy = abs(ix * grid - goal[0]), abs(iy * grid - goal[1])
        return (dx + dy) + (math.sqrt(2) - 2) * min(dx, dy) + (0.35 if layer == 2 else 0)
    si = ((round(start[0] / grid), round(start[1] / grid)), 1)
    gi = ((round(goal[0] / grid), round(goal[1] / grid)), 1)
    openq = [(hh(si), 0, si)]
    came = {si: None}
    gcost = {si: 0}
    DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
    expand = 0
    while openq and expand < max_expand:
        f, g, cur = heapq.heappop(openq)
        if cur == gi:
            path = []
            while cur:
                path.append(cur)
                cur = came[cur]
            return path[::-1], True
        expand += 1
        (cx, cy), cl = cur
        # 层切换（via）
        nl = 2 if cl == 1 else 1
        ni = ((cx, cy), nl)
        if ni not in came:
            x, y = cx * grid, cy * grid
            v = model.via_ok(x, y)
            if not v:
                step = 0.45
                gcost[ni] = g + via_cost
                came[ni] = cur
                heapq.heappush(openq, (eps * (g + via_cost + hh(ni)), g + via_cost, ni))
        for dx, dy in DIRS:
            nij = ((cx + dx, cy + dy), cl)
            if nij in came:
                continue
            nx, ny = (cx + dx) * grid, (cy + dy) * grid
            step = grid * (math.sqrt(2) if dx and dy else 1)
            vv = model.seg_ok((cx * grid, cy * grid), (nx, ny), cl)
            if vv:
                continue
            if (dx, dy) in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                step *= 1.02
            gcost[nij] = g + step
            came[nij] = cur
            heapq.heappush(openq, (eps * (g + step + hh(nij)), g + step, nij))
    return None, False


def simplify(path, grid=0.05):
    def dirof(a, b):
        return (round((b[0][0] - a[0][0])), round((b[0][1] - a[0][1])), a[1])
    keep = [path[0]]
    for i in range(1, len(path) - 1):
        if dirof(path[i - 1], path[i]) != dirof(path[i], path[i + 1]):
            keep.append(path[i])
    keep.append(path[-1])
    return keep


import os
# S4B 运行期环境覆盖（子进程调用时生效）
try:
    if os.environ.get("S4B_ASTAR_WIDTH"):
        TRACK_W = float(os.environ["S4B_ASTAR_WIDTH"])
    if os.environ.get("S4B_ASTAR_CLR_TRACK") is not None:
        CLR = float(os.environ["S4B_ASTAR_CLR_TRACK"])
        CLR_PAD_ACT = float(os.environ.get("S4B_ASTAR_CLR_PAD", CLR_PAD_ACT))
    if os.environ.get("S4B_ASTAR_CLR_VIA"):
        CLR_VIA = float(os.environ["S4B_ASTAR_CLR_VIA"])
except Exception:
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", required=True)
    ap.add_argument("--dump", default="reports/pcb_pads_dump_g4b_j5.json")
    ap.add_argument("--audit", required=True)
    ap.add_argument("--from", dest="frm", required=True)
    ap.add_argument("--to", dest="to", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--eps", type=float, default=2.5,
                    help="A* 启发式膨胀系数；1.0=可采纳（完备），挤紧走廊用")
    args = ap.parse_args()
    pads, tracks, vias = load_state(args.dump, args.audit)
    model = Model(pads, tracks, vias, args.net)
    s = tuple(float(v) for v in args.frm.split(","))
    g = tuple(float(v) for v in args.to.split(","))
    via_cost = float(os.environ.get("S4B_ASTAR_VIA_COST", "0.45"))
    path, ok = astar2(model, s, g, grid=0.05, eps=args.eps, via_cost=via_cost)
    if not ok:
        print(json.dumps({"net": args.net, "blocked": True}))
        sys.exit(1)
    keep = simplify(path)
    segs, via_pts = [], []
    cur_layer = keep[0][1]
    prev = keep[0]
    last_dir = None
    for st in keep[1:]:
        (ix, iy), layer = st
        if layer != cur_layer:
            via_pts.append((prev[0][0] * GRID, prev[0][1] * GRID))
            cur_layer = layer
            last_dir = None
        else:
            a = (prev[0][0] * GRID, prev[0][1] * GRID)
            b = (st[0][0] * GRID, st[0][1] * GRID)
            # 仅同方向延伸：换向必须新起段（否则阶梯路径被直线化切角，见 pitfalls #32）
            d = (st[0][0] - prev[0][0], st[0][1] - prev[0][1])
            if (last_dir == d and segs and segs[-1]["layer"] == cur_layer
                    and abs(segs[-1]["x2"] - a[0]) < 1e-6 and abs(segs[-1]["y2"] - a[1]) < 1e-6):
                segs[-1]["x2"], segs[-1]["y2"] = round(b[0], 3), round(b[1], 3)
            else:
                segs.append({"layer": cur_layer, "x1": round(a[0], 3), "y1": round(a[1], 3),
                             "x2": round(b[0], 3), "y2": round(b[1], 3), "w": TRACK_W})
            last_dir = d
        prev = st
    total = sum(math.hypot(sg["x2"] - sg["x1"], sg["y2"] - sg["y1"]) for sg in segs)
    out = {"net": args.net, "segs": segs,
           "vias": [{"x": round(v[0], 3), "y": round(v[1], 3), "dia_mil": VIA_DIA_MIL, "hole_mil": VIA_HOLE_MIL} for v in via_pts],
           "total_mm": round(total, 2), "blocked": False}
    p = Path(args.out) if args.out else Path(f"reports/route_astar2_{args.net}.json")
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"net": args.net, "segs": len(segs), "vias": len(via_pts), "total_mm": out["total_mm"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
