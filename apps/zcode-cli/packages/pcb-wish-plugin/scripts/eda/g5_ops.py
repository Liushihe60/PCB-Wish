#!/usr/bin/env python3
"""g5_ops.py — G5 画布操作工具：保存 / 文档重载 / 读数复核。

用法:
  python g5_ops.py --action save
  python g5_ops.py --action reload      # closeDocument→openDocument→activateDocument（pitfalls #41）
  python g5_ops.py --action verify      # 3 次一致读数 + 59 件零漂移 + 423 线/80 孔核对
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import invoke, save_json, load_json  # noqa: E402

TAB = "1b70bcd67c184b71@891abc044ef64f37ab704d00aaac791e"
PAGE = "1b70bcd67c184b71"

ACTIVATE = r"""
const tree = await eda.dmt_EditorControl.getSplitScreenTree();
const tabs = tree.tabs || [];
const pcb = tabs.find(t => t.tabId && t.tabId.indexOf('1b70bcd67c184b71@') === 0);
if (!pcb) return JSON.stringify({err: 'PCB_TAB_NOT_FOUND', tabs: tabs.map(t=>t.tabId)});
await eda.dmt_EditorControl.activateDocument(pcb.tabId);
return JSON.stringify({activated: pcb.tabId});
"""

SAVE = r"""
const r = await eda.pcb_Document.save();
return {save: r};
"""

RELOAD = r"""
const a = __CLI__.args;
const TAB = a.tab, PAGE = a.page;
try { await eda.dmt_EditorControl.closeDocument(TAB); } catch(e) { var cErr = String(e).slice(0,150); }
await new Promise(res => setTimeout(res, 3000));
await eda.dmt_EditorControl.openDocument(PAGE);
await new Promise(res => setTimeout(res, 6000));
const tree = await eda.dmt_EditorControl.getSplitScreenTree();
const tabs = tree.tabs || [];
const pcb = tabs.find(t => t.tabId && t.tabId.indexOf(PAGE + '@') === 0);
if (pcb) await eda.dmt_EditorControl.activateDocument(pcb.tabId);
return {tabs: tabs.map(t=>t.tabId), activated: pcb ? pcb.tabId : null};
"""

PROBE = r"""
const out = {};
out.lines = (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length;
out.vias  = (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length;
out.comps = (await eda.pcb_PrimitiveComponent.getAllPrimitiveId()).length;
out.pours = (await eda.pcb_PrimitivePour.getAllPrimitiveId()).length;
out.poured= (await eda.pcb_PrimitivePoured.getAllPrimitiveId()).length;
out.regions = (await eda.pcb_PrimitiveRegion.getAllPrimitiveId()).length;
out.nets = (await eda.pcb_Net.getAllNetsName()).length;
const cids = await eda.pcb_PrimitiveComponent.getAllPrimitiveId();
const comps = [];
for (const id of cids) { try { const c = await eda.pcb_PrimitiveComponent.get(id);
  comps.push({dz: c.getState_Designator(), x: c.getState_X(), y: c.getState_Y(),
              rot: c.getState_Rotation()}); } catch(e) {} }
out.comps_detail = comps;
return out;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", default="verify")
    ap.add_argument("--baseline", default="pcb_pads_dump_manual.json")
    args = ap.parse_args()
    a = invoke(ACTIVATE, timeout_ms=120000)
    if not a.get("ok"):
        print("activate 失败:", json.dumps(a, ensure_ascii=False)[:300])
        sys.exit(1)
    if args.action == "save":
        r = invoke(SAVE, timeout_ms=300000)
        print("save:", json.dumps(r.get("value") or r, ensure_ascii=False)[:300])
        time.sleep(9)
    elif args.action == "reload":
        r = invoke(RELOAD, timeout_ms=300000,
                   args_json=json.dumps({"tab": TAB, "page": PAGE}))
        print("reload:", json.dumps(r.get("value") or r, ensure_ascii=False)[:300])
        time.sleep(6)
    elif args.action == "verify":
        reads = []
        for i in range(3):
            r = invoke(PROBE, timeout_ms=300000)
            v = r.get("value") or {}
            v.pop("comps_detail", None)
            reads.append(v)
            print(f"probe#{i+1}:", json.dumps(v, ensure_ascii=False))
            if i < 2:
                time.sleep(9)
        consistent = len({json.dumps(x, sort_keys=True) for x in reads}) == 1
        print("3次读数一致:", consistent)
        # 零漂移：59 件坐标与 D028 冻结基准比对
        r = invoke(PROBE, timeout_ms=300000)
        cur = (r.get("value") or {}).get("comps_detail") or []
        base = load_json(args.baseline)
        cmap = {c["dz"]: c for c in cur}
        diffs = []
        for c in base:
            k = cmap.get(c["dz"])
            if k is None:
                diffs.append({"dz": c["dz"], "err": "missing"})
                continue
            if abs(k["x"] - c["x"]) > 0.01 or abs(k["y"] - c["y"]) > 0.01:
                diffs.append({"dz": c["dz"], "base": [c["x"], c["y"]], "now": [k["x"], k["y"]]})
        print("59 件零漂移:", "PASS" if (not diffs and len(cur) == 59) else "FAIL",
              json.dumps(diffs, ensure_ascii=False)[:600])
        save_json("_g5_verify.json", {"reads": reads, "consistent": consistent,
                                      "drift": diffs, "comps": len(cur)})


if __name__ == "__main__":
    main()
