# PCB-Wish

把一句自然语言的硬件需求，变成一块可制造的 PCB——面向有电子设计基础的用户，基于
嘉立创EDA专业版（LCEDA Pro）官方 CLI/CLI-MCP 通道 + easyeda-agent 驱动层。

**当前状态：阶段 A（工作流骨架）+ 阶段 B 验证层脚本已可用；电气设计执行的端到端联调依赖安装嘉立创EDA专业版桌面客户端（V4.1.60+）与 ngspice（见下"待安装"）。教学模板 v2 已验收。**

## 已实现（阶段 A + B 离线部分）

| 组件 | 说明 |
|---|---|
| `skills/pcb-wish-intake` | 许愿受理：范围护栏 → 规格缺口分析 → 一次结构化提问（问题测试，预算 2 轮）→ 生成 `MISSION.md` → 机器校验。**FAIL 不放行，杜绝"带缺口开工"与空转** |
| `skills/pcb-design` | 设计引擎（G0-G6 门禁，每门确定性判据）：G1 门自动运行 `ir2assert.py` 生成断言文件（断言与 IR 同源）；EDA 驱动通道不可用时正确报停 |
| `skills/pcb-verify` | 两级验证规范：逐模块五查 + 整板独立第二裁判（DRC≠证据） |
| `skills/teaching-lecture` | 教学讲义生成（模板 v2）：逐模块"导读+五段式+小结"七件套 + 总览章，ElegantBook 内置教学环境全用满，四态状态徽标回链，渲染评审闭环，无习题 |
| `agents/` | **五个专用 agent（2026-10-23，按阶段开面）**：`pcb-intake`（受理，带提问工具）/ `pcb-sourcing`（G0.5 选型专班：WebSearch+WebFetch+库内严格搜索+商城网页引用，**无画布**）/ `pcb-design`（设计引擎，限定 skills=[pcb-design] + mcpServers=[lceda-pro]）/ `pcb-verify`（独立第二裁判，只读 `Read+Bash`）/ `pcb-lecture`（讲义作者）。正文即完整系统提示词，工具面按阶段收窄 = 指令遵循的结构性保证；hooks 再按门禁机械兜底（布线类脚本须过 G4，导出类须过 G5.5） |
| `scripts/gate.py` | **门禁状态机**：G0→G7 门禁表（`projects/<p>/reports/gates.json` 唯一事实来源）。`pass` 带前置门检查 + 证据文件存在性检查；`fail` 自动回退其后各门；`status/next/require` 供 agent 与 hook 读 |
| `hooks/hooks.json` | **硬门禁 hooks**：PreToolUse 拦截未过门的 EDA 写操作与对 gates.json 的手改；SessionStart 注入项目门禁简报。判定器 `scripts/hooks/gate_hook.py`、简报 `scripts/hooks/session_brief.py`（均 fail-open 设计：自身故障绝不阻断工作流） |
| `scripts/validate_agents.py` | agent 定义离线校验器：name=文件名、frontmatter 合法性、目录位置（agent 路径由 loader 硬编码为 `agents/<name>.md`） |
| `scripts/mission_check.py` | MISSION.md 完备性校验器（含正反用例：`scripts/test_fixtures/`） |
| `scripts/decision_log.py` | append-only 决策日志管理（init/add/supersede/validate） |
| `scripts/doctor.py` | 环境自检（EDA / 教学 / 仿真 / 布线 / 交叉验证分级） |
| `scripts/ir/` | IR schema + 校验器 + **`ir2assert.py`（IR→网表断言生成器）** |
| `scripts/verify/netlist_assert.py` | 网表意图级断言（must_connect/must_not_connect/pin_count/net_exists/**net_members 精确成员集**——短路检测以此为准：短路=多成员、漏连=缺成员，DRC=0 时仍会 FAIL） |
| `scripts/verify/testbench_run.py` | ngspice 批处理 runner：`.meas` 结果解析 + 判据评估（缺测=FAIL，绝不默认 PASS）；日志解析器已离线单测 |
| `scripts/verify/gerber_review.py` | 制造文件独立解析复核（零依赖）：文件集完整/可解析/铜层对象统计/钻孔统计/钻孔范围⊆铜层范围——G6 门的独立第二裁判 |
| `blocks/` | 标准电路块库：schema + 种子块 `buck-tps40200`（含分立级 testbench + 判据，draft 状态待 ngspice 联调） |
| `templates/` | MISSION.md 模板 + 讲义模板 v2（七件套骨架/总览章骨架/粒度样例章 + 渲染 PNG） |
| `scripts/eda/lceda_detect.py` | 嘉立创客户端**自动定位**（环境变量→PATH→注册表→标准目录→盘根，不写死路径；注册表命中还会带出版本号） |
| `scripts/eda/lceda_mcp_launch.py` | MCP stdio 启动器：自动定位客户端后拉起 `--mcp stdio` 并转发，错误只写 stderr |
| `.zcode-plugin/plugin.json` | `mcpServers` 声明经启动器挂载（`${CLAUDE_PLUGIN_ROOT}` 展开），客户端装没装、装在哪都不影响插件加载 |

## 环境检测与版本门

- `doctor.py` 对嘉立创客户端做自动检测并显示**检出来源**；版本低于 **4.1.60**（`--mcp stdio` 通道下限）时报 FAIL 并给升级指引；
- 可用环境变量 `PCB_WISH_LCEDA_PATH` 显式指定客户端 exe（优先级最高，部署/绿色版场景用）。

## 已验证的 EDA 通道

2026-10-19 实测：`lceda_mcp_launch.py` → `lceda-pro --mcp stdio`（客户端 4.1.78）MCP initialize 握手成功（serverInfo: JLCEDA Pro 4.1.78.34f5b8d9）。**注意**：低于 4.1.60 的版本（如 3.2.149）对 `--mcp stdio` 是空操作（进程立即退出、无响应），doctor 会拦截。

## 安装（ZCode 桌面端）

1. 打开 **插件市场 / Plugin Marketplace**。
2. **添加 / Add → 添加插件市场 / Add Plugin Marketplace**，粘贴目录：
   `E:\agent project\plugins\`
3. 在 **个人 / Personal** → "pcb-wish-dev" 市场中找到 **PCB-Wish**，点击 **安装**。
4. 安装后可在 **设置 → 插件** 管理；在 composer picker 中选择该技能后开始使用。

## 快速上手（验收样例）

选中新装技能后发送：

> 我想做一块 TP4056 锂电池充电 + USB-C 输入的小板，带充电指示灯，两层板就行，成本越低越好，板框 25×40mm，嘉立创打样。

**预期行为**（intake 技能）：
- 解析出已给约束（接口/成本/尺寸/渠道），列出缺口（充电截止电流、指示灯接法偏好等）；
- 若有影响设计的缺口 → **一次性打包提问**，而不是挤牙膏；
- 生成 `projects/xxx/MISSION.md` 并运行 mission_check；
- 向用户明确"约束齐备，可以开始设计"或"还缺 XXX，请补充"。
- 注意：本例无需超过 2 层板、无 RF → 护栏通过；若你许愿"帮我画个 2.4G 前端"，会收到硬拒绝。

## 设计取舍

- **hooks 硬门禁已实装（2026-10-22，v0.3.0）**：此前"本机无样例、schema 未验证"的暂缓结论已被源码级核验推翻（`runtime-config.ts mergeRuntimeHooks`：插件声明 hooks 即自动启用 runner；官方 superpowers 插件为现成样板）。现三层强制：①PreToolUse 拦未过门的 EDA 写与手改 gates.json；②SessionStart 注入门禁简报；③状态由 `gate.py` 状态机管理。**误拦自保护**：工具参数认不出项目时放行（多项目工作区宁可不拦，不张冠李戴）；hook 自身故障 exit 0 绝不阻断工作流。
- **驱动层**：`lceda-pro` 官方 CLI（V4.1.60+ 内置，含 `--mcp stdio`）为唯一驱动通道；不 fork harness、不自研协议。
- **工作流/块库/模板全部与 harness 解耦**（纯文件+纯 Python），可移植至兼容 agentskills.io 的其他 agent（Qwen Code 等）。

## 完成 Definition（验收标准）

1. 端到端：自然语言需求 → 4 层板制造文件 + DFM 通过，约束齐备时全程无人工干预；
2. 可靠性：约束缺失 100% 停下询问（零幻觉开跑）；历史踩坑（如 DRC=0 静默短路）由网表断言层拦截；
3. 可复现：同需求重跑结果一致，决策日志完整；
4. 教学：勾选后产出图文一致、事实可回链的 PDF。

对应文档：`../方案设计-v2-PCB许愿式设计Agent.md`（骨架）、`../调研报告-PCB许愿式设计Agent.md`（可行性）。

## 许可

本仓库代码 MIT。依赖注意：easyeda-agent（MIT，部分组件 Apache-2.0）、spicelib（GPL-3.0，仅作为外置工具调用）、CircuitJS1（GPL-2.0+，仅引用在线服务时不触发传染）。
