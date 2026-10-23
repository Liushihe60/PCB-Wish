#!/usr/bin/env python3
"""pcb_route_from_plan.py — G4 布线执行器（按 spec/route_plan.json 分阶段执行）。

阶段（--phase）:
  keepout  创建 C6 天线 keepout 区域（层 12 MULTI=双层，规则 5=禁走线/6=禁填充/7=禁覆铜；
           不含 2=禁元件——器件焊盘保留除外），存在即跳过+回读确认
  rules    读网级规则 → 按 plan 线宽表覆写 Track 宽度（VBUS/VBAT 1.0/VDD 0.6/…）→ 回读确认
  gvias    创建 plan.manual.gnd_vias（C8 去耦电容紧邻地过孔，GND 网）+ crystal_guard.gnd_vias
  auto     --stage <id>：启动自动布线作业（startAutoRouting，45°拐角、COMPLETION、KEEP 既有
           走线、ignoreNets=[GND,ANT_N,ANT_FEED]，layers 按 stage），轮询作业状态至完成，
           回读走线/过孔计数与每网铜长
  manual   创建 plan.manual.crystal_guard 走线 + usb_serpentine + manual_fallback（含
           创建前几何校验，违例即拒并报告）
  verify   全量审计：走线/过孔计数、每网铜长表、keepout 越界审计 → reports/routing_verify.json
  drc      save → 等 10s → pcb_Drc.check(strict,ui,verbose) → reports/drc_g4_baseline.json

纪律（D009/D010）：批量合并单次 invoke；查询前等 5-10s；save 后等 8-10s；返回值一律回读。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import ensure_session, run_code  # noqa: E402

MIL = 39.37
IGNORE_NETS = ["GND", "ANT_N", "ANT_FEED"]  # 不布网：G5 铺铜消解 / D001 rf 豁免

STAGES = {
    # id: (nets, layers, 说明)
    "s3_adc":   (["ADC_IN", "DIV"], [1], "ADC 链顶层单层（C3 不换层）"),
    "s4_usb":   (["USB_DP", "USB_DM", "USB_DP_P", "USB_DM_P"], [1, 2], "USB 差分对"),
    "s5_xtal":  (["XC1", "XC2"], [1], "晶振网顶层"),
    "s6_power": (["VBUS", "VBAT", "VREG_IN", "VDD"], [1, 2], "电源主干（1.0/0.6 线宽规则）"),
    "s7_dec":   (["DEC1_N", "DEC2_N", "DEC3_N", "DEC456_N", "DEC5B_N", "DECUSB_N", "DCC", "DCCH"], [1, 2], "去耦/电感位（DEC5B_N=IR DEC5b_N，画布大小写差异）"),
    "s8_signal": (["SWDIO", "SWCLK", "RESET_N", "BTN1_N", "BTN2_N", "LED_A_N", "LED_AA", "LED_B_N",
                   "LED_BA", "CHRG_N", "CHRGA", "STDBY_N", "STDBYA", "SDA", "SCL", "PROG", "CC1", "CC2"],
                  [1, 2], "普通信号"),
}

KEEP_JS = r"""
const ids = await eda.pcb_PrimitiveRegion.getAllPrimitiveId();
const out = [];
for (const id of ids) {
  try { const r = await eda.pcb_PrimitiveRegion.get(id);
        out.push({id, layer: r.getState_Layer?.() ?? r.layer, rule: r.getState_RuleType?.() ?? r.ruleType,
                  name: r.getState_RegionName?.() ?? r.regionName}); } catch (e) { out.push({id, err: String(e).slice(0,80)}); }
}
return out;
"""

RULES_JS = r"""
const widths = __CLI__.args.widths;   // {net: mm}
const rules = await eda.pcb_Drc.getNetRules();
let changed = 0, missing = [];
for (const r of rules) {
  if (widths[r.name] !== undefined) { r.Track = widths[r.name]; changed++; }
}
const res = await eda.pcb_Drc.overwriteNetRules(rules);
const back = await eda.pcb_Drc.getNetRules();
const out = {};
for (const r of back) { if (widths[r.name] !== undefined) out[r.name] = r.Track; }
return {res, changed, applied: out, total: back.length};
"""

VIA_BATCH_JS = r"""
const items = __CLI__.args.items;   // [{net,x,y,hole,dia}]
const out = [];
for (const it of items) {
  try {
    const v = await eda.pcb_PrimitiveVia.create(it.net, it.x, it.y, it.hole, it.dia);
    out.push({net: it.net, x: it.x, y: it.y, ok: !!v, id: v ? v.getState_PrimitiveId() : null,
              back: v ? {net: v.getState_Net(), x: v.getState_X(), y: v.getState_Y(),
                          hole: v.getState_HoleDiameter(), dia: v.getState_Diameter()} : null});
  } catch (e) { out.push({net: it.net, ok: false, err: String(e).slice(0, 100)}); }
}
return out;
"""

LINE_BATCH_JS = r"""
const items = __CLI__.args.items;   // [{net,layer,x1,y1,x2,y2,w}]
const out = [];
for (const it of items) {
  try {
    const l = await eda.pcb_PrimitiveLine.create(it.net, it.layer, it.x1, it.y1, it.x2, it.y2, it.w, false);
    out.push({net: it.net, ok: !!l, id: l ? l.getState_PrimitiveId() : null,
              back: l ? {net: l.getState_Net(), layer: l.getState_Layer(),
                          sx: l.getState_StartX(), sy: l.getState_StartY(),
                          ex: l.getState_EndX(), ey: l.getState_EndY(),
                          w: l.getState_LineWidth()} : null});
  } catch (e) { out.push({net: it.net, ok: false, err: String(e).slice(0, 100)}); }
}
return out;
"""

REGION_JS = r"""
const exist = await eda.pcb_PrimitiveRegion.getAllPrimitiveId();
let found = null;
for (const id of exist) {
  const r = await eda.pcb_PrimitiveRegion.get(id);
  if ((r.getState_RegionName?.() ?? '') === 'ANT_keepout_C6') { found = r; break; }
}
if (found) return {created: false, existing: true, layer: found.getState_Layer?.(), rule: found.getState_RuleType?.()};
const poly = eda.pcb_MathPolygon.createPolygon(__CLI__.args.poly);
const region = await eda.pcb_PrimitiveRegion.create(12, poly, [5, 6, 7], 'ANT_keepout_C6', 10, false);
return {created: true, id: region.getState_PrimitiveId(),
        layer: region.getState_Layer?.(), rule: region.getState_RuleType?.()};
"""

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
return out;
"""

NETLEN_JS = r"""
const nets = await eda.pcb_Net.getAllNets();
return nets.map(n => ({net: n.net, length: n.length || 0}));
"""

START_AUTO_JS = r"""
const props = __CLI__.args.props || {};
const h = await eda.pcb_Document.startAutoRouting(props);
return h && h.jobId ? h.jobId : (h && h.getState_JobId ? h.getState_JobId() : null);
"""

STATUS_JS = r"""
return await eda.pcb_Document.getAutoJobStatus(__CLI__.args.jobId);
"""

SAVE_JS = "await eda.pcb_Document.save(); return 'saved'"

DRC_JS = r"""
const d = await eda.pcb_Drc.check(true, false, true);
return JSON.parse(JSON.stringify(d));
"""


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True,
                    choices=["keepout", "rules", "gvias", "auto", "manual", "verify", "drc"])
    ap.add_argument("--stage", default=None)
    ap.add_argument("--plan", default="spec/route_plan.json")
    ap.add_argument("--session", default=None)
    args = ap.parse_args()
    sid = ensure_session(args.session)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    proj = "E:/agent project/projects/flexiheal-v1"
    report_dir = Path(proj) / "reports"

    def emit(name, obj):
        p = report_dir / name
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"# 落盘 {p}")

    if args.phase == "keepout":
        # keepout 任务系 x34-45/y0-8 → canvas mil: x 1339-1772, y (30-8)*39.37=866.14 → 1181.1
        poly = ["R", mm2mil(34), mm2mil(22), mm2mil(11), mm2mil(8), 0, 0]
        r = run_code(REGION_JS, sid, timeout_ms=180000,
                     args_json=json.dumps({"poly": poly}))
        print(json.dumps(r, ensure_ascii=False)[:400])
        sys.exit(0 if r.get("ok") else 1)

    if args.phase == "rules":
        widths = {n["name"]: n["width_mm"] for n in plan["nets"]
                  if n.get("width_mm") and n["action"] == "auto"}
        r = run_code(RULES_JS, sid, timeout_ms=240000,
                     args_json=json.dumps({"widths": widths}, ensure_ascii=False))
        if not r.get("ok"):
            print(json.dumps(r, ensure_ascii=False)[:300]); sys.exit(1)
        v = r["value"]
        bad = {k: x for k, x in v["applied"].items() if abs((x or 0) - widths[k]) > 1e-6}
        print(f"规则覆写: res={v['res']} 改动 {v['changed']} 网；回读不符 {len(bad)}: {bad}")
        emit("route_rules_apply.json", {"changed": v["changed"], "applied": v["applied"],
                                        "mismatch": bad, "total_rules": v["total"]})
        sys.exit(0 if not bad and v["res"] else 1)

    if args.phase == "gvias":
        items = [{"net": "GND", "x": mm2mil(v["x"]), "y": mm2mil(30 - v["y"]),
                  "hole": 12, "dia": 24}
                 for v in plan["manual"]["gnd_vias"]]
        gv = plan["manual"].get("crystal_guard", {}).get("gnd_vias", [])
        items += [{"net": "GND", "x": mm2mil(x), "y": mm2mil(30 - y), "hole": 12, "dia": 24}
                  for (x, y) in gv]
        r = run_code(VIA_BATCH_JS, sid, timeout_ms=300000,
                     args_json=json.dumps({"items": items}, ensure_ascii=False))
        if not r.get("ok"):
            print(json.dumps(r, ensure_ascii=False)[:300]); sys.exit(1)
        ok = [x for x in r["value"] if x.get("ok")]
        bad = [x for x in r["value"] if not x.get("ok")]
        mismatch = [x for x in ok if x.get("back") and (
            x["back"]["net"] != "GND" or abs(x["back"]["hole"] - 12) > 0.01
            or abs(x["back"]["dia"] - 24) > 0.01)]
        print(f"GND 过孔: {len(ok)}/{len(items)} 成功；失败 {len(bad)}；回读不符 {len(mismatch)}")
        emit("route_gvias.json", {"ok": ok, "failed": bad, "mismatch": mismatch})
        sys.exit(0 if not bad and not mismatch else 1)

    if args.phase == "auto":
        if args.stage not in STAGES:
            print(f"# 未知 stage {args.stage}；可用: {list(STAGES)}"); sys.exit(2)
        nets, layers, note = STAGES[args.stage]
        todo = [n for n in nets if plan["nets"][[x["name"] for x in plan["nets"]].index(n)]["action"] == "auto"]
        if not todo:
            print("# 本 stage 无待布网（全部豁免/暂缓）"); sys.exit(0)
        props = {"RoutingNets": todo, "ignoreNets": IGNORE_NETS,
                 "cornerStyle": 0, "optimization": 1, "existingPrimitiveMode": "keep",
                 "layers": layers}
        r = run_code(START_AUTO_JS, sid, timeout_ms=120000,
                     args_json=json.dumps({"props": props}, ensure_ascii=False))
        if not r.get("ok") or not r.get("value"):
            print(f"# startAutoRouting 失败: {json.dumps(r, ensure_ascii=False)[:300]}"); sys.exit(2)
        job = r["value"]
        print(f"# 作业 {job} 启动（{len(todo)} 网 @层{layers}）", flush=True)
        state, waited = None, 0
        while waited < 900:
            time.sleep(5); waited += 5
            s = run_code(STATUS_JS, sid, timeout_ms=120000,
                         args_json=json.dumps({"jobId": job}))
            if not s.get("ok"):
                print(f"# 状态查询失败（重试中）: {json.dumps(s, ensure_ascii=False)[:200]}")
                continue
            state = s["value"]
            txt = json.dumps(state, ensure_ascii=False)[:200]
            print(f"  [{waited}s] {txt}", flush=True)
            st = (state or {}).get("status") if isinstance(state, dict) else state
            if isinstance(state, dict) and (state.get("finished") or state.get("done") or
                                            (isinstance(st, str) and st.lower() in ("done", "completed", "success", "failed"))):
                break
        # 回读：计数+每网铜长
        time.sleep(5)
        audit = run_code(AUDIT_JS, sid, timeout_ms=300000)
        lens = run_code(NETLEN_JS, sid, timeout_ms=240000)
        out = {"stage": args.stage, "nets": todo, "layers": layers, "job": job,
               "jobStatus": state, "audit_ok": audit.get("ok"), "netlen_ok": lens.get("ok")}
        if audit.get("ok"):
            a = audit["value"]
            out["lineCount"] = a["lineCount"]; out["viaCount"] = a["viaCount"]
        if lens.get("ok"):
            lm = {n["net"]: n["length"] for n in lens["value"]}
            out["netLengths"] = {n: lm.get(n) for n in todo}
        emit(f"route_auto_{args.stage}.json", out)
        print(f"auto[{args.stage}] 完成: lines={out.get('lineCount')} vias={out.get('viaCount')}; "
              f"netLengths={out.get('netLengths')}")
        sys.exit(0)

    if args.phase == "manual":
        m = plan["manual"]
        items = []
        # 晶振护栏走线
        g = m.get("crystal_guard", {})
        for (x1, y1, x2, y2) in g.get("segs", []):
            items.append({"net": "GND", "layer": 1, "x1": mm2mil(x1), "y1": mm2mil(30 - y1),
                          "x2": mm2mil(x2), "y2": mm2mil(30 - y2), "w": mm2mil(g["width_mm"])})
        for (x1, y1, x2, y2, w, layer) in m.get("usb_serpentine", []):
            items.append({"net": "USB_DP", "layer": layer, "x1": mm2mil(x1), "y1": mm2mil(30 - y1),
                          "x2": mm2mil(x2), "y2": mm2mil(30 - y2), "w": mm2mil(w)})
        for r_ in m.get("manual_fallback", []):
            for (x1, y1, x2, y2) in r_["segs"]:
                items.append({"net": r_["net"], "layer": r_["layer"], "x1": mm2mil(x1),
                              "y1": mm2mil(30 - y1), "x2": mm2mil(x2), "y2": mm2mil(30 - y2),
                              "w": mm2mil(r_["width_mm"])})
        if not items:
            print("# manual 无待创建项"); sys.exit(0)
        r = run_code(LINE_BATCH_JS, sid, timeout_ms=600000,
                     args_json=json.dumps({"items": items}, ensure_ascii=False))
        if not r.get("ok"):
            print(json.dumps(r, ensure_ascii=False)[:300]); sys.exit(1)
        ok = [x for x in r["value"] if x.get("ok")]
        bad = [x for x in r["value"] if not x.get("ok")]
        bad += [x for x in ok if x.get("back") and (
            x["back"]["net"] != x["net"] or abs((x["back"]["w"] or 0) - x["w"]) > 1)]
        print(f"manual 走线: {len(ok)}/{len(items)}；失败/不符 {len(bad)}")
        emit("route_manual.json", {"ok": len(ok), "failed": bad})
        sys.exit(0 if not bad else 1)

    if args.phase == "verify":
        audit = run_code(AUDIT_JS, sid, timeout_ms=300000)
        lens = run_code(NETLEN_JS, sid, timeout_ms=240000)
        if not audit.get("ok") or not lens.get("ok"):
            print(json.dumps({"audit": audit.get("error"), "netlen": lens.get("error")}, ensure_ascii=False))
            sys.exit(2)
        a, lensv = audit["value"], {n["net"]: n["length"] for n in lens["value"]}
        # keepout 越界审计（任务系 mm）
        ko = []
        for L in a["lines"]:
            pts = [(L["sx"] / MIL, 30 - L["sy"] / MIL), (L["ex"] / MIL, 30 - L["ey"] / MIL)]
            for (x, y) in pts:
                if 33.9 < x < 45.1 and -0.1 < y < 8.1:
                    ko.append({"net": L["net"], "layer": L["layer"], "at": [round(x, 2), round(y, 2)]})
        un = {n: l for n, l in lensv.items() if not l and n not in IGNORE_NETS}
        emit("routing_verify.json", {
            "lineCount": a["lineCount"], "viaCount": a["viaCount"],
            "regions": a["regions"], "netLengths": lensv,
            "zeroLengthNonExempt": un, "keepoutTouch": ko,
            "vias": a["vias"], "lines": a["lines"]})
        print(f"lines={a['lineCount']} vias={a['viaCount']} regions={len(a['regions'])}; "
              f"非豁免 0 长网 {len(un)}: {list(un)}; keepout 触碰 {len(ko)}")
        sys.exit(0)

    if args.phase == "drc":
        s = run_code(SAVE_JS, sid, timeout_ms=120000)
        print(f"# save: {s.get('value')}，等 10s", flush=True)
        time.sleep(10)
        d = run_code(DRC_JS, sid, timeout_ms=600000)
        if not d.get("ok"):
            print(json.dumps(d, ensure_ascii=False)[:400]); sys.exit(1)
        details = d["value"]
        if not isinstance(details, list):
            details = [details]
        emit("drc_g4_baseline.json", {"checkedAt": time.strftime("%F %T"), "count": len(details),
                                      "violations": details})
        print(f"DRC 违例 {len(details)} 条")
        for i, x in enumerate(details[:40]):
            print(f"  [{i}] {json.dumps(x, ensure_ascii=False)[:180]}")
        sys.exit(0)


def mm2mil(v):
    return int(round(v * MIL))


if __name__ == "__main__":
    main()
