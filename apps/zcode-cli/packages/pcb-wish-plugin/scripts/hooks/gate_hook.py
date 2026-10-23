#!/usr/bin/env python3
"""gate_hook.py — PreToolUse 门禁 Hook（PCB 许愿式设计）。

职责：在工具调用真正执行前，用确定性规则拦截「已知会造成实际损失」的操作。
设计原则（重要）：
  - 只拦「判得准」的事，不拦「猜得到」的事——误伤比漏拦更伤工作流；
  - 默认放行：任何解析失败、项目未找到、状态缺失，一律 exit 0（不阻断）；
  - 只读门禁状态，绝不改写 gates.json（写入只允许 gate.py）。

拦截规则（fail-closed 的三条硬门）：
  R1 IR 门未过 → 禁止 EDA 写操作（MISSION 已 PASS 但 G1 未过就动 EDA，等于无图施工）
  R2 原理图门未过 → 禁止 PCB 阶段写操作（G2 未过就摆板，原理图还会改，布局白做）
  R3 禁止直接手改 gates.json（状态必须由 gate.py 写，否则审计链断裂）

stdin: ZCode hook JSON（含 snake_case 兼容字段）
stdout: {"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny",
                               "permissionDecisionReason":"..."}}
退出码 0=放行（含所有异常路径），2=阻断（兼容 Claude 语义）
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from gate import load_state, gate_status  # noqa: E402
except Exception:  # 插件被移动/损坏时绝不阻断工作流
    load_state = gate_status = None


# ---------------------------------------------------------------- 工具分类

# EDA 写操作：MCP 工具名（服务名 lceda-pro → lceda_pro）或驱动脚本调用
EDA_MCP_WRITE = re.compile(
    r"mcp__lceda[_-]?pro__(create|modify|delete|remove|place|move|route|pour|fill|"
    r"import|save|add|set|update|draw|insert|apply|autoRouting|createNetFlag)",
    re.IGNORECASE,
)
EDA_MCP_ANY = re.compile(r"mcp__lceda[_-]?pro__", re.IGNORECASE)
# 驱动脚本（Bash 调用）：判「写」不判「读」——getAll/search/dump/check 放行
EDA_SCRIPT_WRITE = re.compile(
    r"(lceda_invoke\.py|pcb_layout_from_spec|pcb_text_place|pcb_route_from_plan|"
    r"sch_place_from_bom|sch_batch_place|s3_rebuild|s3d_emit|s3d_spread|s4[bcde]_\w*|"
    r"g5_\w*|g6_\w*|route_finish|pcb_merge_solved|pcb_solve_overlaps)",
    re.IGNORECASE,
)
# Bash 命令里出现这些 → 视为 EDA 写（代码里含写原语）
EDA_CODE_WRITE = re.compile(
    r"(\.create[A-Z]\w*\(|\.modify\(|\.delete[A-Z]\w*\(|\.remove[A-Z]\w*\(|"
    r"autoRouting|importChanges|createNetFlag|createPolygon|createVia|createRegion|"
    r"PrimitiveLine\.create|PrimitiveText\.create)",
    re.IGNORECASE,
)
PCB_STAGE_SCRIPT = re.compile(
    r"(pcb_layout_from_spec|pcb_text_place|pcb_route_from_plan|pcb_merge_solved|"
    r"pcb_solve_overlaps|s3_rebuild|s3d_emit|s4[bcde]_\w*|g4r?_\w*|g5\w*_\w*|route_finish)",
    re.IGNORECASE,
)

# 布线类脚本 → 要求 G4（布局门）已过；导出/制造类脚本 → 要求 G5.5（清零门）已过。
# 命名口径与项目历史一致；判错的脚本名不在此列（宁漏拦不误拦）。
ROUTE_STAGE_SCRIPT = re.compile(
    r"(pcb_route_from_plan|pcb_astar\d?|s4b_\w*|s4c_\w*|s4d_\w*|s4e_\w*|route_finish)",
    re.IGNORECASE,
)
EXPORT_STAGE_SCRIPT = re.compile(r"(g6_\w*|run_pdf_export)", re.IGNORECASE)
GATES_FILE = re.compile(r"gates\.json", re.IGNORECASE)


def read_input():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return None
        return json.loads(raw)
    except Exception:
        return None


def tool_name_of(data):
    return (data.get("toolName") or data.get("tool_name") or "").strip()


def tool_input_of(data):
    ti = data.get("toolInput")
    if ti is None:
        ti = data.get("tool_input")
    return ti if isinstance(ti, dict) else {}


def bash_command(tool_input):
    return str(tool_input.get("command") or "")


def is_write_operation(tool, tool_input):
    """返回 (是否写操作, 是否 PCB 阶段写)。保守判定：不确定就按读处理。"""
    if EDA_MCP_ANY.match(tool):
        return bool(EDA_MCP_WRITE.search(tool)), False
    if tool.lower() in ("write", "edit", "notebookedit"):
        path = str(tool_input.get("file_path") or tool_input.get("path") or "")
        if EDA_SCRIPT_WRITE.search(path):
            return True, bool(PCB_STAGE_SCRIPT.search(path))
        return False, False
    if tool == "Bash":
        cmd = bash_command(tool_input)
        script_hit = EDA_SCRIPT_WRITE.search(cmd)
        code_hit = EDA_CODE_WRITE.search(cmd)
        if script_hit or code_hit:
            pcb = bool(PCB_STAGE_SCRIPT.search(cmd))
            return True, pcb
    return False, False


# ---------------------------------------------------------------- 主逻辑

PROJECT_PATH = re.compile(r"projects[\\/]([A-Za-z0-9._-]+)", re.IGNORECASE)


def project_from_tool_input(ti, cwd):
    """优先从工具参数里认出 proyecto 路径，避免多项目工作区里张冠李戴。

    例：file_path="E:/agent project/projects/flexiheal-v1/spec/ir.json"
        command="python ... --spec projects/flexiheal-v1/spec/ir.json"
    """
    blob = json.dumps(ti, ensure_ascii=False)
    for m in PROJECT_PATH.finditer(blob):
        cand = Path(cwd) / "projects" / m.group(1)
        if cand.is_dir():
            return cand.resolve()
    return None


def candidate_projects(cwd):
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


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))
    return 2


def hint_gate(project, gate, title):
    slug = Path(project).name
    return (f"当前项目 {slug} 的 {gate}（{title}）尚未标记 PASS。"
            f"门禁是机械规则（D1 决策）：门不过不前进。\n"
            f"  查看状态: python \"${{CLAUDE_PLUGIN_ROOT}}/scripts/gate.py\" --project {slug} status\n"
            f"  过门登记: python \"${{CLAUDE_PLUGIN_ROOT}}/scripts/gate.py\" --project {slug} pass {gate} "
            f"--evidence <证据文件>\n"
            f"  若确属误判（例如只做只读探测），请改用只读脚本，或在 gate.py 中核对阶段。")


def main():
    data = read_input()
    if not data:
        return 0
    if load_state is None:
        return 0
    event = data.get("hookEventName") or data.get("hook_event_name") or ""
    if event != "PreToolUse":
        return 0

    tool = tool_name_of(data)
    ti = tool_input_of(data)

    # R3：禁止手改门禁状态文件（任何工具、任何路径）
    if GATES_FILE.search(json.dumps(ti, ensure_ascii=False)):
        if tool.lower() in ("write", "edit", "notebookedit") or (
            tool == "Bash" and re.search(r"(>|>>|tee|sed\s+-i|python\s*-\s*<<)", bash_command(ti))
        ):
            return deny("gates.json 是门禁状态唯一事实来源，禁止手工改写——状态只允许由 "
                        "scripts/gate.py 写入（保证审计链完整）。")

    writing, pcb_stage = is_write_operation(tool, ti)
    cmd = bash_command(ti) if tool == "Bash" else ""
    stage_script_hit = bool(ROUTE_STAGE_SCRIPT.search(cmd)) or bool(EXPORT_STAGE_SCRIPT.search(cmd))
    if not writing and not stage_script_hit:
        return 0

    cwd = data.get("cwd") or os.getcwd()
    project = project_from_tool_input(ti, cwd)
    if project is None:
        # ① 环境变量权威声明（驱动层同款约定：pcb-design agent 每项目 export PCB_WISH_PROJECT）
        env = os.environ.get("PCB_WISH_PROJECT")
        if env:
            cand = Path(env)
            if not cand.is_absolute():
                cand = Path(cwd) / cand
            if cand.is_dir() and (cand / "MISSION.md").is_file():
                project = cand.resolve()
    if project is None:
        # ② 工作区仅一个候选才可锁定；多项目且无声明时宁可不拦，也不张冠李戴。
        cands = candidate_projects(cwd)
        if len(cands) == 1:
            project = cands[0]
    if not project:
        return 0  # 项目不明确：放行（不明确时的误拦比漏拦更伤工作流）

    state = load_state(project)
    if state is None:
        # 有项目但没初始化门禁：放行 + 提示（不阻断存量项目）
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": (
                    f"⚠ 项目 {project.name} 尚无 reports/gates.json，门禁未启用。"
                    f"建议运行: python \"${{CLAUDE_PLUGIN_ROOT}}/scripts/gate.py\" init"
                ),
            }
        }, ensure_ascii=False))
        return 0

    g1 = gate_status(state, "G1")
    if g1 != "pass":
        return deny(hint_gate(project, "G1", "IR 门：网表意图 + 断言同源") +
                    "\n  原因：没有通过校验的 IR 就动 EDA，等于无图施工（历史教训："
                    "带缺口开工导致整板返工）。")

    if pcb_stage:
        g2 = gate_status(state, "G2")
        if g2 != "pass":
            return deny(hint_gate(project, "G2", "原理图门：ERC + 网表断言") +
                        "\n  原因：原理图未过门就摆板/布线，原理图一改布局即报废。")


    if ROUTE_STAGE_SCRIPT.search(cmd):
        g4 = gate_status(state, "G4")
        if g4 != "pass":
            return deny(hint_gate(project, "G4", "布局门：重叠/丝印/朝向可布性") +
                        "\n  原因：布局未过门就布线，布线建立在未验收的落位上。")
    if EXPORT_STAGE_SCRIPT.search(cmd):
        g55 = gate_status(state, "G5.5")
        if g55 != "pass":
            return deny(hint_gate(project, "G5.5", "清零门：error/warning 归零或豁免") +
                        "\n  原因：带着未清零的 error/warning 导出制造文件，等于把缺陷烧进板子。")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Hook 自身故障绝不阻断 agent 工作
        sys.exit(0)
