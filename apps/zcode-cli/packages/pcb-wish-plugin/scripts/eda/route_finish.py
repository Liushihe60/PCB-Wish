#!/usr/bin/env python3
"""route_finish.py — G4 收尾逐网执行器：autoRouting(RoutingNets=[net]) → pcb_Net 独立回验 → save。
用法: python route_finish.py --nets SWDIO SWCLK ... （缺省读 reports/_pending_nets.json）
状态落盘 reports/_finish_state.json (#done/#fail)。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import run_code, ensure_session  # noqa: E402

NETLEN_JS = "const n = await eda.pcb_Net.getAllNets(); const m={}; for (const x of n) m[x.net]=(x.length||0); return m;"
SAVE_JS = "await eda.pcb_Document.save(); return 'saved'"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nets", nargs="+", default=None)
    ap.add_argument("--pause", type=float, default=20.0)
    args = ap.parse_args()
    sid = ensure_session()
    if args.nets:
        nets = args.nets
    else:
        p = Path("E:/agent project/projects/flexiheal-v1/reports/_pending_nets.json")
        nets = json.loads(p.read_text(encoding="utf-8"))
    state = {"routed": [], "failed": [], "skipped": []}
    for net in nets:
        try:
            lens = run_code(NETLEN_JS, sid, timeout_ms=240000)
            cur = (lens.get("value") or {}).get(net)
            if cur and cur > 0:
                state["skipped"].append(net)
                print(f"[skip] {net} 已连通 (len={cur:.1f})", flush=True)
                json.dump(state, open("E:/agent project/projects/flexiheal-v1/reports/_finish_state.json", "w"))
                continue
            r = run_code(f"const res = await eda.pcb_Document.autoRouting({{RoutingNets: ['{net}'], ignoreNets: ['GND','ANT_N','ANT_FEED']}}); return JSON.parse(JSON.stringify(res));",
                         sid, timeout_ms=590000)
            v = r.get("value") or {}
            ok = v.get("success") and v.get("successNetsCount") == 1
            time.sleep(4)
            lens2 = run_code(NETLEN_JS, sid, timeout_ms=240000)
            after = (lens2.get("value") or {}).get(net)
            # 连通性以独立读数为准（0=未连）
            if after and after > 0:
                state["routed"].append(net)
                s = run_code(SAVE_JS, sid, timeout_ms=180000)
                time.sleep(8)
                print(f"[done] {net} len={after:.1f} save={s.get('value')}", flush=True)
            else:
                state["failed"].append(net)
                print(f"[fail] {net} router={json.dumps(v)[:120]} len={after}", flush=True)
        except Exception as e:  # noqa: BLE001
            state["failed"].append(net)
            print(f"[err ] {net}: {e}", flush=True)
        json.dump(state, open("E:/agent project/projects/flexiheal-v1/reports/_finish_state.json", "w"))
        time.sleep(args.pause)
    print("总结:", json.dumps(state), flush=True)


if __name__ == "__main__":
    main()
