# M18 前置 · LLM AgentBackend live 冒烟方案（草案 v1）

> 起草：主会话 A，2026-07-13（用户令：提前上环——催 B 接线 + 起草 live 冒烟）。
> 前提事实：`expos/agent/llm_backend.py` 已落仓（离线测试全绿）；litellm **未安装**；
> `OPENAI_API_KEY` 已在 g209 环境；mcl 环上尚无 backend 选择开关（B 域待接）。

## 0. 不可动摇的护栏（先于一切阶段）

1. **proposal-only**：LLM 只产 `ProposalSchema`，无工具调用、无文件系统、无网络副作用；
   碰不到 truth、改不了 ledger——与模板后端同一契约，一个字不放宽。
2. **truth isolation 断言**：prompt builder 的输入面 = KnowledgeView + 候选空间 + 预算，
   **不得含** reader 内部 / truth_profile / 任何 runs 原始湿数据。上环前加一条静态断言测试：
   构造含 `TRUTH_PROFILES` 标记的假注入，断言 prompt 构造路径 raise（删守卫必红式负样本）。
3. **fail-closed 继承**：litellm 缺失 → from_provider 响亮 raise（已有）；API 错误/校验重试
   耗尽 → legal-quiet 空提案（已有），环继续走安静轮。**任何 live 失败都不得把环带崩。**
4. **决定论口径收窄（诚实声明）**：决策面逐位断言**仅对 TemplateBackend 成立**。LLM 轮
   一律标注 non-deterministic；记录 model id / temperature=0 / provider 返回的 system_fingerprint
   （如有）/ 请求响应全文哈希入 runs/ 审计目录。金丝雀口径见 Stage 2。
5. **费用护栏**：max_tokens 上限（建议 2048/次）、每 run 调用数上限（重试含在内 ≤ 3×轮数）、
   模型先用 cheap tier（gpt-4o-mini 级）。冒烟总预算 < $1。

## 1. B 侧接线规格（一处开关，三档）

`mcl` 配置加一个 key（域归 B，形状 A 提议如下）：

```yaml
agent_backend:
  mode: template | shadow | llm     # 默认 template（生产默认不变，回滚=改回一词）
  provider: "openai/gpt-4o-mini"   # 仅 shadow/llm 档需要
```

- `template`：现行为，逐位不变（回归锚）。
- `shadow`：**决策仍由 TemplateBackend 出**，LLM 并行产提案但只落审计日志
  （`agent_shadow_proposal` 事件：schema 是否过、fingerprint 是否对、basis⊆claim_ids 是否成立、
  与模板提案的候选序 diff）。零决策影响，环行为逐位=template 档。
- `llm`：LLM 提案真实驱动环；legal-quiet 兜底。

## 2. 冒烟阶段（顺序执行，过一关开下一关）

### Stage 0 — 离线金丝雀（已绿，重跑确认）
现有 test_agent_llm 套件：校验重试、legal-quiet、fingerprint 必填、注入 stub 全绿。

### Stage 1 — 接缝单发 live ping（不进环）
`pip install litellm`（版本钉死记录 env.txt）→ 脚本单发一次真实 completion：
- 断言返回文本能过 ProposalSchema（含 validation_context 的 fingerprint 校验）；
- 反例一发：喂过期 fingerprint，断言 validate-and-reask 走一次重试；
- 请求/响应全文 + sha256 落 `runs/llm_smoke_stage1/`。
**通过判据**：1 次合法提案 + 1 次重试路径实证 + 零护栏触碰。

### Stage 2 — shadow 进环（金丝雀主口径）
flat 面（最安全：预期 insufficient、知识不变）跑 2 轮 `mode: shadow`：
- 环行为与同 seed `mode: template` 逐位一致（shadow 不得扰动决策面——判别性验收）;
- 审计日志里 LLM 提案 schema 通过率、fingerprint 命中率、与模板提案的序差异如实记录。
**通过判据**：决策面逐位等 + shadow 事件完整。
**显式前提（信 094 §2 补）**：「决策面逐位等」定义在**带版本号的白名单**上——
`DECISION_FACE_KINDS.v1 = {knowledge_updated, decision(PRIOR_PROPOSAL 序),
promotion_decision, run_stop}`；`agent_shadow_proposal` 事件**构造性排除**
（其 usage/latency/response id 天然非决定论，纳入则 bitwise 永不可能过）。
改白名单必升版。
**shadow 期护栏（信 094 §4）**：shadow 调用计入 §0 同一费用池（≤3/轮含
reask）；shadow 事件须记 `prompt_sha256`（Stage 3 首轮断言同条件哈希一致，
否则 shadow 期数据对 Stage 3 无推断效力）；校验门带稳定版本 id
（`fingerprint_echo@v1`、`basis_subset@v1`）入事件；reask 回注整份冻结
知识的现行做法**保持勿改**（局部修补造成 prompt 漂移破坏同分布假设）。

### Stage 3 — llm 档单 run 上环（收官件）
**统计口径（信 094 §3 成案，判据版本 `d1_c2st.v1`——改判据必升版）**：
- 契约合规 + 反事实响应性口径（EVALGUARD 结论：solvent 域小，不判能力优劣）；
- 分布判别 N=30/条件（60 调用，≪$1 护栏内）；标签置换 C2ST 复用
  qc/stats.py 惯例（9999 次置换 +1 平滑、seed 固定）；判据 p<0.01 ∧
  判别准确率 ≥0.70（0.5+3σ）；bootstrap CI 必附，不裸报点估计；
- 如实声明：30 样本为同条件重复、非独立种子（诚实口径，参照线佐证）。

consistent 面 2 轮 `mode: llm`：
- 提案过同样的 schema/fingerprint 门；晋升四通道门、QC/Trust、聚合器全部无感（对上游
  提案来源盲）；
- 事件流含 agent 代次事件（含 B 待办的 `agent_generation_failed` 种类，若触发）；
- run_stop=success；knowledge_updated 链完整；全程费用与调用数入报告。
**通过判据**：一次零人工 LLM 驱动闭环 + 护栏零违例 + 审计可复查。

## 3. 排期与分工

- **A**：Stage 1 脚本 + truth-isolation 负样本测试 + litellm 安装钉版（llm_backend 域归 A）。
- **B**：§1 config key + shadow 档事件种类（mcl/loop 域归 B）；`agent_generation_failed`
  事件种类正好一并落。
- 时点：三面共跑收官**之后**立即执行（用户已裁可提前——若 B 接线先就绪，Stage 1 可与
  三面共跑并行，Stage 2/3 排在其后以免争 flat/consistent 面的运行资源）。
- 回滚：任何阶段失败 → `mode: template`，零代码回退。
