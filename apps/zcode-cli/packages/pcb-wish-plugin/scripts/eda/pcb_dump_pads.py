#!/usr/bin/env python3
"""pcb_dump_pads.py — 从 EDA 画布导出全部 PCB 元件焊盘（G3 重叠检查输入）。

输出 reports/pcb_pads_dump.json：[{dz, x, y, pads:[{n,x,y,net,pad:[type,w,h]}]}]
坐标为 canvas mil（y 已翻转：任务 mm y = H − mil_y/39.37）。

用法:
    python pcb_dump_pads.py --out reports/pcb_pads_dump.json
退出码: 0=成功（含落盘校验）; 1=失败。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import ensure_session, run_code  # noqa: E402

DUMP_JS = r"""
const ids = await eda.pcb_PrimitiveComponent.getAllPrimitiveId();
const out = [];
for (const id of ids) {
  try {
    const c = await eda.pcb_PrimitiveComponent.get(id);
    const pins = await eda.pcb_PrimitiveComponent.getAllPinsByPrimitiveId(id);
    const pads = (pins || []).map(p => ({
      n: p.padNumber, x: p.x, y: p.y, net: p.net || "", pad: p.pad || null,
      prot: p.rotation || 0,  // 焊盘自转角（画布绝对）——X2 壳脚 90° 案例：丢弃会虚减真实占位
    }));
    out.push({dz: c.designator, x: c.x, y: c.y, rot: c.rotation || 0, pads});
  } catch (e) { out.push({dz: "?" + id, err: String(e).slice(0, 80)}); }
}
return out;
"""

TEXTS_JS = r"""
// 位号/值文本=丝印层(L3) Attribute 原语（用户两层纪律"丝印范围"层）：
// 文本有独立画布坐标，须随件核查（X2 文本锚点偏移 6mm 案例来自库缺陷）。
const ids = await eda.pcb_PrimitiveAttribute.getAllPrimitiveId();
const out = [];
for (const id of ids) {
  try {
    const a = await eda.pcb_PrimitiveAttribute.get(id);
    if (a.layer !== 3 || !a.valueVisible) continue;
    out.push({key: a.key, value: a.value, x: a.x, y: a.y,
              fs: a.fontSize || 0, parent: a.parentPrimitiveId || ""});
  } catch (e) {}
}
return out;
"""


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/pcb_pads_dump.json")
    ap.add_argument("--texts-out", default=None,
                    help="位号文本层（L3 Attribute）导出路径，默认与 --out 同目录 pcb_texts_dump.json")
    ap.add_argument("--skip-texts", action="store_true", help="跳过文本层导出")
    ap.add_argument("--session", default=None)
    args = ap.parse_args()
    sid = ensure_session(args.session)
    r = run_code(DUMP_JS, sid, timeout_ms=300000)
    if not r.get("ok"):
        print(json.dumps(r, ensure_ascii=False)[:400])
        sys.exit(1)
    comps = r["value"]
    bad = [c for c in comps if "err" in c or not c.get("pads")]
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(comps, ensure_ascii=False, indent=1), encoding="utf-8")
    n_txt = None
    if not args.skip_texts:
        rt = run_code(TEXTS_JS, sid, timeout_ms=180000)
        if rt.get("ok"):
            texts = rt["value"]
            n_txt = len(texts)
            tp = Path(args.texts_out) if args.texts_out else outp.parent / "pcb_texts_dump.json"
            tp.write_text(json.dumps(texts, ensure_ascii=False, indent=1), encoding="utf-8")
        else:
            print(f"# 文本层导出失败: {json.dumps(rt, ensure_ascii=False)[:200]}")
    print(f"导出 {len(comps)} 件 / {sum(len(c.get('pads', [])) for c in comps)} 焊盘 → {args.out}"
          f"{'；异常 ' + str(len(bad)) if bad else ''}"
          f"{f'；文本 {n_txt} 条' if n_txt is not None else ''}")
    sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    main()
