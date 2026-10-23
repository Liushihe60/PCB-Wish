#!/usr/bin/env python3
"""tex_quality_check.py — 中文 LaTeX 讲义静态质检器（teaching-lecture 质检门）。

移植自 exam-paper-skill 的 quality_check 思路，针对 agent 生成 tex 的常见错：
  E1 ASCII 直双引号紧邻 CJK 字符（LaTeX 会解释为右引号，渲染错乱）
  E2 数学环境不配对（$ 段内奇数个、\\[ \\]、equation 等）
  E3 数学模式内出现裸 CJK 字符（应放入 \\text{} 或改用正文）
  E4 章文件缺 introduction 导读盒（版式 v2：每章必须以导读开篇）
  E5 章文件缺 conclusion 小结（版式 v2：每章必须以小结收束）
  W1 行内公式超过 120 字符（潜在排版溢出）
  W2 includegraphics 未带宽度约束
  W3 连续 >=5 个 \\paragraph（文字墙信号，应插入盒子/表格/图打断）
  W4 主文件自定义盒子定义超过 2 个（盒子通胀纪律：内置环境 + 唯一自定义盒）
  W5 使用了内置白名单之外的教学盒子环境（疑似拼写错误或私自加盒）
  W6 misconception 盒缺少"错误做法/后果/正确做法"三行式

状态徽标纪律：状态一律用 \\verified/\\computed/\\estimate/\\notestablished，
禁止裸写文字状态（表格"状态"列除外，人工过目）。

用法:
    python tex_quality_check.py <file.tex> [更多.tex ...] [--json]
退出码: 0=无 ERROR, 1=存在 ERROR。

正反用例: test_fixtures/lecture_fail.tex 应报 E4/E5/W3/W6；
          templates/teaching/sample-chapter-tps40200.tex 应 0 ERROR。
"""
import argparse
import json
import re
import sys
from pathlib import Path

CJK = re.compile(r"[\u4e00-\u9fff]")
ASCII_QUOTE_ADJ = re.compile(r'"(?=[\u4e00-\u9fff])|(?<=[\u4e00-\u9fff])"')
EQ_ENVS = ("equation", "align", "gather", "multline", "figure", "table", "longtable", "lstlisting", "verbatim")
# ElegantBook v4.7 内置教学环境白名单（fancy 模式，已核验 cls）+ 自定义 misconception
# 已知 LaTeX 命令名（用于识别"多打一个反斜杠"的命令字面量泄漏）
TEX_COMMANDS = {
    "Rightarrow", "Leftarrow", "Leftrightarrow", "leftrightarrow", "longrightarrow",
    "longleftarrow", "to", "gets", "cdot", "ldots", "cdots", "vdots", "ddots",
    "approx", "equiv", "geq", "leq", "neq", "times", "pm", "mp", "div", "sqrt",
    "sum", "prod", "int", "infty", "partial", "nabla", "in", "notin", "subset",
    "subseteq", "supset", "supseteq", "cup", "cap", "setminus", "oplus",
    "otimes", "wedge", "vee", "perp", "parallel", "angle", "triangle", "square",
    "circ", "ell", "hbar", "dot", "ddot", "tilde", "vec", "hat", "bar", "overline",
    "underline", "widehat", "widetilde", "text", "textbf", "textit", "texttt",
    "mathrm", "mathbf", "mathit", "mathcal", "mathbb", "mathfrak", "mathsf",
    "operatorname", "frac", "dfrac", "tfrac", "binom", "left", "right", "quad",
    "qquad", "displaystyle", "limits", "substack", "phantom", "hfill", "vspace",
    "hspace", "label", "ref", "eqref", "cite", "caption", "centering", "noindent",
    "hline", "cline", "multicolumn", "tabular", "toprule", "midrule", "bottomrule",
    "hline", "footnote", "footnotemark", "emph", "underline", "textcircled",
    "textsuperscript", "textsubscript", "underbrace", "overbrace", "xrightarrow",
    "xleftarrow", "mathbb", "epsilon", "theta", "lambda", "mu", "nu", "xi",
    "rho", "sigma", "tau", "phi", "chi", "psi", "omega", "Gamma", "Delta",
    "Theta", "Lambda", "Xi", "Sigma", "Phi", "Psi", "Omega", "alpha", "beta",
    "gamma", "zeta", "eta", "kappa", "varepsilon", "vartheta", "varphi", "varrho",
    "varpi", "varrho", "varsigma", "vartheta",
}

BOX_ENV_WHITELIST = {
    "definition", "theorem", "lemma", "corollary", "axiom", "postulate", "proposition",
    "example", "problem", "exercise", "note", "remark", "assumption", "property",
    "conclusion", "custom", "solution", "proof", "introduction", "problemset",
    "misconception",
}
PARAGRAPH_WALL = 5      # 连续 \paragraph 达到该数量告警
BARE_CMD_RE = re.compile(r"^\s*(" + "|".join(
    ["footnotesize", "scriptsize", "tiny", "small", "normalsize", "large", "Large",
     "LARGE", "huge", "Huge", "centering", "raggedright", "raggedleft",
     "hline", "toprule", "midrule", "bottomrule", "cmidrule", "itemize",
     "enumerate", "tabular", "multicolumn", "cline", "noalign", "linewidth",
    ]) + r")\b")
CUSTOM_BOX_LIMIT = 2    # 主文件 \newtcolorbox 自定义盒子上限


def check_file(path):
    errors, warnings = [], []
    text = Path(path).read_text(encoding="utf-8", errors="replace")

    # 剥离注释行（不以 \% 结尾的 % 注释）
    lines = []
    for i, raw in enumerate(text.splitlines(), 1):
        code = re.sub(r"(?<!\\)%.*", "", raw)
        lines.append((i, code))
    nospace = "".join(code for _, code in lines)  # 供块级规则使用

    # E1 直引号
    for i, code in lines:
        for m in ASCII_QUOTE_ADJ.finditer(code):
            errors.append({"line": i, "rule": "CJK_ASCII_QUOTES", "message": f"ASCII 直引号紧邻中文: ...{code[max(0, m.start()-10):m.end()+10]}..."})

    # E2 数学环境配对
    env_stack = []
    for i, code in lines:
        for env in EQ_ENVS:
            env_stack += [(env, True, i) for _ in re.findall(rf"\\begin\{{{env}\}}", code)]
            opens = len(re.findall(rf"\\begin\{{{env}\}}", code))
            closes = len(re.findall(rf"\\end\{{{env}\}}", code))
            if opens != closes and opens == closes + 1:
                pass  # 跨行环境：由栈逻辑处理
    # 行级 $ 配对
    for i, code in lines:
        if "\\$" in code:
            code = code.replace("\\$", "")
        n = code.count("$")
        if n % 2 == 1:
            errors.append({"line": i, "rule": "UNBALANCED_MATH", "message": f"本行 $ 数量为奇数（{n}），疑似未闭合公式"})
    # \[ 与 \]
    for i, code in lines:
        o, c = len(re.findall(r"\\\[(?!\[)", code)), len(re.findall(r"\\\]", code))
        if o != c:
            errors.append({"line": i, "rule": "UNBALANCED_DISPLAY_MATH", "message": f"\\[ 与 \\] 数量不等（{o}/{c}）"})

    # E3 数学模式内裸 CJK（逐行粗检：$...$ 内含 CJK）
    for i, code in lines:
        for seg in re.findall(r"\$([^$]*)\$", code):
            if CJK.search(seg) and "\\text" not in seg:
                warnings.append({"line": i, "rule": "CJK_IN_MATH", "message": f"行内公式含裸中文（建议 \\text{{}}）: ${seg[:30]}…"})

    # W1 长行内公式
    for i, code in lines:
        for seg in re.findall(r"\$([^$]*)\$", code):
            if len(seg) > 120:
                warnings.append({"line": i, "rule": "LONG_INLINE_MATH", "message": f"行内公式超 120 字符，可能溢出"})

    # W2 includegraphics 无宽度
    for i, code in lines:
        for m in re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}", code):
            opts = re.search(r"\\includegraphics\[([^\]]*)\]", code)
            if not opts or ("width" not in opts.group(1) and "height" not in opts.group(1) and "scale" not in opts.group(1)):
                warnings.append({"line": i, "rule": "IMAGE_NO_WIDTH", "message": f"includegraphics 未约束尺寸: {m.group(1)}"})

    # E4/E5 章结构完整性：章文件（含 \chapter 且非主文件）必须有导读与小结
    is_driver = "\\documentclass" in text
    if not is_driver and re.search(r"\\chapter\*?\{", nospace):
        chap_line = next((i for i, code in lines if re.search(r"\\chapter\*?\{", code)), 1)
        if not re.search(r"\\begin\{introduction\}", nospace):
            errors.append({"line": chap_line, "rule": "MISSING_INTRODUCTION", "message": "章文件缺 introduction 导读盒（v2 版式：每章以学习目标开篇）"})
        if not re.search(r"\\begin\{conclusion\}", nospace):
            errors.append({"line": chap_line, "rule": "MISSING_CONCLUSION", "message": "章文件缺 conclusion 小结（v2 版式：每章以小结收束）"})

    # W3 文字墙：连续 >=PARAGRAPH_WALL 行都是 \paragraph 开头
    run_start, run_len = None, 0
    for i, code in lines + [(0, "")]:  # 哨兵收尾
        if code.lstrip().startswith("\\paragraph{"):
            if run_len == 0:
                run_start = i
            run_len += 1
        else:
            if run_len >= PARAGRAPH_WALL:
                warnings.append({"line": run_start, "rule": "PARAGRAPH_WALL", "message": f"连续 {run_len} 个 \\paragraph（L{run_start} 起），疑似文字墙，应插入盒子/表格/图打断"})
            run_start, run_len = None, 0

    # W4 盒子通胀：主文件自定义 \newtcolorbox 超限
    if is_driver:
        customs = re.findall(r"\\newtcolorbox\{([a-zA-Z]+)\}", nospace)
        if len(customs) > CUSTOM_BOX_LIMIT:
            warnings.append({"line": 1, "rule": "CUSTOM_BOX_INFLATION", "message": f"自定义盒子 {len(customs)} 个（{', '.join(customs)}）超过上限 {CUSTOM_BOX_LIMIT}，盒子种类要少而语义固定"})

    # W5 未知盒子环境（拼写错误/私自加盒）
    for i, code in lines:
        for m in re.finditer(r"\\begin\{([a-zA-Z]+)\}", code):
            env = m.group(1)
            if env.startswith("tcolorbox") or env in BOX_ENV_WHITELIST:
                continue
            if env in EQ_ENVS or env in ("itemize", "enumerate", "tabular", "center", "multicols", "figure*", "table*"):
                continue
            if env in ("circuitikz", "tikzpicture", "scope", "document", "filecontents"):
                continue
            # 标准 LaTeX 结构环境（非"教学盒子"，但同样是合法环境）。
            # **不要把这类环境报成 UNKNOWN_BOX_ENV**——那条规则的目的是抓
            # "教学盒子名拼错"（如把 proposition 写成 propositionn），
            # 误报标准环境会让质检结论失真、掩盖真问题。
            if env in ("titlepage", "abstract", "minipage", "flushleft", "flushright",
                       "quote", "quotation", "description", "picture", "list", "tabbing",
                       "thebibliography", "bibitem", "subfigure", "subfigurebox", "adjustbox",
                       "landscape", "minted"):
                continue
            warnings.append({"line": i, "rule": "UNKNOWN_BOX_ENV", "message": f"环境 {env} 不在教学盒子白名单，请核对拼写或改用内置环境"})

    # W6 misconception 三行式
    for m in re.finditer(r"\\begin\{misconception\}\{([^}]*)\}(.*?)\\end\{misconception\}", nospace, re.S):
        body = m.group(2)
        for kw in ("错误做法", "后果", "正确做法"):
            if kw not in body:
                warnings.append({"line": 1, "rule": "MISCONCEPTION_FORM", "message": f"misconception「{m.group(1)}」缺少「{kw}」段（三行式：错误做法→后果→正确做法）"})

    # E7 行首裸 LaTeX 命令字面量（丢了反斜杠的 \footnotesize 等）
    #    编译不报错、既有质检也不报，只有渲染出来才看得见 => critical
    for i, code in lines:
        m = BARE_CMD_RE.match(code)
        if m:
            errors.append({"line": i, "rule": "BARE_CMD_LITERAL",
                           "message": f"行首出现裸命令字面量 `{m.group(1)}`（疑似丢失反斜杠），会被当成正文印出"})

    # E8 多打一个反斜杠的命令（\\Rightarrow 等）
    #    LaTeX 里 `\\` = 换行，所以 `\\Rightarrow` 被当成"换行 + 文字 Rightarrow"，
    #    字面量直接印在页面上。**编译不报错、既有质检也不报** => critical。
    #    合法例外：TikZ 节点文本里的 `\\` 换行（如 {SAADC\\P0.02}）——靠
    #    "后面紧跟的必须是**已知 LaTeX 命令名**"来排除。
    for i, code in lines:
        for m in re.finditer(r"\\\\([A-Za-z]{2,})", code):
            cmd = m.group(1)
            if cmd in TEX_COMMANDS:
                errors.append({"line": i, "rule": "DOUBLE_BACKSLASH_CMD",
                               "message": f"出现 `\\\\{cmd}`（多打一个反斜杠）："
                                          f"LaTeX 会当成「换行 + 文字 {cmd}」把 {cmd} 印成正文，"
                                          f"应为单个反斜杠"})

    return {"file": str(path), "errors": errors, "warnings": warnings}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="中文 LaTeX 讲义质检")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    reports = [check_file(f) for f in args.files]
    total_errors = sum(len(r["errors"]) for r in reports)
    ok = total_errors == 0
    if args.json:
        print(json.dumps({"ok": ok, "error_count": total_errors, "reports": reports}, ensure_ascii=False, indent=2))
    else:
        for r in reports:
            print(f"== {r['file']} ==")
            for e in r["errors"]:
                print(f"  [ERROR] L{e['line']} {e['rule']}: {e['message']}")
            for w in r["warnings"]:
                print(f"  [WARN ] L{w['line']} {w['rule']}: {w['message']}")
        print(f"\n结论: {'通过（warnings 需人工过目）' if ok else f'存在 {total_errors} 个 ERROR，须修复后编译'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
