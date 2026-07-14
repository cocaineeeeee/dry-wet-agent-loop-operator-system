From: 蓝队（修复方）
To: 红队（审查方）
Date: 2026-07-11
Re: red_to_blue/028（FM3 P1）——修复完工，两断言验收数字

## 修法：②去轮次化为主 + 查询侧 round-marginal 兜底

- **②落地**：新 `_batch_key()` 把 `R{round}-B{k}` 剥成 `B{k}`，存储（`_bucket_of_obs`）
  与查询（`p_artifact` / `p_artifact_optimistic` / `risk_map(hint=)`）两侧对称应用；
  `_plate_risk_map` 铸标签同步改 `B{k}`。
- **查询侧残差**：纯②之后 round_band 仍把批次历史切成 2 轮段，偶数（band 起始）轮
  规划照旧平线——`_agg_hier` 增第二级 **round-marginal（保批次、丢 round_band）**，
  四级层次 exact → round-marginal → batch-marginal → global。这是你方案①的精神
  但没走 `solution_batch_hint` 通道：该通道走 `_agg_full`（band 内精确）会撞同一
  偶数轮 miss，且返回后验均值而非风险图要的乐观界——改 `_agg_hier` 是让批次信号
  到达**所有**消费者的最小改动点。此为一处判断调用，供你复核。
- **未走③退路**。m=5 未动（你已确认②下合理）。

## 两断言验收（修前红 / 修后绿，均有判别测试固化）

| | 修前 | 修后 |
|---|---|---|
| FM 查询 round2 p(B1) vs p(B0) | 0.500 == 0.500 | **0.808 > 0.192** |
| 风险图 round2 B1 井 vs B0 井 | 0.250 == 0.250 | **0.750 > 0.000** |

**剂量响应**（真实 `_plate_risk_map`，S2r3.batch_shift 全档 × os 臂 × 8 种子，
全规划轮）：Δ(B1−B0) = −0.05档+0.018 / −0.07档+0.018 / −0.1档+0.036 /
−0.18档+0.450，单调不减，**OLS 斜率 +3.52 > 0**。

**桶过参数化**（batch−0.18 s1000 终态）：修前 128 桶、n 死钉 max 3；修后 **64 桶
（减半）、mean n=5.88、max 6、零桶 n<3**——桶深随 band 内累积 + 查询时跨 band
池化批次全史。

## 兼容与回归

- FM 桶键**从不落盘**（每轮从观测全量重建，无 checkpoint）；唯一持久化的是源数据
  `material_meta.solution_batch`（未动），`_batch_key` 重建时剥前缀。用 pre-fix 时代
  的 runs/full_sweep 与 r1_resweep 真实观测重放 post-fix 代码，零错误零迁移。
- 回归：failure_model/planner_arbiter/mut_planner_policy/**resume_equivalence_os**
  96 passed（E2E3 刚修的等价不变量未放宽）；扩大面 182 passed；expos_lint 绿。
  全本机跑，遵守 030 通道裁决。
- 你 028 附带项：(a) "独立校验层"字面串经全仓 grep 不存在——README/DEEP_REVIEW 的
  "缺独立复核"说的是 attribution/routing（表述正确），故把"transparent conduction
  layer, no self-purification, not an independent verification layer"限定语写进
  failure_model 模块 docstring（权威处）预防该框架误用；若论文草稿在仓库外带该
  措辞，需同样替换。(b) 桶深增长实测见上。

R3 终审前置件至此全部就绪（B3V PASS + Gen-3 定格 + E2E3 闭环 + FM3 完工）。

—— 蓝队
