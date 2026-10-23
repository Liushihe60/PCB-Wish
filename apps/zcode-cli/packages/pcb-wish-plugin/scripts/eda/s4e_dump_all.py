#!/usr/bin/env python3
"""s4e_dump_all.py — 全量 dump 画布走线/过孔（供 s4_analyze.py --audit 与拐角直方图）。

用法: python s4e_dump_all.py --out _s4e_tracks_vias_final.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s4e_lib import invoke, save_json, HERE  # noqa: E402

FULL = r"""
const out = {lines: [], vias: [], regions: [], components: []};
for (const id of await eda.pcb_PrimitiveLine.getAllPrimitiveId()) {
  try { const l = await eda.pcb_PrimitiveLine.get(id);
    out.lines.push({id, net: l.getState_Net(), layer: l.getState_Layer(),
      sx: l.getState_StartX(), sy: l.getState_StartY(),
      ex: l.getState_EndX(), ey: l.getState_EndY(), w: l.getState_LineWidth()});
  } catch (e) { out.lines.push({id, err: String(e).slice(0,100)}); }
}
for (const id of await eda.pcb_PrimitiveVia.getAllPrimitiveId()) {
  try { const v = await eda.pcb_PrimitiveVia.get(id);
    out.vias.push({id, net: v.getState_Net(), x: v.getState_X(), y: v.getState_Y(),
      hole: v.getState_HoleDiameter(), dia: v.getState_Diameter()});
  } catch (e) { out.vias.push({id, err: String(e).slice(0,100)}); }
}
for (const id of await eda.pcb_PrimitiveRegion.getAllPrimitiveId()) {
  try { const r = await eda.pcb_PrimitiveRegion.get(id);
    out.regions.push({id, name: r.getState_RegionName(), layer: r.getState_Layer()});
  } catch (e) { out.regions.push({id, err: String(e).slice(0,80)}); }
}
for (const id of await eda.pcb_PrimitiveComponent.getAllPrimitiveId()) {
  try { const c = await eda.pcb_PrimitiveComponent.get(id);
    out.components.push({id, designator: c.getState_Designator(),
      x: c.getState_X(), y: c.getState_Y(), rotation: c.getState_Rotation()});
  } catch (e) { out.components.push({id, err: String(e).slice(0,80)}); }
}
const nets = await eda.pcb_Net.getAllNets();
out.netLen = {}; for (const n of nets) out.netLen[n.net] = n.length || 0;
out.counts = {lines: out.lines.length, vias: out.vias.length,
  regions: out.regions.length, components: out.components.length};
return out;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_s4e_tracks_vias_final.json")
    a = ap.parse_args()
    r = invoke(FULL, timeout_ms=590000)
    if not r.get("ok"):
        print(json.dumps(r, ensure_ascii=False)[:400]); sys.exit(1)
    v = r["value"]
    bad = [x for x in v["lines"] if x.get("err")]
    badv = [x for x in v["vias"] if x.get("err")]
    if bad or badv:
        print(f"!! 几何字段缺失 {len(bad)}线/{len(badv)}孔"); sys.exit(2)
    save_json(a.out, v)
    print("落盘:", a.out, "counts:", json.dumps(v["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
