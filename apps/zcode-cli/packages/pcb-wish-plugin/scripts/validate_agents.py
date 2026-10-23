#!/usr/bin/env python3
"""validate_agents.py — 插件 agent 定义离线校验器。

按 ZCode 运行时解析器（subagent-profile-parser.ts）的关键规则离线自检，
在把插件装进客户端之前抓掉「枚举能看到、运行时 ENOENT」的坑。

校验规则（与源码一致，源码位置见校验报告）：
  R1 frontmatter 必须含 name 与 description（name 为空 → agent_invalid_name）
  R2 文件名必须等于 frontmatter name（loader 用 name 拼路径 agents/<name>.md）
  R3 tools/skills/mcpServers 允许 list 或逗号字符串
  R4 color ∈ {red,blue,green,yellow,purple,orange,pink,cyan}，非法 → 丢弃并告警
  R5 permissionMode ∈ 8 值枚举
  R6 mcpServers 为列表（null → agent_invalid_mcp_servers）
  R7 正文非空（body 即该 agent 的完整系统提示词）
"""
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(json.dumps({"ok": False, "error": "需要 PyYAML"}, ensure_ascii=False))
    sys.exit(2)

VALID_COLORS = {"red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"}
VALID_PERMISSION_MODES = {"acceptEdits", "auto", "bypassPermissions", "default", "dontAsk", "plan"}


def parse_frontmatter(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return None, "缺少 --- frontmatter 围栏"
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        return None, f"YAML 解析失败: {e}"
    if not isinstance(fm, dict):
        return None, "frontmatter 不是映射"
    return fm, None


def as_list(v):
    if v is None:
        return None
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return None


def validate_file(path, plugin_root):
    issues, warns = [], []
    text = path.read_text(encoding="utf-8")
    fm, err = parse_frontmatter(text)
    if err:
        return {"file": path.name, "ok": False, "issues": [err]}
    stem = path.stem
    name = fm.get("name")
    if not name:
        issues.append("缺少 name")
    elif name != stem:
        issues.append(f"R2 name({name}) != 文件名({stem}) — loader 用 name 拼 agents/<name>.md，会 ENOENT")
    if not fm.get("description"):
        issues.append("缺少 description")
    body = text.split("---", 2)[2].strip() if text.count("---") >= 2 else ""
    if not body:
        issues.append("正文为空——正文即该 agent 的完整系统提示词")
    for field in ("tools", "skills", "mcpServers"):
        if field in fm and as_list(fm[field]) is None:
            issues.append(f"{field} 必须是 list 或逗号字符串")
    if fm.get("mcpServers") is None and "mcpServers" in fm:
        issues.append("R6 mcpServers 不能为 null")
    color = fm.get("color")
    if color and color not in VALID_COLORS:
        warns.append(f"color '{color}' 不在合法集内，运行时会静默丢弃")
    pm = fm.get("permissionMode")
    if pm and pm not in VALID_PERMISSION_MODES:
        issues.append(f"R5 permissionMode '{pm}' 非法；合法值 {sorted(VALID_PERMISSION_MODES)}")
    agents_dir = path.parent.name == "agents"
    if not agents_dir:
        issues.append("必须放在插件根下的 agents/ 目录（loader 硬编码路径）")
    return {"file": path.name, "name": name, "ok": not issues, "issues": issues,
            "warnings": warns, "tools": as_list(fm.get("tools")),
            "skills": as_list(fm.get("skills")),
            "mcpServers": as_list(fm.get("mcpServers"))}


def main():
    plugin_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    agents_dir = plugin_root / "agents"
    if not agents_dir.is_dir():
        print(json.dumps({"ok": False, "error": f"{agents_dir} 不存在"}, ensure_ascii=False))
        return 2
    results = [validate_file(p, plugin_root) for p in sorted(agents_dir.glob("*.md"))]
    ok = bool(results) and all(r["ok"] for r in results)
    print(json.dumps({"ok": ok, "agents": results}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
