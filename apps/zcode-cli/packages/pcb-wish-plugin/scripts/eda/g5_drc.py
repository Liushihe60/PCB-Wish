#!/usr/bin/env python3
"""g5_drc.py — G5 DRC 抓取 + 分类计数 + 摘要（两层树口径，pitfalls #37）。

用法: python g5_drc.py --out drc_g5_final.json [--raw _g5_drc_raw.json]
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import invoke, save_json  # noqa: E402

DRC_JS = r"""
const d = await eda.pcb_Drc.check(true, false, true);
return JSON.parse(JSON.stringify(d));
"""


def summarize(drc):
    roots = drc if isinstance(drc, list) else (drc or {}).get("list") or []
    by_type = Counter()
    by_rule = Counter()
    by_net = Counter()
    out = []
    for root in roots:
        cat = root.get("name") or root.get("type") or ""
        groups = root.get("list") or []
        for g in groups:
            gname = g.get("name") or ""
            items = g.get("list") or []
            if not items and g.get("errorType"):
                items = [g]
            for it in items:
                t = it.get("errorType") or cat
                s = it.get("ruleName") or ""
                net = it.get("net") or gname
                ed = it.get("errData") or {}
                by_type[t] += 1
                by_rule[f"{t}|{s}"] += 1
                if net:
                    by_net[str(net)] += 1
                out.append({
                    "category": cat, "type": t, "rule": s, "net": net,
                    "obj_type": it.get("errorObjType"),
                    "objs": it.get("objs") or [],
                    "errData": ed,
                    "pos": it.get("pos") or ed.get("position"),
                    "parentId": it.get("parentId"),
                    "explanation": it.get("explanation"),
                })
    return {"total": len(out),
            "by_type": dict(by_type),
            "by_rule": dict(by_rule.most_common(60)),
            "by_net": dict(by_net.most_common(60)),
            "items": out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_g5_drc.json")
    ap.add_argument("--raw", default="_g5_drc_raw.json")
    args = ap.parse_args()
    t0 = time.time()
    r = invoke(DRC_JS, timeout_ms=590000)
    print("drc 耗时 %.1fs ok=%s" % (time.time() - t0, r.get("ok")))
    if not r.get("ok"):
        print(json.dumps(r, ensure_ascii=False)[:400])
        sys.exit(1)
    d = r["value"]
    save_json(args.raw, d)
    s = summarize(d)
    save_json(args.out, s)
    print("总数:", s["total"])
    print("by_type:", json.dumps(s["by_type"], ensure_ascii=False))
    print("by_rule:", json.dumps(s["by_rule"], ensure_ascii=False)[:1200])
    print("by_net:", json.dumps(s["by_net"], ensure_ascii=False)[:600])


if __name__ == "__main__":
    main()
