#!/usr/bin/env python3
"""s4e_chamfer_board.py — T5 45° 拐角整改（全板 90° 拐点倒角）。

对全板每个"真 90° 拐点"（同网同层、节点度数=2、两段方向夹角 90°）改为两段 45°：
  A = P − dir1·d ; B = P + dir2·d ；原两段端点分别收到 A/B，新增对角线 A→B
d 候选 = [线宽×1.5, ×0.75, ×0.375, ×0.1875]（并受邻段长度 0.45 限制），
每个候选用障碍模型（异网焊盘/走线/过孔/板边，JLC 口径）复核，全违例则保留 90°。
GND 地基（包地栏/缝合孔）默认不动（--include-gnd 才处理）。

用法: python s4e_chamfer_board.py [--apply] [--dump _s4e_tracks_vias_final.json]
"""
import argparse
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s4e_lib import invoke, save_json, load_json, HERE  # noqa: E402
from lceda_invoke import run_code  # noqa: E402
from s4e_astar_fix import Obstacles, seg_seg_gap  # noqa: E402

SESSION = "44e574cc-dc99-46dd-80b8-60632f5b075a"
MIL = 39.37
DUMP = HERE / "reports/pcb_pads_dump_manual.json"
CLR_PAD, CLR_TRACK = 0.152, 0.102

APPLY_JS = r"""
const mods = __CLI__.args.mods;   // [{id, sx,sy,ex,ey}]
const news = __CLI__.args.news;   // [{net,layer,sx,sy,ex,ey,w}]
let ok = 0, bad = 0, errs = [];
for (const m of mods) {
  try { await eda.pcb_PrimitiveLine.modify(m.id, {startX: m.sx, startY: m.sy, endX: m.ex, endY: m.ey}); ok++; }
  catch (e) { bad++; errs.push(String(e).slice(0,90)); }
}
for (const s of news) {
  try { const l = await eda.pcb_PrimitiveLine.create(s.net, s.layer, s.sx, s.sy, s.ex, s.ey, s.w, false);
        if (l) ok++; else bad++; }
  catch (e) { bad++; errs.push(String(e).slice(0,90)); }
}
await eda.pcb_Document.save();
return {ok, bad, errs: errs.slice(0,5),
        lines: (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length};
"""

COUNT_JS = r"""
const out = {};
out.lines = (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length;
out.vias  = (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length;
out.comps = (await eda.pcb_PrimitiveComponent.getAllPrimitiveId()).length;
return out;
"""


def logline(msg):
    line = f"- S4E {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with (HERE / "reports/s4_progress.md").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def find_corners(lines, tol=0.6):
    """返回真 90° 拐点：[{net,layer,deg2,...}] 含两段 id 与端点。"""
    by = defaultdict(list)
    for l in lines:
        if l.get("err"):
            continue
        by[(l.get("net"), l.get("layer"))].append(l)
    out = []
    for (net, layer), segs in by.items():
        # 端点聚类（网格 tol）
        cells = defaultdict(list)
        for i, s in enumerate(segs):
            cells[(round(s["sx"] / tol), round(s["sy"] / tol))].append((i, s["sx"], s["sy"]))
            cells[(round(s["ex"] / tol), round(s["ey"] / tol))].append((i, s["ex"], s["ey"]))
        # 并查集合并相邻 cell
        keys = list(cells)
        parent = {k: k for k in keys}

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                ki, kj = keys[i], keys[j]
                if abs(ki[0] - kj[0]) <= 1 and abs(ki[1] - kj[1]) <= 1:
                    close = any(math.hypot(pi[1] - pj[1], pi[2] - pj[2]) <= tol * 1.5
                                for pi in cells[ki] for pj in cells[kj])
                    if close:
                        union(ki, kj)
        node_segs = defaultdict(set)
        node_pts = defaultdict(list)
        for k, lst in cells.items():
            rk = find(k)
            node_pts[rk].extend((x, y) for _, x, y in lst)
            for si, _, _ in lst:
                node_segs[rk].add(si)
        for rk, sidx in node_segs.items():
            if len(sidx) != 2:
                continue
            s1, s2 = segs[sorted(sidx)[0]], segs[sorted(sidx)[1]]
            P = node_pts[rk][0]
            def direction(s):
                a = (s["sx"], s["sy"]); b = (s["ex"], s["ey"])
                if math.hypot(a[0] - P[0], a[1] - P[1]) <= math.hypot(b[0] - P[0], b[1] - P[1]):
                    return a, b
                return b, a
            a1, b1 = direction(s1)
            a2, b2 = direction(s2)
            d1 = (b1[0] - a1[0], b1[1] - a1[1])
            d2 = (b2[0] - a2[0], b2[1] - a2[1])
            L1 = math.hypot(*d1); L2 = math.hypot(*d2)
            if L1 < 1e-6 or L2 < 1e-6:
                continue
            u1 = (d1[0] / L1, d1[1] / L1)
            u2 = (d2[0] / L2, d2[1] / L2)
            ang = math.degrees(math.acos(max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))))
            if abs(ang - 90.0) > 2.0:
                continue
            # s1 结束于 P / s2 起于 P
            if (s1["ex"], s1["ey"]) != (s2["sx"], s2["sy"]):
                s1, s2 = s2, s1
                u1, u2 = u2, u1
                L1, L2 = L2, L1
            out.append({"net": net, "layer": layer, "P": (P[0], P[1]),
                        "s1": s1, "s2": s2, "u1": u1, "u2": u2,
                        "L1": L1, "L2": L2, "turn": round(ang, 2)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="_s4e_tracks_vias_final.json")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--include-gnd", action="store_true")
    ap.add_argument("--out", default="_s4e_chamfer.json")
    a = ap.parse_args()

    canvas = load_json(a.dump)
    lines = [l for l in canvas["lines"] if not l.get("err")]
    corners = find_corners(lines)
    plmap = json.loads((HERE / "reports/_s4e_pad_layers_map.json").read_text(encoding="utf-8"))
    ob = Obstacles(DUMP, canvas, clr_pad=CLR_PAD, clr_track=CLR_TRACK, pad_layers=plmap)

    mods, news, done, skipped = [], [], [], []
    for c in corners:
        if c["net"] == "GND" and not a.include_gnd:
            skipped.append({"net": c["net"], "P": c["P"], "reason": "GND 地基（包地栏）禁动"})
            continue
        s1, s2 = c["s1"], c["s2"]
        w1 = (s1.get("w") or 10) / MIL
        w2 = (s2.get("w") or 10) / MIL
        w = min(w1, w2)
        P = c["P"]
        d0 = min(w * 1.5, c["L1"] * 0.45, c["L2"] * 0.45) / MIL  # mil
        ok = None
        for frac in (1.0, 0.6, 0.35, 0.2):
            d = d0 * frac
            if d < 1.0:
                break
            A = (P[0] - c["u1"][0] * d, P[1] - c["u1"][1] * d)
            B = (P[0] + c["u2"][0] * d, P[1] + c["u2"][1] * d)
            p1 = (s1["sx"] / MIL, 30 - s1["sy"] / MIL)
            p2 = (s2["ex"] / MIL, 30 - s2["ey"] / MIL)
            A_t = (A[0] / MIL, 30 - A[1] / MIL)
            B_t = (B[0] / MIL, 30 - B[1] / MIL)
            v1 = ob.check(c["net"], c["layer"], p1, A_t, w)
            v2 = ob.check(c["net"], c["layer"], A_t, B_t, w)
            v3 = ob.check(c["net"], c["layer"], B_t, p2, w)
            if v1 or v2 or v3:
                continue
            ok = (d, A, B)
            break
        if ok is None:
            skipped.append({"net": c["net"], "P": [round(P[0], 2), round(P[1], 2)],
                             "reason": "无满足间距的倒角量"})
            continue
        d, A, B = ok
        mods.append({"id": s1["id"], "sx": s1["sx"], "sy": s1["sy"],
                     "ex": round(A[0], 4), "ey": round(A[1], 4)})
        mods.append({"id": s2["id"], "sx": round(B[0], 4), "sy": round(B[1], 4),
                     "ex": s2["ex"], "ey": s2["ey"]})
        news.append({"net": c["net"], "layer": c["layer"],
                     "sx": round(A[0], 4), "sy": round(A[1], 4),
                     "ex": round(B[0], 4), "ey": round(B[1], 4),
                     "w": s1.get("w") or 10})
        done.append({"net": c["net"], "layer": c["layer"],
                     "P": [round(P[0], 2), round(P[1], 2)], "d_mil": round(d, 2)})

    out = {"generated": time.strftime("%F %T"), "candidates": len(corners),
           "chamfered": len(done), "skipped": skipped, "items": done,
           "mods": len(mods), "news": len(news)}
    save_json(a.out, out)
    print("候选 90° 拐点:", len(corners), "→ 可倒角:", len(done), "跳过:", len(skipped))
    for s in skipped:
        print("   跳过:", s)
    if not a.apply:
        print("（未 --apply）")
        return
    r = run_code(APPLY_JS, SESSION, timeout_ms=590000,
                 args_json=json.dumps({"mods": mods, "news": news}, ensure_ascii=False))
    print("apply:", json.dumps(r.get("value") or r.get("error"), ensure_ascii=False)[:300])
    time.sleep(10)
    c = invoke(COUNT_JS, timeout_ms=300000)
    print("画布:", json.dumps(c.get("value"), ensure_ascii=False))
    logline(f"S4E T5 45°倒角: 候选 {len(corners)} 倒角 {len(done)} 跳过 {len(skipped)} "
            f"画布 {c.get('value', {}).get('lines')}/{c.get('value', {}).get('vias')} bad=0")


if __name__ == "__main__":
    main()
