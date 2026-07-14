# M17 — Knowledge Feedback：从控制闭环到科学知识闭环

- **日期**：2026-07-12　**地位**：用户裁决立项（M16 收线裁决原文见 CHECKPOINTS 同日条目）
- **用户裁决要点**：M16 正式定名 **Executable Minimum Dry–Wet–Agent Control Loop**（可执行控制闭环）——G1 证明了"Knowledge 改变→提案真变→晋升真变"（agent 非装饰），但**尚未证明** "Wet result→自动统计与 claim 裁决→Ledger 更新→KnowledgeView 变→下一轮 agent 变"。当前 claims/hypotheses 系 run 起始种子、逐轮重编译同一组数据；翻转 claim 是外部注入的判别测试，不是 wet 数据自己推导的更新。
- **M17 唯一目标**：补上 **Evidence-to-Claim Compiler** 这条箭头，升级为 **Adaptive Dry–Wet–Agent Scientific Loop**。明确排序：**先于 LLM 接入、先于真机**。

## §0 目标管线（用户钦定）

```
Wet ObservationObjects
  → QC / Trust / Certification Policy
  → Statistical Aggregation（轮内小样本，诚实功效语义）
  → ClaimDecision ∈ {supported, rejected, qualified, insufficient}
  → Claim Ledger Update（append-only，supersede 语义）
  → compile_knowledge()
  → New KnowledgeView（fingerprint 变）
  → Agent 下一轮提案（真被重引导）
```

## §1 验收门（判别性优先，沿 M16 纪律）

| 门 | 判据 | 判别性验收 |
|---|---|---|
| **K1 wet 证据自动成 claim** | 每轮末从本轮 TRUSTED wet 观测统计聚合出 ClaimDecision 并入账（事件 + ledger 条目），全程零人工 | 构造"wet 证据与种子 claim 相反"的域场景（真值面翻转版）：种子 claim=polar-higher supported，wet 数据实际 nonpolar-higher → **无外部注入**，轮 1 证据须自动产 rejected/contrary 裁决 |
| **K2 Ledger 更新重引导 agent** | 轮 2 的 KnowledgeView fingerprint ≠ 轮 1（因 wet 证据入账），提案序可预期改变，promotion 集随之变 | 对照跑：wet 证据与种子一致 → fingerprint 演进但提案序不变向；证据相反 → 提案反向。两跑差分即判别（M16 G1 的翻转测试从"外部注入"升级为"数据自推导"） |
| **K3 insufficient 的诚实语义** | 小样本/低功效轮不得硬裁——n 或功效不足时 ClaimDecision=insufficient，**不改变** effective_status，且留痕可见 | 构造单孔/高噪轮 → 必须 insufficient 而非假 supported；NO_COVERAGE 家族纪律的 claim 层落地（缺证据≠支持） |
| **K4 决策链 provenance 闭合** | ClaimDecision 事件携带：输入观测 ids、统计量与检验、功效侧信息、消费的旧 fingerprint、产出的新 claim 版本 | 第三方仅凭事件流可重算该裁决（MIR-3 自足性纪律前移到出生点） |
| **K5 决定论口径** | 决策面（fingerprint 链/ClaimDecision 序列/提案序）同 seed 同数据逐位重放；执行面沿 M16 诚实边界 | 同 M16 |

## §2 与既有资产的关系

- **Certification Policy 的首次实例化**：四层 Policy 拆分（用户 v1.1 裁决）中 Certification Policy 一直是纸面层——K1 的"证据→裁决"正是它的第一个运行时形态。建议实作时显式落 policy 注入位（certification_policy 第七元或并入 verdict 家族），零 mode 分支红线不变。
- **③ 证据流 typing 的消费端**：ClaimDecision 的统计输入天然该吃 typed evidence records（CLEAN 的功效语义直接决定 insufficient 判据）——③ 试点三件（batch/temporal/glare）与 M17 同窗互喂，spec v1.1 就绪。
- **claim_compiler.py 的关系**：现有编译器是 campaign 级离线产物→ledger；M17 是 run 内逐轮在线路径。两者共用 claim schema 与 supersede 语义（REF-K 的 nanopub 三图断言、REF-P 的冻结快照 stale 判定直接参照），**在线路径不绕过离线编译器的治理**（decision_fn 注册、指纹纪律同源）。
- **统计原语现成**：轮内聚合用 qc/stats 家族 + eval/stats_tests 的置换检验（小样本精确路径已有）；功效标定表挂 ③ Q3 决议的独立标定产物。

## §3 分工提案（沿 M16 惯例，B 对案后定）

| 工段 | 内容 | 归属建议 |
|---|---|---|
| K-A | ClaimDecision 对象/事件 schema + ledger 在线更新路径（supersede/append-only） | B（kernel/claims 域） |
| K-B | 轮内统计聚合器（TRUSTED wet obs → 检验统计量 + insufficient 判据） | 共同设计，B 落（qc/eval 域）；功效表 A 供 |
| K-C | Certification Policy 注入位 + mcl 接线（轮末 hook） | B（loop/mcl 域） |
| K-D | 翻转真值面域场景（K1/K2 判别用）+ 对照场景 | A（domains/adapters 域） |
| K-E | K1-K5 验收测试套（判别器主笔） | A（tests/test_w8_* 续用或 test_k_*） |
| K-F | 首跑对表 + 收线 | 双会话 |

## §4 诚实边界（M17 不做）

不接 LLM（K2 证明确定性 agent 被数据重引导后，LLM 是换后端不是换结构）；不接真机；不做跨 campaign 知识聚合（单 run 内闭环先立）；统计聚合限单指标单假设族（多假设校正语义留 M18+，防 R4-I F3 重演的账目先记）。

## §5 施工令增量（2026-07-12 用户 continuation prompt，Phase 4 硬化 + 验收门扩充）

- **K-G 崩溃/恢复硬化**（Phase 4A）：六中断点矩阵——wet 观测落盘后 / 统计证据算出后 / ClaimDelta 发射后 / ledger 更新后 / 知识重编译前 / checkpoint 后——resume 均不得：双入账 claim 版本、双消费证据、双应用同一 ClaimDelta、静默跳过必要知识更新、产出不同终账本。幂等靠确定性 id/内容指纹。实现侧（mcl/claims）归 B，测试矩阵归 A。
- **K-G 重试分类**（Phase 4B）：timeout / defined recoverable / undefined / invalid input / 科学不收敛五分——undefined 恒 fail-closed。
- **K-G 窄 dry 复用**（Phase 4C）：spec_sha 匹配 ∧ result 指纹校验 ∧ SUCCEEDED 终态才计缓存命中，半写永不命中；config 门控默认关；不建跨 campaign 缓存（DRY INDEX 裁定原文被施工令采纳）。
- **验收门扩充**（施工令 14 门并入）：门 12=两次全新独立跑复现科学决策链；门 13=crash/resume 不双入账 ClaimDelta；门 14=隐藏真值不入统计编译器与运行时知识路径（API 形状强制+测试）。
- **Phase 5/6 提前令（用户 2026-07-12 晚）**：LLM AgentBackend 与真机 RecoveryPolicy 即刻施工（A 域两线已下水）——但**上环时序不变**：LLM 走 config 门控、TemplateBackend 保生产默认与金丝雀地位；RecoveryPolicy 默认 NeverRecover=现行为逐位不变。接入≠上环，上环等 M17 门过。
- **收官定名（施工令钦定）**："An executable adaptive Dry–Wet–Agent scientific knowledge loop with real dry computation, simulated wet instrumentation, provenance-aware evidence-to-claim compilation, and a deterministic knowledge-consuming agent."
