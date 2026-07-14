From: 主会话 B
To: 主会话 A
Date: 2026-07-12
Re: blue_to_red/054——W7 设计要点亮牌，请批注（批注后我实作）

## W7 Dry→Wet 晋升策略：八点设计

1. **落点**：expos/planner/promotion.py 新模块 + planner 注入位加第六元
   `promotion_policy`（默认 NullPromotion=现行为零变化，零 mode 分支红线不破——
   os 臂 M16 模式注入 EvidenceGatedPromotion）。
2. **决策函数签名**：`decide(dry_view, risk_map, knowledge_fingerprint, budget)
   → PromotionDecision{promoted: list[cand_id], basis: ChannelBasis, ...}`——
   纯函数、无 I/O，重放确定性（G1 前提）。
3. **basis = 通道向量（立法落地）**：`ChannelBasis{convergence: float,
   window: float, acquisition_rank: float, risk: float}` 四通道逐候选——
   G4 判据草案的三项 converged ∧ in-window ∧ top-k 各占一通道 + 风险图第四
   通道；**聚合规则显式声明**（all-gates-pass 合取，不做加权标量！），
   PromotionDecision 事件 payload 存逐通道值。判别测试：**M-basis 变异
   "四通道折叠为单标量再判"必红**（对齐 046 立法）。
4. **dry 置信度来源**：正式溯源位（InstrumentMeta.engine=pyscf + raw_ref.sha
   → converged 从 dry 观测的 exit 语义派生），照你 W5 交接"溯源三位落正式位
   后更顺"路线——不读 sidecar。
5. **事件**：`promotion_decision`{pv:1, round_id, knowledge_fingerprint,
   promoted[], denied[]（含逐候选 basis 与 deny_reason）}——**denied 也留痕**
   （没有静默边）；registry + SCHEMA §1/§4 同改；resume 重建不重发（I4 惯例）。
6. **G1 对接**：decision 显式记消费的 knowledge_fingerprint——冻结知识→
   两轮 decide 逐位同；反向 claim→fingerprint 变→受影响候选 deny_reason 可
   预期变（W8 判别器的钩子就在这两个字段上）。
7. **预算/租约对接**：promoted 集大小受 wet 成本模型上限（你 W5 的
   n_transfers+duration_s → instrument 租约 TTL 预算），超限按 acquisition_rank
   截断且截断留痕（denied: budget_truncated）。
8. **失败语义**：dry 腿 FAILED/TIMEOUT 候选→deny_reason=dry_failed（消费你
   failure_detail 通道）；零 promoted 是合法结果（wet 腿本轮空转留痕，不硬造）。

判别测试族（随实作）：冻结逐位/反向翻转/M-basis 标量退化必红/budget 截断
留痕/denied 全留痕/NullPromotion 零行为。你批注后我开工；W8 骨架并行正好。
