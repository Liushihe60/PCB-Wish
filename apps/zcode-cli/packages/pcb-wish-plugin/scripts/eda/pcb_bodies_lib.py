#!/usr/bin/env python3
"""pcb_bodies_lib.py — 覆盖区计算共享库（求解器/检查器/导出器三处同一口径）。

覆盖区定义（用户定稿）：元器件"丝印划定范围"内不允许任何覆盖。
  覆盖区 = 从焊盘并集出发，逐个并入丝印层(3)/文档层(48,49)原语包围盒，
  任一原语在某轴超出当前并集 >2mm 判"游离原语"剔除（库缺陷，提问单报告），
  其余并入；最终绕封装原点按元件旋转旋转，取外接框（绝对 mm，dump 空间）。
贴边（矩形相等边相接）不算重叠。
"""
import math

MIL = 39.37
OUTLIER_MM = 3.0  # 原语超出当前并集该值判游离（X1 游离线 7mm 必中；J2/X2/P3 真实体 ≤3mm 保留）


def load(path):
    """读 spec/bodies.json → (footprints_map, by_designator)。"""
    import json
    from pathlib import Path
    b = json.loads(Path(path).read_text(encoding="utf-8"))
    return b["footprints"], b.get("by_designator", {})


def bbox_of_prim(kind, path):
    """单原语 bbox（mil）。POLY=[x0,y0,'L',x1,y1,...]（'ARC' 段=圆角弧，不超出
    相邻直线极值，跳过其 3 个参数）；FILL=[['CIRCLE',x,y,r]|['RECT',...]]。"""
    if kind == "POLY":
        xs, ys = [], []
        i, n = 0, len(path)
        while i < n:
            t = path[i]
            if isinstance(t, str):
                if t == "ARC":
                    i += 4  # ARC + cx + cy + r（圆角弧不外扩包围盒）
                else:
                    i += 1  # L / M 等
                continue
            if i + 1 < n and isinstance(path[i + 1], (int, float)):
                xs.append(t)
                ys.append(path[i + 1])
                i += 2
            else:
                i += 1
        if not xs:
            return None
        return [min(xs), min(ys), max(xs), max(ys)]
    if kind == "FILL":
        lo = [float("inf"), float("inf")]
        hi = [float("-inf"), float("-inf")]
        got = False
        for shp in path or []:
            if not isinstance(shp, list):
                continue
            t = shp[0]
            if t == "CIRCLE" and len(shp) >= 4:
                x, y, r = shp[1], shp[2], abs(shp[3])
                xs, ys = (x - r, x + r), (y - r, y + r)
            elif t == "RECT" and len(shp) >= 5:
                x, y, w, h = shp[1], shp[2], shp[3], shp[4]
                xs, ys = (x, x + w), (y, y + h)
            else:
                continue
            lo = [min(lo[0], xs[0]), min(lo[1], ys[0])]
            hi = [max(hi[0], xs[1]), max(hi[1], ys[1])]
            got = True
        return [lo[0], lo[1], hi[0], hi[1]] if got else None
    return None


def rel_coverage_mil(comp_pads_rel_mil, prim_bboxes_mil, outlier_mm=OUTLIER_MM):
    """封装系覆盖区（mil）。
    comp_pads_rel_mil: 焊盘并集 [x0,y0,x1,y1]（封装系相对原点）。
    prim_bboxes_mil: [(来源名, [x0,y0,x1,y1])] 丝印/文档层各原语。
    返回 (cov [x0,y0,x1,y1] mil, rejected [来源名])——贪心：焊盘并集起步，
    原语按单轴跨度升序并入，任一轴超出当前并集 >outlier_mm 判游离剔除。"""
    cov = list(comp_pads_rel_mil) if comp_pads_rel_mil else None
    rejected = []
    prims = sorted(prim_bboxes_mil, key=lambda p: max(p[1][2] - p[1][0], p[1][3] - p[1][1]))
    if cov is None:
        # 无焊盘（纯丝印件）：从最小原语起步
        if not prims:
            return None, []
        cov = list(prims[0][1])
        prims = prims[1:]
    for name, bb in sorted(prims, key=lambda p: max(p[1][2] - p[1][0], p[1][3] - p[1][1])):
        if (bb[0] < cov[0] - outlier_mm * MIL
                or bb[1] < cov[1] - outlier_mm * MIL
                or bb[2] > cov[2] + outlier_mm * MIL
                or bb[3] > cov[3] + outlier_mm * MIL):
            rejected.append(name)
            continue
        cov = [min(cov[0], bb[0]), min(cov[1], bb[1]), max(cov[2], bb[2]), max(cov[3], bb[3])]
    return cov, rejected


def comp_pads_union_mil(comp):
    """焊盘并集（**画布轴系** mil 相对原点，含焊盘真实旋转）。
    局部系版本见 local_pads_union_mil（cover_abs_mm 管线用）。"""
    lo = [float("inf"), float("inf")]
    hi = [float("-inf"), float("-inf")]
    got = False
    for p in comp["pads"]:
        rw, rh = _pad_half_sizes_mil(p, comp.get("rot", 0) or 0)
        rx, ry = p["x"] - comp["x"], p["y"] - comp["y"]
        lo = [min(lo[0], rx - rw), min(lo[1], ry - rh)]
        hi = [max(hi[0], rx + rw), max(hi[1], ry + rh)]
        got = True
    return [lo[0], lo[1], hi[0], hi[1]] if got else None


def _pad_half_sizes_mil(p, rot_default):
    """焊盘真实外接半尺寸（mil，本地轴）——两层纪律"焊盘真实位置"层：
    pad["pad"] 的 w/h 是焊盘本地轴，按焊盘自转（prot 画布绝对；旧 dump 无 prot
    时退化为组件 rot）旋出真实外接矩形。X2 壳脚 prot=90 案例：横置 6.2×2.5
    曾被按竖置建模，虚减 x 向 3.7mm 占位、漏判 C7×X2 真实重叠。"""
    pd = p.get("pad") or [0, 10, 10]
    w, h = pd[1], (pd[2] if len(pd) > 2 else pd[1])
    prot = p.get("prot")
    th = math.radians((rot_default if prot is None else (prot or 0)) or 0)
    cc, ss = abs(math.cos(th)), abs(math.sin(th))
    return (w * cc + h * ss) / 2, (w * ss + h * cc) / 2


def local_pads_union_mil(comp):
    """焊盘并集（封装**局部系** mil）——焊盘 dump 坐标是画布绝对轴，须逆旋转
    回局部系再与丝印（本地系）同框 union；cover_abs_mm 的整体旋转随后统一施加。"""
    rot = comp.get("rot", 0) or 0
    t0 = math.radians(-rot)
    c0, s0 = math.cos(t0), math.sin(t0)
    lo = [float("inf"), float("inf")]
    hi = [float("-inf"), float("-inf")]
    got = False
    for p in comp["pads"]:
        rw, rh = _pad_half_sizes_mil(p, rot)
        rx, ry = p["x"] - comp["x"], p["y"] - comp["y"]
        lx, ly = rx * c0 - ry * s0, rx * s0 + ry * c0  # 画布轴 → 局部系
        lo = [min(lo[0], lx - rw), min(lo[1], ly - rh)]
        hi = [max(hi[0], lx + rw), max(hi[1], ly + rh)]
        got = True
    return [lo[0], lo[1], hi[0], hi[1]] if got else None


def cover_local_mm(dz, comp, bmap, dmap):
    """封装**局部系**覆盖区（mm）+ 剔除清单：焊盘(局部真实几何)∪丝印层(L3)原语。
    剔除规则与原口径一致（离群>3mm / 病态形状）。旋转统一由 cover_abs_mm 施加。"""
    fp_uuid = dmap.get(dz, "")
    body = bmap.get(fp_uuid, {})
    pads_union = local_pads_union_mil(comp)
    pad_span = [(pads_union[2] - pads_union[0]) if pads_union else 0,
                (pads_union[3] - pads_union[1]) if pads_union else 0]
    prims = [(f"L{p[0]}", p[2]) for p in body.get("prims", [])
             if p[0] == 3 and p[2] is not None]  # 仅丝印层
    kept = []
    rejected = []
    for i, (name, bb) in enumerate(prims):
        sw, sh = bb[2] - bb[0], bb[3] - bb[1]
        if (sw > 2 * pad_span[0] and sw > pad_span[0] + 3 * MIL) or \
           (sh > 2 * pad_span[1] and sh > pad_span[1] + 3 * MIL):
            rejected.append(f"{name}#外形")
            continue
        kept.append((f"{name}#{i}", bb))
    cov_rel, rejected2 = rel_coverage_mil(pads_union, kept, outlier_mm=OUTLIER_MM)
    rejected += rejected2
    if cov_rel is None:
        return None, rejected
    return [v / MIL for v in cov_rel], rejected  # mil → mm


def cover_abs_mm(dz, comp, bmap, dmap):
    """绝对覆盖区（mm，dump 空间）+ 剔除清单。
    覆盖区=丝印层(L3)原语∪焊盘（用户规则：丝印划定范围；文档层/courtyard 不印出不计）。
    两层纪律：焊盘取真实旋转几何（local_pads_union_mil），局部系 union 后按组件
    旋转一次性施加——修复此前"焊盘画布轴系×丝印本地系"混框与旋转件双转误差。"""
    cov_local, rejected = cover_local_mm(dz, comp, bmap, dmap)
    if cov_local is None:
        return None, rejected
    ox, oy = comp["x"] / MIL, comp["y"] / MIL
    rot = comp.get("rot", 0) or 0
    ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    corners = [(cov_local[0], cov_local[1]), (cov_local[2], cov_local[1]),
               (cov_local[2], cov_local[3]), (cov_local[0], cov_local[3])]
    pts = [(ox + px * ca - py * sa, oy + px * sa + py * ca) for px, py in corners]
    cov = [min(p[0] for p in pts), min(p[1] for p in pts),
           max(p[0] for p in pts), max(p[1] for p in pts)]
    return cov, rejected


def pad_rects_mm(comp, margin=0.0):
    """逐焊盘矩形（绝对 mm，dump 空间）——焊盘真实位置层：pad 绝对坐标已含组件
    旋转，尺寸按焊盘自转角 prot（旧 dump 无 prot 时退化用组件 rot）旋出真实外接。"""
    out = []
    rot0 = comp.get("rot", 0) or 0
    for p in comp["pads"]:
        rw, rh = _pad_half_sizes_mil(p, rot0)
        w, h = rw / MIL, rh / MIL
        cx, cy = p["x"] / MIL, p["y"] / MIL
        out.append((cx - w - margin, cy - h - margin, cx + w + margin, cy + h + margin, p["n"]))
    return out


def hit(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def silk_local_mm(bmap, fp_uuid):
    """封装**丝印层(L3)原语并集 bbox**（局部系 mm）——"丝印划定范围"的库口径。
    与 cover_abs_mm 的区别：cover 是"丝印∪焊盘"的**联合包围盒**（含焊盘），
    本函数只给**自身丝印**（不含焊盘），用于"位号文本不得落在自身丝印轮廓内"的判定。
    返回 None 表示该封装没有 L3 原语。"""
    body = bmap.get(fp_uuid, {})
    lo = [float("inf"), float("inf")]
    hi = [float("-inf"), float("-inf")]
    got = False
    for p in body.get("prims", []):
        if p[0] != 3:
            continue
        bb = bbox_of_prim(p[1], p[2])
        if not bb:
            continue
        lo[0] = min(lo[0], bb[0] / MIL)
        lo[1] = min(lo[1], bb[1] / MIL)
        hi[0] = max(hi[0], bb[2] / MIL)
        hi[1] = max(hi[1], bb[3] / MIL)
        got = True
    return [lo[0], lo[1], hi[0], hi[1]] if got else None


def silk_abs_mm(dz, comp, bmap, dmap):
    """自身丝印轮廓（画布绝对 mm，bbox）= silk_local_mm 经器件旋转+平移。"""
    fp = dmap.get(dz, "")
    sl = silk_local_mm(bmap, fp)
    if sl is None:
        return None
    ox, oy = comp["x"] / MIL, comp["y"] / MIL
    rot = comp.get("rot", 0) or 0
    ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    corners = [(sl[0], sl[1]), (sl[2], sl[1]), (sl[2], sl[3]), (sl[0], sl[3])]
    pts = [(ox + px * ca - py * sa, oy + px * sa + py * ca) for px, py in corners]
    return [min(p[0] for p in pts), min(p[1] for p in pts),
            max(p[0] for p in pts), max(p[1] for p in pts)]
