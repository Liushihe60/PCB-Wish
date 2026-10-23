#!/usr/bin/env python3
"""sch_place_from_bom.py — 按 spec/bom.json 零搜索放置（G2 放置阶段，D005 工作流）。

选型已在 bom_finalize.py + 人工确认中定稿；本工具只做机械放置：
  - 泛型阻容（generic-0402/0603 条目 + VALUE_TABLE 值）；
  - 关键器件（ref-<位号> 条目，lib_Device.get 按 UUID 取器件对象）；
  - 逐件 create → modify(位号+值) → 回读；最终以网表元件数复核。

用法:
    python sch_place_from_bom.py --bom spec/bom.json --ir spec/ir.json --out reports/place_report.json
退出码: 0=全部就位; 1=存在失败。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import ensure_session, run_code  # noqa: E402
from sch_batch_place import VALUE_TABLE, GENERIC_0603  # noqa: E402

PLACE_ONE_JS = r"""
const spec = __CLI__.args.spec;   // {ref, val, uuid?, genericPkg?, x, y}
let dev, how;
if (spec.genericPkg) {
  const hits = await eda.lib_Device.search(spec.genericPkg);
  dev = hits.find(d => (d.name || '').trim() === spec.genericPkg);
  how = 'generic-' + spec.genericPkg;
  if (!dev) return {ref: spec.ref, ok: false, err: '泛型未找到 ' + spec.genericPkg};
} else {
  dev = await eda.lib_Device.get(spec.uuid);
  how = 'bom-uuid';
  if (!dev || !dev.uuid) return {ref: spec.ref, ok: false, err: 'UUID 取器件失败: ' + spec.uuid};
}
const comp = await eda.sch_PrimitiveComponent.create(dev, spec.x, spec.y, undefined, 0, false, true, true);
const pid = comp.getState_PrimitiveId();
await eda.sch_PrimitiveComponent.modify(pid, {designator: spec.ref, name: spec.val});
const back = await eda.sch_PrimitiveComponent.get(pid);
return {ref: spec.ref, ok: back.getState_Designator() === spec.ref, primId: pid, how,
        dev: ((back.getState_Component() || {}).name || dev.name || '').slice(0, 40)};
"""


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--bom", required=True)
    ap.add_argument("--ir", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--session", default=None)
    ap.add_argument("--gap", type=float, default=1.2)
    args = ap.parse_args()
    sid = ensure_session(args.session)

    bom = json.loads(Path(args.bom).read_text(encoding="utf-8"))
    ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    comps = []
    for m in ir["modules"]:
        comps += m["components"]

    have_r = run_code("const ids = await eda.sch_PrimitiveComponent.getAllPrimitiveId();"
                      "const out=[]; for (const id of ids){const c=await eda.sch_PrimitiveComponent.get(id);"
                      "out.push(c.getState_Designator());} return out;", sid)
    have = set(have_r.get("value", [])) if have_r.get("ok") else set()
    print(f"# 画布已有 {len(have)} 件", file=sys.stderr)

    results, failures = [], []
    g04 = bom.get("generic-0402", {}).get("deviceUuid")
    g06 = bom.get("generic-0603", {}).get("deviceUuid")

    for n, c in enumerate(comps, 1):
        ref = c["ref"]
        if ref in have:
            continue
        x = 100 + ((n - 1) % 8) * 135
        y = 120 + ((n - 1) // 8) * 115
        if ref in VALUE_TABLE:
            spec = {"ref": ref, "val": VALUE_TABLE[ref], "x": x, "y": y,
                    "genericPkg": "0603" if ref in GENERIC_0603 else "0402"}
            if spec["genericPkg"] == "0603" and not g06:
                failures.append({"ref": ref, "err": "无泛型0603"}); continue
        else:
            entry = bom.get(f"ref-{ref}")
            if not entry:
                failures.append({"ref": ref, "err": "BOM 无此位号条目"})
                continue
            spec = {"ref": ref, "val": entry.get("value", ref), "uuid": entry["deviceUuid"], "x": x, "y": y}
        r = run_code(PLACE_ONE_JS, sid, timeout_ms=120000,
                     args_json=json.dumps({"spec": spec}, ensure_ascii=False))
        v = r.get("value", {})
        if r.get("ok") and v.get("ok"):
            results.append(v)
            print(f"[{n}/{len(comps)}] {ref}: ✓ {v.get('dev','')}")
        else:
            failures.append({"ref": ref, "err": str(v.get("err") or r.get("error"))[:120]})
            print(f"[{n}/{len(comps)}] {ref}: ✗ {failures[-1]['err']}")
        time.sleep(args.gap)

    nl = run_code("const nl = JSON.parse(await eda.sch_Netlist.getNetlist());"
                  "return Object.keys(nl.components).length;", sid, timeout_ms=120000)
    net_n = nl.get("value", 0)
    report = {"total": len(comps), "have_before": len(have), "results": results, "failures": failures,
              "netlist_component_count": net_n}
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_cnt = len(have) + len(results)
    print(f"放置完成 {ok_cnt}/{len(comps)}；失败 {len(failures)}；网表元件数（最终事实源）: {net_n}")
    sys.exit(0 if not failures and net_n >= len(comps) else 1)


if __name__ == "__main__":
    main()
