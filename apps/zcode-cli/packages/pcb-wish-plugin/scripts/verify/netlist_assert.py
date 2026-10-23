#!/usr/bin/env python3
"""netlist_assert.py — 网表断言校验器（pcb-wish G2/G5 门）。

用途：DRC=0 不是"板子正确"的证据；本工具对导出网表做**意图级**断言，
抓 DRC 查不出的问题（如 net port 静默并网导致的意外短路）。

输入:
  --netlist <netlist.json>   格式 {"nets": {"<网络名>": ["<元件>.<引脚>", ...], ...}}
  --assertions <assertions.json>  格式见下

断言类型:
  must_connect:    指定引脚集合必须全部位于同一网络（含同名判断选项 exact_net）
  must_not_connect: 两组引脚必须位于不同网络（抓静默短路）
  pin_count:       某网络引脚数必须等于/≥/≤ 指定值
  net_exists:      网络必须存在/必须不存在
  net_members:     网络成员集合断言——op "==": 实际成员与期望完全一致
                   (抓两类错：意外短路=多成员、漏连=缺成员，均FAIL)；
                   op ">=": 至少包含期望成员；op "<=": 不得出现期望外成员。
                   语义决策（2026-10-19）：短路检测以 net_members "=="/"<=" 为准，
                   must_connect 只表达"这些引脚应同网"，短路时它天然为 PASS，
                   不作为短路证据。

退出码: 0=全绿, 1=存在 FAIL。
"""
import argparse
import json
import sys
from pathlib import Path


def find_net_of(netlist, pin):
    # 精确网络名（如 "GND"）直接按网络解析；否则按 <元件>.<引脚> 查找
    if pin in netlist:
        return pin
    for name, members in netlist.items():
        if pin in members:
            return name
    return None


def run(netlist, asserts):
    results = []
    for a in asserts.get("must_connect", []):
        pins = a["pins"]
        nets = [find_net_of(netlist, p) for p in pins]
        ok = all(n is not None for n in nets) and len(set(nets)) == 1
        results.append({"type": "must_connect", "name": a.get("name", ""), "ok": ok,
                        "detail": f"pins={pins} -> nets={nets}"})
    for a in asserts.get("must_not_connect", []):
        groups = a["groups"]
        nets = []
        for g in groups:
            g_nets = {find_net_of(netlist, p) for p in g}
            nets.append(g_nets)
        ok = all(len(g) == 1 for g in nets) and len({next(iter(g)) for g in nets}) == len(groups)
        results.append({"type": "must_not_connect", "name": a.get("name", ""), "ok": ok,
                        "detail": f"groups_nets={[sorted(x for x in g if x) for g in nets]}"})
    for a in asserts.get("pin_count", []):
        net = a["net"]
        members = netlist.get(net)
        if members is None:
            ok = False
            detail = f"网络 {net} 不存在"
        else:
            n = len(members)
            op = a.get("op", "==")
            target = a["count"]
            ok = {"==": n == target, ">=": n >= target, "<=": n <= target}[op]
            detail = f"{net} 有 {n} 个引脚，期望 {op}{target}"
        results.append({"type": "pin_count", "name": a.get("name", net), "ok": ok, "detail": detail})
    for a in asserts.get("net_exists", []):
        net, should = a["net"], a.get("exists", True)
        ok = (net in netlist) == should
        results.append({"type": "net_exists", "name": a.get("name", net), "ok": ok,
                        "detail": f"网络 {net} {'应存在' if should else '应不存在'}"})
    for a in asserts.get("net_members", []):
        net = a["net"]
        expected = set(a["members"])
        op = a.get("op", "==")
        actual = netlist.get(net)
        if actual is None:
            ok, detail = False, f"网络 {net} 不存在（期望成员 {len(expected)} 个）"
        elif op not in ("==", ">=", "<="):
            ok, detail = False, f"net_members {net}: 未知 op {op}（支持 ==/>=/<=）"
        else:
            act = set(actual)
            extra, missing = act - expected, expected - act
            if op == "==":
                ok = act == expected
            elif op == ">=":
                ok = not missing
            else:
                ok = not extra
            if ok:
                detail = f"{net} 成员一致（{len(act)} 个）"
            else:
                detail = f"{net} 成员不符（实际 {len(act)}，期望 {len(expected)}）"
                if missing:
                    detail += f"；缺失: {sorted(missing)}"
                if extra:
                    detail += f"；多出(疑似短路/错连): {sorted(extra)}"
        results.append({"type": "net_members", "name": a.get("name", net), "ok": ok, "detail": detail})
    return results


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="网表意图级断言校验")
    ap.add_argument("--netlist", required=True)
    ap.add_argument("--assertions", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    netlist = json.loads(Path(args.netlist).read_text(encoding="utf-8"))["nets"]
    asserts = json.loads(Path(args.assertions).read_text(encoding="utf-8"))
    results = run(netlist, asserts)
    ok = all(r["ok"] for r in results)
    if args.json:
        print(json.dumps({"ok": ok, "total": len(results), "results": results}, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['type']:<16} {r['name']:<20} {r['detail']}")
        print(f"\n结论: {'全部断言通过' if ok else '存在断言失败（DRC 可能仍为 0——这正是独立断言的意义）'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
