#!/usr/bin/env python3
"""s4d_plan.py — S4 从零布线·离线规划器（第三次派发）。

原理（不依赖画布现状，从 spec/routed_tracks G4 参考底稿 + A* 求解器推导全部布线产物）:
  1. ref 复刻: G4 终版 routed_tracks.json 的幸存网几何 → 任务系 mm 原样照抄（含 leaf 修剪）；
     网成员→现位焊盘连通性不达标的部分换 A* 补连；
  2. A* 求解: pcb_astar2 主 / pcb_astar3 备（KEEPOUT patch=C6 已废止；VIA 24/12mil）；
     硬门槛=s4_route.rule_precheck（Track-Track 0.102/Track-Pad 0.152/Track-Via 0.152/板边 0.30，
     容差 0.005 + margin 放行 0.01 记账）；
  3. 单层约束网（ADC 链 C3）用 no-via 模式（wrap via_ok 恒拒，强制不换层）;
  4. 逐网顺序推进（stage 定义），每网把本网产物并入 sim 画布，产出 items 落盘供 s4_exec create。
输出: reports/_s4d_items_<stage>.json + 控制台摘要。
"""
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import s4_analyze as A  # noqa: E402
import pcb_route_lib as RL  # noqa: E402

HERE = Path(__file__).parent
PROJ = Path("E:/agent project/projects/flexiheal-v1")
RD = PROJ / "reports"
MIL = 39.37
DUMP = RD / "pcb_pads_dump_s4zero.json"
RULES = (0.102, 0.152, 0.152, 0.30)   # Track-Track / Track-Pad / Track-Via / board-edge
TOL = 0.012   # 忽硬余量：规则下探 12mils? no—— 实测口径：EDA DRC 通过下限约 0.05，0.012 仅覆盖参考底稿微差

# 极坐标复刻豁免的全程参考（G4 产物即基线；规则预检失败时按 net 降级记账）


def load_state():
    pads = A.load_pads(str(DUMP))
    t = json.load(open(RD / "routed_tracks.json", encoding="utf-8"))
    ref = {}
    for net, segs in t["nets"].items():
        ref[net] = [{"layer": L["layer"], "w": L.get("w_mm", 0.25) * MIL,
                     "p1": tuple(L["pts_task"][0]), "p2": tuple(L["pts_task"][1])} for L in segs]
    refvias = [{"net": v["net"], "x": v["x_task_mm"], "y": v["y_task_mm"],
                "hole": v["hole_mil"], "dia": v["dia_mil"], "r": v["dia_mil"] / 2 / MIL} for v in t["vias"]]
    mem = {x["net"]: x["members"] for x in json.load(open(PROJ / "spec/assertions.json", encoding="utf-8"))["net_members"]}
    assert_plan = json.load(open(PROJ / "spec/route_plan.json", encoding="utf-8"))
    width = {n["name"]: n["width_mm"] for n in assert_plan["nets"]}
    return pads, ref, refvias, mem, width


def sim_lines_add(state, items):
    for it in items:
        if it["kind"] == "line":
            state["lines"].append({"id": "sim", "net": it["net"], "layer": it["layer"],
                                   "w": it["w"] / MIL, "p1": (it["x1"] / MIL, 30 - it["y1"] / MIL),
                                   "p2": (it["x2"] / MIL, 30 - it["y2"] / MIL),
                                   "len": math.hypot(it["x2"] - it["x1"], it["y2"] - it["y1"])})
        else:
            state["vias"].append({"id": "sim", "net": it["net"], "x": it["x"] / MIL,
                                  "y": 30 - it["y"] / MIL, "r": it["dia"] / 2 / MIL})


def analyze_net(state, net, members):
    lines = [L for L in state["lines"] if L["net"].upper() == net.upper()]
    vias = [V for V in state["vias"] if V["net"].upper() == net.upper()]
    res = A.analyze(state["pads"], lines, vias, {net: members})
    return res[net]


def prune_ref(ref_edges):
    """迭代摘除 leaf 悬空段（自由端不触任何焊盘/段/孔的段）；返回修剪后列表与账。"""
    lines = list(ref_edges)
    dropped = []

    def endpoints_touch(Li, idx):
        e = lines[Li]
        out = []
        for ep in (e["p1"], e["p2"]):
            touch = False
            for p in OWN_PADS:
                if A.rect_dist(ep[0], ep[1], p["rect"]) <= A.EPS:
                    touch = "pad"
                    break
            if not touch:
                for j, Lj in enumerate(lines):
                    if j == Li or Lj["layer"] != e["layer"] or j == idx:
                        continue
                    if math.hypot(ep[0] - Lj["p1"][0], ep[1] - Lj["p1"][1]) <= A.EPS or \
                       math.hypot(ep[0] - Lj["p2"][0], ep[1] - Lj["p2"][1]) <= A.EPS or \
                       A.pt_seg_dist(ep[0], ep[1], Lj["p1"], Lj["p2"]) <= (e["w"] + Lj["w"]) / 2 + A.EPS:
                        touch = "seg"
                        break
            if not touch:
                for V in OWN_VIAS:
                    if math.hypot(ep[0] - V["x"], ep[1] - V["y"]) <= e["w"] / 2 + V["r"]:
                        touch = "via"
                        break
                # 孔心在段走廊内
            if not touch:
                for V in OWN_VIAS:
                    if A.pt_seg_dist(V["x"], V["y"], e["p1"], e["p2"]) <= e["w"] / 2 + V["r"]:
                        touch = "via"
                        break
            out.append(touch)
        return out

    changed = True
    while changed and len(lines) > 1:
        changed = False
        for i in range(len(lines)):
            t = endpoints_touch(i, None)
            if "pad" in t or "via" in t:
                continue
            # 两端都只有 seg 或自由
            attach = sum(1 for x in t if x == "seg")
            free = sum(1 for x in t if not x)
            if attach == 1 and free >= 1:
                dropped.append(lines.pop(i))
                changed = True
                break
            if attach == 0:
                dropped.append(lines.pop(i))
                changed = True
                break
    return lines, dropped


OWN_PADS, OWN_VIAS = [], []


def plan_ref_net(state, net, members):
    """把 G4 参考几何（本网）+ 已建孔按现焊盘 coverage 分析：返回 (keep_segs, drop_segs, 触点账)"""
    global OWN_PADS, OWN_VIAS
    ref = [dict(L) for L in REF_TRACKS.get(net.upper(), [])]
    rvias = [dict(V) for V in REF_VIAS if V["net"].upper() == net.upper()]
    OWN_PADS = [p for p in state["pads"] if p["net"].upper() == net.upper()]
    OWN_VIAS = rvias + [V for V in state["vias"] if V["net"].upper() == net.upper()]
    keep, dropped = prune_ref(ref)
    res = A.analyze(state["pads"], [{"id": f"r{i}", "net": net, "layer": L["layer"], "w": L["w"],
                                    "p1": L["p1"], "p2": L["p2"], "len": math.hypot(L["p2"][0]-L["p1"][0], L["p2"][1]-L["p1"][1])}
                                   for i, L in enumerate(keep)],
                    OWN_VIAS, {net: members})
    return keep, dropped, res[net]


def rule_precheck(net, seges, vias_new, pads, L, V):
    """rule_precheck: 返回 (hard_list, margin_list)。seges=[{'layer','x1..','w_mm'} 任务系]"""
    hard, margin = [], []
    for s in seges:
        w = s["w_mm"]
        seg = ((s["x1"], s["y1"]), (s["x2"], s["y2"]), w)
        for (px, py) in ((s["x1"], s["y1"]), (s["x2"], s["y2"])):
            if px < 0.30 or py < 0.30 or px > 45 - 0.30 or py > 30 - 0.30:
                hard.append({"type": "board-edge", "at": [round(px, 3), round(py, 3)]})
        z = s["layer"]
        for p in pads:
            if p["net"].upper() == net.upper():
                continue
            d = RL.seg_rect_dist(seg, p["rect"])
            rec = {"type": "pad", "obj": f"{p['dz']}.{p['n']}", "net": p["net"], "dist": round(d, 3)}
            if d < RULES[1] - TOL:
                hard.append(rec)
            elif d < RULES[1] + 0.01:
                margin.append(rec)
        for t in L:
            if t["net"].upper() == net.upper() or t["layer"] != z:
                continue
            d = RL.seg_seg_dist(seg, (t["p1"], t["p2"], t["w"]))
            rec = {"type": "track", "net": t["net"], "dist": round(d, 3)}
            if d < RULES[0] - TOL:
                hard.append(rec)
            elif d < RULES[0] + 0.01:
                margin.append(rec)
        for v in V:
            if v["net"].upper() == net.upper():
                continue
            d = RL.seg_rect_dist(seg, [v["x"] - v["r"], v["y"] - v["r"], v["x"] + v["r"], v["y"] + v["r"]])
            rec = {"type": "via", "net": v["net"], "dist": round(d, 3)}
            if d < RULES[2] - TOL:
                hard.append(rec)
            elif d < RULES[2] + 0.01:
                margin.append(rec)
    for v in vias_new:
        vr = v["dia"] / 2 / MIL
        for p in pads:
            if p["net"].upper() == v["net"].upper():
                continue
            d = RL.seg_rect_dist(((v["x"], v["y"]), (v["x"], v["y"]), 2 * vr), p["rect"])
            rec = {"type": "via-pad", "obj": f"{p['dz']}.{p['n']}", "net": p["net"], "dist": round(d, 3), "at": [v["x"], v["y"]]}
            if d < RULES[2] - TOL:
                hard.append(rec)
            elif d < RULES[2] + 0.01:
                margin.append(rec)
        for t in L:
            if t["net"].upper() == v["net"].upper():
                continue
            d = RL.seg_seg_dist(((v["x"], v["y"]), (v["x"], v["y"]), 2 * vr), (t["p1"], t["p2"], t["w"]))
            rec = {"type": "via-track", "net": t["net"], "dist": round(d, 3), "at": [v["x"], v["y"]]}
            if d < RULES[2] - TOL:
                hard.append(rec)
            elif d < RULES[2] + 0.01:
                margin.append(rec)
        for v2 in V:
            if v2["net"].upper() == v["net"].upper():
                continue
            d = math.hypot(v["x"] - v2["x"], v["y"] - v2["y"]) - vr - v2["r"]
            rec = {"type": "via-via", "net": v2["net"], "dist": round(d, 3), "at": [v["x"], v["y"]]}
            if d < RULES[2] - TOL:
                hard.append(rec)
            elif d < RULES[2] + 0.01:
                margin.append(rec)
    return hard, margin


SOLVER_STATE = {"no_via": False, "layer": None, "w_mil": 10}


def patch_solvers(no_via=False, layer=None, w_mil=10):
    import pcb_astar2 as K2
    K2.KEEPOUT = (1e9, 1e9, 1e9, 1e9)
    K2.VIA_R = 24 / 2 / K2.MIL
    K2.VIA_DIA_MIL, K2.VIA_HOLE_MIL = 24, 12
    K2.TRACK_W = w_mil / MIL
    import pcb_astar3 as K3
    K3.KEEP = (60.0, -10.0, 70.0, -5.0)
    K3.VIA_R = 24 / 2 / K3.MIL
    K3.TRACK_W = w_mil / MIL
    # 单层模式: 裹 via_ok 恒拒 (astar2) / (astar3 靠 via 层禁: no_via 用 b1/b2 同层 push? 简化: 只用 astar2)
    if no_via:
        _orig = getattr(K2.Model, "_orig_via_ok", K2.Model.via_ok)
        K2.Model._orig_via_ok = _orig
        K2.Model.via_ok = lambda self, x, y: "no-via-mode"
    else:
        if hasattr(K2.Model, "_orig_via_ok"):
            K2.Model.via_ok = K2.Model._orig_via_ok
    return K2, K3


def astar_solve(net, sim_lines, sim_vias, width_mil, frm, to):
    """进程内调用 pcb_astar2（patch 后），失败回退 pcb_astar3。audit = sim 快照。"""
    tmp = RD / "_s4d_solver_audit.json"
    aud = []
    for L in sim_lines:
        aud.append({"k": "L", "net": L["net"], "layer": L["layer"], "sx": L["p1"][0] * MIL, "sy": (30 - L["p1"][1]) * MIL,
                    "ex": L["p2"][0] * MIL, "ey": (30 - L["p2"][1]) * MIL, "w": L["w"] * MIL})
    for V in sim_vias:
        aud.append({"k": "V", "net": V["net"], "x": V["x"] * MIL, "y": (30 - V["y"]) * MIL,
                    "hole": V.get("hole", 12), "dia": V.get("dia", V["r"] * 2 * MIL)})
    tmp.write_text(json.dumps(aud), encoding="utf-8")
    import pcb_astar2 as K2
    pads, tracks, vias = K2.load_state(str(DUMP), str(tmp))
    model = K2.Model(pads, tracks, vias, net)
    s = tuple(float(v) for v in (frm[0], frm[1]))
    g = tuple(float(v) for v in (to[0], to[1]))
    path, ok = K2.astar2(model, s, g, grid=0.05, eps=2.5)
    if ok:
        keep = K2.simplify(path)
        segs, via_pts = [], []
        cur_layer = keep[0][1]
        prev = keep[0]
        for st in keep[1:]:
            (ix, iy), layer = st
            if layer != cur_layer:
                via_pts.append((prev[0][0] * K2.GRID, prev[0][1] * K2.GRID))
                cur_layer = layer
            else:
                a = (prev[0][0] * K2.GRID, prev[0][1] * K2.GRID)
                b = (st[0][0] * K2.GRID, st[0][1] * K2.GRID)
                if segs and segs[-1]["layer"] == cur_layer and abs(segs[-1]["x2"] - a[0]) < 1e-6 and abs(segs[-1]["y2"] - a[1]) < 1e-6:
                    segs[-1]["x2"], segs[-1]["y2"] = round(b[0], 3), round(b[1], 3)
                else:
                    segs.append({"layer": cur_layer, "x1": round(a[0], 3), "y1": round(a[1], 3),
                                 "x2": round(b[0], 3), "y2": round(b[1], 3), "w": K2.TRACK_W})
            prev = st
        total = sum(math.hypot(sg["x2"] - sg["x1"], sg["y2"] - sg["y1"]) for sg in segs)
        return {"net": net, "segs": segs,
                "vias": [{"x": round(v[0], 3), "y": round(v[1], 3), "dia_mil": 24, "hole_mil": 12} for v in via_pts],
                "total_mm": round(total, 2), "blocked": False}
    # fallback astar3
    import pcb_astar3 as K3
    out = K3.solve(net, str(DUMP), str(tmp), (float(frm[0]), float(frm[1])), (float(to[0]), float(to[1])))
    if out:
        out["vias"] = [{"x": v["x"], "y": v["y"], "dia_mil": 24, "hole_mil": 12} for v in out.get("vias", [])]
    return out


def snap(solved, frm, to):
    if solved and solved.get("segs"):
        solved["segs"][0]["x1"], solved["segs"][0]["y1"] = frm[0], frm[1]
        solved["segs"][-1]["x2"], solved["segs"][-1]["y2"] = to[0], to[1]
        solved["segs"] = [s for s in solved["segs"] if abs(s["x2"] - s["x1"]) > 1e-9 or abs(s["y2"] - s["y1"]) > 1e-9]
    return solved


REF_TRACKS, REF_VIAS = None, None


def main():
    global REF_TRACKS, REF_VIAS
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    args = ap.parse_args()
    pads, ref, refvias, mem, width = load_state()
    REF_TRACKS, REF_VIAS = ref, refvias
    state = {"pads": pads, "lines": [], "vias": []}
    STAGES = {
        "st_adc":   ["ADC_IN", "DIV"],
        "st_usb":   ["USB_DP", "USB_DP_P", "USB_DM", "USB_DM_P"],
        "st_xtal":  ["XC1", "XC2", "_GND_XTAL"],
        "st_power": ["VBUS", "VBAT", "VREG_IN", "VDD"],
        "st_signal": ["DEC1_N", "DEC456_N", "DEC5b_N", "DECUSB_N", "SDA", "SCL",
                      "SWDIO", "SWCLK", "PROG", "CC1", "CC2", "P3_OUT", "DIV2",
                      "CHRG_N", "CHRGA", "LED_AA", "LED_BA", "LED_B_N"],
        "st_q14":   ["RESET_N", "BTN1_N", "BTN2_N", "DEC3_N", "BAT_ADC", "LED_A_N"],
    }
    if args.stage not in STAGES:
        print(json.dumps({"error": f"unknown stage {args.stage}", "known": list(STAGES)}))
        sys.exit(2)
    wmap_override = json.load(open(RD / "_s4d_width_override.json", encoding="utf-8")) if (RD / "_s4d_width_override.json").exists() else {}
    report = {}
    items = []
    order = STAGES[args.stage]
    manual_override = {}
    mp = RD / "_s4d_manual_override.json"
    if mp.exists():
        mo = json.load(open(mp, encoding="utf-8"))
        for k, listv in mo.items():
            manual_override[k.upper()] = listv
    for net in order:
        if net.startswith("_"):
            members = None
        else:
            members = mem[net]
        w = wmap_override.get(net, width.get(net, 0.25))
        n_report = {"net": net, "w_mm": w}
        if args.stage == "st_xtal" and net == "_GND_XTAL":
            # 晶振区 GND 包地栏 + 22 颗 GND 过孔（17 去耦 D015 修正 + 5 包地栏）
            g = manual_override["_GND_XTAL"]
            seg_items = [{"kind": "line", "net": "GND", "layer": seg.get("layer", 1),
                          "x1": round(seg["x1"] * MIL, 2), "y1": round((30 - seg["y1"]) * MIL, 2),
                          "x2": round(seg["x2"] * MIL, 2), "y2": round((30 - seg["y2"]) * MIL, 2),
                          "w": round(seg.get("w_mm", 0.3) * MIL, 2), "tag": "guard"} for seg in g["segs"]]
            via_specs = [{"for": v["for"], "x": v["x"], "y": v["y"]} for v in g["vias17_dec"]] +                          [{"for": "guard%d" % i, "x": j[0], "y": j[1]} for i, j in enumerate(g["vias5_guard"])]
            via_items = [{"kind": "via", "net": "GND", "x": round(v["x"] * MIL, 2),
                          "y": round((30 - v["y"]) * MIL, 2), "hole": g["holes"], "dia": g["dia"],
                          "tag": v["for"]} for v in via_specs]
            hard, margin = rule_precheck("GND", [{"layer": 1, "x1": s["x1"], "y1": s["y1"], "x2": s["x2"], "y2": s["y2"],
                                                  "w_mm": s.get("w_mm", 0.3)} for s in g["segs"]],
                                         [{"net": "GND", "x": v["x"], "y": v["y"], "hole": g["holes"], "dia": g["dia"]}
                                          for v in via_specs], pads, state["lines"], state["vias"])
            # C8: 每颗去耦孔距本网（GND）焊盘距离 ≤2mm
            gndpads = [p for p in pads if p["net"].upper() == "GND"]
            c8 = []
            for v in via_specs:
                dmin = min(math.hypot(v["x"] - p["x"], v["y"] - p["y"]) for p in gndpads)
                c8.append({"tag": v["for"], "nearest_gnd_pad_mm": round(dmin, 3), "ok": dmin <= 2.0})
            n_report = {"net": "_GND_XTAL", "guard_segs": len(g["segs"]), "vias": len(via_items),
                        "precheck": {"hard": len(hard), "margin": len(margin), "hard_full": hard[:40]},
                        "c8": {"bad": [c for c in c8 if not c["ok"]], "max_mm": max(c["nearest_gnd_pad_mm"] for c in c8)}}
            if not hard and not [c for c in c8 if not c["ok"]]:
                items.extend(seg_items + via_items)
                for it in seg_items:
                    state["lines"].append({"id": "sim", "net": "GND", "layer": 1, "w": it["w"] / MIL,
                                           "p1": (it["x1"] / MIL, 30 - it["y1"] / MIL),
                                           "p2": (it["x2"] / MIL, 30 - it["y2"] / MIL),
                                           "len": math.hypot(it["x2"] - it["x1"], it["y2"] - it["y1"])})
                for it in via_items:
                    state["vias"].append({"id": "sim", "net": "GND", "x": it["x"] / MIL,
                                          "y": 30 - it["y"] / MIL, "r": it["dia"] / 2 / MIL})
            report["_GND_XTAL"] = n_report
            continue
        if net.upper() in manual_override:
            # manual override: 任务系 mm 手工几何，绕过 ref/astar
            ov = []
            for seg in manual_override[net.upper()]:
                ov.append({"layer": seg.get("layer", 1), "p1": (seg["x1"], seg["y1"]), "p2": (seg["x2"], seg["y2"]),
                           "w": seg.get("w_mm", w) * MIL})
            hard, margin = rule_precheck(net, [{"layer": L["layer"], "x1": L["p1"][0], "y1": L["p1"][1],
                                                "x2": L["p2"][0], "y2": L["p2"][1], "w_mm": L["w"] / MIL}
                                               for L in ov], [], pads, state["lines"], state["vias"])
            n_report["override_precheck"] = {"hard": len(hard), "margin": len(margin), "hard_full": hard[:40]}
            if not hard:
                ov_items = [{"kind": "line", "net": net, "layer": L["layer"],
                             "x1": round(L["p1"][0] * MIL, 2), "y1": round((30 - L["p1"][1]) * MIL, 2),
                             "x2": round(L["p2"][0] * MIL, 2), "y2": round((30 - L["p2"][1]) * MIL, 2),
                             "w": round(L["w"], 2), "tag": "manual"} for L in ov]
                items.extend(ov_items)
                sim_lines_add(state, ov_items)
                n_report["override_applied"] = True
            res3 = A.analyze(pads, [L for L in state["lines"]], [V for V in state["vias"]], {net: members})[net]
            n_report["final"] = {"disc": res3["disconnected_members"], "groups": res3["n_pad_groups"],
                                 "segs": res3["seg_count"], "vias": res3["via_count"]}
            report[net] = n_report
            continue
        keep, dropped, res = plan_ref_net(state, net, members)
        n_report["ref_kept"] = len(keep)
        n_report["ref_dropped"] = len(dropped)
        n_report["coverage"] = {"disc": res["disconnected_members"], "groups": res["n_pad_groups"]}
        # 复刻段入 sim（先记 items，不直接入 sim——统一在预检后）
        stage_item = []
        keep_by_conn = keep
        # 连通性缺口: disc 或 >1 组
        disc_now = res["disconnected_members"]
        groups_now = res["pad_groups"]
        if keep:
            seg_items = [{"kind": "line", "net": net, "layer": L["layer"],
                          "x1": round(L["p1"][0] * MIL, 2), "y1": round((30 - L["p1"][1]) * MIL, 2),
                          "x2": round(L["p2"][0] * MIL, 2), "y2": round((30 - L["p2"][1]) * MIL, 2),
                          "w": round(L["w"], 2), "tag": "ref"} for L in keep]
            import json as J
            pads_for_check = pads + [dict(p, network=p.get('net')) for p in []]
            hard, margin = rule_precheck(net, [{"layer": L["layer"], "x1": L["p1"][0], "y1": L["p1"][1],
                                                "x2": L["p2"][0], "y2": L["p2"][1], "w_mm": L["w"] / MIL}
                                               for L in keep],
                                         [], pads, state["lines"], state["vias"])
            n_report["ref_precheck"] = {"hard": len(hard), "margin": len(margin),
                                        "hard_full": hard[:80], "margin_full": margin[:40]}
            if not hard:
                items.extend(seg_items)
                sim_lines_add(state, seg_items)
            else:
                # nudge 修复：对含违例段试水平/垂直平移（自由端跟随），整网复预检
                fixed, keep2, nudged_n = None, keep, 0
                attempts = []
                for step in (0.05, 0.10, 0.15, 0.20, 0.25, 0.30):
                    for axis in ("y+", "y-", "x+", "x-"):
                        trial = [dict(L) for L in keep]
                        for L in trial:
                            dx = step if axis == "x+" else (-step if axis == "x-" else 0.0)
                            dy = step if axis == "y+" else (-step if axis == "y-" else 0.0)
                            L["p1"] = (L["p1"][0] + dx, L["p1"][1] + dy)
                            L["p2"] = (L["p2"][0] + dx, L["p2"][1] + dy)
                        h2, m2 = rule_precheck(net, [{"layer": L["layer"], "x1": L["p1"][0], "y1": L["p1"][1],
                                                      "x2": L["p2"][0], "y2": L["p2"][1], "w_mm": L["w"] / MIL}
                                                     for L in trial], [], pads, state["lines"], state["vias"])
                        attempts.append({"axis": axis, "step": step, "hard": len(h2)})
                        if not h2:
                            fixed, keep2, nudged_n = True, trial, step
                            break
                    if fixed:
                        break
                if fixed:
                    hard2, margin2 = rule_precheck(net, [{"layer": L["layer"], "x1": L["p1"][0], "y1": L["p1"][1],
                                                          "x2": L["p2"][0], "y2": L["p2"][1], "w_mm": L["w"] / MIL}
                                                         for L in keep2], [], pads, state["lines"], state["vias"])
                    keep = keep2
                    n_report["nudge"] = {"applied": True, "offset": nudged_n, "residual_hard": len(hard2), "margin": len(margin2),
                                         "margin_full": margin2[:40], "attempts": attempts[:8]}
                    seg_items = [{"kind": "line", "net": net, "layer": L["layer"],
                                  "x1": round(L["p1"][0] * MIL, 2), "y1": round((30 - L["p1"][1]) * MIL, 2),
                                  "x2": round(L["p2"][0] * MIL, 2), "y2": round((30 - L["p2"][1]) * MIL, 2),
                                  "w": round(L["w"], 2), "tag": "ref+nudge"} for L in keep]
                    items.extend(seg_items)
                    sim_lines_add(state, seg_items)
                    res2 = A.analyze(pads, [{"id": f"r{i}", "net": net, "layer": L["layer"], "w": L["w"], "p1": L["p1"], "p2": L["p2"],
                                             "len": math.hypot(L["p2"][0]-L["p1"][0], L["p2"][1]-L["p1"][1])}
                                            for i, L in enumerate(keep)], OWN_VIAS, {net: members})[net]
                    disc_now, groups_now = res2["disconnected_members"], res2["pad_groups"]
                else:
                    n_report["ref_precheck_hard"] = hard[:80]
                    n_report["nudge"] = {"applied": False, "attempts": attempts[:8]}
                    # 参考几何有硬违例且无法微调修复：转 A* 整网求解（下面 main 分支）
                    disc_now = members  # 全部成员待 A*
                    groups_now = None
        if disc_now or (groups_now and len(groups_now) > 1):
            # A* 补连
            npads = [p for p in pads if p["net"].upper() == net.upper()]
            # 当前组锚点（基于 sim 状态重分析）
            res2 = A.analyze(pads, [L for L in state["lines"]], [V for V in state["vias"]], {net: members})
            r2 = res2[net]
            # MST 作业
            groups = [set(g) for g in r2["pad_groups"]]
            idx = {p["key"]: i for i, p in enumerate(npads)}
            # 已在组内的合并
            anchor_of = {}
            for gi, g in enumerate(groups):
                for key in g:
                    anchor_of[key] = gi
            padmap = {p["key"]: p for p in npads}
            # Prim 合组
            jobs = []
            merged = set(range(len(groups)))
            # groups: list of key-set
            groups_live = [list(g) for g in groups]
            merged_g = {0}
            while len(merged_g) < len(groups_live):
                best = None
                for a_key in sum([groups_live[x] for x in merged_g], []):
                    for gi, g in enumerate(groups_live):
                        if gi in merged_g:
                            continue
                        for b_key in g:
                            d = math.hypot(padmap[a_key]["x"] - padmap[b_key]["x"],
                                           padmap[a_key]["y"] - padmap[b_key]["y"])
                            if best is None or d < best[0]:
                                best = (d, a_key, b_key, gi)
                _, a_key, b_key, gi = best
                jobs.append({"from": (padmap[a_key]["x"], padmap[a_key]["y"]),
                             "from_label": a_key, "to": (padmap[b_key]["x"], padmap[b_key]["y"]),
                             "to_label": b_key, "dist": round(best[0], 2)})
                groups_live[0] = groups_live[0] + groups_live[gi] if 0 in merged_g else groups_live[0]
                groups_live[gi] = []
                merged_g.add(gi)
            # 也处理与既有段锚点合组（若 groups_now>1 但部分 connect=段端）
            w_mil = round(w * MIL, 2)
            nsolv = 0
            no_via = net.upper() in ("ADC_IN", "DIV")
            K2, K3 = patch_solvers(no_via=no_via, w_mil=w_mil)
            for job in jobs:
                solved = astar_solve(net, state["lines"], state["vias"], w_mil, job["from"], job["to"])
                solved = snap(solved, job["from"], job["to"])
                if not solved or not solved.get("segs"):
                    n_report.setdefault("astar_fail", []).append(job)
                    continue
                hard, margin = rule_precheck(net,
                                             [{"layer": s["layer"], "x1": s["x1"], "y1": s["y1"],
                                               "x2": s["x2"], "y2": s["y2"], "w_mm": s["w"]} for s in solved["segs"]],
                                             [{"net": net, "x": v["x"], "y": v["y"], "hole": v["hole_mil"],
                                               "dia": v["dia_mil"]} for v in solved.get("vias", [])],
                                             pads, state["lines"], state["vias"])
                if hard:
                    n_report.setdefault("precheck_reject", []).append({**job, "n_hard": len(hard), "first": hard[0]})
                    continue
                new_items = []
                for s in solved["segs"]:
                    new_items.append({"kind": "line", "net": net, "layer": s["layer"],
                                      "x1": round(s["x1"] * MIL, 2), "y1": round((30 - s["y1"]) * MIL, 2),
                                      "x2": round(s["x2"] * MIL, 2), "y2": round((30 - s["y2"]) * MIL, 2),
                                      "w": round(w_mil, 2), "tag": f"{job['from_label']}->{job['to_label']}"})
                for v in solved.get("vias", []):
                    new_items.append({"kind": "via", "net": net, "x": round(v["x"] * MIL, 2),
                                      "y": round((30 - v["y"]) * MIL, 2), "hole": v["hole_mil"],
                                      "dia": v["dia_mil"], "tag": f"{job['from_label']}->{job['to_label']}"})
                items.extend(new_items)
                sim_lines_add(state, new_items)
                nsolv += 1
                if margin:
                    n_report.setdefault("margin_notes", []).extend(margin[:4])
            n_report["astar_solved_jobs"] = nsolv
        # 终态分析
        res3 = A.analyze(pads, [L for L in state["lines"]], [V for V in state["vias"]], {net: members})[net]
        n_report["final"] = {"disc": res3["disconnected_members"], "groups": res3["n_pad_groups"],
                             "segs": res3["seg_count"], "vias": res3["via_count"]}
        report[net] = n_report
    (RD / f"_s4d_items_{args.stage}.json").write_text(
        json.dumps({"stage": args.stage, "items": items, "report": report}, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.stage == "st_usb":
        pairs = {}
        for pair, nets in (("USB_DP对", ("USB_DP", "USB_DP_P")), ("USB_DM对", ("USB_DM", "USB_DM_P"))):
            seg_mm = 0.0
            per = {}
            for n in nets:
                s = sum(math.hypot(i["x2"] - i["x1"], i["y2"] - i["y1"]) for i in items if i["net"] == n and i["kind"] == "line")
                per[n] = round(s, 2)
                seg_mm += s
            pairs[pair] = {"total_mm": round(seg_mm, 2), "per_net": per}
        report["_usb_pairs_mm"] = pairs
    ok = sum(1 for v in report.values() if isinstance(v, dict) and not v.get("final", {}).get("disc") and v.get("final", {}).get("groups", 9) <= 1)
    print(json.dumps({"stage": args.stage, "items": len(items),
                      "nets_final_ok": [k for k, v in report.items() if isinstance(v, dict) and not v.get("final", {}).get("disc") and v.get("final", {}).get("groups", 9) <= 1],
                      "nets_fail": [k for k, v in report.items() if isinstance(v, dict) and (v.get("final", {}).get("disc") or v.get("final", {}).get("groups", 9) > 1)]},
                     ensure_ascii=False))
    for k, v in report.items():
        if not isinstance(v, dict) or "final" not in v:
            continue
        f = v["final"]
        print(f"  {k:10s} disc={str(f['disc'])[:60]} groups={f['groups']} segs={f['segs']} vias={f['vias']}")


if __name__ == "__main__":
    main()
