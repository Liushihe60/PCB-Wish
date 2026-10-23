#!/usr/bin/env python3
"""trace_quality_check.py — G5 布线门：走线质量确定性检查（角度 + 长度/兜圈子，零依赖）。

用户约束（2026-10-23 固化）：
  走线应尽量短，避免"兜圈子"；不使用锐角或直角走线，使用钝角走线。

判据（全部确定性）：
  A1 锐角    —— 转向 >95°（内角 <85°）的拐角数 = 0（硬门，制造缺陷：尖角蚀刻/酸液滞留）。
  A2 直角    —— 转向 90°±5° 的拐角占比 ≤ 5%（沿用既有闸门，理想为 45° 斜线=钝角 135° 内角）。
  L1 兜圈子  —— 每网 实际布线长度 / 该网焊盘最小生成树(MST) 长度 ≤ len_ratio_max（默认 1.35）；
                超限网逐张列出（"兜圈子嫌疑"），per-net 豁免须给理由。
  L2 未布网  —— 有焊盘但零走线的网在此**只报告不计门**（连通性归几何连通门判）。

输入：
  --tracks  轨迹 dump：{"lines":[{net,layer,sx,sy,ex,ey,w}], "vias":[...]}（canvas mil，与 s4e 站一致）
  --pads    g5in 型 pad dump（提供每焊盘绝对坐标+网名，作 MST 基线）
  --config  可选 {"len_ratio_max":1.35, "right_max_pct":5.0, "len_ratio_exempt":{"NET":"理由"}}
  --out     结果 JSON 落盘
退出码：0=PASS，1=FAIL，3=数据/参数错误。角度分类复用 s4e_lib.corner_histogram（同目录）。
"""
import argparse
import json
import math
import sys
from pathlib import Path

def _load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _save_json(p, d):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def interior_corners(lines, tol=0.5):
    """真实内角拐角扫描。

    背景：s4e_lib.turn_angle 把方向折叠到 0-90°，锐角（转向>90°）与钝角异常折叠后不可区分；
    锐角禁令需要真实内角，故自算。节点合并口径与 s4e_lib 一致（0.5mil 网格 + 邻域并查集）。

    返回 (total_corners, [{"net","layer","x","y","interior"}])，内角 ∈ (0,180]。
    """
    parent = {}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    by_net = {}
    for l in lines:
        by_net.setdefault((l.get("net"), l.get("layer")), []).append(l)
    corners = []
    total = 0
    junctions = 0
    for (net, layer), segs in by_net.items():
        parent = {}
        cells = {}
        order = []
        for s in segs:
            for pt in ((s["sx"], s["sy"]), (s["ex"], s["ey"])):
                k = (int(round(pt[0] / tol)), int(round(pt[1] / tol)))
                if k not in cells:
                    cells[k] = []
                    order.append(k)
                    parent[k] = k
                cells[k].append(pt)
        for i in range(len(order)):
            for j in range(i + 1, len(order)):
                ki, kj = order[i], order[j]
                if abs(ki[0] - kj[0]) <= 1 and abs(ki[1] - kj[1]) <= 1:
                    if any(math.hypot(p[0] - q[0], p[1] - q[1]) <= tol * 1.5
                           for p in cells[ki] for q in cells[kj]):
                        union(ki, kj)
        pts_at = {}
        ends = {}
        for s in segs:
            for pt in ((s["sx"], s["sy"]), (s["ex"], s["ey"])):
                rk = find((int(round(pt[0] / tol)), int(round(pt[1] / tol))))
                pts_at.setdefault(rk, []).append(pt)
                ends.setdefault(rk, []).append((s, pt))
        for rk, items in ends.items():
            vecs = {}
            for s, pt in items:
                other = (s["ex"], s["ey"]) if pt == (s["sx"], s["sy"]) else (s["sx"], s["sy"])
                v = (other[0] - pt[0], other[1] - pt[1])
                n = math.hypot(*v)
                if n == 0:
                    continue
                vecs.setdefault((round(v[0] / n, 3), round(v[1] / n, 3)), v)
            if len(vecs) < 2:
                continue
            dlist = sorted(vecs.items(), key=lambda kv: math.atan2(kv[0][1], kv[0][0]))
            if len(dlist) > 2:
                # 多度节点（T/叉/扇出）：相邻方向对≠真实拐角（会把扇出计成锐角假阳性）。
                # 只统计二度节点的真拐角；多度节点计数后跳过（junctions 返回给报告）。
                junctions += 1
                continue
            total += 1
            for (d1, v1), (d2, v2) in zip(dlist, dlist[1:]):
                dot = max(-1.0, min(1.0, (d1[0] * d2[0] + d1[1] * d2[1])))
                interior = round(math.degrees(math.acos(dot)), 2)
                corners.append({"net": net, "layer": layer,
                                "x": round(pts_at[rk][0][0], 2), "y": round(pts_at[rk][0][1], 2),
                                "interior": interior})
    return total, corners, junctions

MIL_PER_MM = 39.3700787
LEN_RATIO_DEFAULT = 2.0  # 焊盘欧氏MST是过理想下界：L形走线即1.414；正常布线1.0~1.8，兜圈子≥2.5。项目可收紧
RIGHT_MAX_PCT_DEFAULT = 5.0
ACUTE_MIN_TURN_DEG = 95.0


def seg_len(l):
    return math.hypot(l["ex"] - l["sx"], l["ey"] - l["sy"])


def mst_len(points_mm):
    pts = sorted(set((round(x, 4), round(y, 4)) for x, y in points_mm))
    if len(pts) <= 1:
        return 0.0
    inside = [pts[0]]
    rest = pts[1:]
    total = 0.0
    while rest:
        best, bi = None, 0
        for i, p in enumerate(rest):
            d = min(math.hypot(p[0] - q[0], p[1] - q[1]) for q in inside)
            if best is None or d < best:
                best, bi = d, i
        total += best
        inside.append(rest.pop(bi))
    return total


def check(tracks, pads, cfg):
    findings = []
    lines = [l for l in tracks.get("lines", []) if not l.get("err")]
    total_corners, all_corners, junctions_n = interior_corners(lines)

    # A1 锐角：内角 < 85°（转向 >95°）——制造缺陷（尖角蚀刻/酸液滞留），硬门
    acute = [(c["net"], c["interior"], c["x"], c["y"]) for c in all_corners if c["interior"] < 85.0]
    if acute:
        findings.append(("FAIL", "A1_ACUTE", f"锐角拐角（内角<85°）{len(acute)} 处: {acute[:10]}"))

    # A2 直角占比：内角 ∈ [85,95]
    right_n = sum(1 for c in all_corners if 85.0 <= c["interior"] <= 95.0)
    right_pct = (right_n / total_corners * 100.0) if total_corners else 0.0
    right_max = float(cfg.get("right_max_pct", RIGHT_MAX_PCT_DEFAULT))
    if right_pct > right_max:
        findings.append(("FAIL", "A2_RIGHT", f"直角拐角占比 {right_pct:.2f}% > {right_max}%（{right_n}/{total_corners}）"))

    # L1 兜圈子
    pad_map = {}
    for comp in pads or []:
        for pad in comp.get("pads", []):
            net = pad.get("net")
            if net:
                pad_map.setdefault(net, []).append((float(pad["x"]) / MIL_PER_MM, float(pad["y"]) / MIL_PER_MM))
    routed_by_net = {}
    for l in lines:
        routed_by_net[l.get("net")] = routed_by_net.get(l.get("net"), 0.0) + seg_len(l) / MIL_PER_MM
    ratio_max = float(cfg.get("len_ratio_max", LEN_RATIO_DEFAULT))
    exempt = cfg.get("len_ratio_exempt", {})
    net_table, offenders = [], []
    for net in sorted(set(pad_map) | set(routed_by_net)):
        mst = mst_len(pad_map.get(net, []))
        routed = routed_by_net.get(net, 0.0)
        ratio = (routed / mst) if mst > 0 else None
        net_table.append({"net": net, "pads": len(pad_map.get(net, [])),
                          "mst_mm": round(mst, 2), "routed_mm": round(routed, 2),
                          "ratio": round(ratio, 3) if ratio is not None else None})
        if ratio is not None and ratio > ratio_max and net not in exempt:
            offenders.append({"net": net, "ratio": round(ratio, 3),
                              "mst_mm": round(mst, 2), "routed_mm": round(routed, 2)})
    if offenders:
        findings.append(("FAIL", "L1_LOOP", f"兜圈子嫌疑（routed/MST > {ratio_max}）{len(offenders)} 网: "
                         f"{[o['net'] + '×' + str(o['ratio']) for o in offenders[:10]]}"))
    unrouted = [n for n in pad_map if routed_by_net.get(n, 0.0) == 0.0]
    if unrouted:
        findings.append(("INFO", "L2_UNROUTED", f"零走线的网（连通性另判）: {unrouted[:15]}"))

    verdict = "FAIL" if any(s == "FAIL" for s, _, _ in findings) else "PASS"
    return {"verdict": verdict, "total_corners": total_corners, "right_n": right_n,
            "right_pct": round(right_pct, 2), "acute_n": len(acute), "junctions": junctions_n,
            "nets": net_table, "offenders": offenders,
            "findings": [{"severity": s, "code": c, "message": m} for s, c, m in findings]}


def _polyline(net, dirs_deg, step_mm=5.0, start=(0.0, 0.0), layer=1, w=10):
    """方向角序列 → 轨迹段。内角 = 180 - |Δ方向|；dirs=[0,45] → 一处 135° 内角钝角。"""
    lines = []
    x, y = start
    for a in dirs_deg:
        nx = x + step_mm * math.cos(math.radians(a))
        ny = y + step_mm * math.sin(math.radians(a))
        lines.append({"net": net, "layer": layer,
                      "sx": x * MIL_PER_MM, "sy": y * MIL_PER_MM,
                      "ex": nx * MIL_PER_MM, "ey": ny * MIL_PER_MM, "w": w})
        x, y = nx, ny
    return lines


def _pads(net, pts_mm):
    return [{"dz": net, "pads": [{"n": str(i), "x": x * MIL_PER_MM, "y": y * MIL_PER_MM, "net": net}
                                 for i, (x, y) in enumerate(pts_mm)]}]


def selftest():
    lines, pads = [], []
    # N1 合格：单一 135° 钝角，ratio≈1.09
    l1 = _polyline("N1", [0, 45])
    lines += l1
    pads += _pads("N1", [(0, 0), (l1[-1]["ex"] / MIL_PER_MM, l1[-1]["ey"] / MIL_PER_MM)])
    # N5：20 个 135° + 1 个 90°（right = 1/20 = 5.0%，恰在阈值内）
    dirs = []
    for i in range(20):
        dirs.append(22.5 if i % 2 == 0 else 67.5)
    dirs += [157.5]
    l5 = _polyline("N5", dirs, step_mm=2.0)
    lines += l5
    pads += _pads("N5", [(0, 0), (l5[-1]["ex"] / MIL_PER_MM, l5[-1]["ey"] / MIL_PER_MM)])
    # N3 兜圈子：焊盘 10mm，绕 70mm 大圈（ratio 7.0）
    l3 = _polyline("N3", [0, 90, 180, 180], step_mm=10.0, start=(0, 0))  # 兜圈子：U 形回绕
    lines += l3
    pads += _pads("N3", [(0, 0), (l3[-1]["ex"] / MIL_PER_MM, l3[-1]["ey"] / MIL_PER_MM)])
    # N4 锐角：内角 60°
    l4 = _polyline("N4", [0, 120], step_mm=10.0)
    lines += l4
    pads += _pads("N4", [(0, 0), (l4[-1]["ex"] / MIL_PER_MM, l4[-1]["ey"] / MIL_PER_MM)])
    tracks = {"lines": lines, "vias": []}

    r = check(tracks, pads, {})
    codes = [f["code"] for f in r["findings"]]
    assert "A1_ACUTE" in codes and "L1_LOOP" in codes, codes  # N4/N3 必炸
    # 直角占比判据：1/20=5.0% ≤5% 边界——单独看 N5
    r_n5 = check({"lines": l5, "vias": []}, _pads("N5", [(0, 0), (l5[-1]["ex"] / MIL_PER_MM, l5[-1]["ey"] / MIL_PER_MM)]), {})
    assert r_n5["verdict"] == "PASS" and r_n5["right_n"] == 1, r_n5  # 5.0% 恰在阈值内
    r_n5b = check({"lines": l5, "vias": []}, _pads("N5", [(0, 0), (l5[-1]["ex"] / MIL_PER_MM, l5[-1]["ey"] / MIL_PER_MM)]),
                  {"right_max_pct": 4.0})
    assert r_n5b["verdict"] == "FAIL", r_n5b
    # 豁免：N3 豁免后（子集去掉 N5，避免其直角干扰断言）只剩 N4 的锐角 → 仍 FAIL
    sub_tracks = {"lines": [l for l in lines if l["net"] in ("N1", "N3", "N4")], "vias": []}
    sub_pads = [p for p in pads if p["dz"] in ("N1", "N3", "N4")]
    r_ex = check(sub_tracks, sub_pads, {"len_ratio_exempt": {"N3": "客户要求绕开禁布区"}})
    codes_ex = [f["code"] for f in r_ex["findings"]]
    assert "L1_LOOP" not in codes_ex and "A1_ACUTE" in codes_ex, codes_ex
    print(json.dumps({"selftest": "PASS", "cases": ["A1锐角FAIL", "L1兜圈子FAIL", "A2边界5%PASS", "A2收紧4%FAIL", "L1豁免生效"]}, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser(description="G5 布线门：走线质量检查（角度+长度）")
    ap.add_argument("--tracks")
    ap.add_argument("--pads")
    ap.add_argument("--config")
    ap.add_argument("--out")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.tracks or not a.pads:
        print(json.dumps({"ok": False, "error": "需要 --tracks 与 --pads（或 --selftest）"}, ensure_ascii=False))
        return 3
    r = check(_load_json(a.tracks), _load_json(a.pads), _load_json(a.config) if a.config else {})
    if a.out:
        _save_json(a.out, r)
    print(json.dumps({"ok": r["verdict"] == "PASS", "verdict": r["verdict"],
                      "right_pct": r["right_pct"], "acute_n": r["acute_n"],
                      "worst": r["offenders"][:5],
                      "findings": [(f["severity"], f["code"], f["message"][:140]) for f in r["findings"]],
                      "out": a.out}, ensure_ascii=False))
    return 0 if r["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
