#!/usr/bin/env python3
"""s3d_emit.py — S3D 产物落盘：spec/s3d_free_set.json + spec/layout_solved.spread.json（+链上备份）。

输入：reports/_s3d_free_analysis.json + reports/_s3d_solve_result.json
      + spec/layout_solved.post-strip.json（原位来源）+ reports/pcb_pads_dump_s3din.json
坐标：positions_abs = dump 空间 canvas mm（任务 mm y=30−y_canvas，与 post-strip 同口径）。
"""
import json
import shutil
from pathlib import Path

PROJ = Path("E:/agent project/projects/flexiheal-v1")
MIL = 39.37
H = 30.0

analysis = json.loads((PROJ / "reports/_s3d_free_analysis.json").read_text(encoding="utf-8"))
solve = json.loads((PROJ / "reports/_s3d_solve_result.json").read_text(encoding="utf-8"))
poststrip = json.loads((PROJ / "spec/layout_solved.post-strip.json").read_text(encoding="utf-8"))
dump = json.loads((PROJ / "reports/pcb_pads_dump_s3din.json").read_text(encoding="utf-8"))
comps = {c["dz"]: c for c in dump}

free_refs = analysis["free_refs"]
moved = solve["moved"]

# ---- spec/s3d_free_set.json（逐件：位号/现位/新位/理由/涉及网）----
NET_BY_REF = {}
for r in analysis["ledger"]:
    NET_BY_REF[r["ref"]] = r["nets"]

entries = []
for ref in free_refs:
    m = moved[ref]
    led = next(r for r in analysis["ledger"] if r["ref"] == ref)
    why = "; ".join(led["why_free"])
    note = ""
    if ref == "TP3":
        note = "（Q12 在案 9.4；疏散位移后 dev 变大属 D026 疏散优先授权，Q12 行更新数值）"
    if ref == "LED_A":
        note = "（Q12 在案 7.49；同带内重排，Q12 行更新数值）"
    entries.append({
        "ref": ref,
        "pos_before_task_mm": m["old_task_mm"],
        "pos_after_task_mm": m["new_task_mm"],
        "pos_after_canvas_mil": m["new_canvas_mil"],
        "rot": m["rot"],
        "nets": NET_BY_REF[ref],
        "reason": why + note,
        "class_source": led["class"],
    })
out_free = {
    "source": "S3D 温和疏散自由集（D026 授权：只疏散自由件）",
    "derivation": "reports/_s3d_free_analysis.json（全 59 件分类账，程序化逐件落理由）",
    "coord": "任务 mm（板左上原点）；canvas mil y 翻转=(30−y)×39.37",
    "count": len(entries),
    "items": entries,
    "locked_summary": "其余 55 件禁动（达标锚点/去耦环/晶振区/USB/R7R8/连接器/U1U2U3/Q14 持有网走廊件/P3 分压链/LED_B 配套），逐件理由见 derivation 账",
}
(PROJ / "spec/s3d_free_set.json").write_text(json.dumps(out_free, ensure_ascii=False, indent=1),
                                             encoding="utf-8")

# ---- spec/layout_solved.spread.json（全 59 件终态）+ 链上备份 ----
pre_path = PROJ / "spec/layout_solved.post-strip.pre-s3d.json"
if not pre_path.exists():
    shutil.copy2(PROJ / "spec/layout_solved.post-strip.json", pre_path)

pa = dict(poststrip["positions_abs"])
for ref in free_refs:
    m = moved[ref]
    pa[ref] = {"x_mm": m["new_canvas_mm"][0], "y_mm": m["new_canvas_mm"][1], "rot": m["rot"]}
# 与画布实位对账（防 spec 漂移）：post-strip 以外的 55 件须=画布现位
drift = []
for dz, c in comps.items():
    if dz in free_refs:
        continue
    p = pa.get(dz)
    if p is None:
        drift.append((dz, "missing"))
        continue
    if abs(p["x_mm"] - c["x"] / MIL) > 0.02 or abs(p["y_mm"] - c["y"] / MIL) > 0.02:
        drift.append((dz, p, [round(c["x"] / MIL, 3), round(c["y"] / MIL, 3)]))
assert not drift, f"spec 与画布漂移: {drift}"

spread = {
    "gap_mm": 0.5,
    "sources": "S3D 温和疏散（D026）：自由件 LED_A/LED_C/R16/TP3 摊入右侧稀疏带（capsule_S3 §3 释放区），"
               "其余 55 件原位（=layout_solved.post-strip.json 采纳终态，画布对账零漂移）；"
               "自由集推导=spec/s3d_free_set.json，求解=scripts/eda/s3d_spread.py（reports/_s3d_solve_result.json）",
    "positions_abs": dict(sorted(pa.items())),
    "moved_refs": free_refs,
    "residual": [],
}
(PROJ / "spec/layout_solved.spread.json").write_text(json.dumps(spread, ensure_ascii=False, indent=1),
                                                     encoding="utf-8")
print("OK spec/s3d_free_set.json（%d 件）+ spec/layout_solved.spread.json（%d 件）+ 备份 %s"
      % (len(entries), len(pa), pre_path.name))
