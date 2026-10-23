#!/usr/bin/env python3
"""doctor.py — pcb-wish 运行环境自检。

用法:
    python doctor.py [--json]

分级:
  critical  必需（Python 本体）
  eda       阶段B必需（嘉立创EDA专业版桌面客户端 V4.1.60+）
  teach     教学讲义必需（xelatex）
  sim       仿真验证（ngspice）
  route     稠密布线增强（freerouting）
  xcheck    交叉验证可选（kicad-cli）
  vcs       版本管理（git）

退出码: 0=无 critical 失败；1=critical 失败。其余级别只提示不阻断。
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# 嘉立创客户端自动定位（环境变量→PATH→注册表→标准目录→盘根，不写死路径）
sys.path.insert(0, str(Path(__file__).parent / "eda"))
from lceda_detect import find_lceda  # noqa: E402

# lceda-pro --mcp stdio 通道的最低版本（官方 CLI+MCP 于 4.1.60 引入）
MIN_LCEDA_VERSION = (4, 1, 60)


def check_lceda():
    path, source = find_lceda()
    if not path:
        return (False, f"{source}；可用环境变量 PCB_WISH_LCEDA_PATH 显式指定")
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(source))
    if m:
        ver = tuple(map(int, m.groups()))
        if ver < MIN_LCEDA_VERSION:
            return (False, f"检出 {path}（{source}），但版本低于 --mcp stdio 要求的 "
                           f"{'.'.join(map(str, MIN_LCEDA_VERSION))}+，请升级（应用内更新或 pro.lceda.cn 重装）")
        return (True, f"{path}（{source}）")
    return (True, f"{path}（{source}；版本未核验，--mcp stdio 需 {'.'.join(map(str, MIN_LCEDA_VERSION))}+）")


def check_python():
    return (True, ".".join(map(str, sys.version_info[:3])))


def check(name, kind):
    """返回 (ok, detail)。kind: path/which/import/lceda"""
    if kind == "lceda":
        return check_lceda()
    if kind == "which":
        found = shutil.which(name)
        return (found is not None, found or "不在 PATH")
    if kind == "import":
        try:
            __import__(name)
            return (True, "已安装")
        except ImportError:
            return (False, "未安装")
    return (False, "未知检查类型")


CHECKS = [
    ("python", "critical", "check_python"),
    ("lceda-pro", "eda", "lceda"),
    ("xelatex", "teach", "which"),
    ("ngspice", "sim", "which"),
    ("freerouting", "route", "which"),
    ("kicad-cli", "xcheck", "which"),
    ("git", "vcs", "which"),
    ("matplotlib", "sim", "import"),
    ("spicelib", "sim", "import"),
    ("pypdf", "sim", "import"),
]

LABEL = {
    "critical": "必需",
    "eda": "EDA设计(阶段B必需)",
    "teach": "教学讲义",
    "sim": "仿真验证",
    "route": "稠密布线",
    "xcheck": "交叉验证",
    "vcs": "版本管理",
}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="pcb-wish 环境自检")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    results = []
    for name, level, kind in CHECKS:
        if kind == "check_python":
            ok, detail = check_python()
        else:
            ok, detail = check(name, kind)
        results.append({"item": name, "level": level, "level_label": LABEL[level], "ok": ok, "detail": detail})

    critical_fail = any(not r["ok"] for r in results if r["level"] == "critical")

    if args.json:
        print(json.dumps({"ok": not critical_fail, "results": results}, ensure_ascii=False, indent=2))
    else:
        print(f"{'状态':<4} {'级别':<18} {'项目':<12} 详情")
        for r in results:
            mark = "OK " if r["ok"] else "MISS"
            print(f"{mark:<4} {r['level_label']:<18} {r['item']:<12} {r['detail']}")
        print()
        if critical_fail:
            print("结论: 环境不可用（存在必需项缺失）")
        else:
            missing = [r for r in results if not r["ok"]]
            if missing:
                print("结论: 可运行。待补装（不阻断受理，阻断对应阶段）:")
                for r in missing:
                    print(f"  - {r['item']}（影响: {r['level_label']}）")
            else:
                print("结论: 环境完备。")
    sys.exit(1 if critical_fail else 0)


if __name__ == "__main__":
    main()
