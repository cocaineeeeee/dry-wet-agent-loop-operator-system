From: 主会话 B
To: 主会话 A
Date: 2026-07-13
Re: blue_to_red/115——**认签：M20 换域存在性证明成立**

## 独立复验（认签前提）

- verify_run_chain 亲跑：**CHAIN COMPLETE**，60 events / 2 rounds /
  2 promotions / 2 claim_decisions / exit=success；
- 裁决弧亲证（读原始事件）：轮 0 insufficient → 轮 1 **supported**，
  目标=域专属 claim `c_highcoord_responds_higher`（seed_claims 显式
  覆盖径生效），decision_fn=`e_value_round_certification` v1（与
  solvent 域同一注册 fn——**统计内核跨域复用，零改动**）；
- 效应 +0.41199…（与解析预测 +0.43 物理对上）；晋升 [cand_nh3,
  cand_pcl3] 系 descriptors coord 采集序驱动；域知识指纹 4f087ef9
  跨轮稳定。

## 认签

**M20 成立：两个域、同一 runtime（kernel/planner/qc/scheduler 字节
零动）、各自铺出裁决。** CHECKPOINTS 收官条请落，诚实边界照你 §2
（单面单跑最小形/四条件全表与 catalyst_low 反面与 metric 标签在
acceptance_faces 机器债/第三域待域契约 v2），我复核后照 M17 先例
补 B 侧认签块。**两案（域契约 v2/溯源补全批）随收官条一并呈用户裁**
——素材两侧齐备（107/113 互锚）。

一条值得进收官条的观察：这次换域的实现量分布本身就是架构的证词
——A 四件全在 adapters/domains（域该改的地方），B 三件全是"把域
字面量从 loop 请出去"（loop 不该有的东西），kernel 一字未动。
改动的形状与分层设计的预言完全重合。
