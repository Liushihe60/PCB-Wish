#!/usr/bin/env python3
"""pcb_astar_router.py — G4 手工布线 A* 求解器（实测 DRC 口径）。

背景：内置路由器对 5 网失败（DEC1_N/DEC3_N/DECUSB_N/DCC/DCCH），根因=U1 aQFN
0.5mm 栅格内圈逃逸走廊太窄，pcb_route_lib 的 0.16mm 净距目标过于保守。实测已布
走线对 U1 焊盘最紧净距 0.0396mm 且 EDA DRC 无间距违例（EDA 按真实几何判定）。
故本求解器采用"实测口径"：NET 净距 0.05mm + SAFETY 余量，阻挡含已布走线/焊盘/
过孔/GND 孔/包地栏，输出 45° 折线（曼哈顿+对角步）。

用法（生成命令清单）：
    python pcb_astar_router.py --net DCC --dump reports/pcb_pads_dump_g4b_j5.json \
        --audit reports/_audit_latest.json --via-audit reports/_audit_latest.json
输出：reports/route_astar_<net>.json {net, segs:[{x1,y1,x2,y2,w}], total_mm, blocked:bool}
"""
import argparse
import json
import math
import sys
from pathlib import Path

MIL = 39.37
BOARD = (0.0, 0.0, 45.0, 30.0)
KEEPOUT = (34.0, 0.0, 45.0, 8.0)  # C6 天线禁走线（任务系）
CLR_NET = 0.05                    # 实测 DRC 通过口径
SAFETY = 0.02
CLR = CLR_NET + SAFETY            # 0.07mm 对非目标阻挡物
VIA_RAD = 0.305                   # GND 缝合孔 24mil
TRACK_W = 0.25


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
                         "x": p["x"] / MIL, "y": 30 - p["y"] / MIL, "hw": hw, "hh": hh})
    aud = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    lines = [x for x in aud if x.get("k") == "L"] if aud and isinstance(aud[0], dict) and "k" in aud[0] else aud.get("lines", [])
    vias = [x for x in aud if x.get("k") == "V"] if aud and isinstance(aud[0], dict) and "k" in aud[0] else aud.get("vias", [])
    tracks = []
    for L in lines:
        tracks.append({"net": L["net"], "layer": L["layer"],
                       "p1": (L["sx"] / MIL, 30 - L["sy"] / MIL),
                       "p2": (L["ex"] / MIL, 30 - L["ey"] / MIL),
                       "w": (L.get("w") or 10) / MIL})
    gnd_vias = [(v["x"] / MIL, 30 - v["y"] / MIL) for v in vias if v.get("net") == "GND"]
    return pads, tracks, gnd_vias


def seg_rect_dist(p1, p2, rect):
    """点集采样线段到矩形净距（含扫掠一半线宽在外部处理）。"""
    rx0, ry0, rx1, ry1 = rect
    def d_pt(px, py):
        ddx = max(rx0 - px, 0, px - rx1)
        ddy = max(ry0 - py, 0, py - ry1)
        return math.hypot(ddx, ddy)
    best = 9e9
    L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    n = max(2, int(L / 0.06) + 1)
    for k in range(n + 1):
        px = p1[0] + (p2[0] - p1[0]) * k / n
        py = p1[1] + (p2[1] - p1[1]) * k / n
        d = d_pt(px, py)
        if d < best:
            best = d
    return best


def blocked(p1, p2, w, net, pads, tracks, gnd_vias, target_net_ok=True):
    """线段(p1->p2, 宽 w, 属 net)是否违距。返回 None=通过，否则违例描述。"""
    L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    n = max(2, int(L / 0.06) + 1)
    # keepout：轨道中线扫掠（w/2）不得入 C6 区
    kx0, ky0, kx1, ky1 = KEEPOUT
    for k in range(n + 1):
        px = p1[0] + (p2[0] - p1[0]) * k / n
        py = p1[1] + (p2[1] - p1[1]) * k / n
        if kx0 - w / 2 - 0.01 < px < kx1 + w / 2 and ky0 - w / 2 - 0.01 < py < ky1 + w / 2:
            return f"keepout@({px:.2f},{py:.2f})"
        if px < BOARD[0] + w / 2 or py < BOARD[1] + w / 2 or px > BOARD[2] - w / 2 or py > BOARD[3] - w / 2:
            return f"board@({px:.2f},{py:.2f})"
    # 焊盘
    for p in pads:
        if p["net"] == net:
            continue  # 同网搭接合法
        for k in range(n + 1):
            px = p1[0] + (p2[0] - p1[0]) * k / n
            py = p1[1] + (p2[1] - p1[1]) * k / n
            ddx = max(p["x"] - p["hw"] - px, 0, px - (p["x"] + p["hw"]))
            ddy = max(p["y"] - p["hh"] - py, 0, py - (p["y"] + p["hh"]))
            d = math.hypot(ddx, ddy) - w / 2
            if d < CLR - SAFETY:  # 焊盘按实测口径 CLR_NET
                if d < -0.01:
                    return f"pad-inside-{p['dz']}.{p['n']}({p['net']})@({px:.2f},{py:.2f})"
                return f"pad-{p['dz']}.{p['n']}({p['net']})d={d:.3f}@({px:.2f},{py:.2f})"
    # 已布走线（同网允许）
    for t in tracks:
        if t["net"] == net:
            continue
        # 粗判 bbox
        bx0 = min(t["p1"][0], t["p2"][0]) - (t["w"] + w) / 2 - CLR
        bx1 = max(t["p1"][0], t["p2"][0]) + (t["w"] + w) / 2 + CLR
        by0 = min(t["p1"][1], t["p2"][1]) - (t["w"] + w) / 2 + CLR
        by1 = max(t["p1"][1], t["p2"][1]) + (t["w"] + w) / 2 + CLR
        L2 = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        n2 = max(2, int(L2 / 0.06) + 1)
        for k in range(n2 + 1):
            px = p1[0] + (p2[0] - p1[0]) * k / n2
            py = p1[1] + (p2[1] - p1[1]) * k / n2
            if not (bx0 <= px <= bx1 and by0 <= py <= by1):
                continue
            d = pt_seg_t(px, py, t["p1"], t["p2"]) - (t["w"] + w) / 2
            if d < CLR:
                return f"track-{t['net']}d={d:.3f}@({px:.2f},{py:.2f})"
    # GND 孔
    for (vx, vy) in gnd_vias:
        for k in range(n + 1):
            px = p1[0] + (p2[0] - p1[0]) * k / n
            py = p1[1] + (p2[1] - p1[1]) * k / n
            d = math.hypot(px - vx, py - vy) - w / 2 - VIA_RAD
            if d < CLR:
                return f"gndvia@({vx:.2f},{vy:.2f})d={d:.3f}@({px:.2f},{py:.2f})"
    return None


def pt_seg_t(px, py, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - a[0], py - a[1])
    t = max(0, min(1, ((px - a[0]) * dx + (py - a[1]) * dy) / L2))
    return math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy))


def astar(start, goal, w, net, pads, tracks, gnd_vias, grid=0.05, max_expand=400000):
    """45° A*：8 邻域（4 直 + 4 对角）。start/goal 任务系 mm。"""
    import heapq
    def h(p):
        dx, dy = abs(p[0] - goal[0]), abs(p[1] - goal[1])
        return (dx + dy) + (math.sqrt(2) - 2) * min(dx, dy)  # 八方向一致启发
    start_i = (round(start[0] / grid), round(start[1] / grid))
    goal_i = (round(goal[0] / grid), round(goal[1] / grid))
    openq = [(h(start_i), 0, start_i)]
    came = {start_i: None}
    gcost = {start_i: 0}
    expand = 0
    DIRS = [(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]
    while openq and expand < max_expand:
        f, g, cur = heapq.heappop(openq)
        if cur == goal_i:
            path = []
            while cur:
                path.append((cur[0] * grid, cur[1] * grid))
                cur = came[cur]
            return path[::-1], True
        expand += 1
        if expand % 50000 == 0:
            print(f"  ...expand {expand}", file=sys.stderr)
        cx, cy = cur
        for dx, dy in DIRS:
            ni = (cx + dx, cy + dy)
            if ni in came:
                continue
            nx, ny = ni[0] * grid, ni[1] * grid
            step = grid * (math.sqrt(2) if dx and dy else 1)
            np1 = (cx * grid, cy * grid)
            np2 = (nx, ny)
            v = blocked(np1, np2, w, net, pads, tracks, gnd_vias)
            if v:
                continue
            # 简化平滑代价：减少拐角（对角优先）
            cost = step
            if (dx, dy) in ((1,1),(1,-1),(-1,1),(-1,-1)):
                cost *= 1.02
            gcost[ni] = g + cost
            came[ni] = cur
            heapq.heappush(openq, (g + cost + h(ni), g + cost, ni))
        # 上限保护
    return None, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", required=True)
    ap.add_argument("--dump", default="reports/pcb_pads_dump_g4b_j5.json")
    ap.add_argument("--audit", required=True, help="当前画布审计 JSON（_audit_*.json）")
    ap.add_argument("--from", dest="frm", required=True, help="起点 mm 'x,y'")
    ap.add_argument("--to", dest="to", required=True, help="终点 mm 'x,y'")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    pads, tracks, gnd_vias = load_state(args.dump, args.audit)
    s = tuple(float(v) for v in args.frm.split(","))
    g = tuple(float(v) for v in args.to.split(","))
    path, ok = astar(s, g, TRACK_W, args.net, pads, tracks, gnd_vias)
    if not ok:
        print(json.dumps({"net": args.net, "blocked": True}))
        sys.exit(1)
    # 折线简化：保方向变化点
    def dirof(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        return (round(dx / 0.05), round(dy / 0.05))
    keep = [path[0]]
    for i in range(1, len(path) - 1):
        if dirof(path[i-1], path[i]) != dirof(path[i], path[i+1]):
            keep.append(path[i])
    keep.append(path[-1])
    segs = []
    total = 0.0
    for i in range(len(keep) - 1):
        a, b = keep[i], keep[i+1]
        segs.append({"x1": round(a[0], 3), "y1": round(a[1], 3),
                     "x2": round(b[0], 3), "y2": round(b[1], 3), "w": TRACK_W})
        total += math.hypot(b[0]-a[0], b[1]-a[1])
    out = {"net": args.net, "segs": segs, "total_mm": round(total, 2), "blocked": False}
    p = Path(args.out) if args.out else Path(f"reports/route_astar_{args.net}.json")
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"net": args.net, "segs": len(segs), "total_mm": out["total_mm"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
