#!/usr/bin/env python3
"""netlist_export.py — 从 EDA 导出网表并转换为断言用摘要（G2 门）。

流程：sch_ManufactureData.getNetlistFile() → sys_FileSystem.saveFileToFileSystem
落盘 .enet → Python 解析（components[ggeN].props.Designator + pinInfoMap[p].net）
→ {"nets": {NET: ["位号.引脚号", ...]}} 写 spec/exported_netlist.json。

用法:
    python netlist_export.py --enet reports/netlist_export.enet --out spec/exported_netlist.json
退出码: 0=成功; 1=失败。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import ensure_session, run_code  # noqa: E402

EXPORT_JS = r"""
const f = await eda.sch_ManufactureData.getNetlistFile();
if (!f) return null;
await eda.sys_FileSystem.saveFileToFileSystem(__CLI__.args.path, f);
return 'saved';
"""


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--enet", default="reports/netlist_export.enet")
    ap.add_argument("--out", default="spec/exported_netlist.json")
    ap.add_argument("--session", default=None)
    args = ap.parse_args()
    sid = ensure_session(args.session)

    r = run_code(EXPORT_JS, sid, timeout_ms=300000,
                 args_json=json.dumps({"path": str(Path(args.enet).resolve())}, ensure_ascii=False)
                 .replace("\\", "/"))
    v = r.get("value") if r.get("ok") else None
    enet_path = Path(args.enet)
    if not v or not enet_path.exists():
        print(json.dumps(r, ensure_ascii=False)[:300])
        sys.exit(1)

    d = json.loads(enet_path.read_text(encoding="utf-8"))
    nets = {}
    for gge, c in d.get("components", {}).items():
        dz = (c.get("props") or {}).get("Designator")
        if not dz:
            continue
        for num, pin in (c.get("pinInfoMap") or {}).items():
            net = pin.get("net") or ""
            if not net:
                continue
            nets.setdefault(net, []).append(f"{dz}.{pin.get('number', num)}")
    for net in nets:
        nets[net].sort()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"nets": nets}, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    nn = sum(len(v) for v in nets.values())
    print(f"网表导出：{len(nets)} 网 / {nn} 引脚 → {args.out}")
    sys.exit(0)


if __name__ == "__main__":
    main()
