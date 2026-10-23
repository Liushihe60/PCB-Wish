#!/usr/bin/env python3
"""pcb_layout_from_spec.py — 按布局规格画板框 + 逐器件落位（G3）。

输入 spec/layout.json：
  board_mm: [宽, 高]；positions: {位号: [x_mm, y_mm]}
  坐标系=任务书 §5（板左上原点、mm、器件中心值）；canvas 翻转 y：cy=(H-y)mm。
执行：
  1. 层 11 画 4 段板框（mm→mil ×39.37，先按需 setTheNumberOfCopperLayers）；
  2. 逐件 modify {x, y}（PCB 单位 mil）+ 回读，Δ>2mm 记 violation（任务书 §5.2 红线）；
  3. save + 报告落盘。
用法:
    python pcb_layout_from_spec.py --spec spec/layout.json --report reports/layout_report.json
退出码: 0=全部落位且 Δ≤2mm; 1=存在失败/超差。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import ensure_session, run_code  # noqa: E402

MIL = 39.3700787


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--session", default=None)
    args = ap.parse_args()
    sid = ensure_session(args.session)

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    w, h = spec["board_mm"]
    W, H = round(w * MIL), round(h * MIL)  # mil 取整（API 不收浮点）

    # 板框=层 11 闭合折线（单线在板框层会被拒绝，pitfalls 补充）；已有板框时跳过
    outline = run_code(f"""
const exist = await eda.pcb_PrimitivePolyline.getAllPrimitiveId();
if (exist.length) return 'exists';
const poly = eda.pcb_MathPolygon.createPolygon([0, 0, 'L', {W}, 0, {W}, {H}, 0, {H}, 0, 0]);
await eda.pcb_PrimitivePolyline.create('', 11, poly, 6, false);
return 'outline-ok'""", sid, timeout_ms=180000)
    print(f"# 板框: {outline.get('value', outline)}", file=sys.stderr)
    if not outline.get("ok"):
        print("# （板框创建失败，继续落位）", file=sys.stderr)

    scan = run_code("const ids = await eda.pcb_PrimitiveComponent.getAllPrimitiveId();"
                    "const out={}; for (const id of ids) { try { const c = await eda.pcb_PrimitiveComponent.get(id);"
                    "out[c.getState_Designator?.() || c.designator] = id; } catch(e) {} } return out;", sid, timeout_ms=240000)
    if not scan.get("ok"):
        print(json.dumps(scan, ensure_ascii=False)[:300])
        sys.exit(2)
    comps = scan["value"]
    print(f"# PCB 元件 {len(comps)} 件", file=sys.stderr)
    results, failures = [], []

    MOVE_JS = r"""
// 批量落位：一次 invoke 内循环全部器件（防 BETA API 调用次数过载）
const items = __CLI__.args.items;
const out = [];
for (const it of items) {
  try {
    await eda.pcb_PrimitiveComponent.modify(it.id, {x: it.x, y: it.y, rotation: it.rot || 0});
    const back = await eda.pcb_PrimitiveComponent.get(it.id);
    out.push({dz: it.ref, x: back.getState_X?.(), y: back.getState_Y?.(),
              rot: back.rotation, ok: true});
  } catch (e) {
    out.push({dz: it.ref, ok: false, err: String(e).slice(0, 120)});
  }
}
return out;
"""
    items = []
    for ref, pos in spec["positions"].items():
        cid = comps.get(ref)
        if not cid:
            failures.append({"ref": ref, "err": "PCB 无此元件"})
            continue
        xmm, ymm = pos[0], pos[1]
        rot = pos[2] if len(pos) > 2 else 0  # [x,y,rot] 三元组：rot 绕封装原点（度）
        items.append({"ref": ref, "id": cid, "x": round(xmm * MIL), "y": round((h - ymm) * MIL),
                      "rot": rot})
    if items:
        r = run_code(MOVE_JS, sid, timeout_ms=600000,
                     args_json=json.dumps({"items": items}, ensure_ascii=False))
        if not r.get("ok"):
            print(json.dumps(r, ensure_ascii=False)[:300])
            sys.exit(2)
        for v in r.get("value", []):
            if not v.get("ok") or v.get("x") is None:
                failures.append({"ref": v.get("dz"), "err": str(v.get("err", "回读失败"))[:100]})
                continue
            tx = dict((it["ref"], it) for it in items)[v["dz"]]
            dx = abs(v["x"] - tx["x"]) / MIL
            dy = abs(v["y"] - tx["y"]) / MIL
            row = {"ref": v["dz"], "dx_mm": round(dx, 3), "dy_mm": round(dy, 3),
                   "viol": dx > 2 or dy > 2}
            results.append(row)
            if row["viol"]:
                failures.append(row)

    save = run_code("await eda.pcb_Document.save(); return 'saved'", sid, timeout_ms=180000)

    # 位号文本回中（用户两层纪律"丝印范围"层，2026-10-20）：位号=丝印层 L3 Attribute
    # 原语、自带画布绝对坐标，modify 组件**不会**带着文本走——多轮搬移后文本脱位
    # 3.5~6.2mm（J*/U*/L*/P3/X2），压他件丝印/焊盘（曾产生 82 对文本违纪）。
    # 统一回正到组件中心：文本盒必被自身覆盖区包含，覆盖区干净则文本必然干净。
    TEXT_JS = r"""
const ids = await eda.pcb_PrimitiveComponent.getAllPrimitiveId();
const pos = {};
for (const id of ids) {
  try { const c = await eda.pcb_PrimitiveComponent.get(id); pos[c.designator] = {x: c.x, y: c.y}; } catch(e) {}
}
const aids = await eda.pcb_PrimitiveAttribute.getAllPrimitiveId();
const out = {synced: 0, missing: []};
for (const aid of aids) {
  try {
    const a = await eda.pcb_PrimitiveAttribute.get(aid);
    if (a.layer !== 3 || a.key !== 'Designator' || !a.valueVisible) continue;
    const own = pos[(a.value || '').trim()];
    if (!own) { out.missing.push(a.value); continue; }
    if (Math.abs(a.x - own.x) > 1 || Math.abs(a.y - own.y) > 1) {
      await eda.pcb_PrimitiveAttribute.modify(aid, {x: own.x, y: own.y});
      out.synced++;
    }
  } catch (e) {}
}
return out;
"""
    txt = run_code(TEXT_JS, sid, timeout_ms=300000)
    if txt.get("ok"):
        print(f"# 位号文本回中: 同步 {txt['value'].get('synced', 0)} 条"
              f"{'；无主文本 ' + str(txt['value'].get('missing', [])) if txt['value'].get('missing') else ''}",
              file=sys.stderr)
    else:
        print(f"# 位号文本回中失败: {json.dumps(txt, ensure_ascii=False)[:200]}", file=sys.stderr)
    viol = [r for r in results if r.get("viol")]
    rep = {"total": len(spec["positions"]), "moved": len(results), "failures": failures,
           "over2mm": viol, "save": save.get("value")}
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"落位 {len(results)}/{len(spec['positions'])}；失败 {len(failures)}；超差(>2mm) {len(viol)}；"
          f"保存: {save.get('value')}")
    sys.exit(0 if not failures and not viol else 1)


if __name__ == "__main__":
    main()
