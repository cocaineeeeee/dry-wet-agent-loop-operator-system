# 论文骨架 —— expos（信任路由实验 OS）

> 2026-07-10 起草。素材来源：DEEP_REVIEW.md、M9_PROTOCOL.md、REFERENCE_MAP §9.3/§14/§15/§17/§17.1/§19.2、CHECKPOINTS.md（M5 关账数字）。
> 本文骨架服务于**新主张结构**（R2 裁定收编，见 §2 abstract / §3.6 定位；M9_PROTOCOL §0 可信性限定版）：expos 是结构化测量偏差下的**结论认证机**——头排是极显著的**可信性指标**（假最优拒斥 / 训练集污染防护 / 可审计归因，provenance-blind 鲁棒统计不能复现），regret 作场景依赖的解耦观测量**如实报**（多数结构档硬隔离 regret 劣于鲁棒统计，负结果 H1_REJECTED）；P3 Le Cam 两点法证 provenance-aware 为**必要**、非证硬隔离唯一。（旧稿"唯一一条可证伪主张=信任路由增量价值无法被鲁棒统计替代"按 regret 优越读已被 H1_REJECTED 证伪。）
> 状态标注：【已有数】= M5 关账已产实证；【待扫描】= 等 M9 全量网格。

---

## 1. 标题候选与目标 venue

**标题候选 ×3**（均含 "trust routing" / "operating layer"）：
1. *Trust Routing as a First-Class Operating Layer for Closed-Loop Materials Discovery under Structured Measurement Bias*
2. *When Robust Statistics Is Not Enough: An Operating Layer for Provenance-Driven Trust Routing in Self-Driving Labs*
3. *From Measurement Trust to an Experiment Operating Layer: Benchmarking Structured-Bias-Aware Closed-Loop Optimization*

**目标 venue（各一句适配理由）**：
- **Digital Discovery (RSC)**：自主实验方法学 + 诚实基准的天然主场，欢迎 negative/nuance 结论与 SDL 闭环对比，契合"三臂 + 诚实指标"叙事。
- **npj Computational Materials**：看重严谨模拟器 + 物理依据 + 可复现声明，材料结晶真值面与六注入器物理机制正对其口味。
- **NeurIPS Datasets & Benchmarks track**：六注入器基准 + 标定/评估分离 + 预注册协议是 D&B 标准形态，诚实失败指标（检出率-幅度曲线含失效点）是加分项。

---

## 2. Abstract（英文草稿，~150 词）

Closed-loop optimization in self-driving labs assumes its measurements are trustworthy—yet real instruments inject *structured* systematic bias (edge effects, thermal gradients, batch offsets, drift) that no public benchmark models. We present **expos**, an experiment operating layer that acts as a *conclusion certifier* (not a faster optimizer) by treating measurement trustworthiness as a first-class kernel service: a three-tier quality-control verdict, provenance-driven failure attribution, and failure-aware planning route each observation to a response model or a failure model. We contribute a domain-agnostic simulator with six physically grounded bias injectors and a pre-registered three-arm protocol (naive / robust-blind / os) that isolates trust routing from robust statistics. Across structured-bias scenarios, a naive loop chases a bias-induced false optimum (reported best above the true ceiling of 1.0), while expos rejects it at a quality-control tax of ≤2.5% on clean data. Our headline results are the credibility indicators—false-optimum rejection and training-set contamination protection are both highly significant (paired permutation p≈3.1e-5 and p≈1.9e-6, recomputed from B-set score files; provenance in `runs/full_sweep/report/headline_stats.json`)—whereas the regret premium is a scenario-dependent decoupled observable and, honestly reported as a *negative result*, hard isolation is worse than robust statistics on most structured archetypes and not significant against naive (p=0.0668, single-scenario S0.demo, stats_tests.csv). A Le Cam two-point argument shows that provenance-aware routing is a *necessary* mechanism for exploiting design-side identifiability information that provenance-blind robust aggregation cannot recover (it does not prove hard isolation is the unique such mechanism), with the regret premium governed by a soft/hard phase transition.

---

## 3. 逐节骨架

### 3.1 Introduction
- **钩子（实证优先）**：同一 seed（现行代码 B 集 **s1007**）下 naive 闭环第 3 轮把强边缘蒸发伪影追成"假最优"——报告 best=**1.064**（well F6 measured，真值仅 0.492），超过真值面物理上限 **1.0**【已有数】；os 臂同轮判 SUSPECT 隔离、best_trusted=**0.524**（true 0.475）物理合理，假最优被拒。一句话点题：闭环优化的盲点不是采集函数，是"信任测量"。（**个案口径统一以现行代码为准**：旧稿曾引 best=**1.007** / best_trusted=0.630@seed=7——那是**一期 M4 旧估计器**的历史值，已被现行代码 s1007 重跑值 1.064 取代，仅作历史注记。）
- 收窄**三条**主张（DEEP_REVIEW §1 + 第十一轮试点，REFERENCE_MAP §20）：① 结构化系统偏差注入的方法学空白（两次独立核查）；② provenance 驱动失败归因作为一等内核服务（**"一等服务"级封装为 V2 提案**：R2 降级"服务→布局"，无 lineage API、cause 级命中 15–22%，是能力非一等服务，见 ARCHITECTURE_V2_PROPOSAL）；③ **诚实负结果——QC 有幅度窗口**：温和伪影下硬隔离信任路由可*适得其反*（regret 反高于 naive），SDL 文献从未报告过此现象（§20.1），是新贡献点之三。
- 贡献清单：模拟器+六注入器基准、三臂预注册协议、四诚实指标、**弱幅度档诚实负结果 + 软信任预注册对照**、开源可复现。

### 3.2 Related Work（五个空白点，每点一段落位）
1. **闭环基准只做 iid 噪声**：Olympus 仅输出叠加随机分布、Nature Comm SDL metrics、MADE——差异句：*MADE 自认继承 "shared distributional biases" 并列为局限，expos 的可控注入正是其空白*（§17.1）。
2. **鲁棒 BO 不处理结构化空间偏差**：RCGP-UCB（无界腐败）、multi-stage/MSBO（过程噪声）、ORNL Dual-GP——差异句：*这些吸收孤立/对称腐败，但整组副本同向偏移时中位数/稳健损失无从下手*。
3. **失败作为一等对象与归因**：A-Lab/ARROWS3（合成失败与表征失败混在一条决策链、缺独立复核）、Anubis、ORGANA——差异句：*expos 把"测量不可信"与"参数不可行"分类路由，直接回应 A-Lab 被质疑之处*。
4. **可信度框架有 taxonomy 无内核机制**：GIFTERS 七维（63 篇中位 5/7）、APL ML data-fusion perspective——差异句：*expos 把 provenance-by-default + quality gates 落成运行机制而非评分表*。
5. **经典计量学未形式化进自主信任**：MSA/Gauge R&R（%GRR 三档）、Shewhart–Deming（assignable vs chance、tampering=QC 税先驱）——差异句：*SDL 文献谈可复现仅定性，无人把 MSA 验收纪律形式化进自主实验信任判定*（§15 空白#5）。
6. **凭证层信任 vs 逐观测连续信任路由**：LAP（arXiv:2606.03755）在仪器凭证/签名层做门禁——差异句：*LAP 判"哪台仪器可信"，expos 判"这一条观测多可信"并把连续 suspicion 分驱动 per-point 降权/路由*（REFERENCE_MAP §20.3）。
7. **硬排除 QC+AL 未做软硬对比**：arXiv:2603.29135（2026 同类 QC+主动学习）对可疑观测一律硬排除、未比较软降权——差异句：*该工作恰是我们弱幅度档"软 vs 硬"对照的缺失对照组，expos 的 os / os-soft 双臂正面补上*（§20.1）。
- Artificial Coater（失败涂层识别）作应用背书句、非竞品。

### 3.3 Method
- **内核公理**：两内核对象 + DecisionRecord 载荷 + RunStore；agent 仅建议权；truth sidecar 隔离（公理 6，跑内内核禁触）。
- **三级 QC**：TRUSTED/SUSPECT/FAILED 裁决 + SBB 校准嫌疑分 → per-point alpha；标定/评估阈值分离。
- **归因**：DoWhy 式反驳器合同，把 SUSPECT/FAILED 路由到失败模型（正交于 routing）；改判自动生效、按当前裁决全量重建。
- **失败感知规划**：复测/歧义消解/加对照动作，detour 生成上限防自激增殖。
- **图**：架构图（内核对象 + 双模型 + 建议权 agent）+ 信任路由状态机（裁决→路由→动作）。

### 3.3T 理论骨架（X4 理论包收编·R2）

> R2 独立理论核证结论（sha 见 STRESS_TEST_R2 §3.5）：四命题按新颖度分级——P3 真新（主定理）、P2 中等命题、P1/P4 重述当引理并给经典锚点。措辞纪律：路由是"利用设计侧辨识信息的**一种充分机制**"，**不写"唯一/必要"**。
> **P3 完整形式化 + 两点法证明 + 攻击点自白见 [THEORY_P3.md](THEORY_P3.md)**（本节只给收编摘要）。

- **主定理 = P3（聚合盲不可辨识，Le Cam 两点法）**：构造两个数据生成配置，它们对**任何 provenance-盲的聚合估计器**（中位数/Huber/稳健 GP 皆然）在分布上不可区分，却对应不同真最优；由 Le Cam 两点法，任何仅消费聚合观测的估计器在此对上的**极小极大风险精确 = s（下界+中点估计器可达=紧）**，且该地板对**任意自适应聚合盲策略**成立（非仅固定设计估计器，BO 应用所需强度）→ **provenance-aware 是必要的**。**边界申明**：本定理只证到 *provenance-aware 必要*，**不证硬路由（隔离）必要**——软降权（os-soft）同属 provenance-aware 家族，故它是"信任路由不可被稳健统计替代"这句的定理级内核，但不是"硬隔离唯一"的证据。经典锚点：Le Cam (1973)/Tsybakov《Introduction to Nonparametric Estimation》两点法；Huber-Ronchetti 稳健统计的 breakdown 视角作对照。
- **P2 中等命题（软硬相变 b²≈τ²）作 os-soft w(s) 的理论依据**：给出污染偏差 b 与噪声尺度 τ 的相变边界，其 tempered 形式 `w*(s) ∝ σ²/(σ² + n_S·b̂²)` 为 os-soft v2 的权重函数提供**推导出的形状**，替代 v1 的线性斜坡拍脑袋（与 ARCH_V2 §3 suspicion 校准契约互补）。这把 regret 保费从经验事实升为有解析边界的量。
- **P1 引理（污染预算解耦）**：路由把"污染预算"从响应模型解耦——重述为引理，经典锚点 = Huber (1964) ε-contamination 模型与 breakdown point。
- **P4 引理（截断正态 QC 税上界，sanity floor 非独力支点）**：硬隔离在干净数据上的假阳性代价有截断正态解析上界——重述为引理，经典锚点 = Johnson-Kotz 截断正态矩。**注意实测（PREM §A.3）此上界只覆盖保费的 ~5%（隔离通道），主导通道是采集畸变**，故 P4 降格为不依赖经验分布的 **sanity floor**；ARCH_V2 反问 4 的保费上限取 `X = max(P4 下垫, 校准集经验 Q0.90)`，由校准集数据钉死而非自由预注册。

### 3.4 Benchmark
- **六注入器 + 物理依据表**（详见 §4 图表清单表）：edge_evaporation（Deegan 边界层）、thermal_gradient（中心-边缘近线性热传导）、batch_shift（reproducibility 偏移）、instrument_drift（AR(1) 漂移）、glare（ε-contamination）、dust_nucleation（成核抑制丢失）。
- **标定/评估分离**：阈值在标定集 A（seed 0–9、偶数幅度档）锁定，评估集 B（seed 1000+、奇数档 + 未见组合）冻结不回标（DEEP_REVIEW §2-A）。
- **检出功效地板**（§14）：候选间真值差淹没伪影乘性偏移→靠"全板原始值"聚合零功效；只有哨兵/副本配对或独立曝光通道有功效——支撑"残差上做检验"纪律。

### 3.5 Experiments
- **三臂**：naive（全信）/ robust-blind（副本中位数+Huber，信任盲工程上限）/ os（完整信任路由）；仅在裁决策略与聚合策略两对象上不同（loop 主体零分支）。
- **三方稳健对照（结构性公平）**：五策略注入点让三种稳健化只差一个策略对象——**路由层稳健**（os，per-obs 信任裁决/隔离）vs **聚合层稳健**（robust-blind，副本中位数+Huber）vs **模型层稳健**（rcgp，RCGP Plateau-IMQ 软剪裁 GP；离群邻域误差 0.05 vs 朴素 GP 被离群拉飞 50.0）。三者对照公平性是结构性的（同 loop、同注入器、只换稳健对象），直接回应"robust BO/robust GP 已解决伪影"的质疑。
- **四诚实指标**：simple regret、错误最优命中率、污染样本利用率、QC 税。**污染度量双列报告（方法学纪律，§20.2）**：`injected_in_training`（注入标签，Huber ε-污染的文献标准口径，判是否注入）与 `contaminated_in_training`（`|bias|>3σ` 绝对偏差，判注入是否*有效*）两列并报——单列会误导。**R2 裁定收编（答问 4）——主口径改加权**：`contamination_weighted = Σw·1[contaminated]/Σw`，其中 naive/robust `w=1`、os 隔离 `w=0`、os-soft `w=alpha`（降权值）、rcgp `w=1/infl` 归一——这是唯一能跨五臂统一定义、且反映"降权机制生效程度"的口径（os-soft 把污染观测降到 0.1 就计 0.1 个），并顺带消解 rcgp 的 `training_contamination` 恒等于 naive 的循环论证（K-P3）；二值口径（降权>0 即计入模）保留为兼容列。**方法学脚注**：旧版用相对偏差对比绝对阈值（量纲错配）致零伪影场景污染率虚高 **0.72**，改绝对 3σ 后纯噪声误报 ≈0.3%——此 bug 本身佐证"评测协议也需对抗审查"。
- **核心已有实证——主角是可信性指标，不是 regret**【已有数·试点 18 格 + 全量】<!-- R2 裁定收编：换主角 -->（**数据代际＝Gen-1 full_sweep**；可信性主数字取自 S0.demo 干净域，**不在 batch 污染面**，故 Gen-1 有效；批次相关 regret 数字见下方带病标注）：**极显著的可信性主数字提头排**——假最优拒斥 os 0.20 vs naive 1.00（配对置换**精确 p≈3.1e-5**；MC-9999 触底 1e-4；重算溯源 `runs/full_sweep/report/headline_stats.json`）、训练集污染率 os 0.004 vs naive 0.146（配对置换**精确 p≈1.9e-6**，同产物）；QC 税闭环级 **0%**（141 观测）、板级 20 种子均 **2.5%** ≤5% 验收线。全量口径（**1450 格＝标定集 A 450 + 评估集 B 1000**；M9_PROTOCOL §2：报数只来自 B 集 1000 格，A 集 450 仅锁阈值不报数）。**regret 老实标注为不显著/场景依赖**：全量 os vs naive 主口径 regret 差 **p=0.0668（S0.demo 单场景口径，不达 α=0.05；stats_tests.csv）**，且在多数结构性场景 regret 劣于 robust（edge0.2 p=0.0029；**batch−0.18 p=0.0027 为 Gen-1 带病值，方向反，待 Gen-2 r1_resweep 重聚合冻结后引用**）、仅 S0.demo 显著占优（p=0.0014）。**排序纪律（R2）**：headline 首位必须是极显著的可信性指标，regret 作场景依赖的解耦观测量呈现，不得因排版惯性把不显著数字顶到头排。
- **检出率-幅度曲线**：横轴幅度档，纵轴跨轮累积检出率，标失效点（跌破 50%）与 demo 档；S3 留出伪影叠虚线。
- **归因精度-幅度曲线**（§19.2 / 附录 B）：低幅样本少而准、中幅最优、高幅（>50% 板面污染）歧义化——"检测饱和易、归因有幅度窗口"。
- **truth 标签与配对口径（R2 裁定收编·答问 3）**：truth sidecar 记**物理事实**（注入器实际改变了哪些孔的测量值），故 batch 全局效应 truth 标签取 **all-affected**（不迁就归因器）；评分分两层——**检出层按 all-affected 逐孔配对**，**归因质量层按 cause 级配对**（top_cause 判对该板主导伪影即计对，不苛求逐孔）。这样归因门槛不形同虚设，也不冤枉批次可辨识性弱的结构事实（P 路混淆矩阵：batch 真因 cause 级命中仅 22%、板级门 33%，如实进 limitation）。**前置**：`applied=|drift|>1e-9` 的全亮标签须先改 `|drift|>k·σ`，否则 drift 参与的任何口径被污染。
- **GIFTERS 七维自评表**：expos ≈6/7，多种子收敛一致性补 S 维冲 7/7。
- **弱幅度档诚实负结果**【已有数·试点 18 格】（新贡献点之三）：温和边缘蒸发 `strength=0.2` 档，硬隔离的 os 臂**末轮 regret 反高于 naive**，且**两臂都命中假最优**（QUARANTINE 把板缘观测整体挡出训练集→GP 失板缘覆盖→外插退化）；强档 `strength=0.5` 则 os 完胜（naive 三 seed 全命中假最优、os 全避开）。文献定标（§20.1）：SPC 的 ML-TAE 定量复现"软处理对小/中偏移零收益"、Deming 漏斗给出"弱伪影≈共因噪声、据此干预=tampering 放大方差"的经典解释；SDL 文献从无人报告"QC 在弱伪影下适得其反"——本论文首次诚实报告并定标。
- **软信任预注册对照实验（os-soft 臂，SOFT_TRUST_PROPOSAL.md）**【待 580 格全量·已接线待裁决】：QUARANTINE 观测以 `suspicion→alpha` 乘性膨胀降权复归训练集（路由枚举与三级裁决不动，软化只在聚合策略层）；理论化身 = Tempered Posteriors BO（arXiv:2601.07094，逐点自适应 α 降权不丢样本）。**预注册判据（跑前冻结）**：edge 弱档 `regret(os-soft) ≤ naive 且 ≤ os`（PASS-fix，修好弱档回归）；S0 强档 `≈os` 且错误最优不放回（PASS-strong）；S1 零伪影 QC 税差 ≤5%（PASS-tax）。诚实预期承接 §20.1：os-soft 弱档也可能不赢——这本身是可发表的校准结论。

### 3.6 Limitations（诚实节 = 卖点）
- **主张定位是升级不是降格（R2 裁定收编·答问 5）**：主张按"结构化伪影下的结论可信性保障（污染防护/假最优拒斥/可审计），regret 代价有相变条件（P2 命题 b²≈τ²）"定稿。三个支点：(a) P3 把"robust 统计不够"升为定理级（但只证 provenance-aware 必要，故措辞用"一种充分机制"）；(b) P2 给 regret 保费解析边界，与 ARCH_V2 §1 保险合同表述同构；(c) deviation 已按预注册纪律记账，即便 resweep 后 regret 改善也不翻案。这在 TMLR 是比"更优 BO"更强的定位。
- **QC 有幅度窗口——从 limitation 升格为 finding**【已有数·试点 18 格】：硬隔离信任路由的净收益存在幅度下界，弱伪影档下*适得其反*（见 §3.5 弱幅度档负结果）。这不再是单纯的能力边界，而是一条可发表、有 SPC/Deming 文献定标（§20.1）的新发现，并直接催生 os-soft 软信任对照（待 580 格全量裁决）。低幅端的补救属方法（软降权），不属回避。
- **饱和态归因**：>50% 板面污染时"干净多数"假设崩溃、竞争假设歧义化（检测率恒 1.0 但 top_cause 精度降）。
- **单轮 drift 不可辨**：小样本体制下单轮小幅（<10%）伪影抓不到，跨轮 EWMA/CUSUM 累积才抓得到；批次效应 vs 漂移单轮不可辨识（物理限制）。
- **模拟器工程近似清单**：CV-Nývlt 为定性依据、形式工程标定；AR(1) 每轮重置弱化 campaign 级漂移；几何混叠（批次×分层奇偶）是系统性误差源，已以棋盘格批次修复、列入 domain lint backlog。

### 3.7 可复现声明
- **seed 三元组**：`(np, artifact, layout)`，派生规则 `derive_seed`；标定/评估 base 分离（A∈[0,9]、B∈[1000,1019]）。
- **manifest**：`_grid_manifest.tsv`（三臂×场景×种子笛卡尔积）、run 确定性命名、每 task 独立子目录；报数来源 `report/_aggregate/metrics_long.parquet`。
- 预注册假设 H1–H4 跑前冻结、跑后只填数不改判据（M9_PROTOCOL §7）。

---

## 4. 图表清单（每图一句说明 + 数据来源）

| 图/表 | 说明 | 数据来源 |
|---|---|---|
| Fig.1 架构图 | 两内核对象 + 双模型 + 建议权 agent 的数据流 | ARCHITECTURE.md §4/§6 |
| Fig.2 信任路由状态机 | 裁决(TRUSTED/SUSPECT/FAILED)→路由→失败感知动作 | qc/policy.py、M9_PROTOCOL §1 |
| Fig.3 主图（三臂收敛） | best-so-far ±std band，主 demo 第 3 轮标"假最优" | report/<arm>/.../summary.json（§6.1）|
| Fig.4 检出率-幅度曲线 | 每注入器实线 + 留出伪影虚线，标失效点与 demo 档 | report/_aggregate/detection_curves.json |
| Fig.5 归因精度-幅度曲线 | 与 Fig.4 并排，示"信任路由两阶段能力边界" | 附录 B、M6 联合评测 |
| Fig.6 失败分流时间线 | A-Lab 式，每次 os 改判画为时间轴事件点 | trajectory.jsonl actions/qc_alerts |
| Fig.7 软硬信任交叉图（占位） | regret vs 伪影幅度：naive/os/os-soft 三线，标弱档 os>naive 的交叉点与强档 os 完胜区——软硬信任的幅度窗口 | 待 580 格全量（S2.edge×幅度 + S0 + S1，arms=naive,os,os-soft）|
| Tab.1 六注入器物理依据表 | 名/机制/参数/幅度网格/50% 检出边界 | REFERENCE_MAP §9.5/§14、M9_PROTOCOL §2 |
| Tab.2 M9 定位表 | 行=四篇+expos，列=偏差注入/信任路由/失败归因/可组合/理论保证 | REFERENCE_MAP §17.1 |
| Tab.3 GIFTERS 七维自评 | expos 逐维 done/gap，S 维靠多种子收敛补 | REFERENCE_MAP §17.1 |
| Tab.4 四诚实指标汇总 | 三臂 × 场景族 × 指标长表 | metrics_long.parquet |

---

## 5. 待补实验清单（按 M9 协议映射）

**已有数（M5 关账 + M9 试点 18 格，可直接入文；均为**试点**规模）**：
- naive 假最优 best=**1.064** vs os 拒斥 best_trusted=**0.524**（S0 主 demo、现行代码 s1007；旧 M4 值 1.007/0.630@seed=7 见 §3.1 钩子注，仅历史注记）；
- QC 税：闭环 0% / 板级 2.5%（S1 零伪影，20 种子）；
- 首组跨臂实证：os regret 0.035→0.013、naive 0.115 持平（eval 评分器）；
- **弱幅度档负结果（试点 18 格实测）**：edge `strength=0.2` os 末轮 regret 反高于 naive 且两臂命中假最优；`strength=0.5` os 完胜（naive 三 seed 全中假最优、os 全避开）。

**待全量扫描（M9 网格，2460 run / 41 配置 × 3 臂 × 20 种子；软硬信任子扫描标为**待 580 格全量**）**：
- **os-soft 软信任预注册扫描（待 580 格全量）**：S2.edge×5 幅度档 + S0.demo + S1.zero × arms=naive,os,os-soft × 评估集 B N=20，置换检验裁 PASS-fix/strong/tax（SOFT_TRUST_PROPOSAL §8）；
- **rcgp 模型层稳健对照臂**：五策略注入点补第三种稳健对象，与 os/robust-blind 同 loop 对照；
- S2 单伪影×幅度网格（6×5=30）→ 检出率-幅度曲线 + 失效点（Fig.4）；
- S3 留出伪影×幅度（5）→ H4 未见腐蚀兜底行为（Fig.4 虚线）；
- S4 组合场景（4）→ H1 结构性叠加 os vs robust-blind 置换检验 p<0.05；
- 归因精度-幅度曲线（Fig.5）→ 待 M6 归因引擎接线后联合端到端；
- AF/EF、几何 Precision/Recall（辅助指标）→ 全量聚合；
- GIFTERS S 维多种子收敛一致性（冲 7/7）→ N≥20 种子全量。

**前置依赖**：M6（归因+失败模型）/ M7（失败感知规划）/ M9（`expos.eval.run_cell` 新入口 + 评分器）尚 pending——全量指标须待其落地。
