#!/usr/bin/env python3
"""g5_exec_gnd.py — 执行 GND 逃逸连线：为孤立 GND 图元补短走线 + 落地过孔。

输入: reports/_g5_escape_plan.json
输出: reports/_g5_exec_gnd.json（创建结果）
用法: python g5_exec_gnd.py [--apply] [--extra-via U1_0]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import invoke, save_json, load_json  # noqa: E402

ACTIVATE = r"""
const tree = await eda.dmt_EditorControl.getSplitScreenTree();
const tabs = tree.tabs || [];
const pcb = tabs.find(t => t.tabId && t.tabId.indexOf('1b70bcd67c184b71@') === 0);
if (!pcb) return JSON.stringify({err: 'PCB_TAB_NOT_FOUND'});
await eda.dmt_EditorControl.activateDocument(pcb.tabId);
"""

EXEC = r"""
const a = __CLI__.args;
const out = {lines: [], vias: [], errs: []};
for (const it of a.items) {
  try {
    // 走线：从 GND 焊盘/孔中心到新过孔位置
    if (it.track) {
      const ln = await eda.pcb_PrimitiveLine.create('GND', it.layer,
        it.sx, it.sy, it.ex, it.ey, it.w, false);
      if (!ln) { out.errs.push({name: it.name, err: 'line null'}); continue; }
      out.lines.push({name: it.name, id: ln.getState_PrimitiveId(),
        sx: it.sx, sy: it.sy, ex: it.ex, ey: it.ey, layer: it.layer, w: it.w});
    }
    if (it.via) {
      const v = await eda.pcb_PrimitiveVia.create('GND', it.vx, it.vy, it.hole, it.dia);
      if (!v) { out.errs.push({name: it.name, err: 'via null'}); continue; }
      out.vias.push({name: it.name, id: v.getState_PrimitiveId(),
        x: it.vx, y: it.vy, dia: it.dia, hole: it.hole});
    }
  } catch (e) { out.errs.push({name: it.name, err: String(e).slice(0, 250)}); }
}
out.counts = {lines: (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length,
  vias: (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length,
  comps: (await eda.pcb_PrimitiveComponent.getAllPrimitiveId()).length};
return out;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan", default="_g5_escape_plan.json")
    args = ap.parse_args()
    plan = load_json(args.plan)
    items = []
    for rec in plan["items"]:
        opts = rec.get("options") or []
        if not opts:
            print(f"skip {rec['name']}: 无解")
            continue
        o = opts[0]
        items.append({"name": rec["name"], "layer": o["layer"],
                      "sx": rec["x"], "sy": rec["y"], "ex": o["ex"], "ey": o["ey"],
                      "w": 8, "track": True,
                      "vx": o["ex"], "vy": o["ey"], "dia": 24, "hole": 12, "via": True})
    # 额外：U1.0 中心散热焊盘（190.9×190.9mil 大方盘）内落一颗热过孔
    extra = [i for i in items if i["name"] == "U1_0"]
    if not extra:
        items.append({"name": "U1_0_via_in_pad", "layer": 1,
                      "sx": 866, "sy": 630, "ex": 866, "ey": 630, "w": 8, "track": False,
                      "vx": 866, "vy": 630, "dia": 24, "hole": 12, "via": True})
    print("计划执行:", len(items), "项")
    if not args.apply:
        for i in items:
            print("  ", json.dumps(i, ensure_ascii=False))
        return
    invoke(ACTIVATE, timeout_ms=120000)
    r = invoke(EXEC, timeout_ms=590000, args_json=json.dumps({"items": items}))
    v = r.get("value") or r
    print("线:", len(v.get("lines", [])), "孔:", len(v.get("vias", [])), "错误:", json.dumps(v.get("errs"), ensure_ascii=False)[:400])
    print("计数:", json.dumps(v.get("counts"), ensure_ascii=False))
    save_json("_g5_exec_gnd.json", v)


if __name__ == "__main__":
    main()
