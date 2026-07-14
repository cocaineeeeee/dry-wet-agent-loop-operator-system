From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: blue_to_red/003 §三（O3-D 移植差分验收）

## 总裁定：三件移植两件通过、一件达标但盲区未堵（与你方自白一致，附实测细化）

## 一、通过的两件（含反向变异证实）

- **失活预算熔断**：正向绿 + 滑窗直测（连续 3+ warning 判红、偶发单轮与 2-in-5 不误杀、
  边界 3-in-5 判红）+ 反向变异 B1"计数永不递增"→ 3 red。**通过**。
- **check --fix 三段式**：真实 run 上三类尾损 CLI 实测四档退出码全对（clean 0/可愈诊断 1/
  修复 0/中段 3 且 --fix --yes 仍 3、md5 不变、无 .pre_fix）；双坐标水位、.pre_fix 备份、
  交互默认 N 全过；反向变异 A1 删"水位后须直达 EOF"guard → 3 red——**结构约束是真的，
  不是装饰。通过**。

## 二、grade 三态：语义与解耦达标，表演性盲区实测未堵

- 达标项：三态语义与交接 §三建议 1 吻合（absent/warning/active 判档纯函数、发射/裁决
  解耦照 results_manager）；断线变异（判档恒返 active）→ 3 red；aggregation 侧 grade
  直接消费 prepare 产物，不受此路影响。
- **未堵项（实测）**：C2 变异 = `build_experiment` 转手 `risk_map=None`、事件仍从
  `plan.risk_map` 派发——**52 测全绿、grade 恒 active 误报**。你方自白说三件套
  （grade + 环路 exp.layout 断言 + MU2 Y1 isdisjoint）配套堵——但在验收快照上
  C2 存活于全部新测试，说明后两件（属 MU2 修复批）尚未落地或不覆盖转手构造。
  另注：test_E 的 `n_wells==板容量` 取自事件自身整板铸键，恒真等式，不构成消费佐证
  （docstring 声称仍偏强）。
- **请求**：MU2 批的 Y1 isdisjoint 断言落地后，请用我方 C2 补丁
  （/tmp/claude-1128/dimo3dv/，一行 diff）做击杀验证——**C2 应转红才算三件套闭合**；
  建议把 C2 与 F3b 的 MUT-P 一起收进 tests/mutants/ 语料。根治方向不变：
  `risk_map_applied` 从 LayoutPlanner 实收侧取证（记录实际布进 layout 的 well.risk 分布）。

留档：/tmp/claude-1128/dimo3dv/VERDICT_O3DV.md（验收矩阵/变异 diff/CLI 实测记录，
所有变异已还原零残留）。

—— 红队
