#!/usr/bin/env python3
"""s4e_cleanup.py — T2 清场：删除全部非 GND 网走线 + 非 GND 网过孔。

纪律：
  - 硬校验 reports/_s4e_snapshot_pre_auto.json 与 reports/_s4e_pre_cleanup_census.json 必须先落盘
  - GND 包地栏走线 + GND 缝合孔 保留（禁删）
  - delete 必须传数组（pitfalls #23）
  - 删完 save → 等 10s → 重枚举 ×3（读数一致才算完成）
输出：reports/_s4e_cleanup.json
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s4e_lib import invoke, save_json, load_json, HERE  # noqa: E402
from lceda_invoke import run_code  # noqa: E402

SESSION = "44e574cc-dc99-46dd-80b8-60632f5b075a"
SNAP = HERE / "reports/_s4e_snapshot_pre_auto.json"
CENSUS = HERE / "reports/_s4e_pre_cleanup_census.json"
OUT = HERE / "reports/_s4e_cleanup.json"
PROGRESS = HERE / "reports/s4_progress.md"

COUNT_JS = r"""
const out = {};
out.lines = (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length;
out.vias  = (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length;
out.regions = (await eda.pcb_PrimitiveRegion.getAllPrimitiveId()).length;
out.comps = (await eda.pcb_PrimitiveComponent.getAllPrimitiveId()).length;
const nets = await eda.pcb_Net.getAllNets();
out.netLen = {}; for (const n of nets) out.netLen[n.net] = n.length || 0;
const linesByNet = {};
for (const id of await eda.pcb_PrimitiveLine.getAllPrimitiveId()) {
  try { const l = await eda.pcb_PrimitiveLine.get(id); const n = l.getState_Net();
        linesByNet[n] = (linesByNet[n]||0)+1; } catch(e) {}
}
const viasByNet = {};
for (const id of await eda.pcb_PrimitiveVia.getAllPrimitiveId()) {
  try { const v = await eda.pcb_PrimitiveVia.get(id); const n = v.getState_Net();
        viasByNet[n] = (viasByNet[n]||0)+1; } catch(e) {}
}
out.linesByNet = linesByNet; out.viasByNet = viasByNet;
return out;
"""

# 分块删除（单次 invoke 内分块，避免一次性 466 id 触发超时）
CLEAN_JS = r"""
const doLines = __CLI__.args.doLines;   // [id...]
const doVias  = __CLI__.args.doVias;    // [id...]
const log = [];
const CH = 100;
for (let i = 0; i < doLines.length; i += CH) {
  const chunk = doLines.slice(i, i + CH);
  try {
    const r = await eda.pcb_PrimitiveLine.delete(chunk);
    log.push({type:'line', n: chunk.length, i: i, r: (typeof r === 'object') ? JSON.stringify(r).slice(0,120) : String(r).slice(0,120)});
  } catch (e) { log.push({type:'line', n: chunk.length, i: i, err: String(e).slice(0,150)}); }
}
for (let i = 0; i < doVias.length; i += CH) {
  const chunk = doVias.slice(i, i + CH);
  try {
    const r = await eda.pcb_PrimitiveVia.delete(chunk);
    log.push({type:'via', n: chunk.length, i: i, r: (typeof r === 'object') ? JSON.stringify(r).slice(0,120) : String(r).slice(0,120)});
  } catch (e) { log.push({type:'via', n: chunk.length, i: i, err: String(e).slice(0,150)}); }
}
await eda.pcb_Document.save();
return {log, afterLines: (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length,
        afterVias: (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length};
"""


def logline(msg):
    line = f"- S4E {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with PROGRESS.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    if not SNAP.exists() or not CENSUS.exists():
        print("!! 缺 T1 快照或 census —— 禁止删除"); sys.exit(2)
    snap = load_json("_s4e_snapshot_pre_auto.json")
    cen = load_json("_s4e_pre_cleanup_census.json")

    # 目标删除集合：非 GND 网全部走线 + 非 GND 网全部过孔（这些网全部从零重布）
    del_lines = [l["id"] for l in snap["lines"]
                 if l.get("net") and l["net"] != "GND" and not l.get("err")]
    del_vias = [v["id"] for v in snap["vias"]
                if v.get("net") and v["net"] != "GND" and not v.get("err")]
    keep_lines = [l["id"] for l in snap["lines"] if l.get("net") == "GND"]
    keep_vias = [v["id"] for v in snap["vias"] if v.get("net") == "GND"]

    pre = invoke(COUNT_JS, timeout_ms=300000)
    pre_v = pre.get("value") or {}
    print("清场前:", json.dumps({k: pre_v.get(k) for k in ("lines", "vias", "regions", "comps")},
                                ensure_ascii=False))
    print("清场前 linesByNet:", json.dumps(pre_v.get("linesByNet"), ensure_ascii=False))
    print("清场前 viasByNet:", json.dumps(pre_v.get("viasByNet"), ensure_ascii=False))

    r = run_code(CLEAN_JS, SESSION, timeout_ms=590000,
                 args_json=json.dumps({"doLines": del_lines, "doVias": del_vias},
                                      ensure_ascii=False))
    if not r.get("ok"):
        print("!! 删除调用失败:", json.dumps(r, ensure_ascii=False)[:400]); sys.exit(1)
    res = r["value"]
    print("删除返回:", json.dumps(res, ensure_ascii=False)[:600])
    time.sleep(10)

    reads = []
    for i in range(3):
        c = invoke(COUNT_JS, timeout_ms=300000)
        reads.append(c.get("value") or {})
        if i < 2:
            time.sleep(9)
    consistent = len({json.dumps({k: v.get(k) for k in ("lines", "vias", "regions", "comps")},
                                 sort_keys=True) for v in reads}) == 1
    post = reads[-1]
    print("清场后读数:", json.dumps({k: post.get(k) for k in ("lines", "vias", "regions", "comps")},
                                    ensure_ascii=False), "一致:", consistent)
    print("残留 linesByNet:", json.dumps(post.get("linesByNet"), ensure_ascii=False))
    print("残留 viasByNet:", json.dumps(post.get("viasByNet"), ensure_ascii=False))

    # 判定
    non_gnd_lines = {k: v for k, v in (post.get("linesByNet") or {}).items() if k != "GND"}
    non_gnd_vias = {k: v for k, v in (post.get("viasByNet") or {}).items() if k != "GND"}
    terminal_ok = (not non_gnd_lines and not non_gnd_vias and consistent
                   and post.get("lines") == len(keep_lines)
                   and post.get("vias") == len(keep_vias)
                   and post.get("comps") == 59 and post.get("regions") == 0)

    out = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "policy": "清场：删除全部非 GND 网走线与过孔（这些网全部从零重布）；"
                  "保留 GND 包地栏走线与 GND 缝合孔、59 组件、板框、位号文本、Region",
        "pre_counts": {k: pre_v.get(k) for k in ("lines", "vias", "regions", "comps")},
        "pre_linesByNet": pre_v.get("linesByNet"),
        "pre_viasByNet": pre_v.get("viasByNet"),
        "delete_plan": {
            "lines": len(del_lines),
            "vias": len(del_vias),
            "kept_gnd_lines": len(keep_lines),
            "kept_gnd_vias": len(keep_vias),
            "line_ids": del_lines,
            "via_ids": del_vias,
            "kept_line_ids": keep_lines,
            "kept_via_ids": keep_vias,
            "reasons": {
                "non_gnd_tracks": "旧 A* 产物，含大量 90° 直角拐角，用户判失败 → 全部清除后由自动布线从零重布",
                "non_gnd_vias": "所在网全部从零重布，孔随线重建（含 VREG_IN 2 处几何同位 3 连簇）",
                "gnd_keep": "GND 包地栏走线 + GND 缝合孔 = G5 铺铜地基，禁删",
            },
        },
        "delete_log": res.get("log"),
        "post_counts": {k: post.get(k) for k in ("lines", "vias", "regions", "comps")},
        "post_linesByNet": post.get("linesByNet"),
        "post_viasByNet": post.get("viasByNet"),
        "post_reads": [{k: v.get(k) for k in ("lines", "vias", "regions", "comps")} for v in reads],
        "reads_consistent": consistent,
        "residual_non_gnd_lines": non_gnd_lines,
        "residual_non_gnd_vias": non_gnd_vias,
        "terminal_ok": terminal_ok,
    }
    save_json("_s4e_cleanup.json", out)
    print("落盘:", OUT)
    print("terminal_ok =", terminal_ok)
    logline(f"T2 清场: 前 {out['pre_counts']['lines']}/{out['pre_counts']['vias']} → "
            f"后 {out['post_counts']['lines']}/{out['post_counts']['vias']} "
            f"(线/孔) 残留非GND线 {sum(non_gnd_lines.values())} 孔 {sum(non_gnd_vias.values())} "
            f"bad={0 if terminal_ok else 1}")


if __name__ == "__main__":
    main()
