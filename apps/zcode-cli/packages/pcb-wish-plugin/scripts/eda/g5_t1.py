#!/usr/bin/env python3
"""g5_t1.py — G5 T1: 激活页签 + 3 次一致读数 + 铺铜前全量快照。

产出:
  reports/_s5_snapshot_pre_pour.json   回滚依据（lines/vias/pads/pours/outline/counts）
  reports/_s5_t1_probe.json             3 次读数

用法: python g5_t1.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import invoke, save_json  # noqa: E402

ACTIVATE = r"""
const tree = await eda.dmt_EditorControl.getSplitScreenTree();
const tabs = tree.tabs || [];
const pcb = tabs.find(t => t.tabId && t.tabId.indexOf('1b70bcd67c184b71@') === 0);
if (!pcb) return JSON.stringify({err: 'PCB_TAB_NOT_FOUND', tabs: tabs.map(t=>t.tabId)});
await eda.dmt_EditorControl.activateDocument(pcb.tabId);
return JSON.stringify({activated: pcb.tabId, doctype: pcb.data && pcb.data.doctype});
"""

PROBE = r"""
const out = {};
out.lines = (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length;
out.vias  = (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length;
out.comps = (await eda.pcb_PrimitiveComponent.getAllPrimitiveId()).length;
out.pours = (await eda.pcb_PrimitivePour.getAllPrimitiveId()).length;
out.poured= (await eda.pcb_PrimitivePoured.getAllPrimitiveId()).length;
out.regions = (await eda.pcb_PrimitiveRegion.getAllPrimitiveId()).length;
const polys = await eda.pcb_PrimitivePolyline.getAllPrimitiveId();
out.polylines = polys.length;
const attrs = await eda.pcb_PrimitiveAttribute.getAllPrimitiveId();
let ntxt = 0;
for (const id of attrs) { try { const a = await eda.pcb_PrimitiveAttribute.get(id);
  if (a.layer === 3 && a.valueVisible) ntxt++; } catch(e) {} }
out.texts = ntxt;
return out;
"""

FULL = r"""
const out = {lines: [], vias: [], pads: [], pours: [], poured: [], components: [], polylines: []};
const lids = await eda.pcb_PrimitiveLine.getAllPrimitiveId();
for (const id of lids) {
  try { const l = await eda.pcb_PrimitiveLine.get(id);
    const o = {id, net: l.getState_Net(), layer: l.getState_Layer(),
      sx: l.getState_StartX(), sy: l.getState_StartY(),
      ex: l.getState_EndX(), ey: l.getState_EndY(),
      w: l.getState_LineWidth()};
    try { o.rotation = l.getState_Rotation(); } catch(e) {}
    out.lines.push(o);
  } catch (e) { out.lines.push({id, err: String(e).slice(0,120)}); }
}
const vids = await eda.pcb_PrimitiveVia.getAllPrimitiveId();
for (const id of vids) {
  try { const v = await eda.pcb_PrimitiveVia.get(id);
    const o = {id, net: v.getState_Net(), x: v.getState_X(), y: v.getState_Y(),
      hole: v.getState_HoleDiameter(), dia: v.getState_Diameter()};
    try { o.layer = v.getState_Layer(); } catch(e) {}
    out.vias.push(o);
  } catch (e) { out.vias.push({id, err: String(e).slice(0,120)}); }
}
// 焊盘：几何 + 层别（净距核算输入）
const pids = await eda.pcb_PrimitivePad.getAllPrimitiveId();
for (const id of pids) {
  try { const p = await eda.pcb_PrimitivePad.get(id);
    const o = {id, net: p.getState_Net ? p.getState_Net() : p.net,
      x: p.getState_X ? p.getState_X() : p.x,
      y: p.getState_Y ? p.getState_Y() : p.y,
      layer: p.getState_Layer ? p.getState_Layer() : p.layer,
      shape: p.getState_Shape ? p.getState_Shape() : p.shape,
      w: p.getState_Width ? p.getState_Width() : p.width,
      h: p.getState_Height ? p.getState_Height() : p.height,
      rotation: p.getState_Rotation ? p.getState_Rotation() : p.rotation};
    try { o.number = p.getState_PadNumber ? p.getState_PadNumber() : p.padNumber; } catch(e) {}
    try { o.parentPrimitiveId = p.getState_ParentPrimitiveId(); } catch(e) {}
    out.pads.push(o);
  } catch (e) { out.pads.push({id, err: String(e).slice(0,120)}); }
}
const poid = await eda.pcb_PrimitivePour.getAllPrimitiveId();
for (const id of poid) { try { const p = await eda.pcb_PrimitivePour.get(id);
  out.pours.push({id, net: p.getState_Net(), layer: p.getState_Layer(),
    name: p.getState_PourName(), method: p.getState_PourFillMethod(),
    priority: p.getState_PourPriority(), lineWidth: p.getState_LineWidth()});
} catch(e) { out.pours.push({id, err: String(e).slice(0,80)}); } }
const puid = await eda.pcb_PrimitivePoured.getAllPrimitiveId();
for (const id of puid) { try { const p = await eda.pcb_PrimitivePoured.get(id);
  out.poured.push({id, net: p.getState_Net(), layer: p.getState_Layer(),
    keys: Object.keys(p||{}).slice(0,20)});
} catch(e) { out.poured.push({id, err: String(e).slice(0,80)}); } }
const cids = await eda.pcb_PrimitiveComponent.getAllPrimitiveId();
for (const id of cids) { try { const c = await eda.pcb_PrimitiveComponent.get(id);
  out.components.push({id, designator: c.getState_Designator(),
    x: c.getState_X(), y: c.getState_Y(), rotation: c.getState_Rotation()});
} catch(e) { out.components.push({id, err: String(e).slice(0,80)}); } }
const plyid = await eda.pcb_PrimitivePolyline.getAllPrimitiveId();
for (const id of plyid) { try { const p = await eda.pcb_PrimitivePolyline.get(id);
  out.polylines.push({id, layer: p.layer, net: p.net, lineWidth: p.lineWidth, polygon: p.polygon});
} catch(e) { out.polylines.push({id, err: String(e).slice(0,80)}); } }
out.counts = {lines: out.lines.length, vias: out.vias.length, pads: out.pads.length,
  pours: out.pours.length, poured: out.poured.length,
  components: out.components.length, polylines: out.polylines.length};
return out;
"""


def main():
    a = invoke(ACTIVATE, timeout_ms=120000)
    print("activate:", json.dumps(a.get("value") or a.get("error"), ensure_ascii=False)[:300])

    reads = []
    for i in range(3):
        r = invoke(PROBE, timeout_ms=180000)
        v = r.get("value") or {}
        reads.append(v)
        print(f"probe#{i+1}: {json.dumps(v, ensure_ascii=False)}")
        if i < 2:
            time.sleep(9)
    consistent = len({json.dumps(x, sort_keys=True) for x in reads}) == 1
    print("3次读数一致:", consistent)

    r = invoke(FULL, timeout_ms=590000)
    if not r.get("ok"):
        print("full dump 失败:", json.dumps(r, ensure_ascii=False)[:600])
        sys.exit(1)
    v = r["value"]
    bad = [x for x in v["lines"] if x.get("err") or None in (x.get("sx"), x.get("sy"), x.get("ex"), x.get("ey"), x.get("w"))]
    badv = [x for x in v["vias"] if x.get("err") or None in (x.get("x"), x.get("y"))]
    badp = [x for x in v["pads"] if x.get("err") or None in (x.get("x"), x.get("y"))]
    if bad or badv or badp:
        print(f"!! 几何字段缺失 {len(bad)}线/{len(badv)}孔/{len(badp)}焊盘（pitfalls #31）")
        print("   样本:", json.dumps((bad or badv or badp)[:2], ensure_ascii=False)[:600])
    v["probe_reads"] = reads
    v["probe_consistent"] = consistent
    p = save_json("_s5_snapshot_pre_pour.json", v)
    print("快照落盘:", p, "counts=", json.dumps(v["counts"]))
    print("pad 样本:", json.dumps(v["pads"][:3], ensure_ascii=False))


if __name__ == "__main__":
    main()
