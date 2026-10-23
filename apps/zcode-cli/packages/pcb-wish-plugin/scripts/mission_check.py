#!/usr/bin/env python3
"""mission_check.py — MISSION.md 规约完备性校验器（pcb-wish intake 门禁）。

用法:
    python mission_check.py <mission.md> [--json]

退出码: 0=PASS(ok:true), 1=FAIL(ok:false), 2=文件/格式错误。

判定规则（与 templates/MISSION.md.template 对应）:
  S1 六个章节齐全(0-5)
  S2 硬约束: 至少 1 条 [Hn]，每条不含缺口标记（缺口/待确认/TODO/TBD/待补充）
  S3 软约束: 有优先级排序行 (S1 > S2 ...) 且至少一条 S 条目
  S4 范围自检: 层数 ≤4；明确声明 无 RF、无盲埋孔
  S5 交付物: 制造文件+DFM 必须勾选
  S6 授权状态: 常设授权必须明确（是/否）
"""
import argparse
import json
import re
import sys
from pathlib import Path

GAP_MARKERS = ("缺口", "待确认", "TODO", "TBD", "待补充")
H_LINE = re.compile(r"^-\s*\[H(\d+)\]\s*(.+)$", re.M)
S_ORDER = re.compile(r"S\d+\s*(?:>\s*S\d+)+")
S_LINE = re.compile(r"^-\s*\[?S(\d+)\]?\s*", re.M)
LAYER_LINE = re.compile(r"层数[:：]\s*(\d+)\s*层")
DELIVER_LINE = re.compile(r"^-\s*\[( |x|X)\]\s*(.+)$", re.M)
AUTHORIZE = re.compile(r"常设授权[:：]\s*【?(是|否)")

SECTIONS = {
    "0": "需求原文",
    "1": "硬约束（H）",
    "2": "软约束排序（S）",
    "3": "范围自检",
    "4": "交付物",
    "5": "授权状态",
}


def split_sections(text):
    """按 '## <编号>. ' 标题切分，返回 {编号: 正文}。"""
    parts = re.split(r"^##\s+", text, flags=re.M)
    out = {}
    for p in parts:
        m = re.match(r"(\d+)\.\s*(.+)", p.splitlines()[0].strip()) if p.strip() else None
        if m:
            out[m.group(1)] = "\n".join(p.splitlines()[1:]).strip()
    return out


def check(path):
    gaps, warnings = [], []

    def gap(sec, item, msg):
        gaps.append({"section": sec, "item": item, "message": msg})

    p = Path(path)
    if not p.exists():
        return {"ok": False, "gaps": [{"section": "-", "item": "file", "message": f"文件不存在: {p}"}], "warnings": [], "stats": {}}
    text = p.read_text(encoding="utf-8", errors="replace")
    secs = split_sections(text)

    # S1 章节齐全
    for num, name in SECTIONS.items():
        if num not in secs or not secs[num]:
            gap(num, name, f"缺少章节 {num} {name}")
    h_sec = secs.get("1", "")
    h_items = H_LINE.findall(h_sec)
    stats = {"h_count": len(h_items)}
    if not h_items:
        gap("1", "硬约束", "未找到任何 [Hn] 硬约束条目")
    for idx, body in h_items:
        hit = next((mk for mk in GAP_MARKERS if mk in body), None)
        if hit:
            gap("1", f"H{idx}", f"H{idx} 仍是未闭合缺口（含“{hit}”）: {body[:60]}…")
    if h_sec and "（来源：" not in h_sec:
        gap("1", "硬约束", "硬约束缺少来源标注（来源：用户/推导/默认）")

    # S3 软约束排序
    s_sec = secs.get("2", "")
    stats["s_count"] = len(S_LINE.findall(s_sec))
    if s_sec and not S_ORDER.search(s_sec):
        gap("2", "软约束排序", "缺少优先级排序行（应形如 S1 > S2 > S3）")
    if s_sec and not S_LINE.search(s_sec):
        gap("2", "软约束", "缺少 S 条目明细")

    # S4 范围护栏
    r_sec = secs.get("3", "")
    lm = LAYER_LINE.search(r_sec)
    if lm:
        layers = int(lm.group(1))
        stats["layers"] = layers
        if layers > 4:
            gap("3", "层数", f"层数 {layers} 超出护栏（≤4）——超出当前 agent 能力范围，须改需求或终止")
    elif r_sec:
        gap("3", "层数", "未声明层数（应写“层数：N 层”）")
    if r_sec:
        if "RF" in r_sec and "无 RF" not in r_sec and "无RF" not in r_sec:
            gap("3", "RF", "范围自检未明确声明“无 RF”")
        if "盲埋孔" in r_sec and "无盲埋孔" not in r_sec:
            gap("3", "盲埋孔", "范围自检未明确声明“无盲埋孔”")

    # S5 交付物
    d_sec = secs.get("4", "")
    deliver_ok = False
    for mark, body in DELIVER_LINE.findall(d_sec):
        if mark.lower() == "x" and "制造文件" in body and "DFM" in body:
            deliver_ok = True
    if d_sec and not deliver_ok:
        gap("4", "制造文件", "交付物中“制造文件+DFM”必须存在且已勾选")

    # S6 授权状态
    a_sec = secs.get("5", "")
    am = AUTHORIZE.search(a_sec)
    if a_sec and not am:
        gap("5", "常设授权", "常设授权未明确（是/否）")
    elif am and am.group(1) == "否":
        warnings.append("常设授权=否：后续每个设计决策都需用户确认，预计交互轮次较多")

    return {"ok": not gaps, "gaps": gaps, "warnings": warnings, "stats": stats}


def main():
    ap = argparse.ArgumentParser(description="MISSION.md 完备性校验")
    ap.add_argument("mission", help="MISSION.md 路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()
    try:
        result = check(args.mission)
        code = 0 if result["ok"] else 1
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "gaps": [{"section": "-", "item": "parse", "message": str(e)}], "warnings": [], "stats": {}}
        code = 2
    if args.json:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("PASS：约束齐备，可以开始设计" if result["ok"] else "FAIL：存在未闭合缺口")
        for g in result["gaps"]:
            print(f"  [缺口] §{g['section']} {g['item']}: {g['message']}")
        for w in result["warnings"]:
            print(f"  [提示] {w}")
    sys.exit(code)


if __name__ == "__main__":
    main()
