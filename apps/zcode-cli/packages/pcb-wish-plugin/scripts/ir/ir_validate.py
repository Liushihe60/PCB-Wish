#!/usr/bin/env python3
"""ir_validate.py — 设计 IR 结构校验器（pcb-wish G1 门）。

不依赖 jsonschema 包，按 scripts/ir/ir_schema.json v0.1 的字段手工校验，
两者字段必须同步维护。

用法:
    python ir_validate.py <ir.json> [--blocks <blocks目录>] [--json]
退出码: 0=PASS, 1=FAIL。
"""
import argparse
import json
import re
import sys
from pathlib import Path

PIN_RE = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")
MOD_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def problems_of(ir, blocks_dir=None):
    problems = []

    def bad(loc, msg):
        problems.append({"location": loc, "message": msg})

    # meta
    meta = ir.get("meta")
    if not isinstance(meta, dict):
        bad("meta", "缺少 meta 对象")
    else:
        if not meta.get("name"):
            bad("meta.name", "name 不能为空")
        if not DATE_RE.match(str(meta.get("created", ""))):
            bad("meta.created", "created 须为 YYYY-MM-DD")
        if not meta.get("mission"):
            bad("meta.mission", "缺少 mission 路径（回链 MISSION.md）")

    # stackup
    st = ir.get("stackup")
    if not isinstance(st, dict) or "layers" not in st:
        bad("stackup", "缺少 stackup.layers")
    else:
        layers = st["layers"]
        if not (isinstance(layers, int) and 1 <= layers <= 4):
            bad("stackup.layers", f"层数 {layers} 超出 [1,4]（护栏：≤4 层）")

    # modules
    mods = ir.get("modules")
    if not isinstance(mods, list) or not mods:
        bad("modules", "modules 必须为非空数组")
        mods = []
    mod_ids, pin_owner = set(), {}
    for m in mods:
        mid = m.get("id")
        if not mid or not MOD_ID_RE.match(str(mid)):
            bad(f"modules[{m}].id", f"模块 id 非法: {mid}")
            continue
        if mid in mod_ids:
            bad(f"modules[{mid}].id", "模块 id 重复")
        mod_ids.add(mid)
        if not m.get("name"):
            bad(f"modules[{mid}].name", "name 不能为空")
        if not m.get("type"):
            bad(f"modules[{mid}].type", "type 不能为空")
        for pin in m.get("pins", []):
            if not PIN_RE.match(str(pin)):
                bad(f"modules[{mid}].pins", f"引脚格式非法（应 <元件>.<引脚>）: {pin}")
            elif pin in pin_owner:
                bad(f"modules[{mid}].pins", f"引脚 {pin} 重复出现在 {pin_owner[pin]} 与 {mid}")
            else:
                pin_owner[pin] = mid

    # blocks 引用检查
    if blocks_dir:
        blocks_dir = Path(blocks_dir)
        available = set()
        if blocks_dir.exists():
            for bfile in blocks_dir.rglob("block.json"):
                try:
                    b = json.loads(bfile.read_text(encoding="utf-8"))
                    available.add(b.get("id", ""))
                except Exception:  # noqa: BLE001
                    pass
        for m in mods:
            ref = m.get("block_ref")
            if ref and available and ref not in available:
                bad(f"modules[{m.get('id')}].block_ref", f"电路块 {ref} 不在 blocks/ 中")

    # nets
    nets = ir.get("nets")
    if not isinstance(nets, list) or not nets:
        bad("nets", "nets 必须为非空数组")
        nets = []
    net_names = set()
    for n in nets:
        name = n.get("name")
        if not name:
            bad("nets", "存在无 name 的网络")
            continue
        if name in net_names:
            bad("nets", f"网络重名: {name}")
        net_names.add(name)
        members = n.get("members")
        if not isinstance(members, list) or len(members) < 2:
            bad(f"nets[{name}]", "网络成员须 ≥2 个引脚")
            continue
        for pin in members:
            if not PIN_RE.match(str(pin)):
                bad(f"nets[{name}]", f"引脚格式非法: {pin}")
            elif str(pin) not in pin_owner:
                bad(f"nets[{name}]", f"引脚 {pin} 未归属任何模块（悬空引脚）")

    # constraints h_ref 回链
    for c in ir.get("constraints", []) or []:
        if not re.match(r"^H\d+$", str(c.get("h_ref", ""))):
            bad(f"constraints[{c.get('id')}]", f"h_ref 非法: {c.get('h_ref')}（应 Hn，回链 MISSION.md）")
        if not c.get("statement"):
            bad(f"constraints[{c.get('id')}]", "statement 不能为空")

    t = ir.get("teaching", {})
    if t and "enabled" in t and not isinstance(t["enabled"], bool):
        bad("teaching.enabled", "enabled 须为布尔值")

    return problems


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="设计 IR 校验（G1 门）")
    ap.add_argument("ir")
    ap.add_argument("--blocks", default=None, help="blocks/ 目录（校验 block_ref 存在性）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
        problems = problems_of(ir, args.blocks)
    except Exception as e:  # noqa: BLE001
        problems = [{"location": "file", "message": str(e)}]
    ok = not problems
    if args.json:
        print(json.dumps({"ok": ok, "problems": problems}, ensure_ascii=False, indent=2))
    else:
        print("PASS：IR 校验通过，可驱动 EDA" if ok else "FAIL：IR 校验未通过")
        for p in problems:
            print(f"  [FAIL] {p['location']}: {p['message']}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
