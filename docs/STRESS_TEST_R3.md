# 建设性健壮性审查 · R3 报告（前哨 + 可定稿部分）

> 2026-07-11 晚。审查方按 R2 §6 计划推进 R3（闭环终审），本文是**前哨 + 已可定稿的部分**：
> resweep 2700 格刚落盘、修复方尚未聚合，恰是"聚合前急件"最有价值的窗口。本轮共 **12 路
> 独立审查/复算/调研**（G3 数字复算 / Q3 状态机 / F3 机制活性实证 / F3b 击杀验收 / B3 批次
> 方向根因 / MU 变异存活 corpus / HY 属性状态机 / BOF 对照公平性 / SCH 产物契约 / TR 主张
> 溯源 / O3-A/B/C/D 参照包 / FR-1/2/3 前沿切面），审查 agent 一律 Opus，Slurm 全分区 down
> 期走授权 ssh g208/g209，仓库与 runs/ 全程只读，实验限沙盒。
> **时点注记**：修复方在本轮期间持续改码（policy.py mtime 17:39/17:59、
> test_mechanism_activity.py 与活性观测面在重扫落盘后 11 分钟才落地）。凡裁定均标注快照
> 时点，修复方按最新码复核即可，不算翻案。**R3 终审的剩余项**（H1' 终判、grade 校准、
> 保费上限、分层信任反例、M15 论文数字终审）待修复方聚合 resweep + 消化本文急件后进行（§9）。

---

## 0. 总裁定

三句话版本：

1. **R1-2 接线层正式闭环**（R2 复验的头号遗留）：修复方落地的 `tests/test_mechanism_activity.py`
   把 F 路三个议定变异（E/D/权重恒 1）**逐一击杀**（F3b 验收矩阵 §3），且 F3 对 resweep 落盘
   数据的重放证明三机制在真实扫描里通电且剂量单调。表演性构造放行属守门已知边界，出 P2（§3.2）。
2. **resweep 数据卫生与统计基础扎实**（SCH 178 断言 177 PASS、G3 全 4150 格零异常、HY resume
   等价 200 examples 过），**但聚合前有三件必须处置的急件**（§1）：批次"选哪批异常"方向 100%
   判反（新 P0，pre-existing 非 M10 回归）、drift 场景结构性检不出（R2 §1.1 白跑成真）、dust
   检出恒等式（同 glare 一类）。这三件不定性，聚合出的检出/归因曲线与 batch/drift/dust 相关
   结论都会带病。
3. **论文面双喜双忧**：喜——三主张在 2026-07-11 前沿切面下均无撞车（FR-1/2/3），且拿到定理级/
   实证级外部背书可收编；忧——TR 溯源发现 CHECKPOINTS 仍直书"H1 过"无 deviation 标注（与
   README/PAPER 的诚实版矛盾）、headline p 值在 stats_tests.csv 无来源（§6.4），M14 必修。

基调不变：加固不是否定。本轮"已核验清单"（§7）与 findings 同等重要——尤其 os-soft/robust
对照、预算公平性、数据契约这几处，审查方主动确认"公平且扎实"。

---

## 1. 【紧急】修复方聚合 resweep 前必须处置的三件事

### 1.1 [P0] 批次检查"选哪批异常"方向 100% 判反——干净批被隔离、污染批入训练（B3 路根因 + 主会话实锤 + G3 佐证）

- **证据**（file:line 与探针 `/tmp/claude-1128/dimb3/probe_direction.py`）：
  - 现象：`S2r3.batch_shift.-0.18__os__s1000` 八轮里被注入的 B1 批（值压 −18%）**全部 TRUSTED**，
    干净的 B0 批 7/8 轮整批 SUSPECT。G3 20 种子均值：os `injected_in_training≈0.98`（naive 0.5）、
    `training_contamination` 0.56 vs naive 0.36。
  - 根因：`expos/qc/checks.py:461` `top_b = max(batch_shifts, key=abs)`。两批棋盘格下
    `batch_shifts[B0] ≡ −batch_shifts[B1]`（IEEE 精确相反数，已复现验证），**绝对值恒平局**，
    `max` 落插入顺序第一个批——首孔 A1 在 `(row+col)%2` 下永远是 B0，故**触发即标 B0（干净批）**。
    可定向的 `shift_hat` 符号（`checks.py:449`，实测 ≈−0.19）在选批处未被读取；哨兵（每轮 2/3
    横跨两批、有冻结期望）从不参与选批，且 `sentinel_band [0.05,0.45]` 宽到 −18% 位移后仍在带内。
  - 波及面（每格 = 20 seed × 8 round）：S2r3 四档（−0.05/−0.07/−0.10/−0.18）与 ③线 S2 四档与
    S4.batch_dust，**全部 correct=0**——凡触发必判反，−0.18 档 160/160 全反；S4.edge_gradient_batch
    155/160 未触发（`checks.py:456` `and not edge_fired` 静音了批次段，触发的 5 轮仍全判反）。
  - 一期对照：full_sweep 旧估计器同样 0 correct / 117 inverted / 43 not_fired——**判反
    pre-existing，非 M10 重构回归**；M10 只把触发率拉满（检出 1.0），放大了恒错方向的一致性。
  - 指标盲区：检出率 1.0 只问"有无位移"（方向盲）；旧口径归因精度 0.997 是把 **1708 个被误标的
    干净孔按"原因类型对"计 correct** 抬上去的；逐孔口径 1.0 的分母被 TRUSTED 掏空（20 种子仅 17
    个注入孔归因事件）。唯一露馅的是副列 `false_attribution_rate=0.737`。
- **为何重要**：这是 QC 的方向性根错误。maximize 下乘性负位移近似保序，掩盖了它（wrong_optimum
  仍 0、regret 未爆），但 os 系统性隔离干净批、拿污染批训练；位移一旦非保序或换方向，将直接污染
  代理模型与最优判定。**它同时重新定性 R2 答问 3**：修复方诚实披露的 false_attribution_rate=0.74
  当时部分归给"truth 标签口径"，现在证实主因是方向判反，不是口径问题。它也解释了 G3 的三个
  信号（强 batch 场景污染反超、n_suspect_rate 激增、S4.batch_dust 反常）。
- **建议修复**（按强度）：① 主锚=哨兵绝对参考——每批哨兵均值 vs 冻结 target，偏离侧=异常批；
  同时收紧或替换过宽的 sentinel_band（改跨批哨兵差显著性判据）。② 过渡廉价修=用已算出的
  `shift_hat` 符号定向（`shift_hat<0` ⇒ 异常批=偏低批），显式记录"低=异常"前提（升高型污染
  需哨兵锚才能分辨）。③ 无论哪种，删掉 `max(key=abs)` 这个两批下不含定向信息的对称平局选择器。
  ④ 评测侧补一列**方向正确率**（fired_batch vs truth 注入批），使判反不再被检出/精度掩盖。
- **如何验证**：复跑 `probe_direction.py`，期望全档 inverted→0、correct=触发轮数；
  `false_attribution_rate`（batch −0.18）从 0.737 大幅回落；os `injected_in_training` 从 ≈0.98
  回落到 ≈0.5。**注意**：batch 参与的所有 resweep 格（S2/S2r3 batch 各档 + S4.batch_dust +
  S4.edge_gradient_batch）在修复后需要重跑，聚合前先定这条，避免二次 resweep。

### 1.2 [P1] drift 场景白跑成真（R2 §1.1 复核）——检出是与幅度无关的 0.2 平线，比恒 0 更易误读

- **证据**：注入器未改（`adapters/artifacts.py:203-213` `injectors_for_round` docstring 自认
  "每次调用返回新实例——drift 内部状态按轮重置"；`artifacts.py:164` `applied=|drift|>1e-9` 全亮
  标签也未改）。主会话逐格数了全部 drift 格的 `qc_report.check_counts`：**五档 sigma
  （0.002→0.03，跨 15 倍）检出恒 4/20=0.2，每档每臂完全一致，零剂量响应**；零伪影本底 1/20；
  S4.gradient_drift 的 drift 分量=本底（1/20）。聚合器 `INJ_TO_CHECK` 含
  `instrument_drift→temporal_drift`，会照单产出这条平线。协议无盲区声明。
- **为何重要**：0.2 平线比恒 0 更危险——看起来像"弱但真实的检出能力"，实际是轮内相关噪声抬高
  假阳本底的伪信号（注入语义"轮内 AR(1)"与检测语义"跨轮 CUSUM 游走"正交，`checks.py` 自己的
  注释都承认低于信息地板）。波及 drift 300 格 + S4.gradient_drift 60 格。G3 块 5 与此自洽：
  低幅 drift 下 os 既不检出也不压污染。
- **建议修复**：仍是 R2 §1.1 的二选一，现在有开源参照加持（O3-A）：(a) 注入器状态跨轮持久
  ——campaign 级实例缓存 + 全局时间索引 `t = round·caps + capture_index` 驱动 AR(1)（river/
  scikit-multiflow/Menelaus 的共同做法："漂移状态只由全局流索引推进，绝不因新实例/新批归零"），
  顺带把 `mode` 扩成 `{sudden, gradual, incremental, recurring}` 类型学、truth 补"漂移起点轮 +
  每轮受影响"双标签；(b) 最低限度在协议与图注声明"per-round AR(1) 与跨轮检查正交，预期检出为
  假阳本底，作为诚实盲区呈现"，并把 `applied` 判据改 `|drift| > k·sigma`。走 (a) 则 drift 360 格
  重跑，与 1.1 的 batch 重跑合并一批。
- **如何验证**：修后 drift 检出应随 sigma 单调上升（O3-A 建议的"生成器-检测器配对 bench"断言），
  且该断言天然成为可被"漂移持久化失活"变异击杀的守门。

### 1.3 [P2] dust 检出恒等式——与 glare 同类的"独立证据通道"，五档 20/20 平线与扫描幅度构造性解耦

- **证据**：`adapters/artifacts.py` DustNucleation.apply 只动主指标（value ×(1−drop)）；真正被
  dust_channel 读的 `grain_count` 标记是模拟器种的——`sim_base.py:147-148` dust 命中时
  **固定 ×3.0**，而 `checks.py:87` `DUST_RATIO_LO/HI = 1.8/3.0` 坡道恰在 3.0 封顶。扫描参数
  是 `drop`（0.1–0.8，不触标记）。实测五档 os 全 20/20、零伪影本底 0/20。
- **为何重要**：R2 L-3 对 glare 的判定原样适用——检出率是"副本对里出现一次命中"的组合恒等式，
  不含统计推断；放进检出曲线会被读成"dust 全档强检出能力"。
- **建议修复**：图注与协议标注"独立证据通道，不参与能力比较"（与 glare 同款处理）；或让
  grain_count 标记随 drop 缩放制造分布重叠，使检出真的有统计内容。前者零成本，聚合前就能做。
- **如何验证**：标注路线查图注/协议文字即可；缩放路线期望检出率随 drop 出现剂量响应。

**§1 附带两条口径提醒（聚合脚本层面，成本≈0）**：
- G3 发现 full_sweep 的 S2 场景**偶数幅度档只有标定集 A 种子**，③线"机制通电前后"配对只在
  10/19 场景成立——聚合与论文引用凡涉前后对比，须带 `NO_B_OLD` 声明（G3 `block1_os.tsv` 已标）。
- BOF P3：resweep 网格臂间覆盖不均（os/os-soft 48 场景 vs naive 29 / robust 10 / rcgp 0），
  聚合层应断言参与配对的臂间 n_pairs 相等，结论带 (臂, 场景集, 种子集, n_pairs) 声明。

---

## 2. R2 §1 五急件核验表

| 条 | R2 要求 | 裁定 | 证据 |
|---|---|---|---|
| §1.1 drift 注入器 | persist 或声明盲区 | **未整改，白跑成真** | 见 §1.2 |
| §1.2 rcgp 容量税 + os-lite 消融 | 至少报模型税基线 | **未动** | resweep rcgp 0 格、无 os-lite 臂；O3-B 已给有据的最小改法（§8.2），与 BOF P1 的采集受控臂合并成"归因纯化"整改包 |
| §1.3 artifact 种子孤儿 | 接线或改措辞 | **半闭** | 代码已诚实改名 `run_cell.py:64 artifact_orphan`（选了声明孤儿路线）；但 `M9_PROTOCOL.md:177-181` 仍主张"跨臂同 base 同伪影实现（配对可比）"——协议措辞未同步，一句话的事 |
| §1.4 NFS 水位 | 盯 df | **无恙** | 83% 持平、16T 余量；resweep 实际增量可控 |
| §1.5 检出口径 | 量纲 + round-0 + glare 标注 | **半闭** | round-0 已纳入且新旧双列（`aggregate.py:176-178, 446-449, 686-687`，好评）；L-1 量纲（绝对效应/噪声尺度横轴）未落地；glare 标注未见，且新增 dust 同题（§1.3） |

---

## 3. R1-2 接线层终裁（F3 实证 + F3b 击杀验收）

### 3.1 裁定：闭环 ✅

- **击杀验收**（F3b，副本快照 ~17:39，留档 `/tmp/claude-1128/dimf3b/`）：基线
  `tests/test_mechanism_activity.py` 3 passed（g208 空载 20.6s 复核）；变异 E（`planner/policy.py:478`
  返 risk_map=None）→ **RED**（test_E:87 "生产接线被断开"）；变异 D（`policy.py:382` 风险折扣
  分支首行 raise）→ **RED**（第 4 轮 streak=2 进 failure_aware 即触发）；变异 F-3
  （`qc/policy.py:460` `_weight` 恒 1）→ **RED**（soft/TRUSTED alpha 中位比值坍到 1.03，跌破
  3.0 阈）。三个议定变异全部被语义正确的断言击杀，非碰巧崩溃。**R2 §2.1 的验收标准达成。**
- **落盘实证**（F3，`/tmp/claude-1128/dimf3/`）：resweep 数据本身不含活性观测面（观测面代码
  16:48 晚于重扫落盘 ≤16:37，0/2700 命中 `risk_map_applied`/`aggregation_alpha`），但经忠实重放 +
  `stage_changed` 直接证据，三机制在真实扫描里**通电且剂量单调**：风险图非常数真达 LayoutPlanner
  （edge 0.2/0.35 档 8/8 格 n_distinct≥2，零伪影 0/8）；failure_aware 被闭环驱动（434/1920 格
  进入，高档场景 20/20，零伪影 0/20）；软信任降权真实发生（污染场景上千条 w<1 直至 0.05 地板，
  零伪影近乎全 1）。正负对照干净。
- **一手证据缺口（轻）**：要在生产扫描里留活性观测面的一手痕迹，重跑结构伪影子集即可——建议与
  §1 的 batch/drift 重跑合并。

### 3.2 [P2] 表演性生效构造放行（ARCH_V2 反问 1 的答案）

- **证据**：一行变异——`loop.py:396` 把 `build_experiment(..., risk_map=plan.risk_map)` 换成
  `risk_map=None`。`risk_map_applied`（loop.py:409）仍从 `plan.risk_map` 派生并照常发射非常数
  概括，三条守门测试**全绿放行**。test_E docstring "n_wells 对齐即证明被消费"的表述被此构造证伪。
- **为何重要**：守门锚定在**产出侧概括**，发射点与消费点之间的最后一跳（PlanResult→LayoutPlanner）
  不设防——这正是"观测面活跃但机制空转"形态的残余段。定 P2 而非 P1：盲区只剩一条转手表达式，
  且三个议定目标全被杀。
- **建议修复**（三选一，由弱到强）：① `risk_map_applied` 的取数移到消费侧（`build_experiment`
  内部或 `LayoutPlanner.assign` 入口概括**实收**参数）；② test_E 加效果侧断言（候选孔平均风险
  显著低于全板平均）；③ ARCH_V2 §2 的 `mechanism_effect` 差分形态（同种子开/关 risk_map 两次
  布局逐位比对 `n_affected>0`）。O3-D 的参照建议（§8.4）与 ③ 直接互补。同时修 test_E docstring。
- **如何验证**：MUT-P 补丁（`dimf3b/patches.md`）复跑，修复后应转红。**建议把 E/D/F-3/P 四个
  变异升格为版本化 pinned mutant corpus**（O3-A 的 mutants.toml 模式 + MU 路 17 个存活变异作
  初始种子，§5.3），CI 硬门"全杀方算落地"。

现状核对（非违约，记录）：EXP011 未实装（lint 止于 EXP010）、`kernel/mechanisms.py` 不存在、
注册表 0 机制、`tests/mutants/` 不存在——与 ARCH_V2 "先行版逐步接入"的迁移自述一致。

---

## 4. R2 遗留定向复验裁定

| 条目 | 裁定 | 证据 |
|---|---|---|
| Q 路矩阵复跑（reclassify 组合守卫） | ✅ 闭环 | Q3 对最新码枚举：from=TRUSTED 行 planner/human 各 4 ALLOW/16 DENY 与 R2 预期精确一致；全表分层合理（翻案仅 human、agent 全 DENY、PENDING 双向拒）。`lifecycle.py:161-216` |
| Q-2 重路由守卫 | ✅ 闭环 | human 改判 FAILED 后 re-route 响亮 DENY、终态不变（`lifecycle.py:117-123`） |
| Q-4 空 QCReport | ✅ 闭环（带边角） | `checks=[]` 抛 LifecycleError（`lifecycle.py:95-101`）；**HY 精化**：守卫只护 TRUSTED 分支，`suspicion≥0.3` 的零 checks 报告会在守卫前落 SUSPECT/QUARANTINE 不抛——"须有证据方能处置"（公理 2）的边角，交修复方判断是否收紧 |
| Q-3 [0,1] 约束 | ✅ 闭环 | score/suspicion/trust_confidence 对 7.0/−1/NaN 全 ValidationError（`objects.py:274,282,318`） |
| Q-5 改判置信语义 | ✅ 闭环（但见 §6.1 新 P2） | 置 1.0 + `from_confidence` 双写留痕（`lifecycle.py:259,267`），高危翻案另落 conflict 事件 |
| 3 个崩溃 run（U 路） | ⚠️ 半闭 | 修复方已干净重跑三格（数据修复，score.json 齐）；R2 要的"崩溃场景重放验证 reconcile 覆盖该失效模式"未见专项证据——请修复方回应时确认（现有 crash 注入测试若已覆盖该模式，指认即可） |
| resume 等价 | ✅ 加固确认 | HY-2 状态机：torn-tail 随机截尾 + checkpoint 回退 + resume 的随机组合下，科学字段与一次跑完逐项等价（8 examples×5 steps） |

---

## 5. resweep 数字独立复算（G3，4150 格零异常）与工程验收面

### 5.1 核心数字（详表与脚本 `/tmp/claude-1128/dimg3/`）

- **机制通电前后 os 配对**（③线 10 个可配对场景 × 20 种子）：`final_regret` 配对差 **+0.0001**
  （perm p=0.91，逐场景全不显著）——**机制修复没有改变 os 的 regret**；改变的是 QC 活动
  （n_suspect_rate +0.023，p=5e-5，集中在 batch −0.18 档 +0.153——现在知道那是 §1.1 判反机制
  在整批标记干净孔）。os-soft 同向。**含义**：H1' 终判时"机制通电后 regret 改善"的叙事不可用，
  deviation 记账维持 R2 §4 问 5 的定稿方向（可信性保障主张 + regret 相变条件）。
- **robust 有效对照（R1-3c 终判素材）**：S2r3（replicates=3）四臂同期配对——robust−naive 在
  regret 与污染两条线全部 perm p 不显著。**升到 n=3 的中位数仍无可测保护**；真正压污染的是 os
  （edge.0.35：0.095 vs naive 0.547）。R1-3c 可按"n=3 复核后结论不变"关账。
- **S3.wide_edge（留出伪影，H4 素材）**：检出有真实剂量响应（0.05 档 1/20 → 0.4 档 20/20），
  相对匹配签名 edge（0.05 档即 15/20）曲线整体右移——"族内泛化退化、高幅不失效"；os 压污染
  随幅度单调增强（−0.007→−0.266，全 5e-5），代价是小 regret 税（高幅显著）。**这是 H4 的诚实
  正面素材**，措辞锚见 §8.2。
- **S4 组合**：os 强压污染（edge_glare −0.525 等，全 5e-5）；例外 S4.batch_dust +0.065（p=0.002）
  ——已被 §1.1 根因收编。**dust 单伪影**：os 全档压污染近零；**drift**：低幅不压污染（与检出
  平线自洽）。

### 5.2 对照公平性（BOF 路）——一条 P1 + 三处公平确认

- **[P1] naive↔os 头号对照捆绑了采集器变更**：五元组逐臂 diff 证实三个净差配对
  （naive-robust / naive-rcgp / os-ossoft）是干净单因子消融，但 naive↔os **四元同变**——os 的
  gp 阶段用 Kriging-Believer 幻想批（`response_gp.py:248-300`）+ ε 探索配额 + failure_aware
  风险贴现，naive 用静态 UCB top-n（`sampler.py:93-100`）。"信任路由增益"与"采集增益"混淆，
  方向利 os。**修法**：增设 `naive-kb`（naive 裁决/聚合 + os 采集）或 `os-baseline-acq` 受控臂
  做两段分解；与 rcgp 容量税（§2）、os-lite 消融（R2 K-P2）合并为一个**"归因纯化"整改包**，
  一次 resweep 补齐。至少论文明示 naive↔os 是系统级对照而非单因子消融。**联动提醒**：G3 已证
  os regret 不优于 naive——扣除采集增益后 os 的真实 regret 税可能更大，保费表述要留余量。
- **三处公平确认（进已核验清单）**：预算处理对 os 偏严（REMEASURE 占自身候选槽
  `policy.py:448-449`、哨兵五臂同税，对齐 Ax 惯例）；regret 口径 = Ax inference trace 的刻意
  合理选择（best-trusted 是各臂自己的推荐器、评分规则与 f* 同一）；三净差配对干净。
- P2：主图误差带 `np.std`(ddof=0) vs Ax 惯例 SEM(ddof=1)（n=20 差 4.5×），主推断（置换+bootstrap）
  正确——改带宽口径或图注明示即可。

### 5.3 变异存活 corpus（MU 路）与属性测试资产（HY 路）

- **MU**：38 个定向语义变异，**17 存活（45%）**，缺口集中在 `planner/policy.py`（5/6 存活）与
  `qc/checks.py`（7/8 存活）；`arbiter.py` 0/6 全杀是专测充分的正面样板。前二危险存活均 P1：
  删 risk_map 0.25 粗分层护栏（正是 R1-2b 留档的区组饿死触发条件，套件从不把失败模型喂到暖区）、
  副本门控 `and→or`（开启代码注释自警的低值 CV 假阳路径）。台账/补丁/驱动在
  `/tmp/claude-1128/dimmu/`，**可直接作 pinned mutant corpus 的初始种子**（配 F3b 的 E/D/F-3/P
  四个 + O3-A 的 mutants.toml 版本化模式）。
- **MU 附带（套件健康，请修复方优先看）**：`test_property_kernel` 两条 adjudicate 属性测试在
  现码**确定性红**——Q-4 空报告守卫加了、旧断言没更新（仍喂 `checks=[]` 期望返回裁决）。即
  修复方当前树全量套件不绿。另有 `test_compare` 零伪影 flag 与 `test_planner_stages` 禁 import
  两条**顺序相关 flaky**（孤立绿、全量红）。
- **HY**：三台可收编的 hypothesis 状态机（`/tmp/claude-1128/dimhy/`）。信任生命周期机 200
  examples 自动摇出 §6.1 的语义冲突（独立复现 Q3）；resume 等价机、override 投递机全过
  （§4 表）——后两台是"已核验"资产。
- **SCH**：产物契约 178 断言 **177 PASS / 1 WARN / 0 FAIL**（唯一 WARN 是 full_sweep 台账残留
  的 R2 已知 stray 行，聚合器已显式捕获不污染报数）。契约资产 `/tmp/claude-1128/dimsch/` 可收编
  CI；红线级出口断言建议五条：分片 union==master、f_star 同场景恒同、rounds==8+run_dir 一致、
  n_pairs==种子数+p/比率/regret 域检查、yaml 引用存在性。

---

## 6. 新表面 findings 总表（按批次归属）

完整四段式散见各节；此处行动清单。标 ★ = 与聚合/论文直接相关的优先项。

### 6.1 归 M11（机制/内核批）

| 级 | 条目 | 一句话 | 来源 |
|---|---|---|---|
| P0★ | 批次方向判反 | §1.1，聚合前定性 | B3 |
| P2 | trust_confidence 跨模块语义冲突 | `lifecycle.py:267` 改判无条件置 1.0（人工确定性）；os-soft `qc/policy.py:487` 把 QUARANTINE 观测该字段当 suspicion 读 → 人工 RESCUE 反转（human 降级 FAILED→QUARANTINE 意图从轻，os-soft 夹 w=0.05 地板近乎剔除，与意图相反）。修法倾向：os-soft 改读 `qc.suspicion`（单一事实来源），或 reclassify 到 QUARANTINE 回填带内值 | Q3+HY 双路独立 |
| P2 | 表演性生效盲区 | §3.2，消费侧最后一跳 | F3b |
| P2 | 空报告守卫只护 TRUSTED 分支 | §4 Q-4 行边角 | HY |
| P1×2 + P2×6 | 变异存活 corpus 前八项 | §5.3，逐条建议断言在 `dimmu/REPORT_R3_MU.md` | MU |
| P2 | 套件两红两 flaky | §5.3 附带——先修红再谈守门 | MU |

### 6.2 归 M12/M13（评测协议与重扫批）

| 级 | 条目 | 一句话 | 来源 |
|---|---|---|---|
| P1★ | drift 白跑 | §1.2 | 主会话+G3 |
| P2★ | dust 恒等式 | §1.3 | 主会话 |
| P1★ | 采集器混淆（归因纯化包：naive-kb/os-lite/rcgp 对等/可选 tempered 臂） | §5.2 + §8.2 + FR-2 补臂建议 | BOF+O3-B+FR-2 |
| P2 | 评测补"方向正确率"列 | §1.1 修复配套 | B3 |
| P2 | NO_B_OLD 配对口径声明 + 臂间 n_pairs 断言 | §1 附带 | G3+BOF |
| P2 | 误差带 SD→SEM/CI | §5.2 | BOF |
| P3 | SCH 五条出口断言 + full_sweep stray 行清理 | §5.3 | SCH |

### 6.3 归 M14/M15（门面与论文批）

| 级 | 条目 | 一句话 | 来源 |
|---|---|---|---|
| P1★ | CHECKPOINTS 三处直书"H1 过"无 deviation | 与 README/PAPER 诚实版矛盾——同一预注册判据两处结论方向相反（预注册判据实测：四结构档 os_vs_robust regret 全 p<0.05 **劣于**；引用的 0.0086/0.0179 是 S0.demo·os-vs-naive，换臂换场景） | TR |
| P1★ | headline p 值无产物来源 | "假最优 p≈7.7e-8、污染 p<1e-4、regret p=0.0645"均不在 stats_tests.csv（S0.demo 实为 0.0668）；"1450 格"vs 权威报告 1000 格差 450 无解释 | TR |
| P1 | H2/H4 结论性表述"数字未落地先行" | drift/dust/S3/S4 主张全部指向空产物（resweep 已跑未聚合，正好补上——但先过 §1 三急件） | TR |
| P2 | H3 两口径并存 | "板级 2.5%"（M5 试点）与"0.11%"并引；H3 本身链完整、TR 独立复算通过（模范） | TR |
| P2 | claims.json 前向工具化 | aggregate.py 出口 dump claim_id→value/scenario_set/comparison/source_cells_glob/pass_bool，散文只转引——堵住 6 条断链里 4 条的成因；与 U-2 的后向 join 互补 | TR |
| P3 | M9_PROTOCOL:181 配对措辞同步 | §2 表 §1.3 行 | 主会话 |

---

## 7. 已核验清单（R3 汇总，与 findings 同等重要）

- **接线层三机制通电且剂量单调**，守门测试杀掉全部三个议定变异（F3+F3b）。
- **状态机五点全闭环**：转移矩阵与预期精确一致、重路由守卫、空报告拒、[0,1]+NaN 约束、改判留痕
  （Q3）；非法改判异常安全（抛错后观测文件逐位不变）、seq append-only、TRUSTED 必有非空 checks
  ——各 200 examples 属性验证通过（HY）。
- **resume 等价在随机崩溃组合下成立**（HY-2）；**override 投递幂等/无遗失/审计成对**（HY-3）。
- **数据卫生**：4150 格 score.json 零异常（G3）；产物契约 177/178 PASS，resweep 分片
  union==master 无重无漏、f_star 48 场景恒同、R2 stray 现象未在 resweep 复现（SCH）。
- **对照公平性三确认**：预算对 os 偏严、regret=Ax inference trace 合理口径、三净差配对干净
  单因子（BOF）。
- **arbiter.py 变异 0/6 全杀**——专测充分的正面样板（MU）。
- **robust n=3 复核后仍≈naive**——R1-3c 的"中位数无保护"结论在有效对照下维持（G3）。
- **S3.wide_edge 留出伪影有真实剂量响应**，os 压污染单调增强——H4 有诚实正面素材（主会话+G3）。
- **三主张 2026-07-11 前沿切面下均无撞车**（FR-1/2/3），"闭环内结构化偏差注入"空白维持。

---

## 8. 参照与文献包（供修复方按批次取用，全部编号已验真）

### 8.1 drift 修复参照（→M13）
river `ConceptDriftStream`（sample_idx 全局持久 + sigmoid position/width）、`FriedmanDrift`
（drift_type {lea,gra,gsg} + position 元组 + transition_window = 漂移类型学范本）、Menelaus
注入窗口即真值标签。五条移植建议见 O3-A 交付（含"生成器-检测器配对 bench 断言检出随幅度单调"）。

### 8.2 rcgp 对等 + H4 措辞锚（→M13/M15）
- RCGP 原文（arXiv:2311.00463）协议：β 固定 σ/√2 只选 c、c=Q_n(1−ε) 分位法（ε=0.05，原文明确
  否决 c-LOO）、核超参目标=LOO-CV（官方源码 `maximum_log_likelihood_objective→loo_cv()`）、
  附录 B.2/B.5 两臂**共享同一核族、各自连续优化**——expos rcgp 臂粗网格偏离原协议。对等最小
  改法：ARD+L-BFGS+同重启预算，保留 LOO-CV 目标与 Q_n 权阈。公平性锚：Melis 1707.05589 /
  Lucic 1711.10337 / Dodge 1909.03004。**诚实边界**：原文未确证回归实验用 ARD，"升 ARD"依据是
  与 naive 臂对齐 + 通用公平性文献，勿过度归因原文。
- H4 分级锚：Fort 2106.03004（near/far-OOD）+ Yang 2110.11334（covariate/semantic shift）——
  宽边界=near-OOD 族内参数外推，跨族（非指数衰减族）=far-OOD 待补；Ding 2203.14506
  （seen/unseen 异常 benchmark 惯例）。SPC 退化量化锚：Chen & Chen 2007
  （doi:10.1080/07408170701315321，控制图对失配 shift 尺寸的 ARL 退化）——"检出曲线右移"是
  既定现象非本项目伪影。
- FR-2 补臂建议：**tempered/power-likelihood 臂**（2601.07094，os-soft 的理论孪生，~1 天，堵
  "稳健模型已解决"质疑）最高优先；winsorize 低优先；t-GP 引用讨论即可。

### 8.3 口径与理论锚（→M12/M15）
- 加权污染 Σw·1[contam]/Σw：Markatou 1998 加权似然（部分降权按权折算先例）+ Elvira 2022 ESS
  （承认近似性）+ Huber 1964（二值口径本源）；rcgp w=1/infl 锚 Koh & Liang 1703.04730 +
  Data Shapley 1904.02868。
- os-soft v2 w(s) 形状：σ²/(σ²+n·b̂²) = "方差比收缩"母式——Bühlmann 1967 信度 Z=n/(n+k) +
  Kalman 1960 增益（跨域同形）+ Bissiri-Holmes-Walker 2016（generalized Bayes 框架锚）；
  动力学 SafeBayes 2017。
- 路由层必要性理论亲缘：**2601.11924**（验证恢复可学性定理：高污染无已验证信息则 sublinear
  regret 不可能，一笔已验证预算越阈即恢复）；"防护成本与收敛速率解耦"：2602.10971/2603.15596
  可加污染惩罚 + 匹配下界 Ω(dC)。诚实边界：bandit/GLB 设定，类比锚非 GP-BO 直接定理——正合
  "os 保结论可信性而非收敛速度"的发现。

### 8.4 机制活性守门参照（→M11）
新克隆五仓在 `/Data1/ericyang/r3_os_references/`（共 178M，稀疏检出；是否并入 references/
由修复方定）。**自包含交接文档：`/Data1/ericyang/r3_os_references/M11_HANDOFF_O3D.md`**
（六行增补表 + 走读笔记 + 3+2 条移植建议 + 衔接 F-1/F-2/F-3 负样本的验证提示），修复方
直接读它即可，本节是摘要。三条最有用：① `mechanism_effect` 用 k8s 探针三态 `grade:{active,warning,absent}`
替代 `fired:bool`（`probe.go:22-30` Warning 态现成同构；FailureThreshold 去抖=sweep 级收口），
"表演性生效"有专门落点；② Erlang 失活预算熔断（`supervisor.erl` intensity/period 滑窗 +
VS Code CrashTracker 3 次/5 分钟）——机制连续 N 轮黄牌升红，防长期黄牌掩盖真空转；③
`expos check --fix` 照 redis-check-aof 三段式（valid_up_to 水位截尾、干净截断可自愈 vs 中段
损坏必须响亮失败、默认响亮+显式 --fix），行级独立 CRC 优于链式累积。反差注意：VS Code
"声明未激活"是正常惰性语义，与 EXP011 方向相反，只作反差立论。击杀验收工程化：cosmic-ray
baseline+差分（`work_item.py:24-33`）、MutationSpec 可寻址 pinned mutant、
`survival_rate --fail-over` CI 硬门。
**实现级下钻补两条硬纪律**（O3-D 增补）：(a) `--fix` 的"只愈尾"要做成**结构约束**——水位之后
必须直达 EOF 才许截，中段坏行=CorruptedRun（`redis-check-aof.c:306-328` 三重防误截：无 --fix
只诊断、有 --fix 仍交互确认默认 N、多段只许截末文件）；(b) 自愈扫描**遇第一个坏行即停**，绝不
跳行打捞后面的好行（SQLite `walIndexRecover` `wal.c:1390-1530` 语义——只认连续有效前缀，否则
中段损坏会被伪装成尾损）。诊断报告学 check-aof 给字节偏移+行号双坐标。另：k8s
`results_manager.go:27-47` 探测与处置解耦（worker 只 Set 缓存、从不直接动容器）——
`mechanism_effect` 发射者"只记事实不判档"、红/黄裁决收在消费端，可直接引它立论。

### 8.5 前沿切面与 related work（→M15，FR-1/2/3 全文另交）
- **必收编两条**：2607.04382（Settlement Factorization——把公理 7"提案者不得书写评价自己的
  答案键"抬成带定量泄漏界的机制设计定理）；2607.04528（harness 本身是实验变量——协议指纹
  必须纳 fn 文件 sha 的外部实证）。
- 反问 3（grade 校准）外部同构：2607.06596（可疑度监控器校准不跨谱系迁移，41%→19%，须报
  迁移矩阵而非单点精度）——R3 终审该项的评审框架就用它。
- 架构完整性（FR-1 A–G 七项三分类）：多仪器调度=正确排除（建议 CONTROLLER_MODEL 显式化）；
  执行前风险预测=design-note 级；语义安全门（Syntax-to-Safety Gap, 2602.15061）=CAPABILITY_MODEL
  v2 候选绑 G4；claim-license 算子（2606.31273）=轻量协议增补，与 TR 的 claims.json 建议
  正好是同一构件的协议侧与工具侧；Glite ARF/ProtoPilot 进 related work 作"verifier/provenance
  趋同"背书。撞车：无先占。
- 其余可引：2606.27687（为下一个 LLM 预注册的时间封印装置）、2606.31511（placebo 反驳器独立
  同款）、2607.08203（benchmark 审计五点清单）、2607.02055（SASP，空间结构划分=主张①方法学
  背书）、2605.26200（三 collapse，Introduction 现成措辞）。

---

## 9. R3 终审剩余项（待修复方两个动作后进行）

修复方的两个前置动作：**A.** 消化 §1 三急件（batch 方向 + drift + dust）并决定重跑范围；
**B.** 聚合 resweep 出 report（含 §1 附带两条口径声明）。此后审查方做：

1. **H1' 终判独立复算**：对修复方聚合数字复用 G3 脚本逐位对账；deviation 记账核验
   （TR 矩阵为底稿——先修 §6.3 的 CHECKPOINTS 矛盾）。
2. **grade 校准在 S3 留出伪影上的崩坏方式**（ARCH_V2 反问 3）：评审框架用 2607.06596 的
   迁移矩阵范式；前置=推荐档案/grade 机制落地。
3. **保费上限 X 可证伪性**（反问 4）：R2 §4 问 5(c) 方向 + P4 截断正态上界推导复核。
4. **分层信任反例构造**（反问 5）。
5. **M15 论文数字终审**：每个 claim 句 → claims.json/report 产物/limitation 锚（TR 的 A/B/C/D
   分类作验收判据：全部 claim 达 D 或显式 limitation）。
6. **守门回归**：表演性 P（§3.2）修复后 MUT-P 转红复验；mutants corpus 首批收编情况。

*审查方基调不变：这是加固不是否定。本轮修复方最值得肯定的是 test_mechanism_activity.py 的
落地质量（三变异全杀、断言语义正确）与 resweep 的数据卫生（177/178 契约全过）；最需要立刻
看的是 §1——特别是批次方向判反，它 pre-existing、被所有主指标掩盖、且解释了此前多个未解
反常。聚合前处置，一次重跑收口。R3 终审见。*

---

## 10. 对修复方 R2-RESPONSE（终稿版）的即时复核与答反问

> 修复方 `STRESS_TEST_R2_RESPONSE.md` 终稿（含 17:55 终局聚合与 H1' 终判）与本报告 §1-§9
> 是**并行产出、互未见对方**——本节做时序对齐与即时复核。

### 10.1 时序要害：H1' 终判先于 P0 曝光，batch 档带病

修复方 17:55 的终局聚合与 `H1_REJECTED_os_worse`（S2r3 中高档池化 os−robust +0.0161,
p=1e-4）是在**未见本报告 §1.1 批次方向判反（P0）**的情况下出的。P0 直接作用于 S2r3 batch
中高档：os 在这些格隔离干净批、拿污染批训练——"硬隔离数据饥饿"这一根因判断在 batch 档上
实为"隔离了错误的一半"。**H1' 池化里有多少劣化是 P0 缺陷贡献的、剔除 batch 档后
H1_REJECTED 是否仍成立，审查方已安排独立敏感性拆解（AGG3 路，含 batch/去 batch 双版
mean_diff/p/CI），结果出来前请勿把 H1' 数字写入论文面**。edge/thermal 档不受 P0 影响，
deviation 记账本身不受影响（判据跑后重跑的事实不变）。

### 10.2 逐项即时复核

- **三变异击杀**：修复方 kill_record.out 与审查方 F3b 独立验收（§3.1）**双向吻合**（同断言、
  同击杀语义）——R1-2 接线层闭环的裁定双方一致，M11 验收⑧ 可勾。
- **drift 双管齐下**：方向正确（诚实盲区档 + resident 真漂移 + applied_eps）。resident 规格
  已按修复方反问 1 的邀请派专路审查（RES3：非正交性蒙特卡洛 + applied_eps 伪盲区检验 +
  resume 持久机制复核），结果随 R3-B 交付。**注意**：resident 四档场景已备但 0 格已跑，
  开扫前建议等 RES3 的档位/eps 参数意见（半天内），避免三次重扫。
- **M-4b 勘误（接受纠正）**：R2 记"修复方已加 writer.lock"**有误**——全库原本无锁，审查方
  转录了未核实的表述，记 erratum。修复方现补真 `fcntl.flock`（store.py:88-111）+ 并发端到端
  测试，W3 路复核中。
- **两条红属性测试已修**：MU 路发现的 test_property_kernel 确定性红（Q-4 新契约旧断言），
  修复方 549 全绿版已按新契约更新（`test_property_kernel.py:446`）——W3 复核最新树。
- **Q-1 矩阵口径需对齐一次（小事）**：修复方报"planner 6 ALLOW / human 9 ALLOW/7 DENY"，
  审查方 Q3 报"from=TRUSTED 行 planner/human 各 4 ALLOW/16 DENY"（宇宙=actor×from×to×routing
  逐格）。两组数字各自自洽但枚举宇宙不同（修复方疑为 (from,to) 16 格宇宙），R3-B 用同一
  宇宙对齐一次即可，不影响"守卫已落地"的共同结论。
- **X4 理论核证**：修复方"P1/P4=已知重述、P2=中等、P3=真新但轻机械"的定位比审查方原表述
  更精确，接受；主张措辞"一种充分机制"与 X 由推导而非拍定，与 R2 §4 问 5 完全一致。
- **dust 恒等式撞车确认**：修复方 M12 已独立落 `binary_evidence_channel=True`（glare+dust,
  M9_PROTOCOL:150-152）——与本报告 §1.3 同一结论，视为已处置（标注路线）；"标记随 drop
  缩放"路线留作可选增强。

### 10.3 答修复方四+一反问

**反问 1（resident 规格）**：接单，RES3 专路在跑（见 10.2）。先给方向性意见：(a) 的关键是
会话间游走的**每轮增量 σ_walk 与哨兵轮均值标准误的比值**——比值 <1 时 CUSUM 需要多轮累积，
温周期若与轮次同频会制造周期性回零，建议温周期相位随 seed 随机化；(b) 的风险真实存在，
建议 eps 不取绝对常数而取 `k·noise_sd`（与检测侧同量纲），低幅 0.01 档若 applied 率 >30% 而
检出≈本底，就把该档标为"resident 诚实盲区档"而非能力档。定量结论以 RES3 为准。

**反问 2（消融优先级）**：排序 = **os-lite 首位**（已接线，直接回答 K-P2"路由层特异性"）→
**采集受控臂次之且不可省**（BOF 路 P1：naive↔os 捆绑了 KB 幻想批+ε+风险贴现 vs 静态 top-n
的采集器差异，"信任路由增益"与"采集增益"混淆方向利 os；os-minus-* 三臂都仍带 KB 采集，
盖不住这个混淆——需要 `naive-kb` 或 `os-baseline-acq` 之一）→ naive+QC 价值最低（agent
增量已被 os-lite 隔离）。**rcgp**：S1 模型税基线单列先行；ARD 升级（O3-B 给了有据最小改法：
同核族+L-BFGS+同重启预算，保留 LOO-CV 目标与 Q_n(1−ε) 权阈——这是 RCGP 原文协议）后**值得
回 resweep**；若预算只够一个新臂，FR-2 的 **tempered/power-likelihood 臂**（2601.07094，
~1 天）比 rcgp 回归优先——它是 os-soft 的理论孪生，直接堵"稳健模型已解决"的审稿质疑。

**反问 3（活性判据）**：**双档并用**。单环路测试保留固定 3× 比值门（正常 13× vs 变异 1×
隔一个数量级，F3b 实测击杀干净、跑得快）；sweep 级制度化用 ARCH_V2 已写的种子级配对置换
（alpha=0.05, Holm）。"干净轮合法恒等"的解法不是调阈值，而是**夹具选择**：活性断言只跑在
污染 fixture 上、零伪影 fixture 断言静默（F3/F3b 的正负对照模式已验证可行）；再加失活预算
熔断（O3-D：Erlang intensity/period 滑窗——机制在应激活场景连续 N 轮恒等才升红），兜住
"长期黄牌"而不误杀单轮合法恒等。

**反问 4（R3 聚焦）**：**(a)+(b) 审查方已经做完**——见本报告 §3（击杀验收+表演性构造）与
§5（G3 复算），AGG3 正在对 17:55 官方聚合做逐位对账；(c)/(d) 的前置件在飞（CAL3 校准曲线+
迁移矩阵、PREM 保费推导+分层反例）。**新增的最高优先级是 §1.1 P0**：先修方向判反、重跑
batch 参与格，再定稿 H1' 与检出/归因曲线——这卡在 (a) 的前面。

**反问 5（g209 双环境 CI）**：值得，放**合并档**而非每提交（契合你们 CI 三档设计：提交=本地
快速，合并=双环境全量，发版=双环境+抽格复扫）。论据：本轮环境差异摸出 2 个真潜伏 bug，
成本是一次 ssh 全量（分钟级），ROI 为正；且与 FR-3 的 2607.04528（harness 本身是实验变量）
同理——环境就是 harness 的一部分，双环境绿是廉价的"混杂注入"检验。唯一提醒：把 g209 的
环境指纹（python/BLAS/字体等）记进 CI 日志，环境红时才可归因。

## 11. R3-B 增补节（第二/三波 21 路整合，2026-07-11 深夜定稿）

> 完整往来与证据以 mailbox/（red 001-022 × blue 001-012）与各沙盒为准；本节是台账级汇总。

### 11.1 自前哨以来的闭环总表

| 事项 | 状态 |
|---|---|
| P0 批次方向判反 | **修复落地**（双锚+归因交叉守卫）；单格双方独立确认；BA3 八条边界审查判"可托付"（唯一缺口=升高型+band 偏移，生产不可达，入 Backlog）；880 格重跑在烧，完工后 B3 全量验收 |
| H1' 终判 | **双方定格 H1_REJECTED_os_worse**（双独立聚合器逐位一致）；AGG3 敏感性证明**不依赖 P0**（剔 batch 拒绝更强 +0.023）；机制解释=EVAL3 的"伪影–真优对齐"条件性 |
| 表演性 P2（§3.2） | **闭环**：消费侧 provenance 取证根治，C2' 击杀，独立抽验实锤 |
| headline p 值无源（§6.3） | **闭环**：7.7e-8 系二值指标误用参数 t；committed 重算 3.05e-5/1.9e-6，红队闭式独立复验逐位吻合；headline_stats.json=claim→artifact 范本 |
| R2 遗留（drift/dust/资源/文档） | drift=resident 注入器落地+RES3 裁定（P1 applied 判据已修后点火，240 格完工）；dust=binary_evidence_channel 标注；五处旧叙事残留+代际标注平账落地 |
| 熔断参数 | FB3 推导：(3,5) 否决（合法侧 100% 误红），改 consecutive-k k*=7+scope 修+去抖语义，蓝队全接（"单边验证教科书错误"自认） |

### 11.2 新 P1 两条（E2E3/MU2 波，修复在途）

- **[P1] os 家族 resume 非崩溃等价**（E2E3-F1，信 022）：单崩溃即触发——reconcile 只回滚
  物化视图不回滚事件日志，`_pending_actions` 读到丢失尝试的 `action_consumed` 静默跳过
  补救动作，best_trusted 漂移；基线三臂免疫。**R1-5c 裁定需限定重述**。修法=消费侧按
  redo_reconciliation.from_round 过滤。
- **[P1 系统性] 物化视图零故障隔离**（OS3，信 016）：单坏 obs 文件=全 run DoS（含 resume
  写者），expos check 对此全盲；已并入 store.py 合并修复路（view_quarantine+注入测试）。
  MU2 四条 P1（regret 方向无守门/layout 默认路径/after_round 零测试/RCGP UCB 符号）与
  17+17 存活变异 corpus 同批。

### 11.3 已核验清单增补（与 finding 同等重要）

复合应力下整机骨架 I1/I2/I3/I5/I6/I7 全守住（E2E3 七场景矩阵）；audit hook 实证 UI 零写
句柄；truth 隔离四重结构强制（OS3"MMU 级"）；崩溃一致性产品级；基线三臂崩溃逐位等价；
数据契约 177/178；resident 规格四项核验；THEORY_P3 主定理正确且紧（V1-V3 数值验证）；
suspicion 校准四类行为图谱+S2→S3 迁移矩阵（CAL3）；对照公平三确认（BOF）。

### 11.4 架构方向（用户裁决，信 020/021）

四层拆分（QC Evidence/Trust State/Learning Policy/Certification Policy）为 v1.1 头条——
正是 trust_confidence 双语义冲突的病根级解法，PREM/CAL3/EVAL3 是其现成理论地基；
R3-B 前 P0 五件（视图隔离/Claim Compiler+Ledger/batch 重聚合+代际/UI 警示/fresh-clone 门）；
v2 明确推迟。审查方三护栏：α 分档须 tempered 校准驱动非固定带；Ledger 须 pull 计算；
调度层=后端抽象不翻多仪器"不做清单"。

### 11.5 剩余待办（R3 终审收尾）

① 蓝队三批扫描完工报数 → B3 全量验收（判据=inverted 0/correct=触发轮；含 resume 过的
os 格单列核对）+ 消融/resident 独立聚合复算（AGG3 器现成）→ 重聚合冻结 + 代际统一；
② E2E3-F1 修复的 C7 式等价矩阵回归；③ 平账批行号抽验（与 DOC3/TR 对表）；
④ grade 校准终审（反问 3）待 recommendation dossier 落地，评审框架=2607.06596 迁移矩阵。

### 10.4 R3-B 增补节预告

第二波 9 路在飞：AGG3（独立聚合对账+H1' batch 敏感性）、CAL3（suspicion 校准+S2→S3 迁移
矩阵，反问 3 数据底座）、PREM（保费 X 推导+分层信任反例）、RES3（resident 规格）、MU2
（变异第二波：models/design/eval/stats/store/agent）、ATT3（归因方向盲深审+os-soft 冲突
触发面量化）、EVAL3（wrong_opt–regret 张力机制裁定）、DOC3（文档一致性复查）、W3（新人
实操+549 全绿复核）。收齐后出 R3-B 增补节。
