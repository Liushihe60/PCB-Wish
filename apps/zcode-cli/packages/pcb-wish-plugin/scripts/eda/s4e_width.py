#!/usr/bin/env python3
"""s4e_width.py — T4b/T6 网级线宽合规化：按几何余量把走线加宽到网级规则宽度。

判定：
  - 目标宽度 = 网级规则宽度（VBUS/VBAT 1.0 / VDD/VREG_IN 0.6 / DEC_*+ADC_IN+DIV 0.3）
  - 每段实际可加宽量 = min(到异网焊盘净距, 到异网走线净距, 到异网过孔净距, 到板边净距) 推出的上限
  - 实际宽度 = clamp(min(规则宽, 上限), 当前宽, 规则宽)  —— 只加宽不加窄
  - 加宽后用 EDA DRC 终验（几何模型只作第一道筛）

用法:
  python s4e_width.py --plan     # 只出计划（不发写操作）
  python s4e_width.py --apply    # 按计划下发 modify + save + 等 10s + 重枚举
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s4e_lib import invoke, save_json, load_json, HERE  # noqa: E402
from lceda_invoke import run_code  # noqa: E402
import pcb_route_lib as R  # noqa: E402

SESSION = "44e574cc-dc99-46dd-80b8-60632f5b075a"
MIL = 39.37
ALIAS = {"DEC5b_N": "DEC5B_N"}
PROGRESS = HERE / "reports/s4_progress.md"

MODIFY_JS = r"""
const items = __CLI__.args.items;   // [{id, w}]
let ok = 0, bad = 0, errs = [];
for (const it of items) {
  try { await eda.pcb_PrimitiveLine.modify(it.id, {lineWidth: it.w}); ok++; }
  catch (e) { bad++; errs.push(String(e).slice(0,90)); }
}
return {ok, bad, errs: errs.slice(0,5)};
"""

COUNT_JS = r"""
const out = {};
out.lines = (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length;
out.vias  = (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length;
out.comps = (await eda.pcb_PrimitiveComponent.getAllPrimitiveId()).length;
const nets = await eda.pcb_Net.getAllNets();
out.netLen = {}; for (const n of nets) out.netLen[n.net] = n.length || 0;
return out;
"""


def plan_widths():
    plan = json.loads((HERE / "spec/route_plan.json").read_text(encoding="utf-8"))
    w = {}
    for n in plan["nets"]:
        if n.get("width_mm"):
            w[ALIAS.get(n["name"], n["name"])] = n["width_mm"]
    return w


def logline(msg):
    line = f"- S4E {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with PROGRESS.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def pt_rect_dist(px, py, rect):
    rx0, ry0, rx1, ry1 = rect
    ddx = max(rx0 - px, 0.0, px - rx1)
    ddy = max(ry0 - py, 0.0, py - ry1)
    return math.hypot(ddx, ddy)


def seg_axis_rect_dist(p, q, rect):
    """线段中轴到矩形的最小距离（采样）。"""
    L = math.hypot(q[0] - p[0], q[1] - p[1])
    n = max(2, int(L / 0.2) + 1)
    best = 1e9
    for k in range(n):
        t = k / (n - 1)
        x = p[0] + (q[0] - p[0]) * t
        y = p[1] + (q[1] - p[1]) * t
        d = pt_rect_dist(x, y, rect)
        if d < best:
            best = d
    return best


def seg_axis_point_dist(p, q, cx, cy):
    dx, dy = q[0] - p[0], q[1] - p[1]
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(cx - p[0], cy - p[1])
    t = max(0.0, min(1.0, ((cx - p[0]) * dx + (cy - p[1]) * dy) / L2))
    return math.hypot(cx - (p[0] + t * dx), cy - (p[1] + t * dy))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="_s4e_tracks_vias_final.json")
    ap.add_argument("--pads", default="pcb_pads_dump_manual.json")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--nets", nargs="*", default=None)
    ap.add_argument("--out", default="_s4e_width_plan.json")
    a = ap.parse_args()

    dump = load_json(a.dump)
    W = plan_widths()
    pads = R.load_pad_rects([str(HERE / "reports" / a.pads)])
    # 走线（任务系 mm）
    tracks = []
    for l in dump["lines"]:
        if l.get("err"):
            continue
        tracks.append({"id": l["id"], "net": l.get("net"), "layer": l.get("layer"),
                       "w_mm": (l.get("w") or 0) / MIL,
                       "p": (l["sx"] / MIL, 30.0 - l["sy"] / MIL),
                       "q": (l["ex"] / MIL, 30.0 - l["ey"] / MIL),
                       "w_mil": l.get("w") or 0})
    vias = []
    for v in dump["vias"]:
        if v.get("err"):
            continue
        vias.append({"net": v.get("net"), "x": v["x"] / MIL, "y": 30.0 - v["y"] / MIL,
                     "dia_mm": (v.get("dia") or 0) / MIL})

    target_nets = a.nets or [n for n, w in W.items() if w > 0.26]
    items = []
    per_net = {}
    for l in tracks:
        net = l["net"]
        if net not in target_nets:
            continue
        rule_mm = W.get(net)
        if not rule_mm:
            continue
        rule_mil = rule_mm * MIL
        if l["w_mil"] >= rule_mil - 0.01:
            continue  # 已达规则宽
        p, q = l["p"], l["q"]
        lim = rule_mil
        why = []
        # 异网焊盘
        for pd in pads:
            if pd["net"] == net:
                continue
            d = seg_axis_rect_dist(p, q, pd["rect"])
            w_max = 2 * (d - R.CLR_PAD) * MIL
            if w_max < lim:
                lim = max(w_max, l["w_mil"])
                why.append(f"pad:{pd['dz']}.{pd['pad']}({d:.3f}mm)")
        # 异网走线（同层）
        for t in tracks:
            if t["net"] == net or t["layer"] != l["layer"]:
                continue
            d = R.seg_seg_dist((p, q, 0.0), (t["p"], t["q"], t["w_mm"]))
            w_max = 2 * (d - R.CLR_TRACK) * MIL
            if w_max < lim:
                lim = max(w_max, l["w_mil"])
                why.append(f"trk:{t['net']}({d:.3f}mm)")
        # 异网过孔
        for v in vias:
            if v["net"] == net:
                continue
            d = seg_axis_point_dist(p, q, v["x"], v["y"]) - v["dia_mm"] / 2
            w_max = 2 * (d - 0.152) * MIL
            if w_max < lim:
                lim = max(w_max, l["w_mil"])
                why.append(f"via:{v['net']}({d:.3f}mm)")
        # 板边
        edge = min(min(p[0], q[0]), min(p[1], q[1]),
                   R.BOARD_W - max(p[0], q[0]), R.BOARD_H - max(p[1], q[1]))
        w_max = 2 * (edge - R.EDGE_MM) * MIL
        if w_max < lim:
            lim = max(w_max, l["w_mil"])
            why.append("edge")
        # 富余要求：几何上限需比规则宽再多 0.05mm（2mil）才采纳，避免擦边
        target = rule_mil if lim >= rule_mil + 2.0 else lim
        new_w = round(min(rule_mil, target), 2)
        new_w = max(new_w, l["w_mil"])
        if new_w > l["w_mil"] + 0.01:
            items.append({"id": l["id"], "w": new_w, "net": net,
                          "from": l["w_mil"], "rule": rule_mil, "limit": round(lim, 2),
                          "why": why[:3]})
            per_net.setdefault(net, []).append(new_w)
    plan = {"generated": time.strftime("%F %T"), "rule_mm": W,
            "target_nets": target_nets,
            "items": items,
            "summary": {n: {"segments": len(v),
                             "widths_mil": sorted(set(v)),
                             "rule_mil": W.get(n, 0) * MIL}
                        for n, v in per_net.items()}}
    save_json(a.out, plan)
    print("落盘:", a.out, " 待加宽段数:", len(items))
    print(json.dumps(plan["summary"], ensure_ascii=False, indent=1))
    if not a.apply:
        return
    r = run_code(MODIFY_JS, SESSION, timeout_ms=590000,
                 args_json=json.dumps({"items": items}, ensure_ascii=False))
    print("modify:", json.dumps(r.get("value") or r.get("error"), ensure_ascii=False)[:300])
    time.sleep(10)
    c = invoke(COUNT_JS, timeout_ms=300000)
    print("画布:", json.dumps(c.get("value"), ensure_ascii=False)[:200])
    logline(f"T4b 线宽加宽: 段 {len(items)} → {json.dumps(plan['summary'], ensure_ascii=False)[:200]}")


if __name__ == "__main__":
    main()
