#!/usr/bin/env python3
"""decision_log.py — append-only 决策日志管理器（pcb-wish）。

决策日志是全流程的唯一记忆载体，也是教学讲义的素材源。
铁律：条目只追加不改写；修订用 supersede 生成新条目并指向被替代条目。

用法:
    python decision_log.py init  <log.md>
    python decision_log.py add   <log.md> --type design --statement "..." [--basis "..."] [--evidence "..."]
    python decision_log.py supersede <log.md> --id D001 --statement "..." [--basis "..."] [--evidence "..."]
    python decision_log.py validate <log.md>
    python decision_log.py show  <log.md> [--type design]

条目格式（单行）:
    - [D001] 2026-10-19 | design | 一句话决策 | 依据: xxx | 证据: xxx
supersede 条目:
    - [D005] 2026-10-20 | design | supersedes D001 | 一句话决策 | 依据: xxx | 证据: xxx
"""
import argparse
import datetime
import json
import re
import sys
from pathlib import Path

TYPES = ("goal", "constraint", "input", "design", "tradeoff", "scope-reject")
ENTRY = re.compile(
    r"^-\s*\[D(\d+)\]\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([a-z-]+)\s*\|\s*(.+)$"
)
SUPERSEDES = re.compile(r"supersedes\s+D(\d+)", re.I)

HEADER = """# 决策日志（append-only）

规则：
1. 只追加，不改写、不删除既有条目；
2. 修订决策用 `supersede` 生成新条目，指向被替代条目；
3. 每条含：类型 | 一句话决策 | 依据 | 证据（证据须可回链：文件路径/计算式/仿真记录）；
4. 类型：goal / constraint / input / design / tradeoff / scope-reject。

---
"""


def next_id(entries):
    return max((e["id"] for e in entries), default=0) + 1


def load(path):
    p = Path(path)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8", errors="replace").splitlines()


def parse(lines):
    entries, problems = [], []
    seen = set()
    for i, line in enumerate(lines, 1):
        if not line.startswith("- [D"):
            continue
        m = ENTRY.match(line)
        if not m:
            problems.append({"line": i, "message": f"格式不符: {line[:80]}"})
            continue
        eid, date, typ, rest = int(m.group(1)), m.group(2), m.group(3), m.group(4)
        if typ not in TYPES:
            problems.append({"line": i, "message": f"未知类型: {typ}"})
        if eid in seen:
            problems.append({"line": i, "message": f"重复 id D{eid:03d}"})
        seen.add(eid)
        sup = SUPERSEDES.search(rest)
        entries.append(
            {
                "id": eid,
                "date": date,
                "type": typ,
                "supersedes": int(sup.group(1)) if sup else None,
                "raw": line,
            }
        )
    id_set = {e["id"] for e in entries}
    for e in entries:
        if e["supersedes"] is not None and e["supersedes"] not in id_set:
            problems.append({"line": None, "message": f"D{e['id']:03d} 引用了不存在的被替代条目 D{e['supersedes']:03d}"})
    return entries, problems


def cmd_init(args):
    p = Path(args.log)
    if p.exists() and p.stat().st_size > 0:
        print(f"已存在，跳过 init: {p}")
        return 0
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(HEADER, encoding="utf-8")
    print(f"已初始化决策日志: {p}")
    return 0


def cmd_add(args):
    if args.type not in TYPES:
        print(f"错误: type 必须是 {TYPES}", file=sys.stderr)
        return 2
    lines = load(args.log)
    if lines is None:
        print(f"错误: 日志不存在，先 init: {args.log}", file=sys.stderr)
        return 2
    entries, problems = parse(lines)
    if problems:
        print(json.dumps({"ok": False, "problems": problems}, ensure_ascii=False))
        return 1
    nid = next_id(entries)
    today = datetime.date.today().isoformat()
    line = (
        f"- [D{nid:03d}] {today} | {args.type} | {args.statement}"
        + (f" | 依据: {args.basis}" if args.basis else "")
        + (f" | 证据: {args.evidence}" if args.evidence else "")
    )
    with open(args.log, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(f"已追加 D{nid:03d}")
    return 0


def cmd_supersede(args):
    lines = load(args.log)
    if lines is None:
        print("错误: 日志不存在，先 init", file=sys.stderr)
        return 2
    entries, problems = parse(lines)
    if problems:
        print(json.dumps({"ok": False, "problems": problems}, ensure_ascii=False))
        return 1
    target = re.match(r"D(\d+)", args.id, re.I)
    tid = int(target.group(1)) if target else None
    if tid not in {e["id"] for e in entries}:
        print(f"错误: 被替代条目 {args.id} 不存在", file=sys.stderr)
        return 2
    nid = next_id(entries)
    today = datetime.date.today().isoformat()
    line = (
        f"- [D{nid:03d}] {today} | {args.type} | supersedes D{tid:03d} | {args.statement}"
        + (f" | 依据: {args.basis}" if args.basis else "")
        + (f" | 证据: {args.evidence}" if args.evidence else "")
    )
    with open(args.log, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(f"已追加 D{nid:03d}（supersedes D{tid:03d}，原条目保留）")
    return 0


def cmd_validate(args):
    lines = load(args.log)
    if lines is None:
        print(json.dumps({"ok": False, "problems": [{"line": None, "message": "文件不存在"}]}, ensure_ascii=False))
        return 1
    entries, problems = parse(lines)
    ok = not problems
    print(json.dumps({"ok": ok, "count": len(entries), "problems": problems}, ensure_ascii=False))
    return 0 if ok else 1


def cmd_show(args):
    lines = load(args.log)
    if lines is None:
        print("错误: 日志不存在", file=sys.stderr)
        return 2
    entries, _ = parse(lines)
    for e in entries:
        if args.type and e["type"] != args.type:
            continue
        print(e["raw"])
    return 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="决策日志管理（append-only）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "add", "supersede", "validate", "show"):
        s = sub.add_parser(name)
        s.add_argument("log", help="decisions.md 路径")
        if name == "add":
            s.add_argument("--type", required=True, help=f"类型 {'/'.join(TYPES)}")
            s.add_argument("--statement", required=True)
            s.add_argument("--basis", default="")
            s.add_argument("--evidence", default="")
        if name == "supersede":
            s.add_argument("--id", required=True, help="被替代条目如 D001")
            s.add_argument("--type", default="design")
            s.add_argument("--statement", required=True)
            s.add_argument("--basis", default="")
            s.add_argument("--evidence", default="")
        if name == "show":
            s.add_argument("--type", default="")
    args = ap.parse_args()
    sys.exit(
        {"init": cmd_init, "add": cmd_add, "supersede": cmd_supersede, "validate": cmd_validate, "show": cmd_show}[args.cmd](args)
    )


if __name__ == "__main__":
    main()
