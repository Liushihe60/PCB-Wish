#!/usr/bin/env python3
"""sch_batch_place.py — IR → 原理图批量放置（pcb-wish G2 门第一段）。

从 spec/ir.json 读元件清单，分两类放置：
  泛型阻容（R/C/L3）：系统库泛型 "0402"/"0603" 器件 + modify 设位号与值属性
                     （BOM 规格即值字符串；委托方按 §4.3 用下单助手配 C 号）；
  关键器件（U/X/J/D/BTN/LED/TP/P3/ANT/L1/L2）：关键词严格匹配（val+pkg token 同时
  命中 name/description 才算命中，无宽松回退——宁缺勿错，垃圾匹配会污染 BOM）。
逐件回读核实（驱动纪律：不信返回值）；BETA API 读数可能滞后，最终以网表元件数为准。

用法:
    python sch_batch_place.py --ir spec/ir.json --out reports/place_report.json
    python sch_batch_place.py --ir spec/ir.json --audit   # 只审计画布现状
退出码: 0=全部就位; 1=存在失败/可疑。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import ensure_session, run_code  # noqa: E402

# 关键器件关键词表（ref → (搜索词, 值token小写, 封装token小写)）
KW_TABLE = {
    "U1": ("NRF52840", ["nrf52840"], []),
    "U2": ("TP4056", ["tp4056"], []),
    "U3": ("ME6217C33", ["me6217"], []),
    "U4": ("LMV321", ["lmv321"], []),
    "X1": ("32MHz", ["32mhz", "3225"], []),
    "X2": ("32.768kHz", ["32.768", "2012"], []),
    "D1": ("ESD9B5.0ST5G", ["esd9b5.0"], []),
    "J5": ("TYPE-C-16P", ["type-c", "16p"], []),
    "J1": ("PH2.0", ["ph2.0"], []),
    "J2": ("FPC-1.0-2P", ["fpc", "2p"], []),
    "J3": ("排母", ["2.54", "4p"], []),
    "J4": ("SWD", ["1.27", "4p"], []),
    "P3": ("排针", ["2.54", "3p"], []),
    "BTN1": ("轻触开关", ["轻触"], []),
    "BTN2": ("轻触开关", ["轻触"], []),
    "LED_A": ("0603LED", ["0603"], []),
    "LED_B": ("0603LED", ["0603"], []),
    "LED_C": ("0603LED", ["0603"], []),
    "LED_D": ("0603LED", ["0603"], []),
    "ANT1": ("2450AT18B100E", ["2450at"], []),
    "TP1": ("测试点", ["测试点"], []),
    "TP2": ("测试点", ["测试点"], []),
    "TP3": ("测试点", ["测试点"], []),
    "L1": ("10uH", ["10uh"], []),
    "L2": ("10uH", ["10uh"], []),
}

# 泛型阻容值表（ref → 值字符串；进 BOM 的规格）
VALUE_TABLE = {
    "R_PROG": "6.2k", "R1": "2.2k 1%", "R2": "1k", "R4": "100k", "R5": "100k",
    "R6": "0Ω DNP", "R7": "0Ω DNP(V1/V2手焊)", "R8": "0Ω DNP(V3手焊)",
    "R9": "5.1k", "R10": "5.1k", "R11": "33Ω", "R12": "33Ω", "R13": "10k",
    "R16": "1k", "R17": "1k DNP", "R18": "1k", "R19": "1k",
    "R20": "1M", "R21": "510k",
    "R_in": "10k DNP", "R_fb": "220k DNP", "R_ref1": "2.2k DNP", "R_ref2": "20Ω DNP",
    "R_refj1": "0Ω DNP", "R_refj2": "0Ω DNP",
    "C1": "0.1uF", "C2": "100nF", "C3": "100nF", "C4": "100nF", "C5": "100nF",
    "C6": "1uF", "C7": "4.7uF", "C8": "4.7uF",
    "C9": "2.2uF", "C33": "2.2uF", "C34": "2.2uF", "C35": "2.2uF",
    "C10": "100nF", "C11": "100nF DNP", "C12": "100pF", "C13": "100nF",
    "C14": "2.2uF", "C15": "820pF", "C16": "2.2uF", "C17": "1uF", "C18": "10uF",
    "C19": "2.2uF", "C20": "15pF", "C21": "15pF", "C22": "10pF DNP", "C23": "10pF DNP",
    "C24": "22uF DNP", "C25": "DNP", "C26": "DNP",
    "L3": "DNP",
}
GENERIC_0603 = {"C7", "C8", "C18", "C24"}

BATCH = 6

PLACE_JS = r"""
const comps = __CLI__.args.comps;
const norm = s => (s || '').toString().toLowerCase().replace(/µ/g, 'u');
const cache = {};
const out = [];
async function pickDevice(kw, valTok, pkgTok) {
  for (let att = 0; att < 4; att++) {
    try {
      if (!cache[kw]) cache[kw] = await eda.lib_Device.search(kw);
      const hits = cache[kw] || [];
      const strict = hits.filter(d => {
        const t = norm(d.name) + ' ' + norm(d.description);
        return valTok.every(x => t.includes(norm(x))) && pkgTok.every(x => t.includes(norm(x)));
      });
      if (strict.length) return {dev: strict[0], how: 'strict'};
      return {dev: null, how: 'no-strict'};
    } catch (e) {
      if (att === 3) return {dev: null, how: 'search-err'};
      await new Promise(r => setTimeout(r, [2000, 5000, 10000][att]));
    }
  }
  return {dev: null, how: 'no-strict'};
}
for (const c of comps) {
  try {
    let dev, how;
    if (c.generic) {
      const kw = c.pkg;
      if (!cache['g' + kw]) {
        const hits = await eda.lib_Device.search(kw);
        cache['g' + kw] = hits.find(d => (d.name || '').trim() === kw);
      }
      dev = cache['g' + kw];
      how = 'generic-' + kw;
      if (!dev) { out.push({ref: c.ref, ok: false, err: '泛型器件未找到: ' + kw}); continue; }
    } else {
      const r = await pickDevice(c.kw, c.valTok, c.pkgTok);
      if (!r.dev) { out.push({ref: c.ref, ok: false, err: r.how + ': ' + c.kw}); continue; }
      dev = r.dev;
      how = r.how;
    }
    const comp = await eda.sch_PrimitiveComponent.create(dev, c.x, c.y, undefined, 0, false, true, true);
    const pid = comp.getState_PrimitiveId();
    await eda.sch_PrimitiveComponent.modify(pid, {designator: c.ref, name: c.val});
    const back = await eda.sch_PrimitiveComponent.get(pid);
    const compData = back.getState_Component() || {};
    out.push({ref: c.ref, ok: back.getState_Designator() === c.ref, primId: pid, how,
              dev: (compData.name || dev.name || '').slice(0, 40), val: c.val});
  } catch (e) {
    out.push({ref: c.ref, ok: false, err: String(e).slice(0, 120)});
  }
}
return out;
"""

AUDIT_JS = r"""
const ids = await eda.sch_PrimitiveComponent.getAllPrimitiveId();
const out = [];
for (const id of ids) {
  const c = await eda.sch_PrimitiveComponent.get(id);
  const comp = c.getState_Component() || {};
  out.push({dz: c.getState_Designator(), dev: (comp.name || '?').slice(0, 48),
            val: c.getState_Name(), pid: id});
}
return out;
"""


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--ir", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--session", default=None)
    ap.add_argument("--audit", action="store_true")
    args = ap.parse_args()
    sid = ensure_session(args.session)

    ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    comps = []
    for m in ir["modules"]:
        comps += m["components"]

    if args.audit:
        r = run_code(AUDIT_JS, sid, timeout_ms=120000)
        rows = r.get("value", []) if r.get("ok") else []
        sus = [x for x in rows if any(k in (x["dev"] or "") for k in
               ("电位器", "端子", "KF2EDGV", "变压器", "电解", "钽", "MPP"))]
        print(f"画布器件 {len(rows)}；可疑 {len(sus)}")
        for x in sus:
            print("  可疑:", x["dz"], "->", x["dev"])
        sys.exit(0 if rows and not sus else 1)

    # 已放置（位号判定；BETA API 读数可能滞后——最终以网表数为准）
    placed = run_code("const ids = await eda.sch_PrimitiveComponent.getAllPrimitiveId();"
                      "const out=[]; for (const id of ids){const c=await eda.sch_PrimitiveComponent.get(id);"
                      "out.push(c.getState_Designator());} return out;", sid)
    have = set(placed.get("value", [])) if placed.get("ok") else set()
    print(f"# 画布已有 {len(have)} 件", file=sys.stderr)

    todo = [c for c in comps if c["ref"] not in have]
    results, failures = [], []
    for i in range(0, len(todo), BATCH):
        chunk = []
        for j, c in enumerate(todo[i:i + BATCH]):
            ref = c["ref"]
            gidx = i + j
            x = 100 + (gidx % 8) * 135
            y = 120 + (gidx // 8) * 115
            if ref in VALUE_TABLE:
                chunk.append({"ref": ref, "generic": True,
                              "pkg": "0603" if ref in GENERIC_0603 else "0402",
                              "val": VALUE_TABLE[ref], "x": x, "y": y})
            else:
                kw, valTok, pkgTok = KW_TABLE[ref], KW_TABLE[ref][1], KW_TABLE[ref][2]
                chunk.append({"ref": ref, "generic": False, "kw": kw,
                              "valTok": valTok, "pkgTok": pkgTok,
                              "val": (c.get("value") or ref).split("（")[0], "x": x, "y": y})
        r = run_code(PLACE_JS, sid, timeout_ms=300000,
                     args_json=json.dumps({"comps": chunk}, ensure_ascii=False))
        if not r.get("ok"):
            print(f"# 批 {i//BATCH+1} 整批失败: {json.dumps(r, ensure_ascii=False)[:200]}", file=sys.stderr)
            failures += [{"ref": cc["ref"], "err": "batch-failed"} for cc in chunk]
            continue
        for row in r["value"]:
            results.append(row)
            if not row["ok"]:
                failures.append(row)
        okn = sum(1 for row in r["value"] if row["ok"])
        import time as _t
        _t.sleep(4)
        print(f"# 批 {i//BATCH+1}: +{okn}/{len(chunk)}（累计 {len(have)+sum(1 for x in results if x['ok'])}）",
              file=sys.stderr)

    report = {"total": len(comps), "have_before": len(have), "results": results, "failures": failures}
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_cnt = len(have) + sum(1 for x in results if x["ok"])
    print(f"放置完成 {ok_cnt}/{len(comps)}；失败 {len(failures)}")
    sys.exit(0 if not failures and ok_cnt >= len(comps) else 1)


if __name__ == "__main__":
    main()
