#!/usr/bin/env python3
"""s4b_trial_vbus.py — VBUS 走线候选自动搜索（模型试错迭代）。"""
import sys
import itertools
sys.path.insert(0, '.')
from s4b_model import Model

M = Model()
NET = 'VBUS'


def try_spec(spec):
    v = M.route_check(spec)
    ep = M.set_endpoint_ok(spec, NET)
    return v, ep, M.route_total_mm(spec)


def build(lane_y, w_lane=10, lane_x0=2.858, lane_x1=10.400,
          c17_x=6.050, w_br=12, c19_x=6.655,
          d1_branch_from=None, b4_via=True, b4_diag=True, u28_mode='via',
          u28_vx=10.070, p3_l2=None, adb_via=None, adb_leg=None):
    spec = {"net": NET, "segs": [], "vias": []}
    S = spec["segs"]
    w = w_lane
    S.append({"layer": 1, "w_mil": w, "pts": [[2.858, 12.624], [2.858, lane_y]]})
    S.append({"layer": 1, "w_mil": w, "pts": [[lane_x0, lane_y], [lane_x1, lane_y]]})
    S.append({"layer": 1, "w_mil": w_br, "pts": [[c17_x, lane_y], [c17_x, 9.274]]})
    S.append({"layer": 1, "w_mil": w_br, "pts": [[c19_x, lane_y], [c19_x, 11.052]]})
    if d1_branch_from:
        S.append({"layer": 1, "w_mil": 10, "pts": [d1_branch_from, [10.897, 15.293]]})
    if b4_via:
        spec["vias"].append([7.658, 12.624, 24])
        if b4_diag:
            S.append({"layer": 2, "w_mil": 10, "pts": [[7.658, 12.624], [10.897, 15.293]]})
    if u28_mode == 'via':
        spec["vias"].append([9.398 if u28_vx is None else u28_vx, 2.581, 24]) if False else None
    if u28_mode == 'north':
        S.append({"layer": 1, "w_mil": 10, "pts": [[u28_vx, lane_y], [u28_vx, 1.650]]})
        S.append({"layer": 1, "w_mil": 10, "pts": [[u28_vx, 1.650], [9.398, 1.650]]})
        S.append({"layer": 1, "w_mil": 10, "pts": [[9.398, 1.650], [9.398, 2.581]]})
    elif u28_mode == 'via':
        spec["vias"].append([9.398, 2.581, 24])
        S.append({"layer": 2, "w_mil": 10, "pts": [[9.398, 2.581], [10.897, 15.293]]})
    if p3_l2:
        spec["vias"].append([10.897, 15.293, 24])
        S.append({"layer": 2, "w_mil": 10, "pts": [[10.897, 15.293], *p3_l2]})
        spec["vias"].append([7.671, 27.993, 24])
    spec["vias"].append([10.897, 15.293, 24])
    if adb_via:
        spec["vias"].append([15.600, 9.426, 24])
        S.append({"layer": 2, "w_mil": 10, "pts": [[15.600, 9.426], [*adb_via]]})
        spec["vias"].append([*adb_via, 24])
        S.append({"layer": 1, "w_mil": 10, "pts": [[*adb_via], [*adb_leg]]})
    return spec


best = None
for lane_y in (10.9, 11.2, 11.65):
    for lane_x1 in (9.9, 10.07, 10.4):
        for c17_x in (5.95, 6.05, 6.15):
            for d1_x in (9.6, 9.9, 10.15, 10.4, 10.6):
                for u28_vx in (9.398, 10.070, 10.4):
                    adb_opts = [[25.078, 17.400], [25.05, 17.400]]
                    for adb in adb_opts:
                        spec = build(lane_y, lane_x1=lane_x1, c17_x=c17_x,
                                     d1_branch_from=[d1_x, lane_y],
                                     u28_mode='via', u28_vx=u28_vx,
                                     p3_l2=[[11.2, 17.2], [7.671, 27.993]],
                                     adb_via=[25.078, 17.400], adb_leg=[25.150, 16.749])
                        v, ep, tot = try_spec(spec)
                        nbad = len(v) + len(ep)
                        if nbad == 0:
                            print(f"CLEAR lane_y={lane_y} lane_x1={lane_x1} c17={c17_x} d1={d1_x} "
                                  f"u28x={u28_vx} total={tot}mm")
                            sys.exit(0)
                        if best is None or nbad < best[0]:
                            best = (nbad, lane_y, lane_x1, c17_x, d1_x, u28_vx, adb, v, ep)
print("NO CLEAR CONFIG; best:")
print(best[0], best[1:7])
for x in (best[7] + best[8])[:15]:
    print("  ", x)
