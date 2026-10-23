#!/usr/bin/env python3
"""s4e_restore_net.py — 从全量 dump 快照还原指定网的走线/过孔（回滚/兜底用）。

用法: python s4e_restore_net.py --net VDD [--dump _s4e_tracks_vias_final.json]
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
CREATE_JS = r"""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", required=True)
    ap.add_argument("--dump", default="_s4e_tracks_vias_final.json")
    a = ap.parse_args()
    d = load_json(a.dump)
    segs = [{"net": l["net"], "layer": l["layer"], "sx": l["sx"], "sy": l["sy"],
             "ex": l["ex"], "ey": l["ey"], "w": l["w"]}
            for l in d["lines"] if l.get("net") == a.net and not l.get("err")]
    vias = [{"net": v["net"], "x": v["x"], "y": v["y"], "hole": v["hole"], "dia": v["dia"]}
            for v in d["vias"] if v.get("net") == a.net and not v.get("err")]
    print(f"还原 {a.net}: 线 {len(segs)} 孔 {len(vias)}")
    r = run_code(CREATE_JS, SESSION, timeout_ms=590000,
                 args_json=json.dumps({"segs": segs, "vias": vias}, ensure_ascii=False))
    print("create:", json.dumps(r.get("value") or r.get("error"), ensure_ascii=False)[:300])
    time.sleep(10)


if __name__ == "__main__":
    main()
