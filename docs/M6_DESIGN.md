# M6 设计规格 —— 归因引擎 + 失败模型 + 路由接线

> 本文件是 M6 的实现契约，从属于 `docs/ARCHITECTURE.md`（§7.2/§7.3/§7.4、§4.3）与
> `docs/REFERENCE_MAP.md`（§11.4/§11.5、§13.2/§13.5、§12.5）。冲突时先改 ARCHITECTURE 再改本文。
> 交付物：`qc/attribution.py`、`qc/failure_model.py`、`kernel/lifecycle.py` 接线。验收见 `BUILD_PLAN.md` M6。

---

## 1. 范围与非目标

**M6 = 归因（为 SUSPECT/FAILED 观测指认原因）+ 失败模型（provenance 特征 → 伪影率）+ lifecycle 接线（触发归因、生成 next_action、落账）。**

- **依赖**：M5 三级 QC（`qc/checks.py`）已产出 per-obs `QCReport`，含具名 structural `QCCheck`
  （`edge`/`gradient`/`moran`/`batch`/`drift`/`isolated_outlier`）与逐孔嫌疑分及板级证据。
- **非目标（明确不做）**：
  1. **规划器消费**是 M7——M6 只**生成** `next_action` 与 `risk_map`，不决定预算/仲裁/装配；
  2. **Agent 人类可读解释**（`attribution_explanation` DecisionRecord）是 M8；M6 只落**证据事件**；
  3. **偏差校正复归**（DEEP_REVIEW §3.1 backlog）M6 不实现，但归因证据须携带 OS 可见的
     **估计效应量**（残差幅度），为将来去偏留 `debias_hint` 字段（本里程碑不消费）。
- **红线（不作弊）**：归因与失败模型只允许读 **OS 可见证据**——`layout_meta`/`material_meta`/
  `instrument_meta`/`result.secondary`/`QCCheck` 结论及其派生残差；**绝不引用注入器内部参数**
  （`strength`/`prob`/`shift`/`phi`…）。效应量一律从残差/哨兵/secondary/instrument_meta 估计。

---

## 2. 归因引擎 `qc/attribution.py`

### 2.1 输入、证据面与主入口

```python
def attribute(obs, qc, board: BoardContext) -> FailureAttribution   # 纯函数，只读，不写 store
```
`board` = **M5 导出的 `BoardContext`**（生产者见 M5 §2.3；`run_qc` 与逐 obs `QCReport` 一同返回，
同一份 QC 用过的证据，M6 **只读不重算**、不新取真值）：候选组残差矩阵 `residual_grid`
（`r_i = value_i − mean(value | same cand_id)`）、`resid_scale=σ`、哨兵控制带偏离 `sentinel_dev`、
structural 检查的板级系数（`edge_coef`/`edge_r2`、`gradient_slope`/`gradient_r2`、`moran_I/EI/p`、
`batch_shift`、`drift_corr`）与逐孔嫌疑分 `suspicion_by_well`。**所有量均 OS 可见**。
TRUSTED 观测不进归因（`failure_attr=None`）。**M6 不再自算残差/系数**（对抗审查：board 曾无生产者，
现由 M5 §2.3 唯一物化，杜绝二次实现漂移，呼应 §6.6）。
(v2: 依对抗审查修正)

### 2.2 六假设签名表（字段级布尔/阈值）

记 σ = 候选组残差稳健尺度（MAD）；τ_r = 0.5σ。签名项取值 ∈[0,1]（布尔取 {0,1}，
连续项按 `min(1, |量|/阈值)`）。**签名只读下列 OS 可见字段。**

| 假设 | QCCheck 命中 | 观测特征模式（字段级） | 判别锚点 |
|---|---|---|---|
| `edge_evaporation` | `edge`✗ | `d_edge≤1`（现算 `min(row,col,rows-1-row,cols-1-col)`）∧ `r_i>τ_r` ∧ 配对中心副本 `r_center≈0` ∧ 多边同向为正（对称抬升） | 边界局部 + 短衰减 + **对边同号** |
| `thermal_gradient` | `gradient`✗ | 孔位于极端 `frac<0.15 ∨ frac>0.85` ∧ `sign(r_i)` 与位置线性一致 ∧ 贯穿内部 ∧ **对边异号** | 全板单调 + **对边异号** |
| `glare` | `isolated_outlier`✗ | `r_i>0` ∧ 孤立（邻域 Moran 局部不显著）∧ `instrument_meta.exposure` 板内越界（**illumination 判据仅真实台面启用**，见下） | 曝光异常 + 位置无关 |
| `dust_contamination` | `isolated_outlier`✗ | `r_i<0` ∧ `secondary["grain_count"]` 板内高离群 ∧ 孤立 ∧ 曝光正常 | 方向向下 + 成核计数高 |
| `batch_effect` | `batch`✗ | `material_meta.solution_batch` ∈ 越限批 ∧ 该批残差组均值位移显著 ∧ 跨位置 | 沿批次**阶跃** |
| `instrument_drift` | `drift`✗ | `instrument_meta.capture_index` ∈ 漂移显著段 ∧ `r_i` 与 capture_index 单调 ∧ 跨批跨位 | 沿时间**连续** |

`glare`/`dust` 靠**残差方向**（+/−）与 **secondary 成核计数**互斥；`batch`/`drift` 靠
**阶跃（离散批标签）vs 连续（capture_index 单调）**互斥（DEEP_REVIEW §4 语义混叠教训）。

**edge 签名用 `d_edge≤1` 而非裸 `layout_meta.is_edge`（对抗审查）**：注入器 `EdgeEvaporation`
的 `max_range_wells=1` 作用于 **d≤1 的最外两圈**（`artifacts.py`：`d=min(row,col,rows-1-row,cols-1-col)`，
`d>max_range` 才不作用），而内核 `is_edge` **只标 d=0 最外一圈**——裸 `is_edge` 会**漏掉 d=1 被污染圈**，
使签名系统性欠命中。改为从 `layout_meta.row/col` 现算 `d_edge=min(row,col,rows-1-row,cols-1-col)≤1`
（纯几何、OS 可见，不碰注入器参数）。`board.residual_grid` 为全板矩阵，M6 现算 d_edge 无需新证据。
(v2: 依对抗审查修正)

**illumination 判据是死码（对抗审查）**：当前模拟器 `instrument_meta.illumination` **恒为 1.0**
（`objects.py` 默认、`Glare` 只同步抬 `exposure`），故 glare 签名的 illumination 分支**永不触发**。
M6 **只用 `exposure`**（真变化的证据）；illumination 判据**标注为"真实台面才启用"**、模拟评测下不计分，
避免误导为可用证据。同一处理适用于 M5 §2.2 `exposure_illumination` 硬检查的 illumination 半支。
(v2: 依对抗审查修正)

### 2.3 评分与归一化

每假设原始分 `S_h = Σ_j w_hj · s_hj`（QC 命中项权重 0.4，主特征 0.3，判别锚点 0.2，
辅助项 0.1；见 §2.2 行序）。跨假设归一化为伪后验 `p_h = S_h / Σ_h S_h`。
`top_cause = argmax_h p_h`，`confidence = p_top`，`hypotheses` 按 `p_h` 降序写入
（含未命中者的低分，供审计）。每条 `FailureHypothesis.evidence` 记：命中的 QCCheck 名、
触发的特征布尔、**估计效应量**（如边缘回归系数、梯度斜率——OS 可见）、`debias_hint`（预留）。

### 2.4 反驳器绑定（DoWhy 纪律，接口见 §13.5）

只有**通过反驳器**的假设才允许写入 `top_cause`；未通过者降级为低置信候选（留在
`hypotheses` 但不当 top，`evidence["refuter"]="fail"`）。统计量 `statistic_fn` 复用 §2.3 的
OS 可见效应量估计器。

| 假设 | placebo 打乱的标签 | statistic_fn（效应量） | subsample |
|---|---|---|---|
| edge / gradient | 空间位置标签（well_id→row/col） | 边缘回归系数 / 梯度斜率 | 残差子集 frac=0.8 |
| glare / dust | 空间位置标签 | 孤立孔稳健 z（Moran 局部） | ×n=100 |
| batch | `solution_batch` 标签 | 批次组均值位移 | 同上 |
| drift | `capture_index` 顺序 | 残差–capture 相关 | 同上 |

判据（§13.5，方向相反勿混）：
`refute_placebo(statistic_fn, labels, n=999)` PASS ⇔ p_zero>0.05 且 |placebo 均值|<0.1|obs|（**效应塌零**）；
`refute_subsample(statistic_fn, data, frac=0.8, n=100)` PASS ⇔ p_in>0.05 且 std<0.5|obs|（**效应稳定**）。
两者用**经验分位**（n<100 不切正态近似，§13.5 坑）。反驳器结果全量记入 `evidence`。

### 2.5 remedy 映射（含 FireWorks 语义与 placement_hint，§13.2）

`remedy` 落 `FailureHypothesis.remedy`（`ActionType`）；具体队列语义由 §4 的 `propose_action` 装配。

| 假设 | remedy(ActionType) | semantics | placement_hint / 参数 |
|---|---|---|---|
| edge_evaporation | DISAMBIGUATION_REPEAT | **detour**（顶替旧判、钉中心证伪） | `center_only` |
| thermal_gradient | ADD_CONTROLS | **addition**（加密哨兵 + 重随机化种子） | — |
| glare | REMEASURE | **detour**（重拍不重做，同板同液） | `recapture=True` |
| dust_contamination | REPEAT_CANDIDATE | **addition** | 同条件另孔 |
| batch_effect | REPEAT_CANDIDATE | **addition** | `cross_batch=True` |
| instrument_drift | REMEASURE | **detour** | `calibrate_flag=True` |

detour（REMEASURE/DISAMBIGUATION_REPEAT）= 顶替旧观测、门控归因；addition
（ADD_CONTROLS/REPEAT_CANDIDATE）= 纯扩展、不影响旧判（§13.2 图语义）。

### 2.6 竞争假设判别：edge_evaporation vs thermal_gradient

两者都产生空间模式，是最强的混叠对（架构 §6/§9.5 要求分开建模）。**唯一可靠判别锚点 =
对边符号结构**：
- **edge**：边界局部、指数短衰减（~1 孔），**四边同向为正**（对边同号）；哨兵四角同步齐升；
  中心副本不复现。
- **gradient**：贯穿内部的单调线性趋势，**一端高一端低**（对边异号）；哨兵四角呈单调梯度而非齐升。

实现：对残差同时拟合 (a) `d_edge≤1` 哑元回归 与 (b) 连续行/列线性回归，比较各自解释的
残差方差 ΔR²（即 board.`edge_r2` vs `gradient_r2`）；对边同号→edge，对边异号→gradient。
二者 ΔR² 接近且符号结构不清 → 走 §2.7。

**警示（对抗审查）**：**勿退化为纯符号判据**。单轴梯度（如沿 row）在**垂直于梯度轴**的
一对边（col 方向两条边）上残差**对边同号**——纯"对边符号"检查会把这对边误读成 edge。
故 **ΔR² 双回归是主判据**（谁解释的残差方差大谁胜），对边符号只作辅助锚点；两回归的 R²
均来自 M5 §2.3 `BoardContext`，符号结构从 `edge_coef`/`gradient_slope` 联合读取，不靠单对边符号。
(v2: 依对抗审查修正)

### 2.7 证据不足 → inconclusive（借鉴 A-Lab 模糊判定，§12.5）

A-Lab 的教训是"别把不确定强行归因、别把表征失败当合成失败"。触发 inconclusive
（`top_cause=None`、`confidence=p_top`，**保持 QUARANTINE 不 commit**）当且仅当：
1. `p_top < FLOOR`（默认 0.45，无假设够强）；或
2. `p_top − p_second < MARGIN`（默认 0.15，两假设难分——含 edge/gradient 未决）；或
3. 领先假设**反驳器未通过**（效应经不起 placebo/subsample）。

inconclusive 的 `next_action` = DISAMBIGUATION_REPEAT（通用消歧，钉中心 + 跨批复测），
用一次廉价实验换下一轮的可辨识性，而非强行 commit 错误 remedy。

---

## 3. 失败模型 `qc/failure_model.py`

### 3.1 Beta-Bernoulli 计数桶

特征桶 `Bucket = (is_edge: bool, block_id, solution_batch, round_segment)`，
`round_segment` 按轮次三分（early/mid/late）以捕捉漂移的时间维。
- **正例 k** = 落桶且当前裁决 ∈ {SUSPECT, FAILED} 的观测数（含被 QUARANTINE 者——由 trust 驱动、
  与 routing 正交，架构 §3/§7.3）；
- **曝险 n** = 落桶的**全部**观测数（TRUSTED 仅作分母、其响应值绝不进模型）。

收缩先验（§11.5，James-Stein 型）`Beta(m·p̄, m·(1−p̄))`，`m=5`，`p̄` = 全局伪影率
（当前裁决下 SUSPECT∪FAILED / 全部）。后验均值：
```
p_artifact(bucket) = (m·p̄ + k) / (m + n)
```
桶空/稀疏时**层级回退**：full → 去 solution_batch → 去 block_id → 全局 p̄（收缩已部分兜底）。

### 3.2 event-sourced 全量重建

```python
def rebuild(observations, events=None) -> None   # 无增量、无回滚
```
**简化（对抗审查）**：`ObservationObject.trust` **已是物化的当前裁决**——`reclassify` 改判时
直接写回 `obs.trust`（M5/lifecycle 已保证），无需从 `events.jsonl` 逐条回放推断"最后一条
reclassification"。故 `rebuild` **以 observations 的当前 `trust` 计桶**（正例 = trust∈{SUSPECT,FAILED}）；
`events` 仅作**可选校验**（断言事件流末态与 obs.trust 一致，防漂移），不作为计桶数据源。
**改判/翻案自动生效**（obs.trust 已随改判更新；架构 §7.3；对照 FireWorks `_rerun` 不回滚动态
节点的坑，§13.2——全量重建天然规避）。
(v2: 依对抗审查修正)

### 3.3 `risk_map` 输出契约（M2 LayoutPlanner 已收此参数）

```python
def risk_map(layout: LayoutAssignment, round_id: int) -> dict[str, float]   # well_id -> p_artifact ∈[0,1]
def risk_upper(layout, round_id) -> dict[str, float]   # 乐观置信上界，供 M7 贴现（§11.5 RAHBO）
```
- 键 = layout 的**全部** `well_id`，无多余键（`design/layout.py` 对未知键**响亮失败**，
  见 M2 检查点）；
- 布局期 `solution_batch` 尚未定 → 用 §3.1 回退桶 `(is_edge, block_id, round_segment)`；
- `risk_map` 返回**后验均值**（点估计，喂 UI 热图）；`risk_upper` 返回上界供 M7 折扣，
  桶稀疏时上界宽 → 天然弱化折扣（§11.5 覆盖偏差警戒，M6 只提供、不消费）。

---

## 4. 接线 `kernel/lifecycle.py`

### 4.1 触发点（route_observation 语义链）

M5→M6 在**路由阶段**串成：`run_qc → adjudicate → attribute → propose_action → route_observation 落盘`。
`adjudicate`（§7.4，已实现，纯函数）**保持不变**——trust 只由 suspicion 决定。归因**不改 trust**，
只解释 WHY 并给 next_action：

```python
def attribute_and_recommend(store, obs, qc, board, trust):
    if trust in (SUSPECT, FAILED):
        obs.failure_attr = attribute(obs, qc, board)          # §2
        obs.next_action  = propose_action(obs, qc, obs.failure_attr)  # §4.2
        store.append_event("attribution", {                    # 证据事件，非 decision
            "obs_id": obs.obs_id, "round_id": obs.round_id,
            "top_cause": obs.failure_attr.top_cause,
            "confidence": obs.failure_attr.confidence,
            "hypotheses": [...], "next_action": obs.next_action.action.value})
    # TRUSTED: failure_attr=None, next_action=NONE
```
在 `route_observation` 之前调用（它随后 `save_observation` 会持久化 `failure_attr`/`next_action`）。
归因结论落 **`attribution` 事件**（与 `routing` 同类的证据事件）；**人类可读解释是 M8** 的
`attribution_explanation` DecisionRecord。

### 4.2 next_action 生成（`propose_action` 纯函数，§13.2）

```python
def propose_action(obs, qc, attr) -> RecommendedAction   # 只读，不写 store
```
按 `attr.top_cause` 映射 §2.5 的 remedy，`params` 携带队列语义（`RecommendedAction.params`
为 `dict[str,Any]`，schema 不改）：
```
params = {semantics: "detour"|"addition", placement_hint, target_obs: obs.obs_id,
          target_cand: obs.cand_id, supersedes: [obs.obs_id if detour else …],
          created_by_action_id: None}   # ★反向账占位，衍生 obs 创建时由 M7 回填
```
inconclusive（`top_cause=None`）→ DISAMBIGUATION_REPEAT（通用）；置信过低且无动作价值 → NONE。

### 4.3 DecisionRecord 落账（内生动作 vs 将来 agent 提案）

- **内生动作**（M6 生成）：`obs.next_action`（观测字段，属**派生证据**，与 routing 同级），
  **不写 DecisionRecord**；M7 planner 将其包成 `QueueItem{semantics, supersedes,
  created_by_action_id}`（§13.2）直接入队仲裁；
- **agent 提案**（M8）：走 `ACTION_PROPOSAL` DecisionRecord + 强制 `acceptance/rejection` 配对
  （架构 §4.5 审计不变量），与内生动作走**同一校验通道**（预算/约束/布局可行性，M7）。

M6 只产内生动作，不产 DecisionRecord——保持"归因是证据、不是决策"的边界（对照 §13.2
AiiDA data 层不 CREATE 于 workflow：归因不 CREATE 观测值）。

---

## 5. 测试矩阵（`tests/test_qc.py`，M6 段）

| 测试 | 覆盖 | 判据 |
|---|---|---|
| 六注入 top_cause | 每种注入器合成板各一 → `attribute` | `top_cause` == 注入类型，六场景全对 |
| 反驳器拦假阳 | 注入**纯 iid 噪声**（无真效应） | placebo 失败 → `top_cause=None`，不误指某因 |
| 竞争判别 (edge/gradient) | 同板注 edge vs gradient（§2.6） | 对边同号判 edge、异号判 gradient；混叠→inconclusive |
| 竞争判别 (batch/drift) | 同板注 batch_shift vs instrument_drift（§2.2 阶跃 vs 连续；补对抗审查缺行） | 离散批阶跃判 batch、capture 单调判 drift；混叠→inconclusive (v2: 依对抗审查修正) |
| inconclusive 路径 | 弱/歧义信号（p_top<FLOOR 或 margin 小） | `top_cause=None` 且 obs 保持 QUARANTINE，next_action=消歧 |
| risk_map 单调性 | 桶正例数递增 / is_edge 桶 vs 内部桶 | `p_artifact` 随 k 单调增；边缘桶 ≥ 内部桶；键集 == layout well_id |
| 重建等价性 | 增量计桶 vs `rebuild` 全量；改判后重建 | 两路结果相等；改判自动改变正例计数 |
| 双模型隔离 | OS 模式跑一轮（BUILD_PLAN M6 验收） | SUSPECT/FAILED 观测**不在**响应模型训练集（断言指纹） |
| OS 可见红线 | 源扫描 `qc/attribution.py`+`failure_model.py` | 无 `strength/prob/shift/phi/truth` 等注入器/真值引用 |

---

## 6. 风险与开放问题

1. **签名权重是工程标定**（0.4/0.3/0.2/0.1），非文献强制——阈值/权重须在 M9 标定场景集 A 上
   定、评估场景集 B 上验（DEEP_REVIEW 威胁 A：标定/评估分离，防过拟合）。
2. **小样本功效**（5 哨兵/轮、副本 n=2）：单轮内 <10% 小伪影大概率抓不到，跨轮累积才抓得到
   （DEEP_REVIEW §3.3）——归因对小幅信号应更倾向 inconclusive 而非强 commit。
3. **留出伪影**（M9）：签名库无对应假设的注入器（低阶空间高斯随机场）→ 六签名都不直接匹配，
   预期落 inconclusive；这是"没在自证循环"的关键证据，M6 的 inconclusive 路径必须为此稳健。
4. **round_segment 分箱粒度**待定：三分 vs 逐轮，影响 drift 桶的功效与 risk_map 稳定性——
   M9 敏感性扫描定。
5. **偏差校正复归**（DEEP_REVIEW §3.1 backlog）：M6 已在 `evidence` 留估计效应量与 `debias_hint`，
   但去偏还原、以膨胀 alpha 复归训练集留到 backlog——本里程碑不消费，只保接口不悔改。
6. **effect 估计器与 QC 统计量的复用**：§2 效应量应直接复用 M5 structural 检查的板级系数，
   避免二次实现漂移（与 M5 共享一个残差/系数计算入口）。
</content>
</invoke>
