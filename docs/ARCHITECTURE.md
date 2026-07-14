# 闭环实验 OS —— 完整架构蓝图

> 项目代号 `expos`。一个面向**安全、非生物、物理材料实验**（结晶生长、涂层干燥、光学测量等）的闭环实验操作层（operating layer）。
> 本文件是实现的唯一权威蓝图；实现与本文件冲突时，先改文件、再改代码。
> 进度与验收记录见根目录 `CHECKPOINTS.md`；外部参考系统清单见 `docs/REFERENCE_MAP.md`。

---

## 1. 设计公理（第一性原理）

1. **OS ≠ pipeline**：内核只包含两个持久对象（ExperimentObject / ObservationObject）+ 追加式事件日志 + 轮次状态机。设计器、执行器、QC、归因、规划器全部是经由协议注册的可替换模块。换实验领域 = 换一个 YAML 配置 + 一个 adapter，内核零改动。
2. **观测默认是"待裁决证据"，不是数据**：任何测量值进入系统时 `trust = PENDING`，必须经 QC 裁决后才被路由。可疑/失败观测**结构上不可能**进入响应模型——这不是某模块的选项，而是内核路由语义。【os-soft 限定（R2 H-3 修订）：软信任对照臂对 routing=QUARANTINE（suspicion∈[0.3,0.6)）的观测在**聚合层内存态**以膨胀 alpha 降权复归训练；落盘 trust/routing 不变、内核路由语义不动。"结构上不可能"的强表述在全部臂上仅对 SUSPECT→TO_FAILURE_MODEL 与 FAILED 无条件成立。】
3. **双模型**：可信观测更新**响应模型**（条件 → 性能）；可疑/失败观测更新**失败模型**（provenance 特征 → 伪影概率）。两者共同输入规划器。模型更新由 **trust 裁决**驱动、与 routing 处置正交：失败模型的**正例只来自 SUSPECT/FAILED**（含被 QUARANTINE 者），TRUSTED 仅提供曝险分母计数、其响应值绝不进失败模型；反向同理——SUSPECT/FAILED 的数值结构上进不了响应模型【os-soft 限定同公理 2：QUARANTINE 带的 SUSPECT 观测在该对照臂经聚合层降权复归，故此句的无条件形式仅适用于非 os-soft 臂与 TO_FAILURE_MODEL/FAILED 路由】。
4. **位置与来源是一等公民**：绝大多数伪影（边缘蒸发、温度梯度、眩光、灰尘、批次漂移）与 layout/provenance 相关。布局分配必须由系统控制：分层随机化 + 区组 + 哨兵对照，否则失败归因不可辨识。
5. **失败不是垃圾桶而是资产**：被隔离的观测永久保留、可被后续证据改判；失败模型的输出直接变成下一轮的设计约束与专用动作。
6. **仿真真值与 OS 严格隔离**：模拟器（`adapters/sim_*`，唯一生成方）把每孔真值写入 sidecar 文件（`truth/`）；`qc/`、`models/`、`planner/`、`agent/` 一律不得读取，`loop.py` 只把 truth_records 当**不透明块**原样落盘、不解析不转发；只有事后评分脚本读它。这是"系统没被伪影骗到"这一论断可以被定量证明的前提。
7. **Agent 只有建议权，没有裁决权**：Agent Orchestrator 负责目标翻译、先验与理由提议、QC/归因的人类可读解释、下轮动作提案，一切产出以 DecisionRecord 写入事件日志。它**不是内核**：不能绕过 trust/routing 语义，不能触碰可信观测与响应模型训练集（API 层面只给只读视图 + 提案队列），其提案必须经规划器/内核校验后才生效。

## 2. 演示领域

- **主域 `crystal`**：透明安全盐类（明矾/硝酸钾）水溶液蒸发结晶，6×8=48 孔透明板。
  目标：最大化单晶质量指数——基于晶体尺寸分布（CSD）三要素的归一化组合：低成核密度 × 大平均晶粒 × 窄分布（CV），覆盖率作辅助项（物理依据见 REFERENCE_MAP §9.5）。
  变量：过饱和度、微量添加剂比例（log 尺度）、降温/蒸发速率档、是否投籽晶（二元类别）。
  伪影库：边缘蒸发、板下温度梯度、拍摄眩光、灰尘诱导成核、溶液批次漂移、仪器随时间漂移。
- **副域 `coating`**：液滴干燥沉积均匀性（咖啡环效应）。变量：浓度、基底倾角、干燥温度、表面活性剂比例。真值面以"垂直平流/扩散时间尺度比"为核心旋钮（控制沉积从强环连续过渡到均匀，Phys. Rev. Fluids 2024），均匀性度量参照 G 参数判据（REFERENCE_MAP §9.2）。共用全部伪影注入器。**用途：证明"换域只换配置"。**
- **执行方式**：以带伪影注入的模拟器为主执行器（可控真值 → 可定量对比 naive vs OS）；`BenchAdapter` 提供真实台面路径（输出人类可读 worklist + CSV/图像回灌），协议同构。

## 3. 分层架构与数据流

```
┌────────────── Agent Orchestrator 层（建议权，无裁决权）───────────────┐
│ agent/orchestrator.py  目标翻译 · 先验/理由提议 · QC/归因人类可读解释   │
│ agent/backends.py      · 下轮叙述与动作提案 → DecisionRecord 入事件日志 │
│ 边界：只读视图 + 提案队列；提案必须经 planner/kernel 校验后才生效        │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼ propose / explain（永不直写观测与模型数据）
┌────────────────────────── Kernel ───────────────────────────┐
│ objects.py   两个 schema + 全部枚举 + DecisionRecord 载荷     │
│ store.py     RunStore: 追加式事件日志 + 对象存储 + 运行检查点 │
│ lifecycle.py 轮次状态机 + trust 裁决/路由策略                 │
└──────┬──────────┬──────────┬──────────────┬──────────┬──────┘
       │          │          │              │          │
   design/    adapters/   adapters/ingest/  qc/     planner/
   space      base        csv_loader     checks     planner
   sampler    artifacts   image_metrics  attribution
   layout     sim_crystal              failure_model
   budget     sim_coating                  │
              bench_manual        ┌────────┴────────┐
                                  │ models/response_gp（仅 trusted）│
                                  │ qc/failure_model（正例仅 suspect/failed）│
                                  └─────────────────┘
   domain.py  YAML → 域配置装配        loop.py  轮次编排器（可断点续跑）
   ui/app.py  Streamlit 4 页签         scripts/run_loop.py  CLI（os|naive|compare）
```

**轮次状态机**（每次迁移写事件日志，即 provenance 骨架）：

```
DESIGNED → EXECUTED → QC_DONE → ROUTED → CLOSED
```

**观测生命周期**：

```
PENDING ──QC裁决──► TRUSTED ──► TO_RESPONSE_MODEL
                 ├► SUSPECT ──► TO_FAILURE_MODEL | QUARANTINE (+ next_action)
                 └► FAILED  ──► TO_FAILURE_MODEL (+ next_action)
改判：QUARANTINE 中的观测可被后续证据翻案（事件日志记录改判链）。
```

## 4. 内核 Schema（权威字段表）

### 4.1 枚举

| 枚举 | 取值 |
|---|---|
| `ExpStatus` | DESIGNED / EXECUTED / QC_DONE / ROUTED / CLOSED |
| `TrustLevel` | PENDING / TRUSTED / SUSPECT / FAILED |
| `Routing` | TO_RESPONSE_MODEL / TO_FAILURE_MODEL / QUARANTINE / REMEASURE / REPEAT_CANDIDATE |
| `ActionType` | NEW_CANDIDATES / REMEASURE / DISAMBIGUATION_REPEAT / REPEAT_CANDIDATE / ADD_CONTROLS / NONE |

### 4.2 ExperimentObject（pydantic v2）

| 字段 | 类型 | 说明 |
|---|---|---|
| exp_id / round_id / domain | str / int / str | 标识 |
| objective | Objective{name, metric, direction, description} | 目标 |
| design_space | DesignSpace{name, variables:[VariableDef]} | canonical 空间（含单位与变换） |
| active_vars | list[str] | 本轮实际扫描子集（canonical 空间的投影） |
| restrictions | list[Constraint{name, kind, params}] | 混合约束、禁配组合、安全上限 |
| candidates | list[Candidate{cand_id, params, source, rationale, placement_hint, parent_obs_id}] | 候选批 |
| controls | list[Control{control_id, kind: sentinel/negative/positive, params, expected_band}] | 哨兵/参照 |
| replicate_plan | ReplicatePlan{n_replicates, strategy: across_blocks} | 副本策略 |
| layout | LayoutAssignment{rows, cols, seed, wells:[WellAssignment{well_id,row,col,cand_id|control_id,is_edge,block_id}]} | 位置分配（含随机种子） |
| budget | Budget{wells_total, wells_used, rounds_total, rounds_used} | 预算 |
| execution_req | ExecutionReq{adapter, params, n_solution_batches} | 执行要求 |
| provenance | DesignProvenance{generator, acquisition, model_snapshot, based_on_obs, actions_consumed, rationale} | 设计出处（可审计决策链） |
| status / created_at | ExpStatus / str | 状态机 |

`VariableDef{name, kind: continuous|categorical, low, high, transform: linear|log, choices, unit}`。

### 4.3 ObservationObject

| 字段 | 类型 | 说明 |
|---|---|---|
| obs_id / exp_id / round_id | str/str/int | 标识 |
| cand_id / control_id / is_control | 二选一 | 归属 |
| result | MeasuredResult{metric, value, uncertainty, secondary, unit} | 测量结果（uncertainty 为测量不确定度估计，借鉴 LAP MeasurementResult；secondary 如晶粒计数、覆盖率） |
| raw_ref | RawDataRef{uri, kind, sha256} | 原始数据指针 |
| layout_meta | LayoutMeta{well_id, row, col, is_edge, block_id} | 位置元数据 |
| material_meta | MaterialMeta{solution_batch, additive_lot, prep_order} | 材料批次元数据 |
| instrument_meta | InstrumentMeta{instrument_id, exposure, illumination, capture_index} | 仪器/测量元数据 |
| qc | QCReport{checks:[QCCheck{name, level: hard|reference|structural, passed, score, evidence}], flags, suspicion} | QC 证据 |
| trust / trust_confidence | TrustLevel / float | 裁决 |
| failure_attr | FailureAttribution{hypotheses:[FailureHypothesis{cause, score, evidence, remedy}], top_cause, confidence} | 失败归因 |
| routing | Routing | 处置（与裁决分离：可信度与拿它做什么是两个决策） |
| next_action | RecommendedAction{action, params, reason} | 给规划器的建议 |

### 4.4 RunStore（运行目录 = 运行时检查点）

```
runs/<run_name>/
├── config.json          # 域配置快照 + 模式 + 种子（可复现）
├── events.jsonl         # 追加式事件日志：每次状态迁移/裁决/改判/规划决策
│                        #   改判/翻案=追加新事件引用旧事件，永不覆盖历史（OpenLineage facet 版本化模式）
├── checkpoint.json      # 运行检查点：当前轮次、状态、预算余额 → 断点续跑
├── experiments/exp_r<k>.json
├── observations/obs_*.json
├── truth/round_<k>.jsonl   # 仿真真值 sidecar（OS 不可读，仅评分脚本用）
├── models/snapshot_r<k>.json  # 响应模型训练集指纹（可复现模型状态）
└── report/              # 对比图、summary.json
```

`loop.py` 每轮结束原子写 `checkpoint.json`；重启时从最后完成轮继续——**运行级检查点**与 `CHECKPOINTS.md` 的**构建级检查点**呼应，同一哲学：任何进度都必须可恢复、可审计。

### 4.5 DecisionRecord（事件日志载荷，不是第三个内核对象）

Agent、规划器、人类的每一个决策动作都以 DecisionRecord 追加进 `events.jsonl`（`kind="decision"`）。它是内核事件词汇表的一部分（schema 定义在 `kernel/objects.py`），但**不引入新的持久对象**——内核仍然只有 ExperimentObject 与 ObservationObject。

| 字段 | 类型 | 说明 |
|---|---|---|
| decision_id / round_id | str / int | 标识 |
| actor | `agent` \| `planner` \| `human` | 谁提出 |
| kind | goal_translation / prior_proposal / qc_explanation / attribution_explanation / round_rationale / action_proposal / acceptance / rejection / override | 决策类型 |
| refs | list[str] | 关联的 exp_id / obs_id / 上游 decision_id |
| content | dict | 结构化载荷（如翻译出的 Objective、提案的动作列表、解释文本） |
| accepted | bool \| null | 校验结果（提案类必填；由 validator 填写） |
| validator | str \| null | 谁校验的（planner / lifecycle / human） |
| created_at | str | 时间戳 |

审计不变量：任何 `actor=agent` 且 `kind` 为提案类的记录，必须存在一条对应的 acceptance/rejection 记录才可能影响下一轮设计——事件日志上可机器检查。

## 5. 计算设计层 `expos/design/`

| 模块 | 职责与关键接口 |
|---|---|
| `space.py` | canonical 空间 ↔ 单位立方：`to_unit(params)->ndarray` / `from_unit(u)->params`（log 变换、类别 snap）；`check_constraints(params, restrictions)->bool` |
| `sampler.py` | `sobol_candidates(space, n, seed, restrictions, min_dist)`（scipy.qmc scrambled Sobol + 约束拒绝采样）；`propose_candidates(space, n, seed, score_fn, restrictions, pool_size=2048, min_dist)`：Sobol 可行池上按 `score_fn` 打分（BO 占位——M4 由 planner 注入采集函数，本模块不改），top-n + 最小距离去重；约束不可满足/去重产不够一律 DesignError |
| `layout.py` | `LayoutPlanner(rows, cols, seed, sentinel_wells=None)`：(a) 哨兵固定四角+中心（不受 risk_map 影响）；(b) 候选副本跨区组（副本数 ≤ 区组数时强制；注意板级容量精检只保证总数、不保证逐区组可行性，逐区组耗尽时在分配期响亮失败）；(c) 边缘/中心分层随机化（尽量交替）；(d) 尊重 `placement_hint`（center_only / edge_center_pair——歧义消解设计用）；(e) 接受失败模型 `risk_map`（未知 well_id 键拒绝），低风险孔优先；(f) 随机种子记入 LayoutAssignment.seed |
| `budget.py` | `BudgetManager`：`can_afford(wells)` / `spend_wells(wells, what)` / `charge_layout(layout)` / `start_round()`；失败不记账（原子性）。规划器所有动作（复测/重复/新候选/加哨兵）都要向它申请——结构性强制在 M4 loop/M7 planner 接线时落地 |

## 6. 执行 Adapter 层 `expos/adapters/`

统一协议（`base.py`）：

```python
class ExecutionResult:  raw_results: list[RawResult]; truth_records: list[dict] | None
class ExecutionAdapter(Protocol):
    def execute(self, exp: ExperimentObject, rng) -> ExecutionResult: ...
```

`RawResult{well_id, cand_id|control_id, value, secondary, exposure, illumination, capture_index, solution_batch, additive_lot}`。
`truth_records` 仅由 `loop.py` 原样落盘 `truth/`（不透明透传）；任何决策模块（qc/models/planner/agent）不得消费（公理 6）。

| 模块 | 内容 |
|---|---|
| `artifacts.py` | 伪影注入器基类 `Injector.apply(value, well_ctx) -> (value', applied, tag)` 与实现：`EdgeEvaporation(strength)`（Deegan 接触线蒸发通量 J(r)∝(1−(r/R)²)^(−1/2) 衍生的边缘增强形式，特征长度 1–2 孔）、`ThermalGradient(axis, magnitude)`（近似线性中心-边缘梯度；与蒸发机制不同，**分开建模**）、`Glare(prob, boost)`、`DustNucleation(prob, drop)`、`BatchShift(batch_idx, shift)`、`InstrumentDrift(rate)`。**作用在测量层，与真值面分层实现**（否则无法评分）。场景表来自域 YAML `artifact_scenario: [{round, injector, params}]`。方法学定位：现有公开基准（Olympus、Nat. Comms 2024）只做输出随机噪声，无人建模空间系统性偏差下的闭环对比——此模拟器即该空白（REFERENCE_MAP §9.3） |
| `sim_base.py` | 模拟器共享基座（M3 新增，偏离备案见 CHECKPOINTS）：执行链"真值 → 噪声 → 伪影注入 → RawResult + truth sidecar"集中于此，含 `true_optimum()`（密集 Sobol 扫描，仅评分脚本用）；crystal/coating 共用本基座正是"注入器框架域无关"的结构性证明 |
| `sim_crystal.py` | 结晶真值面（继承 SimulatorBase，只提供 `true_value`/`secondary`）：CNT 阈值成核 × Nývlt 降温-CV × Kubota-Mullin 添加剂倒 U × 籽晶因子，内部最优；注释逐项标注【已核实】/【工程近似】（REFERENCE_MAP §11.2） |
| `sim_coating.py` | 咖啡环均匀性真值面（同基座、同注入器框架）——热插拔证明 |
| `bench_manual.py` | 真实台面：`prepare(exp)` 输出人类可读 worklist（每孔配液指令 + 板图 CSV/Markdown）；执行后经 ingest 回灌 |
| `ingest/csv_loader.py` | CSV → RawResult 列表（模板校验） |
| `ingest/image_metrics.py` | PIL + scipy.ndimage：灰度 → 阈值 → 连通域 → {晶粒数, 覆盖率, 平均晶粒尺寸} → 质量指数；无 cv2 依赖 |

所有来源统一产出 `ObservationObject(trust=PENDING)`——ingestion 是独立层。

## 7. QC 与失败归因层 `expos/qc/`

### 7.1 三级检查（`checks.py`，`run_qc(exp, obs_list, history) -> per-obs QCReport`）

| 级别 | 检查 | 机制 |
|---|---|---|
| hard（单观测） | 缺失/NaN、超物理量程、曝光/照度越界 | 规则 |
| reference（板内） | 哨兵偏离历史控制带（均值±kσ，历史来自既往轮哨兵）；副本组 CV 超阈 + 组内最大 |z| 离群 | 控制图 |
| structural（跨观测） | **边缘效应**：同候选跨边缘/中心副本的配对差 + 哨兵对 is_edge 回归；**梯度**：残差沿行/列线性趋势；**空间自相关**：残差 Moran's I（板邻接）；**批次效应**：残差按 solution_batch 分组均值位移；**时间漂移**：残差 vs capture_index 相关 | 残差 = 值 − 候选组均值；哨兵（全板同条件）是最干净的位置信号源。顺序纪律：**先去趋势、再对残差检验**；显著性一律用**置换检验**（小样本网格下比渐近 p 值稳健，参照 PySAL esda 实践） |

每个 structural 检查返回板级结论 + 被牵连 well 的逐孔嫌疑分。

### 7.2 归因引擎（`attribution.py`）

假设库——每个假设声明**签名**（在哪些 provenance 特征上留下什么模式）与**补救动作**：

| 假设 | 签名 | remedy |
|---|---|---|
| edge_evaporation | is_edge ∧ 边缘检查触发 ∧ 值相对副本均值偏高 | DISAMBIGUATION_REPEAT（同条件钉中心位） |
| thermal_gradient | 行/列趋势触发 ∧ 孔位于梯度极端 | ADD_CONTROLS + 重随机化 |
| glare | 孤立高离群 ∧ 与位置无关 ∧ exposure/illumination 异常 | REMEASURE（重拍不重做） |
| dust_contamination | 孤立离群 ∧ secondary 成核计数异常高 | REPEAT_CANDIDATE |
| batch_effect | 批次位移触发 ∧ 观测属该批次 | REPEAT_CANDIDATE（跨批次） |
| instrument_drift | 时间漂移触发 ∧ capture_index 处于漂移段 | REMEASURE + 校准标记 |

打分 = 签名项加权和，归一化为近似后验；输出排序假设 + `next_action`。

**反驳器合同（refuter contract，借鉴 DoWhy refutation 纪律）**：每条假设除签名匹配外标配程序化反驳器——placebo（打乱位置/批次标签后该效应应消失）与 subsample（子集重采样下结论应稳定）；只有通过反驳器的假设才允许写入 `failure_attr.top_cause`，未通过者降级为低置信候选。反驳器结果记入 `FailureHypothesis.evidence`。

**批次分组必须对齐模拟器（对抗审查实锤）**：`attribution._board_frame` 重建每孔 `solution_batch` 时，分组公式须与 `sim_base` **严格一致**——棋盘格 `(row+col)%n_batches`，**不是** capture 序的 `idx%n`。错用 capture 序会把观测自身错分出真批次组、稀释真批次效应、令 `t_batch` 失真。缝隙守卫测试 `test_board_frame_batch_matches_simulator_labels` 钉死此对齐（几何混叠是归因误差的系统性来源，见 M9_PROTOCOL 附录 B）。

### 7.3 失败模型（`failure_model.py`）

Beta-Bernoulli 计数模型：特征桶 = {is_edge, block_id, solution_batch, 轮次段}。**正例 = SUSPECT/FAILED**（由 trust 裁决驱动，与 routing 处置正交：被 QUARANTINE 的 SUSPECT 同样计入正例）；**TRUSTED 只作曝险分母**（纯计数，不携带响应值——估计伪影率必须有分母，但可信数据的数值不进失败模型）。模型可随时从事件日志按**当前**裁决全量重建（event-sourced），因此改判/翻案自动生效、无需增量回滚。输出 `p_artifact(features)` 与整板 `risk_map(layout)`（供规划器贴现与 UI 热图）。

### 7.4 裁决策略（`kernel/lifecycle.py`）

hard 失败 → FAILED + TO_FAILURE_MODEL；结构/参照嫌疑分 ≥ 0.6 → SUSPECT + TO_FAILURE_MODEL；0.3–0.6 → SUSPECT + QUARANTINE；否则 TRUSTED + TO_RESPONSE_MODEL。阈值来自域配置，可人工 override（UI），改判写事件日志。

## 8. 响应模型 `expos/models/response_gp.py`

sklearn `GaussianProcessRegressor`（Matérn ν=2.5 + White），单位立方上训练，仅吃 TRUSTED 观测（fit 对其他 trust/routing 结构性拒绝）；方向内部统一为最大化；`snapshot()` 返回训练集指纹（(X,y) 联合排序哈希，行序无关）写入 provenance 与 `models/`。批量采集：M4 naive 基线用 UCB top-n + 最小距离去重（`propose_candidates(score_fn)`），**Kriging Believer 条件化留待 M7 规划器接线**——与 REFERENCE_MAP §11.1 的偏离已备案（CHECKPOINTS M4）。

## 9. 规划器 `expos/planner/planner.py`

每轮一次仲裁，输入 = 响应模型 + 失败模型 + 预算 + 未决 next_action：

1. **提案裁定 + 动作队列**：`TrustAwarePlanner.plan_round` 开场先 `_adjudicate_proposals`——**仅裁 `ACTION_PROPOSAL`**：非法 `ActionType`、或 `_NEEDS_CANDIDATE` 动作缺在案 target → reject（带中文理由）；其余 accept；`GOAL_TRANSLATION`/`PRIOR_PROPOSAL` 留 human。随后收集上一轮 SUSPECT/FAILED 观测的 `next_action`（REMEASURE / DISAMBIGUATION_REPEAT / REPEAT_CANDIDATE）以及已 accept 的 Agent action_proposal（DecisionRecord），按归因置信度排序，占用预算 ≤ `max_action_frac`（默认 30%）。Agent 提案与内生动作走**同一条校验通道**（预算、约束、布局可行性），接受/拒绝写回 DecisionRecord。
2. **风险贴现采集**：剩余孔位跑 BO；孔位分配阶段用 `risk_map` 贴现——期望信息 × (1 − p_artifact)，高风险位要么避开要么加副本。
3. **对照增补**：整板伪影率超阈 → ADD_CONTROLS（加密哨兵）。
4. **候选容量二次封顶**：`arbitrate`（孔预算）之后，对物化候选数再封 ≤ `n_cands`——`well_cost` 可能被 agent 提案的 `n_wells` 低估，故用 `arbiter.materializes_candidate` 逐项判别、超额入 overflow 留痕（与孔预算溢出同路）。
5. **装配**：候选 + 副本计划 + 对照 → LayoutPlanner → 新 ExperimentObject，`provenance.rationale` 写明每条决策理由与消费的动作（可审计决策链）。

**naive 模式**（对照组）：QC/归因/失败模型全部旁路，一切观测视为 TRUSTED，纯 BO——与 OS 模式共享其余全部代码，保证对比公平。

## 10. Agent Orchestrator 层 `expos/agent/`

**定位：OS 的"外交层"——向下把人类意图编译成内核对象，向上把内核证据翻译成人类叙述。它不是内核**；内核仍然只有：ExperimentObject、ObservationObject、RunStore、生命周期状态机、trust/routing 规则、检查点。

### 10.1 职责（七条，全部落为 DecisionRecord）

| # | 职责 | 接口 | DecisionRecord.kind |
|---|---|---|---|
| 1 | 人类目标 → Objective + DesignSpace 限缩（active_vars）+ 约束 + 预算 + 初始计划提示 | `translate_goal(text, domain_catalog) -> GoalTranslation` | goal_translation |
| 2 | 推理先验与规划理由提议（如"添加剂宜 log 尺度细扫""过饱和度上界保守"） | `propose_priors(space, history) -> PriorProposal` | prior_proposal |
| 3 | QC 与失败归因结果的人类可读解释（引用具体 QCCheck 证据与签名命中） | `explain_qc(obs, qc, attribution) -> str` | qc_explanation / attribution_explanation |
| 4 | 下轮理由叙述 + 动作提案（提案与内生动作同队列竞争预算） | `narrate_round(...) -> str` / `propose_actions(...) -> list[ActionProposal]` | round_rationale / action_proposal |
| 5 | 把以上一切落入事件日志 | agent 只**返回** DecisionRecord；由 `loop.py` 调用内核统一落盘——agent 不持有任何 store 写句柄（与 §10.3 一致） | （全部） |
| 6 | **永不绕过内核路由语义** | 结构保证：agent 模块没有 lifecycle 裁决 API 的写入口 | — |
| 7 | **永不直改可信观测与响应模型训练数据** | 结构保证：agent 只拿 `ReadOnlyRunView`（观测/模型的不可变快照），无任何 save/update 入口 | — |

### 10.2 后端与可测性

`agent/backends.py` 定义 `AgentBackend` 协议，两个实现：
- **TemplateBackend（默认）**：确定性规则+模板（目标解析用关键词/结构化表单，解释用证据填槽模板）。离线、可测试、零外部依赖——CI 和对比实验全部用它，保证可复现。
- **LLMBackend（可选）**：经 API 调用 LLM，输出受 schema 约束（结构化解码 / function-calling），失败回退 TemplateBackend。任何 LLM 输出仍是提案，仍走同一校验通道。

### 10.3 边界的结构性执行（不是纪律，是类型）

- Agent 构造函数只接受 `ReadOnlyRunView`（由 RunStore 导出：deep-frozen 观测快照、模型指纹、QC 报告、事件流），**不持有** RunStore 写句柄；
- 提案通过 `ProposalQueue` 交给规划器，规划器是唯一消费者；接受/拒绝由规划器写 acceptance/rejection DecisionRecord；
- lifecycle 的裁决函数签名不接受任何 agent 产物作为输入（QC 证据之外的东西进不了裁决）；
- 测试矩阵含守门测试：试图从 agent 模块导入写 API 必须失败（API 不存在），伪造 acceptance 的提案不影响下一轮设计。

## 11. 编排器与 CLI

- `expos/loop.py`：`run_round(ctx)` = 设计→执行→ingest→QC→路由→更新双模型→Agent 解释/叙述（DecisionRecord）→写检查点；`run_loop(domain_cfg, mode, rounds, seed, outdir)` 支持从 `checkpoint.json` 断点续跑。**四策略注入点**（M5/M7/M8 落地）：`_policies_for_mode` 是唯一 mode 判定点，返回四元组 `(VerdictPolicy, AggregationPolicy, PlannerPolicy, LoopAgentPolicy)`——裁决（NaivePolicy/QCPolicy）× 聚合（Passthrough/Median/ReplicateVariance）× 规划（BaselinePlanner/TrustAwarePlanner）× **Agent 策略（M8：naive/robust→`NullAgentPolicy` 零行为；os→`TemplateAgentPolicy`）**，loop 主体零 mode 分支；planner 状态（stage/entered_at_round）入 checkpoint，risk_map 由规划器产出喂 LayoutPlanner。`TemplateAgentPolicy.after_round`（`expos/agent/policy.py`）= 导出只读视图 export_view→ingest→把本轮 SUSPECT 观测的 `ACTION_PROPOSAL` 经 `lifecycle.submit_proposal` 入队→`narrate_round` 落 `ROUND_RATIONALE`；红线不变——agent 只有提案/解释权，写账只走 lifecycle（loop 依赖红线测试改为禁 `expos.agent.backends`/`views` 直连）。
- `scripts/run_loop.py`：`--domain crystal|coating --mode os|naive|compare --rounds N --seed S --out runs/<name>`；compare 同种子同伪影场景跑双模式，产出 `report/summary.json` + `report/compare.png`（两条"当前最优真值"曲线 + 简单遗憾曲线）。
- **评分方法**：每轮取各模式"当前推荐最优候选"，用 truth sidecar 与 `true_optimum()` 计算真实质量与 simple regret——OS 从未接触真值，评分完全在事后。

## 12. UI `ui/app.py`（Streamlit，4 页签）

1. **Loop**：轮次时间线、预算消耗、两条最优曲线（naive 视角 vs trusted 视角）——demo 主视觉。
2. **板图**：孔位热图按 trust 着色（绿/黄/红/灰），点开任意孔展示完整 ObservationObject（QC 证据、归因、原始引用）。
3. **模型**：响应曲面 2D 切片 ‖ 失败模型位置/批次风险热图并排。
4. **决策日志**：DecisionRecord 流（agent 提案/规划器裁定/人类 override 的完整链）、每轮理由叙述、事件流、人工改判入口（人在环上）。

只读 `runs/` 目录，与内核零耦合。

## 13. 仓库结构

```
dry_wet_agent_os/
├── README.md  CHECKPOINTS.md  pyproject.toml
├── docs/{ARCHITECTURE,BUILD_PLAN,REFERENCE_MAP}.md
├── expos/
│   ├── kernel/{objects,store,lifecycle}.py
│   ├── design/{space,sampler,layout,budget}.py
│   ├── adapters/{base,artifacts,sim_crystal,sim_coating,bench_manual}.py
│   ├── adapters/ingest/{csv_loader,image_metrics}.py
│   ├── qc/{checks,attribution,failure_model}.py
│   ├── models/response_gp.py
│   ├── planner/planner.py
│   ├── agent/{orchestrator,backends,views}.py   # views.py: ProposalQueue 与提案类型；
│   │                                            #   ReadOnlyRunView 由 kernel/store.py 定义并导出（依赖方向：agent→kernel，永不反向）
│   ├── domain.py  loop.py
├── domains/{crystal,coating}.yaml
├── scripts/run_loop.py
├── ui/app.py
├── tests/{test_kernel,test_design,test_adapters,test_qc,test_planner,test_agent,test_loop_e2e}.py
└── runs/            # 运行产物（gitignore）
```

## 14. 测试矩阵

| 测试 | 覆盖 |
|---|---|
| test_kernel | schema 往返序列化、store 读写、事件日志、裁决策略表、改判 |
| test_design | 单位立方往返（含 log/类别）、约束拒绝、哨兵位固定、副本跨区组、placement_hint、风险避让 |
| test_adapters | 真值面形状（内部最优）、各注入器作用方向、truth sidecar 与 OS 隔离、CSV/图像 ingest |
| test_qc | 合成板：注入边缘膨胀→边缘检查命中；孤立眩光→离群命中；批次位移→分组命中；归因 top_cause 正确 |
| test_planner | 动作消费进下一轮、预算不超支、naive/OS 分叉、DISAMBIGUATION 布局钉中心 |
| test_agent | 目标翻译确定性（TemplateBackend）、提案经校验且预算封顶、**守门测试**（agent 模块不存在观测/模型写 API；未被 accept 的提案不影响下轮设计；每条提案在事件日志有 acceptance/rejection 配对）、DecisionRecord 落盘 |
| test_loop_e2e | crystal OS 模式 3 轮全通、断点续跑、coating 热插拔零内核改动、compare 产出报告 |

## 15. 关键 Demo（验收的终局标准）

**第一幕（假最优狙击）**：同模拟器同预算，第 3 轮边缘蒸发使某边缘孔的平庸条件读数全场最高。naive 闭环围绕假最优烧掉后续预算、最终推荐真值排名差；OS 由哨兵异常 + 边缘回归判 SUSPECT、归因 edge_evaporation、自动生成中心位复测证伪、响应模型保持干净、命中真值最优区。`compare.png` 两条曲线第 3 轮分叉即整个项目的论点；同时 Agent Orchestrator 在决策日志给出人类可读裁决叙述（"该读数来自边缘孔，四角哨兵同步偏高，同条件中心副本未复现——判 edge_evaporation，建议中心位复测"），证明解释链完整。
**第二幕（热插拔）**：`--domain crystal` 改 `--domain coating`，不改内核一行，闭环照跑——OS 论断坐实。
**第三幕（边界即类型）**：现场展示 agent 提案被规划器拒绝的 DecisionRecord 配对记录，与守门测试——Agent 有建议权、无裁决权不是口号，是可机器验证的不变量。

## 16. 依赖与约束

- Python 3.13；numpy/scipy/scikit-learn/pydantic v2/PyYAML/matplotlib/pandas/PIL（已有）；streamlit（已装）；**不用 cv2**（PIL+scipy 替代）；不引入数据库服务（JSON 文件 + JSONL 事件日志）。
- Agent 层默认 TemplateBackend（零外部依赖、CI 可测）；LLMBackend 为可选插件（anthropic SDK），缺 API key 时自动回退，不得成为任何测试或对比实验的必要条件。
- 全程非生物、材料安全（常见食品级/教学级盐类）；无任何生命科学语义。
