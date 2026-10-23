#!/usr/bin/env python3
"""s4e_multi.py — S4E 多网成组自动布线（≤4 网/批，D017 过载边界）。

用途：单网失败且疑似被同族网抢占走廊时，把同族网删净后成组重布
（例：USB_DP 先布成功占道 → USB_DM/DP_P/DM_P 失败 → 四网成组重布）。

用法: python s4e_multi.py --nets USB_DP USB_DM USB_DP_P USB_DM_P
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s4e_lib import invoke, save_json, HERE, corner_histogram  # noqa: E402
from lceda_invoke import run_code  # noqa: E402
from s4e_auto import DELETE_NET_JS, AUTO_SYNC_JS, RELOAD_JS, DUMP_NET_JS, rc, logline  # noqa: E402

ENGINE_DB = HERE / "reports/_s4e_route_engine.json"


def measure(net):
    r = rc(DUMP_NET_JS, {"net": net}, timeout=300000)
    if not r.get("ok"):
        return None
    v = r["value"]
    hist, corners = corner_histogram(v["lines"])
    widths = {}
    for l in v["lines"]:
        widths[l["w"]] = widths.get(l["w"], 0) + 1
    return {"segs": len(v["lines"]), "vias": len(v["vias"]),
            "netLen": v["netLen"].get(net, 0), "corner": hist,
            "widths": widths, "lines": v["lines"], "vias": v["vias"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nets", nargs="+", required=True)
    ap.add_argument("--out", default="_s4e_route_engine.json")
    a = ap.parse_args()
    assert len(a.nets) <= 4, "D017：批量 >4 网易过载返 null"

    rec = {"nets": a.nets, "engine": "sync-batch", "at": time.strftime("%F %T")}
    # 1) 删净组内各网
    teardowns = {}
    for n in a.nets:
        d = rc(DELETE_NET_JS, {"net": n}, timeout=300000)
        teardowns[n] = d.get("value") or {"error": d.get("error")}
    time.sleep(10)
    rec["teardown"] = teardowns

    # 2) 成组自动布线
    r = rc(AUTO_SYNC_JS, {"nets": a.nets, "ignore": ["GND"]}, timeout=590000)
    rec["auto_sync"] = r.get("value") or {"invoke_error": r.get("error")}
    raw = rec["auto_sync"].get("raw")
    print("autoRouting raw=", str(raw)[:300])
    try:
        j = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(j, dict):
            rec["router_ran"] = True
            rec["router_failedNets"] = j.get("failedNets") or []
            rec["router_successNetsCount"] = j.get("successNetsCount")
    except Exception:
        pass
    time.sleep(8)
    if not rec.get("router_ran"):
        rl = rc(RELOAD_JS, timeout=300000)
        rec["reload"] = rl.get("value")
        time.sleep(3)
        r = rc(AUTO_SYNC_JS, {"nets": a.nets, "ignore": ["GND"]}, timeout=590000)
        rec["auto_sync_retry"] = r.get("value")
        print("retry raw=", str(rec["auto_sync_retry"].get("raw"))[:300])
        time.sleep(8)

    # 3) 逐网量测
    meas = {}
    for n in a.nets:
        m = measure(n)
        meas[n] = m
        if m:
            print(f"[{n}] 段 {m['segs']} 孔 {m['vias']} 拐角 "
                  f"{json.dumps(m['corner']['by_class'], ensure_ascii=False)} "
                  f"ratio90={m['corner']['ratio90']:.3f} 线宽 {json.dumps(m['widths'])}")
    rec["measure"] = meas

    s = rc("await eda.pcb_Document.save(); return 'saved';", timeout=180000)
    time.sleep(10)
    c = rc("const o={}; o.lines=(await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length;"
           " o.vias=(await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length;"
           " o.comps=(await eda.pcb_PrimitiveComponent.getAllPrimitiveId()).length; return o;",
           timeout=300000)
    rec["after_save_count"] = c.get("value")
    print("画布:", json.dumps(rec["after_save_count"], ensure_ascii=False))

    p = HERE / "reports" / a.out
    db = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    db.setdefault("engines", []).append(rec)
    save_json(a.out, db)
    for n in a.nets:
        m = meas.get(n) or {}
        logline(f"S4E 成组重布 {a.nets}: {n} 段 {m.get('segs')} 孔 {m.get('vias')} "
                f"ratio90={(m.get('corner') or {}).get('ratio90')}")


if __name__ == "__main__":
    main()
