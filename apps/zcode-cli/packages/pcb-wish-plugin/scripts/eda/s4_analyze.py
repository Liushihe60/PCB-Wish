#!/usr/bin/env python3
"""s4_analyze.py — S4 离线几何分析器：每网连通性（union-find）+ 悬空端 + 孤段 + 网宽统计。

输入:
  --dump   reports/pcb_pads_dump_s4in.json（59 件焊盘，canvas mil，含 prot）
  --audit  reports/_s4_tracks_vias_*.json（canvas mil lines/vias）
  --assert spec/assertions.json（net_members 目标成员集）
输出:
  reports/_s4_conn_analysis.json（全量）+ 控制台摘要

口径:
  - 任务系 mm（x=mil/39.37, y=30−mil/39.37）；线宽 mil→mm。
  - 同网连通: 段端重合(≤0.02)/端点落同网焊盘矩形(+0.02)/端点 T 搭同网段体(≤(w1+w2)/2+0.02)/
    过孔心距同网段走廊≤(dia/2+w/2−0.05 过盈要求)/过孔心在同网焊盘矩形(+0.05)/同网焊盘矩形相交/
    过孔-过孔相交。异层段不直连（须过孔）。
  - 悬空端(stub end): 段端不触任何同网焊盘/段端/过孔/T 搭。
  - 孤段组: 不含任何焊盘的连通组。
"""
import argparse
import json
import math
from pathlib import Path

MIL = 39.37
EPS = 0.02


def to_task_xy(mx, my):
    return mx / MIL, 30.0 - my / MIL


def load_pads(dump_path):
    pads = []
    dump = json.loads(Path(dump_path).read_text(encoding="utf-8"))
    for c in dump:
        for p in c.get("pads", []):
            pd = p.get("pad") or [0, 10, 10]
            w, h = pd[1], (pd[2] if len(pd) > 2 else pd[1])
            prot = math.radians((p.get("prot") or 0))
            cc, ss = abs(math.cos(prot)), abs(math.sin(prot))
            hw, hh = w * cc / 2 / MIL + h * ss / 2 / MIL, w * ss / 2 / MIL + h * cc / 2 / MIL
            x, y = to_task_xy(p["x"], p["y"])
            pads.append({"key": f"{c['dz']}.{p['n']}", "dz": c["dz"], "n": str(p["n"]),
                         "net": p.get("net") or "", "x": x, "y": y, "hw": hw, "hh": hh,
                         "rect": [x - hw, y - hh, x + hw, y + hh]})
    return pads


def load_wires(audit_path):
    aud = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    lines, vias = [], []
    for L in aud["lines"]:
        x1, y1 = to_task_xy(L["sx"], L["sy"])
        x2, y2 = to_task_xy(L["ex"], L["ey"])
        lines.append({"id": L["id"], "net": L["net"], "layer": L["layer"], "w": L["w"] / MIL,
                      "p1": (x1, y1), "p2": (x2, y2),
                      "len": math.hypot(x2 - x1, y2 - y1)})
    for V in aud["vias"]:
        x, y = to_task_xy(V["x"], V["y"])
        vias.append({"id": V["id"], "net": V["net"], "x": x, "y": y,
                     "r": (V.get("dia") or 24) / 2 / MIL})
    return lines, vias


def pt_seg_dist(px, py, a, b):
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


def rects_intersect(r1, r2, pad=0.0):
    return not (r1[2] < r2[0] - pad or r2[2] < r1[0] - pad or
                r1[3] < r2[1] - pad or r2[3] < r1[1] - pad)


class UF:
    def __init__(self):
        self.p = {}

    def find(self, a):
        while self.p.setdefault(a, a) != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def analyze(pads, lines, vias, members_by_net):
    nets = sorted(members_by_net.keys())
    result = {}
    for net in nets:
        npads = [p for p in pads if p["net"].upper() == net.upper()]
        nlines = [i for i, L in enumerate(lines) if L["net"].upper() == net.upper()]
        nvias = [i for i, V in enumerate(vias) if V["net"].upper() == net.upper()]
        uf = UF()
        for p in npads:
            uf.find(("pad", p["key"]))
        for i in nlines:
            uf.find(("L", i))
        for i in nvias:
            uf.find(("V", i))
        # pad-pad 重叠（同网）
        for a in range(len(npads)):
            for b in range(a + 1, len(npads)):
                if rects_intersect(npads[a]["rect"], npads[b]["rect"]):
                    uf.union(("pad", npads[a]["key"]), ("pad", npads[b]["key"]))
        # via-pad
        for i in nvias:
            V = vias[i]
            for p in npads:
                if rect_dist(V["x"], V["y"], p["rect"]) <= 0.05:
                    uf.union(("V", i), ("pad", p["key"]))
        # via-via
        for a in range(len(nvias)):
            for b in range(a + 1, len(nvias)):
                Va, Vb = vias[nvias[a]], vias[nvias[b]]
                if math.hypot(Va["x"] - Vb["x"], Va["y"] - Vb["y"]) <= Va["r"] + Vb["r"]:
                    uf.union(("V", nvias[a]), ("V", nvias[b]))
        # line-line 同层
        for a in range(len(nlines)):
            for b in range(a + 1, len(nlines)):
                La, Lb = lines[nlines[a]], lines[nlines[b]]
                if La["layer"] != Lb["layer"]:
                    continue
                hit = False
                for (px, py) in (La["p1"], La["p2"]):
                    if pt_seg_dist(px, py, Lb["p1"], Lb["p2"]) <= (La["w"] + Lb["w"]) / 2 + EPS:
                        hit = True
                for (px, py) in (Lb["p1"], Lb["p2"]):
                    if pt_seg_dist(px, py, La["p1"], La["p2"]) <= (La["w"] + Lb["w"]) / 2 + EPS:
                        hit = True
                if hit:
                    uf.union(("L", nlines[a]), ("L", nlines[b]))
        # line-pad
        for i in nlines:
            L = lines[i]
            for p in npads:
                hit = False
                for (px, py) in (L["p1"], L["p2"]):
                    if rect_dist(px, py, p["rect"]) <= EPS:
                        hit = True
                # 段体穿焊盘（中点采样）
                if not hit:
                    for f in (0.25, 0.5, 0.75):
                        px = L["p1"][0] + (L["p2"][0] - L["p1"][0]) * f
                        py = L["p1"][1] + (L["p2"][1] - L["p1"][1]) * f
                        if rect_dist(px, py, p["rect"]) <= L["w"] / 2:
                            hit = True
                            break
                if hit:
                    uf.union(("L", i), ("pad", p["key"]))
        # via-line（过孔心在段走廊内=过孔搭接，含端点）
        for i in nvias:
            V = vias[i]
            for j in nlines:
                L = lines[j]
                if pt_seg_dist(V["x"], V["y"], L["p1"], L["p2"]) <= V["r"] + L["w"] / 2 - 0.05:
                    uf.union(("V", i), ("L", j))
        # 汇总
        pad_groups = {}
        for p in npads:
            pad_groups.setdefault(uf.find(("pad", p["key"])), set()).add(p["key"])
        connected = set()
        for v in pad_groups.values():
            connected |= v
        member_set = set(members_by_net[net])
        # 连通判据：全部成员同属一组。取含成员最多组为主组，其余成员=断连成员。
        main_group, main_members = None, set()
        for g, v in pad_groups.items():
            mv = v & member_set
            if len(mv) > len(main_members):
                main_group, main_members = g, mv
        disconnected = sorted(member_set - main_members) if pad_groups else sorted(member_set)
        # 悬空端
        stub_ends = []
        for i in nlines:
            L = lines[i]
            for (px, py) in (L["p1"], L["p2"]):
                ok = False
                for p in npads:
                    if rect_dist(px, py, p["rect"]) <= EPS:
                        ok = True
                        break
                if not ok:
                    for j in nlines:
                        if j == i or lines[j]["layer"] != L["layer"]:
                            continue
                        Lj = lines[j]
                        if math.hypot(px - Lj["p1"][0], py - Lj["p1"][1]) <= EPS or \
                           math.hypot(px - Lj["p2"][0], py - Lj["p2"][1]) <= EPS:
                            ok = True
                            break
                        if pt_seg_dist(px, py, Lj["p1"], Lj["p2"]) <= (L["w"] + Lj["w"]) / 2 + EPS:
                            ok = True
                            break
                if not ok:
                    for j in nvias:
                        V = vias[j]
                        if math.hypot(px - V["x"], py - V["y"]) <= EPS:
                            ok = True
                            break
                if not ok:
                    stub_ends.append({"seg": lines[i]["id"], "at": [round(px, 3), round(py, 3)],
                                      "layer": L["layer"]})
        # 孤段组（组内无焊盘）
        orphans = []
        all_groups = {}
        for i in nlines:
            all_groups.setdefault(uf.find(("L", i)), {"segs": [], "pads": set()})
            all_groups[uf.find(("L", i))]["segs"].append(lines[i]["id"])
        for i in nvias:
            g = uf.find(("V", i))
            all_groups.setdefault(g, {"segs": [], "pads": set()})
        for p in npads:
            g = uf.find(("pad", p["key"]))
            if g in all_groups:
                all_groups[g]["pads"].add(p["key"])
        for g, v in all_groups.items():
            if not v["pads"]:
                orphans.append(v["segs"])
        result[net] = {
            "members": members_by_net[net],
            "connected_pads": sorted(connected),
            "pad_groups": [sorted(v) for v in pad_groups.values()],
            "disconnected_members": disconnected,
            "n_pad_groups": len(pad_groups),
            "seg_count": len(nlines),
            "via_count": len(nvias),
            "copper_mm": round(sum(lines[i]["len"] for i in nlines), 2),
            "width_range_mm": ([round(min(lines[i]["w"] for i in nlines), 4),
                                round(max(lines[i]["w"] for i in nlines), 4)] if nlines else None),
            "stub_ends": stub_ends,
            "orphan_groups": orphans,
        }
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="reports/pcb_pads_dump_s4in.json")
    ap.add_argument("--audit", required=True)
    ap.add_argument("--assert", dest="assertf", default="spec/assertions.json")
    ap.add_argument("--out", default="reports/_s4_conn_analysis.json")
    args = ap.parse_args()
    here = Path("E:/agent project/projects/flexiheal-v1")
    pads = load_pads(str(here / args.dump))
    lines, vias = load_wires(str(here / args.audit))
    a = json.loads((here / args.assertf).read_text(encoding="utf-8"))
    members_by_net = {x["net"]: x["members"] for x in a["net_members"]}
    res = analyze(pads, lines, vias, members_by_net)
    (here / args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    n_ok = 0
    for net in sorted(res):
        r = res[net]
        done = (not r["disconnected_members"]) and r["seg_count"] > 0
        flag = "OK " if done else "DIS"
        if net == "GND":
            flag = "GND"
        note = ""
        if r["stub_ends"]:
            note += f" stub={len(r['stub_ends'])}"
        if r["orphan_groups"]:
            note += f" orphangrp={len(r['orphan_groups'])}"
        print(f"[{flag}] {net:10s} segs={r['seg_count']:3d} vias={r['via_count']:2d} "
              f"len={r['copper_mm']:7.2f}mm groups={r['n_pad_groups']} "
              f"disc={r['disconnected_members'] if r['disconnected_members'] else ''}"
              f" w={r['width_range_mm']}{note}")
        if done:
            n_ok += 1
    print(f"\n非 GND 连通网 {n_ok}/36；明细 → {args.out}")


if __name__ == "__main__":
    main()
