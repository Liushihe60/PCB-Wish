#!/usr/bin/env python3
"""s4e_corner_hist.py — 全板拐角角度直方图（验收判据 4：90° 占比 ≤5%）。

用法: python s4e_corner_hist.py --dump _s4e_tracks_vias_final.json --out _s4e_corner_hist.json
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s4e_lib import save_json, load_json, HERE, corner_histogram  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="_s4e_tracks_vias_final.json")
    ap.add_argument("--out", default="_s4e_corner_hist.json")
    a = ap.parse_args()
    d = load_json(a.dump)
    lines = [l for l in d["lines"] if not l.get("err")]
    vias = [v for v in d["vias"] if not v.get("err")]
    hist, corners = corner_histogram(lines)

    by_net = Counter()
    for c in corners:
        by_net[(c["net"], c["cat"])] += 1
    net90 = Counter()
    net45 = Counter()
    nettot = Counter()
    for (net, layer), segs in _group(lines).items():
        pass
    # 逐网拐角统计
    per_net = {}
    grouped = {}
    for l in lines:
        grouped.setdefault((l.get("net"), l.get("layer")), []).append(l)
    for (net, layer), segs in grouped.items():
        h, c = corner_histogram(segs)
        per_net.setdefault(net, {"segs": 0, "corners": 0, "n90": 0, "n45": 0,
                                 "nother": 0, "nstraight": 0})
        per_net[net]["segs"] += len(segs)
        per_net[net]["corners"] += h["total_corners"]
        per_net[net]["n90"] += h["by_class"].get("90", 0)
        per_net[net]["n45"] += h["by_class"].get("45", 0)
        per_net[net]["nother"] += h["by_class"].get("other", 0)
        per_net[net]["nstraight"] += h["by_class"].get("straight", 0)
    for n, v in per_net.items():
        v["ratio90"] = round(v["n90"] / v["corners"], 4) if v["corners"] else 0.0
        v["ratio45"] = round(v["n45"] / v["corners"], 4) if v["corners"] else 0.0

    out = {
        "generated": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "canvas_counts": d.get("counts"),
        "board": hist,
        "per_net": per_net,
        "corner90_detail": corners,
        "gates": {
            "ratio90_le_5pct": hist["ratio90"] <= 0.05,
            "total_corners": hist["total_corners"],
            "n90": hist["by_class"].get("90", 0),
            "n45": hist["by_class"].get("45", 0),
            "nother": hist["by_class"].get("other", 0),
        },
    }
    save_json(a.out, out)
    print("落盘:", a.out)
    print("总拐角:", hist["total_corners"], "分类:", json.dumps(hist["by_class"], ensure_ascii=False))
    print("ratio90=%.4f ratio45=%.4f other=%.4f 闸门(≤5%%)=%s"
          % (hist["ratio90"], hist["ratio45"], hist["ratio_other"],
             "PASS" if hist["ratio90"] <= 0.05 else "FAIL"))


def _group(lines):
    g = {}
    for l in lines:
        g.setdefault((l.get("net"), l.get("layer")), []).append(l)
    return g


if __name__ == "__main__":
    main()
