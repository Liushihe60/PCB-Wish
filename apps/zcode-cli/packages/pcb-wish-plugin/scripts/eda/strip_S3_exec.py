#!/usr/bin/env python3
"""strip_S3_exec.py — S3 站 spec 三文件纯删除（D021 21 ref + keepout 字段）。

- 备份 *.pre-strip3（字节复制）后原地剔除：
    layout.json            positions[21 ref] + keepout 整字段
    layout_solved_g05.json positions_abs[21 ref] + patch[被删 ref]
    bodies.json            by_designator[21 ref]
  footprints/library_uuids 按 uuid 键、不含 ref——不动（ref 条目口径）。
- 证明：幸存条目逐字不变（逐键深比较）+ 新文件被删 ref 零残留（递归键扫描）。
"""
import json
import shutil
import sys
from pathlib import Path

PROJ = Path(r"E:/agent project/projects/flexiheal-v1")
DELS = ["ANT1", "X2", "C22", "C23", "L1", "L2", "U4", "R_fb", "R_in", "R_ref1", "R_ref2",
        "R_refj1", "R_refj2", "C24", "C25", "C26", "L3", "C11", "R6", "LED_D", "R17"]


def load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def dump(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def scan_keys(obj, dels):
    """递归收集所有 dict 键中命中被删 ref 的位置。"""
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in dels:
                hits.append(k)
            hits += scan_keys(v, dels)
    elif isinstance(obj, list):
        for v in obj:
            hits += scan_keys(v, dels)
    return hits


def main():
    dels = set(DELS)
    summary = {}
    files = {
        "layout.json": lambda d: ([d["positions"].pop(r) for r in DELS if r in d["positions"]],
                                  d.pop("keepout", None)),
        "layout_solved_g05.json": lambda d: (
            [d["positions_abs"].pop(r) for r in DELS if r in d["positions_abs"]],
            [d["patch"].pop(r) for r in DELS if "patch" in d and r in d["patch"]]),
        "bodies.json": lambda d: [d["by_designator"].pop(r) for r in DELS if r in d["by_designator"]],
    }
    for fname, stripper in files.items():
        p = PROJ / "spec" / fname
        bak = p.with_suffix(".pre-strip3.json" if p.suffix == ".json" else ".pre-strip3")
        # 备份：layout.pre-strip3.json 形态（原名+后缀插入）
        bak = p.parent / (p.stem + ".pre-strip3" + p.suffix)
        if not bak.exists():
            shutil.copy2(p, bak)
        before = load(bak) if bak.name.endswith(".json") else load(bak)
        orig = load(bak)  # 以备份为原像，保证可重入
        d = load(bak)
        stripper(d)
        dump(p, d)
        # 证明 1：幸存条目逐字不变——原像中除被删 ref 键外，所有键值深比较
        removed = {}
        def diff_keys(a, b, path=""):
            """a=原像 b=新像：找出 a 有 b 无的键（仅在 dict 层），并验证公共部分深相等。"""
            assert isinstance(a, dict) and isinstance(b, dict), path
            for k in a:
                if k not in b:
                    removed[path + k] = True
                else:
                    if isinstance(a[k], dict) and isinstance(b[k], dict):
                        diff_keys(a[k], b[k], path + k + ".")
                    else:
                        assert json.dumps(a[k], sort_keys=True, ensure_ascii=False) == \
                               json.dumps(b[k], sort_keys=True, ensure_ascii=False), f"modified: {path}{k}"
            for k in b:
                assert k in a, f"added: {path}{k}"
        diff_keys(orig, d)
        # 证明 2：新文件被删 ref 零残留（递归键扫描）
        residual = scan_keys(d, dels)
        summary[fname] = {"removed_top": sorted(removed.keys()), "residual_refs": residual,
                          "backup": str(bak.name)}
    out = PROJ / "reports/strip_S3_diff_proof.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    ok = all(not v["residual_refs"] for v in summary.values())
    print("STRIP_OK" if ok else "STRIP_FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
