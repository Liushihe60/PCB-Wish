#!/usr/bin/env python3
"""s4e_blockers.py — 失败网的"逃逸阻塞源"识别：目标焊盘周围的异网铜距离排行。

用途：单网连续失败时，先量化"谁挡了这条网的出脚"，再决定删哪些网腾走廊 / 转兜底引擎。

用法: python s4e_blockers.py [--dump _s4e_tracks_vias_final.json] [--radius 1.2]
"""
import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s4e_lib import load_json, HERE  # noqa: E402

MIL = 39.37
DUMP = HERE / "reports/pcb_pads_dump_manual.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="_s4e_tracks_vias_final.json")
    ap.add_argument("--radius", type=float, default=1.2)
    a = ap.parse_args()
    canvas = load_json(a.dump)
    conn = json.loads((HERE / "reports/_s4_conn_analysis.json").read_text(encoding="utf-8"))
    pads = json.loads(DUMP.read_text(encoding="utf-8"))
    padpos = {}
    for c in pads:
        for p in c.get("pads", []):
            padpos["%s.%s" % (c["dz"], p["n"])] = (p["x"] / MIL, 30 - p["y"] / MIL, p.get("net"))
    plmap = json.loads((HERE / "reports/_s4e_pad_layers_map.json").read_text(encoding="utf-8"))
    pth = {k for k, v in plmap.items() if v == 12}

    def pt_seg(px, py, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        if L2 == 0:
            return math.hypot(px - a[0], py - a[1])
        t = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / L2))
        return math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy))

    out = {}
    for net, v in conn.items():
        if net == "GND":
            continue
        disc = v.get("disconnected_members") or []
        if not disc:
            continue
        for dm in disc:
            if dm not in padpos:
                continue
            px, py, _ = padpos[dm]
            hits = []
            for l in canvas["lines"]:
                if l.get("net") == net or l.get("err"):
                    continue
                p1 = (l["sx"] / MIL, 30 - l["sy"] / MIL)
                p2 = (l["ex"] / MIL, 30 - l["ey"] / MIL)
                dd = pt_seg(px, py, p1, p2)
                if dd < a.radius:
                    hits.append((round(dd, 3), l["net"], "L%d" % l.get("layer")))
            for vv in canvas["vias"]:
                if vv.get("net") == net or vv.get("err"):
                    continue
                dd = math.hypot(vv["x"] / MIL - px, 30 - vv["y"] / MIL - py)
                if dd < a.radius:
                    hits.append((round(dd, 3), vv["net"], "VIA"))
            for c in pads:
                for p in c.get("pads", []):
                    if (p.get("net") or "") == net:
                        continue
                    key = "%s.%s" % (c["dz"], p["n"])
                    qx, qy = p["x"] / MIL, 30 - p["y"] / MIL
                    dd = math.hypot(qx - px, qy - py)
                    if dd < a.radius:
                        hits.append((round(dd, 3), p.get("net") or "-", "PAD" + ("(PTH)" if key in pth else "")))
            hits.sort()
            agg = Counter()
            for d, n2, kind in hits:
                agg[(n2, kind)] += 1
            out["%s/%s" % (net, dm)] = {
                "pad": [round(px, 3), round(py, 3)],
                "nearest": hits[:12],
                "blocker_nets": dict(Counter(n2 for _, n2, _ in hits).most_common(8)),
            }
    (HERE / "reports/_s4e_blockers.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("落盘 reports/_s4e_blockers.json")
    for k, v in out.items():
        print("%-22s pad=%s 阻塞网=%s" % (k, v["pad"],
              json.dumps(v["blocker_nets"], ensure_ascii=False)))


if __name__ == "__main__":
    main()
