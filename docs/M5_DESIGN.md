# M5 设计规格 —— QC 三级检查 + 裁决单一注入点

> 权威蓝图：`docs/ARCHITECTURE.md` §7（QC 层）、§4（schema）；统计配置 REFERENCE_MAP §11.4；
> 移植配方 §13.5（esda/DoWhy）、§13.2（FireWorks 动作语义）、§13.3（EventKind）；红线 DEEP_REVIEW §3.2/§2.C。
> 本文只规定 M5，不动代码。所有权：仅新建本文件。

---

## 1. 范围与非目标

**M5 做**：三级检查器 `qc/checks.py` + 纯 numpy 统计原语 `qc/stats.py`（后者并行实现，本文只列签名）；
把裁决抽成 `VerdictPolicy` 单一注入点，OS 模式接线（run_qc → 既有 `adjudicate`）；新增 `qc_report/qc_flag` 事件。
再把"副本聚合"抽成与 `VerdictPolicy` **对称**的 `AggregationPolicy` 单一注入点（§3B），
并导出供 M6 归因消费的**板级上下文 `BoardContext`**（§2.3）。
(v2: 依对抗审查修正)

**M5 不做（属 M6/M7，防边界蔓延）**：归因引擎 `attribution.py`（§7.2 假设签名/反驳器写 `top_cause`）、
失败模型 `failure_model.py`（§7.3 Beta-Bernoulli risk_map）、失败感知规划器（§9/M7）。
M5 只产出 `QCReport` 与 `trust/routing`，`failure_attr`/`next_action` 仍为 `None`。
反驳器原语（`refute_*`）在 stats 中就位供 checks 内部去伪，但**签名→top_cause 的归因判决留给 M6**。

---

## 2. 模块切分

### 2.1 `qc/stats.py`（纯 numpy 统计原语，无 schema 依赖；并行已实现——签名清单）

| 函数签名 | 出处 | 返回 |
|---|---|---|
| `median_polish(grid, eps=0.01, maxiter=10) -> {overall,row,col,residuals}` | §11.4/§13.5 | Tukey 中位数抛光；行/列效应 + 去趋势残差 |
| `moran_check(y, grid=(6,8), P=9999, alt="greater") -> {I,EI,p,z}` | §13.5 | queen 邻接、行标准化、**不折叠单尾 greater**，p 地板 1e-4 |
| `robust_mad_z(residuals) -> ndarray` | §11.4 | MAD 稳健 z（median/1.4826·MAD） |
| `cohens_d(a, b) -> float` | §11.4 | 配对效应量（0.2/0.5/0.8 档） |
| `fisher_combine(pvals) -> float` | §11.4 | 跨轮累积（副本方向证据合并） |
| `cusum_ewma(series, k=0.5, h=5, lam=0.2, L=3) -> {breach:bool,stat,which}` | §11.4 | self-starting 双挂控制图【设计稿；实装为 CUSUM 单挂，EWMA 未接线（R5 REF-4 注记）】 |
| `control_band(history, k=3.0) -> (lo,hi)` | §7.1/§11.4 | 哨兵历史均值±kσ |
| `sbb_alpha(p) -> float` | §11.4 | Sellke-Bayarri-Berger 校准 α(p)=[1+(−e·p·ln p)⁻¹]⁻¹；**p≥1/e 夹取 α=0.5**（校准失效区，视作无证据）。α∈[0,0.5]，随 p↓ 而↓；嫌疑分转换见 §2.2 (v2: 依对抗审查修正) |
| `refute_placebo(statistic_fn, labels, n=999) -> {passed,p_zero,evidence}` | §13.5 | 打乱标签判效应塌零（PASS⇔ p_zero>0.05 ∧ \|均值\|<0.1\|obs\|） |
| `refute_subsample(statistic_fn, data, frac=0.8, n=100) -> {passed,p_in,std}` | §13.5 | 子采样判稳定（PASS⇔ p_in>0.05 ∧ std<0.5\|obs\|） |

纪律：NaN 孔剔除须同步删 W 行列；置换/反驳一律经验分位（n<100 不用正态近似，§13.5 坑）。

### 2.2 `qc/checks.py` —— 三级检查器

入口（§7.1）：`run_qc(exp, obs_list, history) -> dict[obs_id, QCReport]`。
`history`=既往轮已裁决观测（哨兵控制带 + 跨轮副本累积用）；纯读、禁触 truth（BUILD_PLAN 纪律）。
残差定义：`残差 = 值 − 候选组均值`；哨兵（全板同条件）为最干净位置信号源（§7.1）。
**顺序纪律：先 median_polish 去趋势 → 再对残差检验**（§7.1/§11.4）。

| 级别 | 检查（QCCheck.name） | 输入 | 算法/参数默认 | evidence 字段 | 嫌疑分 score 映射 |
|---|---|---|---|---|---|
| hard | `missing_nan` | 单 obs.result.value | 缺失/NaN/None | `{reason}` | passed=False→score=1.0 |
| hard | `out_of_range` | value 物理量程 | 越量程（域 YAML `value_range`） | `{value,range}` | 同上 |
| hard | `exposure_illumination` | instrument_meta | exposure/illumination∉[0.5,2.0] | `{exposure,illumination}` | 同上 |
| reference | `sentinel_control_band` | 本轮哨兵值 + history 哨兵 | `control_band(hist,k=3)`；**前 2–3 轮只记不判**（§11.4） | `{band,value,rounds_seen,armed}` | 越带→`min(1,\|z\|/3)`；未 armed→0 |
| reference | `replicate_cv` | 同 cand 副本组 | 组 CV>阈(0.15) ∧ 组内 max\|z\| | `{cv,max_z,n_rep}` | `min(1,cv/阈)` |
| structural | `edge_effect` | is_edge×中心配对 + 哨兵边缘回归 | n=2–3 **不做单轮显著性**；报 `cohens_d` + 跨轮 `fisher_combine`（§11.4） | `{d,dir,fisher_p,edge_wells}` | d 归一 → `1−2·sbb_alpha(fisher_p)`（§2.2 校准）；牵连边缘孔 |
| structural | `row_col_gradient` | median_polish 行/列效应 | 效应量/σ 阈值化（loess 不用，8/6 点带宽不稳） | `{row_eff,col_eff,sigma}` | `min(1,\|eff\|/σ/阈)`；牵连极端孔 |
| structural | `spatial_moran` | 残差网格 | `moran_check(grid=(6,8),P=9999,greater)`；48 格功效低→**定位筛查/排序** | `{I,EI,p}` | `1−2·sbb_alpha(p)`（§2.2 校准；减 EI 判显著） |
| structural | `batch_shift` | 残差 by solution_batch | `cusum_ewma`；前 2–3 轮只记（§11.4） | `{shift,breach,which,armed}` | 越限→1；未 armed→0 |
| structural | `temporal_drift` | 残差 vs capture_index | `cusum_ewma` 按 capture 序 | `{corr,breach,armed}` | 越限→1 |
| structural | `isolated_outlier` | median_polish 残差 | MAD z，\|z\|>3.5（§11.4） | `{z,well_id}` | `min(1,\|z\|/3.5)` |

去伪纪律：每个 structural 检查触发后跑 `refute_placebo`(打乱空间/批次标签) + `refute_subsample`(0.8)；
**未过反驳器 → score 降级**（evidence 记 `{placebo_passed,subsample_passed}`），但**归因判决留 M6**（§13.5 两判据方向相反勿混）。

**合成（§11.4 关键决定，v2 修正 SBB 方向与量纲）**：
1. **SBB 方向**：α(p)=[1+(−e·p·ln p)⁻¹]⁻¹ 是"零假设为真"的保守下界——**α 小 = 证据强 = 更可疑**。
   故嫌疑分 = `1 − α` 才与"越高越可疑"同向（旧文档直接用 α 或 SBB(p) 方向倒置且非单调）。
2. **量纲统一**：`α∈[0,0.5]` ⇒ 裸 `1−α∈[0.5,1]` 带 0.5 **地板**，与 `min(1,|z|/3)` 等 [0,1] 分**不同量纲**；
   跨检查取 max 时 SBB 类恒 ≥0.5 会淹没 z 类、**抬爆 QC 税**。故统一线性拉伸到 [0,1]：
   `suspicion_cal(p) = max(0, 1 − 2·α(p))`——单调随 p↓ 而↑，`p≥1/e`（α=0.5）时 suspicion=0。
3. **合成**：**全部检查（SBB 类与 z 类）先经同一 `suspicion_cal` / `min(1,|量|/阈)` 校准到 [0,1]** →
   `QCReport.suspicion = max(检查 score)`（跨检查取 max，保守）。
Fisher/Cauchy 合并另存 `evidence` 作总体证据，**不直接用 1−p 当分**。`flags`=触发检查 name 列表。
(v2: 依对抗审查修正)
证据面（§sim_base）：`secondary/exposure/illumination/capture_index/solution_batch/is_edge/block_id` 均已带噪（非 1-bit oracle），QC 只做统计推断不做阈值查表。

### 2.3 板级上下文 `BoardContext`（M5 计算并导出，M6 归因直接消费）

**问题（对抗审查·跨文档）**：M6 `attribute(obs, qc, board)` 的 `board` 参数**在 M5 无生产者**——
若 M6 自行重算残差/系数，则与 QC 二次实现漂移（DEEP_REVIEW §威胁 A 的自证风险 + M6 §6.6 复用要求）。
**设计**：`run_qc` 内部算好的板级量一次性物化为 `BoardContext`，**与逐 obs `QCReport` 一同返回**，
M6 只读不重算（同一份证据，不新取真值）。全部字段 **OS 可见**（源自残差/哨兵/secondary/*_meta）。

```python
@dataclass(frozen=True)
class BoardContext:              # M5 导出；qc/checks.py 产出，qc/attribution.py 消费
    round_id: int
    residual_grid: np.ndarray    # 6×8 median_polish 去趋势残差矩阵（NaN 孔保 nan）
    resid_scale: float           # 候选组残差稳健尺度 σ=MAD（供 M6 τ_r=0.5σ）
    edge_coef: float             # is_edge 哑元回归系数（边缘抬升幅度，§2.6 ΔR² 之一）
    edge_r2: float               # is_edge 回归解释的残差方差占比
    gradient_slope: dict         # {"row": 斜率, "col": 斜率}（连续行/列线性回归，§2.6 之二）
    gradient_r2: float           # 行/列线性回归解释的残差方差占比
    moran_I: float; moran_EI: float; moran_p: float   # spatial_moran 板级结果
    batch_shift: dict[str, float]     # solution_batch -> 该批残差组均值位移
    drift_corr: float            # 残差–capture_index 相关（temporal_drift）
    sentinel_dev: dict[str, float]    # well_id -> 哨兵控制带偏离（越带幅度）
    suspicion_by_well: dict[str, float]   # well_id -> 逐孔嫌疑分
```

`run_qc` 签名相应改为 `run_qc(exp, obs_list, history) -> tuple[dict[obs_id, QCReport], BoardContext]`；
`QCPolicy.apply` 拿到 `board` 后原样传给 M6 接线（M6 §2.1/§4.1）。**edge/gradient 的 ΔR² 双回归系数
（`edge_coef/edge_r2/gradient_slope/gradient_r2`）是 §2.6 竞争判别的权威来源，M6 不得另拟。**
(v2: 依对抗审查修正)

---

## 3. VerdictPolicy —— 裁决单一注入点（DEEP_REVIEW §3.2 红线）

**问题**：现状 `loop._route_naive` 内联；M5 接 QC 若长出 `if mode==...` 分叉，公平性主张（"两臂仅在裁决策略上不同"）名存实亡。

**设计**：`expos/qc/policy.py` 定义协议 + 两实现，loop 主体零 mode 分支。

```python
class VerdictPolicy(Protocol):
    def apply(self, store, exp, obs_list, history) -> None: ...   # 落 obs.trust/routing/qc + 事件

class NaivePolicy:   # 对照组：逐字段等价 M4 _route_naive（回归红线）
    def apply(...):  # 全 TRUSTED + TO_RESPONSE_MODEL + conf=1.0；发 routing_bulk 事件；obs.qc 保持 None
class QCPolicy:      # OS 组
    def __init__(self, trust_policy=TrustPolicy(), min_armed_rounds=3): ...
    def apply(...):  # reports=run_qc(exp,obs_list,history)；逐 obs：obs.qc=report→发 qc_report
                     # → route_observation(store,obs,trust_policy)【复用既有 lifecycle】→ SUSPECT/FAILED 发 qc_flag
```

裁决内核不变：`QCPolicy` 只到 `run_qc`，最终判决仍走既有纯函数 `lifecycle.adjudicate`（公理 7：不吃 agent 产物）。

**loop.py 最小 diff 轮廓**：
1. 删 `run_loop` 顶部 `if mode != "naive": raise`；改为 `policy = NaivePolicy() if mode=="naive" else QCPolicy()`（`compare` 由 M9 双跑，非新分支）。
2. 状态迁移 `advance_status(QC_DONE/ROUTED)` 留在 loop（结构不变）。
3. 把 `_route_naive(store, obs_list)` 一行替换为 `policy.apply(store, exp, obs_list, history)`；`history=store.list_observations()`（跨轮哨兵/副本）。
4. `_route_naive` 函数体迁入 `NaivePolicy.apply`（逐字节保留 routing_bulk 事件），loop 不再直接引用。
5. 响应模型重训仍 `store.list_observations(trust=TRUSTED)`——SUSPECT/FAILED 自然不入训练集（无需新分支，M6 断言）。

---

## 3B. AggregationPolicy —— 副本聚合单一注入点（与 VerdictPolicy 对称）

**问题（对抗审查·最高优先，跨文档）**：M9 声称三臂"唯二差异 = 裁决策略 + **聚合策略**对象"，
但 M5/M6 从未设计聚合策略——三臂对 `robust-blind`（副本中位数）与 `os`（副本方差→逐点 alpha）
的差异**无落点**，公平性主张（"仅在两个策略对象上不同"）名存实亡。
**设计**：与 `VerdictPolicy` 对称，`expos/qc/aggregation.py` 定义协议 + 三实现；
"写入响应模型前如何合并同候选副本"从 loop 主体抽出，零 `if arm==` 分支。

```python
class AggregationPolicy(Protocol):
    # 输入同候选的副本观测组（已裁决），输出 (y_agg, alpha) 供响应模型逐点 alpha 接口
    # （response_gp.py 已预留"逐点 alpha（副本方差）留 M5+ 接口"，见其 __init__ 注释）
    def aggregate(self, replicates: list[ObservationObject]) -> tuple[float, float]: ...

class NaiveAgg:        # naive 臂：逐孔直用
    def aggregate(...):  # y_agg=副本均值；alpha=noise_sd²（常量，不看离散度）
class RobustBlindAgg:  # robust-blind 臂：稳健聚合，无 QC/无隔离
    def aggregate(...):  # y_agg=median + Huber(δ=1.345·MAD) 位置估计；alpha=max(noise_sd², s²/r)
                         # n=2 退化（median=mean、零保护）→ 见 M9 §1：保守选 + 副本升 3
class OSAgg:           # os 臂：信任感知聚合
    def aggregate(...):  # 仅聚合 TRUSTED 副本：y_agg=均值，alpha 按副本方差定（方差大→alpha 大）；
                         # SUSPECT 降权（大 alpha 软纳入或按策略排除）、FAILED 隔离（不进聚合）
```

三臂**共用同一 `AggregationPolicy` 注入点**，只换实现对象。`OSAgg` 的"逐点 alpha"是
DEEP_REVIEW §3.1"信任二值、证据连续"的桥（M5 顺手做的那一步）；它与 `VerdictPolicy` 正交：
裁决定 trust/routing，聚合定进模型的 (y, alpha)。M9 §1 三臂表直接引用本节三实现。
(v2: 依对抗审查修正)

---

## 4. 事件词汇新增（§13.3 EventKind 枚举化建议）

**降级（对抗审查）**：EventKind **枚举化改内核越界**（M5 所有权仅本文档 + `qc/`，不动 `kernel/objects.py`）。
故 M5 **只新增字符串 kind + 描述**，`EventKind(str, Enum)` 枚举化整体**移入 backlog**（连同 `OwnershipInfo`
式嵌套关联）。M5 新增三个 kind（前两个 M5 自产，`attribution` 为 M6 产但**在此一并登记词表**，
避免 M6 再动词汇）：

| kind | 里程碑 | 触发 | 载荷 |
|---|---|---|---|
| `qc_report` | M5 | QCPolicy 逐 obs 完成 QC | `{obs_id, round_id, suspicion, flags:[name], n_checks, n_failed}` |
| `qc_flag` | M5 | 该 obs 判 SUSPECT/FAILED | `{obs_id, round_id, trust, routing, top_check, score}` |
| `attribution` | M6 | 归因完成（M6 §4.1，此处仅登记） | `{obs_id, round_id, top_cause, confidence, hypotheses, next_action}` |

现有 kind（不变）：`status_transition, routing, routing_bulk, reclassification, resolution_conflict,
round_designed, resume, model_updated, decision`。`routing` 事件继续由 `route_observation` 发——
QC 只多产 `qc_report/qc_flag`，裁决事件链不变。配覆盖测试：每个字符串 kind 有描述 dict（§13.3 词表不漏描述）。
(v2: 依对抗审查修正)

---

## 5. QC 税与验收标准

### 5.1 QC 税（DEEP_REVIEW §2.C / §5，M5 验收线）

**指标**：零伪影场景假阳性率 = 被判 SUSPECT/FAILED 的（非控制）观测比例。
**测量方法**：`run_loop(crystal, mode=os, rounds=8, artifact_scenario=[])`（域 YAML 空场景）→
统计 8 轮全部候选观测中 `trust≠TRUSTED` 比例。**验收线 ≤5%**。
**轮数 8 与 M9 §3.4 QC 税、S1 零伪影场景统一**（对抗审查：原 4 轮与 M9 8 轮口径不一致）。
(v2: 依对抗审查修正)
关键纪律保证低假阳性：前 2–3 轮控制图/批次/漂移只记不判（§11.4 冷启动）、副本不做单轮显著性、
Moran 单尾 greater（不双尾放大）、structural 经反驳器去伪。

### 5.2 M5 完整验收清单（对齐 BUILD_PLAN M5 行并扩展）

- [ ] `pytest tests/test_qc.py::TestChecks` 全绿：边缘/梯度/Moran/批次/漂移/哨兵/副本 CV 各检查合成板命中且无大面积误报（BUILD_PLAN 原文）。
- [ ] 六种伪影合成板：各注入器命中对应 structural/reference 检查（方向正确）。
- [ ] 干净板（无场景）8 轮：QC 税 ≤5%（§5.1，与 M9 统一 8 轮）。
- [ ] `NaivePolicy` 下 loop 行为与 M4 **逐字段等价**（obs/事件回归，见 §6）。
- [ ] loop 主体零 mode 分支（grep 无新增 `if mode==`）；裁决唯一入口仍 `adjudicate`。
- [ ] 置换/Moran 检验确定性：固定种子输出恒定。
- [ ] `qc/`、`checks.py`、`stats.py` 无 truth 引用（BUILD_PLAN 纪律，grep 断言）。
- [ ] CHECKPOINTS.md 落 M5 条目（状态/命令/输出摘要/偏离）。

---

## 6. 测试矩阵

| 用例 | 构造 | 断言 |
|---|---|---|
| 六伪影命中 | 每注入器单独合成板（edge/gradient/glare/dust/batch/drift 各一，25–50% 幅度） | 对应检查 score≥阈、`flags` 含其 name、牵连孔集合正确 |
| 干净板不误报 | 空 `artifact_scenario`，8 轮（与 M9 统一） | SUSPECT/FAILED 比例 ≤5%；structural 检查 passed |
| 置换检验确定性 | 同板同种子跑 2 次 `moran_check`/`refute_*` | I/p/passed 逐位相同 |
| 控制图冷启动纪律 | 哨兵历史 <3 轮 | `sentinel_control_band`/`batch_shift`/`temporal_drift` armed=False、score=0（只记不判） |
| 策略对象等价性（回归） | 同种子 M4 naive 运行 vs `NaivePolicy` 运行 | observations JSON、events.jsonl（含 routing_bulk）逐字段一致；summary 相同 |
| 裁决入口纯性 | QCPolicy 产 QCReport → adjudicate | trust/routing 与 §7.4 阈值表（0.6/0.3）一致；hard 失败→FAILED |
| 反驳器去伪 | 打乱标签的伪相关板 | placebo 判塌零、structural score 降级、evidence 记 passed 标志 |

---

## 7. 风险与开放问题

1. **5 哨兵历史太短 → EWMA/CUSUM 参数**：固定限需 20+ 子组，前 2–3 轮 armed=False 是缓解；
   `min_armed_rounds` 设 3 是工程判断，短预算（≤8 轮）下控制图窗口小，跨轮累积（Fisher）是主力（DEEP_REVIEW §3.3）。
2. **多检查 max 合成的保守性**：取 max 偏灵敏、可能抬高 QC 税；SBB 校准 + 反驳器去伪对冲。
   若 §5.1 超 5%，回调项：提高 quarantine_low、或对未过反驳器的 structural 直接置 score=0。
3. **自证循环（DEEP_REVIEW §2.A）**：六检查对六注入器一一对应，留出伪影/敏感性扫描属 M9，M5 不解决。
4. **per-point alpha 顺手做（DEEP_REVIEW §3.1）**：TRUSTED 观测 alpha 按副本方差设——本文标记为 M5 可选增量，
   若触及 `models/response_gp.py` 接口则延至 M6，避免越界改响应模型。
5. **compare 模式归属**：M5 只开 os 单跑；naive/os 同种子双跑与三臂（+robust-blind）由 M9 编排，非本文范围。
