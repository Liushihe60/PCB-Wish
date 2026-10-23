#!/usr/bin/env python3
"""s4b_exec.py — S4B 批次执行器（创建→save→等10s→重枚举→记进度 五连）。

用法:
  python s4b_exec.py --validate ADC_IN DIV           # 离线校验（JLC 口径）不写画布
  python s4b_exec.py --create ADC_IN                 # 创建并五连（每批 ≤1 网，大网可拆批）
  python s4b_exec.py --create GUARD                  # GND 包地批
批量段/孔根 route spec（s4b_routes.py ROUTES）。校验失败即拒（不创建）。
权限红线：只创建走线/过孔，不删任何既有图元。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import ensure_session, run_code  # noqa: E402
import s4b_routes as RR  # noqa: E402
from s4b_model import Model  # noqa: E402

MIL = 39.37
HERE = Path("E:/agent project/projects/flexiheal-v1")
PROGRESS_MD = HERE / "reports/s4_progress.md"
PROGRESS_JSON = HERE / "reports/_s4b_progress.json"

CREATE_JS = r"""
const lines = __CLI__.args.lines;    // [{net,layer,x1,y1,x2,y2,w}]
const vias = __CLI__.args.vias;      // [{net,x,y,hole,dia}]
const out = {lines: [], vias: []};
for (const it of lines) {
  try {
    const l = await eda.pcb_PrimitiveLine.create(it.net, it.layer, it.x1, it.y1, it.x2, it.y2, it.w, false);
    out.lines.push({net: it.net, ok: !!l,
                    back: l ? {net: l.getState_Net(), layer: l.getState_Layer(),
                               sx: l.getState_StartX(), sy: l.getState_StartY(),
                               ex: l.getState_EndX(), ey: l.getState_EndY(),
                               w: l.getState_LineWidth()} : null});
  } catch (e) { out.lines.push({net: it.net, ok: false, err: String(e).slice(0, 120)}); }
}
for (const it of vias) {
  try {
    const v = await eda.pcb_PrimitiveVia.create(it.net, it.x, it.y, it.hole, it.dia);
    out.vias.push({net: it.net, ok: !!v,
                   back: v ? {net: v.getState_Net(), x: v.getState_X(), y: v.getState_Y(),
                              hole: v.getState_HoleDiameter(), dia: v.getState_Diameter()} : null});
  } catch (e) { out.vias.push({net: it.net, ok: false, err: String(e).slice(0, 120)}); }
}
return out;
"""

COUNT_JS = r"""
const out = {};
out.lines = (await eda.pcb_PrimitiveLine.getAllPrimitiveId()).length;
out.vias = (await eda.pcb_PrimitiveVia.getAllPrimitiveId()).length;
out.regions = (await eda.pcb_PrimitiveRegion.getAllPrimitiveId()).length;
out.comps = (await eda.pcb_PrimitiveComponent.getAllPrimitiveId()).length;
const nets = await eda.pcb_Net.getAllNets();
out.netLen = {};
for (const n of nets) out.netLen[n.net] = n.length || 0;
return out;
"""


def mil(v):
    return round(v * MIL, 2)


def spec_to_items(spec):
    lines, vias = [], []
    for s in spec["segs"]:
        pts = s["pts"]
        w_mil = float(s["w_mil"]) if "w_mil" in s else float(s["w_mm"]) * 39.37
        for i in range(len(pts) - 1):
            lines.append({"net": spec["net"], "layer": s["layer"],
                          "x1": mil(pts[i][0]), "y1": mil(30.0 - pts[i][1]),
                          "x2": mil(pts[i + 1][0]), "y2": mil(30.0 - pts[i + 1][1]),
                          "w": w_mil})
    for v0 in spec.get("vias", []):
        x, y, dia = v0
        vias.append({"net": spec["net"], "x": mil(x), "y": mil(30.0 - y),
                     "hole": 12, "dia": float(dia)})
    return lines, vias


def validate(names, verbose=True):
    m = Model()
    all_vios = {}
    for n in names:
        if n.startswith("file:"):
            spec = json.loads((HERE / n[5:]).read_text(encoding="utf-8"))
        else:
            spec = RR.ROUTES[n]
        net = spec["net"]
        vios = m.route_check(spec)
        spec_dangling = n == "GUARD"  # GND 包地栏端头悬空合法（G5 铺铜拼接）
        ep = [] if spec_dangling else m.set_endpoint_ok(spec, net)
        all_vios[n] = {"check": vios, "endpoints": ep,
                       "total_mm": m.route_total_mm(spec), "segs": sum(
                           len(s["pts"]) - 1 for s in spec["segs"]),
                       "vias": len(spec.get("vias", []))}
        status = "PASS" if not vios and not ep else "FAIL"
        if verbose:
            print(f"[{status}] {n}: segs={all_vios[n]['segs']} vias={all_vios[n]['vias']} "
                  f"length={all_vios[n]['total_mm']}mm")
            for v in (vios + ep)[:12]:
                print("   ", json.dumps(v, ensure_ascii=False))
            if len(vios + ep) > 12:
                print(f"    ... 共 {len(vios + ep)} 违例")
    return all_vios


def append_progress(text, batch):
    with PROGRESS_MD.open("a", encoding="utf-8") as f:
        f.write(f"- {text}\n")
    pj = {}
    if PROGRESS_JSON.exists():
        pj = json.loads(PROGRESS_JSON.read_text(encoding="utf-8"))
    pj.setdefault("batches", []).append(batch)
    PROGRESS_JSON.write_text(json.dumps(pj, ensure_ascii=False, indent=1), encoding="utf-8")


def create(names, override=False):
    sid = ensure_session()
    # 0) 创建前离线终检（当前画布态）
    v = validate(names)
    blocking = [(n, len(x["check"]) + len(x["endpoints"])) for n, x in v.items()
                if x["check"] or x["endpoints"]]
    if blocking and not override:
        print("!! 校验未过，拒不创建:", blocking)
        sys.exit(2)
    if blocking and override:
        print("!! [override] 带豁免创建（决策日志须记）:", blocking)
    # 1) 创建（单 invoke）
    items = {"lines": [], "vias": []}
    expect = {"lines": 0, "vias": 0}
    for nn in names:
        spec = json.loads((HERE / nn[5:]).read_text(encoding="utf-8")) if nn.startswith("file:") else RR.ROUTES[nn]
        lines, vias = spec_to_items(spec)
        items["lines"] += lines
        items["vias"] += vias
        expect["lines"] += len(lines)
        expect["vias"] += len(vias)
    r = run_code(CREATE_JS, sid, timeout_ms=600000,
                 args_json=json.dumps(items, ensure_ascii=False))
    if not r.get("ok"):
        print("create invoke 失败:", json.dumps(r)[:400]); sys.exit(3)
    res = r["value"]
    okl = [x for x in res["lines"] if x.get("ok")]
    okv = [x for x in res["vias"] if x.get("ok")]
    back_bad = [x for x in res["lines"] if not x.get("ok")] + \
               [x for x in res["vias"] if not x.get("ok")]
    print(f"创建返回: lines {len(okl)}/{expect['lines']} vias {len(okv)}/{expect['vias']} "
          f"bad={len(back_bad)}")
    # 2) save
    s = run_code("await eda.pcb_Document.save(); return 'saved'", sid, timeout_ms=120000)
    print("save:", s.get("value"))
    # 3) 等 10s
    time.sleep(10)
    # 4) 重枚举
    c = run_code(COUNT_JS, sid, timeout_ms=180000)
    if not c.get("ok"):
        print("重枚举失败:", json.dumps(c)[:300]); sys.exit(4)
    st = c["value"]
    print(f"重枚举: lines={st['lines']} vias={st['vias']} regions={st['regions']} comps={st['comps']}")
    nets = set()
    for nn in names:
        if nn.startswith("file:"):
            nets.add(json.loads((HERE / nn[5:]).read_text(encoding="utf-8"))["net"])
        else:
            nets.add(RR.ROUTES[nn]["net"])
    pernet = {n: st["netLen"].get(n) for n in sorted(nets)}
    print("netLen:", json.dumps(pernet, ensure_ascii=False))
    batch = {"at": time.strftime("%F %T"), "nets": names,
             "expected": expect, "createdBack": {"lines": len(okl), "vias": len(okv),
                                                 "bad": len(back_bad)},
             "after": {"lines": st["lines"], "vias": st["vias"], "regions": st["regions"],
                       "comps": st["comps"]},
             "netLen": pernet}
    # 5) 记进度
    nets_txt = "/".join(f"{nn}({pernet.get((json.loads((HERE / nn[5:]).read_text(encoding='utf-8'))['net'] if nn.startswith('file:') else RR.ROUTES[nn]['net']))} mm)"
                        for nn in names)
    append_progress(f"S4B 五连[{batch['at']}] 创建 {nets_txt} 期待线 {expect['lines']}/孔 {expect['vias']}"
                    f" → 画布 {st['lines']}/{st['vias']}；bad={len(back_bad)}", batch)
    sys.exit(0 if len(okl) == expect["lines"] and len(okv) == expect["vias"] else 5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", nargs="+")
    ap.add_argument("--create", nargs="+")
    ap.add_argument("--override", action="store_true",
                    help="校验未过仍创建（走廊受限微差，须决策日志记录）")
    args = ap.parse_args()
    if args.validate:
        validate(args.validate)
        sys.exit(0)
    if args.create:
        create(args.create, override=args.override)
        sys.exit(0)
    print("需 --validate 或 --create")


if __name__ == "__main__":
    main()
