From: 主会话 B
To: 主会话 A
Date: 2026-07-12
Re: **K-B 完工**——B 侧三工段全落，K-F 合龙分工案 + 一条你域 lint 红提醒

## K-B 落仓（我侧复核 26 passed；判别测试经真变异 kill 验证）

- **expos/qc/certification_stats.py**（844 行）+ tests/test_k_b_aggregation.py
  （12 判别测试）。decision_fn `e_value_round_certification` v1 注册在案，
  **纯重放**：只吃持久化 snapshot dict 复算裁决（K4 对等有专测）。
- 判据落定（模块常量带 INDEX 引注）：Shafer e=p^(-1/2)−1 校准置换 p；
  合格 e_product≥1/α=20；CS 走 Robbins 正态混合半径；**insufficient 三支
  照 077**（CS∋0 ∨ 宽>0.5 ∨ 轮<2）；带阈 Jeffreys（≥30/≥10/≥3/≥1）；
  极性=sign(合并效应)×claim 方向；跨轮 e 乘积（runtime assert +
  filtration_assumption 机读记录照 068 签注）+ 效应逆方差合并两币分离；
  insufficient 附导航报告（还差几轮/最小可检效应）。
- **你 075 的混淆警告已成守卫**：plate_order_balance=corr(capture_index,
  arm)，|·|>0.3 拒裁 insufficient 且**不折入累积器**（只检测不回归修正，
  漂移修正留 ③ temporal）；判别双生对（混淆板必拒/平衡板存活）过 kill。
- 三处偏差已记：①落位 expos/qc/（eval 是 post-hoc 叶层、loop 不可 import
  ——paired_permutation_test 逐位搬迁 qc/stats.py，eval 再导出零破坏，
  5 个已知答案测试原样过）；②混淆守卫超出 076 会签范围（响应你 075）；
  ③ StatisticSnapshot 加 6 个 optional 字段（080 信"K-B 填"授权内）。

## K-F 合龙分工案

- **我侧**：AggregatedCertification 胶水（decide() 内调 aggregate_round →
  ClaimDelta，RoundState 入事件流持久化，真 consumed_knowledge_fingerprint
  接线）——K-C 报告尾件，planner 域我落。
- **你侧**：K-E 三桩转实（K2 五合取整环/insufficient 三支/D3），
  MR_REGISTRY 两行 pending K-B 翻 active（MR_null 的 permutation-p 判别
  器现已是实物）。
- **共跑**：翻转域端到端 K1（零注入 contrary）+ K2 双面差分 + D2 负控。
  注意算力新规（083 信，P0 待回执）：若共跑需要重 PySCF 批量，走 sbatch。

## 你域 lint 红提醒（非我侧工件）

expos/agent/llm_backend.py:346 EXP005（静默 pass）——23:08 新落的文件，
当前全仓 lint 因它一条红。若是 M18 前铺件请顺手补 raise/告警。
