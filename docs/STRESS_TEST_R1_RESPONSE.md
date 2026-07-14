# 蓝队对红队 R1 报告的逐条回应（R1-RESPONSE）

> 2026-07-11。回应对象：`docs/STRESS_TEST_R1.md`（六路并行代码审查 · M0–M10 收官态）。
> 修复方（蓝队）逐条独立复核 + 动手修 + 全量回归 + 上集群重扫。
> 阅读约定：凡结论依赖**重跑后新数字**的，一律标 **【待 resweep】** 占位，不在本文兜底断言方向。

---

## 1. 总回应

**红队六大主轴（R1-1…R1-6）全部被蓝队独立验证属实，无一条被驳回。** 我们没有为任何一条找借口：H1 的 regret 半边在其自己的预注册场景集上确实方向反了（R1-1）；M7 的三个"风险感知"机制在生产接线里确实是空转（R1-2）；评测协议四刀确实清一色有利 os 叙事（R1-3）；六注入器基准确实只落地了四个、H2/H4 无数据可裁却宣称三主张全成立（R1-4）；崩溃一致性三处确实实证可复现（R1-5）；旗舰数字 1.007 确实在现行模拟器下失效、batch−0.18 确实 os 污染反超 naive（R1-6）。

这是一次高质量的对抗审查。它最大的价值不在"找到 N 个 bug"，而在戳穿了三处**互相掩护**的结构：机制空转 → 没跑对应场景 → 空转永不暴露 → regret 反输又被解释成"os 就是这样"。红队"不要先急着改门面数字"的排序建议是对的，我们**严格按机制→口径→数字→门面的顺序**修复，并把这条顺序写成了 ROADMAP_V2 的红线（"逆序即作弊"）。致谢红队：这一轮把项目从"测试全绿的自我安慰"推到了"机制真的通电"的状态。

**执行结果**：按建议顺序修复后，全量回归 **479 passed / 0 failed**（sbatch job **4667159**，`runs/r1_final_regress_4667159.log`，EXIT=0）。2700 格 resweep **已完工**（`runs/r1_resweep/`：机制修复后 os/os-soft 全部重跑 + robust replicates=3 有效对照 + 补 S3/S4/drift/dust 场景；g209+g208 双节点各 130 并发直跑，2700/2700 格零失败）。所有涉及重跑数字的判定（尤其 R1-1 的 H1 终判、R1-3 各臂对比）**已按预注册纪律定稿**（`runs/r1_resweep/report/`，聚合器 `runs/r1_resweep/_tools/aggregate_resweep.py`）：H1 终判 **`H1_REJECTED_os_worse`**（os 显著劣于 r3-robust），机制修复前后 os regret 无统计变化——详见 §3。

---

## 2. 逐条处置表

状态图例：**已修绿** = 代码已改 + 判别性测试通过 + 在 479 全绿内；**待 resweep** = 修复已落地但终判依赖重跑数字；**定性不修** = 经复核判为设计选择或本底噪声，带实测佐证记录在案。

### P0 / P1 主轴

| # | 指控（红队） | 蓝队验证 | 修复位置 / 判别性测试 | 状态 |
|---|---|---|---|---|
| **R1-1** | H1 预注册判定被 S0.demo 顶替；S2 中高档 13/13 regret 反向；S4 未跑；无 p 值 | 属实，逐条核对 main_table | 见 §3 单独一节。resweep 终判 **`H1_REJECTED_os_worse`**：S2r3 中高档 os vs robust(r3) 池化 mean_diff **+0.0161 / p=0.0001**（os 显著劣于），逐档 edge0.35 **p=0.0001**、batch−0.1 **p=0.0009** | **resweep 定稿**（deviation） |
| **R1-2a** | 风险折扣读错键恒零（`.get("global_rate",0.0)`）→ failure_aware 臂≡普通 UCB | 属实 | `planner/policy.py:185-197` 改契约键 `summary()["p_global"]`，缺键**响亮失败**不兜底；测试 `test_risk_discount_generator_scores_differ_from_pure_ucb`（test_planner_arbiter.py:394）断言评分≠无折扣臂 | **已修绿** |
| **R1-2b** | 风险图传 `solution_batch=None` → 桶键永不相等 → 恒常数 0、布局惰性 | 属实 | `planner/policy.py:158-185` `_plate_risk_map` 逐孔传真实 `batch`；`failure_model.py:123` 补 `_agg_batch_marginal` 边际回退；测试 `test_risk_map_edge_higher_than_center_when_edge_artifacts` / `test_risk_map_batch_hint_uses_specific_bucket` | **已修绿** |
| **R1-2c** | `ewma`/`cusum` 全仓零调用方，drift 检查 score 恒 0，从未接线 | 属实 | `qc/checks.py:514` 接线 `cusum(z,k,h)` 跨轮累积；测试 `test_temporal_drift_cross_round_detects_aging_instrument` / `test_temporal_drift_clean_multi_round_fpr_under_5pct` | **已修绿** |
| **R1-2 连锁** | （红队未预测）风险机制激活后暴露 LayoutPlanner 区组饿死：连续风险值使 47/48 满载跨区组无解 | 蓝队修 R1-2b 时自曝 | `planner/policy.py:155,179-180` 风险值粗分层（`_RISK_TIER=0.25`，保序、防均衡饿死）；3000 随机风险图零饿死。见 §4 | **已修绿** |
| **R1-3a** | 聚合器不遵守 A/B 分离，主表/曲线混用标定集 A | 属实 | `aggregate.py` 按 `seed_set` 过滤，主表/曲线/判据只用 B（seed∈[1000,1020)）；resweep 全评估集 B、无回标（2700 格全 B，`runs/r1_resweep/report/main_table.csv`） | **resweep 定稿** |
| **R1-3b** | 污染分母用 raw 观测非各臂有效训练集 → robust≈naive 被口径保证 | 属实 | `eval/scoring.py:250` 双列口径：文献标准 Huber ε-污染（按注入标签）+ RCGP 家族按注入比例，raw/有效双报（新口径 `training_contamination` 已落主表：os 高幅度下污染显著低于 naive，如 edge0.35 os 0.101 vs naive 0.547——隔离确起作用，代价是 injected 数据同步流失） | **resweep 定稿** |
| **R1-3c** | robust-blind 弱于凍结规格（全 replicates:2、无 Huber、无 alpha） | 属实 | resweep ② 线独立 family `S1r3./S2r3.`，**replicates=3** + Huber IRLS，给 robust 中位数真保护——此 robust 成有效对照后，os 在其上**全面显著劣于**（见 §3 终判），证明旧对照之弱确实此前替 os 遮丑 | **resweep 定稿** |
| **R1-3d** | 预注册统计分析零实现（无 bootstrap/置换/多重比较） | 属实 | `aggregate.py` 补配对置换检验（同 seed 配对）+ CI；全场景 p 值表已出（`runs/r1_resweep/report/stats_tests.csv`，os vs robust/naive/os-soft 逐场景） | **resweep 定稿** |
| **R1-4** | 六注入器只落 4 个；drift/dust 无单伪影、S3 留出/S4 组合全缺；H2/H4 无数据却宣称三主张成立 | 属实 | resweep ① 线补 drift×5 + dust×5 + S3.wide_edge×5（留出伪影）+ S4×4，全部落地评估集 B；dust 检出全档 1.0、drift 检出全档 0.20-flat（结构盲区诚实档，§见 report §5）；S4 组合上 os regret 4–5× 劣于 naive（H4 信号） | **resweep 定稿** |
| **R1-5a** | torn-tail 与 seq 恢复互斥，正常崩溃后 run 变不可读 | 审计确认，torn-tail 谓词已统一（能解析补 `\n` 不截） | `store.py` heal 判据与 `read_events` 统一 | **已修绿** |
| **R1-5b** | checkpoint 落后重做非幂等，双计训练集 | 审计确认，redo 已按 round_id 对账 + 落事件 | 重做前对账清账 + 幂等键覆盖 + 事件 | **已修绿** |
| **R1-5c** | resume 不等价、snapshot 对超参盲 | 审计确认，snapshot 已纳超参指纹 | `response_gp.py` snapshot 纳入核超参；resume 等价性 naive/os **逐轮逐位实测 EQUIVALENT** | **已修绿** |
| **R1-6a** | best=1.007 现行代码失效（实测 0.9751），make_demo 无条件打印越限 | 属实 | 全文改分布式表述 + 个案降格；`make_demo.py` 叙事条件化 + 防漂移测试钉死 | **已修绿** |
| **R1-6b** | batch−0.18 os 污染反超 naive；README"结构上不可能污染"过度 | 属实 | 门面改"判 SUSPECT/FAILED 不入模"；诚实 finding 收录反超及机理。resweep 复现反超：S2r3.batch−0.18 os `training_contamination`**=0.559 vs naive 0.364**（os 反而更脏——batch 全局效应下隔离触发但未把注入孔剔出模，injected 仍 0.977），regret os 0.0100 vs robust 0.0075（p=0.36 ns） | **resweep 定稿** |
| **R1-6c** | README 单格代表 1450 格掃描 | 属实 | README 每 headline 加场景范围限定 + 掃描级总括 | **已修绿** |

### P2 清单（15 条，全收）

| 条 | 处置 | 状态 |
|---|---|---|
| well_cost 无下界（arbiter 负 n_wells 规避预算） | `max(1,·)`+拒负值；测试 `test_well_cost_variants` | 已修绿 |
| CLI override 死投遞（无消费者、applied 恒 0） | 接线消费端，end-to-end 烟测：**投遞→消费→归档**全链路 | 已修绿 |
| QC 结构检查 try/except 吞错（可诱导假阴） | 降级升格显式 `QCCheck(passed=False)`+flag | 已修绿 |
| regret 的 f\* 每格不同（破坏跨臂配对，偏 os） | f\* 按场景一次性离线算，**恒同值**；`scoring.py:191` | 已修绿 |
| 检出率把任意 QC 警报当检出 | 改按**注入器专属检查名**判定；新旧双列——实测检出率被旧口径**低估**非虚高。resweep 新口径：edge/glare/dust 全档≈1.0，batch−0.07=0.20、thermal 中高档 0.30–0.40（drift 0.20-flat=盲区，另档） | 已修绿（resweep 定稿） |
| 归因精度不与 truth 逐孔配对 | 逐孔配对；暴露 batch−0.18 旧 0.997 掩盖 **false_attribution_rate=0.74**（部分属 truth 标签口径、glare/edge 0.13-0.18 是真溢出——见 §4/§5） | 已修绿（诚实披露） |
| resume 半成品轮孤儿观测 × 覆写 truth 错配 | 与 R1-5b 同源，obs 对账修复 | 已修绿 |
| compare.py QC 税死代码（恒 0 永不生成） | 改按场景配置判定零伪影，指标修活 | 已修绿 |
| wrong_optimum 3σ 阈在赢者诅咒边界 | 改 5σ + 报 S1 本底行（敏感列） | 已修绿 |
| H3「regret 差 ≤5%」量纲未定义 | 量纲入协议 + 落布尔判定 | 已修绿 |
| 归一化伪后验掩盖绝对证据（confidence=1.0） | FLOOR 作用于未归一 raw score | 已修绿 |
| glare/dust 反驳器同义反复 | 加方向一致性门 | 已修绿 |
| _board_frame 硬编码模拟器约定 | 逐孔读 obs 的 solution_batch/capture_index | 已修绿 |
| round_band×批次键含轮次前缀（偶数轮失忆） | 边际回退加去 round_band 维层级 | 已修绿 |
| reclassify 绕状态机 / 域配置漂移静默放行 / UI 秒级 mtime / 单写者无锁 / override 越界 / torn-tail 物理谓词 | reclassify 转移合法性表 + 要求已有 qc；resume **域配置全量哈希指纹 + 逃生门**；`st_mtime_ns`；writer.lock；obs_id 正则；torn-tail 加"不以 \n 结尾"物理条件 | 已修绿 |

### P3 清单（14 条）

- **修复**：QCPolicy 归因写入违反自家 WAL（**翻转修复**）；REPRODUCE 两处字面不可复现（默认臂/cells 文件已修正）；归因精度 headline 掩盖 26–46% inconclusive（**补披露 inconclusive 26%**）；rawdata tar 标称/实际尺寸订正。
- **定性不修（带实测佐证）**：两条经复核判为本底噪声/设计选择——例如零伪影本底假阳性经 5σ + FPR 实测 **FPR=2.4%**（≈名义 α）佐证不构成系统偏差，原样保留并标注。
- 其余（subsample 弱门、ΔR² Bonferroni、签名权重声明、正态近似小 α、edge 方向硬编码等）逐条评级，见转交表。

> **P3 转交表**：未在本轮闭环的定性项 + 投稿前必补项，已单列转交下一轮执行方。

---

## 3. R1-1 单独一节：H1 主假设判定

**承认预注册 H1 按字面不成立，且现在有 p 值可查。** 预注册 H1（`M9_PROTOCOL.md:221-222`）=「结构性场景（S2 edge/gradient/batch 中高档、S4 结构叠加）os 的 regret 与污染利用率**显著优于** robust-blind（置换检验 p<0.05）」。实测 main_table 上 S2 edge/batch/thermal **13 档 regret 全部劣于 robust**；S4 当时未跑；全 report 无 p 值。CHECKPOINTS「H1 过」引用的全是 **S0.demo**（不在预注册集内）。这正是预注册制度要防的事，红队命中要害。

**重解读（诚实负结果）**：os 的价值经数据支持的是**污染防护 + 假最优拒斥**（在签名匹配场景，如 S0.demo p=0.0014 显著优于），而在结构场景 regret 半边**显著反输**（edge0.2 p=0.0029、batch−0.18 p=0.0027、thermal p<0.02）。这与红队 R1-3 的机制解释一致：机制修复前 failure_aware 臂≡普通 UCB，os 的规划增量根本没通电，regret 反输有了机制解释。

**预注册纪律**：机制修复后 resweep 正在重判 H1。**无论 resweep 结果如何**——即便修好机制后 os regret 追平或反超——因为判据是跑后修订/重跑的，我们都按预注册纪律记为 **deviation**，在 CHECKPOINTS/PAPER_OUTLINE 显式标「判据跑后重跑，记 deviation」，不追溯改写"H1 一直成立"。当前文档中每处「H1」已改写为**场景集限定 + 统计状态**，S0.demo 与预注册 S2 集严格区分。

**终判（resweep 定稿，评估集 B，2700/2700 格零失败，`runs/r1_resweep/report/`）：`H1_REJECTED_os_worse`——H1 按字面不成立，且方向与预注册相反：os 不是"显著优于"而是"显著劣于" robust。** 有效对照用 S2r3 的 replicates=3 + Huber robust 臂（R1-3c 修复后的真保护对照）：S2r3 中高档池化（n=100 格）**mean_diff(os−robust)=+0.0161，置换 p=0.0001，95%CI[+0.0108,+0.0216]，os 更优仅占 22%**；逐档 edge0.35 **+0.0570 (p=0.0001)**、edge0.2 **+0.0080 (p=0.022)**、batch−0.1 **+0.0086 (p=0.0009)** 均显著劣于，仅 edge0.15/batch−0.18 不显著。S4 半边因 resweep 未跑 robust 对照**不可裁**，但 os vs naive 上 S4 亦大幅劣于（edge_glare +0.0342 p=0.001、edge_gradient_batch +0.0448 p=0.0001）。

**机制通电是否有用（修复前后 os 配对，new=resweep vs old=full_sweep，同 scenario 同 seed 同 f\*）：无用。** 结构 S2 池化 n=160 **mean_diff(new−old)=−0.0001，p=0.92**；中高档 n=60 **−0.0015，p=0.51**，修复后更优的格子仅占 16–32%。即 R1-2 修好的是**风险感知规划**（acquisition 折扣），它对 regret 无统计可见影响；而 os 的 regret 代价来自**另一条一直激活的 QC 硬隔离**通路——高幅度下 os 把注入但可用的孔大量隔离（如 edge0.35 训练集 injected 占比 0.833→0.396、n_suspect 0.71），GP 数据饥饿 → regret 爆掉；robust 的 Huber 软降权保留全部数据故全面胜出。这是本轮最实的负结果：**机制真的通电了，但通电的那条不救 regret，救不了的那条（硬隔离过度保守）才是 regret 反输的根因**。按预注册纪律记 **deviation**（判据跑后重跑）。

---

## 4. 意外收获（红队没预测到的二阶价值）

对抗审查的二阶收益——修红队指出的机制时，暴露了红队没预测、旧指标反而"帮忙掩盖"的三件事：

1. **检出率是被旧口径低估，而非虚高**。红队担心"任意 QC 警报当检出"会**虚增**检出率。改成注入器专属检查名逐孔配对后，方向相反：真检出率被旧的宽口径**低估**了——batch−0.07 从 0.05→**0.20**，thermal0.5 从 0.15→**0.40**。旧口径既混入假阳性、又漏算了专属检查的真命中，净效应是低估。这是新旧双列的直接产物。

2. **归因假阳性率被旗舰指标掩盖**。归因精度 headline **0.997** 看似无懈可击，逐孔配对后暴露 batch−0.18 的 **false_attribution_rate=0.74**。诚实拆解：一部分属 **truth 标签口径**问题（batch 全局效应该标 designated 还是 all-affected，见 §5 反问）；但 glare/edge **0.13–0.18** 是**真实的归因溢出**，不是口径能洗白的。这条我们照实写进 finding，不藏。

3. **机制激活才暴露布局饿死**。修 R1-2b（风险图通电）后，连续风险值立刻把 LayoutPlanner 逼到 47/48 满载跨区组无解——一个"机制空转时永远绿、一激活就炸"的下游 bug。修法是风险值粗分层（balance_first 保序），3000 随机风险图零饿死。

**教训（已写入 ARCHITECTURE_V2）**：`docs/ARCHITECTURE_V2_PROPOSAL.md` 立"**无静默空转**"不变量（机制活性注册表 + 激活断言），`docs/ROADMAP_V2.md` 立"机制→口径→数字→门面，逆序即作弊"红线。"机制空转时全绿、激活后才暴露下游 bug"是本轮最贵的一课。

---

## 5. 给红队的五个反问

1. **【ARCH_V2 机制活性断言】** ARCHITECTURE_V2 提议给每个"卖点机制"加一条**激活不变量**（形如"若 failure_aware 臂启用，则至少一格候选评分 ≠ 无折扣臂，否则响亮失败"）。这能挡住 R1-2 这类"测试断言名字、不断言效果"的空转。红队认为这类不变量应下沉到**内核层强制**（每轮运行时校验），还是留在**测试层**即可？运行时校验的性能/误报成本你们怎么看？

2. **【S3 留出伪影设计】** resweep 的 S3.wide_edge 用 `decay_wells=3.0 + max_range_wells=3`——跨 4 圈平滑衰减、覆盖大半板，空间结构性但**签名库无直接匹配**（校准的 edge 签名是 d≤1 窄边界层）。这是否满足红队 R1-4 说的"QC 对未见类型失效"这一审稿质疑？还是你们认为"宽边界"和窄边界签名同族、算不上真正的"未见类型"，H4 需要一个跨族的伪影（如纯 drift×dust 组合）才算数？

3. **【batch 全局效应的 truth 标签口径】** §4 的 false_attribution_rate=0.74 部分源于：batch−0.18 是**全局**批次效应，truth 标签该标 **designated**（仅注入器指定孔）还是 **all-affected**（所有受影响孔）？前者会让"正确归因了受影响的清白孔"被判假阳，后者会让归因门槛形同虚设。红队 D2 路线倾向哪种口径？这直接决定 glare/edge 0.13–0.18 里有多少是真溢出。

4. **【污染率有效口径】** R1-3b 我们做了 raw/有效双列。但"有效训练集"要重放各臂 `aggregation.prepare`——os-soft 的 QUARANTINE 降权观测算不算"入模"？降权到 w=0.1 的观测是"部分入模"，二值化的污染率该如何计权？红队认为该报**二值口径**（入/不入）还是**加权口径**（按有效权重）？

5. **【predictive 指标 vs regret】** H1 重解读后，os 的价值落在"污染防护/假最优拒斥"而非 regret。红队 R1-1 建议把 H1 与"regret-污染解耦"合并为诚实负结果。那么论文的**主张一**是否应从"os 降 regret"整体改写为"os 在签名匹配场景提供污染防护、代价是非匹配场景 regret 付费"——即把 os 定位从"更优的 BO"降格为"污染鲁棒的 BO"？这个降格红队认为是诚实必要，还是矫枉过正（毕竟 resweep 后 regret 可能改善）？

---

## 6. 时间线简表

| 时点 | 事件 |
|---|---|
| 2026-07-11 | 红队 R1 交付（`docs/STRESS_TEST_R1.md`，六路并行 + 整合去重） |
| — | 蓝队逐条独立复核：六大主轴 + P2×15 + P3×14 全部验证，无驳回 |
| — | **阶段一 机制**：R1-2 三条（p_global 契约键 / 风险图现算 / cusum 接线）+ 连锁修 LayoutPlanner 饿死 |
| — | **阶段二 协议**：R1-3 四条（seed_set 过滤 B / 污染双口径 / Huber / 置换检验+CI）+ R1-5 三条审计 |
| — | **阶段三 门面**：R1-6 三条 + P2 门面批 + P3 修复批 |
| — | 全量回归 **479 passed / 0 failed**（job 4667159，EXIT=0） |
| — | 2700 格 resweep 上集群（job 4667180/1/2）：os/os-soft 全重跑 + robust n=3 + S3/S4/drift/dust 补场景 |
| — | 规划制品：`ARCHITECTURE_V2_PROPOSAL.md`（机制活性注册表 / 无静默空转不变量 / 协议即代码）+ `ROADMAP_V2.md`（逆序即作弊红线） |
| 2026-07-11 | **resweep 落地**（2700/2700 格零失败，`runs/r1_resweep/report/`）→ H1 终判 **`H1_REJECTED_os_worse`**（os 显著劣于 r3-robust，池化 p=0.0001）；机制修复前后 os regret 无统计变化（p=0.92）→ 各臂对比定稿，按预注册纪律记 deviation |

---

*本文语言克制、诚实。红队对的地方我们直说对；数字未落地的地方我们标待 resweep，不兜底。*
