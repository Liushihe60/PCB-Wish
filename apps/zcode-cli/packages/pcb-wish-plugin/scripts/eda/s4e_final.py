#!/usr/bin/env python3
"""s4e_final.py — T7 收尾聚合：画布终态 dump + 几何连通 + 拐角直方图 + DRC。

用法: python s4e_final.py
产出: reports/_s4e_tracks_vias_final.json / _s4_conn_analysis.json /
      _s4e_corner_hist.json / _s4e_drc.json / _s4e_final_summary.json
"""
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path("E:/agent project/projects/flexiheal-v1")
EDADIR = Path(__file__).parent
sys.path.insert(0, str(EDADIR))


def run(cmd, timeout=1800):
    r = subprocess.run(cmd, cwd=str(HERE), capture_output=True, text=True,
                       timeout=timeout, encoding="utf-8", errors="replace")
    return r


def main():
    t0 = time.time()
    run([sys.executable, str(EDADIR / "s4e_dump_all.py"), "--out", "_s4e_tracks_vias_final.json"])
    run([sys.executable, str(EDADIR / "s4_analyze.py"),
         "--dump", "reports/pcb_pads_dump_manual.json",
         "--audit", "reports/_s4e_tracks_vias_final.json",
         "--assert", "spec/assertions.json"])
    run([sys.executable, str(EDADIR / "s4e_corner_hist.py"),
         "--dump", "_s4e_tracks_vias_final.json", "--out", "_s4e_corner_hist.json"])
    run([sys.executable, str(EDADIR / "s4e_drc.py"), "--out", "_s4e_drc.json"])

    conn = json.loads((HERE / "reports/_s4_conn_analysis.json").read_text(encoding="utf-8"))
    hist = json.loads((HERE / "reports/_s4e_corner_hist.json").read_text(encoding="utf-8"))
    drc = json.loads((HERE / "reports/_s4e_drc.json").read_text(encoding="utf-8"))
    dump = json.loads((HERE / "reports/_s4e_tracks_vias_final.json").read_text(encoding="utf-8"))

    rows = []
    for net, v in sorted(conn.items()):
        if net == "GND":
            continue
        disc = v.get("disconnected_members") or []
        rows.append({"net": net, "segs": v.get("seg_count"), "vias": v.get("via_count"),
                     "mm": v.get("copper_mm"), "groups": v.get("n_pad_groups"),
                     "connected": not disc, "disconnected": disc,
                     "pcb_Net_len": dump.get("netLen", {}).get(net, 0)})
    summary = {
        "generated": time.strftime("%F %T"),
        "canvas_counts": dump.get("counts"),
        "netLen_second_criterion": dump.get("netLen"),
        "connectivity": rows,
        "connected_non_gnd": sum(1 for r in rows if r["connected"]),
        "total_non_gnd_nets": len(rows),
        "corner_board": hist.get("board"),
        "corner_gates": hist.get("gates"),
        "drc_total": drc.get("total"),
        "drc_by_type": drc.get("by_type"),
        "drc_by_net": drc.get("by_net"),
        "elapsed_s": round(time.time() - t0, 1),
    }
    (HERE / "reports/_s4e_final_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("画布:", json.dumps(dump.get("counts"), ensure_ascii=False))
    print("非GND连通 %d/%d" % (summary["connected_non_gnd"], summary["total_non_gnd_nets"]))
    for r in rows:
        if not r["connected"]:
            print("   未连通:", r["net"], r["disconnected"])
    print("拐角:", json.dumps(hist.get("board", {}).get("by_class"), ensure_ascii=False),
          "ratio90=%.4f" % hist.get("board", {}).get("ratio90", 0))
    print("DRC:", drc.get("total"), json.dumps(drc.get("by_type"), ensure_ascii=False))


if __name__ == "__main__":
    main()
