#!/usr/bin/env python3
"""s4b_auto.py — S4B autoRouting 单网遍历器（D017 口径：≤1~2 网/批+冷却）。

用法: python s4b_auto.py --nets VBUS VBAT VREG_IN VDD [--pause 20]
每网: 局外 autoRouting({RoutingNets:[n], ignoreNets:[GND,ANT_N], existingPrimitiveMode:keep})
  → 等 5s → pcb_Net 独立回读 → len>0 则 save+10s+重枚举（五连）→ 记 s4_progress.md
  → len==0 视失败记录；连续 3 次同型失败即停报部分完成。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import ensure_session, run_code  # noqa: E402

HERE = Path("E:/agent project/projects/flexiheal-v1")
PROGRESS_MD = HERE / "reports/s4_progress.md"
PROGRESS_JSON = HERE / "reports/_s4b_progress.json"

NETLEN_JS = "const n = await eda.pcb_Net.getAllNets(); const m={}; for (const x of n) m[x.net]=(x.length||0); return m;"
COUNT_JS = r"""
const out = {};
out.lines = (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length;
out.vias = (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length;
const nets = await eda.pcb_Net.getAllNets();
out.netLen = {};
for (const n of nets) out.netLen[n.net] = n.length || 0;
return out;
"""


def append_progress(text, batch):
    with PROGRESS_MD.open("a", encoding="utf-8") as f:
        f.write(f"- {text}\n")
    pj = {}
    if PROGRESS_JSON.exists():
        pj = json.loads(PROGRESS_JSON.read_text(encoding="utf-8"))
    pj.setdefault("batches", []).append(batch)
    PROGRESS_JSON.write_text(json.dumps(pj, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nets", nargs="+", required=True)
    ap.add_argument("--pause", type=float, default=25.0)
    ap.add_argument("--force", action="store_true",
                    help="不做已连通跳过（stub 局部长度会被误判已布）")
    args = ap.parse_args()
    sid = ensure_session()
    done, failed = [], []
    same_fail = 0
    for net in args.nets:
        if same_fail >= 3:
            print(f"!! 连续 {same_fail} 次同型失败 → 停止（不死循环），failed={failed}")
            break
        if not args.force:
            pre = run_code(NETLEN_JS, sid, timeout_ms=180000)
            cur = (pre.get("value") or {}).get(net) if pre.get("ok") else None
            if cur and cur > 0:
                print(f"[skip] {net} 已连通 len={cur:.0f}")
                done.append(net)
                time.sleep(args.pause)
                continue
        code = (f"const res = await eda.pcb_Document.autoRouting("
                f"{{RoutingNets: ['{net}'], ignoreNets: ['GND','ANT_N'], "
                f"existingPrimitiveMode: 'keep'}}); "
                f"return JSON.parse(JSON.stringify(res));")
        r = run_code(code, sid, timeout_ms=590000)
        time.sleep(5)
        lens = run_code(NETLEN_JS, sid, timeout_ms=180000)
        after = (lens.get("value") or {}).get(net) if lens.get("ok") else None
        router = json.dumps(r.get("value"), ensure_ascii=False)[:140] if r.get("ok") else str(r)[:140]
        if after and after > 0:
            s = run_code("await eda.pcb_Document.save(); return 'saved'", sid, timeout_ms=120000)
            time.sleep(10)
            c = run_code(COUNT_JS, sid, timeout_ms=180000)
            st = c.get("value") or {}
            msg = (f"S4B 五连(router) {net}: len={after:.0f} save={s.get('value')} "
                   f"画布 {st.get('lines')}/{st.get('vias')}")
            print(f"[done] {msg}")
            append_progress(msg, {"at": time.strftime("%F %T"), "type": "autoRouting",
                                  "net": net, "netLen": after,
                                  "after": {k: st.get(k) for k in ("lines", "vias")}})
            done.append(net)
            same_fail = 0
        else:
            same_fail += 1
            print(f"[fail] {net}: router={router} len(after)={after} (fail#{same_fail})")
            append_progress(f"S4B(router) {net} 失败: router={router}", {"at": time.strftime("%F %T"),
                          "type": "autoRouting-fail", "net": net, "router": router})
            failed.append(net)
        time.sleep(args.pause)
    print(f"\n完成 {len(done)}/{len(args.nets)}: done={done} failed={failed}")


if __name__ == "__main__":
    main()
