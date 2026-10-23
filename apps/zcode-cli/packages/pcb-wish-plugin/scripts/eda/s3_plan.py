#!/usr/bin/env python3
"""s3_plan.py — S3 布局站离线推演：画布↔manifest 一致性核验 + 孤儿走线/过孔清理计划。

输入：
  reports/pcb_pads_dump_s3in.json   （T1 快照：80 件/273 焊盘，canvas mil）
  reports/pcb_texts_dump_s3in.json  （T1 快照：80 条 L3 位号文本）
  reports/_s3_tracks_vias_in.json   （T1 快照：384 线 + 92 孔，canvas mil）
  reports/deletion_manifest.json    （唯一删除清单）
输出：
  reports/_s3_plan.json（一致性核验结论 + 删除计划：组件/文本/Region/走线/过孔/拆段）
只做离线推演，不触 EDA。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pcb_bodies_lib as B  # noqa: E402

PROJ = Path(r"E:/agent project/projects/flexiheal-v1")
MIL = B.MIL
H_MM = 30.0  # 板高 45x30（D018 实证：poly y866mil = task y22 = 30-8）

DEAD_NETS = {"ANT_N", "ANT_FEED", "XL1", "XL2", "DEC2_N", "DCC", "DCCH",
             "LMV_OUT", "LMV_INN", "LMV_INP", "REF_BIAS", "STDBY_N", "STDBYA"}
RETAIN = ["R7", "R8", "LED_C", "R16", "R20", "R21"]


def main():
    manifest = json.loads((PROJ / "reports/deletion_manifest.json").read_text(encoding="utf-8"))
    del_refs = [d["ref"] for d in manifest["delete_refs"]]
    comps = json.loads((PROJ / "reports/pcb_pads_dump_s3in.json").read_text(encoding="utf-8"))
    texts = json.loads((PROJ / "reports/pcb_texts_dump_s3in.json").read_text(encoding="utf-8"))
    tv = json.loads((PROJ / "reports/_s3_tracks_vias_in.json").read_text(encoding="utf-8"))
    lines, vias = tv["lines"], tv["vias"]

    by_dz = {c["dz"]: c for c in comps}
    report = {"canvas_counts": {"comp": len(comps), "line": len(lines), "via": len(vias),
                                "text": len(texts), "region": 1}}

    # ---- 1) 一致性：21 被删件在画布且坐标=capsule_S1 §4 ----
    expected_pos = {  # capsule_S1 §4 task mm
        "ANT1": (38.99, 3.51), "X2": (38.0, 9.5), "C22": (14.63, 15.04), "C23": (9.6, 21.08),
        "L1": (20.07, 23.65), "L2": (29.5, 17.6), "U4": (28.93, 12.75), "R_fb": (39.67, 20.98),
        "R_in": (32.79, 13.97), "R_ref1": (38.1, 17.43), "R_ref2": (38.02, 18.98),
        "R_refj1": (32.23, 12.35), "R_refj2": (30.96, 24.03), "C24": (39.93, 15.19),
        "C25": (33.93, 2.11), "C26": (38.89, 1.37), "L3": (36.45, 1.4), "C11": (16.61, 16.56),
        "R6": (13.69, 28.5), "LED_D": (14.94, 10.37), "R17": (11.91, 10.39)}
    pos_check, missing = [], []
    for r in del_refs:
        c = by_dz.get(r)
        if not c:
            missing.append(r)
            continue
        tx, ty = c["x"] / MIL, H_MM - c["y"] / MIL
        ex, ey = expected_pos[r]
        dev = max(abs(tx - ex), abs(ty - ey))
        pos_check.append({"ref": r, "task_mm": [round(tx, 3), round(ty, 3)],
                          "expected": [ex, ey], "dev_mm": round(dev, 4),
                          "ok": dev <= 0.05})
    report["del_refs_missing"] = missing
    report["pos_check"] = pos_check
    report["pos_all_ok"] = not missing and all(p["ok"] for p in pos_check)

    # ---- 2) 保留件在画布 ----
    report["retain_present"] = {r: (r in by_dz) for r in RETAIN}

    # ---- 3) 被删 21 件焊盘真实矩形（canvas mm，含 prot）----
    pad_rects = {}   # ref -> [x0,y0,x1,y1, padNumber]（canvas mm）
    for r in del_refs:
        for rect in B.pad_rects_mm(by_dz[r]):
            pad_rects.setdefault(r, []).append(list(rect[:4]) + [rect[4]])

    def in_any_del_pad(x_mil, y_mil):
        x, y = x_mil / MIL, y_mil / MIL
        for r, rects in pad_rects.items():
            for b in rects:
                if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
                    return f"{r}.{b[4]}"
        return None

    # ---- 4) 走线/过孔分类 ----
    del_lines, del_vias, cut_segments = [], [], []
    for l in lines:
        reason = None
        if l["net"] in DEAD_NETS:
            reason = f"死网 {l['net']}"
        else:
            hit1 = in_any_del_pad(l["x1"], l["y1"])
            hit2 = in_any_del_pad(l["x2"], l["y2"])
            if hit1 or hit2:
                reason = f"端点触被删焊盘 {hit1 or hit2}（网 {l['net']}，拆段）"
        if reason:
            (del_lines if l["net"] in DEAD_NETS or True else del_lines).append(
                {**{k: l[k] for k in ("id", "net", "layer", "x1", "y1", "x2", "y2")},
                 "reason": reason})
            if l["net"] not in DEAD_NETS:
                cut_segments.append({**{k: l[k] for k in ("id", "net", "layer", "x1", "y1", "x2", "y2")},
                                     "reason": reason})
    for v in vias:
        reason = None
        if v["net"] in DEAD_NETS:
            reason = f"死网 {v['net']}"
        else:
            hit = in_any_del_pad(v["x"], v["y"])
            if hit:
                reason = f"孔位落被删焊盘 {hit}（网 {v['net']}）"
        if reason:
            del_vias.append({**{k: v[k] for k in ("id", "net", "x", "y", "d", "h")}, "reason": reason})

    report["plan"] = {"del_lines": del_lines, "del_vias": del_vias,
                      "cut_segments_survivor_net": cut_segments}
    report["plan_counts"] = {
        "dead_net_lines": sum(1 for l in del_lines if l["net"] in DEAD_NETS),
        "cut_lines": len(cut_segments),
        "dead_net_vias": sum(1 for v in del_vias if v["net"] in DEAD_NETS),
        "pad_hit_vias": sum(1 for v in del_vias if v["net"] not in DEAD_NETS),
        "total_lines_del": len(del_lines), "total_vias_del": len(del_vias)}

    # ---- 5) 位号文本归属 ----
    comp_ids = {c["dz"]: None for c in comps}
    # 文本 parent = 组件 primitiveId；dump 里组件没有存自身 id → 用 parent->value 反查
    # 先取 21 件的文本（value/refDel 匹配 + parent 计数）
    t_by_parent = {}
    for t in texts:
        t_by_parent.setdefault(t["parent"], []).append(t)
    report["text_stats"] = {"total": len(texts), "unique_parents": len(t_by_parent),
                            "multi_parent": {p: len(v) for p, v in t_by_parent.items() if len(v) != 1}}
    # 被删件文本：value 位号精确匹配（位号唯一）
    tval = {}
    dup = []
    for t in texts:
        if t["value"] in tval:
            dup.append(t["value"])
        tval[t["value"]] = t
    report["text_dup_values"] = dup
    report["del_text_count"] = sum(1 for r in del_refs if r in tval)
    report["del_text_missing"] = [r for r in del_refs if r not in tval]

    out = PROJ / "reports/_s3_plan.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("canvas_counts", "pos_all_ok", "retain_present",
                                             "plan_counts", "text_stats", "text_dup_values",
                                             "del_text_count", "del_text_missing")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
