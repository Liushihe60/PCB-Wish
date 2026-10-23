#!/usr/bin/env python3
"""s5_exec.py — S5 画布执行器：删除 / 创建走线与过孔（严格 delete+recreate 口径）。

坐标：计划文件用**任务系 mm**；本脚本换算成画布 mil 传给 EDA。

用法:
  python s5_exec.py --create reports/_s5_exec_items.json [--apply]
  python s5_exec.py --delete reports/_s5_exec_deletes.json [--apply]
  python s5_exec.py --save
删除按"重新枚举 + 几何/网络匹配"定位（id 每次 save 后会变，pitfalls #20/#42）。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s5_lib import invoke, save_json, load_json, HERE  # noqa: E402

MIL = 39.3700787402

ACTIVATE = r"""
const tree = await eda.dmt_EditorControl.getSplitScreenTree();
const tabs = tree.tabs || [];
const pcb = tabs.find(t => t.tabId && t.tabId.indexOf('1b70bcd67c184b71@') === 0);
if (!pcb) return JSON.stringify({err: 'PCB_TAB_NOT_FOUND'});
await eda.dmt_EditorControl.activateDocument(pcb.tabId);
return JSON.stringify({ok: 1, tab: pcb.tabId});
"""

ENUM = r"""
const out = {lines: [], vias: []};
for (const id of await eda.pcb_PrimitiveLine.getAllPrimitiveId()) {
  const l = await eda.pcb_PrimitiveLine.get(id);
  out.lines.push({id, net: l.getState_Net(), layer: l.getState_Layer(),
    sx: l.getState_StartX(), sy: l.getState_StartY(),
    ex: l.getState_EndX(), ey: l.getState_EndY(), w: l.getState_LineWidth()});
}
for (const id of await eda.pcb_PrimitiveVia.getAllPrimitiveId()) {
  const v = await eda.pcb_PrimitiveVia.get(id);
  out.vias.push({id, net: v.getState_Net(), x: v.getState_X(), y: v.getState_Y(),
    hole: v.getState_HoleDiameter(), dia: v.getState_Diameter()});
}
out.counts = {lines: out.lines.length, vias: out.vias.length,
  comps: (await eda.pcb_PrimitiveComponent.getAllPrimitiveId()).length,
  pads: (await eda.pcb_PrimitivePad.getAllPrimitiveId()).length,
  pours: (await eda.pcb_PrimitivePour.getAllPrimitiveId()).length,
  poured: (await eda.pcb_PrimitivePoured.getAllPrimitiveId()).length};
return out;
"""

DELETE = r"""
const a = __CLI__.args;
const out = {deleted_lines: [], deleted_vias: [], errs: []};
if (a.lineIds && a.lineIds.length) {
  const r = await eda.pcb_PrimitiveLine.delete(a.lineIds);
  out.deleted_lines = Array.isArray(r) ? r : [r];
}
if (a.viaIds && a.viaIds.length) {
  const r = await eda.pcb_PrimitiveVia.delete(a.viaIds);
  out.deleted_vias = Array.isArray(r) ? r : [r];
}
return out;
"""

CREATE = r"""
const a = __CLI__.args;
const out = {lines: [], vias: [], errs: []};
for (const it of a.items) {
  try {
    if (it.kind === 'line') {
      const ln = await eda.pcb_PrimitiveLine.create(it.net, it.layer,
        it.sx, it.sy, it.ex, it.ey, it.w, false);
      if (!ln) { out.errs.push({name: it.name, err: 'line null'}); continue; }
      out.lines.push({name: it.name, id: ln.getState_PrimitiveId(), net: it.net,
        layer: it.layer, sx: it.sx, sy: it.sy, ex: it.ex, ey: it.ey, w: it.w});
    } else if (it.kind === 'via') {
      const v = await eda.pcb_PrimitiveVia.create(it.net, it.x, it.y, it.hole, it.dia);
      if (!v) { out.errs.push({name: it.name, err: 'via null'}); continue; }
      out.vias.push({name: it.name, id: v.getState_PrimitiveId(), net: it.net,
        x: it.x, y: it.y, hole: it.hole, dia: it.dia});
    }
  } catch (e) { out.errs.push({name: it.name, err: String(e).slice(0, 250)}); }
}
return out;
"""

SAVE = r"""
const r = await eda.pcb_Document.save();
return JSON.stringify({save: typeof r === 'undefined' ? 'void' : r});
"""


def mm2mil(v):
    return v * MIL


def task2canvas(x, y):
    return (mm2mil(x), (30.0 - y) * MIL)


def enumerate_now():
    r = invoke(ENUM, timeout_ms=590000)
    if not r.get("ok"):
        raise SystemExit("枚举失败: " + json.dumps(r, ensure_ascii=False)[:400])
    return r["value"]


def match_id(prim, item, tol=0.05):
    """按 net + 端点几何匹配 primitiveId（mil 坐标）"""
    best, bestd = None, 1e9
    for p in prim:
        if (p.get("net") or "") != (item.get("net") or ""):
            continue
        d1 = abs(p["sx"] - item["sx"]) + abs(p["sy"] - item["sy"])
        d2 = abs(p["ex"] - item["ex"]) + abs(p["ey"] - item["ey"])
        d = min(d1 + d2, abs(p["sx"] - item["ex"]) + abs(p["sy"] - item["ey"]) +
                abs(p["ex"] - item["sx"]) + abs(p["ey"] - item["sy"]))
        if d < bestd:
            best, bestd = p["id"], d
    if bestd <= tol * 2:
        return best
    return None


def do_delete(items):
    """items: [{kind:'line'|'via', net, sx,sy,ex,ey | x,y}] 任务系 mm"""
    invoke(ACTIVATE, timeout_ms=120000)
    snap = enumerate_now()
    lineIds, viaIds, unmatched = [], [], []
    for it in items:
        if it["kind"] == "line":
            sx, sy = task2canvas(it["sx"], it["sy"])
            ex, ey = task2canvas(it["ex"], it["ey"])
            iid = match_id(snap["lines"], {"net": it["net"], "sx": sx, "sy": sy, "ex": ex, "ey": ey})
            if iid:
                lineIds.append(iid)
            else:
                unmatched.append(it)
        else:
            x, y = task2canvas(it["x"], it["y"])
            iid = match_id(snap["vias"], {"net": it["net"], "sx": x, "sy": y, "ex": x, "ey": y})
            if iid:
                viaIds.append(iid)
            else:
                unmatched.append(it)
    print(f"待删: 线 {len(lineIds)} 孔 {len(viaIds)} 未匹配 {len(unmatched)}")
    for u in unmatched:
        print("   未匹配:", json.dumps(u, ensure_ascii=False))
    if not lineIds and not viaIds:
        return {"deleted": [], "unmatched": unmatched}
    r = invoke(DELETE, timeout_ms=590000, args={"lineIds": lineIds, "viaIds": viaIds})
    return {"lineIds": lineIds, "viaIds": viaIds, "raw": r, "unmatched": unmatched}


def do_create(items):
    """items: [{kind:'line'|'via', net, layer, sx,sy,ex,ey,w | x,y,hole,dia}] 任务系 mm"""
    invoke(ACTIVATE, timeout_ms=120000)
    out_items = []
    for it in items:
        o = dict(it)
        if it["kind"] == "line":
            o["sx"], o["sy"] = task2canvas(it["sx"], it["sy"])
            o["ex"], o["ey"] = task2canvas(it["ex"], it["ey"])
            o["w"] = mm2mil(it["w"])
        else:
            o["x"], o["y"] = task2canvas(it["x"], it["y"])
            o["hole"] = mm2mil(it["hole"])
            o["dia"] = mm2mil(it["dia"])
        out_items.append(o)
    r = invoke(CREATE, timeout_ms=590000, args={"items": out_items})
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", default=None)
    ap.add_argument("--delete", default=None)
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.save:
        r = invoke(SAVE, timeout_ms=590000)
        print("save:", json.dumps(r, ensure_ascii=False)[:300])
        return
    if a.delete:
        items = load_json(a.delete)
        items = items["items"] if isinstance(items, dict) else items
        if not a.apply:
            print("DRY-RUN delete", len(items), "项")
            for i in items:
                print("  ", json.dumps(i, ensure_ascii=False))
            return
        res = do_delete(items)
        print(json.dumps(res, ensure_ascii=False)[:1500])
        if a.out:
            save_json(a.out, res)
        return
    if a.create:
        items = load_json(a.create)
        items = items["items"] if isinstance(items, dict) else items
        if not a.apply:
            print("DRY-RUN create", len(items), "项")
            for i in items:
                print("  ", json.dumps(i, ensure_ascii=False))
            return
        r = do_create(items)
        print(json.dumps(r, ensure_ascii=False)[:2000])
        if a.out:
            save_json(a.out, r)
        return
    print("指定 --create/--delete/--save")


if __name__ == "__main__":
    main()
