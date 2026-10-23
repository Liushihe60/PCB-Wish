#!/usr/bin/env python3
"""s4b_model.py — S4B 离线布线几何模型（JLC 规则口径）。

坐标口径：任务系 mm（板左上原点，y 向下）。
规则（JLCPCB Two Layers，D009/D015 实测读回）：
  Track↔Track 0.102 / Track↔Pad(SMD) 0.152 / Track↔Via 0.152 / Via↔Pad 0.152 /
  Via↔Via 0.152 / 板边 0.2997（取 0.30）。
同网（含 GND 锚定几何）互不设限。
板框 45.0×30.0。

主要接口:
  Model()          —— 加载焊盘(pcb_pads_dump_manual.json) + 已布状态(_s4b_state.json)
  check_track(net, layer, pts, w)  —— 校验一条折线 → 违例列表
  check_via(net, x, y, dia)        —— 校验单孔
  route_check(route)               —— 校验 route spec {segs:[{layer,pts,w}], vias:[...]}
"""
import json
import math
from pathlib import Path

MIL = 39.37
BOARD = (0.0, 0.0, 45.0, 30.0)
CLR_TRACK = 0.102
CLR_PAD = 0.152
CLR_VIA = 0.152
EDGE = 0.30

HERE = Path("E:/agent project/projects/flexiheal-v1")


def r(x):
    return round(x, 3)


def load_pads(path="reports/pcb_pads_dump_manual.json"):
    pads = []
    dump = json.loads((HERE / path).read_text(encoding="utf-8"))
    for c in dump:
        for p in c.get("pads", []):
            pd = p.get("pad") or [0, 10, 10]
            w, h = pd[1] / MIL, (pd[2] if len(pd) > 2 else pd[1]) / MIL
            prot = math.radians(p.get("prot") or 0)
            cc, ss = abs(math.cos(prot)), abs(math.sin(prot))
            hw, hh = (w * cc + h * ss) / 2, (w * ss + h * cc) / 2
            x, y = p["x"] / MIL, 30.0 - p["y"] / MIL
            pads.append({"dz": c["dz"], "pad": str(p["n"]), "net": p.get("net") or "",
                         "x": x, "y": y, "hw": hw, "hh": hh,
                         "rect": [x - hw, y - hh, x + hw, y + hh],
                         "key": f"{c['dz']}.{p['n']}"})
    return pads


def load_tracks(path="reports/_s4b_state.json"):
    st = json.loads((HERE / path).read_text(encoding="utf-8"))
    tracks = [{"net": L["net"], "layer": L["layer"], "w": L["w"] / MIL,
               "p1": (L["sx"] / MIL, 30.0 - L["sy"] / MIL),
               "p2": (L["ex"] / MIL, 30.0 - L["ey"] / MIL), "id": L["id"]}
              for L in st["lines"]]
    vias = [{"net": V["net"], "x": V["x"] / MIL, "y": 30.0 - V["y"] / MIL,
             "r": (V.get("dia") or 24) / 2.0 / MIL, "id": V["id"]}
            for V in st["vias"]]
    return tracks, vias


def samples(p1, p2, step=0.05):
    L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    n = max(1, int(L / step))
    for k in range(n + 1):
        yield (p1[0] + (p2[0] - p1[0]) * k / n, p1[1] + (p2[1] - p1[1]) * k / n)


def pt_seg(px, py, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - a[0], py - a[1])
    t = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / L2))
    return math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy))


def rect_dist(px, py, rect):
    ddx = max(rect[0] - px, 0.0, px - rect[2])
    ddy = max(rect[1] - py, 0.0, py - rect[3])
    return math.hypot(ddx, ddy)


# 已证实 SMD 的大外形焊盘（RECT 形、无通孔），L2 潜渡可从其下穿过：
L2_SMD_EXEMPT = frozenset({"U1.0", "U2.9", "U3.2", "X1.1", "X1.2", "X1.3", "X1.4"})


class Model:
    def __init__(self, extra_tracks=None, extra_vias=None, state="reports/_s4b_state.json",
                 pads=None):
        self.pads = load_pads() if pads is None else pads
        tracks, vias = load_tracks(state)
        if extra_tracks:
            tracks = tracks + extra_tracks
        if extra_vias:
            vias = vias + extra_vias
        self.tracks, self.vias = tracks, vias

    # —— 同网豁免判定 ——
    def seg_ok(self, net, layer, w, p1, p2):
        """一条线段对（其他网）的合规检查；返回违例字符串或 None。"""
        for (px, py) in samples(p1, p2):
            if px < BOARD[0] + EDGE or py < BOARD[1] + EDGE or \
               px > BOARD[2] - EDGE or py > BOARD[3] - EDGE:
                if (px, py) not in self._own_pad_rect_pts(net):
                    return f"board-edge@({px:.2f},{py:.2f})"
        for p in self.pads:
            if p["net"] == net:
                continue
            # L2 与 L1 SMD 焊盘不同层不干涉；小外形（min(hw,hh)<0.35）按 SMD 处理；
            # 其余 ≥0.35 视为可能 THT/通孔类（J1~J5/BTN/TP）双层避让（保守），
            # 但 RECT 大件焊盘（EP/晶振）已实证 SMD（L2_SMD_EXEMPT）。
            smd_excluded = (layer == 2 and (min(p["hw"], p["hh"]) < 0.35 or p["key"] in L2_SMD_EXEMPT))
            if smd_excluded:
                continue
            for (px, py) in samples(p1, p2):
                d = rect_dist(px, py, p["rect"]) - w / 2.0
                if d < CLR_PAD:
                    return f"pad-{p['key']}({p['net']})d{d:.3f}@({px:.2f},{py:.2f})"
        for t in self.tracks:
            if t["net"] == net or t["layer"] != layer:
                continue
            for (px, py) in samples(p1, p2):
                d = pt_seg(px, py, t["p1"], t["p2"]) - (t["w"] + w) / 2.0
                if d < CLR_TRACK:
                    return f"track-{t['net']}({t['id'][:8]})d{d:.3f}@({px:.2f},{py:.2f})"
        for v in self.vias:
            if v["net"] == net:
                continue
            for (px, py) in samples(p1, p2):
                d = math.hypot(px - v["x"], py - v["y"]) - w / 2.0 - v["r"]
                if d < CLR_VIA:
                    return f"via-{v['net']}({v['id'][:8]})d{d:.3f}@({px:.2f},{py:.2f})"
        return None

    def _own_pad_rect_pts(self, net):
        """端点落在自网焊盘矩形内时豁免板边判定（焊盘可能贴板边，如 J5/J1/J2）。"""
        pts = set()
        for p in self.pads:
            if p["net"] == net:
                pts.add((round(p["x"], 3), round(p["y"], 3)))
        return pts

    def via_ok(self, net, x, y, dia_mil=24):
        vr = dia_mil / 2.0 / MIL
        if x < BOARD[0] + EDGE or y < BOARD[1] + EDGE or \
           x > BOARD[2] - EDGE or y > BOARD[3] - EDGE:
            return "board-edge"
        for p in self.pads:
            if p["net"] == net:
                continue
            d = rect_dist(x, y, p["rect"]) - vr
            if d < CLR_PAD:
                return f"via-pad-{p['key']}({p['net']})d{d:.3f}"
        for layer in (1, 2):
            for t in self.tracks:
                if t["net"] == net or t["layer"] != layer:
                    continue
                d = pt_seg(x, y, t["p1"], t["p2"]) - vr - t["w"] / 2.0
                if d < CLR_VIA:
                    return f"via-track-{t['net']}({t['id'][:8]})d{d:.3f}"
        for v in self.vias:
            if v["net"] == net:
                continue
            d = math.hypot(x - v["x"], y - v["y"]) - vr - v["r"]
            if d < CLR_VIA:
                return f"via-via-{v['net']}({v['id'][:8]})d{d:.3f}"
        return None

    def check_track(self, net, layer, pts, w):
        out = []
        for i in range(len(pts) - 1):
            v = self.seg_ok(net, layer, w, tuple(pts[i]), tuple(pts[i + 1]))
            if v:
                out.append({"seg": i, "viol": v})
        return out

    def _w_mm(self, s):
        if "w_mm" in s:
            return float(s["w_mm"])
        return float(s["w_mil"]) / 39.37

    def route_check(self, route):
        """route = {"net":N,"segs":[{"layer":1,"pts":[[..],[..]],"w_mil":10}],"vias":[[x,y,24]]}"""
        vios = []
        for si, s in enumerate(route["segs"]):
            v = self.check_track(route["net"], s["layer"], s["pts"], self._w_mm(s))
            for x in v:
                vios.append({"seg": si, **x})
        for vi, (vx, vy, *rest) in enumerate(route.get("vias", [])):
            dia = rest[0] if rest else 24
            v = self.via_ok(route["net"], vx, vy, dia)
            if v:
                vios.append({"via": vi, "viol": v})
        return vios

    def route_total_mm(self, route):
        tot = 0.0
        for s in route["segs"]:
            for i in range(len(s["pts"]) - 1):
                tot += math.hypot(s["pts"][i + 1][0] - s["pts"][i][0],
                                  s["pts"][i + 1][1] - s["pts"][i][1])
        return round(tot, 2)

    def set_endpoint_ok(self, route, net):
        """连通性预检：每个 seg polyline 的首/末点须 落自网焊盘矩形(EPS≤0.05+pad半覆盖)
        或与其他自网 seg 的首/末点重合(≤0.02)（T 搭/树分支）。
        polyline 内部中间拐点=自家连续体，不检（线段物理连续）。"""
        eps = 0.02
        pad_rects = [p["rect"] for p in self.pads if p["net"] == net]
        ends = []  # [(si, pi, pt, layer)]
        for si, s in enumerate(route["segs"]):
            pts = s["pts"]
            ends.append((si, 0, tuple(pts[0]), s["layer"]))
            if len(pts) > 1:
                ends.append((si, len(pts) - 1, tuple(pts[-1]), s["layer"]))
        pad_rects_marg = [[pr[0] - eps, pr[1] - eps, pr[2] + eps, pr[3] + eps]
                          for pr in pad_rects]
        via_pts = [tuple(v[:2]) for v in route.get("vias", [])]

        def touching(ends_i):
            si, pi, pt, layer = ends_i
            for pr in pad_rects_marg:
                if rect_dist(pt[0], pt[1], pr) <= eps:
                    return True
            for vp in via_pts:
                if math.hypot(pt[0] - vp[0], pt[1] - vp[1]) <= eps:
                    return True
            for (sj, pj, p2, layer2) in ends:
                if (sj, pj) == (si, pi):
                    continue
                if math.hypot(pt[0] - p2[0], pt[1] - p2[1]) <= 0.02:
                    return True
            # T 搭：端点落在自网其他 polyline 的段体上（同层，允许搭入）
            for s2 in route["segs"]:
                if s2["layer"] != layer:
                    continue
                pts2 = s2["pts"]
                for k in range(len(pts2) - 1):
                    if pt_seg(pt[0], pt[1], tuple(pts2[k]), tuple(pts2[k + 1])) <= 0.05:
                        return True
            return False
        vios = []
        for e in ends:
            if not touching(e):
                vios.append({"seg": e[0], "pt": e[1], "at": e[2],
                             "viol": "endpoint-not-on-net-pad/backtrack"})
        return vios


if __name__ == "__main__":
    m = Model()
    print(f"pads={len(m.pads)} tracks={len(m.tracks)} vias={len(m.vias)}")
