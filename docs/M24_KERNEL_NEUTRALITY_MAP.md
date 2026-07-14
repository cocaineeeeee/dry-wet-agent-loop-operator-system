# M24 内核中立性地图 — B 侧（kernel/planner/qc/mcl）生物适配面取证

> 只读调查产物（2026-07-14）。核心问题：expos 是真正 domain-neutral 的科学 runtime，
> 还是"披着抽象的化学 runtime"？硬红线：**kernel / planner / evidence-compiler
> (K-B `aggregate_round`) / ledger 为生物零语义改动**。若生物逼这些改，即证抽象不干净——
> 本文件如实报告为 FINDING，不偷改。
>
> 判词档位：`clean`（可零改动复用）/ `leaky-cosmetic`（仅名字/表层，非契约破坏）/
> `needs-vN`（真契约变更）。每条判词附 file:line 证据。

---

## 结论速览

| # | 问题 | 判词 | 触及层 |
|---|------|------|--------|
| Q1 | 非 PySCF 同步轻量 dry 腿 | **clean（契约层）+ leaky（mcl 接线层）** | 编排层 mcl，非 kernel |
| Q2 | construct 骑 candidate.params | **clean** | kernel 零改动 |
| Q3 | 三对照复用 sentinel | **clean（kernel/compiler）+ 新域层 schema** | provider/yaml + mcl 接线，非 kernel |
| Q4 | 批次偏移复用 calibration_drift | **clean** | 复用现存 |
| Q5 | 归一化是否碰 evidence compiler | **clean（红线守住）** | 归一化**必须**在域 wet/QC 层 |
| Q6 | dry_species hook 是否 chemistry-leaky | **needs-v3（payload 类型）+ leaky-cosmetic（名字）** | provider 契约层，非 kernel |

**红线判定：生物不逼 kernel / planner / evidence-compiler / ledger 任何语义改动**
（前提是 Q5 归一化守在域层、Q1 dry 腿在 mcl 编排层做注入化）。抽象在**内核边界**是干净的；
真正的适配缝有两处且都在**允许的域/编排层**：(a) mcl dry 腿硬绑 PySCF（编排 leak），
(b) provider 契约 `DrySpecies` payload 是几何专化类型（需 v3，provider 层）。二者皆**非**内核红线破坏，
但都须响亮报告。

---

## Q1 — 非 PySCF、同步、轻量的 dry 腿：adapter 契约能否容纳？

**判词：契约层 clean（同步 execute 先例存在）；mcl 接线层 leaky（dry 腿硬绑 `PySCFDryAdapter`）——需 B 侧编排重构，但不触 kernel。**

证据：
- **同步 execute 先例确实存在**：`SimulatorBase.execute(exp, rng) -> ExecutionResult`
  （`expos/adapters/sim_base.py:92-102`）是同步契约，`CrystalSim`/`CoatingSim`/`bench_manual`
  都走它（`sim_crystal.py:18`、`sim_coating.py:22`、`bench_manual.py:62`）。域契约已把这枚举化：
  `ExecutionKind.sim_execute`（`expos/domain.py:214-224`）= "synchronous in-silico simulator"。
  故 adapter 契约面**允许**一个非 PySCF、同步、秒级、无 sbatch 的 dry 腿。
- **但 MCL 的 dry 腿不是通过 adapter 契约注入的，而是硬编码 `PySCFDryAdapter`**：
  - `_run_round` 形参类型写死 `dry_adapter: PySCFDryAdapter`（`expos/mcl.py:1131`）；
  - 构造点写死 `PySCFDryAdapter(jobs_root=..., poll_interval_s=0.1)`（`expos/mcl.py:936`）；
  - dry 实验的 `execution_req=ExecutionReq(adapter="pyscf_dry")` 写死（`expos/mcl.py:669`）；
  - dry 腿的候选 params 由 `_candidate_params` → `catalyst_params(level)` 产**几何**
    （`expos/mcl.py:376-383`，`catalysts.py:128` 产 explicit `geometry` key 喂 PySCF）。
- **关键中立性**：dry adapter 只吐 `RawResult`/`DryRawResult` 物料，经 `dry_raw_to_observations`
  变成 `ObservationObject`（metric 为开放字符串 `MeasuredResult.metric`，`objects.py:298`）。
  sequence-feature 预测器吐自己的 metric（如 `cai_proxy`）完全合法——**kernel 不感知 dry 腿是什么**。
- `build_adapter` 对 `PySCFDryAdapter` 显式响亮拒绝（`domain.py:588-597`：async dual-leg），
  证明"dry 腿走 mcl、不走 build_adapter"是既定纪律。

**裁定**：生物的同步 dry 腿在 **ExecutionAdapter 契约层是 clean 的**（`sim_execute` 先例现成）；
需要的改动是**把 mcl 的 dry 腿从"硬绑 `PySCFDryAdapter` + `catalyst_params` 产几何"改为可注入的
dry-adapter 协议**——这是**编排层（mcl bindings）** build 项，**不触 kernel/planner/compiler/ledger**。

---

## Q2 — construct 物件骑 candidate.params：kernel 是否需改？

**判词：clean。kernel 零改动。**

证据：
- `Candidate.params: dict[str, Any]`（`expos/kernel/objects.py:196`）——**全开放 dict**，无化学形状。
  promoter/RBS/CDS variant/条件 组件塞进去即可。
- lineage（parent–child）已有一等载体：`Candidate.parent_obs_id: str | None`
  （`objects.py:201`）——construct 的亲本谱系有落点。
- 观测侧同样开放：`ObservationObject.cand_id`（`objects.py:376`）+ `MeasuredResult.metric`
  开放字符串（`objects.py:298`）。
- 唯一读 params 具体键的是 **PySCF 干腿**（`adapter.py:171-183` 读 `geometry/charge/spin/...`）——
  那是 adapter 私有约定，**非 kernel**；生物干腿读自己的键。

**裁定**：construct（序列组件 + lineage）骑 `candidate.params` 与 `parent_obs_id`，**kernel 一字不改**。

---

## Q3 — 三类对照（positive/negative/reference）：复用 sentinel 机制？

**判词：kernel/compiler 层 clean（对照已是一等公民）；三对照家族的声明是新域层 schema（provider/yaml + mcl 接线），非 kernel。**

证据（对照在 kernel + compiler 已是一等公民）：
- `Control` 内核对象（`objects.py:203-207`）：`kind: Literal["sentinel","negative","positive"]`、
  `params: dict[str,Any]`（开放）、`expected_band: tuple[float,float]|None`。
  **negative / positive 直接就有**；biology 的 "reference（中）" 对照可映射为
  `kind="sentinel"` + 一条 `expected_band`（中段期望带）——**无需给 Literal 加 "reference"**。
- 观测侧对照一等公民：`ObservationObject.is_control` / `control_id`（`objects.py:376-405`）。
- **QC 层已按对照裁决**：`control_band = {c.control_id: c.expected_band ...}`（`qc/checks.py:392`），
  `sentinel_band` / `sentinel_control_band` 检查（`checks.py:820-861`）——域中立，读 `expected_band`。
- **evidence-compiler 已把对照当 arm 键**：`_group_key(obs) = obs.control_id if obs.is_control
  else obs.cand_id`（`certification_stats.py:455-458`）；`ClaimHead.focal_group/reference_group`
  可直接引用 control_id 作对照臂（`certification_stats.py:262-272`）。**compiler 零改动**。

证据（新域层缺口，合法新增）：
- **MCL 循环当前完全不注入任何对照**：`_dry_experiment`/`_wet_experiment`
  （`mcl.py:648-690`）只填 `candidates=cands`，无 `controls=`；全 mcl.py 无 `Control(`/`sentinel` 接线
  （grep 证实为空）。即：kernel 支持对照，但**编排循环尚未把对照打进实验+板位**。
- 域 schema 侧 `SentinelSpec`（`domain.py:142-145`）只声明**单一** sentinel 家族（n/params/band）——
  生物要 positive/negative/reference **三家族**，需域层 schema 扩展（provider 声明对照构建 + 期望带，
  或 yaml 多对照块）。

**裁定**：三对照的**机制**（Control 物件 / is_control / expected_band / QC control_band /
compiler arm 键）**全部现成，kernel 与 compiler 零改动**；需要新增的是**域层对照家族声明**
（provider/yaml）**与 mcl 把 Control 打进实验+layout 的编排接线**——皆新域/编排层，非 kernel。

---

## Q4 — 批次/板间偏移：复用 reader `calibration_drift` artifact？

**判词：clean。复用现存机制，域中立。**

证据：
- 标定漂移模型域中立：`reading = truth * gain + offset + noise`，逐测井累积
  （`sim_reader.py:200`、`:368-378` 累积并打 `calibration_drift` artifact 标签）——
  它作用在 `truth` 上，**与坐标物理含义无关**（板间 offset ≈ 标定 offset）。
- 批次标签生成域中立：`batch = f"R{round}-B{(row+col)%n}"`（`sim_base.py:124`），
  按空间棋盘格与 capture_index/is_edge 双解耦（`sim_base.py:121-124`）。
- compiler 侧已有域中立的板序混淆守卫：`plate_order_balance = corr(capture_index, arm 指示)`
  （`certification_stats.py:525-554`），超界即 confound-suspect 拒裁（`:332-366`, `PLATE_ORDER_BALANCE_MAX`
  `:135`）——正是"标定漂移沿测量序伪造方向信号"的通用守卫，生物批次效应直接受同一守卫保护。

**裁定**：生物 replicate + 批次校正**复用 reader `calibration_drift` + batch + plate_order_balance**
机制，**零新机制、零 kernel/compiler 改动**。

---

## Q5 — 归一化（读出相对对照归一）：域 QC 层还是碰 evidence compiler？【红线】

**判词：clean——红线守住。归一化必须住在域 wet/QC 层；compiler 不得触碰，也不需触碰。**

证据（compiler 的 TRUTH-BLIND / 窄输入契约）：
- `aggregate_round` 的输入**只有** TRUSTED 观测 + 统计 config + 被裁 claim head：
  模块级 TRUTH-BLIND 不变量白纸黑字（`certification_stats.py:63-68`：
  "inputs are ONLY the TRUSTED observation set + statistical config + the claim head"）。
- 它只读 `obs.result.value`（`certification_stats.py:513,733,851`）、公开 arm 键（`_group_key` `:455`），
  计算 focal[i]−reference[i] 配对差（`:733`）。**不读任何域字段、不读对照的"归一含义"**。

两种归一形态与其合法落点：
- **加性/对照差归一**（减 negative control）：**compiler 原生就是这形状**——把
  `ClaimHead.reference_group` 设为 negative control 臂，compiler 直接算 focal−reference 配对差
  （`certification_stats.py:262-272,733`）。**连新层都不需要。**
- **比值/fold-change 归一**（除以 positive control）：非线性变换。**必须**在**域 wet/QC 层**把
  `MeasuredResult.value` 预先算成归一后量（如 log-fold-change vs 板级 negative-control 均值），
  compiler 再对归一后的值跑它标准的配对对比。**若把比值归一塞进 `aggregate_round` = 红旗**
  （破坏 TRUTH-BLIND 窄输入契约）。

**裁定（红线守住）**：归一化**不需要、也不得**进 evidence compiler。它**必须住在域 wet/QC 层**
（产出已归一的 `MeasuredResult.value`，或对照差直接用 `reference_group` 表达）。
compiler 保持域盲——**红线未被生物逼破**。这是一条**放置裁定**，不是 compiler 改动。

---

## Q6 — 最尖锐：`dry_species` hook 是否 chemistry-leaky？【本域契约判官】

**判词：名字 leaky-cosmetic（可 rename `dry_entities`，无实害）；但 payload 返回类型 `DrySpecies`
是真几何/Z-matrix 契约 → needs-v3（provider 契约层，非 kernel）。**

证据（返回类型是硬化学契约）：
- hook 签名：`dry_species(self) -> Mapping[str, DrySpecies]`（`domain_provider.py:112-115`），
  docstring 明写 "geometry/Z-matrix + charge/spin ... The dry leg's discrete-level table"。
- `DrySpecies` 冻结 dataclass **强制**化学字段：`zmatrix: str`（**必填**）、`charge: int=0`、
  `spin: int=0`（`domain_provider.py:63-77`），docstring："a small-molecule geometry that the
  dry (PySCF) leg turns into a descriptor (dipole proxy)"。**生物无几何——construct/序列无法诚实填
  `zmatrix`**（把序列硬塞进 `zmatrix` 字段是章程明禁的"biased proxy 须诚实标注"式作弊）。
- 两化学 provider 都按几何三元组填：`solvent_screen.py:79-83`（`DrySpecies(zmatrix=zmat,...)`
  from `SOLVENTS`）、`catalyst_screen.py:74-78`（from `CATALYSTS`）。

证据（今天 payload 几乎"只有 key 承重"，故 v3 面很小）：
- `check_complete` **只比 key 集合**，不看 payload：`dry_keys = set(self.dry_species());
  wet_keys = set(self.wet_coords()); if dry_keys != wet_keys: raise`
  （`domain_provider.py:194-201`）——**从不读 `zmatrix/charge/spin`**。
- **`dry_species`/`DrySpecies` 在 providers + `check_complete` 之外零消费**（grep 证实：
  mcl、dry adapter 均不读它）。MCL 干腿的几何来自 `catalyst_params`（`mcl.py:376-383`），
  PySCF adapter 读 `params["geometry"]`（`adapter.py:171-183`）——**不经 hook**。

**裁定**：
- hook **语义名** = "干腿候选实体 + 其 compute payload"，construct/序列能填"实体"角色 →
  名字层 **leaky-cosmetic**（可 rename `dry_entities`，不构成 v3 理由）。
- hook **返回类型** `DrySpecies(zmatrix, charge, spin)` 是**硬编码几何/Z-matrix payload 契约**，
  生物无法诚实实现 → **真契约变更 needs-v3**：把干腿 payload 从 `DrySpecies(zmatrix,charge,spin)`
  泛化为开放/带类型的 compute payload（如 `DryEntity` 带不透明 `payload: Mapping` 或域特化类型）。
- **但 v3 完全局限在 provider 契约层**（`domain_provider.py` + 两个 chemistry provider），
  正是章程允许的"新域层 artifact"区。**kernel / planner / compiler / ledger 一字不改。**
  故 Q6 **不是**红线破坏，而是**一个诚实的、局限于 provider 契约层的 v3 需求**。

---

## §Convergence 收敛

### (a) 诚实清单：可零改动复用 / 需新域层 artifact / 是否逼 kernel-compiler 改（= 抽象洁净度 FINDING）

**A. 生物可零改动复用（kernel/compiler/ledger 原样）**
- `Candidate.params`（开放 dict）承载 construct 组件 + `parent_obs_id` 承载 lineage（`objects.py:196,201`）。
- 对照的**机制**：`Control`（negative/positive 现成 + 开放 params + expected_band，`objects.py:203-207`）、
  `is_control`/`control_id`（`objects.py:376-405`）、QC `control_band`（`checks.py:392`）、
  compiler 把对照当 arm 键（`certification_stats.py:455-458`）。
- 批次/漂移：`calibration_drift` + batch 标签 + `plate_order_balance` 守卫
  （`sim_reader.py:200,368-378`；`sim_base.py:124`；`certification_stats.py:525-554`）。
- 归一化的**对照差**形态：`ClaimHead.reference_group` 直接表达（`certification_stats.py:262-272`）。
- 整条 K-A ledger（`claims.py`）+ K-B `aggregate_round`（`certification_stats.py`）**域盲**——
  claim_id/status/evidence/e-value/provenance 无一化学假设。
- 同步 execute 契约先例（`sim_base.py:92`，`ExecutionKind.sim_execute` `domain.py:214`）容纳非 PySCF 干腿。

**B. 需新增的域层 / 编排层 artifact（章程允许区）**
- **新 dry adapter**：sequence-feature 预测器（GC/CAI/RBS/RNA-fold proxy），走同步 `execute`。【A 侧，new】
- **mcl dry 腿注入化**：把 `dry_adapter: PySCFDryAdapter`（硬类型，`mcl.py:1131`）+ 硬构造
  （`mcl.py:936`）+ `_candidate_params`→`catalyst_params` 产几何（`mcl.py:376-383`）改为可注入 dry-adapter
  协议 + 中立 params 构建。【B 侧编排，new】
- **mcl 对照接线**：把 `Control`（positive/negative/reference）打进 `_wet_experiment` + layout
  （当前 `mcl.py:648-690` 完全不注入对照）。【B 侧编排，new】
- **域 schema 对照家族**：`SentinelSpec` 单家族（`domain.py:142-145`）→ 声明三对照家族 + 期望带
  （provider 或 yaml）。【域 schema，new】
- **归一化 policy** 落在域 wet/QC 层（产归一后 `MeasuredResult.value`）。【域层，new】
- **域契约 v3**：`DrySpecies` payload 泛化（见下）。【provider 契约层，new】

**C. 逼 kernel / planner / evidence-compiler / ledger 改的项（= 抽象不洁的 FINDING）**
- **无。** 遍查 `kernel/objects.py`、`kernel/claims.py`、`qc/certification_stats.py` 未见任何一处被生物
  逼出语义改动。唯二边界候选均**不构成**逼改：
  1. `Control.kind` 缺 `"reference"` 字面量（`objects.py:205`）——reference 复用 `"sentinel"`+band，
     **不需**改 kernel enum。
  2. 归一化——**必须**且**能够**守在域 wet/QC 层，compiler 的 TRUTH-BLIND 窄输入契约
     （`certification_stats.py:63-68`）不被触碰。
- **FINDING（须响亮记，非红线破坏）**：唯一"真契约变更"是 `dry_species` 的 payload 类型
  `DrySpecies(zmatrix,charge,spin)` 化学专化（`domain_provider.py:63-77`）→ 需 v3，但**局限 provider
  契约层，kernel 一字不动**。这**证明抽象在内核边界是干净的**：生物把化学专化逼到了**provider 契约**
  这一预留的域层，而非 kernel/compiler/ledger。

**核心问题回答**：expos 在 **kernel / planner / evidence-compiler / ledger 边界是真正 domain-neutral 的
科学 runtime**（红线守住，零语义改动）；"化学味"只残留在两处**域/编排层**（mcl dry 腿硬绑 PySCF、
provider `DrySpecies` 几何 payload），二者皆在章程预留的可改区，且都须响亮报告、不得偷改。

### (b) 域契约 v3 是否需要 + 最小形状

**需要 v3——但极小，且完全局限在 provider 契约层（不触 kernel）。** 最小形状：

1. **干腿 payload 泛化**（唯一硬变更）：
   `DrySpecies(zmatrix: str, charge, spin)` → 引入中立 `DryEntity`（或就地放宽 `DrySpecies`）：
   把几何三元组降为**化学 provider 的一种特化**，契约面只要求"每个 level 一份不透明 compute payload"，
   如 `DryEntity(payload: Mapping[str, object])`；chemistry provider 的 payload =
   `{zmatrix, charge, spin}`，biology provider 的 payload = `{sequence, components, ...}`。
   `check_complete` 现状**只比 key 集合**（`domain_provider.py:194-201`），故 payload 泛化对现有校验零冲击。
2. **hook 改名（可选，cosmetic）**：`dry_species` → `dry_entities`；纯语义清洁，无功能变更。
3. **对照家族 hook（可选，若走 provider 而非 yaml）**：新增 `controls() -> Sequence[ControlSpec]`
   声明 positive/negative/reference + 期望带，让 mcl 从 provider 取对照——把 Q3 的"新域层声明"
   收进契约。

v3 **不涉及** kernel/planner/compiler/ledger 任何 schema；`wet_coords`/`truth_profiles`/`seed_claims`/
`validate_yaml` 四 hook 生物可原样实现（wet_coords=构建设计坐标，truth_profiles=
expression_high/flipped/flat，seed_claims="high-[feature] 构建表达更高"）。

### (c) B 侧 build 工单（逐项标 复用现存 / 新增）

| 工单 | 层 | 标记 |
|------|-----|------|
| mcl dry 腿改为可注入 dry-adapter 协议（去掉 `PySCFDryAdapter` 硬类型 `mcl.py:1131`/硬构造 `:936`） | 编排 mcl | **new** |
| `_candidate_params` 去几何化：descriptor 域不再无条件 `catalyst_params`（`mcl.py:376-383`），按 provider payload 构建 | 编排 mcl | **new** |
| mcl 把 `Control`(positive/negative/reference) 注入 `_wet_experiment`+layout（当前 `mcl.py:648-690` 无对照） | 编排 mcl | **new** |
| 域 schema：`SentinelSpec` 单家族 → 三对照家族 + 期望带声明（`domain.py:142-145`） | 域 schema | **new**（唯一"确不可复用"的生物字段：三对照家族） |
| 域契约 v3：`DrySpecies`→`DryEntity` payload 泛化 + `dry_species`→`dry_entities` 改名（`domain_provider.py:63-77,112-115`） | provider 契约 | **new**（provider 层，非 kernel） |
| 归一化 QC 放置裁定：**住域 wet/QC 层**产归一后 `MeasuredResult.value`；对照差用 `reference_group` 表达；**禁入 `aggregate_round`** | 域 wet/QC | **裁定=复用 compiler 原生对比**（compiler 零改动） |
| 批次偏移：复用 `calibration_drift`+batch+`plate_order_balance` | wet/QC/compiler | **复用现存** |
| construct/lineage：复用 `Candidate.params`+`parent_obs_id` | kernel | **复用现存（零改动）** |
| ledger / K-A / K-B `aggregate_round` | kernel/compiler | **复用现存（零改动，红线）** |

**归一化-QC 放置裁定（明文）**：生物 assay 归一化**判定住在域 wet/QC 层**（在观测进入 TRUSTED、
到达 compiler 之前完成），evidence compiler `aggregate_round` 保持 TRUTH-BLIND 窄输入
（`certification_stats.py:63-68`）不改。对照差归一用 `ClaimHead.reference_group` 表达即可，连域层变换都省。
