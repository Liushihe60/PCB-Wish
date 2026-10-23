#!/usr/bin/env python3
"""pcb_overlap_check.py — PCB 元件重叠检查（G3 验收门禁）。

两级口径：
  默认（--bodies 提供）：**丝印覆盖区口径**（用户定稿规则）——覆盖区=丝印∪文档层
    ∪焊盘逐原语并入（游离原语剔除，见 pcb_bodies_lib.py），禁止 覆盖区×覆盖区、
    焊盘×他件覆盖区、焊盘×焊盘；贴边不算重叠。
  --pads-only：退回旧"焊盘级"口径（逐焊盘外扩 0.1mm），仅作参考。

**两层纪律（用户定稿 2026-10-20，C7×X2 漏判教训）**：
  层1 丝印范围：位号文本=丝印层(L3) Attribute 原语——文本字形盒压**他件**焊盘/丝印盒
     =违纪；文本锚点距本体 >3mm=库锚点异常（审计注记，如 X2 偏移 6mm，报提问单）。
     **2026-10-22 G3T 站新增**：文本压**自身**丝印轮廓 / 自身焊盘同样=违纪
     （"位号不得落在自己器件的丝印划定范围内"——旧版对 own 直接 continue，全站漏判）。
  层2 焊盘真实位置：焊盘矩形按自转角 prot 旋出真实几何（pcb_bodies_lib 统一实现）。

输入：--dump（pcb_dump_pads.py 导出：dz/x/y/rot/pads[+prot]）[+ --bodies spec/bodies.json]
      [+] --texts（位号文本层，默认取 dump 同目录 pcb_texts_dump.json）
退出码: 0=无重叠, 1=存在重叠。
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pcb_bodies_lib as B  # noqa: E402

MIL = B.MIL


def text_box_mm(t):
    """位号文本近似 em 盒（mm，画布系）。

    **2026-10-22 G3T 站修正锚点口径**：属性 (x,y) 实测是 em 盒的**左下角**
    （alignMode=3 = 左下对齐），不是盒心——旧版按"锚点=盒心"建模会让文本整体偏移
    半个盒（实测 U1 位号实际墨迹比旧模型偏 +0.52mm/+0.61mm），判据整体失真。
      rot=0  → [X, X+W] × [Y, Y+H]；rot=90 → [X-H, X] × [Y, Y+W]
    W = 0.65×字号×字符数，H = 字号。"""
    fs = (t.get("fs") or 45) / MIL
    w = 0.65 * fs * max(1, len(t.get("value") or "?"))
    x, y = t["x"] / MIL, t["y"] / MIL
    rot = t.get("rot") or 0
    if rot % 180 == 0:
        return [x, y, x + w, y + fs]
    return [x - fs, y, x, y + w]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True)
    ap.add_argument("--bodies", default=None)
    ap.add_argument("--gap", type=float, default=0.0,
                    help="装配间隙 mm：覆盖区各边外扩 gap/2 后判定（与求解器同口径）")
    ap.add_argument("--pads-only", action="store_true")
    ap.add_argument("--texts", default=None, help="位号文本层 JSON（缺省自动发现，无则跳过文本检查）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    comps = {c["dz"]: c for c in json.loads(Path(args.dump).read_text(encoding="utf-8"))}
    bmap = dmap = None
    if args.bodies and not args.pads_only:
        bmap, dmap = B.load(args.bodies)
    # 丝印 bbox 覆盖表（可选，与 <bodies 同目录的 bodies_silk_override.json）：
    # 某些封装的库封装文档源漏采丝印原语（如 XIAO nRF52840 的 USB/天线舌片外缘），
    # 导致"自身丝印轮廓"偏小 → 位号会压到舌片上。覆盖表按 designator 给局部系 mil bbox。
    silk_override = {}
    if args.bodies:
        ovp = Path(args.bodies).with_name("bodies_silk_override.json")
        if ovp.exists():
            silk_override = json.loads(ovp.read_text(encoding="utf-8"))

    if bmap:
        cov, pads, pathological, own_silk = {}, {}, {}, {}
        for dz, c in comps.items():
            c_dz, rej = B.cover_abs_mm(dz, c, bmap, dmap)
            if c_dz is None:
                continue
            half = args.gap / 2
            cov[dz] = [c_dz[0] - half, c_dz[1] - half, c_dz[2] + half, c_dz[3] + half]
            pads[dz] = B.pad_rects_mm(c)
            sl = B.silk_abs_mm(dz, c, bmap, dmap)
            if dz in silk_override:
                ov = [v / MIL for v in silk_override[dz]]
                ox, oy = c["x"] / MIL, c["y"] / MIL
                rot = c.get("rot", 0) or 0
                ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
                corners = [(ov[0], ov[1]), (ov[2], ov[1]), (ov[2], ov[3]), (ov[0], ov[3])]
                pts = [(ox + px * ca - py * sa, oy + px * sa + py * ca) for px, py in corners]
                sl = [min(q[0] for q in pts), min(q[1] for q in pts),
                      max(q[0] for q in pts), max(q[1] for q in pts)]
            if sl:
                own_silk[dz] = sl
            if rej:
                pathological[dz] = rej
        dzs = sorted(cov)
        pairs = []
        for i, a in enumerate(dzs):
            for b in dzs[i + 1:]:
                if B.hit(cov[a], cov[b]):
                    pairs.append({"a": a, "b": b, "kind": "覆盖区×覆盖区",
                                  "ov": [round(min(cov[a][2], cov[b][2]) - max(cov[a][0], cov[b][0]), 2),
                                         round(min(cov[a][3], cov[b][3]) - max(cov[a][1], cov[b][1]), 2)]})
                    continue
                pa_pb = [(ra, rb) for ra in pads[a] for rb in pads[b] if B.hit(ra[:4], rb[:4])]
                pa_cb = [ra for ra in pads[a] if B.hit(ra[:4], cov[b])]
                pb_ca = [rb for rb in pads[b] if B.hit(rb[:4], cov[a])]
                if pa_pb:
                    pairs.append({"a": a, "b": b, "kind": "焊盘×焊盘",
                                  "pads": [(ra[4], rb[4]) for ra, rb in pa_pb[:4]]})
                elif pa_cb:
                    pairs.append({"a": a, "b": b, "kind": "焊盘×覆盖区",
                                  "pads": [(ra[4], "") for ra in pa_cb[:4]]})
                elif pb_ca:
                    pairs.append({"a": b, "b": a, "kind": "焊盘×覆盖区",
                                  "pads": [(rb[4], "") for rb in pb_ca[:4]]})

        # ---- 层1：位号文本=丝印（C7×X2 教训）——只在 gap0 口径判（gap0.5 的 0.25
        # 外扩环下文本天然穿邻件，判了全是噪声）----
        notes = []
        pairs_txt = []
        tp = args.texts
        if tp is None:
            cand = Path(args.dump).parent / "pcb_texts_dump.json"
            tp = str(cand) if cand.exists() else None
        texts = json.loads(Path(tp).read_text(encoding="utf-8")) if tp else []
        desig = [t for t in texts if t.get("key") == "Designator"]
        for t in desig:
            own = (t.get("value") or "").strip()
            if own not in cov:
                continue
            tb = text_box_mm(t)
            ox_c = abs(t["x"] / MIL - comps[own]["x"] / MIL)
            oy_c = abs(t["y"] / MIL - comps[own]["y"] / MIL)
            # **2026-10-22 G3T 站修正**：旧版"文本锚点距本体 >3mm = 库锚点异常"的注记
            # 建立在"位号回中"前提上；新规则要求位号落在**自身丝印之外**，与本体拉开
            # 几毫米是**正常**结果（U1 的位号合法偏移 14.3mm）。该注记改为只在
            # "文本压在自身丝印上"（即真·锚点/库缺陷）时才报。
            _sl0 = own_silk.get(own)
            if (ox_c ** 2 + oy_c ** 2) ** 0.5 > 3.0 and _sl0 and B.hit(tb, _sl0):
                notes.append({"dz": own, "text_at_mm": [round(t["x"] / MIL, 2), round(t["y"] / MIL, 2)],
                              "body_at_mm": [round(comps[own]["x"] / MIL, 2), round(30 - comps[own]["y"] / MIL, 2)],
                              "offset_mm": [round(t["x"] / MIL - comps[own]["x"] / MIL, 2),
                                            round(t["y"] / MIL - comps[own]["y"] / MIL, 2)]})
            # **J1/J2（2026-10-22 G3T 站新增）：位号文本不得落在自身丝印轮廓内、
            # 不得压自身焊盘**——旧版对 own 直接 continue，23/23 个位号落在自身丝印内
            # 而全站"零违规"。这一条是 G4 布局门的 lint 项。
            sl = own_silk.get(own)
            if sl and B.hit(tb, sl):
                pairs_txt.append({"a": own, "b": own, "kind": "文本×自身丝印范围", "ov": True})
            own_pad_hits = [rb[4] for rb in pads.get(own, []) if B.hit(tb, rb[:4])]
            if own_pad_hits:
                pairs_txt.append({"a": own, "b": own, "kind": "文本×自身焊盘",
                                  "pads": [(own, pb) for pb in own_pad_hits[:2]]})
            for b in dzs:
                if b == own:
                    continue
                pad_hits = [rb[4] for rb in pads[b] if B.hit(tb, rb[:4])]
                if pad_hits:
                    pairs_txt.append({"a": own, "b": b, "kind": "文本×他件焊盘",
                                      "pads": [(own, pb) for pb in pad_hits[:2]]})
                elif B.hit(tb, cov[b]):
                    pairs_txt.append({"a": own, "b": b, "kind": "文本×他件丝印范围", "ov": True})
        if args.gap == 0:
            pairs.extend(pairs_txt)

        if args.json:
            print(json.dumps({"overlaps": pairs, "count": len(pairs),
                              "rejected_prims": pathological, "text_anchor_notes": notes},
                             ensure_ascii=False, indent=1))
        else:
            for p in pairs:
                det = p.get("ov") or p.get("pads")
                print(f"OVERLAP  {p['a']} × {p['b']}   {p['kind']} {det}")
            for dz, rej in pathological.items():
                print(f"# 游离原语剔除（提问单报库缺陷）: {dz} {rej}")
            for n in notes:
                print(f"# 文本锚点异常（库缺陷，提问单报备）: {n['dz']} 文本{n['text_at_mm']} "
                      f"本体{n['body_at_mm']} 偏移{n['offset_mm']}")
            print(f"\n结论: {'无重叠（丝印覆盖区口径+文本层）' if not pairs else f'存在 {len(pairs)} 对重叠'}")
        sys.exit(0 if not pairs else 1)

    # 旧焊盘级口径（--pads-only / 无 bodies）
    margin = 0.1
    prs = {c["dz"]: B.pad_rects_mm(c, margin) for c in comps.values()}
    refs = sorted(prs)
    pairs = []
    for i, a in enumerate(refs):
        for b in refs[i + 1:]:
            hitp = [(ra, rb) for ra in prs[a] for rb in prs[b] if B.hit(ra[:4], rb[:4])]
            if hitp:
                pairs.append({"a": a, "b": b, "pads": [(ra[4], rb[4]) for ra, rb in hitp][:4]})
    if args.json:
        print(json.dumps({"overlaps": pairs, "count": len(pairs)}, ensure_ascii=False, indent=1))
    else:
        for p in pairs:
            print(f"OVERLAP  {p['a']} × {p['b']}   焊盘对: {p['pads']}")
        print(f"\n结论: {'无重叠（焊盘级）' if not pairs else f'存在 {len(pairs)} 对重叠'}")
    sys.exit(0 if not pairs else 1)


if __name__ == "__main__":
    main()
