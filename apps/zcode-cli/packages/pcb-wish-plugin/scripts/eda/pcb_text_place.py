#!/usr/bin/env python3
"""pcb_text_place.py — 位号文本归位（丝印范围层收尾）。

**2026-10-22 G3T 站修订（规则纠正，勿回退）**：旧版把"位号回中（文本=器件中心）"当作
唯一解法，搜索序为"中→上→下→左→右→对角"，判据只禁**他件**焊盘/丝印、不禁**自身**丝印——
于是 23/23 个位号全部落在**自己器件的丝印划定范围内**（委托方 2026-10-21 指出）。
现规则（硬判据，见 SKILL.md 驱动纪律 #7）：

  J1 文本盒 ∩ **自身器件丝印轮廓** = ∅   ← 新增，旧版完全没有
  J2 文本盒 ∩ **任何焊盘** = ∅（含自身）
  J3 文本盒 ∩ **他器件覆盖区**（丝印∪焊盘）= ∅
  J4 文本盒 ⊂ 板内，且距板框 ≥0.3mm
  J5 文本盒尺寸 = 0.65×字号×字符数 × 字号（横排/竖排）
  搜索序：**「中」与任何与自身丝印相交的候选直接剔除**；候选方位 上/下/左/右→四对角；
          步长 自自身丝印禁区外扩 0.3/0.5/0.8/1.2/1.8/2.6mm（末档 0.15/0.05 兜底）；
          字号 45→30→20mil；横排→竖排；取第一个满足 J1~J5 的候选。

**锚点口径（G3T 实测标定，见 projects/flexiheal-v2/reports/_g3t_probe.json）**：
  属性 (x,y) 是文本 **em 盒的左下角**（alignMode=3 实测为左下对齐），**不是盒心**：
    rotation=0  → 盒 = [X, X+W] × [Y, Y+H]（字向右、向上）
    rotation=90 → 盒 = [X-H, X] × [Y, Y+W]（画布逆时针，字向左、向上）
  W = 0.65×字号×字符数，H = 字号。旧版按"锚点=盒心"建模，会把文本整体偏移半个盒。

输入：--dump（pcb_dump_pads.py 含 prot 的焊盘 dump）+ --bodies + --texts
退出码: 0=全部安放; 1=存在无法安放的位号（保持原位，报告待处理）。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import run_code, ensure_session  # noqa: E402
import pcb_bodies_lib as B  # noqa: E402

MIL = B.MIL
FS_FALLBACK = 45          # mil，与现有位号一致
BOARD = (0, 0, 45, 30)    # 画布 mm 板框
EDGE_MARGIN = 0.3         # J4：距板框 ≥0.3mm
MARGIN = 0.12             # 文本描边余量（lineWidth 6mil 半宽 0.076 + 字形外伸 ~0.04）
SILK_PAD = 0.13           # 丝印描边余量（画布实测描边宽 ~0.235mm → 半宽 0.118 + 标定余量）
FS_LIST = (45, 30, 20)
STEPS = (0.3, 0.5, 0.8, 1.2, 1.8, 2.6)
STEPS_FINE = (0.15, 0.05)
DIRS = (("上", 0, 1), ("下", 0, -1), ("左", -1, 0), ("右", 1, 0),
        ("左上", -1, 1), ("右上", 1, 1), ("左下", -1, -1), ("右下", 1, -1))


def text_size(fs_mil, value, rot):
    fs = fs_mil / MIL
    w = 0.65 * fs * max(1, len(value))
    h = fs
    if rot % 180:
        w, h = h, w
    return w, h


def text_box(cx, cy, fs_mil, value, rot=0, margin=0.0):
    """位号文本 em 盒（画布 mm，y 向上）；属性 (cx,cy) = em 盒**左下角**。"""
    w, h = text_size(fs_mil, value, rot)
    if rot % 180 == 0:
        x0, x1, y0, y1 = cx, cx + w, cy, cy + h
    else:
        x0, x1, y0, y1 = cx - h, cx, cy, cy + w
    return [x0 - margin, y0 - margin, x1 + margin, y1 + margin]


def own_zone(silk, pads, pad_grow=0.5):
    """自身丝印禁区（画布 mm）。库 dump 的丝印 bbox 退化时（TESTPOINT-TH_BD3.2 的
    L3 原语被 dump 成 [0,0,0,0]，真实丝印是 Ø3.4mm 圆环）→ 退化为"焊盘 hull 外扩"。"""
    w = silk[2] - silk[0]
    h = silk[3] - silk[1]
    if w > 1e-6 and h > 1e-6:
        return [silk[0] - SILK_PAD, silk[1] - SILK_PAD, silk[2] + SILK_PAD, silk[3] + SILK_PAD]
    if not pads:
        return list(silk)
    return [min(r[0] for r in pads) - pad_grow, min(r[1] for r in pads) - pad_grow,
            max(r[2] for r in pads) + pad_grow, max(r[3] for r in pads) + pad_grow]


def judge(tb, own, zone, cov, pads):
    """J1~J4 判定，返回 (是否全绿, 阻塞者说明)。"""
    if B.hit(tb, zone):
        return False, "压自身丝印"
    for dz, rects in pads.items():
        for r in rects:
            if B.hit(tb, r[:4]):
                return False, "压" + dz + "焊盘"
    for dz, box in cov.items():
        if dz != own and B.hit(tb, box):
            return False, "压" + dz + "覆盖区"
    if not (tb[0] >= BOARD[0] + EDGE_MARGIN and tb[1] >= BOARD[1] + EDGE_MARGIN
            and tb[2] <= BOARD[2] - EDGE_MARGIN and tb[3] <= BOARD[3] - EDGE_MARGIN):
        return False, "出板/板边距<0.3mm"
    return True, ""


def candidates(own, zone, fs_list, steps):
    """候选 (dir, step, fs, trot, X, Y, tb)。**「中」被显式剔除**（它必落在自身丝印内）。"""
    sx0, sy0, sx1, sy1 = zone
    scx, scy = (sx0 + sx1) / 2, (sy0 + sy1) / 2
    out = []
    for fs in fs_list:
        for trot in (0, 90):
            w, h = text_size(fs, own, trot)
            for name, ddx, ddy in DIRS:
                for step in steps:
                    if trot == 0:
                        if ddx == 0 and ddy == 1:
                            X, Y = scx - w / 2, sy1 + step
                        elif ddx == 0 and ddy == -1:
                            X, Y = scx - w / 2, sy0 - step - h
                        elif ddx == -1 and ddy == 0:
                            X, Y = sx0 - step - w, scy - h / 2
                        elif ddx == 1 and ddy == 0:
                            X, Y = sx1 + step, scy - h / 2
                        elif ddx == -1 and ddy == 1:
                            X, Y = sx0 - step - w, sy1 + step
                        elif ddx == 1 and ddy == 1:
                            X, Y = sx1 + step, sy1 + step
                        elif ddx == -1 and ddy == -1:
                            X, Y = sx0 - step - w, sy0 - step - h
                        else:
                            X, Y = sx1 + step, sy0 - step - h
                    else:
                        if ddx == 0 and ddy == 1:
                            X, Y = scx + h / 2, sy1 + step
                        elif ddx == 0 and ddy == -1:
                            X, Y = scx + h / 2, sy0 - step - w
                        elif ddx == -1 and ddy == 0:
                            X, Y = sx0 - step, scy - w / 2
                        elif ddx == 1 and ddy == 0:
                            X, Y = sx1 + step + h, scy - w / 2
                        elif ddx == -1 and ddy == 1:
                            X, Y = sx0 - step, sy1 + step
                        elif ddx == 1 and ddy == 1:
                            X, Y = sx1 + step + h, sy1 + step
                        elif ddx == -1 and ddy == -1:
                            X, Y = sx0 - step, sy0 - step - w
                        else:
                            X, Y = sx1 + step + h, sy0 - step - w
                    out.append((name, step, fs, trot, X, Y,
                                text_box(X, Y, fs, own, trot, MARGIN)))
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="reports/pcb_pads_dump.json")
    ap.add_argument("--bodies", default="spec/bodies.json")
    ap.add_argument("--texts", default="reports/pcb_texts_dump.json")
    ap.add_argument("--session", default=None)
    args = ap.parse_args()
    sid = ensure_session(args.session)

    comps = {c["dz"]: c for c in json.loads(Path(args.dump).read_text(encoding="utf-8"))}
    bmap, dmap = B.load(args.bodies)
    texts = json.loads(Path(args.texts).read_text(encoding="utf-8"))

    cov, pads, zone = {}, {}, {}
    for dz, c in comps.items():
        cv, _ = B.cover_abs_mm(dz, c, bmap, dmap)
        pr = B.pad_rects_mm(c)
        sl = B.silk_abs_mm(dz, c, bmap, dmap)
        if sl is None:
            continue
        cov[dz] = cv
        pads[dz] = pr
        zone[dz] = own_zone(sl, pr)

    updates = []
    failures = []
    for t in texts:
        if t.get("key") != "Designator":
            continue
        own = (t.get("value") or "").strip()
        if own not in zone or own not in comps:
            continue
        cur = (t.get("x"), t.get("y"), t.get("fs") or FS_FALLBACK, t.get("rot") or 0)
        placed = None
        dbg = []
        for steps in (STEPS, STEPS_FINE):
            for name, step, fs, trot, X, Y, tb in candidates(own, zone[own], FS_LIST, steps):
                ok, why = judge(tb, own, zone[own], cov, pads)
                if ok:
                    placed = (name, step, fs, trot, X, Y)
                    break
                if len(dbg) < 8:
                    dbg.append(f"{own}@{name} step{step} fs{fs}: {why}")
            if placed:
                break
        if placed:
            name, step, fs, trot, X, Y = placed
            if (round(X * MIL), round(Y * MIL), fs, trot) != cur:
                updates.append({"text": own, "x": round(X * MIL), "y": round(Y * MIL),
                                "dir": name + ("(竖排)" if trot else ""), "fs": fs, "rot": trot})
        else:
            failures.append(own)
            for d in dbg:
                print(f"#   {d}")

    if updates:
        r = run_code("""
const items = __CLI__.args.items;
const out = [];
const aids = await eda.pcb_PrimitiveAttribute.getAllPrimitiveId();
for (const aid of aids) {
  try {
    const a = await eda.pcb_PrimitiveAttribute.get(aid);
    if (a.layer !== 3 || a.key !== 'Designator') continue;
    const hit = items.find(it => it.text === (a.value || '').trim());
    if (!hit) continue;
    const patch = {x: hit.x, y: hit.y};
    if (hit.fs && a.fontSize && hit.fs !== a.fontSize) patch.fontSize = hit.fs;
    if (hit.rot !== undefined && (a.rotation || 0) !== hit.rot) patch.rotation = hit.rot;
    await eda.pcb_PrimitiveAttribute.modify(aid, patch);
    const b = await eda.pcb_PrimitiveAttribute.get(aid);
    out.push({text: hit.text, x: b.x, y: b.y, fs: b.fontSize, rot: b.rotation});
  } catch (e) {}
}
return out;
""", sid, timeout_ms=300000,
             args_json=json.dumps({"items": updates}, ensure_ascii=False))
        if not r.get("ok"):
            print(json.dumps(r, ensure_ascii=False)[:300])
            sys.exit(2)
        done = r.get("value") or []
        print(f"文本归位: 回写 {len(done)}/{len(updates)} 条 "
              f"[{', '.join(u['text'] + '→' + u['dir'] for u in updates)}]")
    else:
        print("文本归位: 全部位号已在自身丝印禁区之外，无需调整")
    save = run_code("await eda.pcb_Document.save(); return 'saved'", sid, timeout_ms=180000)
    print(f"保存: {save.get('value')}")
    if failures:
        print(f"无法安放（保持原位，待处理）: {failures}")
    sys.exit(0 if not failures else 1)


if __name__ == "__main__":
    main()
