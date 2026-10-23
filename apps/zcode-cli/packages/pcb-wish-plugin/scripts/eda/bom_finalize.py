#!/usr/bin/env python3
"""bom_finalize.py — G0.5 选型定稿：库内搜索 → spec/bom.json（放置与搜索解耦）。

流程修正（D005）：选型与放置是两个阶段。本工具只做选型：
  1. 泛型阻容 → 解析系统库泛型 "0402"/"0603" 器件（按名字精确匹配）+ 值表；
  2. 关键器件 → 单词关键词搜索（一次一件、批间停顿、指数退避——防限流），
     客户端 strict 过滤（值/封装 token 同现于 name+description），无宽松回退；
  3. 产出 spec/bom.json：ref → {deviceUuid, deviceName, value, how}，供
     sch_batch_place.py --from-bom 零搜索放置。未匹配项显式列出待人工定。

用法:
    python bom_finalize.py --ir spec/ir.json --out spec/bom.json [--gap 6]
退出码: 0=全部定稿; 1=存在未匹配（需人工确认后写入 bom.json 的 manual 段）。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import ensure_session, run_code  # noqa: E402
from sch_batch_place import KW_TABLE, VALUE_TABLE, GENERIC_0603  # noqa: E402

# 关键器件单词关键词（避免多词/特殊字符触发服务端 wd 参数错误）
SIMPLE_KW = {
    "U1": "NRF52840-QIAA", "U2": "TP4056", "U3": "ME6217C33", "U4": "LMV321",
    "X1": "3225", "X2": "32.768K", "D1": "ESD9B5.0ST5G",
    "J5": "TYPE-C-16P", "J1": "PH2.0", "J2": "FPC-2P",
    "J3": "排母", "J4": "1.27", "P3": "排针",
    "BTN1": "轻触开关", "BTN2": "轻触开关",
    "LED_A": "0603LED", "LED_B": "0603LED", "LED_C": "0603LED", "LED_D": "0603LED",
    "ANT1": "2450AT18B100E", "TP1": "测试点", "TP2": "测试点", "TP3": "测试点",
    "L1": "10uH", "L2": "10uH",
}
# 客户端 strict 过滤 token（name+description 同现才算命中）
FILTER_TOKENS = {
    "U1": ["nrf52840", "qiaa"], "U2": ["tp4056"], "U3": ["me6217"], "U4": ["lmv321"],
    "X1": ["3225", "32mhz"], "X2": ["32.768", "2012"], "D1": ["esd9b5.0"],
    "J5": ["type-c", "16p"], "J1": ["ph2.0"], "J2": ["fpc", "2p", "1.0"],
    "J3": ["2.54", "4p"], "J4": ["1.27", "4p"], "P3": ["2.54", "3p"], "J3": ["2.54", "4p"],
    "BTN1": ["轻触"], "BTN2": ["轻触"],
    "LED_A": ["0603", "蓝"], "LED_B": ["0603", "红"], "LED_C": ["0603", "红"], "LED_D": ["0603", "红"],
    "ANT1": ["2450at"], "TP1": ["测试点"], "TP2": ["测试点"], "TP3": ["测试点"],
    "L1": ["10uh"], "L2": ["10uh"],
}

SEARCH_JS = r"""
const kw = __CLI__.args.kw;
const norm = s => (s || '').toString().toLowerCase().replace(/µ/g, 'u');
for (let att = 0; att < 3; att++) {
  try {
    const hits = await eda.lib_Device.search(kw);
    return hits.slice(0, 10).map(d => ({uuid: d.uuid, name: d.name || '', desc: (d.description || '').slice(0, 80)}));
  } catch (e) {
    if (att === 2) return {err: String(e).slice(0, 80)};
    await new Promise(r => setTimeout(r, [3000, 8000, 15000][att]));
  }
}
"""


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--ir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--session", default=None)
    ap.add_argument("--gap", type=float, default=6.0, help="搜索间隔秒（防限流）")
    args = ap.parse_args()
    sid = ensure_session(args.session)

    ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    comps = []
    for m in ir["modules"]:
        comps += m["components"]

    bom_path = Path(args.out)
    bom = json.loads(bom_path.read_text(encoding="utf-8")) if bom_path.exists() else {}

    # 1) 泛型阻容
    for gname, pkg in (("0402", "0402"), ("0603", "0603")):
        key = f"generic-{pkg}"
        if key not in bom:
            r = run_code(f"const hits = await eda.lib_Device.search('{pkg}');"
                         f"const g = hits.find(d => (d.name||'').trim() === '{pkg}');"
                         "return g ? {uuid: g.uuid, name: g.name} : {err: '泛型未找到'}", sid)
            v = r.get("value", {})
            if v.get("uuid"):
                bom[key] = {"deviceUuid": v["uuid"], "deviceName": v["name"], "how": "generic"}
                print(f"# 泛型 {pkg}: {v['uuid']}")
            else:
                print(f"# 泛型 {pkg} 未找到: {v}")
            time.sleep(args.gap)

    # 2) 关键器件逐件搜索
    unmatched = []
    todo = [c for c in comps if c["ref"] not in VALUE_TABLE
            and c["ref"] not in {k.split('-')[0] for k in bom if k.startswith('ref-')}
            and f"ref-{c['ref']}" not in bom]
    for n, c in enumerate(todo, 1):
        ref = c["ref"]
        kw = SIMPLE_KW.get(ref, (c.get("value") or ref).split("（")[0].split(" ")[0])
        toks = FILTER_TOKENS.get(ref, [kw.lower()])
        time.sleep(args.gap)
        r = run_code(SEARCH_JS, sid, timeout_ms=90000,
                     args_json=json.dumps({"kw": kw}, ensure_ascii=False))
        hits = r.get("value", [])
        if isinstance(hits, dict) or not isinstance(hits, list):
            print(f"[{n}/{len(todo)}] {ref}: 搜索失败 {hits}")
            unmatched.append({"ref": ref, "err": "search-failed"})
            continue
        strict = [h for h in hits if all(t in (h["name"] + h["desc"]).lower().replace("µ", "u") for t in toks)]
        if strict:
            h = strict[0]
            bom[f"ref-{ref}"] = {"deviceUuid": h["uuid"], "deviceName": h["name"],
                                 "value": (c.get("value") or ref).split("（")[0],
                                 "kw": kw, "how": "strict"}
            print(f"[{n}/{len(todo)}] {ref}: ✓ {h['name'][:40]}")
        else:
            unmatched.append({"ref": ref, "kw": kw,
                              "candidates": [h["name"] for h in hits[:5]]})
            print(f"[{n}/{len(todo)}] {ref}: ✗ 无严格命中（kw={kw}），候选: {[h['name'][:24] for h in hits[:3]]}")

    bom_path.parent.mkdir(parents=True, exist_ok=True)
    bom_path.write_text(json.dumps(bom, ensure_ascii=False, indent=2), encoding="utf-8")
    n_ref = sum(1 for k in bom if k.startswith("ref-"))
    print(f"\nBOM 定稿: 关键件 {n_ref}/{len([c for c in comps if c['ref'] not in VALUE_TABLE])}，"
          f"泛型 2 组；未匹配 {len(unmatched)} → {bom_path}")
    sys.exit(0 if not unmatched else 1)


if __name__ == "__main__":
    main()
