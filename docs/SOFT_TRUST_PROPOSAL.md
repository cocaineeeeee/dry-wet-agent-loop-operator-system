# QUARANTINE → 软信任（partial trust / down-weighting）设计提案

> **状态：提案——待 M9 全量扫描数据裁决。** 不改内核语义：`Routing.QUARANTINE`
> 路由枚举与 `lifecycle.adjudicate` 三级裁决**一律不动**；软化只发生在**聚合策略层**
> （`AggregationPolicy`），把 QUARANTINE 观测以膨胀 alpha 复归训练集，而非硬排除。
> 依据 DEEP_REVIEW §3.1（`docs/DEEP_REVIEW.md:63-67`：信任二值、证据连续，per-point
> alpha 是原则性的桥；中期"alpha 再乘 (1+g(suspicion))"本提案即其落地）。不含任何代码改动。

## 1. 命题与病灶

M9 试点：温和边缘蒸发 `strength=0.2` 档，os 臂（硬隔离 SUSPECT 出训练集）**末轮 regret
反高于 naive**；强档 `0.5` os 完胜。病灶机理：`strength=0.2` 的边缘孔嫌疑分落在
`[0.3, 0.6)` → SUSPECT + QUARANTINE（`expos/kernel/lifecycle.py:90-91`）→ 被
`store.list_observations(trust=TRUSTED)`（`expos/loop.py:305`）整体挡在训练集外 →
GP 失去板缘空间覆盖 → 边缘外插退化 → 末轮 regret 抬高。**猜想：弱档该"降权"而非"排除"。**

## 2. 库 / 文献证据（soft down-weighting vs hard exclude）

| 机制 | 形态 | 代码/文献 |
|---|---|---|
| sklearn per-point `alpha` 数组 | 逐点方差加对角 `K[diag]+=alpha`；`normalize_y` **不**缩放 alpha（须除 y_std²） | `references/scikit-learn/sklearn/gaussian_process/_gpr.py:349-350`,`275-280`,`66-76` |
| Ax 每观测 `SEM²→Yvar` 软降权 + `ABANDONED`+`fit_abandoned` 硬排除（两杠杆并存） | 膨胀 SEM=降权；NaN SEM=学同方差 | `references/Ax/ax/adapter/torch.py:474-478`;`references/Ax/ax/core/data_utils.py:62,92` |
| botorch RelevancePursuit：逐点 `rho_i` **加**到噪声方差 `Σ=Σ_base+diag(ρ)` 软剪裁离群 | 加性方差膨胀（非删点） | `references/botorch/botorch/models/likelihoods/sparse_outlier_noise.py:121,418-421` |
| botorch `train_Yvar` = 逐点观测**方差**（非 std）经 Standardize 缩放 | 固定异方差噪声 | `references/botorch/botorch/models/gp_regression.py:172-180,123-128` |
| RCGP Plateau-IMQ 权重 `w=β·(1+(( \|r\|−L)/c)²)^(−1/2)`（重尾软衰减，有界 PIF） | 幅度无界腐败软剪裁 | `expos/models/robust_gp.py:183-195`；arXiv 2311.00463 / 2511.15315 |
| aiida 分级退出码（`invalidates_cache` 可复用 vs 致命 + 100/110/140 严重度带） | **分级**而非二值 | `references/aiida-core/src/aiida/engine/processes/exit_code.py:30-32`;`.../calcjob.py:530-549` |
| pandera `raise_warning`：SchemaWarning 保留数据 vs SchemaError 拒收（软/硬开关） | 检查级软硬切换 | `references/pandera/pandera/backends/pandas/base.py:144-153` |
| optuna Pruner：`prune()→bool` **纯二值杀死**（无软权重；反面对照） | 硬闸，非降权 | `references/optuna/optuna/pruners/_base.py:14-33`,`_percentile.py:196-213` |

**结论**：主流 BO 栈（sklearn/Ax/botorch）的降权全走**逐点噪声方差**通道——与 expos
现有 `per_point_alpha` 路径（`expos/models/response_gp.py:139-145`）同构；「加性 rho」
与「乘性膨胀」两种形态都有先例。baybe 无此通道（负例，`references/baybe/baybe/surrogates/base.py:468`
`_fit` 签名无 Yvar）。expos 采**乘性膨胀**（下 §3），与 ReplicateVariance 的 y² 尺度自洽。

## 3. 设计：`SoftTrustAggregation`（新聚合策略，os-soft 臂）

复用 `ReplicateVarianceAggregation`（`expos/qc/policy.py:311-347`）的副本方差 alpha 基线，
额外把 **routing==QUARANTINE** 的观测合成为**内存态** TRUSTED+TO_RESPONSE_MODEL 训练样本
（复刻 `MedianAggregation` 合成 `ObservationObject` 的成例，`expos/qc/policy.py:289-306`），
以膨胀 alpha 进训练集。**落盘观测的 routing 仍是 QUARANTINE，审计与枚举不变**（软化只在
聚合返回的训练样本上，不持久化）。

- **信任权重** `w(s)`，`s`=suspicion（=QUARANTINE 观测的 `obs.trust_confidence`，
  `adjudicate` 已回填，`expos/kernel/lifecycle.py:91`；`expos/kernel/objects.py:308`）：
  - **默认（线性斜坡，边界连续）**：`w(s)=clip((suspect_high−s)/(suspect_high−quarantine_low), w_min, 1)`。
    `s→0.3⁺` 时 `w→1`（近满信任）；`s→0.6⁻` 时 `w→0`（与 0.6 硬隔离**连续**衔接）。
  - **备选（IMQ 重尾，借 RCGP）**：`w(s)=(1+((s−quarantine_low)/c)²)^(−1/2)`——对"略超 0.3"更宽容，
    只对逼近 0.6 者强衰减（`expos/models/robust_gp.py:191-194` 同式）。
- **alpha 合成（乘性膨胀）**：`alpha_i = alpha_base_i / w(s_i)`。`alpha_base_i` 取该点副本方差；
  QUARANTINE 多为单孔 → 用组间中位方差兜底（沿用 ReplicateVariance 的 `fallback_var`）。
  **选乘性除法而非 `max()`**：降权=精度×w=方差÷w，是异方差 GP/GLS 的正解（对齐 Ax
  `SEM²/w`、botorch `rho` 膨胀语义）；`max()` 会在副本方差已大时**吞掉**嫌疑信号，失效。
  `w_min` 下限防 `alpha=∞`（等价隔离），封顶膨胀 `1/w_min`。TRUSTED 观测 `w≡1`（行为同 os）。
- **硬隔离不变量（强档保证的根）**：只触碰 `routing==QUARANTINE`。
  `suspicion≥0.6`→TO_FAILURE_MODEL、硬检查失败→FAILED，**一律不复归**。

## 4. 修改点清单（file:function）——本提案不实施

1. `expos/qc/policy.py`：新增 `class SoftTrustAggregation(AggregationPolicy)`（继承/组合
   `ReplicateVarianceAggregation`）。`prepare()` 新增可选入参 `quarantine: list[ObservationObject]=()`
   （**向后兼容**：既有策略忽略之），合成软信任样本 + 膨胀 alpha 拼接返回。
2. `expos/loop.py:305-307` & `:261-264`：向 `aggregation.prepare(...)` 额外传
   `store.list_observations(trust=SUSPECT)` 中 `routing==QUARANTINE` 者（新增关键字参数，
   naive/robust/os 三臂调用不受影响——它们的 prepare 签名默认吞掉该参数）。
3. `expos/loop.py:_policies_for_mode`：新增 `mode=="os-soft"` 分支 = QCPolicy（同 os 裁决）
   + `SoftTrustAggregation()` + TrustAwarePlanner + TemplateAgentPolicy。
4. `expos/eval/run_cell.py:34 _ARM_TO_MODE`：加 `"os-soft":"os-soft"`。
5. **不改**：`lifecycle.adjudicate`、`Routing` 枚举、`response_gp.fit` 守门（合成样本已是
   TRUSTED+TO_RESPONSE_MODEL，天然过闸，`expos/models/response_gp.py:122-126`）、
   `AggregationPolicy` Protocol 现有签名（新参数带默认值）。

## 5. 默认超参

- `w_min = 0.05`（膨胀封顶 20×；防奇异 alpha，边界近似隔离）。
- 权重形态默认**线性斜坡**（可解释、边界连续、单行）；IMQ 备选参数 `c=quarantine_low` 起测。
- 阈值 `suspect_high/quarantine_low` 复用域配置（`domains/crystal.yaml:46` = 0.6/0.3），
  **不新增标定量**——w(s) 完全由既有阈值张成，A/B 标定分离不受影响。

## 6. edge0.2 档预期行为

`strength=0.2` 边缘孔 `s∈[0.3,0.6)`（QUARANTINE 主导）→ 以 `w≈0.5`（`alpha≈2×` 副本基线）
复归训练集：GP **恢复板缘覆盖**、温和边缘偏差被降权不主导后验。预期末轮 regret 由"高于
naive"回落到 **≤ naive 且 ≤ os**；污染利用率（M9 §3 指标 3）从 os 的近零**温和上升**（按设计
纳入弱污染）但仍 < naive。在无观测落入 `[0.3,0.6)` 的场景，os-soft 与 os **逐比特一致**
（snapshot 相同）——软化只在 QUARANTINE 带激活。

## 7. 风险与"强档行为不变"保证

- **风险 R1：软信任把 S0.demo 强档（strength=0.5）假最优放回。** 保证：强边缘孔 `s≥0.6`
  → TO_FAILURE_MODEL，**不在** QUARANTINE 集 → 永不复归；即便复归也被 `1/w` 膨胀压不成
  主导。**依赖**：强档嫌疑分须稳定 ≥0.6——由 M9 检出率-幅度曲线（§3 指标 5）验证其分布。
- **风险 R2：标定漂移使强档孔跌破 0.6。** 缓解：线性 w 在 `s→0.6⁻` 已 `w→w_min`（近隔离），
  边界连续无跳变；验收须核 `strength=0.5` 孔 suspicion 分位。
- **风险 R3：QC 税（S1 零伪影）恶化。** 近净点 `s` 低、`w≈1`、alpha 几不膨胀 → 预期
  regret 差仍 ≤5%（M5 验收线）。

## 8. 验收实验（现 sweep 矩阵，`scripts/gen_sweep.py`）

`gen_sweep.py --arms naive,os,os-soft`，跑 `S2.edge_evaporation`（strength∈{0.05,0.10,0.15,0.20,0.35}）
+ S0.demo（第 3 轮 0.5 强档）+ S1.zero；评估集 B、N=20 种子、置换检验（M9 §3 统计）。
**预注册判据**（比 M9 §3 指标 1 末轮 simple regret，配对同 base 同伪影）：
- **PASS-fix**：edge {0.10,0.15,0.20} 档 `regret(os-soft) ≤ regret(naive)`（CI 内）**且** `≤ regret(os)`
  ——软信任修好弱档回归（核心主张）。
- **PASS-strong**：S0.demo `regret(os-soft) ≈ regret(os)`（CI 重叠）**且**错误最优命中率
  （指标 2）不升——强档行为不变、假最优未回归。
- **PASS-tax**：S1.zero `regret(os-soft)−regret(naive) ≤ 5%`。
- **诊断**：污染利用率（指标 3）应 `naive ≥ os-soft ≥ os`；交叉表核 os-soft 纳入的污染
  按 suspicion 带分布——弱污染纳入、强污染仍隔离。
- **KILL**：若 os-soft 在 0.5/S0 劣于 os 或错误最优命中率上升 → 拒（放回假最优）；
  若 edge0.2 os-soft 不优于 os → 拒（软信任无增益）。

## 9. 引用

- 内部：`docs/DEEP_REVIEW.md:63-67`（per-point alpha 桥）、`docs/REFERENCE_MAP.md:173`
  （异方差逐点 alpha）、`docs/M9_PROTOCOL.md §3`（指标）、`expos/models/response_gp.py:139-145`
  （normalize_y 下 alpha 除 y_std²）、`expos/qc/policy.py:311-347`（ReplicateVariance 基线）。
- 库：sklearn `_gpr.py:349-350`；Ax `torch.py:474-478`；botorch `sparse_outlier_noise.py:418-421`、
  `gp_regression.py:172-180`；aiida `exit_code.py:30-32`；pandera `pandas/base.py:144-153`；
  optuna `_percentile.py:196-213`（二值反例）。
- 文献：RCGP（Altamirano et al. 2024, arXiv:2311.00463，IMQ 加权后验）；Plateau-IMQ
  收紧（arXiv:2511.15315）；scikit-learn GPR 文档
  <https://scikit-learn.org/stable/modules/gaussian_process.html>；BoTorch RelevancePursuit
  robust GP（Ament et al. 2024, arXiv:2410.24222）。
