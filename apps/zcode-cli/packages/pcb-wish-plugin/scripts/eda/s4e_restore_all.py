#!/usr/bin/env python3
"""s4e_restore_all.py — 用全量 dump 快照把非 GND 走线/过孔整体还原（回滚用）。

用法: python s4e_restore_all.py --dump _s4e_tracks_vias_final.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s4e_lib import invoke, save_json, load_json, HERE  # noqa: E402
from lceda_invoke import run_code  # noqa: E402

SESSION = "44e574cc-dc99-46dd-80b8-60632f5b075a"

RESTORE_JS = r"""
const segs = __CLI__.args.segs;
const vias = __CLI__.args.vias;
let ok = 0, bad = 0, errs = [];
for (const s of segs) {
  try { const l = await eda.pcb_PrimitiveLine.create(s.net, s.layer, s.sx, s.sy, s.ex, s.ey, s.w, false);
        if (l) ok++; else bad++; }
  catch (e) { bad++; errs.push(String(e).slice(0,80)); }
}
for (const v of vias) {
  try { const o = await eda.pcb_PrimitiveVia.create(v.net, v.x, v.y, v.hole, v.dia);
        if (o) ok++; else bad++; }
  catch (e) { bad++; errs.push(String(e).slice(0,80)); }
}
await eda.pcb_Document.save();
return {ok, bad, errs: errs.slice(0,5),
        lines: (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length,
        vias: (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length};
"""

DELETE_JS = r"""
const lids = await eda.pcb_PrimitiveLine.getAllPrimitiveId();
const dl = [];
for (const id of lids) { try { const l = await eda.pcb_PrimitiveLine.get(id);
  if (l.getState_Net() !== 'GND') dl.push(id); } catch(e){} }
const vids = await eda.pcb_PrimitiveVia.getAllPrimitiveId();
const dv = [];
for (const id of vids) { try { const v = await eda.pcb_PrimitiveVia.get(id);
  if (v.getState_Net() !== 'GND') dv.push(id); } catch(e){} }
if (dl.length) await eda.pcb_PrimitiveLine.delete(dl);
if (dv.length) await eda.pcb_PrimitiveVia.delete(dv);
await eda.pcb_Document.save();
return {deletedLines: dl.length, deletedVias: dv.length,
        lines: (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length,
        vias: (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length};
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="_s4e_tracks_vias_final.json")
    a = ap.parse_args()
    d = load_json(a.dump)
    segs = [{"net": l["net"], "layer": l["layer"], "sx": l["sx"], "sy": l["sy"],
             "ex": l["ex"], "ey": l["ey"], "w": l["w"]}
            for l in d["lines"] if not l.get("err") and l.get("net") != "GND"]
    vias = [{"net": v["net"], "x": v["x"], "y": v["y"], "hole": v["hole"], "dia": v["dia"]}
            for v in d["vias"] if not v.get("err") and v.get("net") != "GND"]
    print(f"快照还原: 线 {len(segs)} 孔 {len(vias)}")
    r = run_code(DELETE_JS, SESSION, timeout_ms=590000)
    print("delete:", json.dumps(r.get("value") or r.get("error"), ensure_ascii=False)[:200])
    time.sleep(8)
    # 分块下发（命令行长度上限，WinError 206）
    CH = 80
    tot = {"ok": 0, "bad": 0}
    for i in range(0, len(segs), CH):
        r = run_code(RESTORE_JS, SESSION, timeout_ms=590000,
                     args_json=json.dumps({"segs": segs[i:i + CH], "vias": []}, ensure_ascii=False))
        v = r.get("value") or {}
        tot["ok"] += v.get("ok", 0); tot["bad"] += v.get("bad", 0)
    for i in range(0, len(vias), CH):
        r = run_code(RESTORE_JS, SESSION, timeout_ms=590000,
                     args_json=json.dumps({"segs": [], "vias": vias[i:i + CH]}, ensure_ascii=False))
        v = r.get("value") or {}
        tot["ok"] += v.get("ok", 0); tot["bad"] += v.get("bad", 0)
    print("create:", json.dumps(tot, ensure_ascii=False))
    time.sleep(10)


if __name__ == "__main__":
    main()
