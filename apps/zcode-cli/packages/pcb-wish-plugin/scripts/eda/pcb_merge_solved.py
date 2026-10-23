#!/usr/bin/env python3
"""pcb_merge_solved.py — 把 layout_solved.json 的 patch 合并进 spec/layout.json。

坐标系换算：patch 为 dump 空间（canvas mm，y 已翻转）；layout.json 为任务 mm
（板左上原点）。task_x = dump_x；task_y = H − dump_y。
rot≠0 时 positions 值写为 [x, y, rot] 三元组（pcb_layout_from_spec.py 已支持）。
H4 审计：硬件锚点（FIXED∪RELAX）对原坐标位移 >2mm 记 violation 并以退出码 1 报告。
首次运行自动备份原 layout.json → layout.pre-solve.json。

用法:
    python pcb_merge_solved.py --spec spec/layout.json --solved spec/layout_solved.json
退出码: 0=锚点全部 ≤2mm; 1=锚点超差（文件仍写出，供复检）。
"""
import argparse
import json
import math
import sys
from pathlib import Path

ANCHORS = {"J5", "U2", "U1", "J1", "J4", "J2", "BTN1", "BTN2", "ANT1", "P3",
           "J3", "X1", "X2", "R_PROG", "TP1", "TP2", "TP3"}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default="spec/layout.json")
    ap.add_argument("--solved", default="spec/layout_solved.json")
    args = ap.parse_args()

    # 合并基准：最初备份（任务书原值）+ 本次净 patch —— 保证可重入、累计一致
    baseline_path = Path(args.spec).with_suffix(".pre-solve.json")
    if baseline_path.exists():
        spec = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline = {k: list(v) for k, v in spec["positions"].items()}  # 深拷贝！
        # （此前 baseline = spec["positions"] 是同一对象引用，写入后审计变成自己比自己=恒0）
    else:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        baseline = spec["positions"]
        baseline_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"# 已备份原 layout.json → {baseline_path.name}")
    solved = json.loads(Path(args.solved).read_text(encoding="utf-8"))
    h = spec["board_mm"][1]

    bad = []
    pos_abs = solved.get("positions_abs") or {}
    if pos_abs:
        # 全量绝对终态：所有件一次落定（未移动件=输入位置；审计只看锚点累计偏差）
        for dz, p in pos_abs.items():
            old = spec["positions"].get(dz)
            if not old:
                print(f"# {dz}: layout.json 无此位号，跳过")
                continue
            nx, ny = round(p["x_mm"], 2), round(h - p["y_mm"], 2)
            rot = p.get("rot") or 0
            spec["positions"][dz] = [nx, ny, rot] if rot else [nx, ny]
            base = baseline.get(dz, old)
            dev = round(math.hypot(nx - base[0], ny - base[1]), 2)
            if dz in ANCHORS and dev > 2:
                bad.append({"dz": dz, "dev_mm": dev})
                print(f"# {dz}: ({old[0]},{old[1]}) → ({nx},{ny}) rot={rot} Δ={dev}mm  ← 锚点超差(H4>2mm)")
    else:
        for dz, p in solved["patch"].items():
            old = spec["positions"].get(dz)
            if not old:
                print(f"# {dz}: layout.json 无此位号，跳过")
                continue
            nx, ny = round(p["x_mm"], 2), round(h - p["y_mm"], 2)
            rot = p.get("rot") or 0
            spec["positions"][dz] = [nx, ny, rot] if rot else [nx, ny]
            base = baseline.get(dz, old)  # 累计偏差=对任务书原值
            dev = round(math.hypot(nx - base[0], ny - base[1]), 2)
            if dz in ANCHORS and dev > 2:
                bad.append({"dz": dz, "dev_mm": dev})
                flag = "  ← 锚点超差(H4>2mm)"
            print(f"# {dz}: ({old[0]},{old[1]}) → ({nx},{ny}) rot={rot} Δ={dev}mm{flag if dz in ANCHORS and dev > 2 else ''}")

    Path(args.spec).write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"合并完成：移动 {len(solved['patch'])} 件；残余对 {len(solved['residual'])}；"
          f"锚点超差 {len(bad)}")
    sys.exit(0 if not bad and not solved["residual"] else 1)


if __name__ == "__main__":
    main()
