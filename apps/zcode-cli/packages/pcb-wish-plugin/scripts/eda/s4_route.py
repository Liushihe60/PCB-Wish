#!/usr/bin/env python3
"""s4_route.py — S4 组间补连作业生成器 + 求解执行器。

原理:
  - 从当前画布 audit + 焊盘 dump 计算每网焊盘组（s4_analyze）；组数>1 的非 GND 网 → 补连作业。
  - 作业=MST 锚点对：锚点=组内焊盘中心/自有过孔心/L1 裸段端（L2 裸端不可直达目标层，排除）。
  - 求解: pcb_astar2（patch: KEEPOUT 失效——C6 已随 D021 删除; VIA 24/12mil——pitfalls #23
    hole≥12; VIA_R 同步 0.305）。失败回退 pcb_astar3（同 patch）。
  - 预检: pcb_route_lib 规则净距（Track-Track 0.102/Track-Pad 0.152/Track-Via 0.152，采样容差
    0.005）——硬违例拒解换锚重试；margin 区记录放行（D017⑤ 实测口径）。
  - 执行: 单 invoke 批量 create（line/via，canvas mil 浮点）→ 等 6s → 重枚举 audit →
    重分析该网（独立回读，组数=1 为过）。
用法:
  python s4_route.py --phase plan --audit reports/_s4_tracks_vias_in.json
  python s4_route.py --phase run --net VBUS [--audit ...] [--dry]
"""
import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

HERE = Path(__file__).parent
PROJ = Path("E:/agent project/projects/flexiheal-v1")
RD = PROJ / "reports"
MIL = 39.37

RULE_TRACK_TRACK = 0.102
RULE_TRACK_PAD = 0.152
RULE_TRACK_VIA = 0.152
TOL = 0.005

# 作业网顺序（第二次派发 brief T3：R8 回位优先→六网→S3D 增量→Q14→查漏）
NET_ORDER = ["VBUS", "VREG_IN", "VDD", "VBAT", "PROG", "DIV", "DIV2", "ADC_IN",
             "CHRG_N", "CHRGA", "LED_AA",
             "RESET_N", "BTN1_N", "BTN2_N", "DEC3_N", "BAT_ADC", "LED_A_N",
             "XC1", "XC2", "DEC456_N"]
R8_PADS = {"R8.1": "VBUS", "R8.2": "VREG_IN"}
# D028 冻结布线基准（委托方手拖定版）
DUMP_DEFAULT = "pcb_pads_dump_manual.json"


def to_audit_list(audit_json):
    """_s4_tracks_vias 格式 → 求解器 audit 列表格式（canvas mil）。"""
    out = []
    for L in audit_json["lines"]:
        out.append({"k": "L", "net": L["net"], "layer": L["layer"], "sx": L["sx"], "sy": L["sy"],
                    "ex": L["ex"], "ey": L["ey"], "w": L["w"]})
    for V in audit_json["vias"]:
        out.append({"k": "V", "net": V["net"], "x": V["x"], "y": V["y"],
                    "hole": V.get("hole", 12), "dia": V.get("dia", 24)})
    return out


def load_state(audit_path, dump_name=None):
    sys.path.insert(0, str(HERE))
    import s4_analyze as A
    pads = A.load_pads(str(RD / (dump_name or DUMP_DEFAULT)))
    aud = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    lines, vias = A.load_wires(str(audit_path))
    return pads, lines, vias, aud


def net_width_mil(aud, net):
    ws = [L["w"] for L in aud["lines"] if L["net"].upper() == net.upper()]
    if not ws:
        return 9
    return max(ws)


def build_jobs(pads, lines, vias, members_by_net, audit_path):
    import s4_analyze as A
    res = A.analyze(pads, lines, vias, members_by_net)
    jobs = []
    for net in NET_ORDER:
        r = res[net]
        if net == "GND" or r["n_pad_groups"] <= 1:
            continue
        npads = [p for p in pads if p["net"].upper() == net.upper()]
        nlines = [i for i, L in enumerate(lines) if L["net"].upper() == net.upper()]
        nvias = [i for i, V in enumerate(vias) if V["net"].upper() == net.upper()]
        # 组锚点：焊盘心 + 过孔心 + L1 裸段端（该组）
        # 重新用并查集分组（与 analyze 相同逻辑的轻量版——直接用 pad_groups + 段归属）
        groups = [set(g) for g in r["pad_groups"]]
        seg_group = {}
        for i in nlines:
            L = lines[i]
            # 段属于哪组：端点落组内焊盘矩形 → 该组
            for gi, g in enumerate(groups):
                for p in npads:
                    if p["key"] not in g:
                        continue
                    for (px, py) in (L["p1"], L["p2"]):
                        ddx = max(p["rect"][0] - px, 0.0, px - p["rect"][2])
                        ddy = max(p["rect"][1] - py, 0.0, py - p["rect"][3])
                        if math.hypot(ddx, ddy) <= 0.02:
                            seg_group.setdefault(gi, set()).add(i)
        # 链式闭包：与组内既有段端点重合/T 搭（同层）的段也归入该组（悬空链中段锚点可达）
        changed = True
        while changed:
            changed = False
            for i in nlines:
                if any(i in s for s in seg_group.values()):
                    continue
                L = lines[i]
                for gi, segs in list(seg_group.items()):
                    gsegs = [lines[j] for j in segs]
                    hit = False
                    for (px, py) in (L["p1"], L["p2"]):
                        for Lj in gsegs:
                            if Lj["layer"] != L["layer"]:
                                continue
                            if math.hypot(px - Lj["p1"][0], py - Lj["p1"][1]) <= 0.02 or \
                               math.hypot(px - Lj["p2"][0], py - Lj["p2"][1]) <= 0.02 or \
                               A.pt_seg_dist(px, py, Lj["p1"], Lj["p2"]) <= (L["w"] + Lj["w"]) / 2 + 0.02:
                                hit = True
                                break
                        if hit:
                            break
                    if hit:
                        seg_group.setdefault(gi, set()).add(i)
                        changed = True
        via_group = {}
        for i in nvias:
            V = vias[i]
            for gi, g in enumerate(groups):
                for p in npads:
                    if p["key"] not in g:
                        continue
                    ddx = max(p["rect"][0] - V["x"], 0.0, V["x"] - p["rect"][2])
                    ddy = max(p["rect"][1] - V["y"], 0.0, V["y"] - p["rect"][3])
                    if math.hypot(ddx, ddy) <= 0.05:
                        via_group.setdefault(gi, set()).add(i)
        # 段↔孔联合闭包：中心落在组内段走廊（V.r+w/2）的过孔、触及该组段的段，反复并入
        changed = True
        while changed:
            changed = False
            for i in nvias:
                if any(i in s for s in via_group.values()):
                    continue
                V = vias[i]
                for gi in set(list(seg_group.keys()) + list(via_group.keys())):
                    hit = False
                    for j in seg_group.get(gi, ()):
                        L = lines[j]
                        if A.pt_seg_dist(V["x"], V["y"], L["p1"], L["p2"]) <= V["r"] + L["w"] / 2:
                            hit = True
                            break
                    if hit:
                        via_group.setdefault(gi, set()).add(i)
                        changed = True
            for i in nlines:
                if any(i in s for s in seg_group.values()):
                    continue
                L = lines[i]
                for gi in set(list(seg_group.keys()) + list(via_group.keys())):
                    hit = False
                    for (px, py) in (L["p1"], L["p2"]):
                        for Lj in (lines[j] for j in seg_group.get(gi, ())):
                            if Lj["layer"] == L["layer"] and (
                               math.hypot(px - Lj["p1"][0], py - Lj["p1"][1]) <= 0.02 or
                               math.hypot(px - Lj["p2"][0], py - Lj["p2"][1]) <= 0.02 or
                               A.pt_seg_dist(px, py, Lj["p1"], Lj["p2"]) <= (L["w"] + Lj["w"]) / 2 + 0.02):
                                hit = True
                                break
                        if not hit:
                            for V in (vias[j] for j in via_group.get(gi, ())):
                                if math.hypot(px - V["x"], py - V["y"]) <= 0.02:
                                    hit = True
                                    break
                        if hit:
                            break
                    if hit:
                        seg_group.setdefault(gi, set()).add(i)
                        changed = True
        anchors = []  # (group_idx, (x,y), kind, label)
        for gi, g in enumerate(groups):
            for key in g:
                p = next(pp for pp in npads if pp["key"] == key)
                anchors.append((gi, (p["x"], p["y"]), "pad", key))
            for i in via_group.get(gi, ()):  # own vias of this group
                V = vias[i]
                anchors.append((gi, (V["x"], V["y"]), "via", f"via@({V['x']:.2f},{V['y']:.2f})"))
            for i in seg_group.get(gi, ()):  # L1 裸段端
                L = lines[i]
                if L["layer"] == 1:
                    anchors.append((gi, L["p1"], "L1end", f"end({L['p1'][0]:.2f},{L['p1'][1]:.2f})"))
                    anchors.append((gi, L["p2"], "L1end", f"end({L['p2'][0]:.2f},{L['p2'][1]:.2f})"))
        # MST（ Prim，最近锚点对）
        n_g = len(groups)
        merged = {0}
        net_jobs = []
        while len(merged) < n_g:
            best = None
            for gi in range(n_g):
                if gi in merged:
                    continue
                for a in anchors:
                    if a[0] in merged:
                        for b in anchors:
                            if b[0] == gi:
                                d = math.hypot(a[1][0] - b[1][0], a[1][1] - b[1][1])
                                if best is None or d < best[0]:
                                    best = (d, a, b)
            d, a, b = best
            net_jobs.append({"net": net, "from": a[1], "from_label": f"{a[3]}@{a[0]}",
                             "to": b[1], "to_label": f"{b[3]}@{b[0]}", "dist": round(d, 2)})
            merged.add(b[0])
        # R8 焊盘作业置顶
        net_jobs.sort(key=lambda j: 0 if ("R8." in j["from_label"] or "R8." in j["to_label"]) else 1)
        jobs.extend(net_jobs)
    return jobs


def patch_solvers():
    import pcb_astar2 as K2
    K2.KEEPOUT = (1e9, 1e9, 1e9, 1e9)  # C6 keepout 已随 D021 删除
    K2.VIA_R = 24 / 2 / K2.MIL          # pitfalls #23: hole≥12mil → dia 24
    K2.VIA_DIA_MIL, K2.VIA_HOLE_MIL = 24, 12
    import pcb_astar3 as K3
    K3.KEEP = (1e9, 1e9, 1e9, 1e9)
    K3.VIA_R = 24 / 2 / K3.MIL
    return K2, K3


def rule_precheck(net, segs, vias_new, pads, exist_lines, exist_vias, planned, dump_name=None):
    """规则净距预检（异网才查；同网任意层跳过——EDA 无同网跨层间距概念）。
    规则: Track-Track 0.102 / Track-Pad 0.152 / Track-Via 0.152 / 板边 0.30；采样容差 0.005。
    返回 (hard[], margin[])。"""
    import pcb_route_lib as RL
    pad_rects = RL.load_pad_rects([str(RD / (dump_name or DUMP_DEFAULT))])
    w = 0.224
    hard, margin = [], []
    for s in segs:
        w = s.get("w", 0.224)
        seg = ((s["x1"], s["y1"]), (s["x2"], s["y2"]), w)
        # 板边
        for (px, py) in ((s["x1"], s["y1"]), (s["x2"], s["y2"])):
            if px < 0.30 or py < 0.30 or px > 45 - 0.30 or py > 30 - 0.30:
                hard.append({"type": "board-edge", "seg": [s["x1"], s["y1"], s["x2"], s["y2"]], "at": [px, py]})
        # 异网焊盘
        for p in pad_rects:
            if (p["net"] or "").upper() == net.upper():
                continue
            d = RL.seg_rect_dist(seg, p["rect"])
            rec = {"type": "pad", "dz": p["dz"], "padn": p["pad"], "net": p["net"],
                   "dist": round(d, 3), "seg": [s["x1"], s["y1"], s["x2"], s["y2"]]}
            if d < RULE_TRACK_PAD - TOL:
                hard.append(rec)
            elif d < RULE_TRACK_PAD + 0.01:
                margin.append(rec)
        # 异网既有段（同层）
        for t in exist_lines:
            if t["net"].upper() == net.upper() or t["layer"] != s["layer"]:
                continue
            d = RL.seg_seg_dist(seg, (t["p1"], t["p2"], t["w"]))
            rec = {"type": "track", "net": t["net"], "dist": round(d, 3),
                   "seg": [s["x1"], s["y1"], s["x2"], s["y2"]]}
            if d < RULE_TRACK_TRACK - TOL:
                hard.append(rec)
            elif d < RULE_TRACK_TRACK + 0.01:
                margin.append(rec)
        # 异网过孔
        for v in exist_vias:
            if v["net"].upper() == net.upper():
                continue
            d = RL.seg_rect_dist(seg, [v["x"] - v["r"], v["y"] - v["r"], v["x"] + v["r"], v["y"] + v["r"]])
            rec = {"type": "via", "net": v["net"], "dist": round(d, 3),
                   "seg": [s["x1"], s["y1"], s["x2"], s["y2"]]}
            if d < RULE_TRACK_VIA - TOL:
                hard.append(rec)
            elif d < RULE_TRACK_VIA + 0.01:
                margin.append(rec)
    # 新过孔对异网焊盘/段/孔
    for v in vias_new:
        vr = 24 / 2 / MIL
        for p in pad_rects:
            if (p["net"] or "").upper() == net.upper():
                continue
            d = RL.seg_rect_dist(((v["x"], v["y"]), (v["x"], v["y"]), 2 * vr), p["rect"])
            rec = {"type": "via-pad", "dz": p["dz"], "padn": p["pad"], "net": p["net"],
                   "dist": round(d, 3), "at": [v["x"], v["y"]]}
            if d < RULE_TRACK_VIA - TOL:
                hard.append(rec)
            elif d < RULE_TRACK_VIA + 0.01:
                margin.append(rec)
        for t in exist_lines:
            if t["net"].upper() == net.upper():
                continue
            d = RL.seg_seg_dist(((v["x"], v["y"]), (v["x"], v["y"]), 2 * vr), (t["p1"], t["p2"], t["w"]))
            rec = {"type": "via-track", "net": t["net"], "dist": round(d, 3), "at": [v["x"], v["y"]]}
            if d < RULE_TRACK_VIA - TOL:
                hard.append(rec)
            elif d < RULE_TRACK_VIA + 0.01:
                margin.append(rec)
        for v2 in exist_vias:
            if v2["net"].upper() == net.upper():
                continue
            d = math.hypot(v["x"] - v2["x"], v["y"] - v2["y"]) - vr - v2["r"]
            rec = {"type": "via-via", "net": v2["net"], "dist": round(d, 3), "at": [v["x"], v["y"]]}
            if d < RULE_TRACK_VIA - TOL:
                hard.append(rec)
            elif d < RULE_TRACK_VIA + 0.01:
                margin.append(rec)
    return hard, margin


def solve_net(net, jobs_for_net, pads, lines, vias, aud, w_mil, dump_name=None):
    """逐作业求解。返回 (ok_items[], fail_jobs[], margin_notes[])。
    本网内已规划的段/孔即时入障碍与锚点（后续作业可见）。"""
    K2, K3 = patch_solvers()
    audit_list = to_audit_list(aud)
    apath = RD / "_s4_solver_audit_tmp.json"
    apath.write_text(json.dumps(audit_list), encoding="utf-8")
    dump_path = str(RD / (dump_name or DUMP_DEFAULT))
    # 局部增量模型
    local_lines = list(lines)
    local_vias = list(vias)
    planned = []   # 本网已规划段（task mm）
    planned_vias = []
    ok_items, fail_jobs, margin_notes = [], [], []
    for ji, job in enumerate(jobs_for_net):
        solved = None
        tried = [(job["from"], job["to"]), (job["to"], job["from"])]
        for (frm, to) in tried:
            # 用局部模型重建 audit（含 planned）
            tmp = list(audit_list)
            for s in planned:
                tmp.append({"k": "L", "net": net, "layer": s["layer"],
                            "sx": s["x1"] * MIL, "sy": (30 - s["y1"]) * MIL,
                            "ex": s["x2"] * MIL, "ey": (30 - s["y2"]) * MIL, "w": w_mil})
            for v in planned_vias:
                tmp.append({"k": "V", "net": net, "x": v["x"] * MIL, "y": (30 - v["y"]) * MIL,
                            "hole": 12, "dia": 24})
            apath.write_text(json.dumps(tmp), encoding="utf-8")
            r = subprocess.run([sys.executable, str(HERE / "pcb_astar2.py"), "--net", net,
                                "--dump", dump_path,
                                "--audit", str(apath),
                                "--from", f"{frm[0]},{frm[1]}", "--to", f"{to[0]},{to[1]}",
                                "--out", str(RD / "_s4_solver_out_tmp.json")],
                               capture_output=True, text=True, timeout=600, cwd=str(PROJ))
            if r.returncode == 0:
                solved = json.loads((RD / "_s4_solver_out_tmp.json").read_text(encoding="utf-8"))
                break
            # 回退 astar3
            r3 = subprocess.run([sys.executable, str(HERE / "pcb_astar3.py"), "--net", net,
                                 "--dump", dump_path,
                                 "--audit", str(apath),
                                 "--from", f"{frm[0]},{frm[1]}", "--to", f"{to[0]},{to[1]}",
                                 "--out", str(RD / "_s4_solver_out_tmp.json")],
                                capture_output=True, text=True, timeout=600, cwd=str(PROJ))
            if r3.returncode == 0:
                solved = json.loads((RD / "_s4_solver_out_tmp.json").read_text(encoding="utf-8"))
                solved["vias"] = [{"x": v["x"], "y": v["y"], "dia_mil": 24, "hole_mil": 12}
                                  for v in solved.get("vias", [])]
                break
        if solved is None:
            fail_jobs.append({**job, "why": "blocked"})
            continue
        # 锚点精确吸附：段端=作业端点精确坐标（0.05 栅格量化会造成 ~0.03mm 假搭接）
        if solved["segs"]:
            solved["segs"][0]["x1"], solved["segs"][0]["y1"] = frm
            solved["segs"][-1]["x2"], solved["segs"][-1]["y2"] = to
            solved["segs"] = [s for s in solved["segs"]
                              if abs(s["x2"] - s["x1"]) > 1e-6 or abs(s["y2"] - s["y1"]) > 1e-6]
            if not solved["segs"]:
                fail_jobs.append({**job, "why": "degenerate"})
                continue
        # 预检
        hard, margin = rule_precheck(net, solved["segs"], solved.get("vias", []),
                                     pads, lines, vias, planned, dump_name)
        if hard:
            fail_jobs.append({**job, "why": f"precheck-hard x{len(hard)}",
                              "first": hard[0]})
            continue
        for m in margin[:5]:
            margin_notes.append({**m, "job": f"{job['from_label']}->{job['to_label']}"})
        for s in solved["segs"]:
            item = {"kind": "line", "net": net, "layer": s["layer"],
                    "x1": round(s["x1"] * MIL, 3), "y1": round((30 - s["y1"]) * MIL, 3),
                    "x2": round(s["x2"] * MIL, 3), "y2": round((30 - s["y2"]) * MIL, 3),
                    "w": w_mil, "tag": f"{job['from_label']}->{job['to_label']}"}
            ok_items.append(item)
            planned.append({"layer": s["layer"], "x1": s["x1"], "y1": s["y1"],
                            "x2": s["x2"], "y2": s["y2"], "w": s.get("w", 0.224)})
            local_lines.append({"id": "planned", "net": net, "layer": s["layer"],
                                "p1": (s["x1"], s["y1"]), "p2": (s["x2"], s["y2"]),
                                "w": s.get("w", 0.224), "len": math.hypot(s["x2"] - s["x1"], s["y2"] - s["y1"])})
        for v in solved.get("vias", []):
            item = {"kind": "via", "net": net, "x": round(v["x"] * MIL, 3),
                    "y": round((30 - v["y"]) * MIL, 3), "hole": 12, "dia": 24,
                    "tag": f"{job['from_label']}->{job['to_label']}"}
            ok_items.append(item)
            planned_vias.append({"x": v["x"], "y": v["y"]})
            local_vias.append({"id": "planned", "net": net, "x": v["x"], "y": v["y"], "r": 24 / 2 / MIL})
    return ok_items, fail_jobs, margin_notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True, choices=["plan", "run"])
    ap.add_argument("--net", default=None)
    ap.add_argument("--audit", default="reports/_s4_tracks_vias_in2.json")
    ap.add_argument("--dump", default=DUMP_DEFAULT)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa
        pass
    pads, lines, vias, aud = load_state(RD / args.audit if not str(args.audit).startswith("reports/") else PROJ / args.audit, args.dump)
    a = json.loads((PROJ / "spec/assertions.json").read_text(encoding="utf-8"))
    members_by_net = {x["net"]: x["members"] for x in a["net_members"]}

    if args.phase == "plan":
        jobs = build_jobs(pads, lines, vias, members_by_net, RD / args.audit)
        (RD / "_s4_jobs2.json").write_text(json.dumps(jobs, ensure_ascii=False, indent=1), encoding="utf-8")
        for j in jobs:
            print(f"{j['net']:10s} {j['from_label']:22s} -> {j['to_label']:22s} d={j['dist']}")
        print(f"total jobs: {len(jobs)}")
        return

    if args.phase == "run":
        assert args.net, "--net required"
        jobs = json.loads((RD / "_s4_jobs2.json").read_text(encoding="utf-8"))
        jobs_for_net = [j for j in jobs if j["net"].upper() == args.net.upper()]
        if not jobs_for_net:
            print(f"# {args.net}: 无作业（可能已连通）")
            return
        w_mil = net_width_mil(aud, args.net)
        print(f"# {args.net}: {len(jobs_for_net)} 作业, 线宽 {w_mil}mil ({w_mil/MIL:.4f}mm)")
        ok_items, fail_jobs, margin_notes = solve_net(args.net, jobs_for_net, pads, lines, vias, aud, w_mil)
        out = {"net": args.net, "w_mil": w_mil, "items": ok_items, "fail_jobs": fail_jobs,
               "margin_notes": margin_notes}
        tag = args.net.upper()
        (RD / f"_s4_plan_{tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        n_seg = sum(1 for i in ok_items if i["kind"] == "line")
        n_via = sum(1 for i in ok_items if i["kind"] == "via")
        print(f"# 求解: {n_seg} 段 / {n_via} 孔; 失败作业 {len(fail_jobs)}; margin 记录 {len(margin_notes)}")
        for f in fail_jobs:
            print(f"  FAIL {f['from_label']} -> {f['to_label']}: {f['why']} {f.get('first','')}")
        if args.dry:
            return
        if ok_items:
            from lceda_invoke import ensure_session, run_code
            sid = ensure_session(None)
            items_path = RD / f"_s4_create_args_{tag}.json"
            items_path.write_text(json.dumps(ok_items, ensure_ascii=False), encoding="utf-8")
            r = subprocess.run([sys.executable, str(HERE / "s4_exec.py"), "--phase", "create",
                                "--tag", tag, "--items", str(items_path)],
                               capture_output=True, text=True, timeout=700)
            print(r.stdout[-2000:])
            if r.returncode != 0:
                print(f"# create 失败 exit={r.returncode} {r.stderr[-500:]}")
                sys.exit(1)
        sys.exit(0 if not fail_jobs else 2)


if __name__ == "__main__":
    main()
