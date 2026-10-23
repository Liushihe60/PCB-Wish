#!/usr/bin/env python3
"""g5_rules_set.py — 设置覆铜（Copper Zone）间隙为 0.3mm（brief_G5 T2.1）。

路径：config.Plane['Copper Zone'].copperRegion.form.{single,multi}LayerPadModel.data['1'].lineClearance
其余规则原样写回；写后回读并 diff 校验。

用法: python g5_rules_set.py [--value 0.3] [--apply]
产出: reports/_g5_rules_pour_clearance.json / _g5_rules_original.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import invoke, save_json  # noqa: E402

SAVE_ORIG = r"""
const cur = await eda.pcb_Drc.getCurrentRuleConfiguration();
return {name: cur.name, config: cur.config};
"""

GET_BY_NAME = r"""
const n = __CLI__.args.name;
const c = await eda.pcb_Drc.getRuleConfiguration(n);
return {name: n, cfg: c};
"""

APPLY = r"""
const val = __CLI__.args.value;
const name = __CLI__.args.name;
const c = await eda.pcb_Drc.getRuleConfiguration(name);
const cfg = c.config || c;
const cz = cfg.Plane && cfg.Plane['Copper Zone'] && cfg.Plane['Copper Zone'].copperRegion;
if (!cz) return {err: 'Copper Zone rule not found', planeKeys: Object.keys(cfg.Plane||{})};
const before = {};
for (const k of ['singleLayerPadModel', 'multiLayerPadModel']) {
  before[k] = cz.form[k].data['1'].lineClearance;
  cz.form[k].data['1'].lineClearance = val;
}
const ok = await eda.pcb_Drc.overwriteCurrentRuleConfiguration(cfg);
const after = await eda.pcb_Drc.getCurrentRuleConfiguration();
const cz2 = after.config.Plane['Copper Zone'].copperRegion;
const afterV = {};
for (const k of ['singleLayerPadModel', 'multiLayerPadModel']) {
  afterV[k] = cz2.form[k].data['1'].lineClearance;
}
return {ok: ok, src: name, before: before, after: afterV, curName: after.name,
  spacingZoneRow: after.config.Spacing['Safe Spacing']['copperThickness1oz'].tables['1'].content[7]};
"""

DELETE_CFG = r"""
const n = __CLI__.args.name;
try { const r = await eda.pcb_Drc.deleteRuleConfiguration(n); return {deleted: r, name: n}; }
catch(e) { return {err: String(e).slice(0,200)}; }
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--value", type=float, default=0.3)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--delete", default=None)
    ap.add_argument("--source", default="JLCPCB Capability(Two Layers Board)")
    args = ap.parse_args()
    if args.delete:
        r = invoke(DELETE_CFG, timeout_ms=300000, args_json=json.dumps({"name": args.delete}))
        print("delete:", json.dumps(r.get("value") or r, ensure_ascii=False)[:300])
        return
    a = invoke(SAVE_ORIG, timeout_ms=300000)
    if a.get("ok"):
        save_json("_g5_rules_original.json", a["value"])
        print("原始规则已备份:", a["value"]["name"])
    if not args.apply:
        print("dry-run")
        return
    r = invoke(APPLY, timeout_ms=300000,
               args_json=json.dumps({"value": args.value, "name": args.source}))
    print(json.dumps(r.get("value") or r, ensure_ascii=False)[:1200])
    save_json("_g5_rules_pour_clearance.json", r.get("value") or r)


if __name__ == "__main__":
    main()
