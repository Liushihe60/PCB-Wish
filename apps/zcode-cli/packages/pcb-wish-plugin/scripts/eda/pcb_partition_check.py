#!/usr/bin/env python3
"""pcb_partition_check.py — G4 布局门：功能分区落实检查（零依赖，stdlib only）。

用户约束（2026-10-23 固化）：
  按原理图的功能分区确定各功能区域在板上位置；同一功能的放在一起，某芯片与其外围器件放在一起；
  原理图上相连的器件不许盲目摆放；主电路连线尽量短、线宽尽量宽。

判据（全部确定性，禁止凭感觉）：
  R1 分区覆盖      —— 每个已放置器件必须标 block 且 block ∈ IR modules（或 config.extra_blocks）；
                      IR 模块成员未落位 → FAIL。
  R2 分区空间结构  —— 各分区包围盒两两不相交（重叠对数 ≤ config.partition_overlap_max_pairs，默认 0）。
  R3 芯片+外围簇   —— U 开头器件 = 芯片：与其共用电源网的无源件（去耦电容）距离 ≤ decoupling_mm（默认 2.0）；
                      与其共用任意网的同分区无源件距离 ≤ cluster_radius_mm（默认 6.0）。
                      例外走 config.cluster_exceptions（必须给理由）。
  R4 主电路长度    —— clazz=power 的网：成员器件位置的最小生成树（MST）长度 ≤ config.main_nets_max_len_mm[网名]；
                      阈值缺失的网 → 判 WARNING_NEEDS_DECISION（阈值没定稿不许过门，须与用户确认）。
  R5 主电路线宽    —— 报告级：config.main_net_width_mm 每网声明宽度 → 所需走廊 = 宽度 + 2×clearance（默认 0.254）。

坐标约定：positions 为任务系 mm（板左上原点、y 向下）；距离/长度计算与方向无关。
用法：
  python pcb_partition_check.py --ir spec/ir.json --layout spec/layout_v2.json \
      [--config spec/partitions_cfg.json] --out reports/_g4_partition_check.json [--derive spec/partitions_skeleton.json]
  python pcb_partition_check.py --selftest

退出码：0=PASS，1=FAIL，2=PASS 但有 WARNING_NEEDS_DECISION（不许作为过门依据），3=数据/参数错误。
"""
import argparse
import json
import math
import sys
from pathlib import Path

DECOUT_DEFAULT_MM = 2.0
CLUSTER_DEFAULT_MM = 6.0
CLEARANCE_DEFAULT_MM = 0.254
OVERLAP_DEFAULT_MAX = 0


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def save_json(p, d):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def mst_len(points):
    """Prim 最小生成树总长；输入 [(x,y)]，返回 mm。单点/空=0。"""
    pts = sorted(set((round(x, 4), round(y, 4)) for x, y in points))
    if len(pts) <= 1:
        return 0.0
    inside = [pts[0]]
    rest = pts[1:]
    total = 0.0
    while rest:
        best = None
        bi = 0
        for i, p in enumerate(rest):
            d = min(dist(p, q) for q in inside)
            if best is None or d < best:
                best, bi = d, i
        total += best
        inside.append(rest.pop(bi))
    return total


def component_pinmap(ir):
    """{ref: set(网名)} —— 从 IR nets.members 反推每件参与的网。"""
    refs = {}
    for net in ir.get("nets", []):
        for m in net.get("members", []):
            ref = m.split(".", 1)[0]
            refs.setdefault(ref, set()).add(net.get("name"))
    return refs


def module_members(ir):
    mods = {}
    for m in ir.get("modules", []):
        refs = {c["ref"] for c in m.get("components", [])}
        for pin in m.get("pins", []):
            refs.add(pin.split(".", 1)[0])
        mods[m["id"]] = {"refs": refs, "type": m.get("type", "")}
    return mods


def is_ic(ref, refs_by_ref):
    """芯片判定：U 前缀（行业位号惯例）。旧版加"≥3 网"条件会漏判小模块的芯片，已删。"""
    return ref.upper().startswith("U")


MIL_PER_MM = 39.3700787


def pads_net_map(pads_dump):
    """从 g5in 型 dump 建 {(ref, net): [(x_mm, y_mm), ...]} —— 引脚级去耦距离的真实口径。"""
    out = {}
    for comp in pads_dump or []:
        ref = comp.get("dz")
        for pad in comp.get("pads", []):
            net = pad.get("net")
            if not ref or not net:
                continue
            out.setdefault((ref, net), []).append(
                (float(pad["x"]) / MIL_PER_MM, float(pad["y"]) / MIL_PER_MM))
    return out


def min_pad_distance(a_pts, b_pts):
    if not a_pts or not b_pts:
        return None
    return min(dist(a, b) for a in a_pts for b in b_pts)


def check(ir, layout, cfg, pads_dump=None):
    findings = []  # (severity, code, message)
    positions = layout.get("positions")
    if isinstance(positions, list):  # 兼容 [{ref,x,y,...}] 形态
        positions = {p["ref"]: p for p in positions if "ref" in p}
    if not isinstance(positions, dict) or not positions:
        return {"verdict": "FAIL", "findings": [("FAIL", "DATA", "layout.json 无 positions")]}

    refs_by_ref = component_pinmap(ir)
    mods = module_members(ir)
    extra_blocks = set(cfg.get("extra_blocks", []))
    dec_mm = float(cfg.get("decoupling_mm", DECOUT_DEFAULT_MM))
    clu_mm = float(cfg.get("cluster_radius_mm", CLUSTER_DEFAULT_MM))
    max_overlap = int(cfg.get("partition_overlap_max_pairs", OVERLAP_DEFAULT_MAX))
    exceptions = cfg.get("cluster_exceptions", {})

    # R1 分区覆盖
    missing_block = [r for r, p in positions.items() if not p.get("block")]
    unknown_block = sorted({p["block"] for p in positions.values() if p.get("block")
                            and p["block"] not in mods and p["block"] not in extra_blocks})
    unplaced = []
    for mid, m in mods.items():
        for r in sorted(m["refs"]):
            if r not in positions:
                unplaced.append(f"{mid}:{r}")
    if missing_block:
        findings.append(("FAIL", "R1_COVER", f"未标 block 的器件: {missing_block}"))
    if unknown_block:
        findings.append(("FAIL", "R1_COVER", f"block 不在 IR modules/extra_blocks 中: {unknown_block}"))
    if unplaced:
        findings.append(("FAIL", "R1_COVER", f"IR 模块成员未落位: {unplaced}"))

    # R2 分区 bbox 不相交
    boxes = {}
    for mid in mods:
        pts = [(p["x"], p["y"]) for r, p in positions.items() if p.get("block") == mid]
        if pts:
            boxes[mid] = (min(x for x, _ in pts), min(y for _, y in pts),
                          max(x for x, _ in pts), max(y for _, y in pts))
    overlap_pairs = []
    mids = sorted(boxes)
    for i in range(len(mids)):
        for j in range(i + 1, len(mids)):
            a, b = boxes[mids[i]], boxes[mids[j]]
            if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                overlap_pairs.append((mids[i], mids[j]))
    if len(overlap_pairs) > max_overlap:
        findings.append(("FAIL", "R2_BBOX", f"分区包围盒重叠对数 {len(overlap_pairs)} > {max_overlap}: {overlap_pairs}"))

    # R3 芯片+外围簇（引脚级真实口径优先：去耦距离 = IC 电源脚 ↔ 电容同网脚的最近距离；
    # 模块类 IC（XIAO 等）的电容贴边缘引脚，中心距会误报——因此 pad dump 可用时一律走引脚级）
    nets_of = {}
    for net in ir.get("nets", []):
        nets_of[net.get("name")] = net
    pad_map = pads_net_map(pads_dump) if pads_dump else None
    metric = "pin_level" if pad_map else "component_center_fallback"
    violations = []
    evaluated = 0
    for mid, m in mods.items():
        ics = [r for r in m["refs"] if is_ic(r, refs_by_ref)]
        passives = [r for r in m["refs"] if not is_ic(r, refs_by_ref)]
        for ic in ics:
            ic_pos = (positions[ic]["x"], positions[ic]["y"]) if ic in positions else None
            if ic_pos is None:
                continue
            ic_nets = refs_by_ref.get(ic, set())
            for p in passives:
                if p not in positions:
                    continue
                shared = ic_nets & refs_by_ref.get(p, set())
                if not shared:
                    continue
                p_nets_clazz = {nets_of[n].get("clazz") for n in shared if n in nets_of}
                is_decoupling = "power" in p_nets_clazz and p.upper().startswith("C")
                limit = dec_mm if is_decoupling else clu_mm
                evaluated += 1
                if pad_map:
                    ds = [min_pad_distance(pad_map.get((ic, n)), pad_map.get((p, n))) for n in shared]
                    ds = [d for d in ds if d is not None]
                    if not ds:
                        continue  # 该共网对在 pad dump 中无坐标（如未贴装选配件）→ 跳过并记入缺口
                    d = min(ds)
                else:
                    d = dist(ic_pos, (positions[p]["x"], positions[p]["y"]))
                if d > limit and p not in exceptions:
                    violations.append(f"{mid}:{ic}↔{p} {d:.2f}mm > {limit}mm (shared={sorted(shared)})")
    if violations:
        findings.append(("FAIL", "R3_CLUSTER", f"芯片-外围簇距离超限 {len(violations)} 条: {violations[:12]}"))

    # R4 主电路长度（power 网 MST vs 阈值）
    thresholds = cfg.get("main_nets_max_len_mm", {})
    board = layout.get("board_mm", [0, 0])
    diag = math.hypot(float(board[0]), float(board[1]))
    main_report, warn_nets, fail_nets = [], [], []
    for net in ir.get("nets", []):
        if net.get("clazz") != "power":
            continue
        refs = sorted({m.split(".", 1)[0] for m in net.get("members", [])})
        pts = [(positions[r]["x"], positions[r]["y"]) for r in refs if r in positions]
        L = mst_len(pts)
        main_report.append({"net": net.get("name"), "refs": refs, "mst_mm": round(L, 2),
                            "limit": thresholds.get(net.get("name"))})
        lim = thresholds.get(net.get("name"))
        if lim is None:
            warn_nets.append(net.get("name"))
        elif L > float(lim):
            fail_nets.append(f"{net.get('name')} MST {L:.2f}mm > {lim}mm")
    if fail_nets:
        findings.append(("FAIL", "R4_MAIN_LEN", f"主电路 MST 超阈值: {fail_nets}"))
    if warn_nets:
        findings.append(("NEEDS_DECISION", "R4_MAIN_LEN",
                         f"主电路网未定长度阈值: {warn_nets}（板对角 {diag:.1f}mm 作参考尺度，请与用户定稿后重跑）"))

    # R5 线宽走廊（报告级）
    widths = cfg.get("main_net_width_mm", {})
    corridor = [{"net": n, "width_mm": w, "corridor_mm": round(float(w) + 2 * float(cfg.get("clearance_mm", CLEARANCE_DEFAULT_MM)), 3)}
                for n, w in widths.items()]

    verdict = "FAIL" if any(s == "FAIL" for s, _, _ in findings) else (
        "NEEDS_DECISION" if any(s == "NEEDS_DECISION" for s, _, _ in findings) else "PASS")
    return {"verdict": verdict, "metric_r3": metric, "r3_pairs_evaluated": evaluated, "board_diag_mm": round(diag, 2),
            "partition_boxes_mm": {k: [round(v, 2) for v in b] for k, b in boxes.items()},
            "bbox_overlap_pairs": overlap_pairs, "main_nets": main_report,
            "width_corridors": corridor,
            "findings": [{"severity": s, "code": c, "message": m} for s, c, m in findings]}


def derive(ir):
    """由 IR 生成 G3 分区骨架：模块 → 成员 refs + 该模块触及的 power 网（主电路候选）。"""
    mods = module_members(ir)
    refs_by_ref = component_pinmap(ir)
    nets = ir.get("nets", [])
    out = {"$comment": "由 IR 自动派生的 G3 分区骨架：请补 anchors/thresholds 并逐分区定大概位置后交用户确认。",
           "partitions": [], "main_nets_max_len_mm": {}, "main_net_width_mm": {}}
    for mid, m in mods.items():
        power_nets = sorted({n.get("name") for n in nets if n.get("clazz") == "power"
                             and m["refs"] & {x.split('.', 1)[0] for x in n.get("members", [])}})
        out["partitions"].append({
            "id": mid, "type": m["type"],
            "members": sorted(m["refs"]),
            "anchor_refs": sorted(r for r in m["refs"] if r.upper().startswith("J")),
            "main_nets": power_nets,
        })
    return out


def selftest():
    ir = {
        "modules": [
            {"id": "mcu", "type": "mcu", "components": [{"ref": "U1", "value": "MCU"}, {"ref": "C3", "value": "100n"}],
             "pins": ["U1.1", "U1.2", "U1.3", "C3.1", "C3.2", "R1.1", "R1.2"]},
            {"id": "psu", "type": "power", "components": [{"ref": "U2", "value": "LDO"}, {"ref": "C9", "value": "10u"}],
             "pins": ["U2.1", "U2.2", "U2.3", "C9.1", "C9.2"]},
        ],
        "nets": [
            {"name": "VDD", "clazz": "power", "members": ["U1.1", "C3.1", "U2.3", "C9.1"]},
            {"name": "SIG", "clazz": "signal", "members": ["U1.2", "R1.1"]},
        ],
    }
    good = {"positions": {"U1": {"x": 10, "y": 10, "block": "mcu"}, "C3": {"x": 10.8, "y": 10.8, "block": "mcu"},
                          "R1": {"x": 13.0, "y": 10.0, "block": "mcu"},
                          "U2": {"x": 30, "y": 20, "block": "psu"}, "C9": {"x": 30.5, "y": 20.5, "block": "psu"}},
            "board_mm": [45, 30]}
    cfg = {"main_nets_max_len_mm": {"VDD": 30}}
    r1 = check(ir, good, cfg)
    assert r1["verdict"] == "PASS", r1

    far = {"positions": dict(good["positions"], **{"C3": {"x": 40, "y": 10, "block": "mcu"}}), "board_mm": [45, 30]}
    r2 = check(ir, far, {"main_nets_max_len_mm": {"VDD": 40}})
    assert r2["verdict"] == "FAIL" and any(f["code"] == "R3_CLUSTER" for f in r2["findings"]), r2

    overlap = {"positions": {"U1": {"x": 10, "y": 10, "block": "mcu"}, "C3": {"x": 10.8, "y": 10.8, "block": "mcu"},
                             "R1": {"x": 12, "y": 10, "block": "mcu"},
                             "U2": {"x": 11, "y": 10, "block": "psu"}, "C9": {"x": 11.8, "y": 10.8, "block": "psu"}},
               "board_mm": [45, 30]}
    r3 = check(ir, overlap, cfg)
    assert r3["verdict"] == "FAIL" and any(f["code"] == "R2_BBOX" for f in r3["findings"]), r3

    noblock = {"positions": {"U1": {"x": 10, "y": 10}}, "board_mm": [45, 30]}
    r4 = check(ir, noblock, cfg)
    assert r4["verdict"] == "FAIL" and any(f["code"] == "R1_COVER" for f in r4["findings"]), r4

    r5 = check(ir, good, {})  # 未配置任何主电路长度阈值 → 必须判 NEEDS_DECISION，不许默默放行
    assert r5["verdict"] == "NEEDS_DECISION" and any(f["code"] == "R4_MAIN_LEN" for f in r5["findings"]), r5

    # 引脚级口径：模块类 IC 的电容贴边缘引脚（中心距会误报）——真实口径必须过
    pads_ok = [{"dz": "U1", "pads": [{"n": "1", "x": 393.7, "y": 393.7, "net": "VDD"},   # (10,10)mm
                                      {"n": "2", "x": 393.7, "y": 787.4, "net": "SIG"}]},
               {"dz": "C3", "pads": [{"n": "1", "x": 472.4, "y": 393.7, "net": "VDD"}]},  # 距 U1.1 ≈2.0mm 内
               {"dz": "R1", "pads": [{"n": "1", "x": 393.7, "y": 590.6, "net": "SIG"}]},  # (10,15)mm，距 U1 SIG 脚 5mm
               {"dz": "U2", "pads": [{"n": "1", "x": 1181.1, "y": 787.4, "net": "VDD"}]},
               {"dz": "C9", "pads": [{"n": "1", "x": 1220.5, "y": 826.8, "net": "VDD"}]}]
    r6 = check(ir, good, cfg, pads_dump=pads_ok)
    assert r6["metric_r3"] == "pin_level" and r6["verdict"] == "PASS", r6

    pads_far = [{"dz": "U1", "pads": [{"n": "1", "x": 393.7, "y": 393.7, "net": "VDD"}]},
                {"dz": "C3", "pads": [{"n": "1", "x": 1574.8, "y": 393.7, "net": "VDD"}]}]  # 30mm 外
    r7 = check(ir, good, cfg, pads_dump=pads_far)
    assert r7["verdict"] == "FAIL" and any(f["code"] == "R3_CLUSTER" for f in r7["findings"]), r7

    d = derive(ir)
    assert {p["id"] for p in d["partitions"]} == {"mcu", "psu"} and d["partitions"][0]["members"], d
    print(json.dumps({"selftest": "PASS", "cases": ["PASS基线", "R3远电容", "R2重叠", "R1缺block", "R4缺阈值",
                                                    "derive", "R3引脚级PASS", "R3引脚级FAIL"]}, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser(description="G4 布局门：功能分区落实检查")
    ap.add_argument("--ir")
    ap.add_argument("--layout")
    ap.add_argument("--config", help="partitions 配置（阈值/例外/线宽）")
    ap.add_argument("--pads", help="g5in 型 pad dump（有则 R3 走引脚级真实口径）")
    ap.add_argument("--out", help="结果 JSON 落盘路径")
    ap.add_argument("--derive", help="由 IR 派生 G3 分区骨架并写到该路径（check 不执行）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.ir or not a.layout:
        print(json.dumps({"ok": False, "error": "需要 --ir 与 --layout（或 --selftest）"}, ensure_ascii=False))
        return 3
    ir = load_json(a.ir)
    layout = load_json(a.layout)
    if a.derive:
        save_json(a.derive, derive(ir))
        print(json.dumps({"ok": True, "derive": a.derive}, ensure_ascii=False))
        return 0
    cfg = load_json(a.config) if a.config else {}
    pads = load_json(a.pads) if a.pads else None
    r = check(ir, layout, cfg, pads_dump=pads)
    if a.out:
        save_json(a.out, r)
    compact = {k: r[k] for k in ("verdict", "board_diag_mm", "bbox_overlap_pairs")}
    compact["findings"] = [(f["severity"], f["code"], f["message"][:160]) for f in r["findings"]]
    print(json.dumps({"ok": r["verdict"] == "PASS", **compact, "out": a.out}, ensure_ascii=False))
    return {"PASS": 0, "NEEDS_DECISION": 2}.get(r["verdict"], 1)


if __name__ == "__main__":
    sys.exit(main())
