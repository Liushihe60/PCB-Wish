#!/usr/bin/env python3
"""
s4b_astar_link.py — S4B 剩余网 A* 链路求解器（pcb_astar2 顺序 hop，JLC 口径）。

用法:
  python s4b_astar_link.py --net NAME --chain R20.2 R21.1 U1.A8 [--width 0.254]
输出 route spec（任务系 mm）→ reports/s4b_astar/<net>.json，供 s4b_exec.py --create 执行。
每 hop 用 pcb_astar2 双层 A*（从 pad 中心到下一 pad 中心）；已生成支路并入障碍。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path("E:/agent project/projects/flexiheal-v1")
EDADIR = Path(__file__).parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", required=True)
    ap.add_argument("--chain", nargs="+", required=True)
    ap.add_argument("--width", type=float, default=0.254)
    ap.add_argument("--clr", nargs=3, type=float, default=[0.102, 0.152, 0.17],
                    metavar=("TRACK", "PAD", "VIA"))
    ap.add_argument("--eps", type=float, default=2.5,
                    help="pcb_astar2 启发式膨胀系数；1.0=可采纳（完备）")
    args = ap.parse_args()
    from pcb_astar2 import set_rules
    set_rules(args.clr[0], args.clr[1], args.clr[2], args.width)
    from s4b_model import Model
    m = Model()
    by_key = {}
    for p in m.pads:
        by_key[f"{p['dz']}.{p['pad']}"] = p
    missing = [k for k in args.chain if k not in by_key or by_key[k]["net"] != args.net]
    if missing:
        print(f"!! 链含非本网/未知 pad: {missing}")
        sys.exit(2)
    audit_cache = EDADIR / f"_link_{args.net}_audit.json"
    state = json.loads((HERE / "reports/_s4b_state.json").read_text(encoding="utf-8"))

    def write_audit():
        kv = ([{"k": "L", **x} for x in state["lines"]] +
              [{"k": "V", **x} for x in state["vias"]])
        audit_cache.write_text(json.dumps(kv, ensure_ascii=False), encoding="utf-8")

    write_audit()
    segs_all, vias_all = [], []
    logs = []
    cur = args.chain[0]
    for nxt in args.chain[1:]:
        frm = f"{by_key[cur]['x']:.3f},{by_key[cur]['y']:.3f}"
        to = f"{by_key[nxt]['x']:.3f},{by_key[nxt]['y']:.3f}"
        out_f = EDADIR / "_link_hop.json"
        cmd = [sys.executable, str(EDADIR / "pcb_astar2.py"),
               "--net", args.net, "--dump", str(HERE / "reports/pcb_pads_dump_manual.json"),
               "--audit", str(audit_cache), "--from", frm, "--to", to,
               "--out", str(out_f), "--eps", str(args.eps)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env={
            "S4B_ASTAR_CLR_TRACK": str(args.clr[0]),
            "S4B_ASTAR_CLR_PAD": str(args.clr[1]),
            "S4B_ASTAR_CLR_VIA": str(args.clr[2]),
            "S4B_ASTAR_WIDTH": str(args.width),
            "PATH": __import__("os").environ.get("PATH", ""),
            "PYTHONPATH": str(Path(__file__).parent),
        })
        if r.returncode != 0:
            bad = (r.stdout + r.stderr).strip()
            logs.append({"hop": f"{cur}>{nxt}", "err": bad[-400:]})
            audit_cache.unlink(missing_ok=True)
            (HERE / f"reports/s4b_astar_{args.net}_fail.json").write_text(
                json.dumps({"net": args.net, "blocked": True, "logs": logs}, ensure_ascii=False, indent=1),
                encoding="utf-8")
            print(json.dumps({"net": args.net, "blocked": True, "logs": logs}, ensure_ascii=False))
            sys.exit(1)
        hop = json.loads(out_f.read_text(encoding="utf-8"))
        segs_all += hop["segs"]
        vias_all += hop["vias"]
        for i, sg in enumerate(hop["segs"]):
            state["lines"].append({"id": f"lnk_{args.net}_{len(state['lines'])}",
                                   "net": args.net, "layer": sg["layer"],
                                   "w": round(args.width * 39.37, 2),
                                   "sx": round(sg["x1"] * 39.37, 2),
                                   "sy": round((30.0 - sg["y1"]) * 39.37, 2),
                                   "ex": round(sg["x2"] * 39.37, 2),
                                   "ey": round((30.0 - sg["y2"]) * 39.37, 2)})
        for v in hop["vias"]:
            state["vias"].append({"id": f"lnkv_{args.net}_{len(state['vias'])}",
                                  "net": args.net,
                                  "x": round(v["x"] * 39.37, 2),
                                  "y": round((30.0 - v["y"]) * 39.37, 2),
                                  "hole": v.get("hole_mil", 12), "dia": v.get("dia_mil", 16)})
        write_audit()
        cur = nxt

    spec = {"net": args.net,
            "desc": f"astar2 chain: {' > '.join(args.chain)} (w={args.width}mm)",
            "segs": [{"layer": int(s["layer"]), "w_mm": round(args.width, 4),
                      "pts": [[round(s["x1"], 3), round(s["y1"], 3)],
                              [round(s["x2"], 3), round(s["y2"], 3)]]}
                     for s in segs_all],
            "vias": [[round(v["x"], 3), round(v["y"], 3), v.get("dia_mil", 16)]
                     for v in vias_all]}
    out_p = HERE / f"reports/s4b_astar/{args.net}.json"
    out_p.parent.mkdir(exist_ok=True)
    out_p.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    audit_cache.unlink(missing_ok=True)
    tot = sum((s.get("total_mm") or 0) for s in segs_all) if False else None
    print(json.dumps({"net": args.net, "ok": True, "segs": len(segs_all),
                      "vias": len(vias_all), "out": str(out_p)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
