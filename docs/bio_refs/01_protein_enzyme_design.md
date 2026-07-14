# Bio Refs · 组 1：蛋白/酵素/抗体设计 + BioModelBackend

> 参照波分析笔记（expos 自产，进主仓）。喂 **M25 生成式蛋白/酵素设计闭环**。
> 权威蓝图：`docs/BIOLOGY_PROGRAM_2026.md`（§3 模型竞赛层 · §4 硬约束 · §5 验证级别 · §6 clone 纪律）。
> 作者：参照波 agent · 组 1（A 域会话 bf315d15）。日期：2026-07-14。
>
> **诚实红线执行结果**：本组 8 条参照**全部 WebSearch/WebFetch 查证存在**，0 条查无实据、0 条编造。
> 三条 2026 前沿 arxiv（超我方 2026-01 知识截止）已逐一 WebFetch 核对 arxiv 摘要页确认真实存在
> （标题/作者/日期一致）。下文机制描述**仅写查证到的内容**；未查到代码仓的诚实标注 "no public repo surfaced"。

## 0. 验证与 clone 台账（先看这张表）

| # | 参照 | 存在性 | 权威来源 | 公开 repo | 是否 clone | 验证级别 |
|---|------|--------|----------|-----------|-----------|----------|
| 1 | AgentPLM | ✅ VERIFIED | arXiv 2606.02386 (2026-06, ICML'26 workshop) | none surfaced | 否 | simulation (in-silico oracles) |
| 2 | Latent-Y (Latent Labs) | ✅ VERIFIED | arXiv 2603.29727 + latentlabs.com | 闭源（Latent-X2 权重不公开） | 否 | **prospective wet**（抗体单位数 nM 实测） |
| 3 | RosettaSearch | ✅ VERIFIED | arXiv 2604.17175 (2026-04) | none surfaced | 否 | simulation（结构预言 oracle） |
| 4 | AI-Native Enzyme Biofoundry | ✅ VERIFIED | bioRxiv 2026.02.01.703093 (+ UIUC/Zhao Nat.Commun.) | none surfaced | 否 | **physical autonomous**（真机自主 DBTL） |
| 5 | ALDE | ✅ VERIFIED | Nat. Commun. 2025 s41467-025-55987-8 (Arnold) | github.com/jsunn-y/ALDE | **✅ clone** | **prospective wet**（ParPgb 真湿 campaign） |
| 6 | PLMeAE | ✅ VERIFIED | Nat. Commun. 2025 s41467-025-56751-8 | 论文称 code available，URL 未浮现 | 否 | **prospective wet**（biofoundry 真湿） |
| 7 | ESM 系 zero-shot scorer | ✅ VERIFIED | facebookresearch/esm；Rives/Meier bioRxiv 2021 | github.com/facebookresearch/esm | 否（权重巨大，仅引用） | retrospective（ProteinGym DMS 回溯） |
| 8 | MAMMAL | ✅ VERIFIED | arXiv 2410.22367；npj Drug Discovery 2026 | github.com/BiomedSciAI/biomed-multi-alignment + HF 权重 | **✅ clone** | retrospective（11 下游 benchmark） |

**已 clone 到 `references/`**（gitignore，未进主仓）：`references/ALDE/`（39M）、`references/biomed-multi-alignment/`（6.4M, MAMMAL）。
**诚实旁注**：ESM zero-shot 搜索命中一条 "clawRxiv / clawrxiv.io" 结果，来源可疑（疑似非正规镜像/噪声），**未采信**；ESM 的存在性由 facebookresearch/esm 官方 repo 与 Rives et al. 2021 独立坐实，与该可疑源无关。

---

## 1. AgentPLM — Agentic Protein Language Models with Reasoning-Augmented Decoding

1. **mechanism**：给预训练 PLM 装 Reasoning-Augmented Decoding（RAD）——把自回归生成**中途暂停**，
   调用生物物理 oracle 工具（ESMFold / FoldX / AutoDock Vina）拿反馈，再带更新的 context 续写以在线纠正违反热力学/结构约束的候选；
   训练用 Contrastive Agent Policy Optimisation（CAPO）端到端优化。评测跨 de novo 酵素设计、抗体优化、热稳定、PPI 界面设计、zero-shot fitness。作者 Sahil Rahman & Maxx Richard Rahman。
2. **exact expos analogue**：**agentic/generative proposer**（§3 M25 提案层最上一档）。RAD 的"生成中途查 oracle 再续写"= expos 里 proposer→scorer 的**内循环**被拉进单次生成里。
3. **判定：ADAPT（谨慎）**。借镜"proposer 可在生成中消费 dry scorer 反馈"的思想，但**不搬 RAD/CAPO 训练**（§4 无 discriminative 测试前不搬框架）。expos 应把 oracle 反馈保持为**外循环 acquisition**，而非耦进生成——这样每次 oracle 调用都留证据、可审计；RAD 的内循环把证据吞进黑箱，违背 expos 证据可变性。
4. **architecture finding**：**oracle 调用必须留痕成 dry evidence**。AgentPLM 的 oracle（ESMFold/FoldX/Vina）全是 in-silico → 对 expos 是"dry scorer"，**绝不能当 trusted observation**。这印证 BioModelBackend 契约 ⑤：backend 只产 proposal/dry evidence 不改 claim。对 M25：若做 agentic proposer，把"暂停-查 scorer-续写"实现为**显式的多步 proposal 记录**，每步 scorer 版本入 fingerprint。
5. **source/code status**：VERIFIED（arXiv 2606.02386，WebFetch 核对摘要页确认；无公开 repo）。
6. **validation level**：**simulation**（全部 oracle 为计算模型，无湿实验）。

## 2. Latent-Y — Lab-Validated Autonomous Agent for De Novo Drug Design（Latent Labs）

1. **mechanism**：文本 prompt（设计目标+约束）→ 自主设计新抗体，底座是 Latent-X2（Latent Labs 的抗体设计前沿模型）。
   核心：在预训练生成模型的**连续 latent 空间**里做序列+结构联合生成，配 **reward-guided inference-time search** 把生成朝用户目标转向。宣称把完整计算抗体设计流程平均压缩 ~56×（周→时），湿实验确认到单位数 nM 亲和力的 binder。2026-03 与 NVIDIA/Nebius 合作部署。
2. **exact expos analogue**：整体 = **一个封装好的 M25 闭环外壳**（proposer=latent 生成 + acquisition=reward-guided search + 湿验证）。对 expos 最相关的是 **acquisition：latent 空间里的 reward-guided inference-time search**（对应 §3 exploitation/multi-objective 一档）。
3. **判定：NOT-COPY（作为系统）/ ADAPT（作为一个 proposer backend 的概念）**。Latent-X2 闭源、不可审计、权重不入我方 fingerprint → 违反 §4"版本入 provenance"，不能作 expos 核心。但"latent 空间 reward-guided search"可作为**未来 v3 的一个候选 proposer**（须先过 discriminative 门）。
4. **architecture finding**：**这是"models propose, wet certifies"的最强正例**——Latent-Y 明确让湿实验（nM 实测）作为最终裁决，而非模型自证。印证 §4"只有 trusted observation 能 certify"。也是**闭源 backend 反面教材**：expos 若接此类 backend，必须把它当**不可信 proposer**，其"lab-validated"话术不可继承为我方 claim（我方须自跑观测）。
5. **source/code status**：VERIFIED（arXiv 2603.29727 "Latent-Y: A Lab-Validated Autonomous Agent for De Novo Drug Design" + 官网 latentlabs.com 新闻稿；模型闭源无开放权重）。
6. **validation level**：**prospective wet**（论文自称湿实验确认 binder 到 nM——但这是 Latent Labs 的观测，**非 expos 本 run 观测**，§4 明令不可冒充当前 run 证据）。

## 3. RosettaSearch — Multi-Objective Inference-Time Search for Protein Sequence Design

1. **mechanism**：backbone-conditioned 序列设计的**推理期多目标优化**。用 LLM 作 generative optimizer 嵌进搜索算法（受控探索/利用），reward 由 RosettaFold3（结构预测）在**严格算力预算**下算出。
   对 LigandMPNN 单次解码产的 400 条次优序列做优化，恢复出 LigandMPNN 单遍解码做不到的高保真设计，结构保真指标提升 18–68%，设计成功率 2.5×；**关键：用独立结构预言 Chai-1 复评仍稳健**。作者含 Kevin K. Yang、Frank DiMaio（均真实知名研究者）。
2. **exact expos analogue**：**acquisition 层的 multi-objective 推理期搜索** + **"独立 oracle 复评"= 我方 held-out / 第二 scorer 交叉验证**。LigandMPNN=baseline proposer，RosettaSearch=在其上做 acquisition。
3. **判定：ADOPT（模式）**。两点直接采纳到 M25：①**推理期搜索（不重训模型，冻结 scorer + 搜索）**——正合 M25 v1"可审计算子/搜索 + 冻结 scorer"的次序；②**用独立第二 oracle（Chai-1）复评首 oracle（RF3）选出的候选**——这是防 scorer 过拟合/自证的制度化手法，可直接进 expos QC/decision 层。
4. **architecture finding**：**"独立 oracle 交叉复评"应写入 BioModelBackend/decision 契约**。RosettaSearch 用第二结构预言复评，正对应 §3 decision 层的 calibration/OOD 与 §4"技术副本不冒充独立证据"。对 M25：dry eval 至少备两个互异 scorer，**主 scorer 选出的排名须能被第二 scorer 复评**；排名不稳 = abstention 信号（契约 ④）。也印证**baseline 对照**：全篇以 LigandMPNN 单遍为 baseline 量化增益。
5. **source/code status**：VERIFIED（arXiv 2604.17175，WebFetch 核对摘要页；无公开 repo surfaced）。
6. **validation level**：**simulation**（reward 与复评均为结构预测模型，无湿实验）。

## 4. AI-Native Biofoundry for Autonomous Enzyme Engineering

1. **mechanism**：AI-native 自主 biofoundry，"cloud-edge synergistic"架构，LLM 驱动的 Agent-Native 控制系统，跑迭代 **Design-Build-Test-Learn（DBTL）** 闭环，显著降低对人类领域专家依赖。
   （关联坐实：UIUC/Huimin Zhao 团队 AI+机器人+合成生物真机工作，酵素活性最高 26× 提升、底物专一性 90×，见 Nat. Commun. / energy.gov / news.illinois.edu。）
2. **exact expos analogue**：**整套 = expos 生物闭环的目标形态本身**（Hypothesize→Design→Build→Test→Analyse→Certify→Learn→Redesign 的真机版）。对应 M25 闭环 + 远期 M29 具身湿执行。
3. **判定：ADAPT（作为北极星与 DBTL 骨架对照）/ NOT-COPY（不搬其编排栈）**。DBTL 循环结构与 expos 环同构，可作施工对照；但其"LLM 编排真机"栈与 expos 既有 kernel/planner/scheduler/mcl 冲突，且真机边界未成文（§6.1 安全）——不搬。
4. **architecture finding**：**"AI-native / LLM 编排"不等于放弃可审计**——这是对 expos 的挑战也是提醒。expos 的差异化正是在这类系统之上加**证据不可变 + claim ledger + kernel 域中立**。对 M25/M29：可把此参照当"自主酵素 DBTL"的能力上界参照，但 expos 必须坚持**models propose / observations certify** 与真机安全 charter（§6.1），不能像纯 biofoundry 那样让 agent 直接改结论。
5. **source/code status**：VERIFIED（bioRxiv 2026.02.01.703093；UIUC 线多源独立坐实。无公开 repo surfaced）。
6. **validation level**：**physical autonomous**（真机自主 DBTL，本组唯一到此级别者）。

## 5. ALDE — Active Learning-Assisted Directed Evolution（已 clone）

1. **mechanism**：把**主动学习**装进定向进化。在**组合式 site-saturation 文库**（若干残基同时突变、设计空间可枚举）上：
   每轮湿 assay 收 sequence-fitness 数据 → 训监督 ML（GP / 深核 DKL / DNN ensemble / boosting ensemble）→ 用不确定性量化的 acquisition（GREEDY/UCB/Thompson Sampling）挑下一批筛。ParPgb 真湿 campaign：非天然环丙烷化反应产率 3 轮从 12%→93%，优化 5 个上位（epistatic）残基。对照 random selection 与传统 DE 均胜。作者含 Frances Arnold。
   **代码实证**（`references/ALDE/`）：`execute_production.py` 明写双层循环 `for mtype in ["BOOSTING_ENSEMBLE","GP_BOTORCH","DNN_ENSEMBLE","DKL_BOTORCH"]: for acq_fn in ['GREEDY','UCB','TS']` = **4 模型 × 3 acquisition = 12 配置竞赛**；`src/acquisition.py` 基于 botorch GP 后验；**全程无 PLM**，onehot 编码即可。
2. **exact expos analogue**：**这是 M25 acquisition + 模型竞赛层的最贴近开源实现**。
   - `src/models.py` 的 4 模型 = §3 M25 scorer 竞赛（含刻意简单的 GP/onehot baseline）；
   - `src/acquisition.py` 的 GREEDY/UCB/TS = §3 acquisition 层 exploitation/uncertainty；
   - 组合枚举 domain（`generate_domain.py` 出 `all_combos.csv`+`onehot_x.pt`）= M25 v1 **可审计变异算子产出的可枚举设计空间**；
   - 每轮 `indices.pt`→variants 的映射 = expos 的 lineage/proposal 台账。
3. **判定：ADOPT（作为 M25 acquisition 的参考实现）**。**本组对 M25 v1 施工图最有用的一条**——它证明**不需要大模型也能闭环并大幅提升**（12%→93%），完全支撑"可审计算子优先、PLM 是可选 v2"的次序。可借镜其 acquisition/模型接口设计（不搬码进主仓，作对照重写）。
4. **architecture finding**：**M25 acquisition 层直接照 ALDE 的"N 模型 × M acquisition 网格"落地**，且**每格都天然含 baseline**（GP+onehot 就是刻意简单的对照，正应 §3/§4 "foundation model 必须对简单 baseline 竞赛"）。BioModelBackend 契约由此具体化：
   - ② baseline 对照 = 竞赛网格里恒含 onehot-GP；
   - ③ calibration = GP/DKL 后验方差天然可报；
   - ④ abstention = 后验不确定性高时的挑选/弃权语义；
   - 版本入 fingerprint = 模型类型+kernel+dropout+acq 全进配置身份（ALDE 的 `fname` 已如此编码，可镜像成 expos fingerprint 字段）。
5. **source/code status**：VERIFIED + **REPO CLONED** → `references/ALDE/`（github.com/jsunn-y/ALDE，Nat. Commun. 2025 s41467-025-55987-8，Zenodo DOI 14194551）。
6. **validation level**：**prospective wet**（ParPgb 真湿 3 轮 campaign——真实前瞻湿实验，本组最硬的真湿证据之一）。

## 6. PLMeAE — Protein Language Model-enabled Automatic Evolution

1. **mechanism**：PLM + 自动 biofoundry 的闭环。每轮由 PLM（如 ESM-2 zero-shot 突变预测）或监督 ML 设计 96 个 variant → biofoundry 自动构建+测活 → Bayesian optimization + MLP fitness 预测导航 fitness landscape。
   以 p-cyanophenylalanine tRNA 合成酶为模型：4 轮 ~10 天，酵素活性最高 2.4×，胜过 random 与传统 DE。（关联 EVOLVEpro：Science，few-shot PLM+回归，最高 100× 提升。）
2. **exact expos analogue**：**PLM zero-shot proposer（§3 M25 "ESM zero-shot" 档）+ biofoundry 湿 assay 认证**。BO+MLP = acquisition + supervised fitness scorer 两档。
3. **判定：ADAPT**。采纳"**PLM 只作 proposer、湿 assay 才 certify**"的分工与 few-shot 主动学习节律；但**不搬其 biofoundry 编排**（与 expos scheduler/mcl 冲突，且我方当前无真机）。PLM 作为**可选冻结 scorer**接入即可（正是 M25 v2 定位）。
4. **architecture finding**：印证 M25 的**次序**——PLMeAE 里 PLM 与监督 ML **并列供选**、由湿数据裁高下，正是模型竞赛层。对 BioModelBackend：PLM backend 必须①版本入 fingerprint（ESM-2 具体权重）②与监督/random baseline 竞赛③只产 proposal（96 variant 候选）不改 claim。也提示 expos 的"湿 assay 认证"边界：biofoundry 测活 = trusted observation，PLM 打分 = dry evidence，二者不可混。
5. **source/code status**：VERIFIED（Nat. Commun. 2025 s41467-025-56751-8 / PMC11814318；论文称 code available 但具体 repo URL 未在搜索浮现——诚实标注"code stated available, repo URL not surfaced"）。
6. **validation level**：**prospective wet**（biofoundry 真湿 4 轮）。

## 7. ESM 系 PLM zero-shot scorer（ESM-1v / ESM-2）

1. **mechanism**：仅给序列，用 masked marginal log-likelihood ratio 对全部 L×19 单点突变打分（zero-shot，无需训练数据）预测突变效应/fitness。ESM-2 650M 在 ProteinGym 达 Spearman ρ≈0.44–0.50，接近部分监督法（~0.55–0.65）。Meta 官方开源（facebookresearch/esm）。近期改进：inference-time dropout（MC-dropout 式平均）无需重训即可提升 zero-shot。
2. **exact expos analogue**：**M25 "ESM zero-shot scorer" 档的原型本体**（§3 明列）。是一个**冻结、只读、只产 dry 打分**的 scorer backend——BioModelBackend 的最简正例。
3. **判定：ADAPT（作为可选冻结 scorer，v2）**。ADOPT 其"masked-marginal 打分"作为 M25 v2 的一个 scorer；但**不当主力**：ProteinGym ρ~0.5 意味着大量方差未解释，**必须对简单 baseline（保守/BLOSUM/进化频率）竞赛**（§4）。
4. **architecture finding**：**ESM 是 BioModelBackend 契约五要件的教科书落点**——①ESM-2 具体权重(650M/3B)+版本入 fingerprint；②对 substitution-matrix baseline 竞赛；③masked-marginal 分数可校准/报置信；④低置信度→abstention；⑤只产 dry evidence（打分），**绝不 certify**。对 M25：把 ESM 封成"冻结 scorer backend"接口，与 ALDE 式监督 scorer 并列进竞赛网格。**重要 baseline 提醒**：ProteinGym 上 ESM 未必胜专用监督法——正是 §3/§4"foundation model 未必胜 baseline"的直接证据。
5. **source/code status**：VERIFIED + REPO 公开（github.com/facebookresearch/esm；Rives et al. bioRxiv 2021 / Meier et al. zero-shot）。**未 clone**（权重巨大，仅作接口参照）。
6. **validation level**：**retrospective**（ProteinGym/DMS 回溯 benchmark，无本 run 湿实验）。

## 8. MAMMAL — Molecular Aligned Multi-Modal Architecture and Language（已 clone）

1. **mechanism**：跨模态生物基础模型 `ibm/biomed.omics.bl.sm.ma-ted-458m`，20 亿样本 6 数据集 3 域 7 任务预训练（蛋白/小分子/单细胞基因表达）。核心是**可调 task-prompt 语法**：动态组合 token+scalar，把分类/回归/生成统一成 seq2seq，单域或跨域实体皆可。11 下游任务 9 SOTA、2 comparable。**代码+权重全公开**（github.com/BiomedSciAI/biomed-multi-alignment + HF），已支持 MCP agent 集成。
   **代码实证**（`references/biomed-multi-alignment/`）：`mammal/model.py`、`mammal/task.py`、`mammal/keys.py`、LoRA 微调、`mammal_mcp/`。
2. **exact expos analogue**：**BioModelBackend 契约的旗舰参照物**（§3 蓝图点名"MAMMAL 类"）——一个 sequence-only / sequence+structure / molecule+protein / perturbation+transcriptome / multimodal 通吃的**多模态 hypothesis scorer backend**。其 task-prompt 语法 = expos "统一 backend 接口"的现成设计蓝本。
3. **判定：ADAPT（接口/契约蓝本，不搬模型进核心）**。MAMMAL 的**统一 prompt 接口**值得镜像成 expos 的 BioModelBackend 抽象（一个接口喂多模态、产分类/回归/生成）；但 MAMMAL 本体是巨型 foundation model，**§4 无 discriminative 测试前不搬进主仓**，且必须对简单 baseline 竞赛（其自报 11 任务里 2 项仅 comparable、非全胜——已含"未必碾压"的诚实信号）。
4. **architecture finding**：**BioModelBackend 契约应直接照 MAMMAL 抽象成型**——
   - 一个后端、多模态输入（seq/struct/molecule/transcriptome）、可切任务（M25 fitness 回归、M27 perturbation 响应）；
   - ①版本入 fingerprint：MAMMAL 权重 `ma-ted-458m` 有明确 HF 版本可钉；
   - ②baseline 对照：其 9/11 SOTA、2/11 comparable 说明**即使 SOTA 也非全域碾压**，expos 须逐任务对 baseline 竞赛；
   - ⑤只产 proposal/dry evidence：MAMMAL 出的是打分/生成，非湿观测——接入 expos 时定位为 dry scorer，不得 certify。
   - MCP 集成（`mammal_mcp/`）提示未来可作为 expos MCP 审计面下的**只读 scorer 服务**接入。
5. **source/code status**：VERIFIED + **REPO CLONED** → `references/biomed-multi-alignment/`（arXiv 2410.22367；npj Drug Discovery 2026 s44386-026-00047-4；HF 权重公开）。
6. **validation level**：**retrospective**（11 下游 benchmark 回溯评测，无湿实验）。

---

## 9. 喂 M25 的关键 architecture findings（收敛结论）

### 9.1 "可审计变异算子优先于大模型"的次序——本组证据**强烈印证**
- **ALDE = 决定性正例**：全程**无 PLM**，靠"组合枚举设计空间（可审计枚举）+ onehot 编码 + GP/UCB acquisition"就把产率 12%→93%（真湿）。直接支撑 M25 v1 先做确定性算子 + 简单编码 + 主动学习、PLM 留 v2。
- **RosettaSearch** 亦印证：**推理期搜索 + 冻结 scorer**（不重训）即得 2.5× 成功率——M25 v1 不必训大模型。
- **PLMeAE / EVOLVEpro** 是"PLM 作 proposer"的对照支线：PLM 有用，但始终**只作提案、湿 assay 才认证**——与 v1/v2 分层不冲突，恰好定义了 v2 该怎么接。
- **无一条参照要求"先上大模型"**；相反最硬的真湿增益（ALDE 12→93%）来自最简栈。**次序判定：M25 v1 auditable operators-first 得到全组背书。**

### 9.2 "foundation model 必须对简单 baseline 竞赛"——本组提供直接 baseline 证据
- **ALDE**：显式对照 random selection 与传统 DE，且竞赛网格恒含 onehot-GP 这一刻意简单 baseline。
- **RosettaSearch**：以 LigandMPNN 单遍解码为 baseline 量化增益，**并用独立第二 oracle（Chai-1）复评**防自证。
- **ESM zero-shot**：ProteinGym 上 ρ~0.5、未必胜专用监督法——**foundation ≠ 稳胜**的直接实例。
- **MAMMAL**：自报 11 任务 **9 SOTA / 2 仅 comparable**——即使旗舰 foundation 也非全域碾压。
- **落地**：M25 的 scorer 竞赛网格**必须**恒含"简单 baseline 格"（如 substitution-matrix / onehot-GP），任何 PLM/foundation 须在同一 held-out 上跑赢它才被采信。

### 9.3 BioModelBackend 契约（从本组提炼，可直接进 M25 charter）
统一接口照 **MAMMAL task-prompt 抽象** + **ALDE 竞赛网格** 成型，每个 backend 五要件：
1. **版本入 fingerprint**：权重/模型型号/kernel/dropout/acq 全进配置身份（ALDE `fname` 编码 + MAMMAL HF 版本钉 = 现成范式）。
2. **有 baseline 对照**：竞赛网格恒含刻意简单格（onehot-GP / substitution-matrix）。
3. **报 calibration**：GP/DKL 后验方差、masked-marginal 置信——backend 必须能出不确定性。
4. **支持 abstention**：不确定性高 / 两 scorer 排名不稳（RosettaSearch 式独立复评）→ 弃权信号。
5. **只产 proposal / dry evidence，绝不 certify**：ESM 打分、MAMMAL 生成、AgentPLM oracle 反馈——**全是 dry**；唯 ALDE/PLMeAE 的**湿 assay 测活才是 trusted observation**（§4）。

### 9.4 决策/QC 层的一条可直接采纳的新制度
**独立第二 oracle 交叉复评**（RosettaSearch 用 Chai-1 复评 RF3）——写进 expos decision/QC：主 scorer 选出的 top-K 须由结构/序列上**互异的第二 scorer** 复评；排名剧变 = OOD/abstention 触发。这同时防"技术副本冒充独立证据"（§4）。

### 9.5 验证级别诚实分布（本组）
- physical autonomous：仅 #4 AI-Native Biofoundry（真机自主）。
- prospective wet：#2 Latent-Y、#5 ALDE、#6 PLMeAE（但**均为他方观测，不可当 expos 本 run 证据**，§4）。
- simulation：#1 AgentPLM、#3 RosettaSearch。
- retrospective：#7 ESM、#8 MAMMAL。
- **expos 生物侧当前真实级别仍 = simulation（M24-B）**；上述真湿/真机是参照系的能力上界，**不改变我方对外话术**（不得自称 wet-lab validated）。
