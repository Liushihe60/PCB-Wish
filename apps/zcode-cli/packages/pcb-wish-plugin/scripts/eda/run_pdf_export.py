#!/usr/bin/env python3
"""run_pdf_export.py — 后台 PDF 导出（不受 invoke 前台超时限制）。"""
import json
import subprocess
import sys

sys.path.insert(0, r'E:\agent project\plugins\pcb-wish\scripts\eda')
from lceda_invoke import ensure_session, run_code  # noqa: E402

sid = ensure_session(None)
code = r"""
const f = await eda.pcb_ManufactureData.getPdfFile('pcb_view.pdf', 'paged');
await eda.sys_FileSystem.saveFileToFileSystem('E:/agent project/projects/flexiheal-v1/reports/pcb_layout_view.pdf', f);
return 'pdf-done';
"""
r = run_code(code, sid, timeout_ms=270000)
print(json.dumps(r, ensure_ascii=False)[:300])
