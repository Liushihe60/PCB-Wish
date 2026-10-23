#!/usr/bin/env python3
"""g5_pour.py — T2 GND 铺铜（顶层 + 底层）+ 电源同网铜皮。

用法:
  python g5_pour.py --action create      # 建 GND 顶/底铺铜（读回校验）
  python g5_pour.py --action read        # 只读回铺铜状态
  python g5_pour.py --action delete --names GND_TOP GND_BOT

参数:
  --inset-mil 15.75   铺铜相对板边内缩（15.75mil = 0.4mm > 板边铜距 0.3mm 判据）
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import invoke, save_json  # noqa: E402

ACTIVATE = r"""
const tree = await eda.dmt_EditorControl.getSplitScreenTree();
const tabs = tree.tabs || [];
const pcb = tabs.find(t => t.tabId && t.tabId.indexOf('1b70bcd67c184b71@') === 0);
if (!pcb) return JSON.stringify({err: 'PCB_TAB_NOT_FOUND'});
await eda.dmt_EditorControl.activateDocument(pcb.tabId);
"""

CREATE = r"""
const args = __CLI__.args;
const inset = args.inset;
const R = { x0: 0 + inset, y0: 0 + inset, x1: 1772 - inset, y1: 1181 - inset };
const w = R.x1 - R.x0, h = R.y1 - R.y0;
const cx = (R.x0 + R.x1) / 2, cy = (R.y0 + R.y1) / 2;
const out = {made: [], errors: []};
const specs = [
  {net: 'GND', layer: 1, name: 'GND_TOP', prio: 1},
  {net: 'GND', layer: 2, name: 'GND_BOT', prio: 1},
];
for (const s of specs) {
  try {
    const poly = eda.pcb_MathPolygon.createPolygon(['R', cx, cy, w, h, 0, 0]);
    if (!poly) { out.errors.push({name: s.name, err: 'polygon null'}); continue; }
    const pour = await eda.pcb_PrimitivePour.create(s.net, s.layer, poly, 'solid', false, s.name, s.prio, 6, false);
    if (!pour) { out.errors.push({name: s.name, err: 'pour null'}); continue; }
    const pid = pour.getState_PrimitiveId();
    const back = await eda.pcb_PrimitivePour.get(pid);
    out.made.push({name: s.name, net: s.net, layer: s.layer, id: pid,
      method: back.getState_PourFillMethod(), prio: back.getState_PourPriority(),
      lineWidth: back.getState_LineWidth(), preserve: back.getState_PreserveSilos(),
      polySrc: JSON.parse(JSON.stringify(poly.getSource ? poly.getSource() : null)),
      pourObj: JSON.parse(JSON.stringify(back))});
  } catch (e) { out.errors.push({name: s.name, err: String(e).slice(0, 300)}); }
}
out.pourIds = await eda.pcb_PrimitivePour.getAllPrimitiveId();
out.pouredIds = await eda.pcb_PrimitivePoured.getAllPrimitiveId();
return out;
"""

READ = r"""
const ids = await eda.pcb_PrimitivePour.getAllPrimitiveId();
const out = [];
for (const id of ids) {
  try { const p = await eda.pcb_PrimitivePour.get(id);
    out.push({id, net: p.getState_Net(), layer: p.getState_Layer(), name: p.getState_PourName(),
      method: p.getState_PourFillMethod(), prio: p.getState_PourPriority(),
      lineWidth: p.getState_LineWidth(), keys: Object.keys(p||{})});
  } catch(e) { out.push({id, err: String(e).slice(0,120)}); }
}
const pids = await eda.pcb_PrimitivePoured.getAllPrimitiveId();
const poured = [];
for (const id of pids) {
  try { const p = await eda.pcb_PrimitivePoured.get(id);
    const s = JSON.parse(JSON.stringify(p));
    poured.push({id, net: s.net, layer: s.layer, keys: Object.keys(s), area: s.area,
      bbox: s.boundingBox || s.bbox || null});
  } catch(e) { poured.push({id, err: String(e).slice(0,120)}); }
}
return {pours: out, pouredCount: pids.length, poured: poured.slice(0, 6)};
"""

DELETE = r"""
const args = __CLI__.args;
const ids = await eda.pcb_PrimitivePour.getAllPrimitiveId();
const del = [];
for (const id of ids) {
  try { const p = await eda.pcb_PrimitivePour.get(id);
    const nm = p.getState_PourName();
    if (!args.names || args.names.indexOf(nm) >= 0) { del.push(id); }
  } catch(e) {}
}
let r = null;
if (del.length) r = await eda.pcb_PrimitivePour.delete(del);
return {deleted: del, ret: r, left: (await eda.pcb_PrimitivePour.getAllPrimitiveId()).length};
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", default="create")
    ap.add_argument("--inset-mil", type=float, default=15.75)
    ap.add_argument("--names", nargs="*", default=None)
    args = ap.parse_args()
    a = invoke(ACTIVATE, timeout_ms=120000)
    if not a.get("ok"):
        print("activate 失败:", json.dumps(a, ensure_ascii=False)[:300])
        sys.exit(1)
    if args.action == "create":
        r = invoke(CREATE, timeout_ms=590000,
                   args_json=json.dumps({"inset": args.inset_mil}))
    elif args.action == "read":
        r = invoke(READ, timeout_ms=590000)
    elif args.action == "delete":
        r = invoke(DELETE, timeout_ms=300000,
                   args_json=json.dumps({"names": args.names}))
    else:
        print("未知 action")
        sys.exit(1)
    print(json.dumps(r.get("value") or r, ensure_ascii=False)[:4000])
    save_json(f"_g5_pour_{args.action}.json", r.get("value") or r)


if __name__ == "__main__":
    main()
