#!/usr/bin/env python3
"""s4b_state.py — S4B 画布全量状态 dump（线/孔/Region/网长，字段校验 pitfalls #31）。

用法: python s4b_state.py [--out reports/_s4b_state.json]
输出格式兼容 s4_analyze.py 的 --audit（k:L/V 结构，canvas mil）。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import ensure_session, run_code  # noqa: E402

AUDIT_JS = r"""
const out = {lines: [], vias: [], regions: []};
const lids = await eda.pcb_PrimitiveLine.getAllPrimitiveId();
for (const id of lids) {
  try { const l = await eda.pcb_PrimitiveLine.get(id);
        out.lines.push({id, net: l.getState_Net(), layer: l.getState_Layer(),
                        sx: l.getState_StartX(), sy: l.getState_StartY(),
                        ex: l.getState_EndX(), ey: l.getState_EndY(),
                        w: l.getState_LineWidth()}); } catch (e) {
          out.lines.push({id, err: String(e).slice(0,80)}); }
}
const vids = await eda.pcb_PrimitiveVia.getAllPrimitiveId();
for (const id of vids) {
  try { const v = await eda.pcb_PrimitiveVia.get(id);
        out.vias.push({id, net: v.getState_Net(), x: v.getState_X(), y: v.getState_Y(),
                        hole: v.getState_HoleDiameter(), dia: v.getState_Diameter()}); }
  catch (e) { out.vias.push({id, err: String(e).slice(0,80)}); }
}
const rids = await eda.pcb_PrimitiveRegion.getAllPrimitiveId();
for (const id of rids) {
  try { const r = await eda.pcb_PrimitiveRegion.get(id);
        let nm = undefined, lr = undefined;
        try { nm = r.getState_RegionName(); } catch (e) {}
        try { lr = r.getState_Layer(); } catch (e) {}
        out.regions.push({id, layer: lr, name: nm}); } catch (e) {}
}
const nets = await eda.pcb_Net.getAllNets();
out.netLen = {};
for (const n of nets) out.netLen[n.net] = n.length || 0;
out.lineCount = out.lines.length;
out.viaCount = out.vias.length;
out.regionCount = out.regions.length;
return out;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/_s4b_state.json")
    args = ap.parse_args()
    sid = ensure_session()
    r = run_code(AUDIT_JS, sid, timeout_ms=300000)
    if not r.get("ok"):
        print("audit 失败:", json.dumps(r)[:400]); sys.exit(1)
    v = r["value"]
    # 几何字段校验（ pitfalls #31：缺字段=空壳记录，直接失败）
    bad = [x for x in v["lines"] if None in (x.get("sx"), x.get("sy"), x.get("ex"), x.get("ey"), x.get("w")) or x.get("err")]
    badv = [x for x in v["vias"] if x.get("err") or None in (x.get("x"), x.get("y"))]
    if bad or badv:
        print(f"!! 几何字段缺失: {len(bad)} 线 / {len(badv)} 孔（pitfalls #31）")
        print(json.dumps((bad + badv)[:5], ensure_ascii=False))
        sys.exit(2)
    # K=V 兼容格式（s4_analyze.py --audit）
    kv = ([{"k": "L", **x} for x in v["lines"]] +
          [{"k": "V", **x} for x in v["vias"]])
    here = Path("E:/agent project/projects/flexiheal-v1")
    (here / args.out).write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding="utf-8")
    (here / (args.out.replace(".json", "_kv.json"))).write_text(json.dumps(kv, ensure_ascii=False, indent=1), encoding="utf-8")
    import collections
    pernet = collections.Counter(x["net"] for x in v["lines"])
    print(f"lines={v['lineCount']} vias={v['viaCount']} regions={v['regionCount']}; "
          f"pernet={dict(pernet)}")
    npad = {k: round(x / 39.37, 2) for k, x in v["netLen"].items() if x}
    print(f"netLen>0: {npad}")
    sys.exit(0)


if __name__ == "__main__":
    main()
