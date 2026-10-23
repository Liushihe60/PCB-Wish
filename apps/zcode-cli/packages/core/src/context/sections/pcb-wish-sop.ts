// ============================================================
// PCB-Wish Fixed-Workflow SOP Section Builder
// ============================================================
//
// 本项目 fork 的领域核心：把「许愿式 PCB 开发」的固定工序写进稳定系统提示词，
// 与工具白名单/门禁 hook/门禁状态机一起构成指令遵循的多层保证。
// 书写规范（委托方指示）：陈述句、肯定句；不含本地日志、历史代价与样例；
// 引用保持最少且不断链；对齐 ZCode 原生提示词的行文风格。

import type { ContextSection } from "../types.js";
import { estimateTokens } from "../utils.js";

const PCB_WISH_SOP = `# PCB-Wish 固定工序

这是本工作台的主工序。每个任务按以下固定步骤推进，一次一步：

1. **受理**：运行 pcb-wish-intake 技能。产出 MISSION.md 并用 mission_check.py 校验；约束不全时停下向用户提问；数值缺失处标记待确认。
2. **门禁**：按 G0 方案确认 → G0.5 选型定稿 → G1 IR → G2 原理图 → G3 PCB 规划 → G4 布局 → G5 布线 → G5.5 清零 → G6 制造 →（勾选时）G7 讲义 的顺序推进。
3. **过门登记**：gate.py pass <门> --evidence <证据文件>；状态落 projects/<项目>/reports/gates.json；前置门未过或证据缺失时登记会被拒绝。

固定工程规则：

- 布局按原理图功能分区推进：先确定各功能区在板上的位置，同一功能的器件放在一起，芯片与其外围器件放在一起，相连器件的摆放服从分区结构。主电路连线尽量短，线宽尽量宽。
- 布线尽量短，避免兜圈子；使用钝角走线，避免锐角与直角走线。
- 每个写操作后用独立读取回读确认。
- DRC=0 时仍需网表意图断言、几何连通口径与独立第二裁判确认。
- 汇报写清清零账：error 与 warning 归零，或逐条给出豁免记录。
- 下单、支付、开票、对外发送由人工执行。

执行模式：每道门恰好一个执行实体，单进单出，阶段产物以 capsule 交接；主会话负责布置、监控与验收。详细判据与平台规程在内置的 pcb-wish 技能中，动手前先读对应技能正文。`;

export function buildPcbWishSopSection(): ContextSection {
  return {
    name: "PCB-Wish Fixed Workflow SOP",
    source: "pcb_wish_sop",
    injectionTarget: "system",
    cacheHint: "stable",
    chars: PCB_WISH_SOP.length,
    tokens: estimateTokens(PCB_WISH_SOP),
    content: PCB_WISH_SOP,
    preview: PCB_WISH_SOP.slice(0, 100),
  };
}
