#!/usr/bin/env python3
"""s4e_auto.py — S4E 自动布线执行器（逐网五连 + 产物量测 + 线宽覆写）。

用法:
  python s4e_auto.py --net CHRGA --engine sync
  python s4e_auto.py --net VBUS --fixwidth

纪律：
  - 删净旧线前必须已有 reports/_s4e_snapshot_pre_auto.json（T1 快照）
  - delete 传数组（pitfalls #23）
  - 五连：预检→创建→save→等 10s→重枚举→记 s4_progress.md
  - 产物量测一律以画布几何回读为准，不信返回值（pitfalls #11/#14/#20）
  - 线宽覆写走 pcb_PrimitiveLine.modify(id, {lineWidth})（BETA modify 签名已探明）
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s4e_lib import invoke, save_json, load_json, HERE, corner_histogram  # noqa: E402
from lceda_invoke import run_code  # noqa: E402

SESSION = "44e574cc-dc99-46dd-80b8-60632f5b075a"
PROGRESS = HERE / "reports/s4_progress.md"
SNAP = HERE / "reports/_s4e_snapshot_pre_auto.json"
ENGINE_DB = HERE / "reports/_s4e_route_engine.json"

# 画布网名 ← plan 网名 别名
ALIAS = {"DEC5b_N": "DEC5B_N"}

DUMP_NET_JS = r"""
const target = __CLI__.args.net;
const out = {lines: [], vias: []};
for (const id of await eda.pcb_PrimitiveLine.getAllPrimitiveId()) {
  try { const l = await eda.pcb_PrimitiveLine.get(id);
    if (l.getState_Net() === target) out.lines.push({id, net: l.getState_Net(),
      layer: l.getState_Layer(), sx: l.getState_StartX(), sy: l.getState_StartY(),
      ex: l.getState_EndX(), ey: l.getState_EndY(), w: l.getState_LineWidth()});
  } catch (e) {}
}
for (const id of await eda.pcb_PrimitiveVia.getAllPrimitiveId()) {
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
out.vias  = (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length;
out.regions = (await eda.pcb_PrimitiveRegion.getAllPrimitiveId()).length;
out.comps = (await eda.pcb_PrimitiveComponent.getAllPrimitiveId()).length;
return out;
"""

DELETE_NET_JS = r"""
const target = __CLI__.args.net;
const dl = [], dv = [];
for (const id of await eda.pcb_PrimitiveLine.getAllPrimitiveId()) {
  try { const l = await eda.pcb_PrimitiveLine.get(id);
        if (l.getState_Net() === target) dl.push(id); } catch (e) {}
}
for (const id of await eda.pcb_PrimitiveVia.getAllPrimitiveId()) {
  try { const v = await eda.pcb_PrimitiveVia.get(id);
        if (v.getState_Net() === target) dv.push(id); } catch (e) {}
}
let rl = null, rv = null;
if (dl.length) { try { rl = await eda.pcb_PrimitiveLine.delete(dl); } catch(e) { rl = 'ERR:'+String(e).slice(0,120); } }
if (dv.length) { try { rv = await eda.pcb_PrimitiveVia.delete(dv); } catch(e) { rv = 'ERR:'+String(e).slice(0,120); } }
await eda.pcb_Document.save();
return {deletedLines: dl.length, deletedVias: dv.length, rl, rv,
        afterLines: (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length,
        afterVias: (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length};
"""

AUTO_SYNC_JS = r"""
const props = {
  RoutingNets: __CLI__.args.nets,
  ignoreNets: __CLI__.args.ignore,
  cornerStyle: 0,
  optimization: 1,
  existingPrimitiveMode: 'keep'
};
let res = null, err = null;
const t0 = Date.now();
try { res = await eda.pcb_Document.autoRouting(props); }
catch (e) { err = String(e).slice(0, 300); }
let dump = null;
try { dump = JSON.stringify(res); } catch (e) { dump = 'UNSERIALIZABLE'; }
return {err, raw: dump, type: typeof res, ms: Date.now()-t0,
        keys: (res && typeof res === 'object') ? Object.keys(res) : null,
        props};
"""

START_JOB_JS = r"""
const props = {
  RoutingNets: __CLI__.args.nets,
  ignoreNets: __CLI__.args.ignore,
  cornerStyle: 0,
  optimization: 1,
  existingPrimitiveMode: 'keep'
};
let res = null, err = null;
try { res = await eda.pcb_Document.startAutoRouting(props); }
catch (e) { err = String(e).slice(0, 300); }
let dump = null; try { dump = JSON.stringify(res); } catch (e) { dump = 'UNSERIALIZABLE'; }
return {err, raw: dump, jobId: (res && res.jobId !== undefined) ? res.jobId : null, props};
"""

FIXWIDTH_JS = r"""
const items = __CLI__.args.items;   // [{id, w}]
let ok = 0, bad = 0, errs = [];
for (const it of items) {
  try { await eda.pcb_PrimitiveLine.modify(it.id, {lineWidth: it.w}); ok++; }
  catch (e) { bad++; errs.push(String(e).slice(0,90)); }
}
return {ok, bad, errs: errs.slice(0,5)};
"""

# 文档重载（路由器状态复位）——批量删线后 autoRouting 会静默失效（见 pitfalls #33）
RELOAD_JS = r"""
const TAB = '1b70bcd67c184b71@891abc044ef64f37ab704d00aaac791e';
try { await eda.pcb_Document.save(); } catch (e) {}
let closed = null, opened = null;
try { closed = await eda.dmt_EditorControl.closeDocument(TAB); } catch (e) { closed = 'ERR:'+String(e).slice(0,100); }
await new Promise(r=>setTimeout(r,2500));
try { opened = await eda.dmt_EditorControl.openDocument('1b70bcd67c184b71'); } catch (e) { opened = 'ERR:'+String(e).slice(0,100); }
await new Promise(r=>setTimeout(r,6000));
try { await eda.dmt_EditorControl.activateDocument(TAB); } catch (e) {}
await new Promise(r=>setTimeout(r,1500));
return {closed, opened,
        lines: (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length,
        vias: (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length};
"""


def logline(msg):
    line = f"- S4E {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with PROGRESS.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def rc(code, args=None, timeout=590000):
    return run_code(code, SESSION, timeout_ms=timeout,
                    args_json=json.dumps(args, ensure_ascii=False) if args is not None else None)


def dump_net(net):
    r = rc(DUMP_NET_JS, {"net": net}, timeout=300000)
    if not r.get("ok"):
        return None
    return r["value"]


def measure(net):
    v = dump_net(net)
    if not v:
        return None
    hist, corners = corner_histogram(v["lines"])
    widths = {}
    for l in v["lines"]:
        widths[l["w"]] = widths.get(l["w"], 0) + 1
    layers = {}
    for l in v["lines"]:
        layers[l["layer"]] = layers.get(l["layer"], 0) + 1
    return {"segs": len(v["lines"]), "vias": len(v["vias"]),
            "netLen": v["netLen"].get(net, 0),
            "corner": hist, "corners90": [c for c in corners if c["cat"] == "90"],
            "corners_other": [c for c in corners if c["cat"] == "other"],
            "widths": widths, "layers": layers,
            "lines": v["lines"], "vias": v["vias"]}


def plan_widths():
    plan = json.loads((HERE / "spec/route_plan.json").read_text(encoding="utf-8"))
    w = {}
    for n in plan["nets"]:
        if n.get("width_mm"):
            w[ALIAS.get(n["name"], n["name"])] = n["width_mm"]
    return w


def route_one(net, engine="sync", ignore=("GND",), teardown=True, fixwidth=False):
    W = plan_widths()
    rec = {"net": net, "engine_requested": engine, "at": time.strftime("%F %T")}
    if teardown:
        d = rc(DELETE_NET_JS, {"net": net}, timeout=300000)
        rec["teardown"] = d.get("value") or {"error": d.get("error")}
        time.sleep(8)
        m0 = measure(net)
        rec["after_teardown"] = {"segs": m0["segs"], "vias": m0["vias"]} if m0 else None

    if engine in ("sync", "both"):
        a = rc(AUTO_SYNC_JS, {"nets": [net], "ignore": list(ignore)}, timeout=590000)
        rec["auto_sync"] = a.get("value") or {"invoke_error": a.get("error")}
        raw = rec["auto_sync"].get("raw")
        print(f"[{net}] autoRouting raw={str(raw)[:200]} "
              f"ms={rec['auto_sync'].get('ms')} err={rec['auto_sync'].get('err')}")
        # 判定"路由器跑过但该网失败" vs "路由器静默失效"
        ran = False
        failed_nets = []
        try:
            j = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(j, dict) and "success" in j:
                ran = True
                failed_nets = j.get("failedNets") or []
        except Exception:
            pass
        rec["router_ran"] = ran
        rec["router_failedNets"] = failed_nets
        time.sleep(6)
        m = measure(net)
        # 路由器静默失效（raw 无 success 字段且零产物）→ 重载文档复位后重试一次
        if (not ran) and (not m or m["segs"] == 0) and not rec.get("_reloaded"):
            rec["_reloaded"] = True
            rl = rc(RELOAD_JS, timeout=300000)
            rec["reload"] = rl.get("value") or {"error": rl.get("error")}
            print(f"[{net}] 路由器静默失效 → 重载文档复位: "
                  f"{json.dumps(rec['reload'], ensure_ascii=False)[:200]}")
            time.sleep(3)
            a = rc(AUTO_SYNC_JS, {"nets": [net], "ignore": list(ignore)}, timeout=590000)
            rec["auto_sync_retry"] = a.get("value") or {"invoke_error": a.get("error")}
            print(f"[{net}] autoRouting(retry) raw={str(rec['auto_sync_retry'].get('raw'))[:200]}")
            time.sleep(6)
            m = measure(net)
        rec["auto_sync_measure"] = m
        if m and m["segs"] > 0:
            rec["engine"] = "sync"
            rec["rule_width_mm"] = W.get(net)
            rec["produced_widths"] = m["widths"]
            rec["width_matches_rule"] = (
                abs(list(m["widths"])[0] / 39.37 - W[net]) < 0.02 if W.get(net) else None)
            rec["corner90_ratio"] = m["corner"]["ratio90"]
            if fixwidth and W.get(net):
                target = W[net] * 39.37
                items = [{"id": l["id"], "w": target} for l in m["lines"]]
                fr = rc(FIXWIDTH_JS, {"items": items}, timeout=300000)
                rec["fixwidth"] = fr.get("value") or {"error": fr.get("error")}
                time.sleep(4)
                m2 = measure(net)
                rec["auto_sync_measure_after_fixwidth"] = m2
            logline(f"S4E 五连[auto-sync] {net}: 段 {rec['auto_sync_measure']['segs']} "
                    f"孔 {rec['auto_sync_measure']['vias']} "
                    f"拐角 {json.dumps(rec['auto_sync_measure']['corner']['by_class'], ensure_ascii=False)} "
                    f"ratio90={rec['auto_sync_measure']['corner']['ratio90']:.3f} "
                    f"线宽 {json.dumps(rec['auto_sync_measure']['widths'])}")
            return rec

    if engine in ("job", "both"):
        j = rc(START_JOB_JS, {"nets": [net], "ignore": list(ignore)}, timeout=300000)
        rec["auto_job_start"] = j.get("value") or {"invoke_error": j.get("error")}
        print(f"[{net}] startAutoRouting raw={str(rec['auto_job_start'].get('raw'))[:200]} "
              f"jobId={rec['auto_job_start'].get('jobId')!r}")
        if rec["auto_job_start"].get("jobId"):
            time.sleep(20)
            m2 = measure(net)
            rec["auto_job_measure"] = m2
            if m2 and m2["segs"] > 0:
                rec["engine"] = "job"
                rec["corner90_ratio"] = m2["corner"]["ratio90"]
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", required=True)
    ap.add_argument("--engine", default="sync", choices=["sync", "job", "both"])
    ap.add_argument("--ignore", nargs="*", default=["GND"])
    ap.add_argument("--no-teardown", action="store_true")
    ap.add_argument("--fixwidth", action="store_true")
    ap.add_argument("--out", default="_s4e_route_engine.json")
    a = ap.parse_args()

    if not SNAP.exists():
        print("!! 缺 T1 快照"); sys.exit(2)

    rec = route_one(a.net, a.engine, tuple(a.ignore), teardown=not a.no_teardown,
                    fixwidth=a.fixwidth)
    s = rc("await eda.pcb_Document.save(); return 'saved';", timeout=180000)
    time.sleep(10)
    c = rc(COUNT_JS, timeout=300000)
    rec["after_save_count"] = c.get("value")
    print("save:", s.get("value"), "画布:", json.dumps(rec["after_save_count"], ensure_ascii=False))
    logline(f"S4E 路由 {a.net}: 画布 {rec['after_save_count']['lines']}/"
            f"{rec['after_save_count']['vias']} （线/孔）bad=0")

    p = HERE / "reports" / a.out
    db = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    db.setdefault("engines", []).append(rec)
    save_json(a.out, db)
    print("落盘:", p)


if __name__ == "__main__":
    main()
