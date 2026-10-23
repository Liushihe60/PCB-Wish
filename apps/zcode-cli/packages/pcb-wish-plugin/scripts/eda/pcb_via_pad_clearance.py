#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pcb_via_pad_clearance.py — 过孔↔焊盘 净距/禁 via-in-pad 体检。

**为什么需要它**（2026-10-22 用户指示，flexiheal-v2 固化）：
  "过孔最好也不要紧挨着焊盘旁边" —— via-in-pad 只堵住了**铜面重叠**这一种最坏情况，
  但**过孔紧贴焊盘**同样是制造缺陷：
  ① 手焊/回流时焊锡沿铜面被**吸进过孔孔洞**（吸锡 / wicking），焊点发虚、虚焊；
  ② 过孔铜盘挤占焊盘的可上锡面积，0603 这类小焊盘尤其致命；
  ③ 同网相邻不报 DRC，**不会自己暴露**（异网才走 clearance 规则）。
  所以必须有一条**独立于 DRC 的几何体检**，按铜皮几何自己算。

**判据**（全部确定性，不许凭感觉）：
  - `overlap`（via-in-pad）：via 铜盘圆心与焊盘真实外接矩形**相交** → **禁止**。
    焊盘真实几何口径 = 本地 w/h 按**焊盘自转角 prot** 旋出的矩形（不是 AABB 近似）。
  - `diff_net`：异网 via 铜面↔焊盘铜面净距 **≥ 0.254mm**（与板级 DRC Via↔Pad 规则同值）。
  - `same_net`：同网 **孔边**↔焊盘铜面净距 **≥ 0.5mm**（手焊防吸锡工程下限），
    等价于 via 铜面净距 ≥ 0.5 - (via_r - hole_r)。
    推荐值 0.8mm（`same_net_rec` 档），作为布局选点时的优选目标。

**输入**：一份"画布全量快照"JSON（含 `vias[]` 与 `pads[]`），结构见
  flexiheal-v2/scripts/g4r/g0_dump.py（`pcb_PrimitiveVia` / `pcb_PrimitiveComponent.getAllPinsByPrimitiveId`）。
  快照的 mm 坐标系：**任务系**（板左上原点、x 右、y 下），由 dump 脚本负责换算。

**输出**：`reports/_via_pad_clearance.json`（明细）+ stdout 摘要。

用法：
  python pcb_via_pad_clearance.py --dump reports/_g4r_dump_pre.json \
      --out reports/_via_pad_clearance.json [--same-net 0.5] [--same-net-rec 0.8]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

MIL = 39.3700787


# ---------------------------------------------------------------- 几何
def _rot(px, py, deg):
    """把向量按 deg 旋转（画布 y 向下，角度定义与 EDA prot 一致）。"""
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return px * c - py * s, px * s + py * c


def point_rect_distance(px, py, cx, cy, hw, hh, prot):
    """点到**旋转矩形**的最短距离（点在矩形内 → 0）。

    做法：把点平移到矩形中心 → 反向旋转 -prot → 在本地轴系做点-AABB 距离。
    本地 w 沿本地 x、h 沿本地 y（与 dump 中 pad[1]=w, pad[2]=h 一致）。
    """
    dx, dy = px - cx, py - cy
    lx, ly = _rot(dx, dy, -prot)
    ax, ay = abs(lx) - hw, abs(ly) - hh
    if ax <= 0 and ay <= 0:
        return 0.0
    return math.hypot(max(ax, 0.0), max(ay, 0.0))


def point_circle_distance(px, py, cx, cy, r):
    d = math.hypot(px - cx, py - cy)
    return max(0.0, d - r)


def pad_distance(px, py, pad):
    """点到焊盘**铜面**的最短距离（mm）。椭圆按其等效半径处理（外形半径）。"""
    cx, cy = pad["mmx"], pad["mmy"]
    shape = (pad.get("shape") or "").upper()
    w = pad.get("wmm") or 0.0
    h = pad.get("hmm") if pad.get("hmm") is not None else w
    prot = pad.get("prot") or 0.0
    if shape.startswith("ELLIPSE") or shape.startswith("CIRCLE"):
        r = max(w, h) / 2.0
        return point_circle_distance(px, py, cx, cy, r)
    return point_rect_distance(px, py, cx, cy, w / 2.0, h / 2.0, prot)


def via_copper_radius(via):
    return (via.get("diamm") or 0.0) / 2.0


def via_hole_radius(via):
    return (via.get("holemm") or 0.0) / 2.0


# ---------------------------------------------------------------- 主扫描
def scan(dump, same_net, same_net_rec, diff_net):
    vias = [v for v in dump.get("vias", []) if "err" not in v]
    pad_list = []
    for comp in dump.get("pads", []):
        if "err" in comp:
            continue
        for p in comp.get("pads", []):
            if not p.get("wmm"):
                continue
            p = dict(p)
            p["dz"] = comp.get("dz")
            pad_list.append(p)

    violations, near_same, overlaps = [], [], []
    per_via = []
    for v in vias:
        vr, hr = via_copper_radius(v), via_hole_radius(v)
        worst = None
        for p in pad_list:
            d = pad_distance(v["mmx"], v["mmy"], p)
            copper_gap = d - vr
            hole_gap = d - hr
            # **重叠判据 = 过孔铜盘与焊盘铜面相交**（copper_gap < 0），
            # 不是"过孔圆心落在焊盘内"——后者会同时漏判与误判：
            #   漏判：圆心在焊盘外但铜盘压进焊盘（R17.2 案例）；
            #   误判：椭圆焊盘按 AABB 判 → 圆心在 AABB 内但不在椭圆内（U1.18 案例）。
            if copper_gap < 0.0:
                overlaps.append({
                    "kind": "via_disk_overlaps_pad", "via_id": v["id"],
                    "via_net": v.get("net"), "via_mm": [v["mmx"], v["mmy"]],
                    "via_dia_mm": v.get("diamm"), "via_hole_mm": v.get("holemm"),
                    "comp": p["dz"], "pad": p["n"], "pad_net": p.get("net"),
                    "pad_mm": [p["mmx"], p["mmy"]], "pad_shape": p.get("shape"),
                    "copper_gap_mm": round(copper_gap, 4),
                    "hole_gap_mm": round(hole_gap, 4),
                    "center_inside_pad": d <= 0.0,
                })
                continue
            same = (v.get("net") or "") == (p.get("net") or "")
            if same:
                if hole_gap < same_net:
                    violations.append({
                        "kind": "same_net_hole_gap", "via_id": v["id"], "via_net": v.get("net"),
                        "via_mm": [v["mmx"], v["mmy"]], "comp": p["dz"], "pad": p["n"],
                        "pad_net": p.get("net"), "pad_mm": [p["mmx"], p["mmy"]],
                        "copper_gap_mm": round(copper_gap, 4),
                        "hole_gap_mm": round(hole_gap, 4), "limit_mm": same_net,
                    })
                elif hole_gap < same_net_rec:
                    near_same.append({
                        "via_id": v["id"], "via_net": v.get("net"),
                        "via_mm": [v["mmx"], v["mmy"]], "comp": p["dz"], "pad": p["n"],
                        "copper_gap_mm": round(copper_gap, 4),
                        "hole_gap_mm": round(hole_gap, 4), "rec_mm": same_net_rec,
                    })
            else:
                if copper_gap < diff_net:
                    violations.append({
                        "kind": "diff_net_copper_gap", "via_id": v["id"], "via_net": v.get("net"),
                        "via_mm": [v["mmx"], v["mmy"]], "comp": p["dz"], "pad": p["n"],
                        "pad_net": p.get("net"), "pad_mm": [p["mmx"], p["mmy"]],
                        "copper_gap_mm": round(copper_gap, 4), "limit_mm": diff_net,
                    })
            if worst is None or d < worst["dist_mm"]:
                worst = {"dist_mm": round(d, 4), "comp": p["dz"], "pad": p["n"],
                         "pad_net": p.get("net"),
                         "copper_gap_mm": round(d - vr, 4),
                         "hole_gap_mm": round(d - hr, 4)}
        per_via.append({"via_id": v["id"], "net": v.get("net"),
                        "mm": [v["mmx"], v["mmy"]], "nearest": worst})

    per_via.sort(key=lambda x: (x["nearest"] or {}).get("copper_gap_mm", 9e9))
    return {
        "criteria": {
            "via_in_pad": "禁止（via 铜盘圆心与焊盘真实旋转矩形相交）",
            "diff_net_copper_gap_mm": diff_net,
            "same_net_hole_gap_mm": same_net,
            "same_net_hole_gap_recommended_mm": same_net_rec,
            "pad_geometry": "本地 w/h 按焊盘 prot 旋出的真实矩形；椭圆按等效半径",
        },
        "n_vias": len(vias), "n_pads": len(pad_list),
        "n_overlaps": len(overlaps), "n_violations": len(violations),
        "n_near_same_net": len(near_same),
        "overlaps": overlaps, "violations": violations, "near_same_net": near_same,
        "per_via_nearest": per_via,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True)
    ap.add_argument("--out", default="_via_pad_clearance.json")
    ap.add_argument("--same-net", type=float, default=0.5)
    ap.add_argument("--same-net-rec", type=float, default=0.8)
    ap.add_argument("--diff-net", type=float, default=0.254)
    a = ap.parse_args()

    dump = json.loads(Path(a.dump).read_text(encoding="utf-8"))
    res = scan(dump, a.same_net, a.same_net_rec, a.diff_net)
    res["dump"] = a.dump
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"过孔 {res['n_vias']} / 焊盘 {res['n_pads']}")
    print(f"via-in-pad 重叠: {res['n_overlaps']}")
    for o in res["overlaps"]:
        print(f"   {o['comp']}.{o['pad']}({o['pad_net']}) via@{o['via_mm']} "
              f"copper_gap={o['copper_gap_mm']}")
    print(f"净距违例: {res['n_violations']}")
    for v in res["violations"]:
        print(f"   [{v['kind']}] {v['comp']}.{v['pad']}({v['pad_net']}) via@{v['via_mm']} "
              f"copper_gap={v.get('copper_gap_mm')} hole_gap={v.get('hole_gap_mm')} "
              f"limit={v.get('limit_mm')}")
    print(f"同网近距(推荐档 {a.same_net_rec}): {res['n_near_same_net']}")
    for n in res["near_same_net"][:20]:
        print(f"   {n['comp']}.{n['pad']} via@{n['via_mm']} hole_gap={n['hole_gap_mm']}")
    print("最贴的 10 个过孔:")
    for pv in res["per_via_nearest"][:10]:
        nn = pv["nearest"] or {}
        print(f"   via@{pv['mm']} net={pv['net']} 最近 {nn.get('comp')}.{nn.get('pad')} "
              f"copper_gap={nn.get('copper_gap_mm')} hole_gap={nn.get('hole_gap_mm')}")
    print("写出:", out)


if __name__ == "__main__":
    sys.exit(main())
