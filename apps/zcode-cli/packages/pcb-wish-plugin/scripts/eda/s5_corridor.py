#!/usr/bin/env python3
"""s5_corridor.py — S5 T1.3b 逃逸走廊分析：逐焊盘逐方向列出"阻挡走廊的图元清单"。

对每个目标焊盘：
  - 对每个候选方向（边缘法向优先 + 全 72 向），模拟一条 0.1mm 细线从焊盘中心走出，
    记录沿途把净距压到阈值以下的**具体图元**（网名 + primitiveId + 类型）；
  - 输出：走廊可走长度、阻挡图元清单（去重）、以及"是否穿出芯片轮廓"。

输出: reports/_s5_corridor_scan.json（补充 _s5_escape_terrain.json）
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s5_lib import load_json, save_json  # noqa: E402

MIL = 39.3700787402
CLR_TRACK = 0.10199878
CLR_PAD = 0.15200122
CLR_VIA = 0.15200122
HALF = 0.05           # 0.1mm 线半宽
CHIP = (21.996, 13.998)

TARGETS = [
    ("U1.B13", "BTN1_N", "B13", "inner"), ("U1.B11", "BTN2_N", "B11", "inner"),
    ("U1.D23", "DEC3_N", "D23", "inner"), ("U1.E24", "DEC456_N", "E24", "outer"),
    ("U1.N24", "DEC5b_N", "N24", "outer"), ("U1.AC5", "DECUSB_N", "AC5", "inner"),
    ("U1.AC13", "RESET_N", "AC13", "inner"), ("U1.K2", "SCL", "K2", "inner"),
    ("U1.AD4", "USB_DM_P", "AD4", "outer"), ("U1.AD2", "VBUS", "AD2", "outer"),
    ("U1.A23", "XC2", "A23", "outer"),
]
# 边缘法向：外环焊盘的"正外逃逸方向"（芯片为方形，按所在边定法向）
EDGE_NORMAL = {
    "U1.E24": 270.0, "U1.N24": 270.0, "U1.B24": 270.0,
    "U1.AD2": 0.0, "U1.AD4": 0.0, "U1.AD6": 0.0,
    "U1.A23": 180.0, "U1.A22": 180.0, "U1.A12": 180.0,
    "U1.B13": 180.0, "U1.B11": 180.0, "U1.B5": 180.0,
    "U1.AC5": 0.0, "U1.AC13": 0.0,
    "U1.D23": 270.0, "U1.K2": 90.0,
}


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


class Env:
    def __init__(self, snap, exclude_net):
        self.items = []   # (kind, net, layer, id, data...)
        for L in snap["lines"]:
            if L.get("err") or L.get("net") == exclude_net:
                continue
            self.items.append(("track", L.get("net"), L.get("layer"), L["id"],
                               L["sx"] / MIL, 30 - L["sy"] / MIL,
                               L["ex"] / MIL, 30 - L["ey"] / MIL, L["w"] / MIL / 2))
        for V in snap["vias"]:
            if V.get("err") or V.get("net") == exclude_net:
                continue
            self.items.append(("via", V.get("net"), 12, V["id"],
                               V["x"] / MIL, 30 - V["y"] / MIL, V["dia"] / MIL / 2))
        for P in snap["pads"]:
            if P.get("err") or P.get("net") == exclude_net:
                continue
            sh = P.get("pad") or []
            kind = sh[0] if sh else "ELLIPSE"
            w = sh[1] / MIL if len(sh) > 1 else 0.25
            h = sh[2] / MIL if len(sh) > 2 else w
            lay = P.get("layer")
            if lay not in (1, 12):
                continue
            if kind == "ELLIPSE":
                self.items.append(("pad", P.get("net"), lay, P["id"],
                                   P["x"] / MIL, 30 - P["y"] / MIL, max(w, h) / 2))
            else:
                self.items.append(("pad", P.get("net"), lay, P["id"],
                                   P["x"] / MIL, 30 - P["y"] / MIL,
                                   w / 2, h / 2))

    def probe(self, px, py, layer):
        """返回 (min_clear, [(kind,net,id,clr), ... 所有低于阈值的障碍])"""
        best = 1e9
        viol = []
        for it in self.items:
            kind, net, lay, pid = it[0], it[1], it[2], it[3]
            if kind == "track":
                if lay != layer:
                    continue
                d = seg_dist(px, py, it[4], it[5], it[6], it[7]) - it[8]
                c = d - CLR_TRACK
            elif kind == "via":
                d = max(math.hypot(px - it[4], py - it[5]) - it[6], 0)
                c = d - CLR_VIA
            else:
                if lay not in (1, 12):
                    continue
                if len(it) > 7:
                    dx = max(abs(px - it[4]) - it[6], 0.0)
                    dy = max(abs(py - it[5]) - it[7], 0.0)
                    d = math.hypot(dx, dy)
                else:
                    d = max(math.hypot(px - it[4], py - it[5]) - it[6], 0)
                c = d - CLR_PAD
            if c < best:
                best = c
            if c < HALF:
                viol.append({"kind": kind, "net": net, "id": pid, "clr": round(c, 4)})
        return best, viol


def main():
    snap = load_json("_s5_snapshot_pre_escape.json")
    pads_dump = load_json("pcb_pads_dump_s5in.json")
    u1 = [c for c in pads_dump if c["dz"] == "U1"][0]
    pad_by_num = {str(p["n"]): (p["x"] / MIL, 30 - p["y"] / MIL) for p in u1["pads"]}

    out = {"meta": {"probe_width_mm": 0.1, "step_mm": 0.01, "max_mm": 3.0,
                    "rule": {"track": CLR_TRACK, "pad": CLR_PAD, "via": CLR_VIA}},
           "pads": {}}

    for key, net, num, ring in TARGETS:
        cx, cy = pad_by_num[num]
        env = Env(snap, net)
        cands = []
        angs = set()
        if key in EDGE_NORMAL:
            a = EDGE_NORMAL[key]
            for d in (-40, -25, -12, 0, 12, 25, 40):
                angs.add((a + d) % 360)
        for k in range(72):
            angs.add(k * 5.0)
        for adeg in sorted(angs):
            ang = math.radians(adeg)
            ux, uy = math.cos(ang), math.sin(ang)
            t = 0.0
            blockers = {}
            reach = 0.0
            first_hard = None
            while t <= 3.0:
                px, py = cx + ux * t, cy + uy * t
                best, viol = env.probe(px, py, 1)
                if viol:
                    for v in viol:
                        blockers[(v["kind"], v["net"], v["id"])] = v
                    if first_hard is None:
                        first_hard = round(t, 3)
                    break
                reach = t
                t += 0.01
            cands.append({"deg": round(adeg, 1),
                          "free_len_mm": round(reach, 3),
                          "blocked_at_mm": first_hard,
                          "blockers": sorted(blockers.values(),
                                             key=lambda v: (v["kind"], v["net"], v["id"]))})
        cands.sort(key=lambda c: -c["free_len_mm"])
        out["pads"][key] = {"net": net, "ring": ring,
                            "center_task_mm": [round(cx, 3), round(cy, 3)],
                            "cands": cands[:14]}

    save_json("_s5_corridor_scan.json", out)
    for key, net, num, ring in TARGETS:
        p = out["pads"][key]
        print(f"== {key} {net} ({ring})")
        for c in p["cands"][:4]:
            bl = ", ".join(f"{v['kind']}:{v['net']}:{v['id'][:8]}" for v in c["blockers"][:5])
            print(f"   {c['deg']:6.1f} free={c['free_len_mm']:.2f} @ {c['blocked_at_mm']}  {bl}")


if __name__ == "__main__":
    main()
