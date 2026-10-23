#!/usr/bin/env python3
"""session_brief.py — SessionStart 上下文注入（PCB 许愿式设计）。

职责：新会话/压缩后自动把「当前项目 + 门禁状态 + 挂账问题」注入模型上下文，
避免长会话断裂后 agent 忘记自己推进到哪一步（这是本项目历史上最高频的失控来源）。

纪律：只读、绝不写；任何异常一律 exit 0 且不输出（不污染会话）。
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from gate import load_state, gate_status, current_gate, GATE_ORDER, GATE_TITLES
except Exception:
    sys.exit(0)


RE_DISPOSITION = re.compile(r"\*\*(Q-\d+)（.+?）→\s*(.+?)\*\*")


def count_pending(project):
    """统计提问单里未裁决的条目。

    优先读 logs/questions.md 文首处置块（`**Q-xx（…）→ 状态**`，状态含「待」=未裁决）；
    没有处置块或 legacy 文件才退回逐行扫描。
    """
    candidates = (Path(project) / "logs" / "questions.md",
                  Path(project) / "reports" / "questions.md",
                  Path(project) / "reports" / "pending_decisions.md")
    for p in candidates:
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        disp = RE_DISPOSITION.findall(text)
        if disp:
            return p.relative_to(project).as_posix(), sum(1 for _, st in disp if "待" in st)
        n = sum(1 for ln in text.splitlines()
                if ln.lstrip().startswith(("- Q", "* Q", "Q"))
                and "已答复" not in ln and "已裁决" not in ln)
        return p.relative_to(project).as_posix(), n
    return None, 0


def list_projects(cwd):
    """工作区下所有含 MISSION.md 的项目；cwd 本身是项目目录时只返回它一个。"""
    cur = Path(cwd).resolve()
    if (cur / "MISSION.md").is_file() and cur.parent.name == "projects":
        return [cur]
    for d in [cur, *cur.parents]:
        proj_root = d / "projects"
        if proj_root.is_dir():
            cands = [x for x in proj_root.iterdir() if x.is_dir() and (x / "MISSION.md").is_file()]
            if cands:
                return sorted(cands, key=lambda x: x.name)
    return []


def brief_one(project):
    state = load_state(project)
    qname, qn = count_pending(project)
    lines = [f"项目: {project.name}"]
    if state is None:
        lines.append("  门禁: 未初始化（reports/gates.json 不存在）")
        lines.append(f"  下一步: python \"${{CLAUDE_PLUGIN_ROOT}}/scripts/gate.py\" --project {project.name} init")
    else:
        cur = current_gate(state)
        passed = [g for g in GATE_ORDER if gate_status(state, g) == "pass"]
        lines.append(f"  当前门: {cur + ' · ' + GATE_TITLES[cur] if cur else '全部已过'}")
        lines.append(f"  已过: {', '.join(passed) if passed else '（无）'}")
        failed = [g for g in GATE_ORDER if gate_status(state, g) == "fail"]
        if failed:
            lines.append(f"  **失败门: {', '.join(failed)}（其后各门已回退，不得跳过）**")
    if qname and qn:
        lines.append(f"  挂账提问: {qname} 约 {qn} 条待答复（涉及已验收产物的改动前须先裁决）")
    return lines


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    cwd = data.get("cwd") or os.getcwd()
    projects = list_projects(cwd)
    if not projects:
        return 0
    out = ["# PCB-Wish 会话简报（门禁状态，事实来源 reports/gates.json）"]
    if len(projects) > 1:
        out.append(f"工作区共 {len(projects)} 个项目，门禁按你正在操作的项目对应行理解：")
    for p in projects:
        out.extend(brief_one(p))
    lines_txt = "\n".join(out)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": lines_txt,
        }
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
