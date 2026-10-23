#!/usr/bin/env python3
"""s4b_rules.py — S4B 网级线宽规则抽查/重覆写。

--check  : 读 pcb_Drc.getNetRules 抽查 VBUS=1.0/VDD=0.6/ADC_IN=0.3 → reports/_s4b_rules_check.json
--reapply: 按 route_plan.json 宽度表覆写（含 DEC5b_N→画布 DEC5B_N 别名）→ 回读 → s4b_rules_apply.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import ensure_session, run_code  # noqa: E402

GET_RULES_JS = "return await eda.pcb_Drc.getNetRules();"

# 覆写后回读（复用 G4 范式：改 Track 字段 + overwriteNetRules）
APPLY_JS = r"""
const widths = __CLI__.args.widths;   // {canvasNetName: mm}
const rules = await eda.pcb_Drc.getNetRules();
let changed = 0;
for (const r of rules) {
  if (widths[r.name] !== undefined) { r.Track = widths[r.name]; changed++; }
}
const res = await eda.pcb_Drc.overwriteNetRules(rules);
const back = await eda.pcb_Drc.getNetRules();
const out = {};
for (const r of back) { if (widths[r.name] !== undefined) out[r.name] = r.Track; }
return {res, changed, applied: out, total: back.length};
"""

ALIAS = {"DEC5b_N": "DEC5B_N"}  # plan 名 → 画布名


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reapply", action="store_true")
    ap.add_argument("--plan", default="spec/route_plan.json")
    args = ap.parse_args()
    sid = ensure_session()
    here = Path("E:/agent project/projects/flexiheal-v1")
    plan = json.loads((here / args.plan).read_text(encoding="utf-8"))
    widths = {}
    for n in plan["nets"]:
        if n.get("width_mm") and n["action"] == "auto":
            widths[ALIAS.get(n["name"], n["name"])] = n["width_mm"]

    if args.check:
        r = run_code(GET_RULES_JS, sid, timeout_ms=240000)
        if not r.get("ok"):
            print("getNetRules 失败:", json.dumps(r)[:300]); sys.exit(1)
        rules = r["value"]
        want = {"VBUS": 1.0, "VDD": 0.6, "ADC_IN": 0.3}
        got = {x["name"]: fnum(x.get("Track")) for x in rules if x["name"] in want}
        missing = [k for k, v in got.items() if v is None]
        mismatch = {k: v for k, v in got.items() if v is not None and abs(v - want[k]) > 1e-6}
        ok = not missing and not mismatch
        out = {"checkedAt": time.strftime("%F %T"), "want": want, "got": got,
               "missing": missing, "mismatch": mismatch, "ok": ok,
               "total_rules": len(rules)}
        (here / "reports/_s4b_rules_check.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(0 if ok else 2)

    if args.reapply:
        r = run_code(APPLY_JS, sid, timeout_ms=240000,
                     args_json=json.dumps({"widths": widths}, ensure_ascii=False))
        if not r.get("ok"):
            print("覆写失败:", json.dumps(r)[:300]); sys.exit(1)
        v = r["value"]
        bad = {k: x for k, x in v["applied"].items() if x is None or abs(fnum(x) - widths[k]) > 1e-6}
        missing = [k for k in widths if k not in v["applied"]]
        print(f"覆写 res={v['res']} 改动 {v['changed']}；回读不符 {bad}；规则表缺 {missing}")
        out = {"reappliedAt": time.strftime("%F %T"), "changed": v["changed"],
               "applied": v["applied"], "mismatch": bad, "missing": missing,
               "total_rules": v["total"]}
        (here / "reports/s4b_rules_apply.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        # save 前 spot 抽查三点
        s = run_code("await eda.pcb_Document.save(); return 'saved'", sid, timeout_ms=120000)
        print("save:", s.get("value")); time.sleep(10)
        r2 = run_code(GET_RULES_JS, sid, timeout_ms=240000)
        spot = {}
        if r2.get("ok"):
            for x in r2["value"]:
                if x["name"] in ("VBUS", "VDD", "ADC_IN", "DEC5B_N"):
                    spot[x["name"]] = fnum(x.get("Track"))
        out["postSaveSpot"] = spot
        (here / "reports/s4b_rules_apply.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print("save 后抽查:", json.dumps(spot))
        sys.exit(0 if not bad and not missing and spot.get("VBUS") == 1.0 and
                 spot.get("VDD") == 0.6 and spot.get("ADC_IN") == 0.3 else 2)


if __name__ == "__main__":
    main()
