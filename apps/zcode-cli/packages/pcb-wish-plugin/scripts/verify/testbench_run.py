#!/usr/bin/env python3
"""testbench_run.py — ngspice 批处理 testbench runner（pcb-wish 验证层 ④电学）。

输入:
  --testbench <file.sp>       ngspice 网表：含 .tran 与 .meas 语句
  --criteria <criteria.json>  判据文件，格式:
      {"criteria": [
        {"id": "vout-avg", "measure": "vout_avg", "op": "between",
         "min": 4.85, "max": 5.15, "unit": "V", "comment": "..."},
        {"id": "ripple-hi", "measure": "vout_max", "op": "<=", "target": 5.25}
      ]}
      measure 必须与 .sp 中 .meas 的量名一致（ngspice 日志里小写输出）。
  约定（诚实纪律）:
    - .meas 缺结果（仿真未跑到/量名写错）→ 该判据 FAIL，绝不默认 PASS；
    - 无厂商 SPICE 模型的芯片用分立级行为级模型，结论记 COMPUTED；
      禁止伪造厂商模型。testbench 只验证量级与包络，不定稿精确指标。

运行: ngspice -b -o <log> <file.sp>（批处理模式，无交互）
退出码: 0=判据全过, 1=有 FAIL, 2=环境问题（ngspice 未装/文件缺失）。
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MEAS_LINE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|not\s*found|failed)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def parse_meas(log_text):
    """从 ngspice 批处理日志提取 .meas 结果 → {name: value}。
    值为 float；'not found'/'failed' 一律丢弃（后续按缺失处理，不猜数）。
    """
    out = {}
    for m in MEAS_LINE.finditer(log_text):
        name, raw = m.group(1), m.group(2)
        try:
            out[name.lower()] = float(raw)
        except ValueError:
            continue
    return out


def evaluate(meas, criteria):
    results = []
    for c in criteria:
        cid, op = c.get("id", c.get("measure", "?")), c.get("op")
        raw = meas.get(c["measure"].lower())
        if raw is None:
            results.append({"id": cid, "ok": False, "value": None,
                            "detail": f".meas 量 {c['measure']} 未在日志中找到（缺测/未仿真到）"})
            continue
        if op == "<=":
            ok, tgt = raw <= c["target"], c["target"]
            detail = f"{raw:.6g} {op} {tgt}"
        elif op == ">=":
            ok, tgt = raw >= c["target"], c["target"]
            detail = f"{raw:.6g} {op} {tgt}"
        elif op == "==":
            tol = c.get("tol", 1e-9)
            ok = abs(raw - c["target"]) <= tol
            detail = f"{raw:.6g} == {c['target']} (±{tol})"
        elif op == "between":
            ok = c["min"] <= raw <= c["max"]
            detail = f"{raw:.6g} ∈ [{c['min']}, {c['max']}]"
        else:
            ok, detail = False, f"未知 op {op}"
        results.append({"id": cid, "ok": ok, "value": raw, "detail": detail})
    return results


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="ngspice testbench runner")
    ap.add_argument("--testbench", required=True)
    ap.add_argument("--criteria", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--ngspice", default="ngspice", help="ngspice 可执行（默认 PATH）")
    args = ap.parse_args()

    tb, cr = Path(args.testbench), Path(args.criteria)
    for p in (tb, cr):
        if not p.exists():
            print(f"FAIL  文件不存在: {p}")
            sys.exit(2)
    exe = shutil.which(args.ngspice)
    if exe is None:
        print("FAIL  ngspice 不在 PATH——仿真验证不可用（doctor.py sim 级缺项）。")
        print("      安装指引: https://ngspice.sourceforge.io/download.html （Windows 装 64 位包后把 bin 加入 PATH）")
        sys.exit(2)
    try:
        criteria = json.loads(cr.read_text(encoding="utf-8"))["criteria"]
    except (json.JSONDecodeError, KeyError) as e:
        print(f"FAIL  判据文件格式错误: {e}")
        sys.exit(2)

    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "ngspice.log"
        proc = subprocess.run(
            [exe, "-b", "-o", str(log), str(tb)],
            capture_output=True, text=True, timeout=600)
        log_text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    if proc.returncode != 0:
        tail = "\n".join(log_text.splitlines()[-15:] or ["<无日志>"])
        print(f"FAIL  ngspice 退出码 {proc.returncode}（网表/语句错误，日志尾部）:\n{tail}")
        sys.exit(1)

    meas = parse_meas(log_text)
    results = evaluate(meas, criteria)
    ok = all(r["ok"] for r in results)
    report = {"ok": ok, "testbench": str(tb), "measures": meas, "results": results,
              "verdict_level": "COMPUTED",
              "$note": "分立级/行为级仿真结论记 COMPUTED；厂商模型缺失时禁止伪造"}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['id']:<20} {r['detail']}")
        print(f"\n结论: {'判据全过（COMPUTED 级）' if ok else '存在判据失败'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
