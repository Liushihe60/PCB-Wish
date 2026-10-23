#!/usr/bin/env python3
"""s4e_drc.py — S4E DRC 抓取 + 分类计数 + 摘要（不把全量读进上下文）。

用法: python s4e_drc.py --out _s4e_drc.json
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s4e_lib import invoke, save_json, HERE  # noqa: E402

DRC_JS = r"""
const d = await eda.pcb_Drc.check(true, false, true);
return JSON.parse(JSON.stringify(d));
"""


def summarize(drc):
    """DRC 原始结构是两层树：[{name: 类别, list:[{name: 网, list:[条目]}]}]。

    primitiveId 取顶层 objs 数组（不是 obj1.suffix 的全局序号）——见 pitfalls #11。
    """
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
                    "obj1_primitiveId": ed.get("obj1"),
                    "obj1_suffix": ed.get("obj1Suffix"),
                    "pos": it.get("pos") or ed.get("position"),
                    "parentId": it.get("parentId"),
                })
    return {"total": len(out),
            "by_type": dict(by_type),
            "by_rule": dict(by_rule.most_common(60)),
            "by_net": dict(by_net.most_common(60)),
            "items": out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_s4e_drc.json")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    r = invoke(DRC_JS, timeout_ms=590000)
    print("drc 耗时 %.1fs ok=%s" % (time.time() - t0, r.get("ok")))
    if not r.get("ok"):
        print(json.dumps(r, ensure_ascii=False)[:400]); sys.exit(1)
    d = r["value"]
    save_json("_s4e_drc_raw.json", d)
    s = summarize(d)
    save_json(a.out, s)
    print("总数:", s["total"])
    print("by_type:", json.dumps(s["by_type"], ensure_ascii=False))
    print("by_rule:", json.dumps(s["by_rule"], ensure_ascii=False)[:1500])
    print("by_net:", json.dumps(s["by_net"], ensure_ascii=False)[:800])


if __name__ == "__main__":
    main()
