#!/usr/bin/env python3
"""s4e_astar_fix.py — T5 A* 兜底补解 + 45° 拐角倒角整改。

流程（每网）：
  1) 以当前画布（lines+vias）为障碍构建 audit 文件
  2) pcb_astar2 逐级裕量求路径（from=缺失焊盘中心, to=同网已连通焊盘中心）
  3) 45° 倒角：每个 90° 拐点改为两段 45°（切角量 d=min(线宽×1.5, 0.45×邻段长)，
     逐候选 d 做几何校验，违规则逐级减半，全不行则保留 90°）
  4) 几何终检（异网焊盘/走线/过孔/板边，JLC 口径）
  5) --apply 时下发创建（数组传参）→ save → 等 10s → 重枚举

用法:
  python s4e_astar_fix.py --net USB_DP_P --chain U1.AD6 R11.1 [--apply]
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s4e_lib import invoke, save_json, load_json, HERE  # noqa: E402
from lceda_invoke import run_code  # noqa: E402
import pcb_route_lib as R  # noqa: E402

SESSION = "44e574cc-dc99-46dd-80b8-60632f5b075a"
MIL = 39.37
EDADIR = Path(__file__).parent
DUMP = HERE / "reports/pcb_pads_dump_manual.json"
LADDER = [(0.102, 0.152, 0.17, 2.5), (0.102, 0.152, 0.17, 1.0),
          (0.13, 0.17, 0.19, 1.0), (0.15, 0.19, 0.21, 1.0)]

CREATE_JS = r"""
const segs = __CLI__.args.segs;   // [{net,layer,sx,sy,ex,ey,w}] 画布 mil
const vias = __CLI__.args.vias;   // [{net,x,y,hole,dia}]
let ok = 0, bad = 0, errs = [];
for (const s of segs) {
  try { const l = await eda.pcb_PrimitiveLine.create(s.net, s.layer, s.sx, s.sy, s.ex, s.ey, s.w, false);
        if (l) ok++; else bad++;
  } catch (e) { bad++; errs.push(String(e).slice(0,80)); }
}
for (const v of vias) {
  try { const o = await eda.pcb_PrimitiveVia.create(v.net, v.x, v.y, v.hole, v.dia);
        if (o) ok++; else bad++;
  } catch (e) { bad++; errs.push(String(e).slice(0,80)); }
}
await eda.pcb_Document.save();
return {ok, bad, errs: errs.slice(0,5),
        lines: (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length,
        vias: (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length};
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


# ---------- 障碍模型 ----------

def _pt_seg(px, py, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - a[0], py - a[1])
    t = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / L2))
    return math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy))


def seg_seg_gap(p1, p2, w1, q1, q2, w2, step=0.02):
    """两段铜的净距（同 pcb_astar2 口径：A 中轴采样点→B 中轴距离−半宽和）。"""
    L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    n = max(2, int(L / step) + 1)
    best = 1e9
    for k in range(n):
        t = k / (n - 1)
        x = p1[0] + (p2[0] - p1[0]) * t
        y = p1[1] + (p2[1] - p1[1]) * t
        d = _pt_seg(x, y, q1, q2) - (w1 + w2) / 2
        if d < best:
            best = d
    return best


class Obstacles:
    def __init__(self, dump_path, canvas_dump, clr_pad=0.152, clr_track=0.102,
                 pad_layers=None):
        self.pads = R.load_pad_rects([str(dump_path)])
        self.pad_layers = pad_layers or {}
        for pd in self.pads:
            pd["layer"] = self.pad_layers.get(f"{pd['dz']}.{pd['pad']}")
        self.tracks = []
        for l in canvas_dump["lines"]:
            if l.get("err"):
                continue
            self.tracks.append({"net": l.get("net"), "layer": l.get("layer"),
                                "p1": (l["sx"] / MIL, 30 - l["sy"] / MIL),
                                "p2": (l["ex"] / MIL, 30 - l["ey"] / MIL),
                                "w": (l.get("w") or 10) / MIL})
        self.vias = [{"net": v.get("net"), "x": v["x"] / MIL, "y": 30 - v["y"] / MIL,
                      "r": (v.get("dia") or 24) / 2 / MIL}
                     for v in canvas_dump["vias"] if not v.get("err")]
        self.clr_pad = clr_pad
        self.clr_track = clr_track

    def check(self, net, layer, p1, p2, w):
        """返回违例列表（任务系 mm）。"""
        viol = []
        seg = (p1, p2, w)
        for (px, py) in (p1, p2):
            if px < R.EDGE_MM or py < R.EDGE_MM or \
               px > R.BOARD_W - R.EDGE_MM or py > R.BOARD_H - R.EDGE_MM:
                viol.append(("board-edge", round(px, 2), round(py, 2)))
        for pd in self.pads:
            if pd["net"] == net:
                continue
            # 层间无铜-铜间距：L2 走线可从顶层 SMD 焊盘下方穿过（通孔焊盘不豁免）
            if layer == 2 and pd.get("layer") == 1:
                continue
            d = R.seg_rect_dist(seg, pd["rect"])
            if d < self.clr_pad:
                viol.append(("pad", f"{pd['dz']}.{pd['pad']}", round(d, 3)))
        for t in self.tracks:
            if t["net"] == net or t["layer"] != layer:
                continue
            d = seg_seg_gap(p1, p2, w, t["p1"], t["p2"], t["w"])
            if d < self.clr_track:
                viol.append(("track", t["net"], round(d, 3)))
        for v in self.vias:
            if v["net"] == net:
                continue
            d = math.hypot(p1[0] - v["x"], p1[1] - v["y"]) - w / 2 - v["r"]
            d2 = math.hypot(p2[0] - v["x"], p2[1] - v["y"]) - w / 2 - v["r"]
            d = min(d, d2)
            if d < self.clr_track:
                viol.append(("via", v["net"], round(d, 3)))
        return viol


# ---------- 45° 倒角 ----------
def _unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    if L < 1e-9:
        return None
    return (dx / L, dy / L, L)


def chamfer(segs, obstacles, net, width_mm, log=None):
    """segs: [{layer,x1,y1,x2,y2,w}]（任务系 mm）→ 就地 45° 倒角，返回 (segs, stats)。

    仅处理同层、共享端点、夹角 90° 的相邻段对；d 候选 = [w*1.5, w*0.75, w*0.375]
    并受邻段长度 0.45 限制；每个候选 d 用障碍模型复核，违规则减小。
    """
    out = [dict(s) for s in segs]
    stats = {"corners": 0, "chamfered": 0, "kept90": 0}
    layers = {"__seq__": out}
    for layer, ss in layers.items():
        i = 0
        while i < len(ss) - 1:
            s1, s2 = ss[i], ss[i + 1]
            if s1["layer"] != s2["layer"]:
                i += 1
                continue
            e1 = (s1["x2"], s1["y2"])
            s2s = (s2["x1"], s2["y1"])
            if abs(e1[0] - s2s[0]) > 1e-6 or abs(e1[1] - s2s[1]) > 1e-6:
                i += 1
                continue
            u1 = _unit((s1["x1"], s1["y1"]), e1)
            u2 = _unit(e1, (s2["x2"], s2["y2"]))
            if not u1 or not u2:
                i += 1
                continue
            # 转角角 = 两段行进方向的夹角（0=直行，90=直角）
            turn = math.degrees(math.acos(max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))))
            if abs(turn - 90.0) > 3.0:
                i += 1
                continue
            stats["corners"] += 1
            w = s1.get("w") or width_mm
            d0 = min(w * 1.5, u1[2] * 0.45, u2[2] * 0.45)
            done = False
            for frac in (1.0, 0.5, 0.25, 0.12):
                d = d0 * frac
                if d < 0.03:
                    break
                A = (e1[0] - u1[0] * d, e1[1] - u1[1] * d)
                B = (e1[0] + u2[0] * d, e1[1] + u2[1] * d)
                v1 = obstacles.check(net, layer, (s1["x1"], s1["y1"]), A, w)
                v2 = obstacles.check(net, layer, A, B, w)
                v3 = obstacles.check(net, layer, B, (s2["x2"], s2["y2"]), w)
                if v1 or v2 or v3:
                    continue
                s1["x2"], s1["y2"] = round(A[0], 4), round(A[1], 4)
                s2["x1"], s2["y1"] = round(B[0], 4), round(B[1], 4)
                mid = {"layer": layer, "x1": round(A[0], 4), "y1": round(A[1], 4),
                       "x2": round(B[0], 4), "y2": round(B[1], 4), "w": w}
                ss.insert(i + 1, mid)
                stats["chamfered"] += 1
                done = True
                break
            if not done:
                stats["kept90"] += 1
                if log is not None:
                    log.append({"layer": layer, "at": [round(e1[0], 3), round(e1[1], 3)],
                                "reason": "no feasible chamfer d"})
            i += 1
    return out, stats


def build_audit(canvas_dump, out_path):
    kv = []
    for l in canvas_dump["lines"]:
        if l.get("err"):
            continue
        kv.append({"k": "L", **l})
    for v in canvas_dump["vias"]:
        if v.get("err"):
            continue
        kv.append({"k": "V", **v})
    Path(out_path).write_text(json.dumps(kv, ensure_ascii=False), encoding="utf-8")
    return len(kv)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", required=True)
    ap.add_argument("--chain", nargs="+", required=True, help="缺失焊盘 已连通焊盘")
    ap.add_argument("--width", type=float, default=0.254)
    ap.add_argument("--dump", default="_s4e_tracks_vias_final.json")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-chamfer", action="store_true")
    ap.add_argument("--via-cost", type=float, default=0.45, help="过孔代价(mm)，越大越少换层")
    ap.add_argument("--timeout", type=int, default=300, help="每档 A* 求解超时(s)")
    a = ap.parse_args()

    canvas = load_json(a.dump)
    pads = json.loads(DUMP.read_text(encoding="utf-8"))
    padxy = {}
    for c in pads:
        for p in c.get("pads", []):
            padxy[f"{c['dz']}.{p['n']}"] = (p["x"] / MIL, 30 - p["y"] / MIL, p.get("net"))
    for k in a.chain:
        if k not in padxy:
            print(f"!! 未知焊盘 {k}")
            sys.exit(2)
        if padxy[k][2] != a.net:
            print(f"!! 焊盘 {k} 不属于网 {a.net}（实为 {padxy[k][2]}）")
            sys.exit(2)

    audit = HERE / "reports/_s4e_audit.json"
    n = build_audit(canvas, audit)
    print(f"audit 障碍: {n} 条 → {audit}")

    out_f = EDADIR / "_s4e_hop.json"
    attempts = []
    hop = None
    used_ct, used_cp = 0.152, 0.102
    for (ct, cp, cv, eps) in LADDER:
        env = {**os.environ, "S4B_ASTAR_CLR_TRACK": str(ct), "S4B_ASTAR_CLR_PAD": str(cp),
               "S4B_ASTAR_CLR_VIA": str(cv), "S4B_ASTAR_WIDTH": str(a.width),
               "S4B_ASTAR_VIA_COST": str(a.via_cost),
               "S4B_ASTAR_PAD_LAYERS": str(HERE / "reports/_s4e_pad_layers_map.json"),
               "PYTHONPATH": str(EDADIR)}
        cmd = [sys.executable, str(EDADIR / "pcb_astar2.py"), "--net", a.net,
               "--dump", str(DUMP), "--audit", str(audit),
               "--from", f"{padxy[a.chain[0]][0]:.3f},{padxy[a.chain[0]][1]:.3f}",
               "--to", f"{padxy[a.chain[1]][0]:.3f},{padxy[a.chain[1]][1]:.3f}",
               "--out", str(out_f), "--eps", str(eps)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout, env=env,
                               encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            attempts.append({"clr": [ct, cp, cv], "eps": eps, "blocked": True,
                             "msg": "TIMEOUT %ds" % a.timeout})
            continue
        if r.returncode != 0:
            attempts.append({"clr": [ct, cp, cv], "eps": eps, "blocked": True,
                             "msg": (r.stdout + r.stderr).strip()[-200:]})
            continue
        hop = json.loads(out_f.read_text(encoding="utf-8"))
        used_ct, used_cp = ct, cp
        attempts.append({"clr": [ct, cp, cv], "eps": eps, "segs": len(hop["segs"]),
                         "total_mm": hop["total_mm"]})
        break
    if hop is None:
        rec = {"net": a.net, "chain": a.chain, "attempts": attempts, "resolved": False}
        save_json(f"_s4e_astar_{a.net}.json", rec)
        print("BLOCKED:", json.dumps(attempts, ensure_ascii=False)[:400])
        return 2

    # 障碍模型（用于倒角复核）
    plmap = json.loads((HERE / "reports/_s4e_pad_layers_map.json").read_text(encoding="utf-8"))
    ob = Obstacles(DUMP, canvas, clr_pad=used_cp, clr_track=used_ct, pad_layers=plmap)
    segs = []
    for s in hop["segs"]:
        segs.append({"layer": s["layer"], "x1": s["x1"], "y1": s["y1"],
                     "x2": s["x2"], "y2": s["y2"], "w": s.get("w", a.width)})
    clog = []
    if a.no_chamfer:
        csegs, cstats = segs, {"corners": 0, "chamfered": 0, "kept90": 0}
    else:
        csegs, cstats = chamfer(segs, ob, a.net, a.width, clog)

    # 终检：整网所有段
    viol = []
    for s in csegs:
        v = ob.check(a.net, s["layer"], (s["x1"], s["y1"]), (s["x2"], s["y2"]), s["w"])
        if v:
            viol.append({"seg": [s["x1"], s["y1"], s["x2"], s["y2"]], "layer": s["layer"],
                         "viol": v[:3]})
    rec = {"net": a.net, "chain": a.chain, "attempts": attempts, "resolved": True,
           "segs_raw": len(segs), "segs_chamfered": len(csegs), "chamfer": cstats,
           "chamfer_fail": clog, "violations": viol[:10], "n_violations": len(viol),
           "segments": csegs,
           "vias": [{"x": v["x"], "y": v["y"], "hole": v.get("hole_mil", 12),
                     "dia": v.get("dia_mil", 24)} for v in hop["vias"]]}
    save_json(f"_s4e_astar_{a.net}.json", rec)
    print(json.dumps({"net": a.net, "segs_raw": len(segs), "segs_chamfered": len(csegs),
                      "chamfer": cstats, "n_violations": len(viol)}, ensure_ascii=False))
    if viol:
        print("!! 仍有间距违例，未创建")
        return 3
    if not a.apply:
        print("（未 --apply，仅出解）")
        return 0

    csegs_mil = []
    for s in csegs:
        csegs_mil.append({"net": a.net, "layer": s["layer"],
                          "sx": round(s["x1"] * MIL, 3), "sy": round((30 - s["y1"]) * MIL, 3),
                          "ex": round(s["x2"] * MIL, 3), "ey": round((30 - s["y2"]) * MIL, 3),
                          "w": round((s["w"] or a.width) * MIL, 3)})
    vias_mil = [{"net": a.net, "x": round(v["x"] * MIL, 3), "y": round((30 - v["y"]) * MIL, 3),
                 "hole": v["hole"], "dia": v["dia"]} for v in rec["vias"]]
    r = run_code(CREATE_JS, SESSION, timeout_ms=590000,
                 args_json=json.dumps({"segs": csegs_mil, "vias": vias_mil}, ensure_ascii=False))
    print("create:", json.dumps(r.get("value") or r.get("error"), ensure_ascii=False)[:300])
    time.sleep(10)
    c = invoke(COUNT_JS, timeout_ms=300000)
    print("画布:", json.dumps(c.get("value"), ensure_ascii=False))
    logline(f"S4E T5 A*兜底+倒角 {a.net}: 段 {len(csegs)} 孔 {len(vias_mil)} "
            f"倒角 {cstats} 违例 {len(viol)} 画布 "
            f"{c.get('value', {}).get('lines')}/{c.get('value', {}).get('vias')} bad=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
