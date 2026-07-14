# M24 REPO MAP — cell_free_expression_screen 接缝与红线取证地图

> 目的：为 M24（把生物作为**第三个结构上真正不同的域**）产一份准确的 repo 架构地图。
> 方法：纯读取真实代码取证，给 `文件路径::符号` 锚点，不臆测。若某 hook 确实
> chemistry-leaky 就直说。本文件不改任何源码，只描述现状 + 对 M24 的含义 + 建议落点/风险。
>
> 章程：`docs/M24_CELL_FREE_EXPRESSION.md`。收官台账：`CHECKPOINTS.md`（M20 换域存在性证明 /
> 域契约 v2 + 溯源补全批 两条）。
>
> 作者视角：本任务读者是即将开工的 A/B 双侧建设者；结论诚实优先于乐观。

---

## 0. 全景架构一节（数据流 + 分层 + 换域接缝）

### 0.1 两条驱动器，M24 只碰 MCL

expos 有**两条**独立 loop 驱动器，M24 只走后者：

- `expos/loop.py::run_loop` —— **单腿**驱动：一个同步 `ExecutionAdapter.execute()`（sim_crystal /
  sim_coating / bench_manual），走 `build_adapter(cfg)`。crystal/coating 域用这条。**M24 不走这条。**
- `expos/mcl.py::run_mcl_loop` —— **双腿** Dry–Wet–Agent 管线：dry PySCF 筛选腿 → Dry→Wet 晋升门 →
  wet plate-reader 腿。solvent_screen / catalyst_screen 走这条，**M24 也走这条**。它**从不经过
  `build_adapter`**（dry 腿是 async job 形，wet 腿是 out-of-band socket 设备）。

### 0.2 MCL 单轮数据流（`expos/mcl.py::_run_round`，M24 要插的全部接缝都在这条链上）

```
compile_knowledge(claims, hypotheses)              # kernel/knowledge.py —— 证据编译（红线）
  → 模板/LLM agent 提案 (PRIOR_PROPOSAL)            # mcl._propose_candidates
  → dry 腿: PySCFDryAdapter.run(dry_exp)            # 硬接线 PySCF（见 Q1）
      → dry_raw_to_observations                    # adapters/dry/ingest.py（PySCF 特化）
      → run_qc / TrustPolicy 裁决
  → 晋升门 EvidenceGatedPromotion.decide           # planner/promotion.py（纯函数，红线）
  → wet 腿: compile_wet → run_wet_leg              # adapters/wet/screen.py + sim_reader socket
      → raw_to_observations                        # adapters/ingest/__init__.py（唯一 raw→obs 通道）
      → run_qc / TrustPolicy 裁决
  → certification.decide → apply_claim_deltas      # planner/certification.py → kernel/claims.py（红线）
  → round checkpoint
```

### 0.3 内核只懂七个概念（`expos/kernel/objects.py`）

kernel 仅有**两个持久科学对象**：`ExperimentObject`（objects.py:276）与 `ObservationObject`
（objects.py:372）；`DecisionRecord`（objects.py:410）是事件载荷，不是第三对象。agent 读到的
`KnowledgeView`/`HypothesisObject` 是**编译产物**。kernel 的词汇：candidate / observation /
trust / evidence(claim) / knowledge / decision。**M24 红线：这七个概念一字不改。**

### 0.4 换域接缝的两代机制（重要——M24 站在第二代之上）

- **M20 `_domain_bindings`（`expos/mcl.py:326`）**：运行期真正驱动 loop 的换域机制。
  从 `DesignSpace` 里带 `descriptors` 的 categorical 变量解析出 candidate 池 / 采集坐标 /
  偏好方向；没有 `descriptors` 则走 `LEGACY-FALLBACK`（solvent 字面量，byte-identical）。
  catalyst_screen 走 descriptor 路径。**这是 M24 实际要复用/扩展的接缝。**
- **M21 `DomainProvider` 五 hook（`expos/adapters/domain_provider.py`）**：把"换域改五处"
  固化成一份 ABC 契约（`dry_species/wet_coords/truth_profiles/seed_claims/validate_yaml` +
  可选 `null_profiles`）。**关键取证发现（见 Q6）：这五 hook 目前只被 `check_complete()`
  出生治理消费，运行期 loop 根本不读 provider**——`grep dry_species/wet_coords/truth_profiles`
  在 `expos/` 内的运行调用点为零（只有 domain_provider.py:194-203 的 check_complete 与两个
  provider 自身）。M21 建成了契约与出生治理，但**没有把 run 改成消费 provider**；运行期仍直读
  `catalysts.py` / 直读 reader 的 `TRUTH_PROFILES`。

---

## 1. 六问逐一取证答

---

### 问 1 — dry 腿抽象：PySCF 特化还是域通用？最小新 dry adapter 落点？

**现状锚点**

- `PySCFDryAdapter`（`expos/adapters/dry/adapter.py:117`）走的是 **async job 路径**：
  `run(exp, backend)`（adapter.py:342）→ `build_specs` → `submit` 到
  `expos.scheduler.SubprocessBackend`（adapter.py:30-35, 345）→ 子进程 `python -m
  {pkg}.worker`（adapter.py:257）→ 轮询到终态 → 从 `result.json` artifact 收集。它构建 PySCF
  `Mole`（`build_mol`，compute.py:49）、跑 HF/STO-3G、导出 dipole 作 `polarity_proxy`。
  执行方式在 `domain.py::ExecutionKind` 里叫 `dry_compute`（domain.py:222）。
- **`ExecutionKind` 枚举现有值**（`expos/domain.py:203-224`）：`dry_compute`（async job 形，
  仅 `--loop mcl` 驱动，`build_adapter` 对它**响亮拒绝**，domain.py:588-597）/ `wet_assay`
  （同步 wet ExecutionAdapter）/ `sim_execute`（同步 in-silico simulator）。
- **是否存在非 PySCF 的同步 dry adapter 契约面？** 有一个**同步 `ExecutionAdapter` 契约面**：
  `expos/adapters/base.py::ExecutionAdapter`（base.py:69-75，`execute(exp, rng) -> ExecutionResult`
  的 `Protocol`），其基类实现是 `expos/adapters/sim_base.py::SimulatorBase`（sim_base.py:30，
  `execute` 在 sim_base.py:92 同步、本机、秒级、无 sbatch）。crystal/coating 就是它的子类。
  **但**：这条同步 execute 径今天扮演的是 **wet/sim「执行腿」**（走 `build_adapter` + `run_loop`），
  **不是 MCL 的 dry 腿**。章程里提到的 "snar_flow 走 SimulatorBase 同步 execute 径" 指的正是这个
  同步契约面存在——它**契约上**可容纳一个非 PySCF、同步、轻量的 adapter。
- **MCL dry 腿是硬接线的**（关键取证）：`run_mcl_loop` 里 `dry_adapter = PySCFDryAdapter(...)`
  是**具体构造**（`expos/mcl.py:936`），并直接调 `dry_adapter.run(dry_exp, backend=SubprocessBackend())`
  （mcl.py:1212）+ `dry_raw_to_observations`（mcl.py:1218，PySCF 特化 ingest，读 `DryRawResult`）。
  **没有 dry-adapter 注册表 / 没有 dry 腿的 execution_kind 分派**——MCL 的 dry 腿今天只能是 PySCF。

**对 M24 的含义**

生物 dry 腿（GC content / codon adaptation index / RBS strength / RNA folding ΔG 代理）是
**"sequence-feature → scalar proxy" 的轻量同步计算**，**没有分子、没有几何、没有 SCF、无需 sbatch**。
它天然属于 `sim_execute` 那类同步契约（`ExecutionAdapter.execute` / `SimulatorBase`），**不该**伪装
成 PySCF 换几何表（catalyst 是那样，生物不是——章程 §架构风险 #1 明确）。

但接缝不在契约层，而在 **MCL 的 dry 腿硬接线**：`mcl.py:936` 直构 `PySCFDryAdapter`、`mcl.py:1218`
直调 PySCF 特化 ingest。要接生物 dry 腿，**必须让 MCL 的 dry 腿可插拔**。

**建议落点 / 风险**

- **最小新 dry adapter 契约面**：实现一个同步类（形如 `SequenceFeatureDryAdapter`），产出与 dry 腿
  一致的观测形——即每孔一条带 `polarity_proxy` 同位的 scalar proxy（新 dry metric，如 `expression_proxy`，
  debye 之外的单位，见下）。最干净的落点是**复用同步 `ExecutionAdapter.execute` 面**（base.py:69）产
  `ExecutionResult(raw_results=...)`，再走**通用** `raw_to_observations`（ingest/__init__.py:28），
  **绕开** PySCF 特化的 `dry_raw_to_observations`。
- **MCL 需要一处最小改动（B 侧，非 kernel）**：把 `mcl.py:936` 的 `dry_adapter` 从"直构 PySCF"改成
  "按 `cfg.execution_kind` / 一个 dry-adapter 小注册表分派"，并把 dry ingest 也随之分派。这是 M24 在
  loop 层最实的一处新接缝——**它不是 kernel 改动**（mcl 是编排层），但要小心别把它做成"域字面量回流
  loop"（EXP011 精神）。
- **单位红线**：dry metric 若不是 dipole，`UNIT_VOCABULARY`（domain.py:65）只有
  `{arbitrary_unit, debye, dimensionless, celsius, microliter}`。生物 proxy 多为无量纲，用
  `dimensionless` 即可，无需碰 kernel；若要新单位则**加性扩 UNIT_VOCABULARY**（域层，合法）。
- **风险**：dry 腿硬接线是"隐藏的 chemistry 假设"——M20 证词说"dry adapter 亦零改动"，那是因为
  catalyst 复用了 PySCF+几何。生物**不能**复用，故 MCL dry 腿的可插拔化是 M24 **必然要新增的接缝**，
  且它是 loop 层的真实工作量，不是"改个 yaml"。

---

### 问 2 — construct 物件承载：candidate.params 能放 sequence + lineage 而 kernel 零改动吗？

**现状锚点**

- `Candidate`（`expos/kernel/objects.py:194-201`）：`params: dict[str, Any]`（**自由 dict，无 schema**）+
  `source` + `rationale` + `placement_hint` + **`parent_obs_id: str | None`（objects.py:201，已存在的
  lineage 锚点）**。`KernelModel` 是 `extra="forbid"`（objects.py:102），但那约束的是 `Candidate` 的
  **顶层字段**，`params` 内部是完全自由的 `dict[str, Any]`。
- 现有 candidate 的 params 放什么（照抄形）：
  - solvent（LEGACY）：`{solvent: "ethanol", concentration: 5.0, temperature: 25.0, incubation_time: 30.0}`
    （`mcl.py:207` `_FIXED_CONDITIONS` + `mcl.py:383`）。
  - catalyst（descriptor 路径）：`catalyst_params(name)`（`catalysts.py:128`）返回
    `{catalyst: name, geometry: <zmatrix>, charge: .., spin: ..}`——注意 **geometry(zmatrix 字符串)
    就是塞在 params 自由 dict 里**流到未改动的 PySCF adapter 的（`_resolve_geometry`，compute.py:33
    优先取显式 `geometry`）。这是"域特化载荷经 params 注入、kernel 零改"的既有先例。
- `ObservationObject.cand_id`（objects.py:376）+ `parent_obs_id` 在 Candidate 上——**父子谱系已有一级
  字段承载能力**。

**对 M24 的含义**

construct 的 sequence 字符串 + 组件（promoter/RBS/CDS）+ 条件，**可以直接放进 `Candidate.params` 自由
dict**（与 catalyst 把 zmatrix 塞 params 完全同构），**kernel 零改动**。parent–child lineage 有两条现成
路径：`Candidate.parent_obs_id`（objects.py:201）承载"从哪个观测衍生"，或把 lineage 放 params。

**建议落点 / 风险**

- **落点**：construct identity / sequence version / promoter·RBS·CDS 组件 / parent lineage 一律进
  **domain/provider 层的 params 构建**（`mcl.py::_candidate_params` 的 descriptor 分支，或新的
  bio 分支）。这与章程 §红线"属 domain/provider 层（新增合法）"一致。
- **不必扩 kernel schema**：`params: dict[str, Any]` 已是逃逸阀。若确要把 construct 提升为一级 schema，
  那**才**是碰 kernel——按章程应作为"发现"上报，不是默认动作。M24 v1 无需走到这步。
- **风险（诚实）**：`_domain_bindings` 目前假定 categorical 变量的 `descriptors` 是
  `{level: {coord: value}}` 单坐标（mcl.py:335, `screen.target_coord` 默认 `coord`）。construct 若要
  多组件多坐标，descriptor 表可承载多 coord 键（`VariableDef.descriptors` 校验只要求"所有 level 共享同一
  组坐标键"，objects.py:143-157），但 `_domain_bindings` 只取 `_COORD_NAME="coord"` 单轴
  （mcl.py:336）。**多轴 sequence-feature 需要 bindings 层扩展**（B 侧，非 kernel）。

---

### 问 3 — controls / sentinel：能表达生物三对照（negative/positive/reference + 期望带）吗？

**现状锚点**

- `Control`（`expos/kernel/objects.py:203-208`）：`kind: Literal["sentinel","negative","positive"]` +
  `params: dict` + `expected_band: tuple[float,float] | None`。**已支持 negative/positive 两类，但
  没有 "reference" 这个字面量**；`expected_band` 是**单区间**，每个 Control 一条。
- 域声明侧 `SentinelSpec`（`expos/domain.py:142-146`）：`n` + `params`（单组）+ `expected_band`（单条）。
  **只支持一个 sentinel 块 / 一组参数 / 一条期望带**（catalyst_screen.yaml:89 / solvent_screen.yaml:86
  各只声明一个 sentinel）。
- **关键取证：MCL loop 根本不用 controls/sentinel**。`grep control|sentinel` 在 `expos/mcl.py`
  命中 0（除文档串）。`_wet_experiment`（mcl.py:674）**不构建任何 controls**。构建 Control 的唯一处是
  **单腿** `expos/loop.py:143`（`Control(kind="sentinel", ...)`，从 `cfg.sentinel` 复制 `n` 份）——那是
  crystal/coating 的 run_loop，不是 MCL。布局分配器 `expos/design/layout.py:223` 区分 sentinel 与
  other_ctrls，也只在单腿链上。
- wet 侧 `screen.protocol_spec_from_experiment`（screen.py:270-280）**能**把 `exp.controls` 展成
  `SolventSample(is_control=True, ...)`，缺筛选参数的对照默认落 mid 坐标（"calibration sentinels"，
  screen.py:269）——即 wet 腿**有**消费 controls 的能力，但 MCL 从没喂给它 controls。

**对 M24 的含义**

生物三对照（negative 无 promoter/CDS ~baseline / positive 已知强构建 / reference 中）在
**kernel `Control` 模型层**基本可表达：negative/positive 已是合法 kind，各带独立 `expected_band`。
**两个真实缺口**：

1. **`kind` 无 "reference" 字面量**（objects.py:205）——碰它 = 改 kernel schema（红线）。
   规避：把 reference 建成 `kind="sentinel"` 或 `kind="positive"` 的一个带自身 `expected_band` 的
   Control，用 `params` 里的语义标签区分（domain 层），**kernel 零改**。
2. **域声明侧 `SentinelSpec` 只支持单一 sentinel 块**（domain.py:142）——要声明式表达三对照各带期望带，
   需要 domain schema 支持"多对照声明"。这是 **domain 层加性扩展**（B 侧），不碰 kernel。
3. **MCL 从不下发 controls**——要让三对照真的上板并被 wet 腿测量，需在 `mcl._wet_experiment`
   （mcl.py:674）里构建 `exp.controls`（domain 声明 → Control 列表），wet 腿 `screen.py` 已有消费能力。
   这是 **mcl 编排层新增**（B 侧），非 kernel。

**建议落点 / 风险**

- **落点**：三对照声明进 domain yaml（扩 `SentinelSpec` 或新 `controls:` 块，domain.py 加性）→
  `_wet_experiment` 构建 Control 列表 → wet 腿 `protocol_spec_from_experiment` 消费（已支持）。
- **风险**：controls 的**期望带校验/漂移检测**逻辑今天全在**单腿** QC 链（`qc/checks.py` 的
  sentinel z-score / drift，checks.py:752+；`qc/attribution.py` 的 sentinel_sensor）。MCL 双腿链是否
  跑这套 sentinel QC 需要核对——若生物三对照要驱动 QC 裁决，可能触及 qc 层（**软红线**，见 Q5）。
  v1 最小版可先让三对照仅作"观测记录 + 归一化基准"（domain 层），**不**驱动新的 QC 裁决路径，避免碰红线。

---

### 问 4 — batch/plate 效应：板级批次偏移能复用 reader 服务端注入吗？

**现状锚点**

- wet reader 的服务端注入（`expos/adapters/wet/sim_reader.py`）只有**两个** artefact：
  - **calibration_drift**：`ReaderState.GAIN_DRIFT=0.006 / OFFSET_DRIFT=0.004`（sim_reader.py:207-208），
    **每测一孔** gain 下移、offset 上移（sim_reader.py:369-370），`calibrate()` 复位（sim_reader.py:295）。
    即**单调累积、按孔序递增**的系统偏置，标签 `calibration_drift`（sim_reader.py:378）。
  - **dropout**：`dropout_prob` / `dropout_wells`（sim_reader.py:179-180, 362-363），随机或强制丢读。
  - 注入经 `FaultConfig`（sim_reader.py:170）+ `inject` admin 命令（sim_reader.py:485）。
  - **没有板级/批次级的离散 offset 注入器**——reader 的偏置模型是"逐孔累积漂移"，不是"每板一个台阶"。
- 另有一套**独立**的 artefact 框架 `expos/adapters/artifacts.py`（**仅 sim_\* 单腿用**，非 reader）：
  含 `BatchShift`（artifacts.py:124）+ `InstrumentDrift`（artifacts.py:138）等，按
  `WellContext.solution_batch`（sim_base.py:124，`batch = R{round}-B{(row+col)%n_batches}`）作用。
  **这套 BatchShift 在 wet/reader 腿不可用**（reader 服务端自注入，不经 sim_base 注入器框架）。
- replicate + interleave 板序：`screen._replicate_order`（`screen.py:144-174`）——
  `interleave=True` 用 Latin-square 轮转把每个 candidate（每条 arm）均摊到整个 capture 序，令
  `corr(capture_index, arm)→0`，是 capture-order/arm 混淆的实验设计解药（screen.py:159-161）。
  MCL 每轮 = 一板（`_wet_experiment` 每 round 一个 wet_exp，mcl.py:674），故**板序 ≈ 轮序**。

**对 M24 的含义**

- **板内** replicate + 板序去混淆：`_replicate_order` 的 interleave 基底**直接可复用**（域无关，
  已在 catalyst 跑通 8 replicates）。生物 replicate 上板即用。
- **板间批次偏移**：reader 现有 `calibration_drift` 是**逐孔单调漂移**，语义上**不等于**"板级台阶偏移"。
  可近似复用的方式："跨板不 recalibrate → 漂移跨板累积"，即板 N 的整体偏置 ≈ 前 N-1 板累积漂移——
  这是**顺序漂移**而非**离散批次台阶**。真正的"每板一个随机 offset"（生物 plate batch effect 的典型形）
  在 reader 里**没有现成机制**，需**新增一个 reader fault 字段**（如 `plate_offset`，加性、服务端、
  与 dropout 同级）——这是 **adapters/wet 层加性新增**（A 侧），不碰 kernel/QC。

**建议落点 / 风险**

- **落点**：板内混淆复用 `_replicate_order(interleave=True)`（零改）；板间批次台阶若要建模，加一个
  `FaultConfig.plate_offset` 服务端注入器（sim_reader.py 加性），**并把它写进 truth sidecar**（如
  calibration_drift 那样，sim_reader.py:387），**绝不写进 OS 可见 reading**（红线：base.py:5-8，
  伪影透明元数据只进 truth_records）。
- **诚实评估**：章程 §风险 #4 猜"板级偏移 ≈ 标定漂移可复用"——**部分成立**：机制形（服务端注入 + truth
  sidecar 隔离）可完全复用，但 calibration_drift 的**数学形（逐孔单调）不是板级台阶**，直接复用会把
  "批次"误建成"漂移"。建议**新增一个板级 offset fault**（几行），比硬套 drift 更诚实。

---

### 问 5 — normalization 边界：相对对照归一落在哪层？evidence-compiler 红线在哪？

**现状锚点（观测 → 证据 的完整路径）**

1. **ingest**（`expos/adapters/ingest/__init__.py::raw_to_observations`，ingest/__init__.py:28）——
   **唯一 raw→obs 通道**，明文写"**只做形状对齐与布局映射，不做任何裁决**"（ingest/__init__.py:5-8），
   产出 `trust=PENDING, qc=None`。**禁读真值 sidecar**（ingest/__init__.py:10）。
2. **QC/trust 裁决**：`expos/qc/checks.py::run_qc` + `expos/kernel/lifecycle.py::TrustPolicy`
   （mcl.py:115, 142 引入）——把 PENDING 观测裁成 TRUSTED/SUSPECT/FAILED。
3. **certification（证据编译入口）**：`expos/planner/certification.py`（`decide` 纯函数）→
   `expos/qc/certification_stats.py::aggregate_round`（K-B 真统计聚合器）→ 产 `ClaimDelta` →
   `expos/kernel/claims.py::apply_claim_deltas`（ledger 变更）→ 下一轮
   `expos/kernel/knowledge.py::compile_knowledge`（KnowledgeView 编译）。

**evidence-compiler 的确切边界（M24 绝对不可碰）**

真正把"观测 → 证据/知识"编译出来的**红线区**是这一组：

- `expos/kernel/knowledge.py`（`compile_knowledge`——知识是编译产物，knowledge.py:1-20）
- `expos/kernel/claims.py`（`ClaimRecord/ClaimDelta/Ledger/ProvenanceSnapshot/StatisticSnapshot/
  apply_claim_deltas/DECISION_FN_REGISTRY`）
- `expos/qc/certification_stats.py`（`aggregate_round`——e 值/效应聚合，K-B 统计内核；M20 证词：与
  solvent 域**同一注册 fn** `e_value_round_certification v1`，跨域零改动复用）
- `expos/planner/certification.py`（`decide` 纯函数——K5 无 I/O 无随机；certification.py:32-36）
- `expos/planner/promotion.py`（`decide` 纯函数——晋升门）
- `expos/kernel/store.py` / `lifecycle.py` / `overrides.py`（事件/裁决/改判语义）

**对 M24 的含义 + 落点**

生物读出"相对对照归一"（如 fluorescence / positive-control，或减 negative baseline）**若要做，落点必须在
evidence-compiler 之外**：

- **首选落点 = domain/adapter 层**：归一化是"域如何把原始读出变成可比较量"的**域政策**，可在
  wet 腿产观测**之前/之时**完成（adapters/wet 层的 domain-specific 读出变换），使进 ingest 的
  `MeasuredResult.value`（objects.py:297）已是归一化后的量。这样 QC/certification/knowledge 全部
  **无感**，红线区一字不改。章程 §红线正把 "normalization policy" 列为 domain/provider 层合法项。
- **次选落点 = QC 层（软红线，慎）**：若归一化必须看"同板对照"（跨记录），那是 QC/聚合的领域
  （`qc/` 层），**技术上**可加，但一旦归一化进入 `aggregate_round` 的统计输入，就逼近 evidence-compiler
  边界——章程明言"碰即红旗"。
- **诚实划界**：`aggregate_round`（certification_stats.py）的 arm 对比**已经**是"focal vs reference
  arm"的相对量（certification.py:319-323 `_group_key`，focal/reference_group）——即**证据层已内建
  一种"相对参照"**。M24 若把 negative/positive control 映成 aggregator 的 reference arm，那是**用现有
  机制**，非改 compiler。但"读出值本身除以 positive control"这种**逐值归一**必须在 domain 层做完，
  **不得**塞进 aggregate_round。

**建议落点 / 风险**

- **落点**：逐值归一化 → adapters/wet 或 domain provider 层（进 ingest 前完成）；"对照即参照 arm" →
  复用 `AggregatedCertification` 的 focal/reference（不改 compiler）。
- **风险（最需警惕）**：把"减 baseline / 除 control"写进 `certification_stats.aggregate_round` 或
  `certification.decide` = 碰红线。M24 建设者必须把归一化**关在 domain 层**。

---

### 问 6 — `dry_species` hook 是否 chemistry-leaky？（M24 最尖锐契约测试点）

**现状锚点**

- hook 签名：`DomainProvider.dry_species(self) -> Mapping[str, DrySpecies]`
  （`expos/adapters/domain_provider.py:112-115`），docstring 直书"name → DrySpecies
  (**geometry/Z-matrix + charge/spin**)"。
- `DrySpecies`（domain_provider.py:63-77）：`zmatrix: str`（**必填、无默认**）+ `charge: int=0` +
  `spin: int=0` + `meta`。docstring：一个 small-molecule geometry，dry(PySCF)腿把它变成
  descriptor(dipole proxy)。
- 两 provider 实现：
  - solvent：`{name: DrySpecies(zmatrix=zmat, charge, spin) for ... in SOLVENTS.items()}`
    （`providers/solvent_screen.py:79-83`）
  - catalyst：同形，`from CATALYSTS`（`providers/catalyst_screen.py:74-78`）
  两者都返回**真 Z-matrix 几何**。
- **出生治理强制它**：`check_complete()`（domain_provider.py:173-226）第一条硬不变量
  `dry_keys == wet_keys`（domain_provider.py:194-201）——`dry_species()` 的 key 集合**必须等于**
  `wet_coords()` 的 level 集合。且 `dry_species` 是 `@abc.abstractmethod`（domain_provider.py:112），
  **不实现就无法实例化**（构造即 `TypeError`）。
- **但（关键取证，见 §0.4）**：`dry_species()` 的返回值在**整个运行期无人消费**——`grep` 全 `expos/`，
  `dry_species()` 只被 `check_complete`（governance）与两 provider 自身调用。真正喂 dry 腿几何的是
  `mcl._candidate_params → catalyst_params(level)`（catalysts.py:128），**直读 catalysts.py，不经
  provider**。即 provider 的 `dry_species` 今天是**纯出生治理装饰**，运行期是死代码路径。

**诚实评估：是的，`dry_species` 是 chemistry-leaky，且这是 M24 最尖锐的契约测试点**

- hook 的**类型本身**（`DrySpecies.zmatrix: str` 必填）编码了"每个设计 level 都有一个小分子几何"这一
  **化学专属假设**。生物 construct **没有几何**——没有 Z-matrix、没有 charge/spin。
- 因为 `dry_species` 是 `@abstractmethod` **且** `check_complete` 要求 `dry_keys == wet_keys`，一个
  生物 provider **为了通过出生治理，被迫为每条 construct 编造一个假 Z-matrix**——而这个 Z-matrix
  运行期根本不会被读（死值）。这是"契约把 chemistry 假设焊进生物域"的教科书式泄漏。
- 章程 §契约 v2 适配面的预判精准命中：`dry_species` 要么被**重解**为"构建 → dry 输入"（把 `DrySpecies`
  从"几何三元组"泛化成"dry 腿输入载荷"的中性容器），要么**证明契约需 v3**。

**对 M24 的含义 + 建议落点/风险**

- **这是发现，应如实上报**（章程 §红线：若为生物必须改这些即证 abstraction 不够干净——是发现不是偷改）。
  `dry_species`/`DrySpecies` 的化学特化是 M20 catalyst"复用 PySCF+几何"掩盖的债：catalyst 能塞真
  Z-matrix，所以没暴露；生物是第一个**结构上无几何**的域，正好是 abstraction 干净度的判官。
- **两条出路**（供双侧合读裁，A 侧 dry 适配实况为准）：
  1. **契约 v3 / 泛化 `DrySpecies`**：把 `DrySpecies` 改成中性的 "dry-input payload"（如
    `dry_input: Mapping[str, Any]` 或一个带 `kind` 判别的 union），几何降为化学域的一种 payload 形。
    这**碰 `domain_provider.py`**——但 `domain_provider.py` 是**契约层**，改它是"契约演进"（公开的 v3
    决策），**不是偷改 kernel**。kernel（objects/claims/knowledge）仍零改。
  2. **让 `dry_species` 可选 / 出生治理放宽**：既然运行期不消费它，可把 dry↔wet key 一致性校验从
    "geometry 必备"降为"level 集合一致"（用一个 geometry-free 的 dry-level 声明）。这也碰
    `domain_provider.py`。
- **风险**：无论哪条，都要碰 `expos/adapters/domain_provider.py`（契约 ABC + check_complete）。这**不是
  kernel 红线**（domain_provider 是 adapters 层的域契约），但它是**跨双域的共享契约**——改它会波及
  solvent/catalyst 两 provider，须双侧合读、走 v3 台账。**M24 的最大架构决策就在这里**：承认
  `dry_species` chemistry-leaky，把它作为契约 v3 的立项证据。

---

## 2. M24 绝对红线文件清单（碰即违用户裁——kernel/planner-evidence/ledger 零改动是硬门）

以下文件**语义**在 M24 内**不得改动**（章程 §红线 + CHECKPOINTS「kernel 一字未动」双签）：

**kernel（七概念 + 证据/知识/事件语义）**
- `expos/kernel/objects.py` —— Candidate/Observation/Control/Decision 等 schema。**尤其 `Control.kind`
  的 Literal、`Candidate` 顶层字段**：碰即 kernel schema 改动。
- `expos/kernel/claims.py` —— ClaimRecord/ClaimDelta/Ledger/Provenance/StatisticSnapshot/apply_claim_deltas。
- `expos/kernel/knowledge.py` —— compile_knowledge（知识编译）。
- `expos/kernel/store.py` / `lifecycle.py` / `overrides.py` —— 事件/裁决/改判/去重/分叉检测语义。

**evidence-compiler（证据聚合与晋升，纯函数红线）**
- `expos/qc/certification_stats.py` —— aggregate_round（e 值/效应聚合，跨域同一注册 fn）。
- `expos/planner/certification.py` —— decide（K5 纯函数）。
- `expos/planner/promotion.py` —— decide（晋升门纯函数）。
- `expos/planner/arbiter.py` / `stages.py` / `policy.py` —— 规划裁决语义。
- `expos/qc/checks.py` / `attribution.py` / `failure_model.py` / `stats.py` —— QC 裁决/归因内核
  （**软红线**：归一化若要看跨记录对照可能诱惑改这里——章程明言"碰即红旗"，M24 应把归一化关在 domain 层）。

**契约层（非 kernel 红线，但改动 = 公开的 v3 决策，须双侧合读）**
- `expos/adapters/domain_provider.py` —— `DomainProvider` ABC / `DrySpecies` / `check_complete`。
  **M24 很可能被迫碰这里（见 Q6）——这不是偷改，是契约 v3 立项，须走台账双签。**

**允许新增/改动（M24 合法工作面）**
- `expos/adapters/`（dry 新 adapter、wet reader 板级 fault、providers/ 新 bio provider、ingest 分派）
- `expos/domain.py`（加性 schema：新 execution_kind 值、controls 声明、UNIT_VOCABULARY 扩项——**加性**）
- `expos/mcl.py`（编排层：dry 腿可插拔化、_wet_experiment 下发 controls、bio bindings——**非 kernel，
  但守 EXP011 别让域字面量回流**）
- `domains/cell_free_expression_screen.yaml`（新域声明）
- `tests/`（判别测试）

---

## 3. 最小 build 切入顺序建议（先证闭环，勿铺开）

按"红线风险从低到高、依赖从底到上"排序：

1. **先裁 `dry_species` 契约（Q6）**——这是**前置阻塞点**：不先决定"泛化 DrySpecies（v3）还是编造死
   Z-matrix"，bio provider 连出生治理都过不了。**建议：作为 M24 第一个双侧合读裁决**（承认 leaky，
   走 v3 或 optional-hook）。在此之前的所有下游工作都悬在这个决定上。
2. **bio dry proxy 作同步 adapter（Q1）**——实现 sequence-feature→scalar 的同步类（复用
   `ExecutionAdapter.execute` 面 + 通用 `raw_to_observations`），本机秒级、无 sbatch。
3. **MCL dry 腿可插拔化（Q1，B 侧编排）**——把 `mcl.py:936` 的 `PySCFDryAdapter` 直构 + `mcl.py:1218`
   的 PySCF ingest 改成按 execution_kind/小注册表分派。**这是 loop 层真实工作量**，不是 yaml 改。
4. **construct params 映射 + bindings 扩展（Q2）**——`_candidate_params` 新 bio 分支（sequence/组件/
   lineage 进 params 自由 dict，kernel 零改）；若多轴 feature，扩 `_domain_bindings` 的坐标轴假设。
5. **bio 真值面 + wet reader（Q3/Q4）**——`TRUTH_PROFILES` 加 `expression_high/flipped/flat`
   （只 mu 变的 K-D 纪律，sim_reader.py），复用 flat null 面；replicate+interleave 直接用；板级 offset
   若要建模再加 reader fault。
6. **三对照 + 归一化（Q3/Q5）**——domain 声明三对照 → `_wet_experiment` 下发 controls → wet 腿消费；
   归一化**关在 domain 层**（进 ingest 前完成），或映成 aggregator 的 reference arm。**绝不碰
   certification_stats/certification/knowledge。**
7. **domain yaml + 判别测试 + 门 12 全环**——`cell_free_expression_screen.yaml` + provider + 一次全环跑通
   （expression_high 面，rounds=2/replicates≈8），门 12 CHAIN COMPLETE，kernel diff 为空即成立。

**每步验收锚**：kernel 三文件（objects/claims/knowledge）+ qc/certification_stats + planner/certification
的 `git diff` 必须为空（若非空 → 停，作为 abstraction 发现上报）。

---

## 4. 附：一句话结论汇总

1. **dry 腿**：契约层有同步 `ExecutionAdapter.execute` 面（base.py:69 / SimulatorBase）可容纳非 PySCF
   轻量 dry 腿，但 **MCL 的 dry 腿是硬接线 PySCF**（mcl.py:936/1218），生物 dry 腿需**新增同步 adapter +
   把 MCL dry 腿改可插拔**（loop 编排层，非 kernel）。
2. **construct 承载**：`Candidate.params` 是自由 `dict[str,Any]`（objects.py:194）+ 已有
   `parent_obs_id` lineage 位——sequence/组件/lineage **可直接进 params，kernel 零改**（与 catalyst 塞
   zmatrix 同构）。
3. **controls**：kernel `Control` 已支持 negative/positive + 各自 expected_band，但**无 reference 字面量、
   域声明只支持单 sentinel 块、且 MCL 从不下发 controls**——三对照需 domain 层声明 + mcl 下发（非 kernel）。
4. **batch/plate**：replicate+interleave 板序去混淆（screen._replicate_order）**直接可复用**；但 reader
   只有**逐孔累积** calibration_drift，**无板级台阶 offset**——板间批次偏移建议**新增一个 reader fault**
   （adapters/wet 加性），比硬套 drift 更诚实。
5. **normalization**：归一化**必须关在 domain/adapter 层**（进 ingest 前完成）或映成 aggregator 的
   reference arm；**碰 certification_stats/certification/knowledge = 红线**。
6. **dry_species hook**：**确认 chemistry-leaky**——`DrySpecies.zmatrix` 必填 + `@abstractmethod` +
   check_complete 强制 dry↔wet key 一致，逼生物 provider 为每条 construct 编造**运行期无人消费的死
   Z-matrix**（provider hook 目前仅出生治理消费，运行期零调用）。这是 M24 **最尖锐的契约测试点**，应作为
   **契约 v3 立项证据**双侧合读裁。

**最大架构风险（我的判断）**：不是任何单一 hook，而是**"M21 契约与运行期脱节"叠加"MCL dry 腿硬接线
PySCF"**这两点的合流。M21 把换域固化成 provider 五 hook，但**运行期根本不消费 provider**（直读
catalysts.py / 直读 TRUTH_PROFILES），而 MCL dry 腿又是具体 `PySCFDryAdapter`。结果是：生物域会在
**两个层面同时暴露化学假设**——(a) 出生治理层 `dry_species` 强制假几何（Q6，契约 leaky），(b) 运行执行层
dry 腿硬绑 PySCF（Q1，编排 leaky）。catalyst 因"复用 PySCF+几何"把这两点都掩盖了；生物是第一个证伪它们的
域。M24 若不先把这两处理清（契约 v3 决定 + dry 腿可插拔），会在"改哪层才不碰 kernel"上反复——而这恰恰是
本域被钦定为"domain abstraction 干净度判官"要测的东西。诚实结论：**kernel 大概率能守住零改动，但
`domain_provider.py` 契约层与 `mcl.py` 编排层几乎必然要改，且这不是失败——是 M24 该产出的发现本身。**
