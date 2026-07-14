# Bio Refs · 组 3：扰动生物学与虚拟细胞（M27 模型竞赛层输入）

> 参照波 agent · 组 3。任务：为 **M27（扰动生物学与虚拟细胞，research 优先/架构先行）**
> 产**模型竞赛层**架构输入。权威蓝图 = `docs/BIOLOGY_PROGRAM_2026.md`（§3 模型竞赛层、
> §4 硬约束、§5 验证级别、§6 clone 纪律）。
>
> **诚实第一。** 本组最容易被过度吹捧，全程保持怀疑。每条引用先 WebSearch/WebFetch 查证
> 存在性；查无实据 → `UNVERIFIED / not found`，绝不假读。arxiv 2602/2603/2604/2605/2606
> 编号超本 agent 2026-01 知识截止 → 逐条实网核验后才写。
>
> **M27 的定位（用户明令）**：expos 的优势**不是发明另一个 virtual cell**，而是
> "**让多个 virtual-cell 模型提出可被真实扰动资料否决的预测，把否决写回知识与下一轮选择**"。
> M27 必须内建模型竞赛：`mean/NN baseline · linear response · pathway-informed baseline ·
> foundation model · flow/diffusion · ensemble`；裁决 = `performance · calibration ·
> biological fidelity · OOD/abstention · experiment cost`。

---

## 0. 查证结果总表（诚实红线）

| 参照 | 查证状态 | 出处（核验后） | 代码 |
|---|---|---|---|
| **"foundation 未胜 baseline"（Nature Methods 2026, s41592-025-02772-6）** | ✅ **VERIFIED — 真实，非幻觉。M27 关键依据成立** | Ahlmann-Eltze, Huber, Anders. *Nature Methods* **22**, 1657–1661 (Aug 2025). PMID 40759747 / PMC12328236 / DOI 10.1038/s41592-025-02772-6 | ✅ cloned `references/linear_perturbation_prediction-Paper` (Zenodo 14832393) |
| **CellVoyager**（autonomous scRNA-seq agent） | ✅ VERIFIED | Alber, Chen, Sun, Isakova, Wilk, Zou. *Nature Methods* **23**, 749–759 (2026); biorxiv 2025.06.03.657517; DOI 10.1038/s41592-026-03029-6 | ✅ cloned `references/CellVoyager` (github zou-group/CellVoyager) |
| **Spatial Perturb-seq**（组织空间结构内单细胞功能基因体学） | ✅ VERIFIED | *Nature Communications* **17** (2026), s41467-026-69677-6; biorxiv 2024.12.19.628843 | WebFetch only（湿实验方法，无模型 repo 需 clone） |
| **SCALE**（virtual cell perturbation, arxiv 2603.17380） | ✅ VERIFIED（arxiv ID 超截止，实网确认真实） | Chen, Yu 等；corr. Zhangyang Gao（Shanghai AI Lab / pjlab）；arxiv 2603.17380 (2026-03-18)；biorxiv 2026.03.17.712536 | WebFetch only（未见公开权重 repo；BioNeMo 框架，**不搬**） |
| **Lingshu-Cell**（generative cellular world model, arxiv 2603.25240） | ✅ VERIFIED | Alibaba DAMO Academy；arxiv 2603.25240；homepage alibaba-damo-academy.github.io/lingshu-cell-homepage | WebFetch only（大模型，**不搬**） |
| **PerturbDiff**（functional diffusion, arxiv 2602.19685） | ✅ VERIFIED | arxiv 2602.19685 (2026-02)；project page katarinayuan.github.io/PerturbDiff-ProjectPage | WebFetch only |
| **action-conditioned perturbation prediction**（概念族） | ✅ VERIFIED（作为一族已发表方法：PerturbNet / CODEX / CRADLE-VAE / CINEMA-OT） | PerturbNet PMC12322087；CRADLE-VAE arxiv 2409.05484；系统比较 biorxiv 2024.12.23.630036 | 概念族，未单独 clone |

**关键裁定**：那条作为 M27 模型竞赛层核心依据的"foundation 未胜 baseline"比较——**是真实的、非幻觉的**，
可安全作为 M27 架构基石。它甚至比总令描述更锋利：**没有一个深度/foundation 模型稳定胜过
"mean 预测"或一条 ridge 线性模型**（详见 §1）。

**未见实据 / 需保留**：无一条本组核验为幻觉。但 arxiv 2602–2606 一批（SCALE / Lingshu-Cell /
PerturbDiff / OCOO-T / CellxPert / Chem-PerturBridge 等）**均超本 agent 知识截止**，其性能宣称
（"SOTA""超越 STATE"）**只有作者自报的 benchmark 数字**，未经第三方独立复核——按 §4/§8 应当作
"候选 proposer"而非"已验证优胜者"对待。这正是 M27 竞赛层存在的理由。

---

## 1. ★核心参照：Nature Methods 2026 —— "深度学习基因扰动预测尚未胜过简单线性 baseline"

**这是 M27 模型竞赛层最重要的单条依据，故置于全组之首、独立详述。**

### 1.1 mechanism（机制）
一项**对抗性 benchmark**（不是又一个模型），把 7 个为扰动预测设计/改用的深度模型 pk 4 条
**刻意做简单**的 baseline，任务=预测单/双基因扰动后的转录组变化。
- **被测深度/foundation 模型**：scGPT、scFoundation、GEARS、CPA（专为扰动设计）+ scBERT、
  Geneformer、UCE（foundation 改用）。
- **刻意简单的 baseline**：
  1. **"no-change"**：预测 = control（完全不变）；
  2. **"mean"**（单扰动）：预测 = 训练集所有扰动的平均反应；
  3. **"additive"**（双扰动）：`ŷ_AB = y_A + y_B − y_∅`（两单扰动 log-FC 相加）；
  4. **linear model**：`K×L` 矩阵经 gene embedding 与 perturbation embedding、用 **ridge 回归**
     求解（repo 中 `solve_y_axb`，见下）。
- **指标**：1000 高表达基因上的 **L2 距离**、**Pearson delta**（先减 control 再算相关）、
  TPR–FDP 曲线、PR/ROC AUC。
- **数据**：Norman（K562，100 单 +124 双）、Replogle（K562/RPE1）、Adamson（K562）。
- **结论（逐字精神）**："**None of the deep learning models was able to consistently
  outperform the mean prediction or the linear model.**" 双扰动上所有模型误差**显著高于**
  additive baseline。深度模型吃掉大量算力却零性能优势。
- **作者自陈 caveat**：仅 4 个数据集、全为癌系（高突变负荷）——**负面结论本身也须限定范围**，
  这恰恰是"负面/不足结果是一等结果、但要如实标 scope"的范本。

### 1.2 exact expos analogue（对应物）
这**就是 M27 模型竞赛层 + 裁决层的现成范本**：
- baseline 集（mean / additive / linear-ridge）↔ expos `Proposers/Scorers` 里必须常驻的
  **廉价参照线**；
- "foundation 必须对 baseline 竞赛"↔ §4 硬约束逐字命中；
- Pearson-delta / L2 / DE-overlap ↔ expos 裁决层 `performance` face 的具体度量；
- "全癌系" caveat ↔ expos claim 的 **context 边界**（context-dependent claim，M26/M27 共有）。

### 1.3 ADOPT / ADAPT / NOT-COPY
- **ADOPT（最强）**：把这 4 条 baseline 定为 M27 **强制常驻参照线**——任何 foundation/flow/
  diffusion proposer **必须在同一 held-out split 上先赢过 mean 与 linear-ridge，才有资格进入
  acquisition**。"赢不过 baseline"是一等结果，如实入 ledger（不是失败，是知识）。repo 的
  `solve_y_axb`（gene-emb A × pert-emb B 双侧 ridge 分解）可**直接作为 expos linear-response
  scorer 的参考实现**（`references/linear_perturbation_prediction-Paper/benchmark/src/
  run_linear_pretrained_model.R`）。
- **ADAPT**：其评测在"平均反应/群体分布"层；expos 还要加 **calibration + OOD/abstention +
  experiment-cost** 三个裁决 face（原文只做 performance）。把 Pearson-delta/L2 落为 expos
  evidence 度量，但**裁决权归 certification 层**（模型只 propose）。
- **NOT-COPY**：不搬其 R/renv + 自研 workflow manager 整套（§4 no-framework-transplant）；
  只取 baseline 数学与 benchmark 协议。不把它的公开 Perturb-seq 当作本 run 观测（§4）——
  它是 **benchmark/校准语料**，不是当轮 trusted observation。

### 1.4 architecture finding（★M27 竞赛层关键）
> **"跑赢 baseline"必须是 expos 竞赛层的一道硬门（gate），而不是排行榜上的一行。**
> M27 的价值不在托管更多 virtual cell，而在于：**在每个 held-out split 上，先让 mean/linear
> baseline 定出"免费预测"的水位线，任何昂贵 proposer（foundation/flow/diffusion）必须
> 显著且校准地越过该水位，否则其 proposal 不进入 acquisition，且"未越线"作为一等 negative
> claim 写回知识。** 这把一条已发表的怀疑证据变成 expos 的**结构性防吹嘘机制**。

### 1.5 source-code status
✅ 公开、已 clone：`references/linear_perturbation_prediction-Paper`（Zenodo 14832393）。含
`benchmark/src/run_mean_prediction.R`、`run_additive_model.py`、`run_linear_pretrained_model.R`
（baseline）+ `run_cpa.py`/`run_gears.py`/`run_geneformer.py`（DL 对照）+ 各 conda env。
baseline 实现极轻（一条 ridge），适合直接读取移植数学。

### 1.6 validation level
**`retrospective data`**（对已公开 Perturb-seq 的回顾性 benchmark meta-analysis；无自产湿实验）。
对 expos：可用于 benchmark/校准，**绝不冒充当轮 wet 观测**。

---

## 2. CellVoyager —— autonomous scRNA-seq 分析 agent

### 2.1 mechanism
LLM 驱动的 CompBio agent：吃进已处理的 scRNA-seq（.h5ad AnnData）+ 数据集背景 + 已做过的分析
记录，**自主生成并在 Jupyter notebook 内执行**新分析管线，在既有工作上迭代提出并检验假说。
架构（`cellvoyager/agent.py`）：`AnalysisAgentV2` + `HypothesisGenerator.generate_idea` +
notebook 执行器（`execution/notebook_tools.py`、`claude.py`）+ `run` / `run_resume`（可续跑）。
评测 CellBench（76 篇已发表 scRNA-seq 研究）上比 GPT-4o/o3-mini 预测"作者最终会做哪些分析"高
最多 23%；三个 case study（COVID-19 / 细胞通讯 / 衰老）产出被专家评为有创意且科学合理。

### 2.2 exact expos analogue
= M28 的 Omics/Assay analysis agent 的原型，但对 M27 的价值在于**"分析动作"的可审计编排**：
CellVoyager 的 hypothesis→execute→observe→build-on-prior 循环 ↔ expos
Hypothesize→Design→Build→Test→Analyse。其 `run_resume` ↔ expos 的 sensed-state/resume 事务面
（M23 复用）。notebook = 可审计 dry evidence 载体。

### 2.3 ADOPT / ADAPT / NOT-COPY
- **ADOPT**：把"背景 + 已做分析记录"作为 agent 输入的做法——即**显式喂入既往证据以避免重复、
  在既有 claim 上增量**——直接对应 expos knowledge → 下一轮 decision 的闭环。notebook-as-
  evidence（每步可回放）契合 expos 证据不可变 + 写严读容。
- **ADAPT**：CellVoyager 自评"创意/合理"由 LLM-judge + 专家打分；expos 必须把这类**由分析
  产生的东西严格降级为 hypothesis/proposal**——LLM agent **只 propose，不得 certify**
  （§4 硬约束）。任何"新发现"须走 trusted observation 才升为 claim。
- **NOT-COPY**：不搬其 streamlit GUI / 整框架；expos 只需要"可审计的分析动作编排"这一抽象。
  其 case study 结论是 in-silico + 专家意见，**不是湿验证**，不可当独立生物证据。

### 2.4 architecture finding
> **"分析"本身是一个需 provenance 的动作，而非白盒。** agent 自主产出的每一步分析都要落
> notebook + fingerprint（代码版本、输入数据 hash、prior-analysis 记录），且**其产物默认是
> proposal/dry evidence，认证权始终在 certification 层**。这为 M27/M28 的"agent 参与但不越权"
> 划出清晰的分层线。

### 2.5 source-code status
✅ 公开、已 clone：`references/CellVoyager`（github zou-group/CellVoyager）。含 `cellvoyager/`
核心、`CellBench/` 评测、`gui/`、`example/`。依赖 OPENAI/ANTHROPIC API key。

### 2.6 validation level
**`retrospective data` / in-silico**（分析既有公开数据；case study 由专家评估，无新湿验证）。

---

## 3. Spatial Perturb-seq —— 保留组织空间结构的单细胞功能基因体学

### 3.1 mechanism
in vivo CRISPR 技术：在**完整组织**内的单细胞里同时干扰多个基因，兼容 sequencing-based 与
probe-based 空间平台。原位/在体功能筛多基因，绕过会扭曲细胞类型比例的解离步骤，能同时抓
**细胞自主（intracellular）与细胞间（intercellular/微环境）效应**。应用：小鼠脑内敲除神经退行
风险基因，揭示 Lrp1 信号与 ephrin-Eph 受体互作等空间完整组织中的通讯通路候选基因。

### 3.2 exact expos analogue
= M27 观测层里**保留空间上下文**的扰动观测模态：expos 的 observable 需容纳
"扰动 → (细胞自主效应, 细胞间/空间效应)" 的**双层 response**，而非只有解离后的表达向量。
对应 M27 charter 的 `context`（空间近邻）维度与 context-dependent claim。

### 3.3 ADOPT / ADAPT / NOT-COPY
- **ADOPT**：把**空间/微环境上下文**列为 M27 observable 的一等维度——扰动效应不是单细胞标量，
  而是 (cell-autonomous, cell–cell) 分解。这直接支撑"biological fidelity"裁决 face：
  一个只学到平均表达位移、却预测错空间/细胞间效应的模型，fidelity 应被判低。
- **ADAPT**：作为**真湿实验方法**，它是 M27 的"trusted observation 生成器"典范（相对于模型
  proposal）；但真机执行须待 M29 生物安全边界成文（§6.1），当前只作观测语义与 charter 输入。
- **NOT-COPY**：无模型 repo 可搬；这是湿方法，不是软件。技术副本（同组织多切片）**绝不冒充
  独立生物 replicate**（§4）。

### 3.4 architecture finding
> **response 不是一个向量，而是一个带空间/层级结构的对象。** M27 的 observable schema 必须
> 能表达"细胞自主 vs 细胞间/微环境"两层效应，否则 biological-fidelity 裁决无从落地——
> 一个模型可以在"平均表达"上赢却在"空间通讯效应"上全错。这为竞赛层的 fidelity face 提供了
> 可否决的具体内容。

### 3.5 source-code status
WebFetch only（湿实验方法；biorxiv 2024.12.19.628843 / Nat Commun 2026）。无需/无软件 repo。

### 3.6 validation level
**`prospective wet lab`**（真实在体小鼠脑 CRISPR 实验）。注意：这是**它自己的**验证级别；
对 expos 而言它提供的是"trusted observation 应长什么样"的语义，expos 自身生物侧仍 = simulation。

---

## 4. 虚拟细胞模型三条（SCALE / Lingshu-Cell / PerturbDiff）—— 竞赛层的"昂贵 proposer"候选

> 三条**均实网核验为真实论文**，但 arxiv 2602–2603 超本 agent 知识截止，**性能宣称皆作者
> 自报、未见第三方独立复核**。按 §4/§8 一律当作"候选 proposer / 待竞赛者"，非"已验证优胜者"。
> §1 那条 Nature Methods 结论正是对这类宣称的必要怀疑。

### 4.A SCALE（arxiv 2603.17380）
- **mechanism**：virtual cell foundation model；把扰动预测**formulate 为 conditional
  transport**，用 set-aware flow 架构 + LLaMA-based cellular encoding + endpoint-oriented
  监督；BioNeMo 训练/推理框架，宣称 pretrain 12.51× / inference 1.29× 提速；Tahoe-100M 上
  PDCorr +12.02%、DE-overlap +10.66%（**相对 STATE，作者自报**）。
- **expos analogue**：竞赛层的 **flow/diffusion + foundation** 双属性 proposer；conditional
  transport ↔ action-conditioned response 映射。
- **ADOPT**：其"把扰动预测当作 (control 分布 → perturbed 分布) 的条件传输"抽象，是 M27
  response-distribution proposer 的干净范式。
- **ADAPT**：**只当竞赛候选**，进场即须与 mean/linear baseline 及其它 proposer 在同 split
  对决，report calibration + abstention。版本/权重入 fingerprint（§4）。
- **NOT-COPY**：不搬 BioNeMo 框架（§4 no-framework-transplant，且未见公开权重 repo）。
- **source-code**：未见公开 clone-worthy repo（WebFetch only）。
- **validation level**：**`simulation`**（in-silico benchmark on retrospective atlas；无湿验证）。

### 4.B Lingshu-Cell（arxiv 2603.25240，Alibaba DAMO）
- **mechanism**：**masked discrete diffusion** 的"生成式细胞世界模型"，在离散 token 空间学
  ~18000 基因的全转录组表达依赖，联合嵌入 cell-type/donor identity + perturbation，可对
  **未见过的 identity×perturbation 组合**预测全转录组变化；宣称在 Virtual Cell Challenge H1
  遗传扰动 benchmark 领先、能预测 PBMC 细胞因子反应；扩展到 4 物种 24.8 万细胞。
- **expos analogue**：竞赛层的 **diffusion（生成式分布）+ foundation** proposer；
  "identity×perturbation 组合外推"↔ expos 的 OOD/novelty acquisition。
- **ADOPT**：把 identity 与 perturbation **联合条件化**、以此支撑组合外推的思路，映射 expos
  的 context + intervention 双输入 charter。
- **ADAPT**：cytokine 反应预测 ↔ M27 "cytokine 干预"用例；但仍**只 propose**，须对 baseline
  竞赛 + report calibration/abstention；VCC H1 是公开 benchmark，非本 run 观测（§4）。
- **NOT-COPY**：大模型，不搬（§4）。
- **source-code**：homepage 有，未 clone（WebFetch only；大模型权重不入主仓）。
- **validation level**：**`simulation`**（benchmark；无湿验证）。

### 4.C PerturbDiff（arxiv 2602.19685）
- **mechanism**：**functional diffusion**——把建模从"单个细胞"上移到"整个分布"：将分布嵌为
  Hilbert 空间中的点，定义**直接在概率分布上运行**的扩散生成过程。动机：单细胞测序是破坏性的
  （同一细胞不能测扰动前后），故须映射未配对的 control/perturbed 群体；且同一观测条件下反应
  因不可观测隐因子（微环境、批次）而系统性变化，构成"分布的流形"。宣称对**未见扰动**泛化更好、
  SOTA（作者自报）。
- **expos analogue**：竞赛层的 **response-distribution / flow-diffusion** proposer 的最清晰
  代表——"预测的是分布不是点估计"。
- **ADOPT（强）**：**"预测对象是 response 分布而非点"**这一抽象**应写进 expos M27 的
  observable/claim 语义**——它天然承载 uncertainty，直接喂 calibration 与 abstention 裁决
  face（模型报的是分布 → 可量校准 → OOD 时可弃权）。这是本组对竞赛层裁决层最有用的建模输入。
- **ADAPT**：把"分布预测"落为 expos 的 dry evidence（含预测区间），裁决层据此算 calibration；
  但**分布本身不 certify claim**，只有 trusted observation 能否决/认证。
- **NOT-COPY**：不搬其扩散框架；只取"分布为一等预测对象 + 未配对群体映射"的抽象。
- **source-code**：project page（katarinayuan.github.io/PerturbDiff-ProjectPage），未 clone。
- **validation level**：**`simulation`**（benchmark；无湿验证）。

---

## 5. action-conditioned perturbation prediction（概念族）+ OOD-abstention 映射

### 5.1 mechanism
一族已发表方法，把"扰动"当作**作用于细胞状态的动作/干预**，预测反事实反应分布：
PerturbNet（未见化学/遗传扰动的分布预测，含 dosage/covariate）、CODEX（给未扰动数据 + dosage +
干预 → 预测扰动后 scRNA-seq）、CRADLE-VAE（counterfactual + artifact 解耦：把表达分解为
basal/perturbation/artifact）、CINEMA-OT（最优传输做因果反事实配对）。

### 5.2 exact expos analogue → claim 认证
- **action-conditioned** ↔ expos 的 `intervention → response` 一等输入（M27 charter 的
  genetic/chemical/cytokine 干预）；dosage/covariate ↔ context。
- **response-distribution** ↔ 裁决层 `calibration`：预测分布 vs 观测分布的一致性。
- **OOD-abstention** ↔ 裁决层 `OOD/abstention`：对训练分布外的干预，模型**须能弃权**
  （report "insufficient"），而弃权在 expos 是**一等结果**（§4）——不是失败。
- **CRADLE-VAE 的 artifact 解耦** ↔ 硬约束"技术副本绝不冒充独立生物证据"：把 basal/artifact
  从 perturbation 效应中分出，正是防止批次/技术信号被当成生物 claim。

### 5.3 ADOPT / ADAPT / NOT-COPY
- **ADOPT**：`intervention` 作为一等条件 + `response 为分布` + `OOD 时弃权` 三件套，直接写进
  M27 claim 认证语义。
- **ADAPT**：这些方法各自的 VAE/OT 内核只是**竞赛层里的又一族 proposer**，须与 baseline
  同场竞赛、报 calibration/abstention。
- **NOT-COPY**：不选定单一方法作"官方 virtual cell"；expos 只固化"竞赛 + 认证"的接口。

### 5.4 architecture finding
> **abstention 必须是竞赛层的一等动作，且在裁决中受奖励。** 一个诚实弃权（OOD → insufficient）
> 的模型，在 expos 里应当**优于**一个对分布外干预自信而错的模型——这把 §4"negative/insufficient
> 是一等结果"落成竞赛层可计算的裁决规则。

### 5.5 source-code status / 5.6 validation level
概念族，未单独 clone（PerturbNet PMC12322087 等公开）。validation level：**`simulation` /
`retrospective data`**。

---

## 6. ★给 M27 模型竞赛层的关键 architecture finding（汇总，供架构主线）

1. **baseline-gate 是硬门，不是排行榜行**（源：§1 Nature Methods）。M27 竞赛层必须让
   `mean` 与 `linear-ridge` baseline 在每个 held-out split 上定"免费预测水位线"；任何昂贵
   proposer（foundation/flow/diffusion）**须显著且校准地越线才进入 acquisition**，否则
   "未越线"作为**一等 negative claim** 入 ledger。→ 这是 expos 的结构性防吹嘘机制。
   `references/linear_perturbation_prediction-Paper/benchmark/src/run_linear_pretrained_model.R`
   的 `solve_y_axb` 可作 linear-response scorer 参考实现。

2. **预测对象是 response 分布，不是点估计**（源：PerturbDiff / action-conditioned 族）。
   observable/claim 语义须承载分布 → 天然喂 `calibration` 与 `OOD/abstention` 裁决 face。

3. **response 是带结构的对象，不是单向量**（源：Spatial Perturb-seq）。须表达
   (cell-autonomous, cell–cell/spatial) 两层，`biological fidelity` face 才可否决——
   一个赢"平均表达"却错"空间通讯"的模型 fidelity 应判低。

4. **abstention 是一等动作且受奖励**（源：action-conditioned OOD）。诚实弃权 > 自信错答。
   落成竞赛层可计算的裁决规则，兑现 §4"negative/insufficient 一等结果"。

5. **public 扰动数据（Perturb-seq/单细胞/空间）严格双角色边界**（源：§4 + §1 caveat + VCC）。
   一律 = **benchmark / 校准语料**，**绝不当本 run 的 trusted observation**；且 benchmark 须
   随其 scope 一起入 fingerprint（如 §1"全癌系"caveat → context 边界）。数据集/权重/模型
   版本全部进 provenance fingerprint。

6. **models propose；只有 trusted observation 能 certify**（跨所有本组参照）。CellVoyager 的
   分析产物、三条 virtual cell 的预测、action-conditioned 的反事实——**全是 proposal/dry
   evidence**，认证权恒在 certification 层，kernel/ledger 保持生物盲。

7. **kernel 中立性不被本组触碰**：所有生物语义（分布 response、空间层级、intervention 条件、
   abstention）都落在 domain/provider/adapter/QC 与 `BioModelBackend` 契约里
   （版本入 fingerprint / 有 baseline 对照 / 报 calibration / 支持 abstention /
   只产 proposal），kernel 一字不动。

---

## 7. 诚实标注 · 查无实据引用清单

- **无一条本组参照核验为幻觉**：§0 全部 7 条实网确认真实（含带 arxiv 2602/2603 编号者）。
- **须保留怀疑（非幻觉，但证据强度受限）**：SCALE / Lingshu-Cell / PerturbDiff 及邻近一批
  （OCOO-T 2606.12838、CellxPert 2605.00930、Chem-PerturBridge 2605.31522、Chem2Gen-Bench
  2606.21109、"Virtual Cells Need Context Not Just Scale" biorxiv 2026.02.04.703804 等）——
  **均超本 agent 2026-01 知识截止，性能宣称皆作者自报、未见独立第三方复核**。按 §8 反
  novelty-chasing：**新论文 ≠ 采纳**，须过 expos discriminative test 门。本文件将其一律登记为
  "竞赛层候选 proposer"，而非"已验证优胜者"。
- **交叉印证的独立怀疑证据**（强化 §1，非本组主参照但相关、均真实）：
  *Nature Methods* s41592-025-02980-0 "Benchmarking algorithms for generalizable single-cell
  perturbation response prediction"；biorxiv 2024.12.23.630036 "A Systematic Comparison of
  Single-Cell Perturbation Response Prediction Models"；Arc Institute *Virtual Cell Challenge*
  (Cell S0092-8674(25)00675-0)。→ 与 §1 同向：该领域普遍存在"baseline 难被稳定超越"的怀疑。

---

## 8. 本组对 expos 源码的触碰边界（合规声明）
- 未改任何 expos/ 源码；未 commit/push；未搬任何外部框架进主仓。
- clone 仅进 `references/`（已 gitignore，非 expos 根）：`linear_perturbation_prediction-Paper`、
  `CellVoyager` 两个真实公开 repo。三条 virtual cell（SCALE/Lingshu-Cell/PerturbDiff）为大模型/
  框架，按 §4 只 WebFetch、不 clone、不入主仓。
