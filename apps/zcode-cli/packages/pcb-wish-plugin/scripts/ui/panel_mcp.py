#!/usr/bin/env python3
"""panel_mcp.py — PCBWish 项目面板 MCP 服务（stdio，零依赖 stdlib）。

职责：
  - 向宿主提供两个 App-only 工具（visibility=["app"]，不进模型上下文，零提示词污染）：
      pcb_wish_state    → 复用 scripts/ui/panel_state.py 聚合门禁/挂账/决策/交付物
      pcb_wish_snapshot → 读项目板级快照 PNG → dataUri
  - 提供 ui://pcb-wish/panel.html 资源（mimeType=text/html;profile=mcp-app），宿主据此渲染面板页。

协议：MCP stdio（换行分隔 JSON-RPC 2.0）。实现 initialize / notifications/initialized /
ping / tools/list / tools/call / resources/list / resources/read；未知方法回 -32601。

项目解析：环境变量 PCB_WISH_PROJECT > 工作区（ZCODE_PROJECT_DIR/PCB_WISH_WORKSPACE）
下唯一含 MISSION.md 的 projects/* 候选；找不到时工具返回可读错误，不崩溃。
"""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_CANDIDATES = ("_g6_pcb_top.png", "_g5f_stitch.png", "_g4r_board_full.png", "_g3t_board.png")
PROTOCOL_VERSION = "2025-06-18"


def log(msg):
    print(f"pcb-wish-panel: {msg}", file=sys.stderr)


def resolve_workspace():
    for key in ("ZCODE_PROJECT_DIR", "PCB_WISH_WORKSPACE", "CLAUDE_PROJECT_DIR"):
        v = os.environ.get(key)
        if v and Path(v).is_dir():
            return Path(v)
    return Path.cwd()


def resolve_project(ws: Path):
    env = os.environ.get("PCB_WISH_PROJECT")
    if env:
        p = Path(env)
        if not p.is_absolute():
            p = ws / p
        if (p / "MISSION.md").is_file():
            return p
    root = ws / "projects"
    if root.is_dir():
        cands = [x for x in root.iterdir() if x.is_dir() and (x / "MISSION.md").is_file()]
        if len(cands) == 1:
            return cands[0]
    return None


def list_candidates(ws: Path):
    root = ws / "projects"
    if root.is_dir():
        return sorted(x.name for x in root.iterdir() if x.is_dir() and (x / "MISSION.md").is_file())
    return []


def pick_project(ws: Path, params: dict):
    """优先级：工具入参 project > 环境变量 PCB_WISH_PROJECT > 工作区唯一候选。多候选返回 None。"""
    slug = (params or {}).get("project")
    if slug:
        p = Path(slug) if Path(slug).is_absolute() else ws / "projects" / slug
        if (p / "MISSION.md").is_file():
            return p
    proj = resolve_project(ws)
    if proj is not None:
        return proj
    cands = list_candidates(ws)
    if len(cands) > 1:
        return None, (f"工作区有多个项目：{', '.join(cands)}。"
                      f"请在调用参数里指定 project（如 {{\"project\": \"{cands[0]}\"}}），"
                      f"或设环境变量 PCB_WISH_PROJECT。")
    if len(cands) == 0:
        return None, "工作区 projects/ 下没有含 MISSION.md 的项目目录。"
    return None, "项目解析失败。"


def run_state(ws: Path, params: dict = None):
    got = pick_project(ws, params)
    if isinstance(got, tuple):
        return {"isError": True, "content": [{"type": "text", "text": got[1]}]}
    proj = got
    cmd = [sys.executable, str(PLUGIN_ROOT / "scripts" / "ui" / "panel_state.py"),
           "--project", str(proj), "--workspace", str(ws)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if r.returncode != 0:
        return {"isError": True, "content": [{"type": "text", "text": r.stderr.strip() or "panel_state.py 失败"}]}
    return {"content": [{"type": "text", "text": r.stdout}], "structuredContent": json.loads(r.stdout)}


def run_snapshot(ws: Path, params: dict = None):
    got = pick_project(ws, params)
    if isinstance(got, tuple):
        return {"isError": True, "content": [{"type": "text", "text": got[1]}]}
    proj = got
    rel = None
    for name in SNAPSHOT_CANDIDATES:
        if (proj / "reports" / name).is_file():
            rel = "reports/" + name
            break
    if rel is None:
        return {"content": [{"type": "text", "text": "（无板级快照）"}], "structuredContent": {"dataUri": None}}
    data = (proj / rel).read_bytes()
    uri = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    return {"content": [{"type": "text", "text": rel}], "structuredContent": {"dataUri": uri, "path": rel}}


def read_panel_html():
    p = PLUGIN_ROOT / "ui" / "panel.html"
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8")


TOOLS = [
    {
        "name": "pcb_wish_state",
        "description": "PCBWish 项目面板状态：门禁流水线、挂账（豁免/上报问题）、最近决策、交付物。",
        "inputSchema": {"type": "object", "properties": {"project": {"type": "string", "description": "项目 slug；缺省按 PCB_WISH_PROJECT 或工作区唯一候选"}}, "additionalProperties": False},
        "_meta": {"ui": {"resourceUri": "ui://pcb-wish/panel.html", "visibility": ["app"]}},
    },
    {
        "name": "pcb_wish_snapshot",
        "description": "PCBWish 板级快照 PNG（dataUri）。App-only。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "_meta": {"ui": {"resourceUri": "ui://pcb-wish/panel.html", "visibility": ["app"]}},
    },
]

PANEL_URI = "ui://pcb-wish/panel.html"


def handle(req, ws):
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": req.get("params", {}).get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False}},
            "serverInfo": {"name": "pcb-wish-panel", "version": "0.3.1"},
        }}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = (req.get("params") or {}).get("name")
        if name == "pcb_wish_state":
            return {"jsonrpc": "2.0", "id": rid, "result": run_state(ws, (req.get("params") or {}).get("arguments"))}
        if name == "pcb_wish_snapshot":
            return {"jsonrpc": "2.0", "id": rid, "result": run_snapshot(ws, (req.get("params") or {}).get("arguments"))}
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"unknown tool {name}"}}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"resources": [
            {"uri": PANEL_URI, "name": "PCBWish 项目面板", "mimeType": "text/html;profile=mcp-app"}]}}
    if method == "resources/read":
        uri = (req.get("params") or {}).get("uri")
        if uri != PANEL_URI:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"unknown resource {uri}"}}
        html = read_panel_html()
        if html is None:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "panel.html 缺失"}}
        return {"jsonrpc": "2.0", "id": rid, "result": {"contents": [
            {"uri": PANEL_URI, "mimeType": "text/html;profile=mcp-app", "text": html}]}}
    if rid is not None:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return None


def main():
    ws = resolve_workspace()
    log(f"workspace={ws}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            log(f"bad json: {line[:80]}")
            continue
        try:
            resp = handle(req, ws)
        except Exception as e:  # 单请求失败不拖垮服务
            log(f"handler error: {e}")
            resp = {"jsonrpc": "2.0", "id": req.get("id"),
                    "error": {"code": -32603, "message": f"internal error: {e}"}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
