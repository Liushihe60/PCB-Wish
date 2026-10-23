#!/usr/bin/env python3
"""s4e_astar_all.py — T5 兜底驱动：对全部"缺焊盘"网逐网 A* 补解 + 45° 倒角 + 创建。

流程：
  - 读 reports/_s4_conn_analysis.json 取各网 disconnected_members
  - 每个缺失焊盘 → 最近同网已连通焊盘 作为 chain
  - 调 s4e_astar_fix.py --apply（创建 → save → 等 10s → 重枚举）
  - 每网后重新 dump 画布（后续网的障碍模型要含已建铜）
"""
import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

HERE = Path("E:/agent project/projects/flexiheal-v1")
EDADIR = Path(__file__).parent
MIL = 39.37
DUMP = HERE / "reports/pcb_pads_dump_manual.json"


def pad_positions():
    d = json.loads(DUMP.read_text(encoding="utf-8"))
    out = {}
    for c in d:
        for p in c.get("pads", []):
            out["%s.%s" % (c["dz"], p["n"])] = (p["x"] / MIL, 30 - p["y"] / MIL, p.get("net"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nets", nargs="*", default=None)
    ap.add_argument("--via-cost", type=float, default=2.0)
    ap.add_argument("--width", type=float, default=0.254)
    a = ap.parse_args()

    conn = json.loads((HERE / "reports/_s4_conn_analysis.json").read_text(encoding="utf-8"))
    pads = pad_positions()
    jobs = []
    for net, v in conn.items():
        if net == "GND":
            continue
        disc = v.get("disconnected_members") or []
        if not disc:
            continue
        members = v.get("members") or []
        connected = [m for m in members if m not in disc]
        if not connected:
            for i in range(len(disc) - 1):
                jobs.append((net, disc[i], disc[i + 1]))
            continue
        for dm in disc:
            if dm not in pads:
                continue
            px, py, _ = pads[dm]
            best = min(connected,
                       key=lambda k: math.hypot(pads[k][0] - px, pads[k][1] - py)
                       if k in pads else 1e9)
            jobs.append((net, dm, best))
    if a.nets:
        jobs = [j for j in jobs if j[0] in a.nets]
    print("待补解 %d 跳：" % len(jobs), flush=True)
    for j in jobs:
        print("   ", j, flush=True)

    results = []
    for net, frm, to in jobs:
        print("\n=== %s: %s -> %s ===" % (net, frm, to), flush=True)
        r = subprocess.run([sys.executable, str(EDADIR / "s4e_astar_fix.py"),
                            "--net", net, "--chain", frm, to,
                            "--width", str(a.width), "--via-cost", str(a.via_cost),
                            "--apply"],
                           capture_output=True, text=True, timeout=2400,
                           cwd=str(HERE), encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        print(out[-900:], flush=True)
        ok = ("仍有间距违例" not in out) and ("BLOCKED" not in out) and (r.returncode == 0)
        results.append({"net": net, "chain": [frm, to], "ok": ok,
                        "rc": r.returncode, "tail": out[-300:]})
        if ok:
            subprocess.run([sys.executable, str(EDADIR / "s4e_dump_all.py"),
                            "--out", "_s4e_tracks_vias_final.json"],
                           cwd=str(HERE), capture_output=True, text=True, timeout=900,
                           encoding="utf-8", errors="replace")
        time.sleep(4)
    (HERE / "reports/_s4e_astar_all.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n汇总:", json.dumps([{"net": r["net"], "chain": r["chain"], "ok": r["ok"]}
                                for r in results], ensure_ascii=False))


if __name__ == "__main__":
    main()
