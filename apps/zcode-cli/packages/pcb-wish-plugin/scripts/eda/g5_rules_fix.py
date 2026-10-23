#!/usr/bin/env python3
"""g5_rules_fix.py — 覆铜间隙设为 0.3mm，并把当前规则配置名恢复为原名。

背景：overwriteCurrentRuleConfiguration 会把当前配置 fork 成"自定义配置"。
本脚本在写回后把名字改回 'JLCPCB Capability(Two Layers Board)'（工程原配置名），
使项目规则配置身份不变，仅 Copper Zone 间隙由 0.254mm 调整为 0.3mm（brief_G5 T2.1）。

用法: python g5_rules_fix.py [--value 0.3] [--apply]
产出: reports/_g5_rules_pour_clearance.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import invoke, save_json  # noqa: E402

FIX = r"""
const val = __CLI__.args.value;
const ORIG = 'JLCPCB Capability(Two Layers Board)';
const cur = await eda.pcb_Drc.getCurrentRuleConfiguration();
const cfg = cur.config;
const cz = cfg.Plane['Copper Zone'].copperRegion;
const before = {};
for (const k of ['singleLayerPadModel', 'multiLayerPadModel']) {
  before[k] = cz.form[k].data['1'].lineClearance;
  cz.form[k].data['1'].lineClearance = val;
}
const out = {curNameBefore: cur.name, before: before, steps: []};
// 1) 先把修改后的配置存成一个存在的条目（当前条目名可能已被删）
const tmp = 'G5_POUR_CFG';
await eda.pcb_Drc.deleteRuleConfiguration(tmp);
out.steps.push({saveTmp: await eda.pcb_Drc.saveRuleConfiguration(cfg, tmp, true)});
// 2) 覆写当前配置
out.steps.push({overwrite: await eda.pcb_Drc.overwriteCurrentRuleConfiguration(cfg)});
// 3) 删除同名内置配置（若存在），再把 tmp 改名回原名
out.steps.push({delOrig: await eda.pcb_Drc.deleteRuleConfiguration(ORIG)});
out.steps.push({rename: await eda.pcb_Drc.renameRuleConfiguration(tmp, ORIG)});
const after = await eda.pcb_Drc.getCurrentRuleConfiguration();
const cz2 = after.config.Plane['Copper Zone'].copperRegion;
const afterV = {};
for (const k of ['singleLayerPadModel', 'multiLayerPadModel']) {
  afterV[k] = cz2.form[k].data['1'].lineClearance;
}
out.curNameAfter = after.name;
out.after = afterV;
out.allNames = (await eda.pcb_Drc.getAllRuleConfigurations()).map(c => c.name);
out.config = after.config;
return out;
"""

READ = r"""
const cur = await eda.pcb_Drc.getCurrentRuleConfiguration();
const cz = cur.config.Plane['Copper Zone'].copperRegion;
const v = {};
for (const k of ['singleLayerPadModel', 'multiLayerPadModel']) v[k] = cz.form[k].data['1'].lineClearance;
return {name: cur.name, clearance: v,
  allNames: (await eda.pcb_Drc.getAllRuleConfigurations()).map(c=>c.name),
  config: cur.config};
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--value", type=float, default=0.3)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.apply:
        r = invoke(READ, timeout_ms=300000)
        v = r.get("value") or {}
        print("当前规则名:", v.get("name"), "Copper Zone 间隙:", json.dumps(v.get("clearance")))
        print("配置清单:", v.get("allNames"))
        save_json("_g5_rules_pour_clearance.json", v)
        return
    r = invoke(FIX, timeout_ms=300000, args_json=json.dumps({"value": args.value}))
    v = r.get("value") or r
    print(json.dumps({k: v.get(k) for k in ("curNameBefore", "before", "steps", "curNameAfter", "after", "allNames")},
                     ensure_ascii=False)[:1500])
    save_json("_g5_rules_pour_clearance.json", v)


if __name__ == "__main__":
    main()
