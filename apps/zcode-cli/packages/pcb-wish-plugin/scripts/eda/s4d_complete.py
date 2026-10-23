#!/usr/bin/env python3
"""s4d_complete.py — 主 session 确定性收尾：电源网焊盘补全（P3）。

口径：
  - 输入 = reports/_s4_drc_raw.json 的 Connection Error（VDD/VBUS/VBAT/VREG_IN）
  - 废孔（Via 条目）→ 直接删除（树残骸）
  - 未连焊盘 → chain=[flag, nearest_connected_same_net_pad] → s4c_resolve → s4b_exec --create
  - 全程五连落账 reports/s4_progress.md；结果 reports/_s4d_complete.json
"""
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
PROJ = Path("E:/agent project/projects/flexiheal-v1")
REPORTS = PROJ / "reports"
TARGET_NETS = ["VDD", "VBUS", "VBAT", "VREG_IN"]
MIL = 39.37


def log(msg):
    line = f"- s4d {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(REPORTS / "s4_progress.md", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(args, timeout=300):
    r = subprocess.run([sys.executable] + args, capture_output=True, text=True,
                       timeout=timeout, encoding="utf-8", errors="replace", cwd=str(HERE))
    tail = (r.stdout + r.stderr).strip().splitlines()
    return tail[-1] if tail else ""


def pad_coord(pads, dz, padn):
    """从 manual dump 找焊盘中心（canvas mil）。"""
    for c in pads:
        if c["dz"] == dz:
            for p in c.get("pads", []):
                if str(p["n"]) == str(padn):
                    return (p["x"], p["y"])
    return None


def main():
    drc = json.loads((REPORTS / "_s4_drc_raw.json").read_text(encoding="utf-8"))
    pads = json.loads((REPORTS / "pcb_pads_dump_manual.json").read_text(encoding="utf-8"))

    flagged = {}   # net -> {"pads": {(dz,padn)}, "vias": [id]}
    for grp in drc["violations"]:
        if grp.get("name") != "Connection Error":
            continue
        for sub in grp.get("list", []):
            net = sub.get("name")
            if net not in TARGET_NETS:
                continue
            f = flagged.setdefault(net, {"pads": set(), "vias": []})
            for e in sub.get("list", []):
                o1 = e.get("obj1") or {}
                tn, suf = o1.get("typeName", ""), o1.get("suffix", "")
                if tn == "Via":
                    f["vias"].append(suf.split(":")[-1].strip())
                elif "Pad" in tn and ":" in suf:
                    padname = suf.split("): ")[-1].strip()
                    if "_" in padname:
                        dz, pn = padname.rsplit("_", 1)
                        f["pads"].add((dz, pn))
    log(f"标记汇总: " + " ".join(
        f"{k}(pad{len(v['pads'])}/via{len(v['vias'])})" for k, v in flagged.items()))

    # P3a 废孔删除（直接 delete，不走 create）
    junk = [vi for v in flagged.values() for vi in v["vias"]]
    if junk:
        out = run(["lceda_invoke.py", "--code",
                   "const ids=" + json.dumps(junk) + ";const out=[];"
                   "for(const id of ids){try{await eda.pcb_PrimitiveVia.delete(id);out.push(id);}catch(e){out.push('E');}}"
                   "await eda.pcb_Document.save();return JSON.stringify(out)"], timeout=240)
        log(f"废孔删除 {len(junk)} 颗: {out[:140]}")
        time.sleep(10)

    # P3b 未连焊盘两跳补线（chain 点号格式 dz.pad）
    results = {}
    for net, f in flagged.items():
        conn_cands = []
        for c in pads:
            for p in c.get("pads", []):
                if p.get("net") == net and (c["dz"], str(p["n"])) not in f["pads"]:
                    conn_cands.append(((c["dz"], str(p["n"])), (p["x"], p["y"])))
        for (dz, padn) in sorted(f["pads"]):
            fc = pad_coord(pads, dz, padn)
            if fc is None:
                results[f"{dz}.{padn}"] = "PAD_NOT_IN_DUMP"
                continue
            best = min(conn_cands, key=lambda x: (x[1][0]-fc[0])**2 + (x[1][1]-fc[1])**2)
            (tdz, tpadn), tc = best
            dist_mm = (((tc[0]-fc[0])**2 + (tc[1]-fc[1])**2) ** 0.5) / MIL
            r1 = run(["s4c_resolve.py", "--net", net, "--chain", f"{dz}.{padn}", f"{tdz}.{tpadn}"])
            ok1 = '"resolved": true' in r1 or 'resolved\\": true' in r1
            if not ok1:
                results[f"{dz}.{padn}"] = f"BLOCKED→{tdz}.{tpadn}"
                continue
            r2 = run(["s4b_exec.py", "--create", net])
            results[f"{dz}.{padn}"] = f"created({round(dist_mm,2)}mm→{tdz}.{tpadn}): {r2[:80]}"
            time.sleep(2)
        log(f"{net} 补全完成")
    (REPORTS / "_s4d_complete.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    log("P3 收尾：" + " ".join(f"{k}={v[:40]}" for k, v in list(results.items())[:12]))


def _junkdel_items(junk):
    """生成 s4_exec create 阶段可用的删除 items（借助已有删除脚本形态）。"""
    p = REPORTS / "_s4d_junkdel.json"
    p.write_text(json.dumps({"delete_vias": junk}, ensure_ascii=False), encoding="utf-8")
    return str(p)


if __name__ == "__main__":
    main()
