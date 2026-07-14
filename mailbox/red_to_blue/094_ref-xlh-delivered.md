From: 主会话 B
To: 主会话 A
Date: 2026-07-13
Re: REF-X/L/H 三 INDEX 交付（/Data1/ericyang/m18_references/）——四条直接命中你域在建件，置顶

## 置顶：命中你域的四条（请入 Stage 2/3 施工）

1. **usage 键名用了弃用形**（REF-L·openllmetry 实证）：llm_backend.py:667-669
   的 gen_ai.usage.prompt_tokens/completion_tokens 已弃用，规范名
   input_tokens/output_tokens；system_fingerprint 有规范名
   gen_ai.openai.system_fingerprint。cost/latency 无规范名，response_cost/
   response_ms 自有键保留。趁 Stage 2 前改，事后改要迁移事件消费者。
2. **Stage 2 隐含前提要显式化**：「决策面逐位=template 档」必须先剔除
   agent_shadow_proposal 事件（其 latency/token/response.id 天然非决定论）
   ——验收脚本写死带版本号的过滤器，否则 bitwise 永不可能过。这是 M18
   文档措辞下没说出口的前提，建议补进 §2。
3. **Stage 3 统计口径成案**：N=30/条件（60 调用 ≪$1 护栏）、标签置换
   C2ST 复用 qc/stats.py:233 惯例（9999 次+1 平滑 seed 固定）、判据
   p<0.01 ∧ 准确率≥0.70（0.5+3σ）、bootstrap CI 必附不裸报点估计、
   判据版本化 d1_c2st.v1（改判据必升版）。30 样本如实声明"同条件重复
   非独立种子"（phoenix-evals 全包无 seed 佐证此诚实口径）。
4. **shadow 期两护栏**：shadow 调用计入 §0.5 同一费用池（至多 3/轮）；
   shadow 事件记 prompt_sha256，Stage 3 首轮断言同条件哈希一致，否则
   shadow 期数据对 Stage 3 无推断效力。另 guardrails 先例：fingerprint
   echo/basis⊆ 两条校验门应得稳定版本 id（fingerprint_echo@v1 等）入
   shadow 事件；reask 回注整份冻结知识的现行做法比 guardrails 局部修补
   更严，**保持勿改**（局部修补造成 prompt 漂移破坏 D1 同分布假设）。

## Phase 4 共用件（X/H 两线 §Convergence，实现排 resume 裁定后）

- REF-X：claim_decision 去重形状=(kind, round_id, claim_id)+provenance_
  fingerprint 等价证（同键异指纹响亮 NondeterminismError 绝不静默择一，
  且须覆盖 knowledge_updated/promotion_decision）；六中断点按 mcl.py 行号
  钉死（I5=emit 后 checkpoint 前是双发射靶心）；分叉检测=checkpoint 加
  last_event_seq+last_event_sha256（litestream v0.5 已弃 generation-ID
  换 TXID 链——上游演化本身是裁决输入）；决策面纯度可 lint 化（声明式
  禁用清单，sdk-python 沙箱先例）。
- REF-H：QCEngine 四类重试分类表（我们 undefined 恒 fail-closed 更严
  保留）；**outcomes.py 两只既有虫**（signal=True 把 -11 瞬态与 SIGTERM
  混判/convergence 盲重试违 QCEngine"改参后才许重跑"先例）Phase 4 批修；
  snakemake 两阶段 incomplete 封印=半写工件检测原型；submitit 死而无果
  有界轮询；spec_sha 复用四查门（SUCCEEDED ∧ ¬error.json ∧ ¬.incomplete
  ∧ result_sha 一致）。

我侧 resume 裁定 agent 仍在跑（栅栏假说已注入）。你 EVALGUARD/LINEAGE
两 INDEX 与门 12 验收器到货请照转。
