# M9 对比实验评测协议书（预注册式）

> 2026-07-10 起草。骨架 = DEEP_REVIEW.md 的五项硬约束（三臂、QC 税、留出伪影、
> 检出率-幅度曲线、标定/评估分离）逐条落实。指标依据 REFERENCE_MAP §11.3；轨迹格式
> 依据 §13.6 建议 + Olympus Campaign 字段子集；主图配方依据 §12.6（CLSLab + best-so-far
> ±std band）。本协议在跑数前冻结假设与判据（§7），跑后只填数不改判据。

---

## 0. 目的与主张（可证伪化）

证明一条主张的**可信性限定版**（原"regret 优越"读法已被 H1_REJECTED 证伪，更正如下）：
当伪影是*结构性*偏差时（边缘/梯度/批次），信任路由（os 臂）在**结论可信性**上的增量价值
——**假最优拒斥、训练集污染防护、可审计归因**——是 provenance-blind 鲁棒统计（robust-blind 臂）
**所不能复现**的；但这一增量**不表现为 regret 优越**：硬隔离在多数结构档 regret 反劣于鲁棒
统计（如实记为负结果，H1_REJECTED），存在软/硬相变边界。孤立/对称伪影上二者预期追平。
P3（Le Cam 两点法）证 provenance-aware 为**必要**、非证硬隔离唯一。一切设计围绕"让这条主张
更可证伪"服务（DEEP_REVIEW §6）。

> **判据冻结原则**：本节（§0）为随证据更正的**立场句**；§3/§7 的预注册判据文本一律不动
> （跑后只填数不改判据），deviation 一律走 §4.9 矩阵备案 / CHECKPOINTS 压测更正记录。

---

## 1. 三臂定义（代码面共享声明）

三臂**共享 M4 全部代码**（域配置、模拟器、注入器、layout、GP 响应模型、采样器、预算、
checkpoint）。**唯二差异**：`裁决策略`对象（per-obs 的 trust/routing/alpha 如何定，即 M5 §3
`VerdictPolicy`）与`聚合策略`对象（写入响应模型前如何合并副本，即 M5 §3B `AggregationPolicy`）。
loop 主体零 `if mode==` 分支（DEEP_REVIEW §3.2 红线）；方法节一句话："三臂仅在裁决策略对象与
聚合策略对象上不同。" **两个注入点均在 M5 设计并导出**（对抗审查：M9 曾引用不存在的聚合策略
对象，现由 M5 §3B `NaiveAgg`/`RobustBlindAgg`/`OSAgg` 落地，本节三臂表直接映射之）。
(v2: 依对抗审查修正)

| 臂 | 裁决策略 | 聚合策略 | 路由/规划 | 定位 |
|---|---|---|---|---|
| `naive` | 常量 TRUSTED，conf=1，alpha=noise_sd（现状 `_route_naive`） | 副本均值 | 无 | 现状对照，稻草人 |
| `robust-blind` | 常量 TRUSTED，但 alpha 按副本离散度膨胀 | 副本**中位数** + **Huber(δ=1.345·MAD)** 稳健 y（**副本数升为 3**，中位数对孤立伪影有真保护）；退化 n=2 时见下"退化处理" | 无 QC、无失败模型、无失败感知规划 | **信任盲的工程上限** |
| `os` | M5 三级 QC 裁决（TRUSTED/SUSPECT/FAILED）+ SBB 校准嫌疑分 → per-point alpha | TRUSTED 用副本方差定 alpha；SUSPECT 降权；FAILED 隔离 | M5-M7 全链：QC→归因路由→失败模型→失败感知规划（复测/歧义消解/加对照） | 完整 OS |

**robust-blind 精确算法**（每候选、每轮）：取该候选 r 个副本 y₁..yᵣ →
m=median(yᵢ)，s=MAD(yᵢ)=median(|yᵢ−m|)·1.4826 → Huber 位置估计 μ̂ 以 δ=1.345·s 迭代
（IRLS，≤10 次；s=0 时 μ̂=m）→ 写入响应模型的 y=μ̂，alpha=max(noise_sd², s²/r)。
**不看 truth、不看 exposure 证据、不产 QC 事件、不路由**——纯统计吸收。

**副本数 r=3（对抗审查，从 n=2 上调）**：n=2 时 `median≡mean`——中位数对**单个**孤立伪影
**零保护**（两副本一污染，中位数=均值仍被拉偏），且"取较保守者"与 median 定义自相矛盾。
故**域配置副本数升为 3**（三副本中位数可屏蔽 1 个离群副本，robust-blind 的"上限"主张才成立）。
**须同步修改**：`domains/*.yaml` 的 `replicate_plan.n_replicates=3`、以及**每轮容量计算**
（48 孔 − 5 哨兵 = 43 候选孔位 → 每候选 3 副本 ⇒ 每轮唯一候选数约 ⌊43/3⌋≈14，见 §2/§5 更新）。
**n=2 退化处理**（若某域仍配 n=2）：中位数=均值退化时，`RobustBlindAgg` 明确取
**方向保守选**——`objective.direction=maximize` 时取 `min(y₁,y₂)`（悲观、抵抗被伪影抬高的假高），
`minimize` 时取 `max`；这与 `naive`（取均值）在同一对副本上给出**可测的确定性差异**（非恒等），
从而 n=2 下两臂仍可区分。alpha 仍 `max(noise_sd², s²/r)`。
(v2: 依对抗审查修正)

---

## 2. 场景矩阵

所有场景在 crystal 域（6×8 板、5 哨兵/轮、**副本 n=3**、8 轮、384 孔预算）上运行。
（v2：副本从 n=2 升为 3——见 §1 退化处理；须同步 `domains/crystal.yaml` 与容量：每轮 48 孔 −5 哨兵
=43 候选孔 ⇒ 每候选 3 副本 → 唯一候选约 14/轮，8 轮共约 112 唯一候选。384 孔总预算不变。）
(v2: 依对抗审查修正)
**标定/评估分离**（DEEP_REVIEW §2-A）：QC 阈值（0.3/0.6、SBB、控制带 k）在**标定集 A**
上锁定，A 用 seed∈[0,9] + 幅度网格的**偶数档**；**评估集 B**（本协议全部报数来源）用
seed∈[1000,1000+N) + **奇数档 + 未在 A 出现的组合**。A 锁定后 B 冻结，绝不回标。

| 场景族 | 配置数 | 说明 |
|---|---|---|
| S0 主 demo | 1 | crystal.yaml 现状：常驻弱 AR(1) 漂移 + 常驻眩光 ε=6% + 第 3 轮强边缘 strength=0.5（"假最优"第一幕，给观众看） |
| S1 零伪影对照 | 1 | 关闭全部注入器（noise_sd=0.02 保留）——QC 税专用 |
| S2 单伪影×幅度网格 | 6×5=30 | 六注入器各 5 档，含**低于检出边界档** |
| S3 留出伪影×幅度 | 5 | 空间相关高斯随机场（下述），签名库无对应假设 |
| S4 组合场景 | 4 | 结构+孤立叠加：{边缘+眩光}、{梯度+漂移}、{批次+灰尘}、{边缘+梯度+批次}（结构性叠加，中位数不可救） |
| **合计** | **41** | 每配置 × 三臂 × 评估集 N=20 种子 |

**S2 幅度网格**（每档为乘性偏差量级，最低档设计为落在检出边界以下）：
- edge_evaporation `strength` ∈ {0.05, 0.10, 0.20, 0.35, 0.50}
- thermal_gradient `magnitude` ∈ {0.05, 0.10, 0.15, 0.25, 0.40}（对齐 §11.3 中心-边缘 2× 上限）
- batch_shift `shift` ∈ {−0.05, −0.10, −0.15, −0.25, −0.40}（信号 SD 10–30% 扫到压力档）
- instrument_drift `sigma`(ar1) ∈ {0.002, 0.004, 0.008, 0.015, 0.030}（%/满量程惯例，扫描）
- glare `boost` ∈ {0.10, 0.20, 0.35, 0.55, 0.80}（prob 固定 0.10；ε-contamination 温和→压力）
- dust_nucleation `drop` ∈ {0.10, 0.20, 0.40, 0.60, 0.80}（prob 固定 0.05）

**S3 留出伪影生成式（不在 INJECTOR_REGISTRY，评估时外挂注入）**：
在 6×8 网格上采样低阶空间相关高斯随机场 b(well)：协方差 K[(r,c),(r',c')]=σ_f²·
exp(−(Δr²+Δc²)/(2ℓ²))，长度尺度 ℓ=2.0 孔、幅度 σ_f∈{0.05,0.10,0.15,0.25,0.40}；
Cholesky 采样 b~N(0,K)，measured *= (1+b)。ℓ=2 使偏差跨多孔平滑相关——**六签名都不直接
匹配**（非板边限定、非线性单轴、非批次分组、非时间序、非孤立点）。种子独立于 S2（下 §4.3）。

---

## 3. 指标（精确计算式与数据来源）

真值 sidecar（`truth/round_*.json`）**仅事后评分**读取，跑内内核禁触（公理 6）。

1. **simple regret** `= f*_true − f(x̂)`，f* = 该场景真值面全局最优（离线网格 + L-BFGS 精化，
   每场景一次），x̂ = 各臂 best-so-far 报告的候选，f(x̂) 用真值面**去伪影**评估。逐轮记 regret_t。
2. **错误最优命中率**：best-so-far 报告的 x̂ 的 measured 值 > 其去伪影真值 + 3σ 的轮次占比
   （即"被伪影抬成假最优"）。数据源：观测 measured 对照 truth。
   **赢者诅咒边界（v4 敏感性补记）**：x̂ 本身取自 `argmax(measured)`，选择偏差使**纯噪声**
   也可能把某孔 measured 抬过 +3σ——零伪影 S1 本底假最优率 ~5% 属抽样噪声而非伪影
   （robust S1 观测到 ~0.15 即此机制，非真值判据失效）。**预注册 3σ 判据不改**（保住跨版本
   可比与冻结承诺），另报 **5σ 敏感性列**佐证真伪：`wrong_optimum_hit_5sigma`（同式换 5σ 阈）
   与汇总 `wrong_optimum_hit_any_5sigma`——5σ 下本底趋 0，仍命中者才是伪影抬升的强证据。
   实现见 `expos.eval.scoring`（逐轮双列 3σ/5σ）。解读 S1 假最优率须并看 5σ 列，勿把
   本底噪声当机制失效。
3. **污染样本利用率**（三臂对比核心）= (响应模型训练集中**真污染**观测数) / (训练集总观测数)。
   **真污染定义（对抗审查 + 试点修正）**：`|bias| > τ_bias`，**bias 为绝对偏差** `bias = y_measured − y_clean`
   （`y_clean` = truth sidecar 的 `true_value`，无伪影无噪声），阈值 `τ_bias = 3·noise_sd`（默认 3×0.02=0.06）——
   与本节指标 2「错误最优命中率」的 3σ 判准同构。**试点实锤的量纲 bug（v3 修正）**：旧版用**相对**偏差
   `y_measured/y_clean − 1` 对比**绝对** `τ=noise_sd`，量纲错配——零伪影场景旧定义污染率虚高 ~0.72；
   改绝对偏差 + 3σ 后纯噪声误报 ≈0.3%。
   **不用裸 `artifact_applied=True`**：`EdgeEvaporation`（d≤1 恒命中）与 `ThermalGradient`（全板恒命中）
   即使幅度趋零也返回 `applied=True`，裸标签会在**低幅档虚高**污染率、污损三臂对比。
   naive≈真污染率；robust-blind 因不隔离≈naive；os 应显著更低。**若 sidecar 未存 y_clean，则退化用
   `artifact_applied` 但仅作跨臂相对量**（同口径可比，绝对值不解读）。数据源：训练集 obs_id ∩ truth。
   实现见 `expos.eval.scoring._bias` / `tau = 3·noise_sd`。
   (v3: 依试点量纲修正)
   **可复算 + 加权口径（R2）**：
   - **成员清单可复算（G-2）**：`contaminated_in_training` 依赖轮内时点信任/路由快照，外部
     无法重推——`score_run` 落 `report/training_members.json`，逐轮 dump 实际入模原始观测
     （obs_id/well_id/round + 逐孔 bias/contaminated/injected 标志），第三方据此独立重算。
   - **加权污染口径（K-P3 / §4 问4，spec）**：**主口径加权** `Σw·1[contaminated]/Σw`，
     w：naive/robust w=1、os 硬隔离 w=0、os-soft w=alpha（软降权值）、rcgp w=1/infl（归一）；
     二值口径（降权>0 即入模）保留为兼容列。rcgp 的 `training_contamination` 定义上**恒等于
     naive**——其稳健性在损失加权而非入模筛选，训练集成员本就同集合（诚实事实，非 bug）。
     加权口径需 **models 侧导出 per-obs influence 权重**（`robust_gp` 的 infl / SoftTrust 的
     alpha 均为跑内局部量，未落盘）——**需 models 侧配合**，评分器不擅自重算模型内部权重。
4. **QC 税**（仅 S1）：SUSPECT/FAILED 率 = 被 os 判非 TRUSTED 的好观测占比；regret 差 =
   regret_os − regret_naive（应 ≥0 且小）；多花复测预算 = os 触发的 REMEASURE 孔数。
   **量纲口径（v4 补，消 R1 歧义）**：两条验收线均取**绝对值 ≤0.05**——
   (i) SUSPECT/FAILED 率本就是无量纲比例，≤0.05 即 ≤5 个百分点；
   (ii) **regret 差取原单位之差**（signed，非取绝对值）`regret_os − regret_naive ≤ 0.05`
   （regret 原单位，**非**相对百分比 `(regret_os−regret_naive)/regret_naive`；os 更优即差为负，
   自然 ≤0.05 通过）。绝对读法与相对读法不可混：同一组数绝对读法 1.2% 过、相对读法 +152% 惨败，
   歧义即 R1 命中处。判据落布尔见 `runs/full_sweep/_tools/aggregate.py`
   QC 税块 `h3_*_pass` / `h3_pass`（与本口径逐位一致）。
   验收线 ≤0.05（DEEP_REVIEW §2-C / M5 验收）。**轮数统一 8 轮**（与 §2 所有场景及 M5 §5.1
   一致——对抗审查：M5 原写 4 轮，现已改为 8，两文档同口径）。
   (v2: 依对抗审查修正；v4: 补量纲口径 + 布尔判定)
5. **检出率-幅度曲线**：横轴幅度档，纵轴 = os 在该配置 N 种子中"至少一轮对该伪影发 SUSPECT/
   FAILED 警报"的比例（跨轮累积，§11.4 小样本姿势）；标出**失效点**（检出率跌破 50%）与
   demo 档位置。留出伪影 S3 同图叠一条虚线（未见腐蚀的兜底能力）。
   **检出口径三修（R2）**：
   (a) **专属检查口径**（L-1 前置，已实现）：检出＝注入器**对应**的 QC 检查（`INJ_TO_CHECK`）
   在任一轮 `check_counts>0`，剔除旁路误报（旧口径任意 SUSPECT/FAILED 会虚高）；旧口径
   `*_detection_rate_any` 保留为对比列。
   (b) **量纲归一（L-1）**：注入是**乘性**、edge/thermal 检出阈是**绝对量**，故名义 amplitude
   在不同板值水平上检出率漂 **3–8×**（L 路实测 edge 0.05 档：低值板 0/30、高值板 15/30），
   臂间板值轨迹不同还会把臂差混进检出率。检出曲线**横轴改（或并列）"实现绝对效应/板噪声尺度"**
   = 被物理影响孔上 `|measured − true − noise| / noise_sd`（有效 SNR，all-affected 口径）——
   落 `detection_curve.csv` 的 `eff_over_noise_mean/median` 列 + `detection_curve_effnoise.png`。
   (c) **round-0 纳入（G-P1）**：truth/round_0 自首轮即含注入伪影，首轮 QC 检出为真检出；
   旧口径静默排除 round 0 无协议依据，新口径 `*_detection_rate` **纳入 round 0**。
   (d) **glare 独立证据通道（L-3）**：glare 读模拟器种下的 16σ 分离曝光标记，检出率
   =1−(1−p)^43 的二项恒等式，**不含统计推断**——`detection_curve.csv` 标 `binary_evidence_channel=True`，
   图注明"独立证据通道，不参与臂间能力比较"（S3 留出伪影虚线同理与推断型检出分列）。
6. **AF / EF**（§11.3，中位 AF≈6 参照）：Acceleration Factor = naive 达到目标质量阈所需轮数 /
   os 所需轮数；Enhancement Factor = os 在固定预算末轮 best 值 / naive 末轮 best 值。
7. **几何指标**（辅助，防几何误导）：Precision/Recall/AvgDistance（arXiv 2401.01981），
   在报告的 top-k 候选集上算。

统计：N≥20 种子，报效应量 + bootstrap 95%CI + 跨臂**置换检验**（§11.4，小样本体制不做单轮
显著性）。

**多重比较与小样本分布口径（R2 补，P-4/P-6）**：
- **族错误率（P-6）**：单板并行跑 ~12 个 QC 检查、**无多重比较校正**——全干净板的
  family-wise 误报率实测约 **7.5%**（远高于任一单检查的 ~0.3%）。本协议**声明**采用逐检查
  未校正阈值（signature 检出优先，宁可单检查偏灵敏），FWER 作为已知口径披露；逐孔 QC 税
  （SUSPECT/FAILED 率 ≈1.09%）达标不受此影响（它是逐孔而非逐检查族）。检出率/归因数字读法
  须并看此 FWER 基线，勿把干净板的族误报当能力。
- **批次 WLS 统计量分布（P-4）**：批次位移检查的 `shift_hat/se` 在小 `n_pairs` 下**实为 t 分布**
  而非正态——"z>3→0.3%" 的正态尾在 `n_pairs=4` 时实测 FPR≈**5.8%**（偏乐观 ~14×），小板
  FPR 目前靠幅度地板硬扛。**声明**该近似偏乐观；根治（改 t 分位 `t.sf(z, df=n_pairs−1)` 或
  按 df 抬阈）落在 `expos/qc/checks.py`（QC 包，非本评测叶子层，另批处理）。

---

## 4. 轨迹与产物格式

### 4.1 逐轮 JSONL schema（`report/trajectory.jsonl`，每轮一行，追加式）
对齐 Olympus `Campaign.to_dict` 字段子集（§13.6 建议）：
```
{
  "arm": "naive|robust-blind|os",
  "scenario_id": "S2.edge_evaporation.0.20",   // 场景族.注入器.幅度档
  "round_id": 3,
  "seed_triplet": {"np": 1000, "artifact": 5123, "layout": 8841},
  "artifact_kind": ["edge_evaporation"],          // 本轮生效注入器名列表（[] = 零伪影）
  "artifact_truth_params": {"edge_evaporation": {"strength": 0.20, "decay_wells": 1.0}},
  "proposed": [{"cand_id": "...", "x": {...}}],    // 本轮设计候选
  "observed": [{"obs_id": "...", "cand_id": "...", "y_measured": 0.83,
                "y_agg": 0.79, "trust": "SUSPECT", "routing": "QUARANTINE",
                "alpha": 0.03, "feasible": true}],
  "best_so_far": {"cand_id": "...", "y_reported": 0.79, "round_id": 2},
  "regret_true": 0.14,                             // 事后评分回填（第二遍）
  "qc_alerts": [{"check": "edge", "suspicion": 0.71}],
  "actions": [{"kind": "REMEASURE", "target_obs": "..."}]   // os 臂失败感知动作
}
```
`feasible` 标签 = 采集是否判该点可行（失败感知规划用）；`artifact_truth_params` 从 truth
sidecar 事后回填，**跑内 loop 不写此字段**（防泄漏）。

### 4.2 report/ 目录布局
```
report/<arm>/<scenario_id>/<seed_np>/
  trajectory.jsonl        # §4.1
  summary.json            # 复用 loop._summarize + 追加 arm/scenario_id
  events.jsonl            # 内核事件全量（软链或复制自 run 根）
report/_aggregate/
  metrics_long.parquet    # 全 run × 全指标长表（评分器产出）
  detection_curves.json   # §3.5
  figures/                # §6
```

### 4.3 种子三元组
每 run 记 `(np, artifact, layout)`。派生规则复用 `loop.derive_seed`：
`np=base_seed`；`artifact=derive_seed(base_seed,"artifact",scenario_id)`；
`layout=derive_seed(base_seed,"layout",round_id)`（已在 build_experiment）。
**标定/评估参数分离**：A 用 base∈[0,9]、B 用 base∈[1000,1019]；artifact 种子经 scenario_id
派生，保证同一 base 在不同场景独立、跨臂**同 base 同伪影实现**（配对可比）。

---

## 4.9 实跑矩阵备案（v5 增补，R2 H-4/L-9——判据原文不动，只备案预注册矩阵与实跑的漂移）

预注册矩阵（§5 的 41 配置×3 臂×20 种子=2460 run）与历次实跑存在漂移，逐一备案：

| 实跑批次 | 实际矩阵 | 与预注册的偏离 | 出处 |
|---|---|---|---|
| full_sweep（M9 关账） | 19 场景 × 5 臂 × A10+B20 种子 = 1450 格 | 场景 41→19（drift/dust/S3/S4 缺）；臂 3→5（多 os-soft/rcgp）；replicates=2 非协议 n=3 | runs/full_sweep/ |
| r1_resweep（R1 修复后） | 48 场景 id × 4 臂 = 2700 格（含 S2 补 drift/dust、S3.wide_edge、S4×4、S2r3 n=3 对照） | robust 仅 S2r3 变体有格（200）；rcgp 0 格（容量税待 os-lite 对照）；全 B 种子 | runs/r1_resweep/ + campaign_manifest.json |
| ablation（备料未跑） | 5 消融臂 1240 格 | 新臂不在预注册 | runs/ablation/ |
| resident_sweep（备料未跑，待 RES3 参数意见） | resident 漂移 4 档 240 格 | 注入器语义修订（R2 §1.1） | runs/resident_sweep/ |

纪律：判据条文（§3/§7）不随实跑修订；矩阵漂移一律在本节备案而非改写预注册文本；
根治方案是 protocol.yaml 可执行化（ARCH_V2 §4），届时本节转为其 CampaignManifest 的人读索引。
另备案：batch 相关档位数字在 R3 §1.1（批次方向判反 P0）修复并重跑前**带病**，暂缓引用。
另备案（BA3 边界，Backlog 在案）：批次双锚检查的**主锚**（哨兵 vs `expected_band` 几何中心）
适用边界＝**降低型**批次位移；**升高型**注入或换域（干净真值贴 band 高沿）前须先改主锚 target 为
哨兵池稳健中位数、或收紧 `target_unreliable` 阈——否则干净哨兵真值系统性偏离 band 中心（~0.10）会
误指干净批（现安全网有效：与回退锚冲突→record-only、false-accuse 恒 0；生产不可达：全库 batch_shift
均降低型）。判据条文不动。
另注（种子语义勘误）：上文 §4.3"同 base 同伪影实现"的强声称经 R2 ③ 复核降级为
"共同随机数近似"（artifact 派生键是孤儿，执行真源是 exec 流；见 run_cell._seed_triplet docstring）。

## 5. 计算计划

**本地**（开发/冒烟）：S0 主 demo、S1 零伪影、各族 1 配置 × 3 臂 × 3 种子的冒烟；评分器与
出图管线全程本地。**Slurm**（集群 `/opt/slurm/bin`）：全评估集网格。

**作业矩阵规模**：41 配置 × 3 臂 × 20 种子 = **2460 run**。单 run = 一次 8 轮 campaign
（48 孔/轮、GP 训练点 ≤~200），CPU 单核约 1–3 min。总核时 ≈ 2460×2 min ≈ **82 核时**，
纯 CPU、无 GPU、embarrassingly parallel。种子提到 30 则 3690 run（≈123 核时）。

**sbatch 数组草案**（一行一 task，索引映射到 (arm,scenario,seed) 三元组清单）：
```bash
#!/bin/bash
#SBATCH --job-name=m9grid
#SBATCH --array=0-2459%200        # 2460 tasks，最多 200 并发
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:15:00
#SBATCH --output=report/_slurm/%A_%a.out
export PATH=/opt/slurm/bin:$PATH
LINE=$(sed -n "$((SLURM_ARRAY_TASK_ID+1))p" report/_grid_manifest.tsv)  # arm\tscenario\tseed
read ARM SCEN SEED <<< "$LINE"
python -m expos.eval.run_cell --arm "$ARM" --scenario "$SCEN" --seed "$SEED" \
    --domain domains/crystal.yaml --out "report/$ARM/$SCEN/$SEED"
```
清单 `_grid_manifest.tsv` 由评分器预生成（三臂 × 场景 × 种子笛卡尔积）。评分（regret 回填、
检出曲线）为第二个短数组作业或单机聚合。**Slurm 多节点写同一 run 目录禁止**——每 task 独立
子目录（§13.1 坑）。

**`expos.eval.run_cell` 是新增模块（对抗审查显式标注）**：M4 尚无此入口，属 M9 交付物——
单 (arm, scenario, seed) 三元组跑一次 8 轮 campaign 并落 §4 产物；它按 `--arm` 装配 M5 §3
`VerdictPolicy` 与 §3B `AggregationPolicy`（零 `if arm==` 分支），复用 `loop.run_loop`/`_summarize`。
`expos.eval` 包（含评分器、检出曲线、出图）一并为 M9 新增，不改 M4 既有模块。
(v2: 依对抗审查修正)

---

## 6. 可视化（figures/）

1. **主图**：三臂 best-so-far ±std band（CLSLab 配方 §12.6）——横轴轮次，三条
   `np.minimum.accumulate` best-so-far（maximize 用 maximum.accumulate）跨种子均值线 +
   ±1std 带；主 demo 场景第 3 轮标注"假最优"事件。os 收敛线应在结构性场景显著高于另两臂。
2. **检出率-幅度曲线**（§3.5）：每注入器一条实线 + 留出伪影虚线，标失效点与 demo 档；
   叠 robust-blind 的"regret 未恶化幅度上限"作对照参考线。
3. **失败样品分流时间线**（A-Lab 式，§12.7）：横轴轮次/孔，每次 os 改判（TRUSTED→SUSPECT/
   FAILED、REMEASURE、去偏复归）显式画为时间轴事件点，颜色编码动作类型。

---

## 7. 预注册声明

**假设 H1**：结构性场景（S2 edge/gradient/batch 中高档、S4 结构叠加）os 的 regret 与污染
利用率**显著优于** robust-blind（置换检验 p<0.05，跨 N 种子）。
**H2**：孤立/对称场景（S2 glare/dust、低档漂移）robust-blind **追平** os（差异 CI 含 0）
——**诚实预期 robust-blind 在此追平，如实报告**，这正是 nuance 结论（DEEP_REVIEW §2-B）。
**H3**（QC 税）：S1 下 os 的 SUSPECT/FAILED 率 ≤0.05（无量纲比例）**且** regret 原单位之差
（signed）`regret_os − regret_naive` ≤0.05（regret 原单位，**非**相对百分比；量纲口径见 §3 指标 4）。
两条皆过方判 H3 成立；aggregate 落 `h3_pass` 布尔（M5 验收线）。
**H4**（留出伪影）：S3 上 os 检出率随 σ_f 上升但低于对应幅度的匹配签名场景——**报兜住多少、
错判成什么**，不粉饰未见腐蚀的漏检。

**成功判据**：H1 成立 **且** H3 成立。H2 追平**不算失败**（它证明主张的边界）。

> **[deviation 指针 — 判据文本冻结不动，仅加指针]** H1 的"regret **显著优于** robust-blind"
> 支已在评估集 B 实测被**否决**（H1_REJECTED：多数结构档 os regret 反劣于 robust）——本项目
> **未**达成本节原始成功判据的 regret 支；主张已重定位为**结论可信性**（§0 可信性限定版：假最优
> 拒斥 / 污染防护 / 可审计归因，这三项 provenance-blind 鲁棒统计不能复现）。更正与账目见
> CHECKPOINTS.md 压测更正记录、STRESS_TEST_R1_RESPONSE §3、§4.9 矩阵备案。**只读本节判据者请
> 务必并读本指针**，勿据冻结文本误判项目已达成自己的成功判据。
**诚实报告承诺**：(a) 检出率-幅度曲线含失效点与低于检出边界的档，不裁剪；(b) robust-blind
追平的场景全部列出；(c) 小样本体制下单轮小幅伪影抓不到、靠跨轮累积——如实写入结果叙述
（§11.4 / DEEP_REVIEW §3.3）；(d) A/B 分离一旦锁定，B 上判据不回改。

---

## 附录 A：第七轮前沿复查后的引用补充（v3，2026-07-10）

1. **对照基准引用**：MADE（arXiv 2601.20996）作为"闭环发现基准存在、但无结构化偏差注入"的直接对照——强化本协议方法学空白主张（REFERENCE_MAP §17）。
2. **robust-blind 臂增强候选**：除副本中位数+温莎化外，敏感性分析可加两个文献级 robust baseline 作扩展臂——multi-stage BO（arXiv 2512.15483，过程噪声鲁棒）与 RCGP-UCB（arXiv 2511.15315，无界腐败鲁棒）——用于回应"robust BO 已解决伪影"的潜在质疑（两者均不处理结构化空间偏差，预期在 edge/gradient/batch 场景仍败于 os 臂）。
3. **应用背书引用**：Artificial Coater（chemRxiv 2026-03，失败涂层识别）与 Self-Supervised Instrument Calibration（arXiv 2606.29466，仪器漂移物理来源）。

## 附录 B：归因精度-幅度曲线（v4 新增指标，2026-07-10）

M6 联合端到端的饱和态发现：strength=0.5 污染 77% 板面时"干净多数"假设崩溃，归因在竞争假设间歧义化（检测率恒 1.0 但 top_cause 精度下降）——**检测饱和易、归因有最优幅度窗口**。据此新增指标：

- **attribution precision@amplitude**：单伪影场景各幅度档上，被隔离观测的 top_cause 命中率（以场景活跃注入器为真值；inconclusive 不计对错、单列比率）；
- 预期形态：低幅（检测边界附近）样本少而准、中幅最优、高幅（>50% 板面污染）歧义化——与检出率曲线并排呈现即"信任路由的两阶段能力边界"；
- 附带记录批次-布局混叠的修复背景（棋盘格批次，sim_base）：几何混叠是归因误差的系统性来源之一，混叠审计应作为域配置检查项（backlog：domain lint 检查批次分配与 is_edge/capture 序的相关性）。

**truth 标签口径 = all-affected + 归因分层报（§4 问3 裁定，R2）**：
- **truth 主口径 = all-affected**：truth sidecar 记**物理事实**——注入器实际改变了哪些孔的
  测量值（`|artifact_effect|>0`），不迁就归因器。batch_shift 是**批次级**效应（命中
  `batch_suffix` 的整批孔，含哨兵/控制孔），truth.artifacts 应标该批**全部**被影响孔而非
  designated 靶孔。核验现状：`BatchShift.apply`（`expos/adapters/artifacts.py`）对
  `solution_batch.endswith(batch_suffix)` 的每孔返回 `applied=True` 并标签——**batch_shift
  的 all-affected 标注已成立**（B 集 os run round-0 实测 23/47 孔标签＝整个 B1 批，含控制孔）。
  **需 adapters 侧配合的 spec（不在本次改动）**：(i) 任何"仅标 designated 靶孔"的注入器须改为
  标全部 `applied=True` 孔；(ii) InstrumentDrift 的 `applied=|drift|>1e-9` 使全 43 孔每轮
  全亮 drift 标签（L-2/§1.1），须改 `|drift|>k·sigma` 后 all-affected 才对 drift 成立——
  该项属 adapters 包（另一 agent 在用），本协议仅登记 spec，不改码。
- **归因质量分层**（避免 all-affected 使归因门形同虚设）：
  - **检出层**：按 all-affected 逐孔配对（`detected_true` 专属检查 / 逐孔 truth 命中）。
  - **归因质量层 = cause 级配对（主口径）**：`top_cause` 判对该板**主导伪影**即计对，不苛求
    逐孔——每种子 ≥1 注入孔命中 expected cause 即算识出（`attr_cause_hit_rate`，落
    `attribution_curve.csv`）；**种子级 percentile bootstrap 95%CI**（G-P3：事件池化+种子内
    聚簇会低估 CI，改按种子重采样，`attr_cause_hit_ci_low/high`）。
  - **弃权率如实报（P-11）**：注入孔上 `top_cause=None` 的 `attr_abstention_rate_injected`
    进 limitation（实测显著高于事件级 `inconclusive_rate`；batch 真因 cause 级命中弱是
    棋盘格×去身份残差的结构性可辨识性问题，checks.py 已留档，口径怎么选都洗不白也冤枉不了）。
  - 归因引擎命名空间：注入器名 → 原因词（`INJ_TO_CAUSE`：batch_shift→batch_effect、
    dust_nucleation→dust_contamination）——直接字符串比对会把正确归因记成 wrong（精度恒 0），
    cause 级配对须经此映射。
