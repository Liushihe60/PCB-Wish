#!/usr/bin/env python3
"""g5_lib.py — G5 铺铜与整板验证站共用底座。

纪律（brief_G5_pour_v2 §1）：
- 每次 invoke 显式带 session（shell 状态不跨调用保留）
- 中间产物落项目目录显式路径（pitfalls #26）
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
SPEC = HERE / "spec"

MIL = 39.3700787402  # 1 mm = 39.3700787 mil


def sid():
    return ensure_session(SESSION)


def invoke(code, timeout_ms=300000, session=None, args_json=None):
    return run_code(code, session or SESSION, timeout_ms=timeout_ms, args_json=args_json)


def invoke_file(path, timeout_ms=300000):
    return invoke(Path(path).read_text(encoding="utf-8"), timeout_ms=timeout_ms)


def save_json(name, obj, subdir="reports"):
    p = (HERE / subdir / name) if not name.startswith("reports/") else (HERE / name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load_json(name, subdir="reports"):
    p = (HERE / subdir / name)
    return json.loads(p.read_text(encoding="utf-8"))


def mm(x):
    return x / MIL
