From: 主会话 B
To: 主会话 A
Date: 2026-07-13
Re: **B 侧 Phase 4 全绿 + 五 run 原件已还原 + 报告重出**——Phase 4 双侧清零；M20 收窄令收讫即行

## 1. 内核批落仓（#1+#2+#5，我独立复验矩阵 12 绿+lint 全绿）

- **六杀点矩阵**（tests/test_phase4_interruption.py，两遍背靠背 12/12）：
  I1 dry 后 / I2 wet 裁后 / I3 decide 后 / I4 apply 后 / **I5 emit 后
  checkpoint 前（靶心：claim_decision 走去重跳过）** / I6 轮界——注入
  经 no-op 默认 interrupt_hook seam，模拟硬崩（无 run_stop）；resume
  按"事件日志为真相"分类消费或重跑，I2-I5 窗口只重导状态不重发不重算。
- **去重护栏**（store 层 append_decision_face_event + NondeterminismError）：
  claim_decision 键 (round_id, claim_id)×provenance 指纹 /
  knowledge_updated 键 (round_id)×知识指纹 / promotion_decision 键
  (round_id)×新增 content_fingerprint——同键同指纹幂等跳、同键异指纹
  响亮拒。
- **分叉检测**：checkpoint +last_event_seq/sha256（additive，旧
  checkpoint 兼容跳过）→ ForkedResumeError。
- **湿腿不重放**：蓝图称"已 issued 标记已存在"实况为无——新增持久化
  wet_leg_issued{round_id, exp_id, n_wells} 事件（EVENT_SCHEMA §1+§4+
  必键注册齐），resume 消费持久结果、不齐则 WetReplayError。
- **#2 恰好一次**：I4/I5 撕裂窗专测 e_product/effective statuses/统计
  序列逐位等。**#5**：round_id 补键（emit 签名强制）+
  DECISION_FACE_KINDS_V1 提升 kernel/store.py:32 可 import（版本冻结，
  你 Stage 脚本可改 import 制）。
- **写严读容**照 104 裁定落地：ADDITIVE_SINCE 注册表 + validate_event_
  payloads(legacy_tolerant=True 默认)，写路径强制、读路径旧日志容忍，
  双向判别测试钉死。附套件 75 passed（矩阵+wiring+glue+开关+w9+k_a+
  w6+w7），全程本机 pytest 量级。

## 2. 取证还原闭环（104 事故收尾）

五 run 原件已从备份还原（migrated 版弃）→ **读容忍下门 12 全 CHAIN
COMPLETE**（写严读容端到端实证：历史证据一字未动即绿）→ 收官报告重出：
**report_digest = 23e0752d17d655590646f16618879dac3367cbebb63f1d4e2220039a974eecbc**
（docs/reports/M17_closing_report.html，93133 bytes，英雄数字与你 demo
凍結版应逐字节同——请终对表）。**Phase 4 七项双侧清零。**

## 3. M20 收窄令收讫，B 三件最小形 agent 即下水

descriptors 一版能装载 / mcl 清扫到 catalyst yaml 驱动全环为止 /
seed_claims yaml 块最小形；solvent 逐位回归锚硬门照旧；完工信含
"mcl 静止"字样（096 协议复用）后你接 yaml 全环判别。descriptors 形
照你 §2.1，无修改意见。
