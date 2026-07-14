# M26 参照波 · 组 2：基因迴路与可程式化细胞

> 参照 agent 产出（2026-07-14）。喂 **M26 基因迴路（research 优先 / 架构先行）** 架构输入。
> 权威蓝图：`docs/BIOLOGY_PROGRAM_2026.md`（§2 M26 行、§4 硬约束、§5 验证级别、§6 clone 纪律）。
> **诚实第一**：每条先 WebSearch/WebFetch 查证存在性；查无实据一律标 `UNVERIFIED / not found`，绝不编造机制。

## 0. 查证结果总账

| # | 参照 | 标识 | 状态 | 验证级别 | 公开 repo |
|---|---|---|---|---|---|
| 1 | GenCircuit-RL | arXiv **2605.14215v1**（Noah Flynn, 2026-05-14, cs.AI/cs.LG/q-bio.QM, CC BY-NC-ND） | **VERIFIED** | `simulation`（纯计算，结构/拓扑验证，无动力学、无湿实验） | **无公开 repo**（作者 GitHub nrflynn2 无此项目） |
| 2 | GenAI-Net | arXiv **2601.17582**（Maurice Filo, Nicolò Rossi, Zhou Fang, Mustafa Khammash — ETH Zürich D-BSSE, 2026-01） | **VERIFIED** | `simulation`（确定 + 随机 CRN 仿真评估） | 未见（搜索无 GitHub） |
| 3 | Sequential Design of Genetic Circuits Under Uncertainty With RL | arXiv **2605.06552**（Michal Kobiela, Diego A. Oyarzún, Michael U. Gutmann — Edinburgh, 2026-05-07, cs.LG） | **VERIFIED** | `simulation`（ODE + Markov jump 仿真） | 未见（搜索无 GitHub） |
| 4 | SBOL 生态工具（pySBOL3 / SBOL-utilities / SBOL3 规范） | SynBioDex org | **VERIFIED**（成熟公开标准，已 clone） | N/A（数据标准+工具，非 claim） | **已 clone**：`references/pySBOL3`, `references/SBOL-utilities` |

**统计：VERIFIED = 4，UNVERIFIED / not found = 0。** 无幻觉引用（三条 2026 前沿 arXiv ID 全部真实可取回；细节均取自 arXiv 摘要/HTML 原文，非记忆推断）。
**查无实据的诚实标注**：GenCircuit-RL 与另两篇均**无公开代码仓库**（唯一可 clone 的是 SBOL 官方生态工具，非上述三篇论文本体）。三篇均**仅 `simulation` 级**，无一做湿实验——引用其结论时不得升格为 wet-lab validated。

---

## 参照 1 — GenCircuit-RL（arXiv 2605.14215）**［本组最强 ADOPT］**

### 1. mechanism
把基因迴路设计当作**代码生成**问题：LLM（Qwen3-8B 为主，Llama-3.1-8B / Gemma-3-12B 交叉验证）产出 **pysbol3 Python 代码**构造 SBOL 迴路。核心是 **hierarchical verification reward（分层验证奖励）**，把"对不对"拆成**五级**，配**四阶段 curriculum** 把优化压力从"能生成代码"逐步推向"功能正确"。
- **五级验证（低→高）**：① **Execution** 代码能跑；② **Validity** SBOL 文档过合规检查；③ **Structure** 部件顺序/组成、拓扑与约束正确；④ **Semantics** 本体注释（SO/SBO 术语）正确；⑤ **Function** 任务特定拓扑分析（逻辑门用符号传播算真值表；toggle/oscillator 用 **motif detection 母题检测**）。
- **四阶段 curriculum**：Stage1 Execution（代码修复/补全）→ Stage2 Structure（部件替换、自然语言转码）→ Stage3 Reasoning（逻辑预测、迴路调试）→ Stage4 Design（de novo 设计）。
- **SynBio-Reason benchmark**：4,753 迴路，六种 canonical 类型（**expression cassettes / logic gates / feed-forward loops / toggle switches / oscillators / cascaded circuits**），九任务（T1 代码修复、T2 补全、T3 部件替换、T4 NL→代码、T5 逻辑预测、T6 迴路调试、T7 de novo；评估 T8 门分配优化、T9 级联调试，均基于 Cello）。
- **OOD 设计**：10 个正交 repressor–promoter 对分训练层（LacI/TetR/cI/PhlF/SrpR）与 held-out 层（BM3R1/AmtR/QacR/BetI/AmeR）；程序化生成只取训练层部件，Cello 真迴路 71 in-distribution + 40 OOD。测"是否把**抽象调控原理**迁移到新部件"。
- 结果：分层奖励在功能推理任务比二元奖励 **+14~16 个百分点**；curriculum 是强设计性能的**必要条件**；模型能重新发现文献中的 canonical 设计。

### 2. exact expos analogue
**五级验证 = expos "propose→（廉价确定性 verify 门）→dry→promote→wet" 中 dry 之前那道 verify 门的完整模板。** 对应现状：
- expos 已有 `SequenceProxyAdapter`（`expos/adapters/dry/sequence_adapter.py`）——**廉价、确定性、无 truth、不 mutate ExperimentObject** 的 dry proxy；GenCircuit-RL 的五级验证正是这种"廉价确定性 dry 面"的分层化。
- 候选身份：expos 现用 `construct_id` + `candidate.params`（M20 zero-kernel-change 契约，dry 输入搭 `params` 进来，kernel/planner/ledger 不动）。GenCircuit-RL 的 SBOL 图 = **topology-level 候选身份**（比 M24-B 的标量参数身份高一层）。
- benchmark 六类型里 **expression cassette** 直接对应已闭环的 `cell_free_expression_screen`（构造已带 promoter/rbs/cds）——是 M24-B → M26 的天然桥。

### 3. ADOPT / ADAPT / NOT-COPY
- **ADOPT（本组最强）**：**"verify-before-simulate" 的分层门**——把 propose 后、dry 仿真前插入**廉价确定性、可分级、可提供细粒度反馈**的验证阶梯（execution→validity→structure→semantics→function）。expos 落地为一组 **QC / verification faces**（每级一个 deterministic 判据），拒绝在拓扑非法/母题缺失的候选上浪费昂贵动力学仿真。**关键红线映射**：这些验证面是 dry proxy，**只能产 proposal 或 dry evidence，绝不 certify**（§4）。
- **ADAPT**：把"母题检测/真值表符号传播"作为 **function 级验证器**接进 expos，但**不搬 RL 训练回路**——expos 不训 LLM，只把"分层验证"用作**决策/acquisition 的证据分层**（哪级过、哪级卡）。curriculum 概念 ADAPT 成 expos 的"task 难度阶梯"（先 expression 后 toggle 后 oscillator）。
- **NOT-COPY**：① 不 copy 其 RL/curriculum 训练框架进主仓（无 discriminative expos 测试前禁框架移植，§4）；② 不把 SBOL 本体/pysbol3 语义泄进 kernel——SBOL 只活在 domain/adapter；③ 其"验证 = 拓扑/结构，不含定量动力学"是**已知短板**（作者自陈 Appendix G.5，见验证级别），expos **不能**把结构验证冒充为动态表型证据。

### 4. architecture finding
**M26 应把候选身份分成两层：`topology identity`（SBOL 图的 canonical hash）与 `parameter identity`（部件/动力学参数）。** 五级验证天然分居两层：execution/validity/structure/semantics/function-拓扑 属**topology 层**（廉价确定性 dry 门，先跑）；定量动态表型（振荡频率、开关概率）属**parameter/dynamics 层**（昂贵仿真 dry，后跑；且只有 trusted observation 能 certify）。这正是"generate→verify→simulate→sequential experiment"到 expos "propose→dry-verify→dry-simulate→promote→wet" 的映射。

### 5. source-code status
**无公开 repo。** 仅 arXiv preprint（v1）。作者 GitHub `nrflynn2` 仓库与本项目无关。**只能 WebFetch，不可 clone。**

### 6. validation level
**`simulation`（纯计算）。** 作者明言验证为"结构/拓扑，非动力学"："confirms correct parts, regulatory connections, and motif presence, but does not simulate quantitative behavior"。无任何湿实验。→ 引用时对外话术止于 "topology-level design generation validated in simulation ✅；dynamic/wet ❌ pending"。

---

## 参照 2 — GenAI-Net（arXiv 2601.17582）

### 1. mechanism
生成式 AI 框架，**从目标动态行为反推 chemical reaction network（CRN）**（逆向设计）。机制：**一个 agent 提议 reactions，耦合到由用户目标定义的 simulation-based 评估**。覆盖任务：dose response、复杂逻辑门、classifiers、oscillators、robust perfect adaptation（RPA），**确定性与随机（含降噪）双设定**。产出"拓扑多样的候选族 + 可复用 motif"。

### 2. exact expos analogue
**"agent 提议 + 仿真评估" = expos 的 proposer → dry 评估回路的教科书形态。** agent = expos proposer（产 topology proposal，绝不改 claim）；simulation-based evaluation = expos dry adapter（对**动态目标**打分）。用户目标（dose response / 振荡 / RPA）= expos 的**动态表型 observable**。RPA（robust perfect adaptation）对应 M26 特别关注的"**latent 环境漂移下的鲁棒设计**"。

### 3. ADOPT / ADAPT / NOT-COPY
- **ADOPT**：**动态目标规约 → 候选族**的 propose/dry 结构，且把 observable 从标量升为**时间序列/剂量响应曲线/适应误差**。确认 M26 必须支持 time-series 观测物件。
- **ADAPT**：把"objective 定义仿真评估"接成 expos 的 dry scorer，但**强制 §4 边界**——GenAI-Net 的 simulation 评估在 expos 里**只是 dry proposer/scorer，绝不 certify**；只有 trusted（sim-wet / 真湿）observation 能认证动态表型 claim。其"确定性 + 随机双设定"ADAPT 成 expos 的"dry 确定 proxy vs wet 随机 truth"分工（与 `sequence_adapter` 的 dry-无噪/wet-持噪分层一致）。
- **NOT-COPY**：不把其 agent/CRN 生成器当独立证据源搬入；CRN/动力学求解器留 domain 层，不进 kernel。

### 4. architecture finding
**observable 需从"标量 RawResult"泛化为"动态 trace / 曲线族"，但 kernel 仍只见 `value + secondary`。** 现状 `sequence_adapter` 已把 `expression_proxy` 塞 `RawResult.value`、四特征塞 `secondary`，"discriminator/QC/ledger 保持 bit-for-bit 域中立"。M26 的时间序列可沿同一策略：**动态表型摘要**（振荡频率、开关概率、适应误差、剂量-EC50）落 `value/secondary`，原始 trace 进 content-store（域层），kernel 永不见"振荡/CRN"字面——保持 §4 中立。

### 5. source-code status
未见公开 repo（搜索无 GitHub）。**只能 WebFetch。**

### 6. validation level
**`simulation`。** 确定性 + 随机 CRN 仿真评估，无湿实验。

---

## 参照 3 — Sequential Design of Genetic Circuits Under Uncertainty With RL（arXiv 2605.06552）**［最强 ADAPT］**

### 1. mechanism
针对**双重不确定性**的**闭环序贯设计**：① 生化反应的 **intrinsic stochasticity（内在随机性 / aleatoric）**；② 跨实验室/实验环境的 **parameter/environmental variability（epistemic）**。用 **RL policy**，仿真器为 ODE 或 **Markov jump process**。核心创新：**amortized 方法**——预先跨"可能的不确定参数分布"训练，避免每轮实验后昂贵的推断/优化，实现**即时、基于观测的适应**（无需每轮显式参数估计）。案例：**heterologous gene expression** 与 **repressilator**（振荡器）。

### 2. exact expos analogue
**这是 expos 序贯实验闭环（propose→test→learn→redesign）本身的直接对应**，且补上两件 M26 硬需求：**intrinsic noise** 与 **latent 环境漂移**。amortized "跨参数分布训练" = expos 的 **robustness acquisition face**（跨环境上下文的鲁棒目标）。**最关键映射**：其把 **intrinsic noise（aleatoric，不可约——即生物复本单元）** 与 **parameter uncertainty（epistemic，可由实验缩减）** 明确二分，正对应 §4 铁律"**technical replicate 绝不冒充独立生物证据**"——intrinsic noise 属 within-replicate，环境/参数变动才产生独立生物证据。

### 3. ADOPT / ADAPT / NOT-COPY
- **ADAPT（本组最强）**：把 **aleatoric（intrinsic 噪声，属技术/复本变异）vs epistemic（环境/参数不确定，属独立生物变异）的二分**接进 expos 的 replicate 语义与 acquisition。robustness 目标（跨潜在环境分布的期望性能）ADAPT 成 M26 的 **context-dependent claim** 与"latent 环境漂移下鲁棒设计"acceptance face。
- **ADOPT**：**sequential closed-loop under uncertainty** 的问题框架（epistemic+aleatoric 同场）直接确认 M26 的 sequential experiment 语义。
- **NOT-COPY**：不搬 amortized RL policy 训练器进主仓（§4 禁框架移植）；其 RL 只当"acquisition 可以 uncertainty-aware"的动机，不当实现。

### 4. architecture finding
**M26 的复本/证据模型必须显式区分两类方差源。** intrinsic noise（Markov-jump 层）= 同一 topology+parameter+context 的**多次 trusted 观测之间的技术变异**，不构成独立生物证据；跨 **context（环境/资源）** 的变动才是独立生物证据轴。这把 §4 "技术副本≠独立生物证据"从蛋白域延伸到迴路域，并要求 **context 成为候选身份/claim 的一等维度**（context-dependent claim）。**sim-to-experiment discrepancy** 亦源于此：dry 仿真取某参数点，wet 落在环境分布另一点——差分门须归因到 epistemic 而非误判为设计失败。

### 5. source-code status
未见公开 repo。**只能 WebFetch。**

### 6. validation level
**`simulation`。** ODE + Markov jump 仿真，两个案例（heterologous expression、repressilator）均计算，无湿实验。

---

## 参照 4 — SBOL 生态工具（pySBOL3 / SBOL-utilities / SBOL3 规范）**［已 clone］**

### 1. mechanism
**SBOL（Synthetic Biology Open Language）v3** 是社区数据标准，用**本体支撑、机器可解析、Semantic-Web 知识图**表示生物设计。核心 typed 对象（已在 `references/pySBOL3/sbol3/` 核验）：`Component` / `SubComponent` / `Feature` / `Interaction` / `Participation` / `Constraint` / `Location` / `Sequence` / `CombinatorialDerivation` / `Provenance`——即一张**typed construct graph**。SBOL-utilities（`references/SBOL-utilities/sbol_utilities/`）提供：`sbol_diff.py`（两 SBOL 文档差分）、`calculate_complexity_scores.py`（合成复杂度/可制造性评分）、`expand_combinatorial_derivations.py`（组合设计空间展开）、`graph_sbol.py`（可视化）、`calculate_sequences.py`。SBOL 3.1.0 含完整 validation rules。

### 2. exact expos analogue
- **typed graph = M26 的 topology 候选身份对象**（confined 到 domain/adapter，正如 `cell_free` 把 SBOL-free 的 construct dict 限在 `adapters/dry/constructs.py`）。
- **`sbol_diff.py` = expos 的"两 run 首个分岔点/候选拓扑差分"**（拓扑级 candidate identity 比较、provenance 溯源）。
- **`calculate_complexity_scores.py` = M25/M26 acquisition 的 manufacturability face**（可合成性约束）。
- **`expand_combinatorial_derivations.py` = 候选池生成**（组合枚举 = proposer 的一种确定性算子，类比 M25 的 auditable mutation operators）。
- **SBOL validation rules = GenCircuit-RL 五级中的 Validity/Structure/Semantics 三级现成实现**。

### 3. ADOPT / ADAPT / NOT-COPY
- **ADOPT**：把 SBOL typed-graph **数据模型**（Component/SubComponent/Interaction/Constraint）作为 M26 domain 层内部拓扑表示；把 SBOL validation + `sbol_diff` 作为 verify 门（execution/validity/structure/semantics 级）现成引擎；`complexity_scores` 作 manufacturability 证据。
- **ADAPT**：`expand_combinatorial_derivations` ADAPT 成确定性、可审计的候选枚举算子（承接 M25 lineage 纪律：显式算子先于生成模型）。
- **NOT-COPY**：**绝不把 RDF/Semantic-Web/pysbol3 依赖搬进 kernel/planner/ledger**——SBOL 只活在 `expos/adapters/dry/*` 与 domain provider；kernel 只见 `input_kind='circuit_topology'`（新 ComputeTarget，类比现有 `INPUT_KIND_SEQUENCE_CONSTRUCT`）+ 域中立 `value/secondary`。不把整个 SynBioHub/仓库栈引入。

### 4. architecture finding
**M26 新增一个 `input_kind`（如 `circuit_topology` / `sbol_construct`）ComputeTarget（Contract v3+），拓扑对象搭 `candidate.params` 进来，新的 `SBOLTopologyAdapter` 读它——kernel/planner/evidence-compiler/claim-ledger 一字不改（复刻 M20/M24 的 zero-kernel-change 换域证明）。** 拓扑 canonical hash 折入 candidate fingerprint（延用 `config_fingerprint` 的 additive-fold 机制，`expos/domain.py:594`），使"同拓扑不同部件"（GenCircuit-RL 的 OOD 轴）与"同部件不同拓扑"在身份层可区分。

### 5. source-code status
**公开、成熟、已 clone**：`references/pySBOL3`（clone URL `github.com/SynBioDex/pySBOL3`）、`references/SBOL-utilities`（`github.com/SynBioDex/SBOL-utilities`）。均 `--depth 1`，进 `references/`（已 gitignore），**未进主仓、未 commit**。

### 6. validation level
N/A（数据标准/工具，非科学 claim）。工具本身是**确定性 dry 基础设施**——落 expos 后其产物为 dry evidence，绝不 certify。

---

## M26 架构综合（喂蓝图）

### A. topology 先生成+验证再模拟 → propose→dry→promote→wet 映射
```
GenAI-Net/GenCircuit-RL                    expos M26
  generate topology (agent/LLM)     →   proposer 产 SBOL topology proposal（不改 claim）
  verify (5-level, cheap, det.)      →   dry-verify 门：QC/verification faces
                                          （execution/validity/structure/semantics/function
                                           母题检测）——SBOL validation + sbol_diff 现成引擎
  simulate (dynamic objective)       →   dry-simulate：动态表型 proxy（振荡频率/开关概率/
                                           剂量响应/适应误差），确定性、无 truth
  sequential experiment under noise  →   promote→wet：trusted observation 认证；
                                           intrinsic noise=技术复本，环境 context=独立生物证据
```
**核心 finding**：五级验证的前四级（拓扑/结构/语义）是**廉价确定性 dry 门**，应在昂贵动态仿真前拦截非法候选；动态表型仿真是**第二层 dry**；两者都只产 dry evidence，**只有 wet/sim-wet 能 certify**（§4）。这把"generate→verify→simulate→experiment"干净落进 expos 既有 propose→dry→promote→wet，无需碰 kernel。

### B. M26 第一个最小 task 建议（research 优先 / 架构先行 / 不碰复杂 mammalian）
**分两步走，架构先行：**

1. **Walking skeleton（第一刀）= promoter–RBS expression tuning（稳态标量）。**
   **依据**：GenCircuit-RL 的六 canonical 类型第一类即 **expression cassette**；且**直接复用已闭环的 `cell_free_expression_screen`**（构造已带 promoter/rbs/cds，`adapters/dry/constructs.py`）。此 task **只需稳态标量 observable**，不引入 time-series 机器，却能第一次落地：`circuit_topology` typed-graph 候选身份 + SBOL verify 门 + 拓扑 fingerprint。**风险最低、承接最自然、验证 kernel 中立换域**。

2. **第一个真正 dynamic task（第一个 M26 里程碑 assay）= two-node toggle switch（开关概率，内在噪声）。**
   **依据**：GenCircuit-RL 明列 **toggle switch** 为 canonical 类型且其 function 级验证用 **motif detection**（可直接当 verify 门第五级）；Sequential-design 论文（2605.06552）提供 **intrinsic noise + Markov-jump 的开关/双稳随机语义**与序贯闭环；GenAI-Net 覆盖双稳逻辑。toggle 首次逼出**动态表型（bistability / switch probability）+ topology-dependent 身份 + intrinsic-noise 复本语义**，但**远比 mammalian circuit 简单**，符合"不碰复杂"约束。

3. **（后续）oscillator frequency tuning** — 最富（time-series、振荡频率），GenAI-Net（oscillator task）+ Sequential-design（repressilator 案例）双撑，但仿真机器最重，**延后为 task 3**。

> 一句话：**先用 expression-tuning 建拓扑对象 + verify 门的骨架，再以 toggle switch 作第一个动态表型里程碑。**

### C. kernel 中立怎么保（§4）
- 拓扑/时间序列**只活在 domain/provider/adapter/QC 层**：新 `input_kind='circuit_topology'` ComputeTarget（类比 `INPUT_KIND_SEQUENCE_CONSTRUCT`）、新 `SBOLTopologyAdapter`（类比 `SequenceProxyAdapter`）、动态表型摘要塞 `RawResult.value/secondary`，原始 trace 进 content-store。
- kernel/planner/evidence-compiler/claim-ledger **永不见** "SBOL/toggle/振荡/repressor" 字面（现状 `cell_free` provider 已证此边界："kernel/planner never see a construct/promoter/rbs literal"）。
- 拓扑 canonical hash 折入 candidate fingerprint（延用 `domain.py` additive-fold），provider/模型/权重版本入 provenance fingerprint。
- **§4 硬门复核**：models（含 GenCircuit-RL/GenAI-Net 类生成器与仿真评估）**只 propose 或产 dry evidence，绝不 certify**；技术复本（intrinsic noise 多次读数）≠ 独立生物证据（后者须跨 context）；公开数据/权重可校准 benchmark，绝不当本 run 观测；foundation model 须与简单 baseline 竞赛；**无 discriminative expos 测试前禁搬 SBOL/RL 框架进主仓**；negative/insufficient（如 verify 门全过但动态表型不达标）为一等结果。

### D. 关键 architecture finding（一句浓缩）
**M26 = 在 candidate identity 上加一层 `topology`（SBOL typed graph，canonical-hash 身份），在 observable 上加一层 `dynamics`（时间序列摘要），并在 propose 与 dry-simulate 之间插入一道分层、廉价、确定性的 `verify` 门——三者全部锁在 domain/adapter/QC 层，kernel 一字不改，复刻已两次成立的 zero-kernel-change 换域证明。**

---

## 诚实标注（红线复核）
- **无幻觉**：三条 2026 前沿 arXiv（2605.14215 / 2601.17582 / 2605.06552）**全部真实取回**，作者/机构/摘要/技术细节均取自 arXiv 原文，非记忆编造。
- **查无实据的项**：三篇论文**均无公开代码仓库**（诚实标 not found）；唯一 clone 的是 SBOL 官方生态工具（pySBOL3 / SBOL-utilities），**非论文本体**。
- **验证级别诚实**：三篇**全为 `simulation` 级**，无一湿实验；GenCircuit-RL 更仅"结构/拓扑验证、非定量动力学"（作者自陈短板）。对外话术止于 "topology/dynamic circuit design validated in simulation ✅；prospective wet / physical ❌ pending"。
