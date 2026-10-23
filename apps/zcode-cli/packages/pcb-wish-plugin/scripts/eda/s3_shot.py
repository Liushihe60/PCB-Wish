#!/usr/bin/env python3
"""s3_shot.py — S3 站截图助手：zoomToRegion + getCurrentRenderedAreaImage（Blob→b64）。

用法: python s3_shot.py --out <png> [--region left,right,top,bottom(mil)] [--layer 1|2]
"""
import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lceda_invoke import ensure_session, run_code  # noqa: E402

SHOT_JS = r"""
// selectLayer 在该客户端 = 选中并单层显示（其余层 layerStatus→2 隐藏，pitfalls #30）。
// 截图前快照全部图层状态，截图后逐层恢复，禁止用 selectLayer(1) "恢复"。
let saved = null;
if (__CLI__.args.layer) {
  const ls = await eda.pcb_Layer.getAllLayers();
  saved = (Array.isArray(ls) ? ls : []).map(l => ({id: l.id, st: l.layerStatus}));
  await eda.pcb_Layer.selectLayer(__CLI__.args.layer);
}
if (__CLI__.args.region) {
  const [l, r, t, b] = __CLI__.args.region;
  await eda.dmt_EditorControl.zoomToRegion(l, r, t, b);
} else {
  await eda.dmt_EditorControl.zoomToAllPrimitives();
}
await new Promise(r => setTimeout(r, 2500));
const blob = await eda.dmt_EditorControl.getCurrentRenderedAreaImage();
const buf = await blob.arrayBuffer();
const bytes = new Uint8Array(buf);
let bin = '';
for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
if (saved) {
  for (const s of saved) {
    try {
      if (s.st === 1) { await eda.pcb_Layer.setLayerVisible(s.id); }
      else { await eda.pcb_Layer.setLayerInvisible(s.id); }
    } catch (e) {}
  }
}
return btoa(bin);
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--region", default=None, help="left,right,top,bottom (canvas mil)")
    ap.add_argument("--layer", default=None)
    args = ap.parse_args()
    sid = ensure_session()
    a = {"layer": int(args.layer) if args.layer else None}
    if args.region:
        a["region"] = [float(x) for x in args.region.split(",")]
    r = run_code(SHOT_JS, sid, timeout_ms=120000, args_json=json.dumps(a))
    if not r.get("ok"):
        print(json.dumps(r, ensure_ascii=False)[:300])
        sys.exit(1)
    data = base64.b64decode(r["value"])
    Path(args.out).write_bytes(data)
    print(f"saved {args.out} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
