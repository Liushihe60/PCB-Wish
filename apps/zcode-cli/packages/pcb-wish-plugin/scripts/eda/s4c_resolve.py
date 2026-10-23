#!/usr/bin/env python3
"""s4c_resolve.py — S4C 违例解重解驱动（离线，不写画布）。

对每个指定网：按 s4b_astar_link（pcb_astar2 逐 hop）以递增间距裕量重解 →
s4b_model.route_check + set_endpoint_ok 校验 → 首个全过者写
reports/s4b_astar/<net>.json（S4B 原解已备份 s4b_astar_s4b_orig/）。
全部尝试失败 → 记 blocked（带引擎/裕量证据）。

用法: python s4c_resolve.py --net NAME --chain A B C [--width 0.254]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path("E:/agent project/projects/flexiheal-v1")
EDADIR = Path(__file__).parent
sys.path.insert(0, str(EDADIR))

# (clr_track, clr_pad, clr_via, eps) 递增裕量档位；eps=1.0 可采纳 A*（完备性档）
LADDER = [(0.102, 0.152, 0.17, 2.5), (0.102, 0.152, 0.17, 1.0),
          (0.13, 0.17, 0.19, 1.0), (0.15, 0.19, 0.21, 1.0)]


def validate(net):
    import s4b_exec as EX
    from s4b_model import Model
    m = Model()
    spec = json.loads((HERE / f"reports/s4b_astar/{net}.json").read_text(encoding="utf-8"))
    vios = m.route_check(spec)
    ep = m.set_endpoint_ok(spec, net)
    return vios + ep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", required=True)
    ap.add_argument("--chain", nargs="+", required=True)
    ap.add_argument("--width", type=float, default=0.254)
    args = ap.parse_args()
    out = {"net": args.net, "chain": args.chain, "attempts": []}
    for (ct, cp, cv, eps) in LADDER:
        cmd = [sys.executable, str(EDADIR / "s4b_astar_link.py"),
               "--net", args.net, "--chain", *args.chain, "--width", str(args.width),
               "--clr", str(ct), str(cp), str(cv), "--eps", str(eps)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                           cwd=str(HERE), encoding="utf-8", errors="replace")
        if r.returncode != 0:
            tail = (r.stdout + r.stderr).strip().splitlines()
            out["attempts"].append({"clr": [ct, cp, cv], "eps": eps, "blocked": True,
                                    "msg": tail[-1][:200] if tail else ""})
            continue
        vios = validate(args.net)
        if not vios:
            out["attempts"].append({"clr": [ct, cp, cv], "eps": eps, "ok": True, "segs_valid": True})
            out["resolved"] = True
            break
        out["attempts"].append({"clr": [ct, cp, cv], "eps": eps, "ok": False, "vios": vios[:8],
                                "n_vios": len(vios)})
    out.setdefault("resolved", False)
    (HERE / f"reports/_s4c_resolve_{args.net}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"net": args.net, "resolved": out["resolved"],
                      "attempts": [{k: v for k, v in a.items() if k != "vios"} for a in out["attempts"]]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
