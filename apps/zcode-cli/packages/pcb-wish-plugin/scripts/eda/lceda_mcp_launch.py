#!/usr/bin/env python3
"""lceda_mcp_launch.py — 嘉立创EDA专业版 MCP stdio 启动器（插件 mcpServers 调用）。

职责：自动定位本机嘉立创EDA专业版客户端（lceda_detect.find_lceda，不写死路径），
拉起 `<client> --mcp stdio` 并把 stdio 原样转发给宿主（PCB Wish MCP 框架）。

纪律：
  - 本进程的 stdout 是 MCP JSON-RPC 信道，任何日志只准写 stderr；
  - 未检出客户端 → stderr 说明 + 退出码 2（宿主显示 server failed，不崩会话）；
  - 转发进程随宿主退出而退出（wait + 传播退出码）。
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lceda_detect import find_lceda  # noqa: E402


def main():
    path, source = find_lceda()
    if not path:
        print(
            "lceda_mcp_launch: 未找到嘉立创EDA专业版客户端（需 V4.1.60+）。\n"
            f"  检测详情: {source}\n"
            "  处理: 安装后重启会话；或将环境变量 PCB_WISH_LCEDA_PATH 指向客户端 exe。",
            file=sys.stderr,
        )
        return 2
    print(f"lceda_mcp_launch: 启动 {path} --mcp stdio（来源: {source}）", file=sys.stderr)
    proc = subprocess.Popen(
        [path, "--mcp", "stdio"],
        stdin=sys.stdin,
        stdout=sys.stdout,
    )
    return proc.wait()


if __name__ == "__main__":
    sys.exit(main())
