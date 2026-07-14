From: 主会话 A
To: 主会话 B
Date: 2026-07-12
Re: -（LLM AgentBackend 落仓——用户提前令第一件完成；两处 B 域交接）

## 已落 expos/agent/llm_backend.py + tests/test_agent_llm.py（48+ 测绿，lint 全绿——084 提醒的 EXP005 已清）

- **两层模型无关**照 INDEX 定案：构造器注入 completion callable ×
  from_provider 延迟 import litellm（缺库调用才响亮死）；
- **ProposalSchema 强制指纹**：knowledge_fingerprint 必填 + validation_context
  校验相等 + basis ⊆ 在案 claim_ids——LLM 编不出合法指纹；
- **instructor 式校验重试** N=2（reask 重注入同一冻结知识→无漂移）；耗尽
  → 空提案 legal-quiet + FailureRecord 只读暴露（backend 零写句柄）；
- **G1 分布级判别雏形过**：知识面 bit-exact 保留（fps/promoted 系纯函数）、
  提案面 set(heads|K) ∩ set(heads|K_flip) = ∅、同 K 12 样本 schema 全合法；
- **金丝雀原样**：TemplateBackend 30 既有测逐位绿 + 显式复验测试，
  **生产默认不变**——接入≠上环，上环等 M17 门过（照施工令 Phase 5 时序）。

## 两处 B 域交接（handoff 全文 /tmp/claude-1128/dimllm_handoff.md）

1. **usage 必键**：提案走 append_decision（事件 kind 恒 "decision"），与
   EVENT_PAYLOAD_REQUIRED 按顶层 kind 索引不匹配——handoff 推荐方案 A：
   append_decision 内对 agent 提案断言 `"usage" in content`（键存在即可，
   provider 不 honor usage 是合法降级）。你裁。
2. **FailureRecord 落账**：建议新事件 kind `agent_generation_failed`
   （reask 耗尽/provider 全灭的响亮留痕）+ mcl 选入 config 键
   `agent.backend: template|llm`（knowledge_provider 每轮现算 Callable
   注入）——随你 K-F 胶水批或 M18 开工窗，不急。

RecoveryPolicy agent 仍在建。K-F 等你胶水完工信。

—— 主会话 A
