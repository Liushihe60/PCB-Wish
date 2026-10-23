#!/usr/bin/env python3
"""s4_exec.py — S4 布线重走站执行器（D021 流水线第四站）。

阶段（--phase）:
  audit    走线/过孔/Region/组件全量 dump → reports/_s4_tracks_vias_<tag>.json（canvas mil）
  netlen   pcb_Net.getAllNets → reports/_s4_netlen_raw.json（EDA 长度口径）
  save     pcb_Document.save + 等 10s
  drc      pcb_Drc.check(true,false,true) → reports/_s4_drc_raw.json
  create   创建走线/过孔（--items <json>，含创建前几何自检结果透传）→ reports/_s4_create_<tag>.json
  probe    轻量计数（lines/vias/regions/comps）

纪律（D009/D017/pitfalls #11/#14/#20/#26/#27）：批量单 invoke；查询前等 5-10s；save 后等
8-10s；返回值一律独立回读；返回值丢失勿重放（以重枚举为准）。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import ensure_session, run_code  # noqa: E402

PROJ = Path("E:/agent project/projects/flexiheal-v1")
RD = PROJ / "reports"

AUDIT_JS = r"""
const out = {lines: [], vias: [], regions: []};
const lids = await eda.pcb_PrimitiveLine.getAllPrimitiveId();
for (const id of lids) {
  try { const l = await eda.pcb_PrimitiveLine.get(id);
        out.lines.push({id, net: l.getState_Net(), layer: l.getState_Layer(),
                         sx: l.getState_StartX(), sy: l.getState_StartY(),
                         ex: l.getState_EndX(), ey: l.getState_EndY(), w: l.getState_LineWidth()}); } catch (e) {}
}
const vids = await eda.pcb_PrimitiveVia.getAllPrimitiveId();
for (const id of vids) {
  try { const v = await eda.pcb_PrimitiveVia.get(id);
        out.vias.push({id, net: v.getState_Net(), x: v.getState_X(), y: v.getState_Y(),
                        hole: v.getState_HoleDiameter(), dia: v.getState_Diameter()}); } catch (e) {}
}
const rids = await eda.pcb_PrimitiveRegion.getAllPrimitiveId();
for (const id of rids) {
  try { const r = await eda.pcb_PrimitiveRegion.get(id);
        out.regions.push({id, layer: r.getState_Layer?.(), rule: r.getState_RuleType?.(),
                           name: r.getState_RegionName?.()}); } catch (e) {}
}
out.lineCount = out.lines.length;
out.viaCount = out.vias.length;
const cids = await eda.pcb_PrimitiveComponent.getAllPrimitiveId();
out.compCount = cids.length;
return out;
"""

NETLEN_JS = r"""
const nets = await eda.pcb_Net.getAllNets();
return nets.map(n => ({net: n.net, length: n.length || 0}));
"""

SAVE_JS = "await eda.pcb_Document.save(); return 'saved'"

DRC_JS = r"""
const d = await eda.pcb_Drc.check(true, false, true);
return JSON.parse(JSON.stringify(d));
"""

CREATE_JS = r"""
const items = __CLI__.args.items;
const out = [];
for (const it of items) {
  try {
    if (it.kind === 'via') {
      const v = await eda.pcb_PrimitiveVia.create(it.net, it.x, it.y, it.hole, it.dia);
      out.push({net: it.net, kind: 'via', ok: !!v, tag: it.tag || '',
                back: v ? {net: v.getState_Net(), x: v.getState_X(), y: v.getState_Y(),
                            hole: v.getState_HoleDiameter(), dia: v.getState_Diameter()} : null});
    } else {
      const l = await eda.pcb_PrimitiveLine.create(it.net, it.layer, it.x1, it.y1, it.x2, it.y2, it.w, false);
      out.push({net: it.net, kind: 'line', ok: !!l, tag: it.tag || '',
                back: l ? {net: l.getState_Net(), layer: l.getState_Layer(),
                            sx: l.getState_StartX(), sy: l.getState_StartY(),
                            ex: l.getState_EndX(), ey: l.getState_EndY(),
                            w: l.getState_LineWidth()} : null});
    }
  } catch (e) { out.push({net: it.net, kind: it.kind, ok: false, tag: it.tag || '', err: String(e).slice(0, 120)}); }
}
return out;
"""


def emit(name, obj):
    p = RD / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"# 落盘 {p}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLEA001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True,
                    choices=["audit", "netlen", "save", "drc", "create", "probe"])
    ap.add_argument("--tag", default="in")
    ap.add_argument("--items", default=None, help="create 阶段 items JSON 路径")
    ap.add_argument("--session", default=None)
    args = ap.parse_args()
    sid = ensure_session(args.session)

    if args.phase == "probe":
        r = run_code(
            "const l=await eda.pcb_PrimitiveLine.getAllPrimitiveId();"
            "const v=await eda.pcb_PrimitiveVia.getAllPrimitiveId();"
            "const g=await eda.pcb_PrimitiveRegion.getAllPrimitiveId();"
            "const c=await eda.pcb_PrimitiveComponent.getAllPrimitiveId();"
            "return {lines:l.length,vias:v.length,regions:g.length,comps:c.length};",
            sid, timeout_ms=120000)
        print(json.dumps(r, ensure_ascii=False))
        sys.exit(0 if r.get("ok") else 1)

    if args.phase == "audit":
        r = run_code(AUDIT_JS, sid, timeout_ms=300000)
        if not r.get("ok"):
            print(json.dumps(r, ensure_ascii=False)[:300]); sys.exit(1)
        emit(f"_s4_tracks_vias_{args.tag}.json", r["value"])
        v = r["value"]
        print(f"lines={v['lineCount']} vias={v['viaCount']} regions={len(v['regions'])} comps={v['compCount']}")
        sys.exit(0)

    if args.phase == "netlen":
        r = run_code(NETLEN_JS, sid, timeout_ms=240000)
        if not r.get("ok"):
            print(json.dumps(r, ensure_ascii=False)[:300]); sys.exit(1)
        emit("_s4_netlen_raw.json", {"checkedAt": time.strftime("%F %T"), "nets": r["value"]})
        zero = [n["net"] for n in r["value"] if not n["length"]]
        print(f"nets={len(r['value'])} zeroLength={len(zero)}: {zero}")
        sys.exit(0)

    if args.phase == "save":
        s = run_code(SAVE_JS, sid, timeout_ms=120000)
        print(f"save: {json.dumps(s, ensure_ascii=False)[:200]}，等 10s", flush=True)
        time.sleep(10)
        sys.exit(0 if s.get("ok") else 1)

    if args.phase == "drc":
        d = run_code(DRC_JS, sid, timeout_ms=600000)
        if not d.get("ok"):
            print(json.dumps(d, ensure_ascii=False)[:400]); sys.exit(1)
        details = d["value"]
        if not isinstance(details, list):
            details = [details]
        emit("_s4_drc_raw.json", {"checkedAt": time.strftime("%F %T"), "count": len(details),
                                  "violations": details})
        print(f"DRC 违例 {len(details)} 条")
        sys.exit(0)

    if args.phase == "create":
        items = json.loads(Path(args.items).read_text(encoding="utf-8"))
        r = run_code(CREATE_JS, sid, timeout_ms=600000,
                     args_json=json.dumps({"items": items}, ensure_ascii=False))
        if not r.get("ok"):
            print(json.dumps(r, ensure_ascii=False)[:400]); sys.exit(1)
        res = r["value"]
        ok = [x for x in res if x.get("ok")]
        bad = [x for x in res if not x.get("ok")]
        # 回读不符：net 不等或几何偏差 >1mil
        mism = []
        for x, it in zip(res, items):
            b = x.get("back")
            if not b:
                continue
            if b.get("net") != it.get("net"):
                mism.append({"tag": it.get("tag"), "why": "net", "back": b})
            elif it["kind"] == "line":
                lg = (abs(b["sx"] - it["x1"]) > 1.01 or abs(b["sy"] - it["y1"]) > 1.01
                      or abs(b["ex"] - it["x2"]) > 1.01 or abs(b["ey"] - it["y2"]) > 1.01)
                if lg:
                    mism.append({"tag": it.get("tag"), "why": "geom", "back": b})
            else:
                if abs(b["x"] - it["x"]) > 1.01 or abs(b["y"] - it["y"]) > 1.01:
                    mism.append({"tag": it.get("tag"), "why": "geom", "back": b})
        emit(f"_s4_create_{args.tag}.json", {"ok": len(ok), "failed": bad, "mismatch": mism, "detail": res})
        print(f"create[{args.tag}]: {len(ok)}/{len(items)} ok, failed={len(bad)}, mismatch={len(mism)}")
        for x in bad[:10]:
            print(f"  FAIL {json.dumps(x, ensure_ascii=False)[:160]}")
        for x in mism[:10]:
            print(f"  MISM {json.dumps(x, ensure_ascii=False)[:160]}")
        sys.exit(0 if not bad and not mism else 1)


if __name__ == "__main__":
    main()
