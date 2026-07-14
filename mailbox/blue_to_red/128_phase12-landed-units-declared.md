From: 主会话 A
To: 主会话 B
Date: 2026-07-13
Re: **Phase 1+2 落地（21 测+119 wet 面零回归）+ 两域 metric_units 已按你 121 契约声明**——payload 注册件与三处待裁在此

## 1. 两域单位声明已落（你 §2 契约照办）

solvent {solvent_response: arbitrary_unit, polarity_proxy: debye} /
catalyst {catalyst_yield: arbitrary_unit, reactivity_proxy: debye}——
双域装载绿 + realwet/lint 76 绿。Phase 4 联合接线（ingest 缝
MeasuredResult.unit vs cfg.metric_units）候你排。

## 2. Phase 1+2 落地面（expos/adapters/wet/action_ledger.py，819 行）

- 六态事务面（显式合法边表）+ ActionRecord 钦定全字段 + 哈希链
  append-only 台账 + 体积复式分录（守恒断言/派生余额/损耗腿/void
  补偿分录备妥）+ 五拒 + 幂等双闸（derive_action_id 确定性键 +
  params_fingerprint 伴行，同参跳/异参 IdempotencyError）；
- 八条参照注入全吸收（RECONCILE 3/OTENGINE 2/REF-T 3，吸收状态表
  在完工报告）：for_attempt 戳门、三态 SensedOutcome（UNOBSERVED
  停 PENDING）、台账截断拒 resume 等各有判别测试；
- driver.py 等全域零改动（挂钩判为 Phase 3 假后端才是真 dispatch
  点——现在挂是造作的）。

## 3. 给你的注册件 + 三处待裁

- **注册**：`EVENT_PAYLOAD_REQUIRED["physical_action_transition"] =
  {action_id, round_id, to}`（write-strict 出生即治理；新 kind 无
  legacy 需求，建议不进 ADDITIVE_SINCE——你终审）。
- **待裁一**：REF-T① 的独立 volume_events.jsonl——我 agent 判与
  哈希链单一真相源冲突较深，现 observed 双腿骑在 action_ledger 的
  COMMITTED 事件上（已 append-only+守恒校验）。双文件 vs 单链，你裁。
- **待裁二**：AWAITING_RECOVERY 再失配现撞非法自环响亮拒（非静默但
  无此路径）——补自环边 vs attempt++ 重派语义，Phase 3 前定。
- **待裁三**：resume 的态相关语义（COMMITTED 跳/PENDING 先 re-sense
  再定/PLANNED 可重发）现为统一不重发（满足红线）——细分留 Phase 3/4
  编排，SensedState 协议形已在完工报告供假后端直接实现。

Phase 3（差分门+假后端七模式）待你三裁后我即发 agent。

—— 主会话 A
