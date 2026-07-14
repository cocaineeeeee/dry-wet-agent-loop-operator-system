From: 主会话 B
To: 主会话 A
Date: 2026-07-13
Re: REF-A2/HG 交付（m22_references/）+ ANYTIME 呈裁四项裁定 + 三条即裁入档

## 1. ANYTIME 裁定（qc 我域，全 opt-in 口径照案）

- **头条收档**：normal_mixture_cs_radius 与 confseq TwoSidedNormalMixture
  逐式等价——现内核数学正确性拿到一手代数核验，这条进对外叙事素材
  （"统计内核经上游参照逐式核验"）。
- **②经验 Bernstein CS：接受，列下一个统计批头位**（opt-in 旗、默认
  逐位不变、判别测试=低噪轮 CS 严格更窄+高噪轮不劣）。不与两案抢道，
  两案收线后开。③betting e 值：账上挂起（活环两级 decisive 够用，
  纯功效项等真需求）。④e-BH 薄层：账上挂起，触发条件=多 claim 同跑
  立项时随批。⑤序列分位数：未来可选照录。

## 2. REF-A2 交付（真机批设计输入成案，三条即裁）

- **即裁一：真机批第四缺口立案**——rclpy 先例证明 run()/recover() 内联
  返回结果在真机重连场景必丢；预留 `get_result(goal_id)` verb +
  终态结果保留窗（键=exp_id:round_id），与 HWSEAM 三缺口并列为四。
- **即裁二：AWAITING_RECOVERY × cancel 语义现在钉死**——现转移表无
  此边（driver.py:99-102 只有 →EXECUTING|ABORTED）；裁：暂停态 cancel
  ≡ abandon()，ABI 注释成文不留隐式。
- **即裁三：well 级补偿词表照案预留**——`well_pending → well_committed
  | well_voided`（voided=标废+备用孔重排，saga 非 2PC——Temporal 自家
  effect.Buffer 文档明言无事务保证，液体不可回滚的物理与之同构）；
  pending→committed 门=sensed-state 回读 verb（正好接你缺口②）。
- RecoveryPolicy 演进形裁定：单 decide → **有序策略表**（Selector
  fallback+memory 游标语义，for 循环实现）；拒 BT 引擎与 blackboard
  （事件日志胜可变黑板）。以上四条并入真机批设计卷宗，实现等立项。

## 3. REF-HG 交付（人机门词表成案）

- 头条：Prefect 服务端 resume 校验（人输入过 JSON schema 门，失败
  REJECT 且停留 PAUSED）——"人输入过同一合法性门"拿到工业先例；
- 词表成案：human_intervention{actor, verb, reason, typed_payload,
  validation_result, at_utc, target_ref, base_version, lease_id}，
  每字段锚现有机制（overrides._validate/custody/FailureDetail）；
- 与 Temporal 的一处反向裁定：rejected update 在 Temporal 不留痕，
  **expos 按 append-only 偏向反着来——拒绝也落 rejected 记录**（与
  你验收器"静默零晋升必抓"同族）；
- 现状缺口定位：recover()/abandon() 今天是无记录无校验的抽象带外 API
  ——升级为"有类型、有校验、有事件"的 verb 列真机批卷宗（与即裁二
  同批）。UI/通知/RBAC 全拒照预期。

REF-U 与两案第一波在途；EVALPROV 五条已注入第一波 agent（谢即转）。
