import type { DraftSuggestedPromptItem } from "@/v4/draftSuggestedPromptItems.js";

/**
 * PCB-Wish fork：首页草稿推荐固定为硬件领域语料，
 * 不再依赖远端 Client Scenes 的通用编码推荐。
 */
export const PCB_WISH_DRAFT_SUGGESTED_PROMPTS: DraftSuggestedPromptItem[] = [
  {
    id: "pcb-wish-usbc-sensor",
    iconName: "circuit-board",
    label: {
      cn: "做一块 USB-C 供电的传感器板",
      en: "Build a USB-C powered sensor board",
    },
    prompt: {
      cn: "我想做一块传感器小板：USB-C 供电、I2C 输出、两层板、嘉立创打样，尺寸尽量小。先只问影响设计的关键参数，然后按 G0→G7 门禁流程推进，不要跳步。",
      en: "I want a small sensor board: USB-C powered, I2C output, two layers, fabricated at JLCPCB, as small as practical. Ask only the few questions that change the design, then follow the G0→G7 gated workflow without skipping steps.",
    },
  },
  {
    id: "pcb-wish-carrier-board",
    iconName: "cpu",
    label: {
      cn: "给我的开发板画一块底板",
      en: "Design a carrier board for my devkit",
    },
    prompt: {
      cn: "我有一块开发板，想给它画一块底板，把接口、供电和调试口引出来。先问清板子型号、尺寸和接口需求，再优先复用 blocks/ 里已验证的标准块开始原理图设计。",
      en: "I have a devkit and want a carrier board for it that exposes its interfaces, power, and debug port. First pin down the board model, size, and interface needs, then start the schematic by reusing verified blocks from blocks/ where possible.",
    },
  },
  {
    id: "pcb-wish-fab-review",
    iconName: "shield-check",
    label: {
      cn: "复核这块板能不能打样",
      en: "Review this board before fabrication",
    },
    prompt: {
      cn: "请对我当前项目的制造文件做一次独立复核：网表意图、几何连通、过孔与焊盘净距、Gerber 完整性，逐条给出 PASS/FAIL 和证据。发现问题先不要改产物，列出来给我裁决。",
      en: "Independently review the fabrication files of my current project: netlist intent, geometric connectivity, via-to-pad clearance, and Gerber completeness. Report PASS/FAIL with evidence per criterion; if something is wrong, list it for my decision instead of modifying artifacts.",
    },
  },
  {
    id: "pcb-wish-lecture",
    iconName: "book-open",
    label: {
      cn: "把这块板讲成一份讲义",
      en: "Turn this board into a course handout",
    },
    prompt: {
      cn: "项目已经过了制造门，请按教学讲义模板把这块板写成逐模块讲解的 LaTeX 讲义：每个数值和结论都标证据状态（verified/computed/estimate/notestablished），并能回链到工程记录，不要编造未实测的结论。",
      en: "The project has passed the manufacturing gate. Write a module-by-module LaTeX course handout using the teaching template: tag every number and conclusion with its evidence state (verified/computed/estimate/notestablished), link them back to project records, and never invent unverified results.",
    },
  },
];
