#!/usr/bin/env python3
"""lceda_detect.py — 嘉立创EDA专业版客户端自动定位（所有调用方共用，不写死路径）。

查找顺序（命中即返回）:
  0) 环境变量 PCB_WISH_LCEDA_PATH（用户/部署显式覆盖，最高优先级）
  1) PATH 上的 lceda-pro / easyeda-pro（安装器通常会写入）
  2) Windows 注册表卸载项（DisplayName 含 lceda/easyeda/嘉立创）→
     InstallLocation / DisplayIcon 反推 exe
  3) 标准安装目录（Program Files × 2）
  4) 各盘根目录扫描（X:\\lceda-pro\\lceda-pro.exe 等，覆盖"装到自选盘根"）

用法:
    from lceda_detect import find_lceda
    path, source = find_lceda()   # source=检出方式说明；path 可为 None
    python lceda_detect.py        # 独立运行打印检测结果，退出码 0/1
"""
import os
import shutil
import sys
from pathlib import Path

EXE_NAMES = ("lceda-pro.exe", "easyeda-pro.exe")
INSTALL_DIRS = ("lceda-pro", "easyeda-pro")
STD_DIRS = [
    r"C:\Program Files",
    r"C:\Program Files (x86)",
]
REGISTRY_KEYS = [
    "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
    "SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
]
NAME_HINTS = ("lceda", "easyeda", "嘉立创")


def _exe_in_dir(d):
    for exe in EXE_NAMES:
        p = Path(d) / exe
        if p.is_file():
            return p
    return None


def _from_env():
    v = os.environ.get("PCB_WISH_LCEDA_PATH")
    if v and Path(v).is_file():
        return str(Path(v)), "环境变量 PCB_WISH_LCEDA_PATH"
    return None, None


def _from_path():
    for exe in EXE_NAMES:
        w = shutil.which(exe[:-4] if exe.endswith(".exe") else exe) or shutil.which(exe)
        if w:
            return str(Path(w)), "PATH"
    return None, None


def _from_registry():
    try:
        import winreg
    except ImportError:
        return None, None  # 非 Windows
    views = [0]  # 0 = 默认视图
    if sys.maxsize > 2**32:
        views += [winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY]
    roots = [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]
    for root in roots:
        for regkey in REGISTRY_KEYS:
            for view in views:
                try:
                    hk = winreg.OpenKey(root, regkey, 0, winreg.KEY_READ | view)
                except OSError:
                    continue
                try:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(hk, i)
                            i += 1
                        except OSError:
                            break
                        try:
                            sk = winreg.OpenKey(hk, sub)
                        except OSError:
                            continue
                        try:
                            name, _ = winreg.QueryValueEx(sk, "DisplayName")
                        except OSError:
                            continue
                        if not any(h in str(name).lower() or h in str(name) for h in NAME_HINTS):
                            continue
                        # InstallLocation 优先，DisplayIcon 兜底（形如 "C:\...\x.exe,0"）
                        for field, clean in (("InstallLocation", False), ("DisplayIcon", True)):
                            try:
                                v, _ = winreg.QueryValueEx(sk, field)
                            except OSError:
                                continue
                            v = str(v).split(",")[0].strip().strip('"')
                            if not v:
                                continue
                            cands = [_exe_in_dir(v)] if not clean else [Path(v)]
                            for p in cands:
                                if p and p.is_file():
                                    return str(p), f"注册表（{name}）"
                            if not clean:
                                p = _exe_in_dir(v)
                                if p:
                                    return str(p), f"注册表（{name}）"
                finally:
                    hk.Close()
    return None, None


def _from_std_dirs():
    for d in STD_DIRS:
        for app in INSTALL_DIRS:
            p = _exe_in_dir(Path(d) / app)
            if p:
                return str(p), f"标准目录（{d}\\{app}）"
    return None, None


def _from_drive_roots():
    # 覆盖"装到自选盘根"：E:\lceda-pro\lceda-pro.exe 等
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        for app in INSTALL_DIRS:
            p = Path(f"{letter}:\\") / app
            if p.is_dir():
                exe = _exe_in_dir(p)
                if exe:
                    return str(exe), f"盘根目录（{p}）"
    return None, None


def find_lceda():
    """返回 (exe路径或None, 检出来源说明)。"""
    for fn in (_from_env, _from_path, _from_registry, _from_std_dirs, _from_drive_roots):
        path, source = fn()
        if path:
            return path, source
    return None, "未找到（PATH/注册表/标准目录/盘根均未检出嘉立创EDA专业版）"


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    path, source = find_lceda()
    if path:
        print(f"OK    {path}  （来源: {source}）")
        sys.exit(0)
    print(f"MISS  {source}")
    sys.exit(1)
