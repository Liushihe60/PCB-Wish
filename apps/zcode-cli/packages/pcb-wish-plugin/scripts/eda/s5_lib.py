#!/usr/bin/env python3
"""s5_lib.py — S5 布线终局站共用底座。

纪律（brief_S5_escape.md）：
- 每次 invoke 显式带 session（shell 状态不跨调用保留）
- 中间产物一律落项目目录显式路径（pitfalls #26）
- 不把大 JSON 读进上下文，只读摘要
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import ensure_session, run_code  # noqa: E402

SESSION = "44e574cc-dc99-46dd-80b8-60632f5b075a"
HERE = Path("E:/agent project/projects/flexiheal-v1")
REPORTS = HERE / "reports"
MIL = 39.3700787402


def invoke(code, timeout_ms=590000, session=None, args=None):
    return run_code(code, session or SESSION, timeout_ms=timeout_ms,
                    args_json=json.dumps(args, ensure_ascii=False) if args is not None else None)


def invoke_file(path, timeout_ms=590000):
    return invoke(Path(path).read_text(encoding="utf-8"), timeout_ms=timeout_ms)


def save_json(name, obj, subdir="reports"):
    p = (HERE / subdir / name) if not name.startswith("reports/") else (HERE / name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load_json(name, subdir="reports"):
    return json.loads((HERE / subdir / name).read_text(encoding="utf-8"))


def mm(mil):
    return mil / MIL


def task_y(mil):
    """canvas mil -> 任务系 mm (板高 30mm)"""
    return 30.0 - mil / MIL


def to_task_xy(mx, my):
    return mx / MIL, 30.0 - my / MIL
