#!/usr/bin/env python3
"""lceda_invoke.py — lceda-pro CLI 通用调用器（pcb-wish 驱动层底座）。

用法:
    python lceda_invoke.py --code "return 1+1"
    python lceda_invoke.py --code-file script.js [--session <id>] [--timeout-ms 1800000]
    python lceda_invoke.py --ensure-session            # 无会话时自动 open 一个并打印

行为:
  - 会话解析：--session 显式指定 > 环境变量 PCB_WISH_EDA_SESSION > 自动 `lceda-pro open`
    （GUI 会话；headless 会话不能编辑文档，见 pitfalls #10）并回写环境变量；
  - lceda-pro 可执行文件用 lceda_detect 自动定位（不写死路径），必要时补 PATH；
  - 代码是"异步函数体"：可直接 await / return；--ext-uuid eda 默认附加（完整权限）；
  - 输出统一 JSON：{"ok":..., "value":..., "logs":..., "error":...}（CLI 原样透传）。

驱动纪律（pitfalls #9-13）:
  返回值可能丢失（BETA API）——所有写操作必须回读确认，不信返回值。
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_detect import find_lceda  # noqa: E402


def exe_path():
    p, source = find_lceda()
    if not p:
        print(json.dumps({"ok": False, "error": {"code": "LCEDA_NOT_FOUND", "message": source}}))
        sys.exit(2)
    return p


def ensure_session(explicit=None):
    sid = explicit or os.environ.get("PCB_WISH_EDA_SESSION")
    if sid:
        return sid
    r = subprocess.run([exe_path(), "open"], capture_output=True, text=True, timeout=180,
                       env={**os.environ})
    out = json.loads(r.stdout)
    if not out.get("ok"):
        raise RuntimeError(f"open 失败: {out}")
    sid = out["value"]["sessionId"]
    os.environ["PCB_WISH_EDA_SESSION"] = sid
    print(f"# 新会话: {sid}（已写入 PCB_WISH_EDA_SESSION）", file=sys.stderr)
    return sid


def run_code(code, session=None, ext_uuid="eda", timeout_ms=180000, args_json=None):
    args = [exe_path(), "invoke", "--session", session, "--timeout", str(timeout_ms)]
    if ext_uuid:
        args += ["--ext-uuid", ext_uuid]
    if args_json:
        args += ["--args", args_json]
    args += ["--code", code]
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout_ms / 1000 + 60,
                       encoding="utf-8", errors="replace")
    line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "{}"
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"ok": False, "error": {"code": "PARSE", "message": (r.stdout + r.stderr)[:400]}}


def main():
    ap = argparse.ArgumentParser(description="lceda-pro invoke 通用调用器")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--code", help="异步函数体 JS")
    g.add_argument("--code-file", help="从文件读 JS（UTF-8）")
    ap.add_argument("--session", default=None)
    ap.add_argument("--timeout-ms", type=int, default=180000)
    ap.add_argument("--no-ext", action="store_true", help="不附加 --ext-uuid eda")
    ap.add_argument("--args-json", default=None, help="透传 --args（JSON 字符串，代码内读 __CLI__.args）")
    ap.add_argument("--ensure-session", action="store_true", help="只确保会话存在并打印")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    sid = ensure_session(args.session)
    if args.ensure_session:
        print(json.dumps({"ok": True, "session": sid}))
        return
    code = Path(args.code_file).read_text(encoding="utf-8") if args.code_file else args.code
    out = run_code(code, sid, None if args.no_ext else "eda", args.timeout_ms, args.args_json)
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0 if out.get("ok") else 1)


if __name__ == "__main__":
    main()
