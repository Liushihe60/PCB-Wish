#!/usr/bin/env python3
"""s4e_fix_drc.py — 修 DRC 硬违例：①过小过孔改标准孔径 ②板边铜距不足的走线整体内移。

口径：板框 = 任务系 x0~45 / y0~30（板左上原点，y 向下）；板边铜距规则 0.2997mm；
走线半宽计入（实际铜缘到板边 ≥0.2997）。内移按"最近边法向"逐端点位移，
共享端点的多段用同一新坐标，保证连接不断。
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

SESSION = "44e574cc-dc99-46dd-80b8-60632f5b075a"
MIL = 39.37
BW, BH = 45.0, 30.0
EDGE = 0.2997

MODIFY_JS = r"""
const mods = __CLI__.args.mods;
const vias = __CLI__.args.vias;
let ok = 0, bad = 0, errs = [];
for (const m of mods) {
  try { await eda.pcb_PrimitiveLine.modify(m.id, {startX: m.sx, startY: m.sy, endX: m.ex, endY: m.ey}); ok++; }
  catch (e) { bad++; errs.push(String(e).slice(0,90)); }
}
for (const v of vias) {
  try { await eda.pcb_PrimitiveVia.modify(v.id, {holeDiameter: v.hole, diameter: v.dia}); ok++; }
  catch (e) { bad++; errs.push(String(e).slice(0,90)); }
}
await eda.pcb_Document.save();
return {ok, bad, errs: errs.slice(0,5),
        lines: (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length,
        vias: (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length};
"""


def edge_info(x, y):
    """返回 (到最近边距离, 内移单位向量)"""
    cands = [(x, (1.0, 0.0)), (BW - x, (-1.0, 0.0)), (y, (0.0, 1.0)), (BH - y, (0.0, -1.0))]
    d, v = min(cands, key=lambda t: t[0])
    return d, v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="_s4e_tracks_vias_final.json")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default="_s4e_drc_fix.json")
    a = ap.parse_args()
    d = load_json(a.dump)

    # ① 过小过孔 → 目标 12/24 mil（受异网铜净距 0.152 约束，可达则达标）
    from s4e_astar_fix import Obstacles, _pt_seg  # noqa: E402
    plmap = json.loads((HERE / "reports/_s4e_pad_layers_map.json").read_text(encoding="utf-8"))
    ob = Obstacles(HERE / "reports/pcb_pads_dump_manual.json", d,
                  clr_pad=0.152, clr_track=0.102, pad_layers=plmap)
    via_fix = []
    via_skip = []
    for v in d["vias"]:
        if v.get("err"):
            continue
        if (v.get("dia") or 0) >= 24 and (v.get("hole") or 0) >= 12:
            continue
        vx, vy = v["x"] / MIL, 30 - v["y"] / MIL
        net = v.get("net")
        r_cur = (v.get("dia") or 24) / 2 / MIL
        # 允许的最大外径：异网走线/焊盘/过孔净距 0.152
        lim = 99.0
        for t in ob.tracks:
            if t["net"] == net:
                continue
            dd = _pt_seg(vx, vy, t["p1"], t["p2"]) - t["w"] / 2
            lim = min(lim, dd - 0.152)
        for pd in ob.pads:
            if pd["net"] == net:
                continue
            if pd.get("layer") == 1:
                continue
            ddx = max(pd["rect"][0] - vx, 0.0, vx - pd["rect"][2])
            ddy = max(pd["rect"][1] - vy, 0.0, vy - pd["rect"][3])
            lim = min(lim, math.hypot(ddx, ddy) - 0.152)
        for vv in ob.vias:
            if vv["net"] == net:
                continue
            lim = min(lim, math.hypot(vv["x"] - vx, vv["y"] - vy) - vv["r"] - 0.152)
        dia_mm = 2 * max(lim, r_cur)
        dia_mil = min(24, int(dia_mm * MIL))
        if dia_mil < 20:
            via_skip.append({"id": v["id"], "net": net, "from": [v.get("hole"), v.get("dia")],
                             "reason": "净距不足，无法放大到 20mil"})
            continue
        via_fix.append({"id": v["id"], "net": net, "hole": 12, "dia": dia_mil,
                        "from": [v.get("hole"), v.get("dia")]})

    # ② 板边铜距不足的走线：逐端点求亏量并内移
    mods = []
    moved_pts = {}
    for l in d["lines"]:
        if l.get("err"):
            continue
        w_mm = (l.get("w") or 10) / MIL
        need = EDGE + w_mm / 2
        for px, py in ((l["sx"], l["sy"]), (l["ex"], l["ey"])):
            x, y = px / MIL, 30 - py / MIL
            dist, direction = edge_info(x, y)
            deficit = need - dist
            if deficit > 0.001:
                nx, ny = x + direction[0] * deficit, y + direction[1] * deficit
                moved_pts.setdefault((round(px, 2), round(py, 2)),
                                    (round(nx * MIL, 3), round((30 - ny) * MIL, 3), round(deficit, 4)))
    for l in d["lines"]:
        if l.get("err"):
            continue
        p1 = moved_pts.get((round(l["sx"], 2), round(l["sy"], 2)))
        p2 = moved_pts.get((round(l["ex"], 2), round(l["ey"], 2)))
        if p1 or p2:
            mods.append({"id": l["id"], "net": l.get("net"),
                         "sx": p1[0] if p1 else l["sx"], "sy": p1[1] if p1 else l["sy"],
                         "ex": p2[0] if p2 else l["ex"], "ey": p2[1] if p2 else l["ey"]})

    out = {"generated": time.strftime("%F %T"), "via_fix": via_fix,
           "via_skip": via_skip, "mods": mods,
           "moved_points": {str(k): v for k, v in moved_pts.items()}}
    save_json(a.out, out)
    print(f"过小过孔 {len(via_fix)} 颗（跳过 {len(via_skip)}）；板边内移走线 {len(mods)} 段（端点 {len(moved_pts)}）")
    for v in via_fix:
        print("   via", v["net"], v["from"], "-> 12/24")
    if not a.apply:
        print("（未 --apply）")
        return
    r = run_code(MODIFY_JS, SESSION, timeout_ms=590000,
                 args_json=json.dumps({"mods": mods, "vias": via_fix}, ensure_ascii=False))
    print("modify:", json.dumps(r.get("value") or r.get("error"), ensure_ascii=False)[:300])
    time.sleep(10)


if __name__ == "__main__":
    main()
