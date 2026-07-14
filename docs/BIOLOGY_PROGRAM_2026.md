# expos · Biology Program 2026（用户正式总令 2026-07-14，权威）

> **战略定性**：M24-B 已证明 sequence → phenotype → claim → knowledge → redesign 可闭环、
> 且 kernel/ledger/certification 保持生物盲。**expos 由此正式从"跑通一个生物闭环的 runtime"
> 改造为 Biology-Primary Adaptive Research OS。** 不再把多数精力用于打磨
> `cell_free_expression_screen`（保留它为回归 + 因果闭合锚）；而是让系统逐步覆盖
> 蛋白、基因迴路、细胞状态、扰动生物学与自动化实验执行。**权威裁决原文见本文件 §7。**

前沿五线在 2026 汇合：生成式生物设计 + active learning/DBTL + cell-state/perturbation
models + biological scientific agents + protocol-to-robot execution。expos 不选其一，而
把它们变成同一个**可验证研究闭环**。

## 1. Program 目标（一句）
> 从生物**序列与干预**出发 → 生成设计 → 执行或模拟 assay → 观测表型与细胞状态 →
> 认证生物 claim → 用被改变的知识创造下一个生物实验。四种能力：**Design / Program /
> Perturb / Understand biology**，由 expos 统一走 Hypothesize→Design→Build→Test→
> Analyse→Certify→Learn→Redesign。

## 1.5 施工策略：BREADTH-FIRST v0.1 PASS（用户裁决 2026-07-14 晚，覆盖深度优先）

**不要"M25 做完才进 M26"的线性路线。** 先把 M25–M29 全部做成薄型 v0.1 vertical slice，
先实例化 Biology-Primary OS 的**整张骨架**，再用真实结果选一两条深挖（预期 M25+M27+M29，
但先让五个都存在）。**目标不是五个完整产品，是先长出五个器官。**

**每个 M 的 v0.1 vertical slice = 一条真闭环**（非只有 architecture doc / README / class stub）：
```
正式 typed 资料物件 → adapter/provider → 一条可执行 end-to-end run → observation
→ claim decision → knowledge update → 下一步行为改变（排序/分析/扰动/拓扑/protocol）
```

### v0.1「薄型完成」定义（每个 M 至少 10 项，缺一不算完成）
1. 一个 typed domain object + provider；
2. 一条可执行的 end-to-end run；
3. 至少一个正向面 + 一个反向或 flat 面（acceptance faces）；
4. append-only provenance + model/dataset/protocol 指纹；
5. trusted observation 进入**既有** claim 生命周期；
6. claim 更新 + knowledge 更新；
7. 被改变的 knowledge 改变后续某个决策（proposal/analysis/perturbation/topology/protocol）；
8. machine-generated report；
9. 诚实标注 simulation / retrospective / fake-backend 局限；
10. kernel/ledger/evidence-compiler/knowledge-compiler **零生物特化**。

### 五 Team 目录分工（防踩档案）
```
Team M25 Design    domains/generative_construct/ · adapters/dry/mutation* · design/lineage*
Team M26 Program   domains/genetic_circuit/ · adapters/dry/circuit* · adapters/wet/timeseries*
Team M27 Perturb   domains/perturbation/ · adapters/models/virtual_cell* · datasets/replay*
Team M28 Understand agents/biology_discovery/ · hypotheses/ · analysis_backends/
Team M29 Execute   protocols/ · adapters/physical/ · device_ir/
```
**Integration owner（单写者，= 主会话 B）**：共用 schema 冲突 / kernel neutrality scan /
fingerprints / event schema / Gate 12·report / 跨 milestone 回归 / **mcl.py 接线**。
**五 Team 绝不同时改 mcl.py 或共享中央文件**；需要共用接缝时由 integration owner 单写。
Team agent 只在自己域目录做实现 + 声明 seam 需求给 integration owner。

### 本轮暂禁（否则一定卡在 M25 到不了 M29）
大规模模型训练 · 完整 SBOL/RDF runtime · 完整 single-cell pipeline · 真实机器人视觉 ·
大规模蛋白结构生成 · 多仪器实验室部署 · 每个 milestone 的 UI 精修 · 论文级 benchmark。
**五个 v0.1 全部双签后**，再比较各自科学/工程产出、选最强 1–2 条深挖。

### 用户 breadth-first 总令原文
```
User ruling — execute a breadth-first Biology Program pass. Do not fully complete M25
before beginning M26–M29. Build rough but runnable v0.1 vertical slices for M25, M26,
M27, M28, M29 in parallel. The goal is to instantiate the complete architecture of a
Biology-Primary Adaptive Research OS before deeply optimizing any one scientific track.
[v0.1 completion = typed domain object+provider; one runnable end-to-end workflow; ≥1
positive and ≥1 negative/flat face; append-only provenance + impl fingerprints; trusted
observations entering the existing claim lifecycle; claim+knowledge update; changed
knowledge altering a subsequent proposal/analysis/perturbation/topology/protocol
decision; a machine-derived report; explicit simulation/retrospective/fake-backend
limits; zero biological specialization in kernel/ledger/evidence-compiler/knowledge-
compiler.] M25 gen construct mutation loop; M26 typed circuit graph + time-series
dynamic claim; M27 cell-state+perturbation, simple vs complex backends, informative
selection, causal claim; M28 minimal multi-agent discovery (hypothesis/analysis/
contradiction/replication, agents propose+analyse but only trusted evidence modifies
claims); M29 protocol → device-neutral typed actions → fake physical backend →
sensed-state → commit/rollback/timeout/duplicate-reply/resume. One integration owner
for shared orchestration+schema; five teams do not edit shared central files
concurrently. No large models / full SBOL·RDF runtime / real hardware / publication-
grade benchmarks in this pass. After all five v0.1 milestones are countersigned,
compare yield and select the strongest one or two for deep development.
```

## 2. 里程碑路线图（M25–M29）

| 里程碑 | 名称 | 定位 | 主要新物件 |
|---|---|---|---|
| **M25** | 生成式蛋白/酵素设计闭环 | **立即施工 Build 主线** | parent→variant 生成、sequence lineage、可审计变异算子、PLM/结构 backend（可选冻结）、多目标 acquisition |
| **M26** | 基因迴路与可程式化细胞 | Research 优先 | SBOL typed graph、topology 候选身份、time-series 表型、动态相位、context-dependent claim |
| **M27** | 扰动生物学与虚拟细胞 | Research 优先 | cell state + perturbation + context、response 分布、模型竞赛、Perturb-seq/单细胞/空间观测 |
| **M28** | 自主生物假说与多组学分析 | 后续 | Hypothesis/Assay/Omics/Contradiction/Replication/Mechanism agents，结论落 claim ledger |
| **M29** | Protocol 编译与具身湿执行 | 硬体交会，**不抢 M25 前** | biological intent→typed protocol→compiler 验证→device code→sensed-state→physical commit（复用 M23 事务/sensed-state/resume） |

**M25 是下一个立即落碼的主要里程碑**（最自然承接已跑通的 cell-free expression）。M26/M27
为立即 research 优先（架构先行）。M29 第一台真机建议从 plate reader/简单 liquid handler 起，
不直接做双臂具身 lab。

## 3. expos 特定架构：可替换模型竞赛层（跨所有里程碑的核心模式）

expos 的优势**不是发明另一个 PLM/virtual cell**，而是建立**可替换、可验证、可被湿数据否决**
的模型竞赛层。统一形态：

```
Proposers / Scorers（提案与打分，只产 proposal 或 dry evidence，绝不改 claim）
  M25: deterministic mutation operators · ESM zero-shot · supervised fitness ·
       structure-aware scorer · agentic/generative proposer
  M27: mean/NN baseline · linear response · pathway-informed · foundation model ·
       flow/diffusion · ensemble
Acquisition（选择）
  exploitation · uncertainty · diversity · novelty · manufacturability/constraints ·
  multi-objective Pareto
Decision（裁决，走 expos 既有认证）
  performance · calibration · biological fidelity · OOD/abstention · experiment cost
```

**`BioModelBackend` 契约**（跨模态生物模型，如 MAMMAL 类；每个 backend 一律）：
sequence-only / sequence+structure / molecule+protein / perturbation+transcriptome /
multimodal hypothesis scoring。**每个 backend 必须**：① 版本与权重入 fingerprint；
② 有 baseline 对照；③ 报 calibration；④ 支持 abstention；⑤ **不得直接改 claim，只能产
proposal 或 dry evidence**。**foundation model 必须对简单 baseline 竞赛**（2026 有系统比较
发现多个深度/foundation model 未胜过刻意简单的 baseline——保持怀疑）。

## 4. 硬约束（用户明令，不可破）
- kernel / planner / evidence compiler / knowledge compiler **保持生物盲（域中立）**；
- 生物语义只活在 domain / provider / adapter / QC 层；
- **技术副本绝不冒充独立生物证据**（M24-B 塌缩件已立此机制）；
- **models propose；只有 trusted observation 能 certify**；
- 公开生物数据可训练/benchmark/校准，但**绝不可当作本 run 产生的观测**；
- foundation model 必须与简单 baseline 竞赛；
- 模型/数据集/权重版本一律入 provenance fingerprint；
- **无 discriminative expos 测试之前，绝不搬入大型框架**（no framework transplant）；
- **negative 与 insufficient 结果是一等结果**（如实报告）。

## 5. 验证级别分类（每个参照/每个 claim 必须标注）
`simulation` → `retrospective data` → `prospective wet lab` → `physical autonomous loop`。
当前 expos 生物侧真实级别：M24-B = **simulation（可信模拟 wet + 真 sequence dry proxy）**，
真机/真湿实验 = ❌ pending。对外一律诚实标注，绝不把 simulation 说成 wet-lab validated。

## 6. 施工策略：三条并行线（用户明令，勿十 agent 同改主仓）
- **Build 主线**：M25 生成式蛋白/酵素 loop——立即落碼（先 charter 后动碼，第一刀承接
  cell_free_expression + M24-B lineage 字段）。A 会话主建（sequence generators/acquisition/
  dry eval/expression/assay 在 A 域），B 配 kernel 中立 + mcl 编排 + qc 证据。
- **Clone/理解线**：多 agent 深读四组参照（protein design / genetic circuits / perturbation
  biology / wet execution）。每条**只产四种输出**：`ADOPT` / `ADAPT` / `NOT-COPY` /
  `EXPOS ABSTRACTION FINDING`，外加 source/code status + validation level。
  **纪律**：先 WebSearch/WebFetch **查证存在性**（多数 2026 引用超知识截止，可能含幻觉——
  查无实据诚实标注，绝不假读）；**绝不把外部 repo 搬进主仓**；若值得 clone 只进 `references/`
  （非 expos 根，且 references/ 已 gitignore）。
- **Architecture 主线**：本文件 = 权威 program 蓝图；每域施工前补 charter（生物物件类型/
  dry model/wet assay/observable/controls/biological replicate unit/claim 类型/acceptance
  faces/下一轮 decision/是否需真机/是否碰 kernel/安全·双重用途边界）。

## 6.1 安全与双重用途边界
生物设计涉及潜在双重用途。expos 生物 program 限于：安全的表达/活性/表型筛选与设计、
公开教学级生物元件、模拟与公开数据校准。**不做**：增强致病性/毒性/传播性的设计、
受管制病原体/毒素序列生成、规避生物安全审查的流程。任何真机/真湿执行须待明确生物安全
边界成文（M29 charter 强制项）。

## 7. 用户正式总令原文（可直接传两会话）
```
User ruling — expand expos aggressively into frontier biology as of 2026-07-14.
Biology is now the primary scientific program, not a one-domain demonstration.
Do not continue spending the majority of effort polishing the existing
cell_free_expression_screen. Preserve it as a regression and causal-closure anchor.

Launch a coordinated Biology Program: M25 Generative protein/enzyme design (fixed-pool
→ parent→variant generation; explicit sequence lineage; auditable mutation operators
first; PLM/structure-aware backends as optional frozen scorers; uncertainty/diversity/
novelty/manufacturability/multi-objective acquisition; close through expression+functional
assays; wet evidence must be able to overturn model ranking). M26 Genetic circuit &
programmable biology (SBOL-compatible typed graphs; generate+verify topology before
simulation; dynamic/time-series phenotypes; optimize under intrinsic noise+environmental
variation; kernel neutrality). M27 Perturbation biology & virtual cells (genetic/chemical/
cytokine interventions; compare simple baselines vs causal vs foundation models; select
informative perturbations; ingest Perturb-seq/single-cell/spatial; update causal claims).
M28 Autonomous biological discovery (literature-grounded hypothesis; assay selection;
RNA-seq/flow/imaging/single-cell analysis agents; contradiction+replication agents; every
conclusion in the claim ledger with machine provenance). M29 Protocol compilation &
embodied wet execution (ProtoPilot/compiler-verified protocols/BioProVLA; compile intent
into typed verifiable device-valid actions; reuse M23 physical transaction+sensed-state;
no physical biological autonomy claim until real hardware runs).

Parallel clone/reference wave over protein/antibody design agents, active-learning
biofoundries, genetic-circuit generation+verification, virtual-cell/perturbation models,
autonomous single-cell analysis, protocol-to-code+embodied agents. For every reference:
1 mechanism; 2 exact expos analogue; 3 ADOPT/ADAPT/NOT-COPY; 4 architecture finding;
5 source/code status; 6 validation level (simulation/retrospective/prospective wet/
physical autonomous).

Hard constraints: kernel/ledger/evidence-compiler/knowledge-compiler remain biology-
agnostic; biological semantics stay in domain/provider/adapter/QC; technical replicates
never masquerade as independent biological evidence; models propose, only trusted
observations certify; public data may train/benchmark/calibrate but not be presented as
current-run observations; foundation models must compete against simple baselines; model/
dataset/weight versions enter provenance fingerprints; no large framework transplant
before a discriminative expos test exists; report negative and insufficient outcomes as
first-class results.

Immediate build priority: M25 generative protein/enzyme loop.
Immediate research priority: M26 genetic circuits and M27 perturbation/virtual-cell.
Program objective: From biological sequences and interventions, generate designs, execute
or simulate assays, observe phenotypes and cellular states, certify biological claims, and
use the changed knowledge to create the next biological experiment.
```

## 8. 每周追踪（用户建议）
设每周扫描最新 autonomous biology / protein design / virtual cell / single-cell agent /
wet-lab automation 论文；**只在出现值得纳入 expos 的新机制时更新本文件**（附验证级别 +
ADOPT/ADAPT/NOT-COPY 判定）。避免 novelty-chasing：新论文≠采纳，须过 discriminative test 门。
