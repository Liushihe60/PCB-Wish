#!/usr/bin/env python3
"""s4c_route2.py — 单 hop 可达性探测（in-process astar2, eps=1.0 可采纳=完备）。

用法:
  python s4c_route2.py --net NAME --frm x,y --to x,y [--width 0.254] [--clr 0.102 0.152 0.17]
                       [--out path]
对 (frm→to) 以给定线宽/间距找一条路径；--out 给出时写 hop 片段
{segs:[{layer,x1,y1,x2,y2,w}], vias:[{x,y,dia_mil,hole_mil}]}（与 _link_hop.json 同构）。
"""
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path("E:/agent project/projects/flexiheal-v1")
EDADIR = Path(__file__).parent
sys.path.insert(0, str(EDADIR))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", required=True)
    ap.add_argument("--frm", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--width", type=float, default=0.254)
    ap.add_argument("--clr", nargs=3, type=float, default=[0.102, 0.152, 0.17])
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    os.environ["S4B_ASTAR_CLR_TRACK"] = str(a.clr[0])
    os.environ["S4B_ASTAR_CLR_PAD"] = str(a.clr[1])
    os.environ["S4B_ASTAR_CLR_VIA"] = str(a.clr[2])
    os.environ["S4B_ASTAR_WIDTH"] = str(a.width)
    import pcb_astar2 as A2
    A2.TRACK_W = a.width
    A2.CLR = a.clr[0]
    A2.CLR_PAD_ACT = a.clr[1]
    A2.CLR_VIA = a.clr[2]
    state = json.loads((HERE / "reports/_s4b_state.json").read_text(encoding="utf-8"))
    import tempfile
    tmp = Path(tempfile.gettempdir()) / "_s4c_route2_audit.json"
    tmp.write_text(json.dumps([{"k": "L", **x} for x in state["lines"]] +
                              [{"k": "V", **x} for x in state["vias"]]), encoding="utf-8")
    pads, tracks, vias = A2.load_state(str(HERE / "reports/pcb_pads_dump_manual.json"), str(tmp))
    m = A2.Model(pads, tracks, vias, a.net)
    s = tuple(float(v) for v in a.frm.split(","))
    g = tuple(float(v) for v in a.to.split(","))
    path, ok = A2.astar2(m, s, g, grid=0.05, eps=1.0)
    if not ok:
        print(json.dumps({"net": a.net, "frm": a.frm, "to": a.to, "blocked": True}, ensure_ascii=False))
        sys.exit(1)
    keep = A2.simplify(path)
    segs, via_pts, cur_layer, prev, last_dir = [], [], keep[0][1], keep[0], None
    for st in keep[1:]:
        (ix, iy), layer = st
        if layer != cur_layer:
            via_pts.append((prev[0][0] * A2.GRID, prev[0][1] * A2.GRID))
            cur_layer = layer
            last_dir = None
        else:
            pa = (prev[0][0] * A2.GRID, prev[0][1] * A2.GRID)
            pb = (st[0][0] * A2.GRID, st[0][1] * A2.GRID)
            d = (st[0][0] - prev[0][0], st[0][1] - prev[0][1])
            if (last_dir == d and segs and segs[-1]["layer"] == cur_layer
                    and abs(segs[-1]["x2"] - pa[0]) < 1e-6 and abs(segs[-1]["y2"] - pa[1]) < 1e-6):
                segs[-1]["x2"], segs[-1]["y2"] = round(pb[0], 3), round(pb[1], 3)
            else:
                segs.append({"layer": cur_layer, "x1": round(pa[0], 3), "y1": round(pa[1], 3),
                             "x2": round(pb[0], 3), "y2": round(pb[1], 3), "w": a.width})
            last_dir = d
        prev = st
    out = {"net": a.net, "segs": [s for s in segs if abs(s["x2"] - s["x1"]) > 1e-9 or abs(s["y2"] - s["y1"]) > 1e-9],
           "vias": [{"x": round(v[0], 3), "y": round(v[1], 3), "dia_mil": 16, "hole_mil": 12} for v in via_pts]}
    if a.out:
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"net": a.net, "ok": True, "segs": len(out["segs"]), "vias": len(out["vias"])},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
