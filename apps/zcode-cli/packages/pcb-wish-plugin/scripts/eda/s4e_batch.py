#!/usr/bin/env python3
"""s4e_batch.py — T4 批量重布驱动（auto 优先，先难后易，批 ≤2 网 + 冷却 55s）。

用法: python s4e_batch.py --nets DECUSB_N BAT_ADC --out _s4e_route_engine.json
纪律:
  - 每网五连（预检→创建→save→等 10s→重枚举→记 s4_progress.md）
  - 批 ≤2 网，批间冷却 55s（D017 过载边界）
  - 连续 3 次同型失败即停（交 T5 兜底）
  - 产物量测一律以画布几何回读为准
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s4e_lib import save_json, HERE  # noqa: E402
from s4e_auto import route_one, logline  # noqa: E402

COOLDOWN = 55


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nets", nargs="+", required=True)
    ap.add_argument("--out", default="_s4e_route_engine.json")
    ap.add_argument("--cooldown", type=int, default=COOLDOWN)
    ap.add_argument("--batch", type=int, default=2)
    ap.add_argument("--fixwidth", action="store_true")
    a = ap.parse_args()

    p = HERE / "reports" / a.out
    db = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    db.setdefault("engines", [])

    for i, net in enumerate(a.nets):
        # 同名网已成功记录则跳过
        done = {r["net"] for r in db["engines"] if r.get("engine") in ("sync", "job")}
        if net in done:
            print(f"跳过（已布）{net}")
            continue
        tries = 0
        rec = None
        while tries < 3:
            tries += 1
            rec = route_one(net, "sync", ("GND",), teardown=True, fixwidth=a.fixwidth)
            if rec.get("engine"):
                break
            if rec.get("router_ran"):
                # 路由器跑过但该网失败（failedNets）→ 属真实失败，可重试（不需重载文档）
                logline(f"S4E 路由 {net} 第 {tries} 次路由器判定失败 "
                        f"failedNets={rec.get('router_failedNets')}，重试")
                time.sleep(15)
            else:
                logline(f"S4E 路由 {net} 第 {tries} 次无产物（引擎静默），重试")
                time.sleep(20)
        rec["tries"] = tries
        rec["status"] = "routed" if rec.get("engine") else "FAILED"
        if not rec.get("engine"):
            rec["fallback"] = "astar"
        db["engines"].append(rec)
        save_json(a.out, db)
        m = rec.get("auto_sync_measure") or {}
        logline(f"S4E 路由 {net} 结果={rec['status']} 段 {m.get('segs')} 孔 {m.get('vias')} "
                f"ratio90={(m.get('corner') or {}).get('ratio90')}")
        if (i + 1) % a.batch == 0 and i != len(a.nets) - 1:
            print(f"--- 冷却 {a.cooldown}s ---", flush=True)
            time.sleep(a.cooldown)
    print("批次完成")


if __name__ == "__main__":
    main()
