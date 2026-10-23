#!/usr/bin/env python3
"""s4e_pads_dump.py — 从 EDA 全量 dump 焊盘（含层别/通孔属性），供几何模型判定层间间距。

用法: python s4e_pads_dump.py --out _s4e_pads_layers.json
输出: [{"key":"U1.AD6","x":..,"y":..,"net":..,"layer":1,"hole":..,"pad":[..],"rot":..}]
layer 12 = Multi-Layer(通孔), 1 = 顶层, 2 = 底层
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s4e_lib import invoke, save_json, HERE  # noqa: E402

PADS_JS = r"""
const out = [];
for (const id of await eda.pcb_PrimitivePad.getAllPrimitiveId()) {
  try {
    const p = await eda.pcb_PrimitivePad.get(id);
    let dz = null, num = null;
    try { const c = await eda.pcb_PrimitiveComponent.get(p.getState_PrimitiveId()); } catch(e) {}
    out.push({id, x: p.getState_X(), y: p.getState_Y(), net: p.getState_Net(),
      layer: p.getState_Layer(), num: (p.getState_PadNumber !== undefined ? p.getState_PadNumber() : null),
      pad: p.getState_Pad ? p.getState_Pad() : null, rot: p.getState_Rotation ? p.getState_Rotation() : null});
  } catch (e) { out.push({id, err: String(e).slice(0,80)}); }
}
return out;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_s4e_pads_layers.json")
    a = ap.parse_args()
    r = invoke(PADS_JS, timeout_ms=590000)
    if not r.get("ok"):
        print(json.dumps(r, ensure_ascii=False)[:400]); sys.exit(1)
    v = r["value"]
    bad = [x for x in v if x.get("err")]
    if bad:
        print(f"!! {len(bad)} 焊盘读取失败")
    # 关联位号：用画布几何 + 已有 pads dump 的 (x,y) 匹配
    dump = json.loads((HERE / "reports/pcb_pads_dump_manual.json").read_text(encoding="utf-8"))
    pos2key = []
    for c in dump:
        for p in c.get("pads", []):
            pos2key.append((p["x"], p["y"], f"{c['dz']}.{p['n']}"))
    for p in v:
        x, y = p.get("x"), p.get("y")
        best = None
        bd = 1e9
        for (mx, my, k) in pos2key:
            dd = (mx - x) ** 2 + (my - y) ** 2
            if dd < bd:
                bd = dd
                best = k
        p["key"] = best if bd <= 1.0 else None
    save_json(a.out, v)
    print("落盘:", a.out, "pads:", len(v), "未匹配位号:",
          sum(1 for p in v if not p.get("key")))


if __name__ == "__main__":
    main()
