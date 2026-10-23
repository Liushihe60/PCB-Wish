#!/usr/bin/env python3
"""g5_final_drc.py — T6：生成终验 DRC 报告（原始树 + 分类计数 + 逐条豁免说明）。

输入: reports/_g5_drc_raw_after_stitch2.json（文档重载后 pcb_Drc.check(true,false,true) 原始树）
输出: reports/drc_g5_final.json
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import load_json, save_json  # noqa: E402

RAW = "_g5_drc_raw_after_stitch2.json"

Q16_NETS = ["BTN1_N", "BTN2_N", "DEC3_N", "DEC456_N", "DEC5b_N", "DECUSB_N",
             "RESET_N", "SCL", "USB_DM_P", "VBUS", "XC2"]

GND_RESIDUAL = {
    "D1_2": "D1(ESD)GND 焊盘，最近异网铜(USB_DP) 0.233mm < 铺铜让出带 0.3mm，铺铜不可达；逃逸走线/过孔几何搜索无解",
    "e113": "既有 GND 缝合孔（冻结地基），VDD 走线距 0.226mm，铺铜不可达；不可挪动（禁动）",
    "e109": "既有 GND 缝合孔（冻结地基），VDD 走线距 0.121mm，铺铜不可达；不可挪动（禁动）",
    "X1_4": "晶振 X1 GND 焊盘，最近异网铜(VDD) 0.055mm，细间距，铺铜与走线均不可达",
    "U2_9": "TP4056 U2 GND 焊盘，最近异网铜(CHRG_N) 0.14mm，走线/过孔几何搜索无解",
    "U2_3": "TP4056 U2 GND 焊盘，最近异网铜(PROG) 0.086mm，细间距，几何不可达",
}

VIA_SIZE_NOTE = ("过孔外径 16mil < 项目规则 viaSize 24mil；周边异网铜净距仅 0.05~0.22mm，"
                 "改径到 24mil 会产生 0.15~0.21mm 新间距违例（S4E 已同结论），记档豁免；"
                 "JLC 双层 1oz 制造能力下限 0.3/0.2mm，16/10mil 可制造")

CLEARANCE_NOTE = {
    "Board Outline to Track": "VBAT 顶部贴边走线（板边净距 10.6mil < 11.8mil）；"
                              "capsule_S4 §8-F19 已判几何无解（板边 0.12mm + BAT_ADC 焊盘 0.97mm 夹逼），记档",
    "Track to Via": "GND 缝合孔与 VDD/VREG_IN 走线间距 4.2~5.5mil < 6mil；"
                    "涉既有 22 颗冻结缝合孔，挪孔=动地基、改 VDD 走线=重布线，均超出本站授权，记档",
}


def walk(nodes, out):
    for n in nodes:
        for it in (n.get("list") or []):
            if isinstance(it.get("list"), list) and it.get("errorType") is None:
                walk([it], out)
            else:
                out.append(it)


def main():
    raw = load_json(RAW)
    items = []
    walk(raw, items)
    by_type = Counter()
    by_rule = Counter()
    by_net = Counter()
    out_items = []
    for it in items:
        t = it.get("errorType") or ""
        rule = it.get("ruleName") or ""
        net = it.get("net") or ""
        suffix = (it.get("obj1") or {}).get("suffix") or ""
        by_type[t] += 1
        by_rule[f"{t}|{rule}"] += 1
        if net:
            by_net[str(net)] += 1
        disp = it.get("errorObjType") or ""
        expl = None
        if t == "Connection Error":
            if suffix.startswith("(GND)"):
                key = suffix.split(": ")[-1] if ": " in suffix else suffix
                expl = GND_RESIDUAL.get(key, "GND 残余连接错误")
            elif any(f"({n})" == suffix.split(":")[0] for n in Q16_NETS):
                expl = ("Q16 在案豁免：U1 aQFN73 细间距焊盘（行/列间距 0.5/0.25mm，焊盘间隙 ≈0.251mm），"
                        "任何 ≥0.05mm 走线都无法穿过（capsule_S4 §5，四档裕量全 blocked）")
            else:
                expl = "未分类连接错误"
        elif t == "Physical Error":
            expl = VIA_SIZE_NOTE
        elif t == "Clearance Error":
            expl = CLEARANCE_NOTE.get(disp, "残余间距错误")
        out_items.append({
            "category": it.get("parentId", "").split("|_|")[2] if it.get("parentId") else "",
            "type": t, "rule": rule, "ruleTypeName": it.get("ruleTypeName"),
            "net": net, "obj_type": disp, "obj1_suffix": suffix,
            "objs": it.get("objs") or [], "pos": it.get("pos"),
            "explanation_param": (it.get("explanation") or {}).get("param"),
            "exemption": expl,
            "layer": it.get("layer"),
        })
    gnd_conn = [i for i in out_items if i["type"] == "Connection Error" and i["net"] == "GND"]
    q16 = [i for i in out_items if i["type"] == "Connection Error" and i["net"] != "GND"]
    res = {
        "stage": "G5 铺铜与整板验证（终验）",
        "generated": "2026-10-21",
        "drc_call": "pcb_Drc.check(true, false, true)  （save → closeDocument → openDocument → activateDocument 复位后取数）",
        "raw_tree_file": RAW,
        "total": len(out_items),
        "by_type": dict(by_type),
        "by_rule": dict(by_rule.most_common()),
        "by_net": dict(by_net.most_common()),
        "acceptance": {
            "GND_connection_errors": len(gnd_conn),
            "GND_baseline_before_pour": 64,
            "Q16_connection_errors": len(q16),
            "Q16_nets": Q16_NETS,
            "Netlist_Error": by_type.get("Netlist Error", 0),
            "Clearance_Error": by_type.get("Clearance Error", 0),
            "Physical_Error": by_type.get("Physical Error", 0),
        },
        "items": out_items,
    }
    save_json("drc_g5_final.json", res)
    print("总数", res["total"], json.dumps(res["by_type"], ensure_ascii=False))
    print("GND 残余", len(gnd_conn), "Q16", len(q16), "Netlist", res["acceptance"]["Netlist_Error"])
    print("→ reports/drc_g5_final.json")


if __name__ == "__main__":
    main()
