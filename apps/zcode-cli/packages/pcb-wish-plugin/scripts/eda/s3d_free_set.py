#!/usr/bin/env python3
"""s3d_free_set.py — S3D 温和疏散自由集推导（D026 授权口径，逐件落理由）。

判定依据（brief_S3D_spread §1）：
  纳入 = ① 派生 32 件中所有网络均无就近硬约束者
             （ir constraints：无 去耦≤2mm(C8) / 晶振<1.5mm(C5) / USB 串阻紧靠(C4) /
               R7R8 VIN 2mm(C9) 之类条款约束，含同类功能邻接：TVS 近 J5 例）；
         ② Q12 在案超差锚点件中电学自由者（TP3/LED_A）。
  禁动 = §5.2 达标锚点（dev≤2mm）/ 去耦电容环 / 晶振区(X1/C20/C21) / USB 差分与串阻 /
         J1~J5/P3 连接器 / U1/U2/U3 本体 / R7/R8 / Q14 持有网在案走廊件。

输出：reports/_s3d_free_analysis.json（全 59 件分类账，供人审）
坐标口径：dump 空间（canvas mm，y 翻转）；任务 mm = (x, 30−y)。
"""
import json
import sys
from pathlib import Path

PROJ = Path("E:/agent project/projects/flexiheal-v1")
MIL = 39.37
H = 30.0

# 禁动的网类（ir clauses / 功能邻接同类）
DEC_NETS = {"DEC1_N", "DEC3_N", "DEC456_N", "DEC5b_N", "DECUSB_N"}
POWER_NETS = {"VDD", "VBUS", "VBAT", "VREG_IN"}          # 去耦/储能电容所在
USB_NETS = {"USB_DP_P", "USB_DP", "USB_DM_P", "USB_DM", "CC1", "CC2"}
XTAL_NETS = {"XC1", "XC2"}
Q14_HELD = {"RESET_N", "BTN1_N", "BTN2_N", "DEC3_N", "BAT_ADC", "LED_A_N"}
CONNECTORS = {"J1", "J2", "J3", "J4", "J5", "P3"}
BODIES_BIG = {"U1", "U2", "U3", "X1"}


def nets_of(ref, net_members):
    return sorted({n for n, mem in net_members.items()
                   if any(m.split(".")[0] == ref for m in mem)})


def main():
    ir = json.loads((PROJ / "spec/ir.json").read_text(encoding="utf-8"))
    rebuild = json.loads((PROJ / "spec/layout.rebuild.json").read_text(encoding="utf-8"))
    presolve = json.loads((PROJ / "spec/layout.pre-solve.json").read_text(encoding="utf-8"))
    poststrip = json.loads((PROJ / "spec/layout_solved.post-strip.json").read_text(encoding="utf-8"))
    dump = json.loads((PROJ / "reports/pcb_pads_dump_s3din.json").read_text(encoding="utf-8"))

    net_members = {n["name"]: n["members"] for n in ir["nets"]}
    classes = rebuild["classes"]                       # fixed 27 / derived 32
    anchor = presolve["positions"]                     # G3 时代 §5.2 落位（任务 mm）
    cur = {c["dz"]: c for c in dump}                   # canvas mil

    def task_mm(dz):
        c = cur[dz]
        return (round(c["x"] / MIL, 2), round(H - c["y"] / MIL, 2))

    rows = []
    for dz in sorted(cur):
        cls = classes.get(dz, "?")
        nets = nets_of(dz, net_members)
        t = task_mm(dz)
        anchor_dev = None
        if dz in anchor:
            a = anchor[dz]
            anchor_dev = round(((t[0] - a[0]) ** 2 + (t[1] - a[1]) ** 2) ** 0.5, 2)
        rows.append({"ref": dz, "class": cls, "nets": nets,
                     "pos_task_mm": t, "anchor_task_mm": anchor.get(dz),
                     "anchor_dev_mm": anchor_dev})

    # ---- 分类 ----
    FREE_EXPLICIT = {"TP3", "LED_A"}                   # brief ②点名（Q12 在案、电学自由）
    # brief ②"LED_A（状态灯，链上自由）"同类：LED_A 链上串联电阻随 LED_A 同判
    # （LED_AA 网两端=LED_A/R18，链上自由）——但其 LED_A_N 侧为 Q14 持有网，走廊现状保持，
    # 且现位已在稀疏带内疏散零收益 → R18 仍禁动（落理由，不硬套"同类"）。
    FREE_Q14_CHAIN = set()
    q12 = {"TP3": 9.4, "LED_A": 7.49}                  # Q12 重测表在案 dev（其余锚点件为功能约束）
    ledger = []
    for r in rows:
        dz, nets, cls = r["ref"], r["nets"], r["class"]
        why_free, why_locked = [], []
        if dz in BODIES_BIG:
            why_locked.append("U1/U2/U3/X1 本体禁动")
        if dz in CONNECTORS:
            why_locked.append("连接器禁动")
        if dz in {"R7", "R8"}:
            why_locked.append("R7/R8 C9 供电跳线禁动")
        if cls == "fixed":
            dev = r["anchor_dev_mm"]
            if dz in q12:
                if dz in FREE_EXPLICIT:
                    why_free.append(f"Q12 在案超差锚点(dev{dev})且电学自由——brief ②点名纳入")
                else:
                    why_locked.append(f"Q12 在案但功能约束（TVS 近 J5/晶振负载/ADC 链/PROG/连接器同类）")
            elif dev is not None and dev <= 2.0:
                why_locked.append(f"§5.2 达标锚点(dev {dev}≤2mm)禁动")
            else:
                why_locked.append(f"§5.2 锚点件(dev {dev})非 Q12 在案——按达标禁动")
        # 派生件：按网成员判（电源网条款只锁电容——电阻/测试点在电源网上是"链上自由"引出，
        # 同一网上的 TP3 即 brief ②点名自由件，见 VBAT 先例）
        is_cap = dz.startswith("C")
        if not why_locked:
            on_dec = set(nets) & DEC_NETS
            on_pow = set(nets) & POWER_NETS
            on_usb = set(nets) & USB_NETS
            on_xtal = set(nets) & XTAL_NETS
            on_q14 = set(nets) & Q14_HELD
            if on_dec:
                why_locked.append(f"去耦电容环（{','.join(sorted(on_dec))}，C8 ≤2mm 条款）")
            if on_pow and is_cap:
                why_locked.append(f"电源去耦/储能电容（{','.join(sorted(on_pow))}，C8 同类条款）")
            if on_xtal:
                why_locked.append("晶振负载（C5 <1.5mm 条款）")
            if on_usb:
                why_locked.append(f"USB 接口电阻近 J5（{','.join(sorted(on_usb))}，C4 同类条款）")
            if on_q14 and dz not in FREE_Q14_CHAIN:
                why_locked.append(f"Q14 持有网在案走廊件（{','.join(sorted(on_q14))}，走廊现状保持）")
            if dz in {"C3", "R13"}:
                why_locked.append("复位 RC/上拉近 U1.AC13/R13（D1 'TVS 近 J5' 同类功能邻接；RESET_N=Q14 持有网）")
            if dz in {"R4", "R5"}:
                why_locked.append("P3_OUT 分压链随 P3 连接器就近（连接器配套，C9 跳线同类）")
            if dz == "R18":
                why_locked.append("LED_A 串联电阻本可链上自由，但 LED_A_N=Q14 持有网（U1.L1 逃逸走廊在案待裁决），"
                                  "且现位 (32.69,22.51) 已在稀疏带内——疏散零收益、动之改变 Q14 在案走廊现状，保持")
            if dz == "R19":
                why_locked.append("达标锚点件 LED_B 直接配套（LED_BA 网仅两成员），随 LED_B 现位；已在稀疏带内无疏散收益")
        status = "FREE" if (why_free and not why_locked) else ("LOCKED" if why_locked else "FREE?")
        if not why_free and not why_locked and cls == "derived":
            why_free.append("派生 32 件中所有网络均无就近硬约束（低速指示/链上自由）")
            status = "FREE"
        ledger.append({**r, "status": status,
                       "why_free": why_free, "why_locked": why_locked})

    out = {
        "source": "S3D 自由集推导（D026 只疏散自由件·温和）",
        "coord": "任务 mm（y=板左上原点）；画布 y 翻转 = 30−y",
        "free_refs": [r["ref"] for r in ledger if r["status"] == "FREE"],
        "ledger": ledger,
    }
    (PROJ / "reports/_s3d_free_analysis.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    fr = out["free_refs"]
    print("FREE :", " ".join(fr), f"({len(fr)} 件)")
    print("LOCKED:", sum(1 for r in ledger if r["status"] == "LOCKED"),
          "  FREE?:", [r["ref"] for r in ledger if r["status"] == "FREE?"])
    for r in ledger:
        if r["status"] == "FREE":
            print(f"  {r['ref']}: {r['pos_task_mm']} nets={r['nets']} :: {'; '.join(r['why_free'])}")


if __name__ == "__main__":
    main()
