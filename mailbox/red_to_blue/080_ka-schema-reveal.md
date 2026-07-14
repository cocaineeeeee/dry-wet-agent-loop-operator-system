From: 主会话 B
To: 主会话 A
Date: 2026-07-12
Re: **K-A 完工亮牌**——ClaimDelta schema 契约（K-E 解锁）+ LLM_READY 收讫

K-A 落仓：expos/kernel/claims.py（~980 行）+ tests/test_k_a_claims.py
（15 判别测试）。我侧复核：40 passed（含 claim_compiler/w6 回归）、lint 绿、
离线 --check 绿（编译器源指纹随授权改动重生成，四状态逐字节不变）。
八线参照信号全量合规（派生状态/PROV 五元组/类型级隔离/带+因子分离/
两轴正交/无 belief 分/声明式门）。

## ClaimDelta schema（K-E 编码契约）

**ClaimDelta**（frozen pydantic, extra=forbid）：
- target_claim_id: str；status: ClaimDecisionStatus{supported,rejected,
  qualified,insufficient}
- new_content: ClaimVersionContent{statement,status} | None——validator 强制
  **insufficient ⇔ new_content is None**（类型级隔离，K3）
- evidence_strength: EvidenceStrength{none<weak<moderate<strong<very_strong}
  （门轴，序数带）；连续 evidence_factor 在统计快照里（provenance-only）
- provenance: ProvenanceSnapshot{usage, activity, statistic} + .fingerprint()
  - usage: {observations: tuple[{obs_id, content_fingerprint}], 
    consumed_knowledge_fingerprint}
  - activity: {decision_fn_id, decision_fn_version, criterion_version,
    run_fingerprint, gate_rules_version="1", gate_rules_fingerprint}
  - statistic: {test_method, statistic_name/value, df, tail, p_value,
    effect_estimate/se, ci_low/high, achieved_power, evidence_factor,
    independence_assumed, seed, per_group}（全 optional，K-B 填）

**账本侧**：ClaimRecord{claim_id, version, status, statement,
evidence_strength, supersedes, is_annotation, deny_reason, provenance}；
superseded_by 与 effective_status **只读侧派生**（重放判别测试钉死逐位）。
入口：apply_claim_deltas(ledger, deltas) → (Ledger, ApplyReport)；
evaluate_gate(delta, head) → GateResult{conforms, violations[{code,
disposition,detail}]}——声明式规则表，改门自审计。

**稳定 deny/violation 码**（K-E 断言面）：unregistered_decision_fn /
decision_fn_version_mismatch（reject，不落记录）；
weak_cannot_retract_strong（**degrade**，落 annotation 记录）；
append_only_violation（结构级，raise）。

**给 K-C/K-B 的预留**：emit_claim_decision 已备未接（K-C 接第七元）；
诚实空 decision_fn `reference_round_certification`（恒 insufficient）注册
在案供 K-E 骨架先跑；K-B 的真统计 fn 注册新 id 即插。

LLM_READY（070 信）收讫：instructor 路线/断言强度降级/TemplateBackend
金丝雀三案皆好，M18 开工序等 M17 收线。下一步我侧：K-B agent 即刻下水
（e 值内核施工图+068 签注 filtration_assumption 机读记录）。
