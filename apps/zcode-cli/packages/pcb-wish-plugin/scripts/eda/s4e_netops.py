#!/usr/bin/env python3
"""s4e_netops.py — S4E 逐网图元操作（拆净/还原/量测）。

纪律：
- 删除只针对指定 net 的 lines+vias；GND 永不动。
- delete 必须传数组（pitfalls #23）。
- 任何删除前 T1 快照 _s4e_snapshot_pre_auto.json 必须已存在（脚本硬校验）。
- 还原以 T1 快照几何为准（save 后 id 全变，pitfalls #20 → 按几何判定）。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s4e_lib import invoke, save_json, load_json, HERE, corner_histogram  # noqa: E402

SNAP = "_s4e_snapshot_pre_auto.json"

DUMP_NET_JS = r"""
const target = __CLI__.args.net;
const out = {lines: [], vias: []};
const lids = await eda.pcb_PrimitiveLine.getAllPrimitiveId();
for (const id of lids) {
  try { const l = await eda.pcb_PrimitiveLine.get(id);
    if (l.getState_Net() === target) out.lines.push({id, net: l.getState_Net(),
      layer: l.getState_Layer(), sx: l.getState_StartX(), sy: l.getState_StartY(),
      ex: l.getState_EndX(), ey: l.getState_EndY(), w: l.getState_LineWidth()});
  } catch (e) {}
}
const vids = await eda.pcb_PrimitiveVia.getAllPrimitiveId();
for (const id of vids) {
  try { const v = await eda.pcb_PrimitiveVia.get(id);
    if (v.getState_Net() === target) out.vias.push({id, net: v.getState_Net(),
      x: v.getState_X(), y: v.getState_Y(), hole: v.getState_HoleDiameter(),
      dia: v.getState_Diameter()});
  } catch (e) {}
}
const nets = await eda.pcb_Net.getAllNets();
out.netLen = {}; for (const n of nets) out.netLen[n.net] = n.length || 0;
out.counts = {lines: out.lines.length, vias: out.vias.length};
return out;
"""

COUNT_JS = r"""
const out = {};
out.lines = (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length;
out.vias = (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length;
out.regions = (await eda.pcb_PrimitiveRegion.getAllPrimitiveId()).length;
out.comps = (await eda.pcb_PrimitiveComponent.getAllPrimitiveId()).length;
const nets = await eda.pcb_Net.getAllNets();
out.netLen = {}; for (const n of nets) out.netLen[n.net] = n.length || 0;
return out;
"""

DELETE_NET_JS = r"""
const target = __CLI__.args.net;
const lids = await eda.pcb_PrimitiveLine.getAllPrimitiveId();
const dl = [];
for (const id of lids) {
  try { const l = await eda.pcb_PrimitiveLine.get(id);
    if (l.getState_Net() === target) dl.push(id); } catch (e) {}
}
const vids = await eda.pcb_PrimitiveVia.getAllPrimitiveId();
const dv = [];
for (const id of vids) {
  try { const v = await eda.pcb_PrimitiveVia.get(id);
    if (v.getState_Net() === target) dv.push(id); } catch (e) {}
}
let rl = null, rv = null;
if (dl.length) { try { rl = await eda.pcb_PrimitiveLine.delete(dl); } catch(e) { rl = 'ERR:'+String(e).slice(0,100); } }
if (dv.length) { try { rv = await eda.pcb_PrimitiveVia.delete(dv); } catch(e) { rv = 'ERR:'+String(e).slice(0,100); } }
await eda.pcb_Document.save();
const cl = (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length;
const cv = (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length;
return {deletedLines: dl.length, deletedVias: dv.length, rl: rl, rv: rv,
        afterLines: cl, afterVias: cv};
"""

RESTORE_JS = r"""
const items = __CLI__.args.items;
let okl = 0, badl = 0, okv = 0, badv = 0;
for (const it of items.lines) {
  try { const l = await eda.pcb_PrimitiveLine.create(it.net, it.layer,
      it.sx, it.sy, it.ex, it.ey, it.w, false); if (l) okl++; else badl++;
  } catch (e) { badl++; }
}
for (const it of items.vias) {
  try { const v = await eda.pcb_PrimitiveVia.create(it.net, it.x, it.y, it.hole, it.dia);
    if (v) okv++; else badv++;
  } catch (e) { badv++; }
}
await eda.pcb_Document.save();
return {okLines: okl, badLines: badl, okVias: okv, badVias: badv};
"""


def need_snapshot():
    p = HERE / "reports" / SNAP
    if not p.exists():
        print(f"!! 缺回滚快照 {p} —— 禁止任何删除")
        sys.exit(2)
    return p


def cmd_dump(net, out=None):
    r = invoke(DUMP_NET_JS, timeout_ms=300000) if False else None
    from lceda_invoke import run_code
    r = run_code(DUMP_NET_JS, "44e574cc-dc99-46dd-80b8-60632f5b075a",
                 timeout_ms=300000, args_json=json.dumps({"net": net}))
    if not r.get("ok"):
        print("dump 失败:", json.dumps(r, ensure_ascii=False)[:400]); sys.exit(1)
    v = r["value"]
    print(json.dumps({k: v[k] for k in ("counts",)}, ensure_ascii=False))
    if out:
        save_json(out, v)
        print("落盘:", out)
    return v


def cmd_delete(net):
    need_snapshot()
    from lceda_invoke import run_code
    pre = cmd_dump(net)
    r = run_code(DELETE_NET_JS, "44e574cc-dc99-46dd-80b8-60632f5b075a",
                 timeout_ms=300000, args_json=json.dumps({"net": net}))
    print("delete 返回:", json.dumps(r.get("value") or r.get("error"), ensure_ascii=False)[:300])
    time.sleep(10)
    post = cmd_dump(net)
    print(f"拆净核验: {net} 前 {pre['counts']} → 后 {post['counts']}")
    return pre, post


def cmd_restore(net):
    need_snapshot()
    snap = load_json(SNAP)
    items = {"lines": [l for l in snap["lines"] if l.get("net") == net],
             "vias": [v for v in snap["vias"] if v.get("net") == net]}
    from lceda_invoke import run_code
    r = run_code(RESTORE_JS, "44e574cc-dc99-46dd-80b8-60632f5b075a",
                 timeout_ms=300000, args_json=json.dumps({"items": items}, ensure_ascii=False))
    print("restore:", json.dumps(r.get("value") or r.get("error"), ensure_ascii=False)[:300])
    time.sleep(10)
    post = cmd_dump(net)
    print(f"还原核验: {net} → {post['counts']}")
    return post


def cmd_measure(net):
    v = cmd_dump(net)
    hist, corners = corner_histogram(v["lines"])
    print(f"量测 {net}: segs={len(v['lines'])} vias={len(v['vias'])} "
          f"netLen={v['netLen'].get(net, 0):.0f}")
    print("  拐角:", json.dumps(hist["by_class"], ensure_ascii=False),
          f"ratio90={hist['ratio90']:.3f} ratio45={hist['ratio45']:.3f}")
    w = {}
    for l in v["lines"]:
        w[l["w"]] = w.get(l["w"], 0) + 1
    ly = {}
    for l in v["lines"]:
        ly[l["layer"]] = ly.get(l["layer"], 0) + 1
    print("  线宽:", w, " 层:", ly)
    return v, hist, corners


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump")
    ap.add_argument("--delete")
    ap.add_argument("--restore")
    ap.add_argument("--measure")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.dump:
        cmd_dump(a.dump, a.out)
    if a.measure:
        cmd_measure(a.measure)
    if a.delete:
        cmd_delete(a.delete)
    if a.restore:
        cmd_restore(a.restore)
