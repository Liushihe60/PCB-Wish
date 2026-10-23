#!/usr/bin/env python3
"""sch_wire_from_ir.py — IR 网络表 → 原理图网络标签/端口布线（G2 布线）。

策略（pitfalls #1 + BETA API 能力边界）:
  GND 类 → createNetFlag('Ground')；电源类（VDD/VBAT/VBUS/VREG_IN）→ createNetFlag('Power')；
  其余信号/模拟/差分/RF → createNetPort('BI')（本单 ~104 端子，低于 180-263 并网阈值，
  且 G2 门有独立 netlist_assert 兜底）。
  引脚解析：IR 成员 "REF.引脚" → 引脚 pinNumber 精确匹配 → pinName 精确匹配 → 别名表
  （DP→D+ 等）；同名多引脚（如 J5.VBUS×4）全部挂网。解析结果落盘 spec/pinmap.json
  （=四方确认表数据源：封装焊盘号/网络名逐脚对照）。解析失败不猜，列入报告待人工映射。

用法:
    python sch_wire_from_ir.py --ir spec/ir.json --report reports/wire_report.json
退出码: 0=全部成员挂网; 1=存在未解析成员。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import ensure_session, run_code  # noqa: E402

POWER_NETS = {"VDD", "VBAT", "VBUS", "VREG_IN"}

# IR 引脚名 → 符号引脚名候选（大小写不敏感；pinNumber 也参与精确匹配）
ALIASES = {
    "DP": ["DP", "D+", "DP1", "D+1"],
    "DM": ["DM", "D-", "DN", "DN1"],
    "VBUS": ["VBUS", "VBUS1"],
    "GND": ["GND"],
    "CC1": ["CC1"], "CC2": ["CC2"],
    "VIN": ["VIN"], "EN": ["EN", "CE"], "VOUT": ["VOUT"], "VSS": ["GND", "VSS"],
    "PROG": ["PROG"], "CE": ["CE"], "CHRG": ["CHRG"], "STDBY": ["STDBY"], "BAT": ["BAT"],
    "INP": ["IN+", "INP"], "INN": ["IN-", "INN"], "OUT": ["OUT"], "VP": ["V+", "VP", "VCC"], "VM": ["V-", "VM", "GND"],
}

SCAN_JS = r"""
const ids = await eda.sch_PrimitiveComponent.getAllPrimitiveId();
const out = {};
for (const id of ids) {
  const c = await eda.sch_PrimitiveComponent.get(id);
  const dz = c.getState_Designator();
  const pins = await eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId(id);
  out[dz] = pins.map(p => ({num: p.pinNumber, name: p.pinName || '', x: p.x, y: p.y, pid: p.primitiveId}));
}
return out;
"""

WIRE_BATCH_JS = r"""
const flags = __CLI__.args.flags;   // [{net, kind, x, y}]
const out = [];
for (const f of flags) {
  try {
    let pid;
    if (f.kind === 'Ground' || f.kind === 'Power') {
      const r = await eda.sch_PrimitiveComponent.createNetFlag(f.kind, f.net, f.x, f.y, 0, false);
      pid = r.getState_PrimitiveId();
    } else {
      const r = await eda.sch_PrimitiveComponent.createNetPort('BI', f.net, f.x, f.y, 0, false);
      pid = r.getState_PrimitiveId();
    }
    out.push({net: f.net, ref: f.ref || '', pin: f.pin || '', ok: !!pid, pid});
  } catch (e) {
    out.push({net: f.net, ref: f.ref || '', ok: false, err: String(e).slice(0, 80)});
  }
  await new Promise(r => setTimeout(r, 150));
}
return out;
"""


def resolve_pins(ir, canvas):
    """IR nets 成员 → 画布引脚坐标。返回 (taps, pinmap, unresolved)。"""
    taps, pinmap, unresolved = [], {}, []
    for net in ir["nets"]:
        nname, clazz = net["name"], net.get("clazz", "signal")
        kind = "Ground" if clazz == "ground" else ("Power" if nname in POWER_NETS else "Port")
        for member in net["members"]:
            ref, _, pin = member.partition(".")
            pins = canvas.get(ref)
            if not pins:
                unresolved.append({"net": nname, "member": member, "why": "画布无此位号"})
                continue
            aliases = ALIASES.get(pin, [pin]) + [pin]
            al = [a.lower() for a in aliases]
            hit = [p for p in pins if (p["num"] or "").lower() in al or (p["name"] or "").lower() in al]
            if not hit:
                unresolved.append({"net": nname, "member": member,
                                   "why": f"符号无匹配引脚（候选名: {sorted({p['name'] for p in pins})[:8]}）"})
                continue
            pinmap.setdefault(ref, {})[pin] = {"pinNumbers": [p["num"] for p in hit],
                                               "symbolNames": sorted({p["name"] for p in hit}),
                                               "net": nname}
            for p in hit:
                taps.append({"net": nname, "kind": kind, "x": p["x"], "y": p["y"],
                             "ref": ref, "pin": p["num"]})
    return taps, pinmap, unresolved


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--ir", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--pinmap", default="spec/pinmap.json")
    ap.add_argument("--session", default=None)
    ap.add_argument("--batch", type=int, default=15)
    args = ap.parse_args()
    sid = ensure_session(args.session)

    ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    scan = run_code(SCAN_JS, sid, timeout_ms=180000)
    if not scan.get("ok"):
        print(json.dumps(scan, ensure_ascii=False)[:300])
        sys.exit(2)
    canvas = scan["value"]
    print(f"# 画布器件 {len(canvas)} 件", file=sys.stderr)

    taps, pinmap, unresolved = resolve_pins(ir, canvas)
    Path(args.pinmap).parent.mkdir(parents=True, exist_ok=True)
    Path(args.pinmap).write_text(json.dumps(pinmap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"# 解析: 待挂网 {len(taps)} 处；未解析 {len(unresolved)}（pinmap 已落盘）", file=sys.stderr)
    for u in unresolved[:15]:
        print(f"  [未解析] {u['member']} @ {u['net']}: {u['why'][:70]}", file=sys.stderr)

    wired, failures = [], []
    for i in range(0, len(taps), args.batch):
        chunk = taps[i:i + args.batch]
        r = run_code(WIRE_BATCH_JS, sid, timeout_ms=240000,
                     args_json=json.dumps({"flags": chunk}, ensure_ascii=False))
        if not r.get("ok"):
            failures += [{"net": f["net"], "ref": f["ref"], "err": "batch-failed"} for f in chunk]
            print(f"# 批 {i//args.batch+1} 整批失败", file=sys.stderr)
            continue
        for row in r["value"]:
            (wired if row["ok"] else failures).append(row)
        print(f"# 批 {i//args.batch+1}: +{sum(1 for x in r['value'] if x['ok'])}/{len(chunk)}",
              file=sys.stderr)
        time.sleep(3)

    await_save = run_code("await eda.sch_Document.save(); return 'saved'", sid, timeout_ms=120000)
    rep = {"taps": len(taps), "wired": len(wired), "failures": failures, "unresolved": unresolved,
           "save": await_save.get("value")}
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"布线完成 {len(wired)}/{len(taps)}；失败 {len(failures)}；未解析 {len(unresolved)}；"
          f"保存: {await_save.get('value')} → {args.report}")
    sys.exit(0 if not failures and not unresolved else 1)


if __name__ == "__main__":
    main()
