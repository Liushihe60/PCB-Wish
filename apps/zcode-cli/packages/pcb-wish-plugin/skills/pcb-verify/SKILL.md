---
name: pcb-verify
description: PCB 设计的两级验证规范：逐模块验证（连通/几何/参数/仿真或计算/边界五查）与整板验证（EDA DRC 之外的独立第二裁判）。铁律：DRC=0 不是板子正确的证据。当设计推进到 G2/G5 门禁、或用户要求"验证/检查/DRC"时使用。
---

# 两级验证（Verification）

## 第一级：逐模块验证（每完成一个模块就跑）

对每个模块（IR 中的 module），五查全过才算该模块完成：

| 查 | 内容 | 工具/方法 |
|---|---|---|
| ① 连通 | 模块内网络与块定义拓扑一致；边界端口与外部连接符合 IR | `netlist_assert.py`（模块级断言） |
| ② 几何 | 无重叠、间距达规则、丝印不压焊盘 | EDA 模块级 DRC + 几何校验脚本 |
| ③ 参数 | 元件值与 IR/计算一致（分压比、限流值、去耦配置） | 计算器复算 + 回读实例值比对 |
| ④ 电学 | 关键模块跑 SPICE（有模型）或 datasheet 计算复核（无模型，标 COMPUTED）：`python "${CLAUDE_PLUGIN_ROOT}/scripts/verify/testbench_run.py" --testbench <块>/testbench/*.sp --criteria <块>/testbench/criteria.json`（种子块示例：blocks/seed/buck-tps40200/testbench/）；.meas 缺结果=FAIL，绝不默认 PASS | `testbench_run.py`（ngspice）+ 块判据表；**禁止伪造厂商模型** |
| ⑤ 边界 | 块的验证判据（blocks/*/verification.criteria）逐条核对 | 块自带判据表 |

每模块的验证记录（输入、判据、结果、证据截图/波形路径）写入 `reports/module-<id>.md`。

## 第二级：整板验证（G5/G6 门）

顺序固定，前一步绿了才走下一步：

1. **EDA 自查**：`eda.pcb_Drc.check()`（官方文档确认含原理图-PCB 网表比对）→ 0 错误。
2. **独立第二裁判**：换一条与本 agent 生成路径无关的复核通道——
   - 独立网表断言（G1 门由 `ir2assert.py` 生成的断言 vs 导出网表；短路检测以 `net_members` 为准）；
   - 制造文件独立解析复推：`python "${CLAUDE_PLUGIN_ROOT}/scripts/verify/gerber_review.py" --dir <gerber目录> --layers <叠层数>`——文件集完整/可解析/铜层对象统计/钻孔刀具与孔数/钻孔范围⊆铜层范围，两条不同代码路径得出同一数字才算 VERIFIED；
   - 3D 导出做装配干涉检查；
   - 可选：kicad-cli DRC/parity 交叉（需 KiCad 安装）。
3. **冷重启复测**：保存 → 关闭 EDA → 重开工程 → 复测 DRC/网表（同进程结果不是文件证据）。
4. **只读对抗评审**：一个独立上下文的评审者只读不写，重推关键数字；**终止条件在开始前写死**（评审轮次上限、问题清单），否则无限循环。

## 证据与诚实纪律

- 每条结论标注：VERIFIED（实测/规则检查）/ COMPUTED（计算）/ ESTIMATE（估算）/ NOT ESTABLISHED（未建立）；
- incomplete 的检查不能报告为通过；工具自己先校准（"Verify the tool before the result"）；
- 全部证据落盘 `reports/`——它们同时是讲义的素材。
