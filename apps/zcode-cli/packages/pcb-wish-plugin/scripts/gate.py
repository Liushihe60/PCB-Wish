#!/usr/bin/env python3
"""gate.py — PCB 许愿式设计阶段门禁状态机（G0 → G7）。

用法:
    python gate.py init  --project flexiheal-v2
    python gate.py status [--project P] [--json]
    python gate.py pass  G1 --evidence spec/ir.json --evidence spec/assertions.json
    python gate.py fail  G1 --reason "..."
    python gate.py pass  G3 --force --note "回溯补记"
    python gate.py require G1          # 退出码 0=已过, 1=未过
    python gate.py next                # 打印下一个待过之门

设计纪律:
  - 状态文件 projects/<p>/reports/gates.json 是唯一事实来源，手改无效（下次 pass 会重写）。
  - 「门不过不前进」是机械规则：pass G<n> 要求 G<n-1> 已 pass；除非 --force（会记 forced=true）。
  - 本脚本只判定「有没有走过这道门」，不判定「门后的质量」——质量由各自判据脚本负责。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

GATE_ORDER = ["G0", "G0.5", "G1", "G2", "G3", "G4", "G5", "G5.5", "G6", "G7"]
GATE_TITLES = {
    "G0": "方案确认（用户拍板）",
    "G0.5": "选型定稿（BOM 冻结）",
    "G1": "IR 门（网表意图 + 断言同源）",
    "G2": "原理图门（ERC + 网表断言）",
    "G3": "PCB 规划门（板框层叠分区）",
    "G4": "布局门（重叠/丝印/朝向可布性）",
    "G5": "布线门（DRC + 连通 + 拐角）",
    "G5.5": "清零门（error/warning 归零或豁免）",
    "G6": "制造门（Gerber/钻孔/BOM/DFM）",
    "G7": "讲义门（LaTeX→PDF，仅勾选时）",
}
GATE_STATUSES = ("pending", "pass", "fail", "skipped")


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- project 解析

def find_project(explicit=None, start=None):
    """定位项目目录：显式 > 环境变量 > cwd 上溯 > projects/* 里最新 MISSION.md。"""
    if explicit:
        p = Path(explicit)
        if p.is_dir():
            return p.resolve()
        for base in (Path(start or os.getcwd()),):
            cand = base / "projects" / explicit
            if cand.is_dir():
                return cand.resolve()
        return None

    env = os.environ.get("PCB_WISH_PROJECT")
    if env:
        p = Path(env)
        if p.is_dir():
            return p.resolve()

    cur = Path(start or os.getcwd()).resolve()
    for d in [cur, *cur.parents]:
        # cwd 本身就在 projects/<slug>/ 里
        if (d / "MISSION.md").is_file() and d.parent.name == "projects":
            return d
        proj_root = d / "projects"
        if proj_root.is_dir():
            cands = [x for x in proj_root.iterdir() if x.is_dir() and (x / "MISSION.md").is_file()]
            if cands:
                cands.sort(key=lambda x: (x / "MISSION.md").stat().st_mtime, reverse=True)
                return cands[0].resolve()
    return None


def gates_path(project):
    return Path(project) / "reports" / "gates.json"


def load_state(project):
    p = gates_path(project)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def empty_state(project):
    return {
        "schema": 1,
        "project": Path(project).name,
        "created": now(),
        "updated": now(),
        "gates": {g: {"status": "pending"} for g in GATE_ORDER},
    }


def save_state(project, state):
    p = gates_path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    state["updated"] = now()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


# ---------------------------------------------------------------- 状态查询

def gate_status(state, gate):
    return (state or {}).get("gates", {}).get(gate, {}).get("status", "pending")


def passed(state):
    return [g for g in GATE_ORDER if gate_status(state, g) == "pass"]


def current_gate(state):
    for g in GATE_ORDER:
        if gate_status(state, g) != "pass":
            return g
    return None


def prereq_of(gate):
    i = GATE_ORDER.index(gate)
    return GATE_ORDER[i - 1] if i > 0 else None


# ---------------------------------------------------------------- 命令实现

def cmd_init(args):
    project = find_project(args.project)
    if not project:
        print(json.dumps({"ok": False, "error": "未找到项目目录（需含 MISSION.md）"}, ensure_ascii=False))
        return 2
    state = load_state(project)
    if state and not args.reset:
        print(json.dumps({"ok": True, "created": False, "path": str(gates_path(project))},
                         ensure_ascii=False))
        return 0
    state = empty_state(project)
    path = save_state(project, state)
    print(json.dumps({"ok": True, "created": True, "path": str(path)}, ensure_ascii=False))
    return 0


def cmd_status(args):
    project = find_project(args.project)
    if not project:
        print(json.dumps({"ok": False, "error": "未找到项目目录"}, ensure_ascii=False))
        return 2
    state = load_state(project)
    if not state:
        print(json.dumps({"ok": False, "error": "gates.json 不存在，先运行 gate.py init",
                          "project": str(project)}, ensure_ascii=False))
        return 1
    if args.json:
        print(json.dumps({"ok": True, "project": str(project), "state": state}, ensure_ascii=False))
        return 0
    cur = current_gate(state)
    print(f"项目: {project.name}")
    for g in GATE_ORDER:
        rec = state["gates"].get(g, {})
        mark = {"pass": "PASS", "fail": "FAIL", "skipped": "SKIP"}.get(rec.get("status"), "----")
        note = rec.get("note") or rec.get("reason") or ""
        print(f"  [{mark}] {g:<5} {GATE_TITLES[g]}" + (f"  — {note}" if note else ""))
    print(f"当前门: {cur or '全部已过'}")
    return 0


def cmd_pass(args):
    project = find_project(args.project)
    if not project:
        print(json.dumps({"ok": False, "error": "未找到项目目录"}, ensure_ascii=False))
        return 2
    gate = args.gate
    if gate not in GATE_ORDER:
        print(json.dumps({"ok": False, "error": f"未知门 {gate}；合法值: {GATE_ORDER}"}, ensure_ascii=False))
        return 2
    state = load_state(project) or empty_state(project)

    pre = prereq_of(gate)
    forced = False
    if pre and gate_status(state, pre) != "pass":
        if not args.force:
            print(json.dumps({
                "ok": False,
                "error": f"门不过不前进：{gate} 的前置门 {pre} 尚未 PASS"
                         f"（当前 {gate_status(state, pre)}）。确需回溯补记请加 --force。",
            }, ensure_ascii=False))
            return 1
        forced = True

    evidence = list(args.evidence or [])
    missing = [e for e in evidence if not (Path(project) / e).exists()]
    if missing and not args.allow_missing_evidence:
        print(json.dumps({
            "ok": False,
            "error": "证据文件不存在：" + ", ".join(missing) + "（确需登记请加 --allow-missing-evidence）",
        }, ensure_ascii=False))
        return 1

    state["gates"][gate] = {
        "status": "pass",
        "at": now(),
        "evidence": evidence,
        **( {"note": args.note} if args.note else {} ),
        **( {"forced": True} if forced else {} ),
    }
    path = save_state(project, state)
    print(json.dumps({"ok": True, "gate": gate, "status": "pass", "forced": forced,
                      "path": str(path)}, ensure_ascii=False))
    return 0


def cmd_fail(args):
    project = find_project(args.project)
    if not project:
        print(json.dumps({"ok": False, "error": "未找到项目目录"}, ensure_ascii=False))
        return 2
    gate = args.gate
    if gate not in GATE_ORDER:
        print(json.dumps({"ok": False, "error": f"未知门 {gate}"}, ensure_ascii=False))
        return 2
    state = load_state(project) or empty_state(project)
    state["gates"][gate] = {"status": "fail", "at": now(), "reason": args.reason or ""}
    # 失败门之后的门全部回退到 pending（不许带着已过之门往下走）
    for g in GATE_ORDER[GATE_ORDER.index(gate) + 1:]:
        if gate_status(state, g) == "pass":
            state["gates"][g] = {"status": "pending", "note": f"因 {gate} 失败而回退"}
    save_state(project, state)
    print(json.dumps({"ok": True, "gate": gate, "status": "fail"}, ensure_ascii=False))
    return 0


def cmd_require(args):
    project = find_project(args.project)
    if not project:
        print(json.dumps({"ok": False, "gate": args.gate, "status": "unknown",
                          "reason": "未找到项目目录"}, ensure_ascii=False))
        return 1
    state = load_state(project)
    st = gate_status(state, args.gate)
    ok = st == "pass"
    print(json.dumps({"ok": ok, "gate": args.gate, "status": st,
                      "project": project.name}, ensure_ascii=False))
    return 0 if ok else 1


def cmd_next(args):
    project = find_project(args.project)
    if not project:
        print(json.dumps({"ok": False, "error": "未找到项目目录"}, ensure_ascii=False))
        return 2
    state = load_state(project)
    nxt = current_gate(state)
    print(json.dumps({"ok": True, "next": nxt, "title": GATE_TITLES.get(nxt) if nxt else None,
                      "passed": passed(state)}, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="PCB 许愿式设计阶段门禁状态机")
    ap.add_argument("--project", help="项目 slug 或目录")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="初始化 gates.json")
    p_init.add_argument("--reset", action="store_true", help="已存在时重建")
    p_init.set_defaults(func=cmd_init)

    p_st = sub.add_parser("status", help="打印门禁状态")
    p_st.add_argument("--json", action="store_true")
    p_st.set_defaults(func=cmd_status)

    p_pass = sub.add_parser("pass", help="标记某门通过")
    p_pass.add_argument("gate")
    p_pass.add_argument("--evidence", action="append", help="证据文件（相对项目根）")
    p_pass.add_argument("--note")
    p_pass.add_argument("--force", action="store_true", help="跳过前置门检查")
    p_pass.add_argument("--allow-missing-evidence", action="store_true")
    p_pass.set_defaults(func=cmd_pass)

    p_fail = sub.add_parser("fail", help="标记某门失败并回退其后各门")
    p_fail.add_argument("gate")
    p_fail.add_argument("--reason")
    p_fail.set_defaults(func=cmd_fail)

    p_req = sub.add_parser("require", help="断言某门已过（退出码 0/1）")
    p_req.add_argument("gate")
    p_req.set_defaults(func=cmd_require)

    p_next = sub.add_parser("next", help="打印下一个待过之门")
    p_next.set_defaults(func=cmd_next)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
