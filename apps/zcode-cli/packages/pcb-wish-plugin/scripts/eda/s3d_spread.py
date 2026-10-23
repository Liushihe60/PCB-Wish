#!/usr/bin/env python3
"""s3d_spread.py — S3D 温和疏散布局求解（D026：只疏散自由件）。

输入：reports/pcb_pads_dump_s3din.json + spec/bodies.json + reports/pcb_texts_dump_s3din.json
      + reports/_s3d_tracks_vias_in.json + reports/_s3d_free_analysis.json
输出：reports/_s3d_solve_result.json（自由件新位+判据账）

目标（brief T2）：自由件摊入右侧稀疏带（任务系 x28.9~39.7 / y12.4~24.0）与就近空当，
最小化局部密度（Σcell·dens² 代理）。
硬约束：
  H1 覆盖区 gap0.5 装配间隙（cov 各边外扩 0.25 判交，同 pcb_solve_overlaps 口径）；
  H2 焊盘×他件覆盖区（外扩 0.25）/ 焊盘×焊盘（原始）零重叠（两层纪律·pad_rects_mm 含 prot）；
  H3 覆盖区在板内（0,0,45,30）；焊盘铜距板边 ≥0.3mm（EDGE_MM）；
  H4 新覆盖区+0.25 不碰**同层(顶层)走线**与过孔（"其余走线禁动"红线）；
  H5 不压任何在位位号文本盒（文本=丝印 L3，两层纪律）。
软约束：带外惩罚 / 位移温和 / 指示件网分区亲和（弱）/ R16-LED_C 链长（CHRGA 两成员网）。
坐标：dump 空间 canvas mm（任务 mm y=30−y_canvas）。
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pcb_bodies_lib as B  # noqa: E402

PROJ = Path("E:/agent project/projects/flexiheal-v1")
MIL = B.MIL
H = 30.0
BOARD = (0.0, 0.0, 45.0, 30.0)
EDGE_MM = 0.30            # 板边铜距
GAP = 0.5                 # 装配间隙
CELL = 1.5                # 密度网格 mm
BAND_T = (28.9, 12.4, 39.7, 24.0)   # 任务系稀疏带
BAND_C = (BAND_T[0], H - BAND_T[3], BAND_T[2], H - BAND_T[1])

W_BAND = 3.0      # 每件带外罚（按带外距离 mm 计）
W_DISP = 0.01     # 位移²
W_ZONE = 0.002    # 网分区亲和²
W_CHAIN = 0.01    # 链距²（超出 5mm 部分）
CHAIN_MAX = 5.0

FREE = ["TP3", "LED_C", "R16", "LED_A"]   # 求解顺序：TP3 焊盘最大 → 指示链 → LED_A
REFINE_ROUNDS = 4


def rect_hit(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def inter_area(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def inflate(r, m):
    return [r[0] - m, r[1] - m, r[2] + m, r[3] + m]


def main():
    dump = json.loads((PROJ / "reports/pcb_pads_dump_s3din.json").read_text(encoding="utf-8"))
    texts = json.loads((PROJ / "reports/pcb_texts_dump_s3din.json").read_text(encoding="utf-8"))
    tv = json.loads((PROJ / "reports/_s3d_tracks_vias_in.json").read_text(encoding="utf-8"))
    analysis = json.loads((PROJ / "reports/_s3d_free_analysis.json").read_text(encoding="utf-8"))
    free_refs = analysis["free_refs"]
    assert sorted(free_refs) == sorted(FREE), f"free set 不一致: {free_refs}"

    comps = {c["dz"]: c for c in dump}
    bmap, dmap = B.load(PROJ / "spec/bodies.json")

    # ---- 几何：覆盖区/焊盘（原始位）/文本盒 ----
    cov, pads = {}, {}
    for dz, c in comps.items():
        cv, rej = B.cover_abs_mm(dz, c, bmap, dmap)
        assert cv is not None, f"{dz} 无覆盖区"
        cov[dz] = cv
        pads[dz] = B.pad_rects_mm(c)
        if rej:
            print(f"# 游离原语剔除 {dz}: {rej}")
    text_box = {}
    for t in texts:
        if t.get("key") != "Designator":
            continue
        own = (t.get("value") or "").strip()
        fs = (t.get("fs") or 45) / MIL
        w = 0.65 * fs * max(1, len(own))
        x, y = t["x"] / MIL, t["y"] / MIL
        text_box[own] = [x - w / 2, y - fs / 2, x + w / 2, y + fs / 2]

    # ---- 障碍：同层（顶层=1）走线 + 全部过孔 ----
    track_obs, via_obs = [], []
    for l in tv["lines"]:
        if l.get("layer") != 1:
            continue
        hw = (l.get("w") or 10) / MIL / 2
        x1, y1, x2, y2 = l["x1"] / MIL, l["y1"] / MIL, l["x2"] / MIL, l["y2"] / MIL
        track_obs.append({"box": [min(x1, x2) - hw, min(y1, y2) - hw,
                                  max(x1, x2) + hw, max(y1, y2) + hw],
                          "net": l["net"]})
    for v in tv["vias"]:
        r = (v.get("d") or 24) / MIL / 2
        via_obs.append({"cx": v["x"] / MIL, "cy": v["y"] / MIL, "r": r, "net": v["net"]})

    # ---- 网分区锚（弱亲和）= 各网固定成员焊盘质心（canvas mm）----
    ir = json.loads((PROJ / "spec/ir.json").read_text(encoding="utf-8"))
    net_members = {n["name"]: n["members"] for n in ir["nets"]}

    def net_anchor(net):
        pts = []
        for m in net_members.get(net, []):
            ref, pin = m.split(".")
            if ref in FREE or ref not in comps:
                continue
            for p in comps[ref]["pads"]:
                if str(p["n"]) == str(pin):
                    pts.append((p["x"] / MIL, p["y"] / MIL))
        if not pts:
            return None
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    zone_anchor = {}
    for dz in FREE:
        zs = [net_anchor(n) for n in sorted({p.get("net") for p in comps[dz]["pads"]} - {""})]
        zone_anchor[dz] = [z for z in zs if z]

    # ---- 密度场 ----
    nx, ny = int(45 / CELL) + 1, int(30 / CELL) + 1
    dens = [[0.0] * ny for _ in range(nx)]

    def field_update(box, sign):
        for i in range(max(0, int(box[0] / CELL)), min(nx, int(box[2] / CELL) + 1)):
            for j in range(max(0, int(box[1] / CELL)), min(ny, int(box[3] / CELL) + 1)):
                cellrect = [i * CELL, j * CELL, (i + 1) * CELL, (j + 1) * CELL]
                dens[i][j] += sign * inter_area(box, cellrect) / (CELL * CELL)

    for dz in comps:
        if dz not in FREE:
            field_update(cov[dz], +1)

    def dens_cost(box):
        s = 0.0
        for i in range(max(0, int(box[0] / CELL)), min(nx, int(box[2] / CELL) + 1)):
            for j in range(max(0, int(box[1] / CELL)), min(ny, int(box[3] / CELL) + 1)):
                cellrect = [i * CELL, j * CELL, (i + 1) * CELL, (j + 1) * CELL]
                d = inter_area(box, cellrect) / (CELL * CELL)
                s += (dens[i][j] + d) ** 2 - dens[i][j] ** 2
        return s

    # ---- 自由件当前世界（动态）：world_cov 外扩 0.25；world_pads 原始 ----
    world_cov, world_pads = {}, {}

    def sync_world(dz=None, remove=False):
        """把某自由件（按其原始位）加入/移出障碍世界。"""
        if dz is None:
            for d0 in comps:
                if d0 not in FREE:
                    world_cov[d0] = inflate(cov[d0], GAP / 2)
                    world_pads[d0] = pads[d0]
            return
        if remove:
            world_cov.pop(dz, None)
            world_pads.pop(dz, None)
        else:
            cx, cy = placed[dz]
            dx = cx - comps[dz]["x"] / MIL
            dy = cy - comps[dz]["y"] / MIL
            world_cov[dz] = inflate([cov[dz][0] + dx, cov[dz][1] + dy,
                                     cov[dz][2] + dx, cov[dz][3] + dy], GAP / 2)
            world_pads[dz] = [(r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy, r[4]) for r in pads[dz]]

    sync_world()
    placed = {}   # dz -> (cx, cy) canvas mm

    def geom_at(dz, cx, cy):
        dx = cx - comps[dz]["x"] / MIL
        dy = cy - comps[dz]["y"] / MIL
        box = [cov[dz][0] + dx, cov[dz][1] + dy, cov[dz][2] + dx, cov[dz][3] + dy]
        prs = [(r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy, r[4]) for r in pads[dz]]
        return box, prs

    def hard_ok(dz, cx, cy):
        box, prs = geom_at(dz, cx, cy)
        if box[0] < BOARD[0] or box[1] < BOARD[1] or box[2] > BOARD[2] or box[3] > BOARD[3]:
            return None, "出板"
        for r in prs:
            if r[0] < EDGE_MM or r[1] < EDGE_MM or r[2] > 45 - EDGE_MM or r[3] > 30 - EDGE_MM:
                return None, "板边铜距<0.3"
        ib = inflate(box, GAP / 2)
        for oz, ob in world_cov.items():
            if oz == dz:
                continue
            if rect_hit(ib, ob):
                return None, f"覆盖区×{oz}"
            for r in prs:
                if rect_hit(r[:4], ob):
                    return None, f"焊盘×{oz}覆盖区"
        for oz, oprs in world_pads.items():
            if oz == dz:
                continue
            for r in prs:
                for orr in oprs:
                    if rect_hit(r[:4], orr[:4]):
                        return None, f"焊盘×{oz}焊盘"
        ibq = inflate(box, GAP / 2)
        for t in track_obs:
            if rect_hit(ibq, t["box"]):
                return None, f"走线({t['net']})"
        for v in via_obs:
            if rect_hit(ibq, [v["cx"] - v["r"], v["cy"] - v["r"],
                              v["cx"] + v["r"], v["cy"] + v["r"]]):
                return None, f"过孔({v['net']})"
        for oz, tb in text_box.items():
            if oz == dz:
                continue
            if rect_hit(box, tb):
                return None, f"压{oz}文本"
        return box, None

    def soft_cost(dz, box, cx, cy):
        j = dens_cost(box)
        ddx = max(BAND_C[0] - cx, 0, cx - BAND_C[2])
        ddy = max(BAND_C[1] - cy, 0, cy - BAND_C[3])
        j += W_BAND * math.hypot(ddx, ddy)
        dx0, dy0 = cx - comps[dz]["x"] / MIL, cy - comps[dz]["y"] / MIL
        j += W_DISP * (dx0 * dx0 + dy0 * dy0)
        for zx, zy in zone_anchor[dz]:
            j += W_ZONE * ((cx - zx) ** 2 + (cy - zy) ** 2)
        if dz == "R16" and "LED_C" in placed:
            lx, ly = placed["LED_C"]
            d = math.hypot(cx - lx, cy - ly) - CHAIN_MAX
            if d > 0:
                j += W_CHAIN * d * d
        return j

    def place(dz, step=0.25):
        w = cov[dz][2] - cov[dz][0]
        h = cov[dz][3] - cov[dz][1]
        best = None
        world_cov.pop(dz, None)
        world_pads.pop(dz, None)
        x0 = BOARD[0] + EDGE_MM
        while x0 <= BOARD[2] - EDGE_MM - w + 1e-9:
            y0 = BOARD[1] + EDGE_MM
            while y0 <= BOARD[3] - EDGE_MM - h + 1e-9:
                cx, cy = x0 + w / 2, y0 + h / 2
                box, why = hard_ok(dz, cx, cy)
                if box is not None:
                    j = soft_cost(dz, box, cx, cy)
                    if best is None or j < best[0]:
                        best = (j, cx, cy, box)
                y0 += step
            x0 += step
        return best

    for rnd in range(REFINE_ROUNDS):
        for dz in FREE:
            placed.pop(dz, None)
            sync_world(dz, remove=True)
            best = place(dz)
            assert best is not None, f"{dz} 全板无合法位（轮{rnd}）"
            j, cx, cy, box = best
            placed[dz] = (cx, cy)
            sync_world(dz)
        pos = " ".join(f"{d}=({placed[d][0]:.2f},{placed[d][1]:.2f})" for d in FREE)
        print(f"# 精化轮 {rnd}: {pos}")

    # ---- 自由件必须真的位移（判据2：位移集合=自由集恰全集）----
    for dz in FREE:
        c = comps[dz]
        d0 = math.hypot(placed[dz][0] - c["x"] / MIL, placed[dz][1] - c["y"] / MIL)
        if d0 >= 1.0:
            continue
        bx = (BAND_C[0] + BAND_C[2]) / 2
        by = (BAND_C[1] + BAND_C[3]) / 2
        ang = math.atan2(by - placed[dz][1], bx - placed[dz][0])
        found = None
        for dist in (1.5, 2.0, 2.5, 3.0):
            for da in (0, -0.4, 0.4, -0.8, 0.8):
                cx = placed[dz][0] + dist * math.cos(ang + da)
                cy = placed[dz][1] + dist * math.sin(ang + da)
                box, why = hard_ok(dz, cx, cy)
                if box is not None:
                    found = (cx, cy)
                    break
            if found:
                break
        assert found, f"{dz} 原位最优且无法微移"
        placed[dz] = found
        sync_world(dz, remove=True)
        sync_world(dz)
        print(f"# {dz} 原位最优 → 强制微移 {dist:.1f}mm（位移=自由集恰全集）")

    # ---- 终位复核 ----
    for dz in FREE:
        cx, cy = placed[dz]
        box, why = hard_ok(dz, cx, cy)
        assert box is not None, f"{dz} 终位复核失败: {why}"

    # ---- 输出 ----
    out = {"moved": {}, "band_task_mm": BAND_T, "weights":
           {"W_BAND": W_BAND, "W_DISP": W_DISP, "W_ZONE": W_ZONE, "W_CHAIN": W_CHAIN}}
    for dz in FREE:
        c = comps[dz]
        cx, cy = placed[dz]
        out["moved"][dz] = {
            "old_canvas_mm": [round(c["x"] / MIL, 3), round(c["y"] / MIL, 3)],
            "new_canvas_mm": [round(cx, 3), round(cy, 3)],
            "old_task_mm": [round(c["x"] / MIL, 3), round(H - c["y"] / MIL, 3)],
            "new_task_mm": [round(cx, 3), round(H - cy, 3)],
            "rot": c.get("rot", 0) or 0,
            "old_canvas_mil": [round(c["x"]), round(c["y"])],
            "new_canvas_mil": [round(cx * MIL), round(cy * MIL)],
        }
    (PROJ / "reports/_s3d_solve_result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out["moved"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
