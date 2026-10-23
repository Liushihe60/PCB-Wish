#!/usr/bin/env python3
"""s5_terrain.py — S5 T1.3 逃逸地形测绘（带障碍物身份）。

对 11 个 U1 aQFN73 细间距焊盘，逐个做多方向径向射线扫描：
- 每方向沿射线步进，记录第一个障碍物（异网走线/过孔/异网焊盘）的 **primitiveId + 网名 + 距离**；
- 计算该方向上 0.1mm 细线可安全延伸的最大长度（DRC 口径：
  Track↔Track 0.102 / Track↔Pad 0.152 / Track↔Via 0.152）；
- 额外给出"径向朝芯片外"方向（outward）专项评估。

输出: reports/_s5_escape_terrain.json
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

TARGETS = [
    ("U1.B13", "BTN1_N", "B13", "inner"), ("U1.B11", "BTN2_N", "B11", "inner"),
    ("U1.D23", "DEC3_N", "D23", "inner"), ("U1.E24", "DEC456_N", "E24", "outer"),
    ("U1.N24", "DEC5b_N", "N24", "outer"), ("U1.AC5", "DECUSB_N", "AC5", "inner"),
    ("U1.AC13", "RESET_N", "AC13", "inner"), ("U1.K2", "SCL", "K2", "inner"),
    ("U1.AD4", "USB_DM_P", "AD4", "outer"), ("U1.AD2", "VBUS", "AD2", "outer"),
    ("U1.A23", "XC2", "A23", "outer"),
]
CHIP_CX, CHIP_CY = 21.996, 13.998


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


class Env:
    def __init__(self, snap, exclude_net):
        self.tracks = []   # (net, layer, ax, ay, bx, by, halfw, id)
        self.vias = []     # (net, cx, cy, r, id)
        self.pads = []     # (net, layer, cx, cy, hw, hh, r, id, dz, num)
        for L in snap["lines"]:
            if L.get("err") or L.get("net") == exclude_net:
                continue
            self.tracks.append((L.get("net"), L.get("layer"), L["sx"] / MIL, 30 - L["sy"] / MIL,
                                L["ex"] / MIL, 30 - L["ey"] / MIL, L["w"] / MIL / 2, L["id"]))
        for V in snap["vias"]:
            if V.get("err") or V.get("net") == exclude_net:
                continue
            self.vias.append((V.get("net"), V["x"] / MIL, 30 - V["y"] / MIL,
                              V["dia"] / MIL / 2, V["id"]))
        for P in snap["pads"]:
            if P.get("err") or P.get("net") == exclude_net:
                continue
            sh = P.get("pad") or []
            kind = sh[0] if sh else "ELLIPSE"
            w = sh[1] / MIL if len(sh) > 1 else 0.25
            h = sh[2] / MIL if len(sh) > 2 else w
            cx, cy = P["x"] / MIL, 30 - P["y"] / MIL
            layer = P.get("layer")
            if kind == "ELLIPSE":
                self.pads.append((P.get("net"), layer, cx, cy, 0, 0, max(w, h) / 2,
                                  P["id"], "", str(P.get("padNumber"))))
            else:
                self.pads.append((P.get("net"), layer, cx, cy, w / 2, h / 2, 0,
                                  P["id"], "", str(P.get("padNumber"))))

    def probe(self, px, py, layer):
        """返回 (最小可用净距, 障碍描述)。净距 = 到异网铜表面距离 − 规则间距。"""
        best, desc = 1e9, None
        for (net, lay, ax, ay, bx, by, hw, pid) in self.tracks:
            if lay != layer:
                continue
            c = seg_dist(px, py, ax, ay, bx, by) - hw - CLR_TRACK
            if c < best:
                best = c
                desc = {"type": "track", "net": net, "id": pid}
        for (net, cx, cy, r, pid) in self.vias:
            c = max(math.hypot(px - cx, py - cy) - r, 0) - CLR_VIA
            if c < best:
                best = c
                desc = {"type": "via", "net": net, "id": pid}
        for (net, lay, cx, cy, hw, hh, r, pid, dz, num) in self.pads:
            if lay not in (1, 12):
                continue
            if r > 0:
                d = max(math.hypot(px - cx, py - cy) - r, 0)
            else:
                dx = max(abs(px - cx) - hw, 0.0)
                dy = max(abs(py - cy) - hh, 0.0)
                d = math.hypot(dx, dy)
            c = d - CLR_PAD
            if c < best:
                best = c
                desc = {"type": "pad", "net": net, "id": pid}
        return best, desc


def scan(env, cx, cy, ang, step=0.005, maxlen=4.0, half=0.05):
    ux, uy = math.cos(ang), math.sin(ang)
    t = 0.0
    reach = 0.0
    obs = None
    od = None
    while t <= maxlen:
        px, py = cx + ux * t, cy + uy * t
        c, d = env.probe(px, py, 1)
        if c < half:
            obs, od = d, round(t, 3)
            break
        reach = t
        t += step
    return reach, obs, od


def main():
    snap = load_json("_s5_snapshot_pre_escape.json")
    pads_dump = load_json("pcb_pads_dump_s5in.json")
    u1 = [c for c in pads_dump if c["dz"] == "U1"][0]
    pad_by_num = {str(p["n"]): (p["x"] / MIL, 30 - p["y"] / MIL) for p in u1["pads"]}

    N_DIR = 72
    out = {"meta": {"clearances_mm": {"track_track": CLR_TRACK, "track_pad": CLR_PAD,
                                      "track_via": CLR_VIA},
                    "probe_width_mm": 0.1, "probe_half_mm": 0.05,
                    "directions": N_DIR, "step_mm": 0.005, "max_scan_mm": 4.0,
                    "chip_center_task_mm": [CHIP_CX, CHIP_CY],
                    "note": "free_len = 该方向上 0.1mm 细线中轴可安全延伸的最大长度（已扣规则间距与半宽）"},
           "pads": {}}

    for key, net, num, ring in TARGETS:
        cx, cy = pad_by_num[num]
        env = Env(snap, net)
        # 径向朝芯片外方向（从芯片中心指向焊盘，再往外）
        out_ang = math.atan2(cy - CHIP_CY, cx - CHIP_CX)
        dirs = []
        for k in range(N_DIR):
            ang = 2 * math.pi * k / N_DIR
            reach, obs, od = scan(env, cx, cy, ang)
            dirs.append({"deg": round(math.degrees(ang), 2),
                         "ux": round(math.cos(ang), 4), "uy": round(math.sin(ang), 4),
                         "free_len_mm": round(reach, 3),
                         "first_obstacle": obs, "first_obstacle_dist_mm": od})
        orx, ory = math.cos(out_ang), math.sin(out_ang)
        orreach, orobs, orod = scan(env, cx, cy, out_ang)
        # 径向朝芯片内方向（参考）
        in_ang = out_ang + math.pi
        inreach, inobs, inod = scan(env, cx, cy, in_ang)
        out["pads"][key] = {
            "net": net, "pad_number": num, "ring": ring,
            "center_task_mm": [round(cx, 3), round(cy, 3)],
            "outward_deg": round(math.degrees(out_ang), 2),
            "outward_free_mm": round(orreach, 3),
            "outward_obstacle": orobs, "outward_obstacle_dist_mm": orod,
            "inward_free_mm": round(inreach, 3),
            "inward_obstacle": inobs, "inward_obstacle_dist_mm": inod,
            "dirs": dirs,
            "top_dirs": sorted(dirs, key=lambda d: -d["free_len_mm"])[:8],
        }

    save_json("_s5_escape_terrain.json", out)
    for key, net, num, ring in TARGETS:
        p = out["pads"][key]
        t = p["top_dirs"][0]
        print(f"{key:9s} {net:10s} {ring:5s} OUT({p['outward_deg']:6.1f}) free={p['outward_free_mm']:.2f} "
              f"obst={p['outward_obstacle']}  |  BEST {t['deg']:6.1f} free={t['free_len_mm']:.2f} "
              f"obst={t['first_obstacle']}")


if __name__ == "__main__":
    main()
