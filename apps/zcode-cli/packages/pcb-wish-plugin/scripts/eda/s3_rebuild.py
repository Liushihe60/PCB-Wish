#!/usr/bin/env python3
"""s3_rebuild.py — T6.1 布局规格重建 + 回位候选几何可行性测试。

口径（全部 dump 空间 canvas mm，任务系 y=(30−y_task)）：
- 固定锚 27（§5.2 显式坐标幸存者）=原样保留现位（现位已含 H4 内微调与 Q12 记录）。
- 派生项：就近原则重推导——仅对"拥挤妥协且被删 21 件释放空间可治愈"的条目
  提回位候选，逐个做 gap0.5 双层几何（覆盖区∪焊盘）可行性测试。
- 几何测试用求解器同款管线（pcb_bodies_lib + pcb_solve_overlaps.any_hit/BOARD）。
输出：reports/_s3_rebuild_tests.json。
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pcb_bodies_lib as B  # noqa: E402
import pcb_solve_overlaps as S  # noqa: E402

MIL = B.MIL
PROJ = Path(r"E:/agent project/projects/flexiheal-v1")
GAP = 0.5

FIXED31 = ["J5", "D1", "U2", "R_PROG", "J1", "U3", "R7", "R8", "J3", "X1", "U1",
           "L1", "L2", "X2", "J4", "LED_A", "LED_B", "BTN1", "BTN2", "J2",
           "R1", "C1", "R2", "C2", "TP1", "TP2", "TP3", "P3", "C20", "C21", "ANT1"]
DELS = ["ANT1", "X2", "C22", "C23", "L1", "L2", "U4", "R_fb", "R_in", "R_ref1", "R_ref2",
        "R_refj1", "R_refj2", "C24", "C25", "C26", "L3", "C11", "R6", "LED_D", "R17"]

# 回位候选：ref -> (目标任务系 mm, 理由)
CANDIDATES = {
    "R7": ((16.5, 6.9), "§5.2/pre-solve 锚回位；G3 拥挤迁北缘 dev7.81；释放空间已可容"),
    "R8": ((16.5, 8.5), "§5.2 锚回位；Q12 记录 dev8.1 自由件东迁；RF 位/keepout 删除后西归 VBUS/VREG_IN 干线"),
    "LED_A": ((35.0, 22.5), "§5.2 锚回位（蓝/红 LED 对团圆）；LED_A_N=Q14 持有网，回位缩短跨板"),
    "C21": ((24.5, 8.6), "X1 负载电容 §5.2 显式坐标→pre-solve (24.5,8.6)；回位收紧 XC2 回路（晶振区单层约束）"),
    "TP3": ((43.5, 23.9), "pre-solve 锚回位测试（Q12 +9.4）"),
    "R_PROG": ((14.5, 5.5), "§5.2 锚回位测试（Q12 +2.94；U2/U3 本体夹缝，预期仍不可容）"),
    "J3": ((24.0, 4.0), "§5.2 锚回位测试（Q12 +2.2；RELAX 锚定件）"),
}


def main():
    dump = json.loads((PROJ / "reports/pcb_pads_dump_s3out.json").read_text(encoding="utf-8"))
    comps = {c["dz"]: c for c in dump}
    bmap, dmap = B.load(str(PROJ / "spec/bodies.json"))
    fixed27 = [r for r in FIXED31 if r not in DELS]

    # 现位 state（canvas mm）：cov/pads 先取绝对再转相对，gap0.5 外扩
    state = {}
    for dz, c in comps.items():
        cov_abs, _ = B.cover_abs_mm(dz, c, bmap, dmap)
        if cov_abs is None:
            print(f"# {dz}: 无覆盖区，跳过")
            continue
        ox, oy = c["x"] / MIL, c["y"] / MIL
        rot0 = c.get("rot", 0) or 0
        pads = []
        for q in c["pads"]:
            pd = q.get("pad") or [0, 10, 10]
            w, h = pd[1] / MIL / 2, (pd[2] / MIL / 2 if len(pd) > 2 else pd[1] / MIL / 2)
            th_l = (q.get("prot") if q.get("prot") is not None else rot0) - rot0
            cc, ss = abs(math.cos(math.radians(th_l))), abs(math.sin(math.radians(th_l)))
            nw, nh = w * cc + h * ss, w * ss + h * cc
            rx, ry = q["x"] / MIL - ox, q["y"] / MIL - oy
            pads.append((ox + rx - nw - 0.05, oy + ry - nh - 0.05,
                         ox + rx + nw + 0.05, oy + ry + nh + 0.05, q["n"]))
        half = GAP / 2
        state[dz] = {"cov": [cov_abs[0] - half, cov_abs[1] - half,
                             cov_abs[2] + half, cov_abs[3] + half],
                     "pads": pads, "ox": ox, "oy": oy}

    def conflicts_for(dz, target_task_mm):
        tx, ty = target_task_mm[0], 30 - target_task_mm[1]  # canvas mm
        g = state[dz]
        dx, dy = tx - g["ox"], ty - g["oy"]
        gc = [g["cov"][0] + dx, g["cov"][1] + dy, g["cov"][2] + dx, g["cov"][3] + dy]
        if gc[0] < S.BOARD[0] or gc[1] < S.BOARD[1] or gc[2] > S.BOARD[2] or gc[3] > S.BOARD[3]:
            return ["<board>"]
        moved = {"cov": gc,
                 "pads": [(p[0] + dx, p[1] + dy, p[2] + dx, p[3] + dy, p[4]) for p in g["pads"]]}
        return [o for o in state if o != dz and S.any_hit(moved, state[o])]

    tests = {}
    for ref, (tgt, why) in CANDIDATES.items():
        conf = conflicts_for(ref, tgt)
        tests[ref] = {"target_task_mm": list(tgt), "why": why,
                      "conflicts": conf, "feasible": not conf}
        print(f"{ref:8s} -> {tgt}  {'FEASIBLE' if not conf else 'BLOCKED by ' + ','.join(conf[:6])}")

    conf = conflicts_for("TP3", (43.0, 29.0))
    tests["TP3@43,29"] = {"target_task_mm": [43.0, 29.0], "conflicts": conf, "feasible": not conf}
    print(f"TP3      -> (43, 29) {'FEASIBLE' if not conf else 'BLOCKED by ' + ','.join(conf[:6])}")

    (PROJ / "reports/_s3_rebuild_tests.json").write_text(
        json.dumps({"fixed27": fixed27, "tests": tests}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print("saved -> reports/_s3_rebuild_tests.json")


if __name__ == "__main__":
    main()
