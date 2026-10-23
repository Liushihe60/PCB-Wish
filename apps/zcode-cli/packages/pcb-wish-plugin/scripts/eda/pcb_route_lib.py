#!/usr/bin/env python3
"""pcb_route_lib.py — 布线几何共享库（G4 布线校验器）。

坐标口径：任务系 mm（板左上原点，y 向下）；canvas mil 换算 x=mil_x/39.37, y_task=30−mil_y/39.37。
障碍模型：
  - 全部 273 焊盘真实矩形（pcb_bodies_lib.pad_rects_mm，含焊盘自转 prot）；
  - 无网焊盘（'-'）与异网焊盘同权处理（EDA 会把接触的无网焊盘并入导线网络——必须避让）；
  - 同网焊盘不设间距（允许搭接）；
  - C6 天线 keepout（x34~45/y0~8，双层禁走线，器件焊盘保留除外——层 12 区域在 EDA 侧管，
    本库只做布线前几何预检）；
  - 板边内缩 EDGE_MM（Track↔Board Outline 0.2997）。
间距规则（JLCPCB Capability Two Layers 实测读回）：Track↔Track 0.102 / Track↔SMD Pad 0.152 /
Track↔Via 0.152 / 板边 0.2997。校验目标值取规则值+余量。
"""
import json
import math
from pathlib import Path

MIL = 39.37
BOARD_W, BOARD_H = 45.0, 30.0
EDGE_MM = 0.30            # 距板边（规则 0.2997）
CLR_PAD = 0.16            # 轨道↔异网焊盘（规则 0.152）
CLR_TRACK = 0.12          # 轨道↔异网轨道/过孔（规则 0.102）
KEEPOUT = (34.0, 0.0, 45.0, 8.0)  # C6 任务系

def mm2mil(v):
    return int(round(v * MIL))

def task_to_canvas_mm(x, y):
    """任务系 mm → canvas mm (y 翻转)。"""
    return x, 30.0 - y

def canvas_to_task_mm(x, y):
    return x, 30.0 - y

def load_pad_rects(paths):
    """paths: [reports/pcb_pads_dump_*.json] → {key:(dz,pad): rect} + list。
    rect=(x0,y0,x1,y1,net,dz,padn)，任务系 mm。"""
    out = []
    for p in paths:
        dump = json.loads(Path(p).read_text(encoding="utf-8"))
        for c in dump:
            for pad in c.get("pads", []):
                pd = pad.get("pad") or [0, 10, 10]
                w, h = pd[1], (pd[2] if len(pd) > 2 else pd[1])
                prot = pad.get("prot")
                th = math.radians((prot if prot is not None else (c.get("rot", 0) or 0)) or 0)
                cc, ss = abs(math.cos(th)), abs(math.sin(th))
                hw = (w * cc + h * ss) / 2 / MIL
                hh = (w * ss + h * cc) / 2 / MIL
                cx, cy = pad["x"] / MIL, 30.0 - pad["y"] / MIL
                out.append({"rect": [cx - hw, cy - hh, cx + hw, cy + hh],
                            "net": pad.get("net") or "", "dz": c["dz"], "pad": str(pad["n"])})
    return out

def load_tracks(path):
    """reports/routed_tracks.json → [{net, layer, pts_mil}] 已布轨道（画布 mil，y 翻转）。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []

def seg_rect_dist(seg, rect):
    """线段（含线宽 w 的扫掠）到矩形的净距（近似=中轴线到矩形距离 − w/2）。"""
    (x1, y1), (x2, y2) = seg[0], seg[1]
    w = seg[2]
    rx0, ry0, rx1, ry1 = rect
    # 点(x,y)到线段距离
    def pt_seg_dist(px, py):
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        if L2 == 0:
            return math.hypot(px - x1, py - y1)
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
        return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
    # 中轴线到矩形距离：矩形内部/边界=0
    def axis_dist(px, py):
        ddx = max(rx0 - px, 0.0, px - rx1)
        ddy = max(ry0 - py, 0.0, py - ry1)
        return math.hypot(ddx, ddy)
    # 采样：0.03mm 密集采样（5 点采样会漏掉长线段中段的最近点 → 误判"无违例"）
    L = math.hypot(x2 - x1, y2 - y1)
    n = max(2, int(L / 0.03) + 1)
    d = 1e9
    for k in range(n):
        f = k / (n - 1)
        dd = axis_dist(x1 + (x2 - x1) * f, y1 + (y2 - y1) * f)
        if dd < d:
            d = dd
    return d - w / 2.0

def check_track(net, layer, pts, width_mm, pads, tracks, own_ok=True):
    """校验一条折线（任务系 mm）。返回违例列表。
    pads: load_pad_rects 结果；tracks: 已布轨道 [{net,layer,pts_task}]。
    规则：异网焊盘 ≥CLR_PAD；异网轨道 ≥CLR_TRACK；keepout 双层禁入（焊盘保留除外——
    起止点在自己网焊盘上者豁免其焊盘矩形，但轨道段仍禁入 keepout）；板边 ≥EDGE_MM。"""
    viol = []
    for i in range(len(pts) - 1):
        seg = (pts[i], pts[i + 1], width_mm)
        # 板边
        for (px, py) in (pts[i], pts[i + 1]):
            if px < EDGE_MM or py < EDGE_MM or px > BOARD_W - EDGE_MM or py > BOARD_H - EDGE_MM:
                viol.append({"type": "board-edge", "at": [px, py]})
        # keepout（除两端点所在自网焊盘，轨道体禁入）
        kx0, ky0, kx1, ky1 = KEEPOUT
        if layer in (1, 2, 12):
            d = seg_rect_dist(seg, [kx0, ky0, kx1, ky1])
            if d < 0.02:  # 轨道进入 keepout（负=深入）
                viol.append({"type": "keepout", "seg": i, "dist": round(d, 3)})
        # 异网焊盘
        for p in pads:
            pnet = p["net"]
            same = (pnet == net) or (net == "" and pnet == "")
            if same:
                continue
            d = seg_rect_dist(seg, p["rect"])
            if d < CLR_PAD:
                viol.append({"type": "pad", "dz": p["dz"], "padn": p["pad"],
                             "net": pnet, "dist": round(d, 3), "seg": i})
        # 异网已布轨道
        for t in tracks:
            if t["net"] == net and layer == t["layer"]:
                continue  # 同网轨道允许相邻/搭接（树形布线）
            tpts = t["pts_task"]
            for j in range(len(tpts) - 1):
                if t["layer"] != layer:
                    continue
                d = seg_seg_dist(seg, (tpts[j], tpts[j + 1], t.get("w", 0.25)))
                if d < CLR_TRACK:
                    viol.append({"type": "track", "net": t["net"], "seg": i, "tseg": j,
                                 "dist": round(d, 3)})
    return viol

def seg_seg_dist(a, b):
    """两线段(各含宽)净距：把彼此当点采样取最小（粗但保守，正交/45° 足够）。
    为防采样漏，对每段按长度采样至 0.3mm 间隔。"""
    def samples(seg):
        (p, q, w) = seg
        L = math.hypot(q[0] - p[0], q[1] - p[1])
        n = max(2, int(L / 0.3) + 1)
        return [(p[0] + (q[0] - p[0]) * k / (n - 1), p[1] + (q[1] - p[1]) * k / (n - 1)) for k in range(n)]
    def pt_rect(px, py, rect):
        ddx = max(rect[0] - px, 0.0, px - rect[2])
        ddy = max(rect[1] - py, 0.0, py - rect[3])
        return math.hypot(ddx, ddy)
    def seg_rect(seg, rect):
        return min(pt_rect(px, py, rect) for px, py in samples(seg))
    # seg a 的中轴线到 seg b 的扫掠矩形（用平行四边形近似=对采样点用 rect of b 端点+宽）
    (p, q, w) = b
    bx0, by0 = min(p[0], q[0]) - w / 2, min(p[1], q[1]) - w / 2
    bx1, by1 = max(p[0], q[0]) + w / 2, max(p[1], q[1]) + w / 2
    d1 = seg_rect(a, (bx0, by0, bx1, by1))
    (p, q, w) = a
    ax0, ay0 = min(p[0], q[0]) - w / 2, min(p[1], q[1]) - w / 2
    ax1, ay1 = max(p[0], q[0]) + w / 2, max(p[1], q[1]) + w / 2
    d2 = seg_rect(b, (ax0, ay0, ax1, ay1))
    return min(d1, d2)
