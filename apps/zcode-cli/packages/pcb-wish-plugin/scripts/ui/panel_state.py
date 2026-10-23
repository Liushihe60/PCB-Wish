#!/usr/bin/env python3
"""panel_state.py — 面板数据聚合器（PCB 许愿式设计）。

把 projects/<slug>/ 下散落的门禁状态、挂账、决策、交付物聚合成一份 JSON，
作为 ui/panel.html 与后续 MCP 服务的唯一数据源。只读，任何单项缺失都降级为
null/空列表，绝不因解析失败而整体报错（与 hooks 的 fail-open 同纪律）。

用法：
    python panel_state.py --project flexiheal-v2 [--workspace <ws>] [--pretty]
输出：stdout，UTF-8 JSON。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows 控制台默认码页（cp936）会截断/损坏中文输出，强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from gate import GATE_ORDER, GATE_TITLES
except Exception:
    GATE_ORDER = ["G0", "G0.5", "G1", "G2", "G3", "G4", "G5", "G5.5", "G6", "G7"]
    GATE_TITLES = {}

RE_DECISION = re.compile(r"^- \[(D\d+)\]\s+(\d{4}-\d{2}-\d{2})\s+\|\s+([\w-]+)\s+\|\s+\*\*(.+?)\*\*", re.M)
RE_WAIVER = re.compile(r"^##\s+(W-\d+)[　\s]*(.*)$", re.M)
RE_QUESTION = re.compile(r"^##\s+(Q-\d+)｜(.*)$", re.M)
RE_QDISPOSITION = re.compile(r"^>?\s*- \*\*(Q-\d+)（.+?）→\s*(.+?)\*\*", re.M)
RE_QSTATUS = re.compile(r"\*\*状态\*\*：(.+)$", re.M)
RE_DELIVERABLE = re.compile(r"^- \[( |x|X)\]\s+(.+)$", re.M)
SNAPSHOT_CANDIDATES = ("_g6_pcb_top.png", "_g5f_stitch.png", "_g4r_board_full.png", "_g3t_board.png")


def plain(text):
    return text.replace("**", "").replace("`", "").strip()


def default_workspace() -> Path:
    env = os.environ.get("PCB_WISH_WORKSPACE")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[4]


def read_text(path: Path):
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def read_json(path: Path):
    text = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def clean(note):
    # 控制台码页造成的 U+FFFD 损坏文本不展示，避免面板显示乱码
    if not note or chr(0xFFFD) in note:
        return None
    return note


def collect_gates(project: Path):
    state = read_json(project / "reports" / "gates.json") or {}
    raw = state.get("gates", {})
    gates = []
    for gid in GATE_ORDER:
        entry = raw.get(gid, {})
        gates.append({
            "id": gid,
            "title": GATE_TITLES.get(gid, gid),
            "status": entry.get("status", "pending"),
            "at": entry.get("at"),
            "evidence": entry.get("evidence", []),
            "note": clean(entry.get("note")),
        })
    current = next((g["id"] for g in gates if g["status"] != "pass"), None)
    return gates, current, state.get("updated")


def collect_mission(project: Path):
    text = read_text(project / "MISSION.md")
    if not text:
        return None
    title = None
    first = text.splitlines()[0] if text.splitlines() else ""
    m = re.match(r"^#\s*MISSION[：:]\s*(.+)$", first)
    if m:
        title = m.group(1).strip()
    hard = len(re.findall(r"^- \[H\d+\]", text, re.M))
    deliverables = []
    section = re.search(r"^##\s*4\.\s*交付物\s*$(.+?)(?=^##\s|\Z)", text, re.M | re.S)
    if section:
        for checked, label in RE_DELIVERABLE.findall(section.group(1)):
            deliverables.append({"done": checked.lower() == "x", "label": plain(label)})
    return {"title": title, "hard_constraints": hard, "deliverables": deliverables}


def collect_waivers(project: Path):
    files = sorted((project / "reports").glob("waivers_*.md"), key=lambda p: p.stat().st_mtime) if (project / "reports").is_dir() else []
    if not files:
        return [], None
    path = files[-1]
    text = read_text(path) or ""
    items = [{"id": wid, "title": plain(title)} for wid, title in RE_WAIVER.findall(text)]
    return items, path.relative_to(project).as_posix()


def collect_questions(project: Path):
    path = project / "logs" / "questions.md"
    text = read_text(path)
    if not text:
        return [], None
    disposition = {qid: status.strip() for qid, status in RE_QDISPOSITION.findall(text)}
    items = []
    for qid, title in RE_QUESTION.findall(text):
        block = text.split("## " + qid, 1)[-1].split("\n## Q-", 1)[0]
        status = disposition.get(qid)
        if not status:
            m = RE_QSTATUS.search(block)
            status = m.group(1).strip() if m else None
        items.append({"id": qid, "title": plain(title), "status": status,
                      "open": bool(status) and "待" in status})
    return items, path.relative_to(project).as_posix()


def collect_decisions(project: Path, limit=4):
    path = project / "logs" / "decisions.md"
    text = read_text(path)
    if not text:
        return [], None
    found = RE_DECISION.findall(text)
    items = [{"id": d, "date": date, "kind": kind, "title": plain(title)} for d, date, kind, title in found]
    return items[-limit:], (items[-1]["id"] if items else None)


def collect_artifacts(project: Path):
    out = {"board_snapshot": None, "gerber_zip": None, "gerber_files": 0,
           "assembly": [], "docs": [], "bom": None, "teaching": None}
    export = project / "export"
    if export.is_dir():
        zips = sorted(export.glob("*.zip"))
        if zips:
            out["gerber_zip"] = zips[0].relative_to(project).as_posix()
        gdir = export / "gerber"
        if gdir.is_dir():
            out["gerber_files"] = len([p for p in gdir.iterdir() if p.is_file()])
        for key, sub in (("assembly", "assembly"), ("docs", "docs")):
            d = export / sub
            if d.is_dir():
                out[key] = sorted(p.name for p in d.iterdir() if p.is_file())
    bom = read_json(project / "spec" / "bom.json")
    if isinstance(bom, dict):
        refs = [k for k in bom if k.startswith("ref-")]
        out["bom"] = {"path": "spec/bom.json", "refs": len(refs),
                      "generic": "generic-0603" in bom}
    reports = project / "reports"
    if reports.is_dir():
        for name in SNAPSHOT_CANDIDATES:
            if (reports / name).is_file():
                out["board_snapshot"] = "reports/" + name
                break
    pdf = project / "teaching" / "lecture.pdf"
    if pdf.is_file():
        teaching = {"pdf": "teaching/lecture.pdf", "pages": None, "backlink_pct": None}
        try:
            import fitz
            teaching["pages"] = fitz.open(str(pdf)).page_count
        except Exception:
            pass
        bl = read_json(project / "teaching" / "_backlink_report.json")
        if isinstance(bl, dict):
            teaching["backlink_pct"] = bl.get("coverage_pct")
        out["teaching"] = teaching
    return out


def main():
    ap = argparse.ArgumentParser(description="聚合项目状态为面板 JSON")
    ap.add_argument("--project", required=True, help="项目 slug 或 projects/<slug> 路径")
    ap.add_argument("--workspace", default=None, help="工作区根（默认按插件位置推断或 PCB_WISH_WORKSPACE）")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    ws = Path(args.workspace) if args.workspace else default_workspace()
    proj = Path(args.project)
    if not proj.is_absolute():
        proj = ws / "projects" / args.project
    if not proj.is_dir():
        print(json.dumps({"error": "项目目录不存在", "path": str(proj)}, ensure_ascii=False))
        return 2

    gates, current, gates_updated = collect_gates(proj)
    waivers, waivers_path = collect_waivers(proj)
    questions, questions_path = collect_questions(proj)
    decisions, last_decision = collect_decisions(proj)
    state = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": {
            "slug": proj.name,
            "path": proj.relative_to(ws).as_posix() if ws in proj.parents else proj.as_posix(),
            "mission": collect_mission(proj),
        },
        "gates": gates,
        "current_gate": current,
        "gates_updated": gates_updated,
        "pending": {
            "waivers": waivers,
            "waivers_path": waivers_path,
            "questions": questions,
            "questions_path": questions_path,
        },
        "decisions": {"recent": decisions, "last": last_decision},
        "artifacts": collect_artifacts(proj),
    }
    print(json.dumps(state, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
