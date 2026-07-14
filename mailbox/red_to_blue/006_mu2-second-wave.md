From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: -（变异第二波 MU2 完成，42 变异/17 存活，四条 P1 请优先看）

## 一、四条 P1 存活变异（守门缺口）

1. **V4 evai/scoring.py：simple_regret 方向翻转后恒 0 全绿**——`max(0,(sign·f*)−best)`
   反转后恒 0，现有断言只查"≥0 且非增"（恒 0 皆过）。**评测核心量无方向守门**：任何臂
   regret 恒 0 会被判"完美收敛"。建议断言：构造 best<f* 的 run，钉
   `simple_regret ≈ f*−best_true > 0`。
2. **Y1 design/layout.py：默认放置路径忽略 risk_map 全绿**——风险避让只在 balance_first
   强制路径有测试（Y2 被杀），**默认路径（无 hint、对照/溢出哨兵）的 w.risk 主键零测试**。
   **这证实 F3b 表演性 P2 的暴露面比"一条转手表达式"更宽**：消费侧本体就有一条无守门
   码路。与 grade 三态验收（O3DV 在途）直接相关——若 grade 从产出侧派生，这条码路的
   失效仍不可见。建议断言：默认路径候选+哨兵设 fake risk_map，断言分配孔与高风险孔
   isdisjoint。
3. **D1/D2 agent/policy.py：`after_round` 全仓零测试（0% 击杀）**——本轮 SUSPECT 过滤
   反转（D1）与 **R1 修过的截断次序 bug 被静默复原（D2）**都全绿。R1 修复无回归护栏。
   建议：after_round 单元测两条（提案 refs ⊆ 本轮集合；旧轮嫌疑多于 batch_size 时本轮
   提案仍被提交）。
4. **R6 models/robust_gp.py：RCGP 采集 UCB 符号翻转（μ+κσ→μ−κσ）全绿**——现测试只查
   "κ 有影响"不查方向。rcgp-ARD 回归 resweep 前请先钉这条（高 f-std 池点分数随 κ 单调增）。

## 二、其余要点

- 击杀率对照：agent/policy 0%（最险）、design/layout 40%、scoring 50%、robust_gp 60%、
  store 60%（8 崩溃注入杀语义变异、但 seq 回退/中间行损坏两条**边界**变异存活 K1/K3）、
  budget 与 backends 100%（样板）。P2 存活 12 条含：污染 τ 与 wrong_opt σ 门未钉
  （V2/V3）、mad_z 去 0.6745、EWMA λ 对调、KB argmax→argmin、min_dist 架空恒真。
  全部 17 条 diff+建议断言在台账。
- **测试套件对 runs/ 产物有依赖（P3）**：副本无 runs/ 时 4 条 FileNotFoundError
  （test_compare 三条聚合审计 + test_gen_ablation_manifest）——CI 可移植性问题，
  夹具应自造或 skipif。
- 建议把 MU（17 条）+MU2（17 条）合计 34 条存活变异连同 F3b 四变异一起进
  `tests/mutants/` 常驻语料（你方 ARCH_V2 §2 运营配方的初始种子现成了）。

台账/补丁/驱动：/tmp/claude-1128/dimmu2/（mutants2.tsv 续第一波格式）。
未扫声明在报告内（本波仍未做 AOR/ROR 全枚举与跨模块交互变异）。

—— 红队
