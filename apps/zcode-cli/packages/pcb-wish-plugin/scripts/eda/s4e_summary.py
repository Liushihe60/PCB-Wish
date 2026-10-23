#!/usr/bin/env python3
"""s4e_summary.py — 快照/状态摘要（不把大 JSON 读进上下文）。

用法: python s4e_summary.py [--file reports/_s4e_snapshot_pre_auto.json]
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s4e_lib import HERE, corner_histogram, classify_corner, turn_angle, seg_angle  # noqa: E402


def summarize(v):
    lines = v.get("lines", [])
    vias = v.get("vias", [])
    pernet = Counter(l.get("net") for l in lines)
    pernetv = Counter(x.get("net") for x in vias)
    widths = defaultdict(Counter)
    layers = Counter()
    for l in lines:
        widths[l.get("net")][l.get("w")] += 1
        layers[l.get("layer")] += 1
    hist, corner_pts = corner_histogram(lines)
    print(f"counts: lines={len(lines)} vias={len(vias)} regions={len(v.get('regions',[]))} "
          f"comps={len(v.get('components',[]))}")
    print(f"per-net lines: {dict(pernet)}")
    print(f"per-net vias : {dict(pernetv)}")
    print(f"layers: {dict(layers)}")
    print(f"corner hist: {hist['by_class']} total={hist['total_corners']} "
          f"ratio90={hist['ratio90']:.3f} ratio45={hist['ratio45']:.3f}")
    # 每网拐角
    pernet90 = Counter(c["net"] for c in corner_pts if c["cat"] == "90")
    print(f"per-net 90deg corners: {dict(pernet90)}")
    print("net widths:", {k: dict(vv) for k, vv in sorted(widths.items())})
    return hist, corner_pts


def drift():
    ref = HERE / "reports/pcb_pads_dump_manual.json"
    if not ref.exists():
        print("!! 缺 pcb_pads_dump_manual.json")
        return
    r = json.loads(ref.read_text(encoding="utf-8"))
    base = r if isinstance(r, list) else (r.get("components") or r.get("comps") or [])
    ref_map = {}
    for c in base:
        d = c.get("designator") or c.get("name")
        ref_map[d] = (round(float(c.get("x", 0)), 3), round(float(c.get("y", 0)), 3),
                      round(float(c.get("rotation", 0)) % 360, 2))
    snap = json.loads((HERE / "reports/_s4e_snapshot_pre_auto.json").read_text(encoding="utf-8"))
    now_map = {}
    for c in snap.get("components", []):
        now_map[c.get("designator")] = (round(float(c.get("x", 0)), 3), round(float(c.get("y", 0)), 3),
                                        round(float(c.get("rotation", 0)) % 360, 2))
    only_ref = set(ref_map) - set(now_map)
    only_now = set(now_map) - set(ref_map)
    diff = {k: (ref_map[k], now_map[k]) for k in set(ref_map) & set(now_map) if ref_map[k] != now_map[k]}
    print(f"零漂移核对: ref={len(ref_map)} now={len(now_map)} 仅ref={sorted(only_ref)} "
          f"仅now={sorted(only_now)} 位移={len(diff)}")
    if diff:
        print("  位移明细(前20):", json.dumps(dict(list(diff.items())[:20]), ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="_s4e_snapshot_pre_auto.json")
    ap.add_argument("--drift", action="store_true")
    a = ap.parse_args()
    v = json.loads((HERE / "reports" / a.file).read_text(encoding="utf-8"))
    summarize(v)
    if a.drift:
        drift()
