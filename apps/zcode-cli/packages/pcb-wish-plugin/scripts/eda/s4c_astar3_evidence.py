#!/usr/bin/env python3
"""s4c_astar3_evidence.py — Q14/持有网 astar3（最松口径 Dijkstra）不可达证据生成器。

对 net+chain 逐 hop 调 pcb_astar3（CLR=0.003/0.224 现状宽，最松已证 DRC 通过口径），
audit=当前 _s4b_state.json + 前序 hop 产物。全 blocked → 证据 reports/_s4c_q14_astar3_<net>.json。
用途：持有网"最新不可达证据"（astar2 严格档 + astar3 最松档双引擎）。
"""
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

HERE = Path("E:/agent project/projects/flexiheal-v1")
EDADIR = Path(__file__).parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", required=True)
    ap.add_argument("--chain", nargs="+", required=True)
    args = ap.parse_args()
    state = json.loads((HERE / "reports/_s4b_state.json").read_text(encoding="utf-8"))
    audit_cache = EDADIR / "_link_q14_audit.json"

    def write_audit():
        audit_cache.write_text(json.dumps(
            [{"k": "L", **x} for x in state["lines"]] +
            [{"k": "V", **x} for x in state["vias"]], ensure_ascii=False), encoding="utf-8")

    # pad 坐标
    import s4b_model  # noqa: E402
    m = s4b_model.Model()
    by_key = {f"{p['dz']}.{p['pad']}": p for p in m.pads}
    missing = [k for k in args.chain if k not in by_key]
    if missing:
        print(json.dumps({"net": args.net, "error": f"unknown pads {missing}"}, ensure_ascii=False))
        sys.exit(2)
    write_audit()
    segs_all, vias_all, logs = [], [], []
    blocked_at = None
    for i in range(len(args.chain) - 1):
        cur, nxt = args.chain[i], args.chain[i + 1]
        frm = f"{by_key[cur]['x']:.3f},{by_key[cur]['y']:.3f}"
        to = f"{by_key[nxt]['x']:.3f},{by_key[nxt]['y']:.3f}"
        out_f = EDADIR / "_link_hop3.json"
        env = {"PATH": __import__("os").environ.get("PATH", ""),
               "PYTHONPATH": str(EDADIR)}
        r = subprocess.run([sys.executable, str(EDADIR / "pcb_astar3.py"),
                            "--net", args.net, "--dump", str(HERE / "reports/pcb_pads_dump_manual.json"),
                            "--audit", str(audit_cache), "--from", frm, "--to", to,
                            "--out", str(out_f)],
                           capture_output=True, text=True, timeout=1800,
                           cwd=str(HERE), env=env, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            logs.append({"hop": f"{cur}>{nxt}", "blocked": True})
            blocked_at = f"{cur}>{nxt}"
            break
        hop = json.loads(out_f.read_text(encoding="utf-8"))
        segs_all += hop["segs"]
        vias_all += hop["vias"]
        for sg in hop["segs"]:
            state["lines"].append({"id": f"ev3_{args.net}_{len(state['lines'])}", "net": args.net,
                                   "layer": sg["layer"], "w": round(sg.get("w", 0.224) * 39.37, 2),
                                   "sx": round(sg["x1"] * 39.37, 2), "sy": round((30.0 - sg["y1"]) * 39.37, 2),
                                   "ex": round(sg["x2"] * 39.37, 2), "ey": round((30.0 - sg["y2"]) * 39.37, 2)})
        for v in hop["vias"]:
            state["vias"].append({"id": f"ev3v_{args.net}_{len(state['vias'])}", "net": args.net,
                                  "x": round(v["x"] * 39.37, 2), "y": round((30.0 - v["y"]) * 39.37, 2),
                                  "hole": 12, "dia": 16})
        write_audit()
    audit_cache.unlink(missing_ok=True)
    spec = None
    if blocked_at is None:
        segs_seen = [s for s in segs_all if math.hypot(s["x2"] - s["x1"], s["y2"] - s["y1"]) > 1e-9]
        spec = {"net": args.net,
                "desc": f"astar3 chain: {' > '.join(args.chain)} (w=0.224mm, 最松已证 DRC 口径)",
                "segs": [{"layer": int(s["layer"]), "w_mm": round(s.get("w", 0.224), 4),
                          "pts": [[round(s["x1"], 3), round(s["y1"], 3)],
                                  [round(s["x2"], 3), round(s["y2"], 3)]]} for s in segs_seen],
                "vias": [[round(v["x"], 3), round(v["y"], 3), v.get("dia_mil", 16)] for v in vias_all]}
        (HERE / f"reports/s4b_astar/{args.net}.json").write_text(
            json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    rec = {"net": args.net, "chain": args.chain, "engine": "pcb_astar3(CLR=0.003/0.224 最松已证 DRC 口径)",
           "blocked_at": blocked_at, "ok": blocked_at is None, "spec_saved": spec is not None,
           "hops_ok": len(segs_all), "state_at": HERE.joinpath("reports/_s4b_state.json").stat().st_mtime,
           "logs": logs}
    (HERE / f"reports/_s4c_q14_astar3_{args.net}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"net": args.net, "ok": rec["ok"], "blocked_at": blocked_at}, ensure_ascii=False))


if __name__ == "__main__":
    main()
