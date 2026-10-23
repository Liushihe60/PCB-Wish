#!/usr/bin/env python3
"""g5_netlist_fix.py — T4 P2 幽灵网清理（路径1：清除 PCB 侧 7 颗幽灵焊盘的网络归属）。

背景：D021 删 RF/电源块后，PCB 侧 7 颗焊盘仍残留旧网络名（画布无对应器件）：
  U1.A18=DEC2_N / U1.B3=DCC / U1.D2=XL1 / U1.F2=XL2 / U1.H23=ANT_N / U1.AB2=DCCH / U2.6=STDBY_N
原理图侧这些脚均未接（spec/exported_netlist.json 无这些引脚）→ PCB 清空网络即与原理图一致。

用法: python g5_netlist_fix.py [--apply]
产出: reports/_g5_netlist_fix.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import invoke, save_json  # noqa: E402

GHOST_NETS = ["DEC2_N", "DCC", "XL1", "XL2", "ANT_N", "DCCH", "STDBY_N"]

ACTIVATE = r"""
const tree = await eda.dmt_EditorControl.getSplitScreenTree();
const tabs = tree.tabs || [];
const pcb = tabs.find(t => t.tabId && t.tabId.indexOf('1b70bcd67c184b71@') === 0);
if (!pcb) return JSON.stringify({err: 'PCB_TAB_NOT_FOUND'});
await eda.dmt_EditorControl.activateDocument(pcb.tabId);
"""

PROBE = r"""
const args = __CLI__.args;
const ids = await eda.pcb_PrimitivePad.getAllPrimitiveId();
const out = [];
for (const id of ids) {
  try { const p = await eda.pcb_PrimitivePad.get(id);
    if (args.ghosts.indexOf(p.net) >= 0) {
      out.push({id, net: p.net, x: p.x, y: p.y, layer: p.layer, padNumber: p.padNumber});
    }
  } catch(e) {}
}
return {ghostPads: out, netNames: await eda.pcb_Net.getAllNetsName()};
"""

CLEAR = r"""
const args = __CLI__.args;
const ids = await eda.pcb_PrimitivePad.getAllPrimitiveId();
const targets = [];
for (const id of ids) {
  try { const p = await eda.pcb_PrimitivePad.get(id);
    if (args.ghosts.indexOf(p.net) >= 0) targets.push({id, net: p.net, num: p.padNumber});
  } catch(e) {}
}
const res = [];
for (const t of targets) {
  try {
    await eda.pcb_PrimitivePad.modify(t.id, { net: '' });
    const back = await eda.pcb_PrimitivePad.get(t.id);
    res.push({id: t.id, was: t.net, now: back.net});
  } catch(e) { res.push({id: t.id, was: t.net, err: String(e).slice(0, 200)}); }
}
return {cleared: res, netNames: await eda.pcb_Net.getAllNetsName()};
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    a = invoke(ACTIVATE, timeout_ms=120000)
    if not a.get("ok"):
        print("activate 失败:", json.dumps(a, ensure_ascii=False)[:300])
        sys.exit(1)
    p = invoke(PROBE, timeout_ms=300000,
               args_json=json.dumps({"ghosts": GHOST_NETS}))
    pv = p.get("value") or {}
    print("幽灵焊盘:", json.dumps(pv.get("ghostPads"), ensure_ascii=False))
    print("清理前网表(%d):" % len(pv.get("netNames") or []), pv.get("netNames"))
    out = {"before": pv}
    if args.apply:
        c = invoke(CLEAR, timeout_ms=300000,
                   args_json=json.dumps({"ghosts": GHOST_NETS}))
        cv = c.get("value") or {}
        out["clear"] = cv
        print("清理结果:", json.dumps(cv.get("cleared"), ensure_ascii=False))
        print("清理后网表(%d):" % len(cv.get("netNames") or []), cv.get("netNames"))
    save_json("_g5_netlist_fix.json", out)


if __name__ == "__main__":
    main()
