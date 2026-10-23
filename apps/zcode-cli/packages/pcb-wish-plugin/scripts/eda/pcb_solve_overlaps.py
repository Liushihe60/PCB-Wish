#!/usr/bin/env python3
"""pcb_solve_overlaps.py v3 — PCB 重叠自动求解（G3 门禁：丝印覆盖区零重叠）。

覆盖区口径（用户定稿）：每个元器件"丝印划定的范围"内不允许任何覆盖。
  覆盖区=丝印∪文档层∪焊盘逐原语并入（游离原语剔除，见 pcb_bodies_lib.py）；
  碰撞=覆盖区×覆盖区、焊盘×他件覆盖区、焊盘×焊盘；贴边不算重叠。
调度：FIXED 板边/大件不可动 → RELAX 硬件锚点限幅微调（H4 ≤2mm，超差即报告）
  → 派生件自由；预位移（PREMOVE）破贪心死锁；螺旋位移取最近空位；
  旋转（ROTATE 按位号配置，绕封装原点，与 EDA modify rotation 语义一致）。

输入：--dump（pcb_dump_pads.py 导出）+ --bodies（pcb_export_bodies.py 导出）
输出：--out {patch:{位号:{dx_mm,dy_mm,rot,x_mm,y_mm}}, rejected_prims, residual}
坐标为 dump 空间（canvas mm，y 已翻转）；任务 mm = (x, H−y)。
应用：pcb_merge_solved.py → pcb_layout_from_spec.py。

用法:
    python pcb_solve_overlaps.py --dump reports/pcb_pads_dump.json \
        --bodies spec/bodies.json --out spec/layout_solved.json
退出码: 0=零重叠; 1=有残余。
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pcb_bodies_lib as B  # noqa: E402

BOARD = (0.2, 0.2, 44.8, 29.8)
MIL = B.MIL
FIXED = {"J5", "U2", "U1", "J1", "J4", "J2", "ANT1", "BTN1", "BTN2"}
RELAX = {"J3": 2.0, "X1": 2.0, "R_PROG": 3.0, "P3": 3.0,
         "TP1": 2.0, "TP2": 2.0, "TP3": 2.0}
# 预移位目标（任务系绝对坐标 mm，y=板左上原点；求解器按 dump 实位换算 delta）：
# 粘性锚定=主循环不再移动这些件，由人群让位。理由见决策日志 D008。
PREMOVE_T = {"L2": (29.5, 17.6),   # L1/L2 焊盘跨 6.8、任务书间距 3.2 必叠：拉开（dev 2.4→提问单）
             "TP1": (43.2, 17.8),  # TP 三件套排 3.3mm 间距、x 内收防出板（dev ≤0.6）
             "TP2": (43.2, 21.1),
             "TP3": (43.2, 14.5),
             "J3": (25.95, 3.0),   # J3 下移+右移，给 U3 真实本体让位（dev 2.0）
             "X1": (22.45, 7.85),  # Q10 批准换 3225 无源晶振后回位（任务书锚 (22,8.6) dev 0.87）；
                                   #  覆盖区含焊盘：下探 cy+2.05（U1 覆盖区 y 起 10.45 → cy ≤7.90）、
                                   #  西邻 U3 引脚跨东伸至 x=19.85 → cx ≥22.45 才够 0.5 间隙
             "U3": (17.4, 4.5),    # U3 微移左，与 J3/X1 拉开（dev 0.6 ≤2）；上移让窗方案
                                   #  （17.3,3.2）实测撞输入电容 C6，已回退
             "J2": (39.0, 25.65),  # 用户令丝印收边入板：实测丝印底 30.31 出板 0.31 → 收至 29.95
             "R_PROG": (16.2, 7.9),  # 退出 U2/U3 夹缝（真实本体无空间，dev 2.9→提问单）
             "J5": (4.85, 15.0),   # 用户令内收 0.35：实测丝印左缘贴边 0.00（ARC 修复后；旧审计
                                   #  0.33 出板系弧段误判）→ 收后左缘 0.35 留裕量
             "U2": (11.3, 5.5),    # U2/J1 真实本体在任务书坐标下叠 0.2mm：U2 右移 0.3
             "C8": None, "C7": None, "C6": None}  # 电容类不锚定，由求解器迁徙（rmax 15）
ROTATE = {"U4": (90, 270)}  # U4 让 L2 覆盖区：转 90° 收窄 x 向（绕封装原点）


def any_hit(sa, sb):
    if B.hit(sa["cov"], sb["cov"]):
        return True
    return any(B.hit(pa[:4], sb["cov"]) for pa in sa["pads"]) or \
        any(B.hit(pb[:4], sa["cov"]) for pb in sb["pads"]) or \
        any(B.hit(pa[:4], pb[:4]) for pa in sa["pads"] for pb in sb["pads"])


def in_board(cov):
    return not (cov[0] < BOARD[0] or cov[1] < BOARD[1]
                or cov[2] > BOARD[2] or cov[3] > BOARD[3])


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True)
    ap.add_argument("--bodies", default="spec/bodies.json")
    ap.add_argument("--out", default="spec/layout_solved.json")
    ap.add_argument("--gap", type=float, default=0.5,
                    help="覆盖区间最小间隙 mm（焊装间隙；两阶段退火 0.5→1.2）")
    ap.add_argument("--dump-out", default=None,
                    help="把终态写成合成 dump（供下一阶段/检查复用）")
    ap.add_argument("--spread-mm", type=float, default=1.0,
                    help="摊匀目标：覆盖区边缘间距低于该值的对相互推开（默认 1.0）")
    ap.add_argument("--spread-rounds", type=int, default=0,
                    help="摊匀迭代轮数（默认 30；0=关闭）")
    ap.add_argument("--orient-rounds", type=int, default=3,
                    help="朝向优化（HPWL）轮数（默认 3；0=关闭）")
    args = ap.parse_args()
    gap = args.gap
    comps = {c["dz"]: c for c in json.loads(Path(args.dump).read_text(encoding="utf-8"))}
    bmap, dmap = B.load(args.bodies)

    def geom_of(a, dx, dy, deg):
        """覆盖区（外扩 gap/2 焊装间隙）+焊盘，绕封装原点旋转 deg 再平移 dx,dy。"""
        cov = _shift_rot_cov(B, a, comps, bmap, dmap, dx, dy, deg)
        pads = _shift_rot_pads(B, a, comps, dx, dy, deg)
        half = gap / 2
        cov = [cov[0] - half, cov[1] - half, cov[2] + half, cov[3] + half]
        return {"cov": cov, "pads": pads}

    state, rejected_all = {}, {}
    for dz, c in comps.items():
        cov, rej = B.cover_abs_mm(dz, c, bmap, dmap)
        if cov is None:
            print(f"# {dz}: 无覆盖区（无焊盘无丝印）——跳过")
            continue
        if rej:
            rejected_all[dz] = rej
            print(f"# 游离原语剔除: {dz} {rej}（提问单报库缺陷）")
        g = geom_of(dz, 0, 0, c.get("rot", 0) or 0)
        state[dz] = {**g, "ox": c["x"] / MIL, "oy": c["y"] / MIL,
                     "rot": c.get("rot", 0) or 0}

    patch, patch_premove = {}, {}
    for dz, tgt in PREMOVE_T.items():
        if dz not in state or tgt is None:
            continue
        tx, ty = tgt
        c = comps[dz]
        dx = round(tx - c["x"] / MIL, 2)
        dy = round((30 - ty) - c["y"] / MIL, 2)
        g = geom_of(dz, dx, dy, 0)
        state[dz].update({**g, "ox": state[dz]["ox"] + dx, "oy": state[dz]["oy"] + dy})
        patch_premove[dz] = {"dx_mm": dx, "dy_mm": dy, "rot": 0,
                             "x_mm": round(state[dz]["ox"], 2), "y_mm": round(state[dz]["oy"], 2)}

    sticky = {dz for dz, t in PREMOVE_T.items() if t}  # 仅"有目标"的预移位件锚定
    order = sorted(state, key=lambda d: (0 if d in RELAX else 1, d))

    # ---- 力导向摊匀阶段：比装配间隙更宽的目标间距，把过密局部推开（O(n²)/轮）----
    def edge_gap(ca, cb):
        dx = max(cb[0] - ca[2], ca[0] - cb[2], 0.0)
        dy = max(cb[1] - ca[3], ca[1] - cb[3], 0.0)
        return math.hypot(dx, dy)

    for rd in range(args.spread_rounds):
        pushed = 0
        step = {}
        keys = sorted(state)
        for a in keys:
            if a in FIXED or a in sticky:
                continue
            fx = fy = 0.0
            for b in keys:
                if a == b:
                    continue
                g = edge_gap(state[a]["cov"], state[b]["cov"])
                if g >= args.spread_mm:
                    continue
                ca, cb = state[a]["cov"], state[b]["cov"]
                acx, acy = (ca[0] + ca[2]) / 2, (ca[1] + ca[3]) / 2
                bcx, bcy = (cb[0] + cb[2]) / 2, (cb[1] + cb[3]) / 2
                ddx, ddy = acx - bcx, acy - bcy
                d = math.hypot(ddx, ddy) or 1e-6
                w = (args.spread_mm - g) / args.spread_mm
                if b in FIXED or b in sticky:
                    w *= 2.0  # 固定件不动：可动件承担双倍推开量
                fx += ddx / d * w
                fy += ddy / d * w
            if abs(fx) > 0.02 or abs(fy) > 0.02:
                step[a] = (round(fx * 0.6, 2), round(fy * 0.6, 2))
                pushed += 1
        if not step:
            print(f"# 摊匀 pass {rd}: 已达目标间距 {args.spread_mm}mm，提前收敛")
            break
        for a, (dx, dy) in step.items():
            g = geom_of(a, dx, dy, state[a]["rot"])
            state[a].update({**g, "ox": state[a]["ox"] + dx, "oy": state[a]["oy"] + dy})
            patch[a] = {"dx_mm": round(state[a]["ox"] - comps[a]["x"] / MIL, 2),
                        "dy_mm": round(state[a]["oy"] - comps[a]["y"] / MIL, 2),
                        "rot": state[a]["rot"],
                        "x_mm": round(state[a]["ox"], 2), "y_mm": round(state[a]["oy"], 2)}
        keys2 = sorted(state)
        total = sum(1 for i, a in enumerate(keys2) for b in keys2[i + 1:] if any_hit(state[a], state[b]))
        print(f"# 摊匀 pass {rd}: pushed={pushed} 重叠={total}")
        if total > 0:
            print("# 摊匀引入重叠，回退本步并结束摊匀")
            for a, (dx, dy) in step.items():
                g = geom_of(a, -dx, -dy, state[a]["rot"])
                state[a].update({**g, "ox": state[a]["ox"] - dx, "oy": state[a]["oy"] - dy})
            break
    for it in range(80):
        moved = 0
        for a in order:
            if a in FIXED or a in sticky:
                continue
            if not any(any_hit(state[a], state[o]) for o in state if o != a):
                continue
            found = None
            for deg in ROTATE.get(a, ()):
                if deg == state[a]["rot"]:
                    continue
                g = geom_of(a, 0, 0, deg)
                if not in_board(g["cov"]):
                    continue
                if not any(any_hit(g, state[o]) for o in state if o != a):
                    found = (0.0, 0.0, deg)
                    break
            if not found:
                cov = state[a]["cov"]
                cx, cy = (cov[0] + cov[2]) / 2, (cov[1] + cov[3]) / 2
                r, rmax = 0.0, RELAX.get(a, 15.0)
                while r <= rmax and not found:
                    n = max(12, int(2 * math.pi * r / 0.15))
                    for i in range(n + 1):
                        ang = 2 * math.pi * i / n
                        nx, ny = round(cx + r * math.cos(ang), 2), round(cy + r * math.sin(ang), 2)
                        dx, dy = nx - cx, ny - cy
                        g = geom_of(a, dx, dy, state[a]["rot"])
                        if not in_board(g["cov"]):
                            continue
                        if not any(any_hit(g, state[o]) for o in state if o != a):
                            found = (dx, dy, None)
                            break
                    r += 0.2
            if found:
                dx, dy, fr = found
                rot = state[a]["rot"] if fr is None else fr
                g = geom_of(a, dx, dy, rot)
                state[a].update({**g, "ox": state[a]["ox"] + dx, "oy": state[a]["oy"] + dy, "rot": rot})
                patch[a] = {"dx_mm": round(dx, 2), "dy_mm": round(dy, 2), "rot": rot,
                            "x_mm": round(state[a]["ox"], 2), "y_mm": round(state[a]["oy"], 2)}
                moved += 1
        keys = sorted(state)
        total = sum(1 for i, a in enumerate(keys) for b in keys[i + 1:]
                    if any_hit(state[a], state[b]))
        print(f"# pass {it}: moved={moved} 残余={total}")
        if total == 0:
            break
    # ---- 朝向优化（HPWL 贪心）：两引脚件试 4 朝向，降布线难度；不引入重叠 ----
    if args.orient_rounds > 0:
        net_pads = {}   # net -> [绝对 pad 矩形(含当前 state 的平移)]
        pad_owner = {}  # (dz, padn) -> net
        for dz, st in state.items():
            for q in comps[dz]["pads"]:
                net = q.get("net") or ""
                if net:
                    pad_owner[(dz, q["n"])] = net

        def hpwl_total():
            tot = 0.0
            for net, rects in net_pads.items():
                xs0 = min(r[0] for r in rects); xs1 = max(r[2] for r in rects)
                ys0 = min(r[1] for r in rects); ys1 = max(r[3] for r in rects)
                tot += (xs1 - xs0) + (ys1 - ys0)
            return tot

        def rebuild_net_pads():
            for net in list(net_pads):
                del net_pads[net]
            for dz, st in state.items():
                for r in st["pads"]:
                    net = pad_owner.get((dz, r[4]))
                    if net:
                        net_pads.setdefault(net, []).append(r[:4])

        rebuild_net_pads()
        for rd in range(args.orient_rounds):
            improved = 0
            for a in sorted(state):
                if a in FIXED or a in sticky or not (a[0] in "RC" or a.startswith("LED")):
                    continue
                base = hpwl_total()
                best = None
                for deg in (90, 180, 270):
                    g = geom_of(a, 0, 0, deg)
                    if not in_board(g["cov"]):
                        continue
                    old_pads = state[a]["pads"]
                    touched = {pad_owner[(a, r[4])] for r in old_pads if (a, r[4]) in pad_owner}
                    if not touched:
                        continue
                    # 试转：替换该件焊盘后测 HPWL
                    old_rects = {net: [r for r in net_pads[net] if True] for net in touched}
                    for r in old_pads:
                        net = pad_owner.get((a, r[4]))
                        if net:
                            net_pads[net] = [x for x in net_pads[net] if list(x) != list(r[:4])]
                    for r in g["pads"]:
                        net = pad_owner.get((a, r[4]))
                        if net:
                            net_pads[net].append(r[:4])
                    cand = hpwl_total()
                    hit = any(any_hit(g, state[o]) for o in state if o != a)
                    # 回滚
                    rebuild_net_pads()
                    if not hit and cand < base - 0.05 and (best is None or cand < best[1]):
                        best = (deg, cand)
                if best:
                    deg = best[0]
                    g = geom_of(a, 0, 0, deg)
                    state[a].update({**g, "rot": deg})
                    rebuild_net_pads()
                    patch[a] = {"dx_mm": round(state[a]["ox"] - comps[a]["x"] / MIL, 2),
                                "dy_mm": round(state[a]["oy"] - comps[a]["y"] / MIL, 2),
                                "rot": deg,
                                "x_mm": round(state[a]["ox"], 2), "y_mm": round(state[a]["oy"], 2)}
                    improved += 1
            print(f"# 朝向 pass {rd}: improved={improved}")
            if improved == 0:
                break

    keys = sorted(state)
    pairs = [(a, b) for i, a in enumerate(keys) for b in keys[i + 1:]
             if any_hit(state[a], state[b])]
    for k, v in patch_premove.items():
        patch.setdefault(k, v)
    # 全量绝对终态（合并的权威来源：未移动件=输入位置；移动件=最终绝对位置）
    positions_abs = {dz: {"x_mm": round(st["ox"], 2), "y_mm": round(st["oy"], 2),
                          "rot": st["rot"]} for dz, st in state.items()}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"gap_mm": gap, "patch": patch,
                                          "positions_abs": positions_abs,
                                          "rejected_prims": rejected_all,
                                          "residual": pairs}, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    if args.dump_out:
        dump = []
        for dz, st in state.items():
            c = comps[dz]
            rot = st["rot"]
            ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
            ox, oy = st["ox"] * MIL, st["oy"] * MIL
            pads = []
            for q in c["pads"]:
                rx = q["x"] - c["x"]
                ry = q["y"] - c["y"]
                pads.append({"n": q["n"], "net": q.get("net", ""),
                             "x": ox + rx * ca - ry * sa, "y": oy + rx * sa + ry * ca,
                             "pad": q.get("pad")})
            dump.append({"dz": dz, "x": ox, "y": oy, "rot": rot, "pads": pads})
        Path(args.dump_out).write_text(json.dumps(dump, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print(f"求解：移动 {len(patch)} 件；残余 {len(pairs)} 对；游离原语 {len(rejected_all)} 件")
    for a, b in pairs[:8]:
        print("  残余:", a, "×", b)
    sys.exit(0 if not pairs else 1)


def _shift_rot_cov(B, a, comps, bmap, dmap, dx, dy, deg):
    """覆盖区相对原点平移 dx,dy 并旋转 deg（deg 绕封装原点，画布绝对）。
    两层纪律修复：局部系覆盖区（焊盘真实旋转几何+丝印同框）一次施加 deg——
    此前 cover_abs_mm(rot=0) 对已旋转件的画布系焊盘再转 deg，丝印/焊盘混框双转。"""
    comp = comps[a]
    cov_local, _ = B.cover_local_mm(a, comp, bmap, dmap)
    if cov_local is None:
        return None
    ox, oy = comp["x"] / B.MIL + dx, comp["y"] / B.MIL + dy
    ca, sa = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    corners = [(cov_local[0], cov_local[1]), (cov_local[2], cov_local[1]),
               (cov_local[2], cov_local[3]), (cov_local[0], cov_local[3])]
    pts = [(ox + px * ca - py * sa, oy + px * sa + py * ca) for px, py in corners]
    return [min(p[0] for p in pts), min(p[1] for p in pts),
            max(p[0] for p in pts), max(p[1] for p in pts)]


def _shift_rot_pads(B, a, comps, dx, dy, deg):
    """焊盘真实位置层：局部系焊盘（真实自转）绕封装原点施加深 deg 旋转 + 平移，
    外接矩形按 (deg+本地自转) 绝对角旋出——pad 自转丢弃是 C7×X2 漏判根因。"""
    comp = comps[a]
    rot0 = comp.get("rot", 0) or 0
    t0 = math.radians(-rot0)
    c0, s0 = math.cos(t0), math.sin(t0)
    ox, oy = comp["x"] / B.MIL + dx, comp["y"] / B.MIL + dy
    ca, sa = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    out = []
    for p in comp["pads"]:
        pd = p.get("pad") or [0, 10, 10]
        w, h = pd[1] / B.MIL / 2, (pd[2] / B.MIL / 2 if len(pd) > 2 else pd[1] / B.MIL / 2)
        rx, ry = p["x"] / B.MIL - comp["x"] / B.MIL, p["y"] / B.MIL - comp["y"] / B.MIL
        lx, ly = rx * c0 - ry * s0, rx * s0 + ry * c0  # 画布轴 → 局部系
        px, py = ox + lx * ca - ly * sa, oy + lx * sa + ly * ca
        th_l = (p.get("prot") if p.get("prot") is not None else rot0) - rot0  # 焊盘本地自转
        th_eff = deg + th_l
        cc, ss = abs(math.cos(math.radians(th_eff))), abs(math.sin(math.radians(th_eff)))
        nw, nh = w * cc + h * ss, w * ss + h * cc
        out.append((px - nw - 0.05, py - nh - 0.05, px + nw + 0.05, py + nh + 0.05, p["n"]))
    return out


if __name__ == "__main__":
    main()
