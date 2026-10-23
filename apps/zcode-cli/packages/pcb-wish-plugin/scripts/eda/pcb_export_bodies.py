#!/usr/bin/env python3
"""pcb_export_bodies.py — 从 EDA 库导出每件 footprint 的真实丝印/占位几何（G3 覆盖区口径）。

原理：LIB_Footprint.getDocumentSource(uuid, libraryUuid) 返回 .foo 文档源（'|\n' 分隔
记录，每记录 '头||体' 两段 JSON）。取 POLY/FILL 原语按 layerId 分组：
  - 顶层丝印层（layerName 含 SILK 且 TOP）；
  - 文档/装配层 48/49（部分库存图形画在文档层，丝印为空时作为本体兜底）；
  - 11=板框。
输出 spec/bodies.json：
  {layers: {层号: 描述}, footprints: {封装uuid: {silk_bbox_mil_rel:[x0,y0,x1,y1],
   doc_bbox_mil_rel: [...], layers_used: {...}}}, by_designator: {位号: 封装uuid}}
bbox 相对封装原点（mil，封装坐标系）。

用法:
    python pcb_export_bodies.py --out spec/bodies.json
退出码: 0=成功; 1=部分封装读取失败。
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import ensure_session, run_code  # noqa: E402
import pcb_bodies_lib as B  # noqa: E402

COMP_JS = r"""
const ids = await eda.pcb_PrimitiveComponent.getAllPrimitiveId();
const out = [];
for (const id of ids) {
  try {
    const c = await eda.pcb_PrimitiveComponent.get(id);
    out.push({dz: c.designator, fp: c.footprint ? {uuid: c.footprint.uuid, lib: c.footprint.libraryUuid, name: c.footprint.name} : null});
  } catch (e) { out.push({dz: "?" + id, err: String(e).slice(0, 80)}); }
}
return out;
"""

SRC_JS = r"""
let lib = __CLI__.args.lib;
const map = __CLI__.args.libmap || {};
if (!lib && map[__CLI__.args.uuid]) lib = map[__CLI__.args.uuid];
const s = await eda.lib_Footprint.getDocumentSource(__CLI__.args.uuid, lib);
return s || null;
"""


def parse_records(src):
    """拆 .foo 源 → (layer_map, prims)。layer_map: {层号: 'layerType|layerName'}；
    prims: [(layerId, kind, path)]，path 为原始几何（POLY=平铺坐标列，FILL=形状列表）。"""
    layers, prims = {}, []
    pending = None  # 头半段 {type, id}
    for rec in src.split("|\n"):
        for j in rec.split("||"):
            j = j.strip()
            if not j:
                continue
            try:
                o = json.loads(j)
            except json.JSONDecodeError:
                continue
            if not isinstance(o, dict):
                continue
            if o.get("type") in ("LAYER", "POLY", "FILL", "PAD"):
                pending = o
            elif pending and "type" not in o:
                h, b = pending, o
                pending = None
                if h["type"] == "LAYER":
                    try:
                        num = json.loads(h["id"].replace("\\", ""))[1]
                        layers[num] = f'{b.get("layerType", "?")} | {b.get("layerName", "?")}'
                    except Exception:  # noqa: BLE001
                        pass
                elif h["type"] in ("POLY", "FILL"):
                    prims.append((b.get("layerId"), h["type"], b.get("path")))
    return layers, prims


def poly_bbox(path):
    """POLY path（[x0,y0,'L',x1,y1,...]）→ (minx,miny,maxx,maxy)。"""
    xs, ys = [], []
    for i in range(0, len(path) - 1):
        x, y = path[i], path[i + 1]
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def fill_bbox(path):
    """FILL path（[['CIRCLE',x,y,r]|['RECT',x,y,w,h]|...]）→ bbox 并集。"""
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


def union_of(bboxes):
    """一组 bbox (mil) → 并集；空集返回 None。"""
    bb = [b for b in bboxes if b]
    if not bb:
        return None
    return [min(b[0] for b in bb), min(b[1] for b in bb),
            max(b[2] for b in bb), max(b[3] for b in bb)]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="spec/bodies.json")
    ap.add_argument("--session", default=None)
    ap.add_argument("--libmap", default=None, help="fpUuid->libraryUuid 映射 JSON（库 uuid 缺失时兜底）")
    args = ap.parse_args()
    sid = ensure_session(args.session)

    r = run_code(COMP_JS, sid, timeout_ms=300000)
    if not r.get("ok"):
        print(json.dumps(r, ensure_ascii=False)[:400])
        sys.exit(1)
    comps = r["value"]
    bad = [c for c in comps if "err" in c or not c.get("fp")]
    if bad:
        print(f"# 组件读取异常 {len(bad)}：{[c.get('dz') for c in bad][:8]}")

    fps = {}
    for c in comps:
        fp = c.get("fp")
        if fp and fp["uuid"]:
            fps.setdefault(fp["uuid"], fp["name"] or fp["uuid"])

    libmap = {}
    if args.libmap and Path(args.libmap).exists():
        libmap = json.loads(Path(args.libmap).read_text(encoding="utf-8"))
    layer_map, per_fp, fails = {}, {}, []
    for i, (uuid, name) in enumerate(sorted(fps.items())):
        s = run_code(SRC_JS, sid, timeout_ms=120000,
                     args_json=json.dumps({"uuid": uuid, "lib": None, "libmap": libmap},
                                          ensure_ascii=False))
        src = s.get("value") if s.get("ok") else None
        if not src:
            s2 = run_code(SRC_JS, sid, timeout_ms=120000,
                          args_json=json.dumps({"uuid": uuid, "lib": "891abc044ef64f37ab704d00aaac791e",
                                                "libmap": libmap}, ensure_ascii=False))
            src = s2.get("value") if s2.get("ok") else None
        if not src:
            fails.append(name)
            print(f"# [{i+1}/{len(fps)}] {name}: 源码获取失败", file=sys.stderr)
            continue
        layers, prims = parse_records(src)
        for k, v in layers.items():
            layer_map.setdefault(k, v)
        cov_ids = ({k for k, v in layer_map.items()
                    if "SILK" in v.upper() and "TOP" in v.upper()}) | {48, 49}
        prims_out = []
        for lid, kind, path in prims:
            if lid not in cov_ids:
                continue
            bb = B.bbox_of_prim(kind, path)
            if bb:
                prims_out.append([lid, kind, bb])
        sb = union_of([p[2] for p in prims_out if p[0] == 3])
        db = union_of([p[2] for p in prims_out if p[0] in (48, 49)])
        per_fp[uuid] = {"name": name, "silk_bbox_mil_rel": sb, "doc_bbox_mil_rel": db,
                        "prims": prims_out,
                        "prim_layers": sorted({p[0] for p in prims_out})}
        print(f"# [{i+1}/{len(fps)}] {name}: silk={sb} doc={db} 原语={len(prims_out)}")

    by_dz = {c["dz"]: c["fp"]["uuid"] for c in comps if c.get("fp") and c["fp"]["uuid"]}
    lib_map = {}
    for c in comps:
        fp = c.get("fp")
        if fp and fp["uuid"]:
            lib_map.setdefault(fp["uuid"], fp.get("lib"))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"layers": {str(k): v for k, v in sorted(layer_map.items())},
                                          "footprints": per_fp, "library_uuids": lib_map,
                                          "by_designator": by_dz},
                                         ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"导出 {len(per_fp)}/{len(fps)} 封装 → {args.out}；失败 {len(fails)}：{fails}")
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
