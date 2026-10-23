#!/usr/bin/env python3
"""s5_window.py — 局部几何窗口 ASCII 地图（任务系 mm）。

用法: python s5_window.py --pad AD2 --cx 25.2476 --cy 16.7488 --r 2.0
输出文本到 stdout。
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s5_lib import load_json  # noqa: E402

MIL = 39.3700787402
CH = " .:-=+*#%@"


def render(snap, cx, cy, r, step=0.1):
    W = int(2 * r / step)
    grid = [[" "] * W for _ in range(W)]
    layers = {1: [[], []], 2: [[], []]}

    def put(x, y, ch, layer):
        ix = int((x - (cx - r)) / step)
        iy = int(((cy + r) - y) / step)
        if 0 <= ix < W and 0 <= iy < W:
            grid[iy][ix] = ch

    for L in snap["lines"]:
        if L.get("err"):
            continue
        lay = L.get("layer")
        if lay not in (1, 2):
            continue
        net = L.get("net") or "-"
        ax, ay = L["sx"] / MIL, 30 - L["sy"] / MIL
        bx, by = L["ex"] / MIL, 30 - L["ey"] / MIL
        n = max(2, int(math.hypot(bx - ax, by - ay) / (step / 3)))
        for i in range(n + 1):
            t = i / n
            put(ax + (bx - ax) * t, ay + (by - ay) * t, net[0].upper() if net != "GND" else "G", lay)
    for V in snap["vias"]:
        if V.get("err"):
            continue
        net = V.get("net") or "-"
        put(V["x"] / MIL, 30 - V["y"] / MIL, (net[0].upper() if net != "GND" else "G"), 1)
    for P in snap["pads"]:
        if P.get("err"):
            continue
        sh = P.get("pad") or []
        w = sh[1] / MIL if len(sh) > 1 else 0.25
        h = sh[2] / MIL if len(sh) > 2 else w
        ell = (sh[0] == "ELLIPSE") if sh else True
        net = P.get("net") or "-"
        px, py = P["x"] / MIL, 30 - P["y"] / MIL
        if ell:
            for a in range(0, 360, 30):
                for rr in (max(w, h) / 2,):
                    put(px + rr * math.cos(math.radians(a)), py + rr * math.sin(math.radians(a)),
                        (net[0].upper() if net != "GND" else "G"), 1)
            put(px, py, (net[0].upper() if net != "GND" else "G"), 1)
        else:
            for i in range(int(-w / 2 / step), int(w / 2 / step) + 1):
                for j in range(int(-h / 2 / step), int(h / 2 / step) + 1):
                    put(px + i * step, py + j * step, (net[0].upper() if net != "GND" else "G"), 1)
    return grid, W


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cx", type=float, required=True)
    ap.add_argument("--cy", type=float, required=True)
    ap.add_argument("--r", type=float, default=2.0)
    ap.add_argument("--step", type=float, default=0.1)
    ap.add_argument("--snap", default="_s5_snapshot_pre_escape.json")
    a = ap.parse_args()
    snap = load_json(a.snap)
    grid, W = render(snap, a.cx, a.cy, a.r, a.step)
    print(f"x {a.cx - a.r:.2f} .. {a.cx + a.r:.2f}   y {a.cy - a.r:.2f} .. {a.cy + a.r:.2f} (step {a.step})")
    hdr = "".join(str(int((a.cx - a.r) + i * a.step) % 10) for i in range(W))
    print("      " + hdr)
    for j, row in enumerate(grid):
        y = (a.cy + a.r) - j * a.step
        print(f"{y:6.2f} " + "".join(row))


if __name__ == "__main__":
    main()
