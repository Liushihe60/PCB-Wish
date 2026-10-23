#!/usr/bin/env python3
"""g5_pour_fix.py — 修正 GND 铺铜多边形坐标（多边形帧原点在板顶，y 与画布 y 相差板高 1181mil）。

依据（reports/_g5_fill_dump.json 实测）：
  'R', X, Y, W, H  →  画布 x ∈ [X, X+W]，画布 y ∈ [1181-(Y+H), 1181-Y]
  原值 (886, 590.5, 1740.5, 1149.5) → 只覆盖画布右上象限。
  修正值 (15.75, 15.75, 1740.5, 1149.5) → 覆盖全板内缩 0.4mm。

同时删除探针用的 TEST_* 临时覆铜。

用法: python g5_pour_fix.py [--apply]
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

FIX = r"""
const a = __CLI__.args;
const out = {steps: [], fills: []};
// 1) 删除探针临时覆铜
const ids0 = await eda.pcb_PrimitivePour.getAllPrimitiveId();
const del = [];
for (const id of ids0) {
  const p = await eda.pcb_PrimitivePour.get(id);
  const nm = p.getState_PourName();
  if (nm && nm.indexOf('TEST_') === 0) del.push(id);
}
if (del.length) out.steps.push({deleted: await eda.pcb_PrimitivePour.delete(del)});
// 2) 修正 GND_TOP / GND_BOT 多边形
const ids = await eda.pcb_PrimitivePour.getAllPrimitiveId();
for (const id of ids) {
  const p = await eda.pcb_PrimitivePour.get(id);
  const nm = p.getState_PourName();
  if (nm !== 'GND_TOP' && nm !== 'GND_BOT') continue;
  const poly = eda.pcb_MathPolygon.createPolygon(['R', a.x, a.y, a.w, a.h, 0, 0]);
  const mod = await eda.pcb_PrimitivePour.modify(id, { complexPolygon: poly });
  const p2 = await eda.pcb_PrimitivePour.get(id);
  const r = await p2.rebuildCopperRegion();
  const fills = r ? r.getState_PourFills() : [];
  out.steps.push({name: nm, modified: !!mod,
    poly: JSON.stringify(p2.getState_ComplexPolygon ? p2.getState_ComplexPolygon() : null)});
  out.fills.push({name: nm, nFills: fills.length,
    polys: fills.map(f => f.path.complexPolygon)});
}
out.pourIds = await eda.pcb_PrimitivePour.getAllPrimitiveId();
return out;
"""


def parse_pts(sub):
    pts = []
    i, n = 0, len(sub)
    while i < n and not isinstance(sub[i], str):
        if i + 1 < n and isinstance(sub[i + 1], (int, float)):
            pts.append((sub[i], sub[i + 1])); i += 2
        else:
            i += 1
    while i < n:
        m = sub[i]
        if m == 'L':
            i += 1
            while i + 1 < n and isinstance(sub[i], (int, float)) and isinstance(sub[i + 1], (int, float)):
                pts.append((sub[i], sub[i + 1])); i += 2
                if i < n and isinstance(sub[i], str):
                    break
        elif m == 'ARC':
            i += 4
        elif m == 'CARC':
            i += 6
        else:
            i += 1
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--x", type=float, default=15.75)
    ap.add_argument("--y", type=float, default=15.75)
    ap.add_argument("--w", type=float, default=1740.5)
    ap.add_argument("--h", type=float, default=1149.5)
    args = ap.parse_args()
    invoke(ACTIVATE, timeout_ms=120000)
    r = invoke(FIX, timeout_ms=590000,
               args_json=json.dumps({"x": args.x, "y": args.y, "w": args.w, "h": args.h}))
    v = r.get("value") or r
    print(json.dumps(v.get("steps"), ensure_ascii=False)[:1200])
    for f in v.get("fills", []):
        bbs = []
        for poly in f["polys"]:
            allpts = []
            for sub in poly:
                allpts += parse_pts(sub)
            if not allpts:
                continue
            xs = [q[0] for q in allpts]; ys = [q[1] for q in allpts]
            bbs.append((min(xs), max(xs), min(ys), max(ys)))
        big = sorted(bbs, key=lambda b: -(b[1] - b[0]) * (b[3] - b[2]))[:4]
        print(f"{f['name']}: fills={f['nFills']}  最大几块 bbox(0.1mil) ->",
              [(round(b[0]), round(b[1]), round(b[2]), round(b[3])) for b in big])
    save_json("_g5_pour_fix.json", v)


if __name__ == "__main__":
    main()
