#!/usr/bin/env python3
"""s4e_lib.py — S4E 站共用底座（会话/落盘/角度直方图）。

纪律（brief S4E §4）：
- 每次 Bash 显式带 --session（shell 状态不跨调用保留）
- 中间产物一律落项目目录显式路径（pitfalls #26）
- 不把大 JSON 读进上下文，只读摘要
"""
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import ensure_session, run_code  # noqa: E402

SESSION = "44e574cc-dc99-46dd-80b8-60632f5b075a"
HERE = Path("E:/agent project/projects/flexiheal-v1")
REPORTS = HERE / "reports"


def sid():
    return ensure_session(SESSION)


def invoke(code, timeout_ms=300000, session=None):
    return run_code(code, session or SESSION, timeout_ms=timeout_ms)


def invoke_file(path, timeout_ms=300000):
    code = Path(path).read_text(encoding="utf-8")
    return invoke(code, timeout_ms=timeout_ms)


def save_json(name, obj, subdir="reports"):
    p = (HERE / subdir / name) if not name.startswith("reports/") else (HERE / name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load_json(name, subdir="reports"):
    p = HERE / subdir / name
    return json.loads(p.read_text(encoding="utf-8"))


# ---------- 几何 ----------

def seg_angle(sx, sy, ex, ey):
    return math.degrees(math.atan2(ey - sy, ex - sx)) % 180.0


def turn_angle(a, b):
    """两条线段方向夹角（0=同向，180=折返）"""
    d = abs(a - b) % 180.0
    if d > 90.0:
        d = 180.0 - d
    return d


def classify_corner(deg):
    if deg <= 5.0:
        return "straight"
    if abs(deg - 45.0) <= 5.0:
        return "45"
    if abs(deg - 90.0) <= 5.0:
        return "90"
    return "other"


def corner_histogram(lines, tol=0.5):
    """lines: [{net,layer,sx,sy,ex,ey,w}]  单位 canvas mil。

    节点口径：同网同层的段端点合并（容差 tol mil），节点度数 d：
      - d==2 → 1 个拐角（真拐角）
      - d>2  → T/叉接点，不计单拐角（单独统计），但按“方向对”展开时
        每一对相邻方向算 1 个转角（T 形接 1 个转角），避免阶梯误判
    返回 (摘要, 拐角明细)。
    """
    by_net = {}
    for l in lines:
        by_net.setdefault((l.get("net"), l.get("layer")), []).append(l)

    hist = Counter()
    raw_angle = Counter()
    corner_pts = []
    junctions = 0
    for (net, layer), segs in by_net.items():
        # 端点按 tol 网格聚类
        cells = {}
        order = []

        def cellkey(x, y):
            return (int(round(x / tol)), int(round(y / tol)))

        for s in segs:
            for pt in ((s["sx"], s["sy"]), (s["ex"], s["ey"])):
                k = cellkey(*pt)
                if k not in cells:
                    cells[k] = []
                    order.append(k)
                cells[k].append(pt)
        # 合并相邻 cell（同一节点可能跨 tol 边界）→ 用代表点
        # 简单法：同 cell 内所有端点视为同一节点；跨 cell 邻接再合并
        # 用并查集在 tol*1.5 邻域内合并
        parent = {k: k for k in order}

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i in range(len(order)):
            for j in range(i + 1, len(order)):
                ki, kj = order[i], order[j]
                if abs(ki[0] - kj[0]) <= 1 and abs(ki[1] - kj[1]) <= 1:
                    # 距离检查用真实端点
                    close = any(math.hypot(pi[0] - pj[0], pi[1] - pj[1]) <= tol * 1.5
                                for pi in cells[ki] for pj in cells[kj])
                    if close:
                        union(ki, kj)
        node_pts = {}
        for k in order:
            node_pts.setdefault(find(k), []).extend(cells[k])
        node_segs = {}
        for s in segs:
            for pt in ((s["sx"], s["sy"]), (s["ex"], s["ey"])):
                k = cellkey(*pt)
                rk = find(k)
                node_segs.setdefault(rk, set()).add(id(s))
        for rk, segids in node_segs.items():
            if len(segids) < 2:
                continue
            sl = [s for s in segs if id(s) in segids]
            # 该节点出发的方向（按每个段的两个端点相对节点位置判断）
            dirs = []
            for s in sl:
                a = (s["sx"], s["sy"])
                b = (s["ex"], s["ey"])
                if (round(a[0], 2), round(a[1], 2)) in [(round(p[0], 2), round(p[1], 2)) for p in node_pts[rk]]:
                    dirs.append((seg_angle(*a, *b), s, a, b))
                else:
                    dirs.append((seg_angle(*b, *a), s, b, a))
            uniq = {}
            for ang, s, p, q in dirs:
                uniq.setdefault(round(ang, 1), (ang, s, p, q))
            dvec = list(uniq.values())
            if len(dvec) < 2:
                continue
            if len(dvec) == 2:
                pairs = [(dvec[0], dvec[1])]
            else:
                # T/叉：相邻方向对（角度差最小邻接）→ 计 len-1 个转角
                dvec.sort(key=lambda x: x[0])
                pairs = [(dvec[i], dvec[i + 1]) for i in range(len(dvec) - 1)]
                junctions += 1
            for (a1, s1, p1, q1), (a2, s2, p2, q2) in pairs:
                deg = turn_angle(a1, a2)
                cat = classify_corner(deg)
                hist[cat] += 1
                raw_angle[round(deg)] += 1
                if cat in ("90", "other"):
                    corner_pts.append({"net": net, "layer": layer,
                                       "x": round(node_pts[rk][0][0], 2),
                                       "y": round(node_pts[rk][0][1], 2),
                                       "angle": round(deg, 2), "cat": cat})
    total = sum(hist.values())
    out = {
        "total_corners": total,
        "by_class": dict(hist),
        "raw_angle_hist": dict(sorted(raw_angle.items())),
        "junctions": junctions,
        "ratio90": round(hist.get("90", 0) / total, 4) if total else 0.0,
        "ratio45": round(hist.get("45", 0) / total, 4) if total else 0.0,
        "ratio_other": round(hist.get("other", 0) / total, 4) if total else 0.0,
        "ratio_straight": round(hist.get("straight", 0) / total, 4) if total else 0.0,
    }
    return out, corner_pts
