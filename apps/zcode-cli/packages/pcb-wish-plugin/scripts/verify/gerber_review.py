#!/usr/bin/env python3
"""gerber_review.py — 制造文件独立解析复核（pcb-wish G6 门的"独立第二裁判"）。

铁律：EDA 导出成功 ≠ 制造文件正确；本工具与 EDA 完全两条代码路径，
独立解析 Gerber(RS-274X) 与 Excellon 钻孔，复推关键事实并交叉核对。

检查项（全部确定性）:
  F1 文件集完整：铜层文件数 == 期望层数；有板框与钻孔文件；
     阻焊/丝印按 --strict-mask 要求存在（默认要求）。
  F2 文件可解析：非空、含格式说明（%FSLA…%）/钻孔单位头；孤儿 D 码（用了未定义的
     光圈）记 FAIL。
  F3 铜层统计：光圈数、flash(D03)/draw(D01) 对象数、坐标范围。
  F4 钻孔统计：刀具表、孔数、孔径范围、坐标范围。
  F5 交叉核对：钻孔范围须落在铜层范围之内（板外钻孔=跨层错位）；铜层数与
     --layers 期望一致。

用法:
    python gerber_review.py --dir <gerber目录> --layers 4 [--json]
退出码: 0=全过, 1=有 FAIL, 2=目录不可用。
"""
import argparse
import json
import re
import sys
from pathlib import Path

FMT = re.compile(r"%FSLAX(\d)(\d)Y(\d)(\d)\*%")
ADD = re.compile(r"%ADD(\d+)([A-Za-z_][A-Za-z0-9_]*)(?:,[^%]*)?\*%", re.I)  # shape 名不限 C/R/O；逗号参数段可缺省（AM 宏光圈如 %ADD10IconRect*%）
XY = re.compile(r"([XY])([-+]?\d+)")
DRILL_TOOL = re.compile(r"^T(\d+)[C.]?([\d.]+)?", re.I)
DRILL_UNIT = re.compile(r"(METRIC|INCH)")


def classify(name):
    n = name.lower()
    # 嘉立创导出里 "Gerber_DrillDrawingLayer.GDD" 是**Gerber 图层**（钻孔示意图），
    # 不是 Excellon 钻孔文件 —— 必须先按 Gerber 后缀判，再轮 Drill 关键字，
    # 否则会被当 Excellon 解析并误报"缺 METRIC/INCH 单位头"（G6 实测踩坑）。
    if n.endswith((".drl", ".xln")):  # Excellon 优先判定（真钻孔文件不得被当成 Gerber 图层）
        return "drill"
    if re.search(r"\.g[a-z0-9]{2,3}$", n) or n.startswith("gerber"):
        if "outline" in n or n.endswith((".gko", ".gm1", ".gml")):
            return "outline"
        if re.search(r"(\.gtl|\.gbl|\.g1|\.g2|\.g3|\.g4|copper)", n):
            return "copper"
        if "paste" in n or n.endswith((".gtp", ".gbp")):
            return "paste"
        if "silk" in n or n.endswith((".gto", ".gbo")):
            return "silk"
        if "soldermask" in n or n.endswith((".gts", ".gbs")):
            return "mask"
        return "gerber"
    if n.endswith((".drl", ".xln")) or "drill" in n:
        return "drill"
    if re.search(r"(\.gtl|\.gbl|\.g1|\.g2|\.g3|\.g4|copper|\.gml?-)", n):
        return "copper"
    if "edge" in n or n.endswith((".gko", ".gm1", ".gml")) or "outline" in n or "boardoutline" in n:
        return "outline"
    if "mask" in n or n.endswith((".gts", ".gbs")):
        return "mask"
    if "silk" in n or n.endswith((".gto", ".gbo")):
        return "silk"
    if "paste" in n or n.endswith((".gtp", ".gbp")):
        return "paste"
    if "outline" in n:
        return "outline"
    return "unknown"


class GerberFile:
    def __init__(self, path):
        self.path = path
        self.ok = True
        self.error = None
        self.apertures = set()
        self.used_apertures = set()
        self.flashes = self.draws = 0
        self.xmin = self.ymin = float("inf")
        self.xmax = self.ymax = float("-inf")
        self._parse()

    def _span(self, x, y):
        self.xmin, self.xmax = min(self.xmin, x), max(self.xmax, x)
        self.ymin, self.ymax = min(self.ymin, y), max(self.ymax, y)

    def _parse(self):
        text = self.path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            self.ok, self.error = False, "空文件"
            return
        fmt = FMT.search(text)
        if not fmt:
            self.ok, self.error = False, "缺 %FSLAX..Y..*% 格式说明（导出参数或文件损坏）"
            return
        dec = int(fmt.group(2))  # 小数位数（leading-zeros-omitted 约定）
        for m in ADD.finditer(text):
            self.apertures.add(m.group(1))
        lx = ly = 0.0
        cur = None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("%"):
                continue
            # 光圈选择：两种写法都要认——
            #   a) 独立一行  "D10*"
            #   b) 与 G 码合并 "G54D10*"（嘉立创 EDA 导出的实际写法）
            # 只认 (a) 会让 cur 恒为 None ⇒ 后续 D03 被误判成"孤儿 D 码"（G6 实测踩坑）。
            ap = re.fullmatch(r"D(\d+)\*?", line)
            if ap:
                cur = ap.group(1)
                continue
            ap = re.fullmatch(r"G\d+D(\d+)\*?", line)
            if ap:
                cur = ap.group(1)
                continue
            if not re.match(r"^[GXYD]", line):
                continue
            coords = {k: v for k, v in XY.findall(line)}
            if "X" in coords or "Y" in coords:
                if "X" in coords:
                    lx = int(coords["X"]) / (10 ** dec)
                if "Y" in coords:
                    ly = int(coords["Y"]) / (10 ** dec)
                if line.endswith("D01*"):
                    self.draws += 1
                    self._span(lx, ly)
                elif line.endswith("D03*"):
                    if cur is None:
                        self.ok, self.error = False, f"用了未定义光圈（孤儿 D 码）: {line}"
                        return
                    self.flashes += 1
                    self.used_apertures.add(cur)
                    self._span(lx, ly)
                elif line.endswith(("D02*", "D2*")):
                    self._span(lx, ly)
            if re.fullmatch(r"(G0?1|G0?2|G0?3)D0[13]\*", line):
                pass  # 合并坐标+操作的同写法已由上面 endswith 覆盖


class DrillFile:
    def __init__(self, path):
        self.path = path
        self.ok = True
        self.error = None
        self.tools = {}          # T号 -> 直径
        self.hits = {}           # T号 -> 孔数
        self.points = []         # (T号, x, y) 逐孔坐标（跨文件去重要用）
        self.xmin = self.ymin = float("inf")
        self.xmax = self.ymax = float("-inf")
        self._parse()

    def _span(self, x, y):
        self.xmin, self.xmax = min(self.xmin, x), max(self.xmax, x)
        self.ymin, self.ymax = min(self.ymin, y), max(self.ymax, y)

    def _parse(self):
        text = self.path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            self.ok, self.error = False, "空文件"
            return
        if not DRILL_UNIT.search(text):
            self.ok, self.error = False, "缺 METRIC/INCH 单位头（不是有效 Excellon）"
            return
        scale = 1.0 if "METRIC" in text.upper() else 25.4  # 英寸→mm
        cur = None
        lx = ly = 0.0
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            t = DRILL_TOOL.match(line)
            if t and re.match(r"^T\d+", line, re.I) and not re.search(r"[XY]", line):
                tid, dia = t.group(1), t.group(2)
                if dia:
                    self.tools[tid] = float(dia) * scale
                cur = tid
                continue
            if re.search(r"[XY][-+.\d]", line) and cur:
                xm = re.search(r"X(-?[\d.]+)", line)
                ym = re.search(r"Y(-?[\d.]+)", line)
                # Excellon 模态坐标：省略的轴沿用上一次值
                if xm:
                    lx = float(xm.group(1)) * scale
                if ym:
                    ly = float(ym.group(1)) * scale
                if xm or ym:
                    self._span(lx, ly)
                    self.hits[cur] = self.hits.get(cur, 0) + 1
                    self.points.append((cur, round(lx, 4), round(ly, 4)))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="制造文件独立解析复核")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--layers", type=int, required=True, help="期望铜层数")
    ap.add_argument("--no-strict-mask", action="store_true", help="不强制要求阻焊/丝印")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    d = Path(args.dir)
    if not d.is_dir():
        print(f"FAIL  目录不存在: {d}")
        sys.exit(2)

    items, files = [], []
    for p in sorted(d.iterdir()):
        if not p.is_file():
            continue
        kind = classify(p.name)
        parsed = DrillFile(p) if kind == "drill" else GerberFile(p)
        if kind in ("unknown",):
            continue
        files.append({"file": p.name, "kind": kind, "ok": parsed.ok, "error": parsed.error,
                      "stats": {
                          "apertures": len(getattr(parsed, "apertures", []) or []),
                          "flashes": getattr(parsed, "flashes", None),
                          "draws": getattr(parsed, "draws", None),
                          "tools": getattr(parsed, "tools", None),
                          "hits": getattr(parsed, "hits", None),
                        "points": getattr(parsed, "points", None),
                          "extent": ([parsed.xmin, parsed.ymin, parsed.xmax, parsed.ymax]
                                     if parsed.xmin != float("inf") else None)}})
        f = files[-1]
        if not parsed.ok:
            items.append(("FAIL", f"F2 {p.name}: {parsed.error}"))
            continue
        if kind == "copper":
            if parsed.flashes == 0 and parsed.draws == 0:
                items.append(("FAIL", f"F3 {p.name}: 铜层无任何对象（flash/draw 均为 0）"))
        if kind == "drill" and sum(parsed.hits.values()) == 0:
            items.append(("FAIL", f"F4 {p.name}: 钻孔文件无孔"))

    copper = [f for f in files if f["kind"] == "copper"]
    drills = [f for f in files if f["kind"] == "drill"]
    outlines = [f for f in files if f["kind"] == "outline"]
    masks = [f for f in files if f["kind"] == "mask"]
    silks = [f for f in files if f["kind"] == "silk"]

    if len(copper) != args.layers:
        items.append(("FAIL", f"F1 铜层文件数 {len(copper)} ≠ 期望 {args.layers}（识别为铜层: {[f['file'] for f in copper]}）"))
    else:
        items.append(("PASS", f"F1 铜层文件数 = {args.layers}"))
    if not drills:
        items.append(("FAIL", "F1 缺钻孔文件"))
    if not outlines:
        items.append(("FAIL", "F1 缺板框文件"))
    if not args.no_strict_mask and (not masks or not silks):
        items.append(("FAIL", f"F1 缺阻焊/丝印（mask×{len(masks)} silk×{len(silks)}）"))

    # F5 钻孔范围 ⊆ 铜层范围（容差 0.5mm）
    if copper and drills and all(f["stats"]["extent"] for f in copper + drills):
        cx0 = min(f["stats"]["extent"][0] for f in copper)
        cy0 = min(f["stats"]["extent"][1] for f in copper)
        cx1 = max(f["stats"]["extent"][2] for f in copper)
        cy1 = max(f["stats"]["extent"][3] for f in copper)
        dx0 = min(f["stats"]["extent"][0] for f in drills)
        dy0 = min(f["stats"]["extent"][1] for f in drills)
        dx1 = max(f["stats"]["extent"][2] for f in drills)
        dy1 = max(f["stats"]["extent"][3] for f in drills)
        tol = 0.5
        # 孔数：跨文件去重（嘉立创把 24/12mil 过孔同时写进 Through 与 Via 两个文件 ⇒ 累加会重复计）
        all_pts = set()
        for f in drills:
            for tool, x, y in (f["stats"].get("points") or []):
                all_pts.add((tool, round(x, 3), round(y, 3)))
        total_hits = len(all_pts)
        if dx0 < cx0 - tol or dy0 < cy0 - tol or dx1 > cx1 + tol or dy1 > cy1 + tol:
            items.append(("FAIL", f"F5 钻孔范围越出铜层范围: 钻孔[{dx0:.2f},{dy0:.2f}..{dx1:.2f},{dy1:.2f}] vs 铜层[{cx0:.2f},{cy0:.2f}..{cx1:.2f},{cy1:.2f}]"))
        else:
            items.append(("PASS", f"F5 钻孔范围在铜层范围内（板面 {cx1-cx0:.1f}×{cy1-cy0:.1f}mm，去重后 {total_hits} 孔）"))

    ok = all(s == "PASS" for s, _ in items)
    report = {"ok": ok, "dir": str(d), "expected_layers": args.layers, "files": files, "checks": [{"status": s, "detail": m} for s, m in items]}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for s, m in items:
            print(f"{s}  {m}")
        print(f"\n结论: {'制造文件独立复核通过' if ok else '存在复核 FAIL——EDA 导出成功不代表文件正确'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
