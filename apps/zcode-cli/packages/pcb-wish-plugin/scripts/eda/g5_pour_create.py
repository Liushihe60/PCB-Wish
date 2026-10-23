#!/usr/bin/env python3
"""g5_pour_create.py — 建 GND 顶层+底层铺铜（多边形坐标按实测映射换算）。

实测映射（reports/_g5_map2.json，探针覆铜 L 点包围盒）：
  'R', X, Y, W, H  →  矩形 x ∈ [X, X+W]，y ∈ [Y-H, Y]（同画布坐标系，y 与画布一致）
  板框 = 画布 x 0..1772、y 0..1181 mil（45×30mm）
  内缩 0.4mm(15.75mil) → x 15.75..1756.25、y 15.75..1165.25
  ⇒ 多边形 = ['R', 15.75, 1165.25, 1740.5, 1149.5, 0, 0]

用法: python g5_pour_create.py [--inset 15.75] [--priority 1]
产出: reports/_g5_pour_create.json
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
const a = __CLI__.args;
const out = {steps: []};
// 清理所有旧覆铜（含探针残留）
const old = await eda.pcb_PrimitivePour.getAllPrimitiveId();
if (old.length) out.steps.push({deletedOld: await eda.pcb_PrimitivePour.delete(old), n: old.length});
await new Promise(r => setTimeout(r, 1500));
for (const s of [{layer:1, name:'GND_TOP'}, {layer:2, name:'GND_BOT'}]) {
  const poly = eda.pcb_MathPolygon.createPolygon(['R', a.x, a.y, a.w, a.h, 0, 0]);
  const pour = await eda.pcb_PrimitivePour.create('GND', s.layer, poly, 'solid', false, s.name, a.prio, 6, false);
  if (!pour) { out.steps.push({name: s.name, err: 'create null'}); continue; }
  const id = pour.getState_PrimitiveId();
  const r = await pour.rebuildCopperRegion();
  let bb = null, nf = null;
  if (r) {
    const fills = r.getState_PourFills(); nf = fills.length;
    let xs=[1e9,-1e9], ys=[1e9,-1e9];
    function lpts(sub) {
      const pts=[]; let i=0,n=sub.length;
      while (i<n && typeof sub[i] !== 'string') { if (typeof sub[i+1]==='number'){pts.push([sub[i],sub[i+1]]); i+=2;} else i++; }
      while (i<n) { const m=sub[i];
        if (m==='L') { i++; while (i+1<n && typeof sub[i]==='number' && typeof sub[i+1]==='number'){ pts.push([sub[i],sub[i+1]]); i+=2; if (i<n && typeof sub[i]==='string') break; } }
        else if (m==='ARC') i+=4; else if (m==='CARC') i+=6; else i++; }
      return pts;
    }
    for (const f of fills) for (const sub of f.path.complexPolygon) for (const q of lpts(sub)) {
      xs[0]=Math.min(xs[0],q[0]); xs[1]=Math.max(xs[1],q[0]); ys[0]=Math.min(ys[0],q[1]); ys[1]=Math.max(ys[1],q[1]);
    }
    bb = xs.concat(ys);
  }
  out.steps.push({name: s.name, id, layer: s.layer, nFills: nf, bbox_fillframe: bb});
  await new Promise(r => setTimeout(r, 1500));
}
out.pourIds = await eda.pcb_PrimitivePour.getAllPrimitiveId();
out.pouredIds = await eda.pcb_PrimitivePoured.getAllPrimitiveId();
return out;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inset", type=float, default=15.75)
    ap.add_argument("--priority", type=float, default=1)
    args = ap.parse_args()
    invoke(ACTIVATE, timeout_ms=120000)
    a = {"x": args.inset, "y": 1181 - args.inset, "w": 1772 - 2 * args.inset, "h": 1181 - 2 * args.inset,
         "prio": args.priority}
    print("多边形:", json.dumps(a))
    r = invoke(CREATE, timeout_ms=590000, args_json=json.dumps(a))
    v = r.get("value") or r
    print(json.dumps(v.get("steps"), ensure_ascii=False)[:1200])
    save_json("_g5_pour_create.json", v)


if __name__ == "__main__":
    main()
