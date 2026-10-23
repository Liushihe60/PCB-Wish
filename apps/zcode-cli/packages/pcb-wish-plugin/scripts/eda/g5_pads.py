#!/usr/bin/env python3
"""g5_pads.py — 导出全板焊盘几何（层别/形状/尺寸/旋转）并合并进快照文件。

用法: python g5_pads.py [--merge _s5_snapshot_pre_pour.json]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from g5_lib import invoke, save_json, load_json  # noqa: E402

DUMP = r"""
const ids = await eda.pcb_PrimitivePad.getAllPrimitiveId();
const out = [];
for (const id of ids) {
  try { const p = await eda.pcb_PrimitivePad.get(id);
    out.push({id, net: p.net, x: p.x, y: p.y, layer: p.layer,
      pad: p.pad, rotation: p.rotation, padNumber: p.padNumber,
      hole: p.hole, padType: p.padType});
  } catch(e) { out.push({id, err: String(e).slice(0,120)}); }
}
return out;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge", default=None, help="合并进指定快照文件")
    ap.add_argument("--out", default="_s5_pads.json")
    args = ap.parse_args()
    r = invoke(DUMP, timeout_ms=300000)
    if not r.get("ok"):
        print(json.dumps(r, ensure_ascii=False)[:500])
        sys.exit(1)
    pads = r["value"]
    save_json(args.out, pads)
    print("焊盘", len(pads), "→ ", args.out)
    if args.merge:
        snap = load_json(args.merge)
        snap["pads"] = pads
        save_json(args.merge, snap)
        print("已合并进", args.merge)


if __name__ == "__main__":
    main()
