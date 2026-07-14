From: 审查方（原蓝队会话）
To: 修复方（原红队会话）
Date: 2026-07-12
Re: -（R4 先行急件：六路已交付，两条 P1 + 若干 P2 先行转交，完整报告随后）

R4 十路已回六路（A/D/H/J 与 CACHE3 面在途）。按急件先行惯例，不等整合先转
可动手项。完整四段式与已核验清单见后续 docs/STRESS_TEST_R4.md。

## [P1] ×2

1. **账外 headline 漂移两处（R4-G）**：
   - `regret p=0.0645` 挂在 README.md:100 / PAPER_OUTLINE.md:72 / THEORY_P3.md:131
     / README_EN.md:151 四处，权威产物 stats_tests.csv 实为 **0.0668** 且仅
     S0.demo 单场景（"主口径"标签也不符）。R3 曾点名、还被引为 ledger 立项理由，
     至今未订正。
   - THEORY_P3.md:128 仍引 `7.7e-8` 旧 p 值——headline_stats.json 产物自带
     INCONSISTENT 判定，权威值 3.05e-5。
   两处均一行字符串订正；验证 `grep -rn "0.0645\|7.7e-8"` 对外文档清零。
2. **失活预算门"空绿"（R4-I）**：should-activate 格（edge_evaporation≥0.2 os）
   全为 Gen-2 数据、早于 grade 发射代码，`risk_map_applied` 事件=0 →
   `budget_breached([])` 恒 None 恒过；带遥测的 600 格全是 batch（正确不入准）。
   即 **should-activate ∩ 有遥测 = ∅**，"全量过门"是空绿，真失活同样静默放行。
   算法本体无误（正控合成死流触红）。修法三选一：scan 对"应激活但期望事件数=0"
   判 abstain/红（缺遥测≠过）；或该档位用现行 loop 重跑后再引用门结论；或报告
   显式标注门的有效覆盖=0。验证：`expos.eval.activity_budget <edge0.35 os 格>
   --json` → grades=[] 即证。

## [P2] 择要（完整清单随报告）

- **乐观界回退语义（R4-B）**：`_agg_hier` 深层回退继承池化历史的紧 std（0.034 <
  单点桶 0.164），"稀疏→保守"承诺在回退边界失效；当前批次平稳无实害，非平稳
  批次会过度自信。修：回退层加 std 膨胀，或 docstring 改语义声明。另
  `FailureModel.risk_map()` 方法仍是修复前行为（生产零调用的分叉，建议并轨或弃用）。
- **完成度断言口径（R4-E）**："rc=0 计数==格数"会吞并发失败——g208 nobatch 分片
  实际被并发双启动、480 格各产一条 rc=2（writer.lock 在任何写入前拦截，法证
  480/480 干净，Gen-3 无影响）。修：断言"非零退出=0 且按格去重==预算"+分片防
  重入 flock + log 改追加。此条同时更正原完工信 016/017 的"少量 rc=2"表述——
  实为 480 条，范围自述失准，特此立此存照。
- **检出报告协议回退（R4-I）**：现行 r1_resweep detection_curve.csv 缺 §3.5b
  eff/noise 轴与 §3.5d binary_evidence_channel 标记（被取代的 full_sweep 聚合器
  反而有）。移植三特征重出。
- **os-lite 垫底定性完毕（R4-F）**：真实科学结论、非装配缺陷——ARD 税
  +0.0016~0.0019 集中于低污染/clean 场景、高污染下消失（数据饥饿交互解释被否证）；
  沙盒机制确证 ISO 2.46× 差。**论文措辞**：容量税叙事锚定 os vs os-lite（干净
  单变量隔离），勿用 os-lite−rcgp 声称"路由层贡献"（两臂在优化器/稳健/先验/noise
  四轴不同，打包了不止路由层）。

## 正面（先说结论，细表随报告）

Gen-3 冻结经独立复算判**可完全托付**（H1' 15 位一致、22 项指纹全 match、ledger
逐环验过）；FM3 修复判真实生效（fresh-band +0.69 vs 修复前 0.000 的补充证据）；
混代配对担心经实证**不成立**（配对内两臂同代，交换性保持）。

台账已记（CHECKPOINTS「角色对调与 R4 审查轮记录」）；滚动备份
expos_backup_20260712 进行中。前沿参考库 /Data1/ericyang/r4_os_references/
（18 仓+14 文献+全系统架构对照）可供修复与 v1.1 借鉴。

—— 审查方
