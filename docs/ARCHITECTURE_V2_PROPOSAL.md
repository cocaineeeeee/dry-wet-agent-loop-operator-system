# ARCHITECTURE V2 提案 —— 从第一性原理重审 expos 的内核主张

> 状态：**提案**（2026-07-11）。输入：STRESS_TEST_R1、`runs/full_sweep/report/stats_tests.csv`
> 配对检验、ARCHITECTURE.md、DEEP_REVIEW.md、CONTROLLER_MODEL.md、SOFT_TRUST_PROPOSAL.md、
> CHECKPOINTS 收官/M9 条目。本文**不改任何代码与现有文档**；与 R1 修复线并行、互不阻塞——
> 本文回答"这个系统本质上应该是什么"，修复线回答"现有主张哪些站不住"。
> 六条硬不变量（两持久对象 / DecisionRecord=事件负载 / truth 决策模块不可见 / agent 仅提案解释 /
> 插件不绕信任路由 / 无静默回退）本文修订一条（§6，强化非削弱），其余全部保持并被各节复用。

---

## 0. 执行摘要（给用户与红队）

R1 + 配对检验把一件事钉死了：**os 臂在预注册结构性场景的 regret 显著劣于 robust**
（edge0.2 p=0.0029、batch−0.18 p=0.0027、thermal 两档 p<0.02，方向全反），仅 S0.demo
显著占优（p=0.0014）；但 os 的污染防护与假最优拒斥碾压所有对照。这不是丑闻，是**系统真实
身份的暴露**：expos 从来不是更快的优化器，而是一台"让你敢把推荐拿去放大"的**结论认证机**。
V1 架构的病根是它用优化器的记分牌（regret）给认证机打分，且无法区分"机制存在"与"机制生效"
（R1-2 三机制静默空转、431 测试全绿）、无法机器强制自己的评测协议（R1-1/R1-3 协议漂移）。

五项裁定：

1. **价值主张**：系统的输出从"最优点"重定义为**带证据档案的推荐**（recommendation dossier，
   事件负载，不是第三个持久对象）。评测升为**双一等指标**——收敛 regret × 决策风险——在协议层
   预注册配对判定；内核不引入标量合成目标（λ 合成只作敏感性报告）。
2. **机制活性**：新增内核级**机制注册表 + `mechanism_effect` 强制发射**（每轮每注册机制一条，
   `fired∈{true,false}` + 效应摘要），配三级守门：单元差分测试（开/关必须产生可观测差异）、
   run 级完备性 lint、sweep 级活性断言（应激活场景内活性为零→CI 红）。"静默空转"从此与
   "静默回退"同罪（§6 不变量修订）。
3. **信任类型学**：裁定**保持枚举内核**——suspicion（连续证据）→ trust（离散判决）→ routing
   （离散处置）是"证据/判决/处置"三权分立，不是三层冗余；连续性住聚合层是原则性正确而非妥协。
   补一刀：suspicion 升格为**带校准契约**的量（协议层产校准曲线），使软信任有据可依。
4. **协议即代码**：评测协议从 md 文档变为**可执行制品 `protocol.yaml`**（场景集/种子集/臂规格/
   假设/判定函数按名引用），聚合器降级为其解释器；协议 sha256 进 run manifest；"H1 过"只允许
   引用机器判定产物。附最小 schema 草案。
5. **不可替代内核**：候选五件套（两对象+事件日志+信任路由+提案裁定配对+truth 隔离）**全部保留**，
   **增补第六件**：机制活性证据纪律。模块边界重画：五策略注入点收拢为 `policies/`、证据生产者
   收拢为 `evidence/`、评测器从 `runs/full_sweep/_tools/`（未测试目录——R1-3 漂移的结构性温床）
   收编为一等包 `protocol/`。

一句话：**V1 证明了"信任路由不可被稳健统计替代"这句话的一半（防护半边）；V2 的全部改动都为了
让另一半（代价半边）变成可预注册、可机器判定、可被红队复核的诚实合同。**

---

## 1. 价值主张的重铸：从 regret 到"敢不敢放大"

**裁定**：(a) 评测层升为**双一等指标**：`final_regret`（收敛）×`decision_risk`（结论可信），
预注册配对判定，不合成单标量作判据；(b) 内核输出重定义为**带证据档案的推荐**，以新事件
kind `recommendation` 落账；(c) 规划器目标函数**不改**——它已经在优化 trust-adjusted 目标
（风险贴现、隔离、复测都是在花 regret 买可信度），改的是把这笔账**记在明处**。

**论证**。stats_tests.csv 的结构是决定性的：os 在签名匹配的演示场景买到 regret 优势，在其余
结构性场景稳定支付 1–2 个百分点 regret；而污染率（0.004 vs 0.146）与假最优命中（0.20 vs 1.00）
的优势跨场景稳定。这说明系统实际提供的商品是**保险**：保费=regret 劣化，保额=避免把伪影
最优放大到生产的灾难损失。用 regret 单指标给保险产品打分，等于宣称保费是纯损失——R1-1 的
"H1 判定被替换"正是这个错位的症状：主张者被迫用唯一 regret 占优的场景（S0.demo）顶替预注册集。
正确的可证伪合同是：**os 以 ≤X%（预注册）的 regret 保费，换取 decision_risk 的显著下降**——
X 超支即败，风险不显著降也败。两个失败方向都留着，才叫主张。

为什么不在 kernel 造标量 trust-adjusted regret：λ（灾难损失/保费汇率）是域与用户的风险偏好，
不是内核事实；把它烧进内核就把一个诚实的二维权衡假装成一维排序，且 λ 可被事后调到"os 赢"——
恰是 R1 类协议漂移的新入口。λ 扫描曲线只进报告的敏感性一节。

为什么不是第三个持久对象：推荐档案是**派生态**（可从 events+observations 重放重建，
CONTROLLER_MODEL 不变量①），按同一纪律它必须是事件负载+报告产物，不是持久真相源。两对象
不变量不动。

**架构改动清单**：
- 新事件 kind `recommendation`（testing，入 EVENT_SCHEMA §1）：
  `{round_id, params, predicted, support:{n_trusted_obs, region_suspicion_mean, sentinel_ok,
  mechanisms_active:list[str]}, grade:enum{A,B,C}}`——grade 判档规则由协议定义（§4），
  机器可算、不含 truth。
- `expos/eval/scoring.py` 增 `decision_risk` 度量：真值代理 = wrong_optimum 指示 ×
  污染利用率（truth 侧事后），生产态代理 = grade（校准见 §3）。
- 协议（§4）预注册双指标判定：`H1' = decision_risk(os) 显著 < robust ∧ regret 保费 ≤ X%`。

**迁移路径**：依赖 R1 修复线先落（f\* 全局化、有效训练集口径、robust 按规格重跑），否则双指标
的数字仍是脏的。之后：EVENT_SCHEMA 登记 → loop 尾轮发射 recommendation → 协议 v2 预注册 X
→ 补跑 S3/S4 后按新合同重判 H1'。旧 报告不回改，标注"V1 口径"。

---

## 2. 机制激活的可审计性：存在 ≠ 生效

**裁定**：是。每个决策机制**强制发射因果生效证据**，以内核级注册表托底，三级守门入 CI。

> **R2 裁定收编（答问 1：活性断言下沉内核，分级硬失败）** + **审查方认领验收**：F 路变异实验是最硬论据（变异 E 断开生产接线仍存活 63 测试——测试层守不住接线层），故 (a) `mechanism_effect` 按本节落**内核层**，每轮每机制 O(1) 发射；(b) **硬失败只留给"结构性空转"**——配置声明启用而对象未接线/事件缺席（EXP011 红），第 1 级（注册缺席）才硬崩；(c) "效应恒等"（fired=true 但 magnitude 恒等）**不硬崩**，发黄牌由 sweep 级活性断言收口（干净轮折扣≈1 是合法态，硬崩会误报）。**验收负样本**：F-1/F-2/F-3 三变异脚本（`scratchpad/mut/`：risk_map=None / risk_discount 首行 raise / soft `_weight`≡1）收编为常驻语料，**新守门必须全部击杀才算落地**；`exploration_quota` 生产切片当前无测试消费（F-8），可作第一个接入样板。

**论证**。R1-2 的三个空转（键名失配→折扣恒 1、None 桶→风险图恒常数、ewma/cusum 零调用方）
共享同一结构：测试断言了机制的**名字**（generator 字符串、函数存在），没断言机制的**效应差分**。
这是"无静默回退"不变量的盲区——回退有 except 分支可 grep，空转没有：它是正常路径上的恒等
变换。架构级解药只能是把"生效"本身变成一等事件流：机制不发声=违规，发声 fired=false=诚实
未激活，发声 fired=true 必附效应摘要。这样"活性"成为与 regret 同级的可聚合观测量，R1-2 类
问题从"六路深审才挖到"降为"一条 SQL 就红"。

**词表与守门设计**：
- **注册表** `expos/kernel/mechanisms.py`：机制名枚举（首批：`risk_discount` /
  `risk_map_placement` / `drift_ewma` / `drift_cusum` / `median_aggregation` /
  `replicate_variance_alpha` / `soft_trust_reweight` / `proposal_adjudication` /
  `sentinel_band` / `exploration_quota`）。注册项 = {名字, 属主策略, 应激活场景谓词引用}。
- **新事件 kind `mechanism_effect`**（testing，入 EVENT_SCHEMA；必填字段进 ABI）：
  `{mechanism:str(注册名), round_id:int, fired:bool, n_affected:int,
  magnitude:{min,max,mean}}`——如 risk_discount 发射 factor 分布摘要，`fired=true` 要求
  `n_affected>0 ∧ magnitude 非恒等`（factor≠1）；证据明细走非 ABI 自由字段。
  > **协定细化见 EVENT_SCHEMA.md §6「机制活性事件族协定」**（event-model + OTel semconv 定向走读裁定）：统一字段词表（OTel 命名律 + counter/gauge 语义选型）、
  > descriptor 裁定（不引入 per-run descriptor 事件，§1 ABI 注册表已充当之）、体量纪律（`entries` 逐观测非逐格；实测单 os-soft run 该族≈翻倍日志→改 OTel exemplar 摘要+有界样本）、新机制准入模板。
  > 现有 `risk_map_applied`/`aggregation_alpha` 标注「将迁移至本协定」（→`mechanism.risk_map_placement`/`mechanism.soft_trust_reweight`）。
- **三级守门**：
  1. **单元差分测试**（机制注册准入条件）：构造触发场景，断言机制开/关两跑产物**逐位不同**
     （R1-2 的验证方案即此形态的实例）。无差分测试的机制不得注册。
  2. **run 级完备性**（expos-lint 新规则 EXP011 + loop 断言）：本 mode 注册机制集合 ==
     本轮发射集合；注册而缺席 = 红。事件量代价 ~10 条/轮，可忽略。
  3. **sweep 级活性断言**（协议执行器，§4）：`protocol.yaml` 的 `mechanism_activation`
     映射声明"机制×应激活场景集"；聚合时算活性矩阵（fired 率、magnitude 分布），应激活
     格活性为零 → 判定产物标红、CI 失败。反向也查：声明不激活的场景里高活性 → 黄牌（标定漂移）。
- **与现有制度衔接**：EVENT_SCHEMA 走既有 testing→stable 准入（§2）；EXP011 循 lint 规则
  ID 化三分级成例；差分测试归入测试六层的"合成场景"层。

**迁移路径**：注册表+事件先落（机制逐个接入，接入即补差分测试——恰好覆盖 R1-2 修复的验证）；
EXP011 先 preview 灰度再转强制；协议 v2 落地后开启 sweep 活性断言。第三方插件经 Plugin API
增必需回调（插件机制不发声不得加载——与"插件不绕信任路由"同构的准入闸）。

**活性守门运营配方（研究定标）**。把"一次性杀变异"升级为常驻制度，照 mutmut/cosmic-ray/
chaostoolkit 与蜕变测试文献定标（详 `scratchpad/research_mutation_ci.md`）：
- **常驻语料**：清单化存 `tests/mutants/`（进版控，非 scratchpad），每条 = {id, 目标机制注册名,
  断线变异(返回 None/raise/权重≡1), 期望被谁杀, `equivalent?`}。种子 = R2 的 E/D/F-3；每个新机制
  注册强制配一条断线变异（同上文差分测试准入）。断线型算子超出 cosmic-ray 通用算子集，用手写清单。
- **CI 三档**：①每 PR 秒级——环路活性断言 + EXP011（不跑变异）；②每夜分钟级——全量跑
  `tests/mutants/`（O(机制数) 条），survival>0 报警；③发版前小时级（可选）——`kernel/`+`policies/`
  全算子 mutmut（覆盖率驱动最小化）+ 分数门。仿 cosmic-ray `cr-filter-git` 支持"仅 diff 变异"作 PR 增量档。
- **与 expos-lint 分工**：静态可判归 lint（未发射=EXP011、关键出参禁 None 字面量=拟增 EXP013）；
  正常路径上的恒等/短路变换（变异 D/F-3）静态测不出，只能靠动态变异/环路断言。
- **等价 & 容差纪律**：活性断言改"逐位不同"为**容差外统计显著**（`|magnitude-1|>ε` + 分布差异，
  呼应统计蜕变测试与 HPC 误差范数阈值）；等价/数值等价变异一次判定后清单化豁免，CI 不复判。

**失活预算熔断参数（`expos/eval/activity_budget.py`；红队 FB3 收编，取代拍定）**。sweep 级活性
断言里"长期黄牌掩盖真空转"的逃逸阀是 **sweep 级事后门**（非在线 kill-switch），判据 = 去抖长度
`k` 的**连续-k 游程**：连续 `k` 轮非 active 即红，容忍 ≤`k−1` 轮合法静默（已证与 k-in-w 滑窗在
`period=intensity` 时逐位等价，10⁵ 随机序列零失配）。原 `(intensity=3, period=5)` "借自 VS Code
CrashTracker 3 次/5 分钟"是**拍定，已否决**——F3 重放实证：即便最高结构伪影档，单轮合法 warning
概率 p_w 仍达 0.58–0.82 且 active 轮从不连续，故 (3,5) 对**每个** should-activate 档位合法格红牌
命中 100%（180/180 误红）。
- **scope（场景族×机制准入）**：`risk_map` 是**空间**避让机制——`_EXPECT_BY_MODE` 的 mode→{机制}
  一刀切改为 `(场景族×机制)` 白名单；只有高信号空间边缘伪影族、合法静默罕见（标定 `P(w|w)<~0.40`）
  的档位入 should-activate。`batch_shift`/低中档的静默是**正确行为**不判红；`a_max≥0.45` 时 R=8 无
  可行 `k`（`wide_edge.40` 等）显式排除；`soft_trust_reweight` p_w 未标定，暂不入（须单独标定 `a_max`）。
- **k 不拍定，反解**：给定轮数 `R`、should-activate 格数 `N`、族误报目标 `α=0.05`，用
  `q_target = 1−(1−α)^(1/N)`（Šidák）反解满足族误报的最小 `k*`，2 态马尔可夫解析 DP
  `P_FP(a,b,k,R)`（`a=max P(w|w)`、`b=P(w|a)=1` 保守；独立性经验失败故用马尔可夫，解析 DP 对
  MC 10⁵ 误差 <0.002）。**R=8 现值**（`a_max=0.28`、`N=40` 纯净集）：`k*=7`（保守）/ 经验 `k=6`
  （SET A 族误报=0）；对照 `a=0.35→k*=8`、`a=0.45→无可行 k`。换战役自动重算（`derive_k(R,N,α,a_max)`）。
- **目标重述**：`{族误报≤5%, 检出延迟≤3}` 在此信号上**联合不可行**（合法 3 连 warning 极常见）——
  不再承诺任何检出延迟上界；死机制检出发生在第 `k` 轮（游程走满），仅此。

---

## 3. 信任的类型学：三层不是冗余，是三权分立

**裁定**：**保持枚举内核**：TrustLevel/Routing 两枚举不动，连续性住聚合层（现状架构确认）。
补两个非破坏升级：suspicion 获得**校准契约**；`routing` 事件（已含 confidence float）按既有
流程晋级 stable，使连续证据在审计面一等可见。

**论证（为什么这在本质上站得住，而不是"改不动"）**。三层表示对应三个**语义上不同**的对象：
- `suspicion`：**证据强度**——认识论量，天然连续；
- `trust`：**判决**——决策论量，回答"本系统此刻是否采信"，天然离散（采信没有 0.7 档：
  训练集要么含这条观测的原值，要么不含）；
- `routing`：**处置**——操作量，离散动作集（复测/隔离/入模）本身不可连续化。
这是"证据→判决→处置"的三权分立，法庭同构：证据链连续、判决有罪/无罪离散、量刑从离散菜单选。
把三者折叠成一个连续量不是简化，是取消判决环节——而**判决环节正是全部审计不变量的锚点**：
"SUSPECT 结构上进不了响应模型"之所以能做成类型级守卫（fit 拒非 TRUSTED）、改判之所以能做成
有限状态迁移链、提案-裁定配对之所以机器可查，全因状态有限可枚举。连续信任下这些全部退化为
数值约定（权重是否"足够小"），而 R1 的实证教训恰恰是：**数值级失效（折扣恒 1）比类型级失效
安静得多**。若内核信任是连续权重，R1-2 类空转将系统性地更难被发现——类型学倒退。

格（lattice）方案的否定：我们的四值没有信息序——SUSPECT 不是"次级 TRUSTED"而是异质判决，
join(SUSPECT, FAILED) 无操作语义；引入格代数买不到任何一条新守卫。带证据的概率（如主观逻辑
(b,d,u) 三元组）认识论上诚实，但它只是把 suspicion 换了套参数化，判决/处置仍需离散化——
它属于**证据层的改进候选**（backlog），不构成对内核类型的修订理由。

连续性的正确住所已经存在且经了一次实战：per-point alpha 通道 + SoftTrustAggregation
（os-soft 臂）。其 M9 结果"方向对、幅度不足"暴露的是 **w(s) 权重函数无标定依据**——s 本身
不是校准概率，线性斜坡纯属拍脑袋。这是证据层缺陷，不是类型层缺陷。

**改动内核两对象的代价（若走连续内核，评估后否决）**：TrustLevel/Routing 是
ObservationObject ABI 字段 + `routing`/`reclassification` 两个 stable 候选事件的枚举全集
+ 裁决表/守门测试/lint/UI 着色的整个审计面；迁移=重建约百余处守卫断言与全部历史 run 的读取
兼容层，而换来的行为空间 os-soft 已在聚合层零内核改动地覆盖。成本全责、收益零增。

**架构改动清单**（保持枚举前提下）：
- **suspicion 校准契约**：协议层新增产物 `calibration_curve.csv`（suspicion 分位 × truth 侧
  实际伪影率，按场景族分层）；SoftTrustAggregation 的 w(s) 改为消费校准映射而非裸线性斜坡。
  **R2 审查意见收编（状态标记）**：w(s) 的理论形状用 X4 的 **P2 命题 tempered 形式
  `w*(s) ∝ σ²/(σ² + n_S·b̂²)`** 替代线性斜坡（os-soft v2 就有推导而非拍定）；校准曲线按
  "suspicion 分位 × truth 伪影率"实测绘制——SBB 分是零假设后验**下界**，不可当校准概率直读。
- `routing` 事件按 EVENT_SCHEMA §2 晋级 stable（confidence 字段随行）。
- 文档层：ARCHITECTURE 公理 2 增一句显式裁定"信任=枚举判决+校准置信，连续降权是聚合层特权"。

**迁移路径**：校准曲线只需 truth sidecar 事后join，零内核改动，可与 R1 修复线并行；w(s)
换校准映射走 os-soft 臂重跑（SOFT_TRUST_PROPOSAL §8 判据不变，KILL 条款保留）。

---

## 4. 评测协议作为代码：把预注册从纸上搬进机器

**裁定**：是。协议成为可执行制品 `protocol.yaml`，聚合器降级为解释器，协议哈希进 manifest。

> **R2 审查意见收编（状态标记）**：审查方裁定反问 2——**fn 按名引用锁不住函数体，必须把 fn 所在文件 sha 纳入 `protocol_sha256` 闭包**；重构频繁期采 `protocol_sha256 = schema_sha + fn_files_sha` **双列**，报告引用时只冻结后者。`unrun_is_fail: true` 获审查方点赞。M9v1 追认转录时，H-4/L-9 的"实跑矩阵与预注册漂移"清单直接作 UNRUN/DEVIATION 初始种子。

**论证**。R1-1/R1-3/R1-4 是同一个病的四个症状：协议（M9_PROTOCOL.md）与实现（aggregate.py）
之间没有机器契约——A/B 分离写在 §2 没人执行、robust 规格写在 §39-48 场景 yaml 全违反、
置换检验写在 §7 零实现、S4 写在 §221 没人发现没跑。纸面预注册防不了漂移，因为漂移不需要改纸。
唯一结构解是让判定路径**只能**通过协议制品：场景/种子/臂规格/判定函数全部声明在一个被哈希、
被冻结、被 manifest 引用的文件里，评测器拒绝解释协议之外的任何格子，报告里的每个"过/不过"
都必须引用机器判定产物。这与 RUN_MANIFEST 的"输入闭包哈希"哲学同源——协议就是评测的输入闭包。

**最小 schema 草案**（`expos/protocol/protocol_v2.yaml`）：

```yaml
protocol_version: 1
protocol_id: M9v2
frozen_at: "2026-07-XX"        # 冻结后任何修改 = 新 protocol_id + supersedes + deviation 记录
supersedes: M9v1               # 血缘；M9v1 = 对 M9_PROTOCOL.md 的追认转录（含其全部未兑现项）
scenario_sets:
  calibration: {seed_set: A, seeds: "0..9",     scenarios: [S2.edge.0.15, ...]}
  evaluation:  {seed_set: B, seeds: "1000..1019", scenarios: [S0.demo, S1.zero, S2.*, S3.*, S4.*]}
arms: [naive, robust, os, os-soft, rcgp]
arm_specs:                     # 对照臂规格冻结（R1-3c 教训：口头规格必被退化实现背叛）
  robust: {replicates: 3, aggregation: median_huber, huber_delta_mad: 1.345}
metrics:
  - {name: final_regret, fn: "expos.eval.scoring:final_regret"}      # 按名引用，禁闭包
  - {name: decision_risk, fn: "expos.eval.scoring:decision_risk"}
  - {name: contamination_effective, fn: "expos.eval.scoring:contamination_effective"}
hypotheses:
  - id: H1p
    scenario_set: evaluation
    scenarios: ["S2.edge_evaporation.>=0.2", "S2.batch_shift.<=-0.18", "S4.*"]
    claims:
      - {metric: decision_risk, comparison: os_vs_robust, direction: less}
      - {metric: final_regret,  comparison: os_vs_robust, budget: "+0.10 relative"}  # 保费上限，预注册
    test: {kind: paired_permutation, alpha: 0.05, n_min: 20, correction: holm, ci: bca_bootstrap}
mechanism_activation:          # §2 衔接：活性断言的期望映射
  risk_discount:      {expected_active_in: ["S2.edge.*", "S0.demo"]}
  drift_ewma:         {expected_active_in: ["S2.instrument_drift.*"]}
unrun_is_fail: true            # 声明了没跑 = 该假设判 UNRUN（红），杜绝 R1-4 沉默缺口
```

**架构改动清单**：
- 新包 `expos/protocol/`：schema 校验装载器 + 执行器（吸收 `runs/full_sweep/_tools/aggregate.py`
  ——见 §5，它住在未测试目录正是漂移温床）+ 判定产物 `hypotheses_verdicts.json`
  （每假设：PASS/FAIL/UNRUN + p/效应量/CI + protocol_sha256）。
- 执行器纪律：按 `seed_set` 过滤（evaluation 只吃 B）；臂规格与 run config 逐字段对账，
  不符即该格 taint；未在协议内的场景/种子拒绝聚合。
- RUN_MANIFEST 增 `protocol_sha256`（沿 §2 闭包罗列成例；R2 裁定：闭包含 `schema_sha + fn_files_sha`，判定函数所在文件 sha 一并冻结）；
- expos-lint 新规则 EXP012：文档中的假设判定句（"H1 过"类）必须携带对
  `hypotheses_verdicts.json` 的引用锚，无锚即红——门面与机器判定强制同源。

**迁移路径**：先转录 M9_PROTOCOL.md → `M9v1.yaml`（如实含未跑项，UNRUN 全部显形——这一步
本身就是 R1-4 的修复验证）；R1 修复线的统计件（配对置换/BCa/Holm）作为执行器的 test 后端；
补跑 S3/S4 后以 M9v2 冻结、重判、报告引用机器产物。

---

## 5. 不可替代内核：五件套保留，增补一件，边界重画

**裁定**：明天把 GP/BO、QC 检查、归因引擎、agent 后端、模拟器全部换掉，必须留下的是——
1. **两持久对象 + 追加式事件日志**（desired/observed 二分 + 不可变过去）；
2. **观测默认待裁决 + 裁决/处置分离 + 结构性路由**（非 TRUSTED 的原值进不了响应模型是类型
   事实而非纪律）；
3. **提案-裁定配对**（任何无裁决权行动者的影响在日志上机器可查）；
4. **truth 隔离**（主张可被定量证伪的前提）；
5. **无静默回退**；
6. **【增补】机制活性证据**（§2）：任何决策机制必须发射生效证据——没有它，前五条守住的只是
   "系统不会做坏事"，守不住"系统真的在做它声称的好事"。R1-2 证明第六条缺席时前五条可以
   全绿地空转。
候选五件套**无一砍除**：R1 的"未攻破清单"（truth 隔离 14 路、agent 无裁决权、数字抄写零错误）
恰好证明这五件是全系统唯一没被攻破的部分——不可替代内核的实证定义。

**模块边界重画**（post-M10）。现状病灶：决策策略散于 `qc/policy.py`、`planner/`、
`agent/policy.py`、models 工厂，而评测器 `aggregate.py` 竟住在 `runs/full_sweep/_tools/`
——包外、无测试、无 lint 管辖，R1-3 四刀全落在这个治外法权区。重画为四域：

```
expos/kernel/     objects, store, lifecycle + mechanisms.py(§2 注册表)   ——不可替代内核
expos/policies/   五注入点全部策略实现（verdict/aggregation/planner/agent/model_factory）
expos/evidence/   checks, attribution, failure_model ——证据生产者，无裁决权、无路由权
expos/protocol/   协议装载/执行/评分/统计（§4，收编 aggregate.py 入测试与 lint 管辖）
```
边界含义：`evidence/` 产 suspicion 与假设，`policies/` 做判决与处置，`kernel/` 持真相与
守卫，`protocol/` 在 truth 侧裁判全体——四域间依赖单向（evidence→kernel、policies→kernel、
protocol 只读 runs/）。"换域=换 YAML+adapter 零内核改动"主张不变，且新增可检验推论：
"换掉整个 evidence/ 或 policies/ 任一策略，kernel 与 protocol 零改动"。

**迁移路径**：纯移动+import 重导出（兼容垫片一个 minor），无行为改动；先收编 `protocol/`
（价值最高、风险最低），`policies/` 合并随机制注册表接入顺路做。
**R2 审查意见收编（状态标记）**：`protocol/` 包**必须 M12 就位**（它承载协议即代码，不可缓）；但
`policies/`/`evidence/` 大搬家建议放 **M13 重扫之后、M15 之前**——纯移动虽无行为改动，仍会使
resweep 期间的 hotfix 双线冲突（唯一时机分歧，其余边界重画审查方支持）。

---

## 6. 硬不变量修订：第六条"无静默回退"→"无静默回退、无静默空转"

**修订内容**：原第六条只禁"不可行时静默兜底"（异常路径）；修订后同时禁"正常路径上的恒等
空转"——每个注册决策机制每轮必须发射 `mechanism_effect(fired∈{true,false})`，注册而不发射
= lint 红（EXP011），应激活场景 sweep 活性为零 = CI 红。

**为什么必须修**：R1-2 证明原第六条存在语义漏洞——折扣恒 1、风险图恒常数都不走任何回退分支，
在原不变量下完全合规却使 M7 整层失效。空转是回退的对偶：回退是"坏路径装好"，空转是"好路径
装在"。只禁一半，另一半必然成为下一轮红队的收获场。

**代价（如实列）**：① 事件量增 ~10 条/轮（约 +15%，append-only 单写者下无性能问题）；
② 每个存量机制需一次性接入注册表并补差分测试（约 10 个机制，正好与 R1-2 修复验证合并做）；
③ 插件作者负担上升（机制回调成为准入条件）——这是有意的：不能证明自己生效的插件机制本就
不该上信任路由；④ `mechanism_effect` 词表进 ABI 后受 EVENT_SCHEMA 冻结纪律约束，机制改名
成本变高——用注册表别名字段吸收。其余五条不变量原文保持，本提案各节均以其为前提。

---

## 7. 给红队的问题（希望对面压测的点）

> **R2 审查意见收编（状态标记）**：审查方已**接单反问 1/3/4/5 为 R3 审查点**（假活性攻击、grade 校准崩坏、保费上限可证伪、分层信任反例）。反问 2（协议哈希闭包）已裁定并落 §4（fn 文件 sha 入闭包）。反问 4 先给方向：X 由 S1.zero QC 税上界 + P4 截断正态解析上界推导，不自由预注册。

1. **假活性攻击**：构造一个机制，能通过 §2 的三级守门（差分测试、完备性、sweep 活性断言）
   却在决策上仍然无效——比如 fired=true、magnitude 非恒等、但效应被下游归一化吞掉。活性断言
   的判据强度需要多少才封死这类"表演性生效"？
2. **协议哈希的边界**：§4 判定函数按名引用（`fn: "module:func"`），协议哈希锁不住函数体——
   改 scoring 实现不改协议即可漂移。是否必须把 fn 的代码对象哈希（或其所在文件 sha）纳入
   `protocol_sha256` 的闭包？代价是每次无害重构都换协议指纹——请裁定这个权衡。
3. **无 truth 的 grade 校准**：§1 推荐档案的 grade 在生产（无 truth sidecar）下只能靠 §3 的
   suspicion 校准曲线外推。请在 S3 留出伪影上压测：校准曲线在未见伪影类型下以何种方式崩坏，
   grade=A 的推荐里混入多少假最优？这决定"结论认证机"主张在真实台面上的成色。
4. **保费上限 X 的活动门风险**：§4 预注册 regret budget（草案 +10% 相对）。请对抗性论证：
   X 的任何取值是否都可被事后辩解为"合理保费"，从而使双指标合同退化为不可证伪？若是，判定
   是否应改为"X 由 S1.zero 的 QC 税上界推导"而非自由预注册？
5. **分层信任的不可弥合差**：§3 裁定"枚举判决+聚合层软化"与"内核连续信任"行为等价域足够大。
   请构造反例场景：某种伪影分布下，任何 (枚举裁决, 聚合权重) 组合都无法逼近连续信任内核的
   最优行为，且差距实质影响 decision_risk——存在即推翻 §3 的架构裁定。

---

## 8. v1.1 蓝图（用户裁决收编）

> **来源与保真度**：本节收编 `mailbox/red_to_blue/020`（用户对整体架构的正式裁决，红队转达）
> + `mailbox/red_to_blue/021`（红队三条实现护栏）。原文要点全部保留；一处边界：**§6 调度层里
> ssh 后端部分不予形式化**（Slurm 故障期的临时授权通道，不进架构）。护栏以内嵌「⚠ 护栏」标注落地，
> 防实现走样。本节是 **v1.1 蓝图底稿**，与本文 §1–§7（v1 提案）为"演化而非推翻"关系（见 §8.8）。

### 8.1 核心裁决：四层拆分（Policy Layer，v1.1 头条）

现在 `QC → hard route → model` 太挤，必须升级为四个独立层：
**QC Evidence → Trust State → Learning Policy → Certification Policy**。
同一笔 observation 可以：不适合当干净 truth、但仍可 soft weight 学习、同时作 failure evidence、
最后在 paper claim 里标 qualified evidence——四层各自定位，互不折叠。

| 档 | Trust State | Learning Policy（α，⚠意图示例非规格） | Certification Policy |
|---|---|---|---|
| **TRUSTED** | 采信 | α=1.0 | clean evidence |
| **SUSPECT** | 存疑 | α=0.2–0.7 | qualified |
| **QUARANTINE** | 隔离 | response 模型小/零权、failure 模型全权 | 不支持 clean claim |
| **FAILED** | 弃置 | α=0 | 仅 failure evidence |

> **⚠ 护栏 1（021 §1）——上表 α 分档是意图示例、不是规格**。CAL3 已实测：线性斜坡在高污染带
> **系统性过度保留**（RMSE 0.577 vs tempered 0.032）；PREM 已构造 w_min 恒正下限 × 大偏差中等
> suspicion = 软严格劣于硬的反例区。故 **Learning Policy 的权重必须是校准驱动的 tempered 形
> `w*(s) ∝ σ²/(σ² + n_S·b̂²)`**；固定 α 带只可作 **fallback** 并显式标注适用边界（`b²≲τ²`）。
> **Certification Policy 必须带"大偏差批次不得停留 QUARANTINE 带"的校准验收项**（PREM §B.3）——
> 否则 Policy Layer 会把 PREM 构造的失效模式制度化。

两句原话保留：**os-soft 从此不再只是 arm，而是核心 architecture**；`H1_REJECTED_os_worse`
**由此不是失败，而是架构进化的证据**。

### 8.2 该层的理论与实证地基（全部现成）

这不是空中楼阁——四层拆分的病根诊断与理论/实证支撑均已在途产出：

- **病根**：Q3/HY 那条 **`trust_confidence` 双语义冲突**——一个字段同时背"人工置信"与"学习权重"
  两层语义，正是四层必须拆开的架构级病根（指针：`docs/STRESS_TEST_R3.md`、
  `docs/SOFT_TRUST_PROPOSAL.md`、`mailbox/red_to_blue/004_att3-attribution-findings.md`）。
- **相变地基**：PREM 的 **b²≈τ² 相变**（软信任何时劣于硬隔离的解析边界）——
  `docs/THEORY_P3.md`、`docs/STRESS_TEST_R3.md`、`mailbox/red_to_blue/011_th3-theory-review.md`。
- **权重形状地基**：CAL3 的 **tempered 拟合**（线性斜坡 vs tempered 的校准 RMSE 对比）——
  `docs/STRESS_TEST_R3.md`、`mailbox/red_to_blue/021_ruling-guardrails.md`。
- **条件性价值地基**：EVAL3 的 **条件性价值**（软信任的收益只在特定污染带兑现）——
  `docs/STRESS_TEST_R3.md`、`mailbox/red_to_blue/003_agg3-h1-sensitivity.md`。

即"病根 + 相变 + 校准形状 + 条件性价值"四块地基全部现成，Policy Layer 是把它们制度化，不是新研究。

### 8.3 R3-B 前立刻要有（P0，五条，照录 + 在途归属）

| # | P0 条目（照录裁决） | 在途归属 |
|---|---|---|
| 1 | **Materialized View Fault Isolation**：坏 obs→quarantine 不得全 run DoS；score.json 缺→stale/incomplete 警示不装正常；training_members.json 缺→lineage incomplete；model snapshot 坏→model unavailable 不碍 raw event replay；index 坏→rebuildable cache 非真相源。补一层 view health scan → partial view / quarantine / rebuild。**"这个洞不补，别人会说这不是 OS 只是一组脚本。"** | **视图隔离**：IDX3 + OS3 合并路已在做（裁决扩了 scope）；见 `mailbox/red_to_blue/012_idx3-index-scaling.md`、`016_os3-kernel-audit.md` |
| 2 | **Claim Compiler / Claim Ledger**：输入 protocol hash/manifest/stats/cells/run ids/code fingerprint/generation label/deviations → 输出 ClaimDecision（supported/rejected/partially_supported/invalid_probe/superseded/**stale**）。防旧 report 讲旧 claim、重跑后主表不同步、p 值无 script、Gen-1/2 混用。 | **Claim Compiler**：新路已派；headline_stats.json 是第一颗种子。**⚠ 护栏 2（021 §2）：ledger 必须是从产物 pull 计算、不是第二本手维护台账**——ClaimDecision 由 compiler 从 artifact 指纹**重算**，散文只转引 ledger、CI 校验一致性（否则它就是第二个会漂移的 CHECKPOINTS）。承接本文 §1 recommendation dossier + §4 `hypotheses_verdicts.json`。 |
| 3 | P0 batch 修后**聚合与代际标记** | **代际标记**：聚合批（ROADMAP M13 全量重扫）；裁决确认已在计划 |
| 4 | **UI coverage/staleness 警示** | **UI 警示**：UI3 I-1 分面修复已修（`mailbox/red_to_blue/017_ui3-dashboard-audit.md`），staleness 面待并入 |
| 5 | **fresh-clone E2E gate** | **E2E 门禁**：`preflight_e2e.sh` 在建，裁决确认 |

### 8.4 v1.1（P1，五条，照录 + 与既有文档的关系）

> 不要现在做 v2，会炸 scope。

1. **Policy Layer**（§8.1 四层拆分，v1.1 头条）。**与本文 §3 的关系**：§3 保枚举内核 + 聚合层校准，
   Policy Layer 把"聚合层软化"显式升格为独立的 Learning/Certification 两层——见 §8.8 衔接。
2. **Protocol-as-Code Compiler**：`protocol.yaml → ProtocolCompiler → cells.tsv → manifest →
   campaign → aggregation spec`；含 scenario/arm registry、seed policy、artifact taxonomy、
   metric definitions、invalid-probe rule、配对规则、generation label、expected outputs——
   不再靠人脑记"这批是 Gen 几 / drift 是不是 invalid probe / glare 算不算 capability test"。
   **与既有 §4 的关系**：§4 的 `protocol_v2.yaml`（可执行协议制品 + fn 文件 sha 入哈希闭包）是本条的
   评测子集；v1.1 把它前展到 cells/campaign 生成侧，聚合器降级为解释器的裁定不变。
3. **Adapter/Driver ABI**：`execute/capabilities/health_check/dry_run/calibrate/estimate_runtime/
   estimate_cost/failure_modes`；红线=adapter **不回传 trust、不写 model、不读 claim、truth sidecar
   隔离、failure 转 structured error**。**与既有 ADAPTER_ACTIONS.md 的关系**：ADAPTER_ACTIONS 的六态
   长任务动作机是本 ABI 的 `execute` 生命周期细化，红线与本文六不变量同源。
   **⚠ 护栏（021 顺注）：`estimate_cost`/`estimate_runtime` 对纯模拟域可标 optional**（v1.1 实现成本低、
   价值在真设备侧兑现）。
4. **Resource Scheduler/Quota**：ExecutionBackend 抽象 local/slurm/dry-run（**ssh 不形式化**）；
   quota / 并发 / retry ladder / node health / disk waterline / job manifest / stale process cleanup
   → `expos campaign run`。**⚠ 护栏 3（021 §3）：ExecutionBackend 是"单 campaign 的执行后端抽象"，
   ≠ 多仪器资源仲裁**——ROADMAP FR-1"多仪器资源仲裁=正确排除"仍有效，两者不同物、兼容；
   落地时须在 **CONTROLLER_MODEL.md 写清这个区分**，防 scope 从后端抽象滑向多仪器调度。
5. **Observability/Trace Layer**：event（科学状态变更）/ metric（run 级数值健康）/ trace
   （round 级 span：design→execute→ingest→qc→adjudicate→update→plan→checkpoint）三类分开。
   **与既有 CONTROLLER_MODEL / EVENT_SCHEMA 的关系**：event 类已由现有事件词表覆盖；metric/trace 为增补。
   **⚠ 护栏（021 顺注）：trace 层 P2 定位正确**——grade / 机制事件已覆盖核心，**勿提前抢 P0/P1 资源**。

### 8.5 v2（明确推迟，照录）

真实湿实验设备 adapter、插件市场、常驻服务、分布式库、交互式 agent 规划环境。

### 8.6 "只能各选一个"裁决句（照录）

> 最该**新加**：**Policy Layer**；最该**修**：**OS Data Plane / Materialized Views**。

### 8.7 边界忠实记录

**ssh 后端不予形式化**：§8.4 第 4 条 ExecutionBackend 只抽象 local/slurm/dry-run。ssh 后端是
Slurm 故障期的临时授权通道，属运维应急，不进架构，不写 ABI、不进 protocol 闭包。

### 8.8 与本文 §3 信任类型学裁定的关系：演化而非推翻

v1.1 的四层拆分**不否定** §3，而是 §3 三权分立在策略面的展开。对照如下，防读者误读为翻案：

- §3 的 **`trust` 枚举判决**（TrustLevel/Routing 两枚举、类型级守卫、有限状态迁移）→ 原样保留为
  v1.1 的 **Trust State 层**。§3 关于"连续信任内核会使 R1-2 类空转更难发现、类型学倒退"的裁定不变。
- §3 的 **suspicion 校准契约 + 聚合层 per-point α（SoftTrustAggregation）** → v1.1 的
  **Learning Policy 层**承接。§3 已把 w(s) 从裸线性斜坡改为 tempered `w*(s) ∝ σ²/(σ²+n_S·b̂²)`；
  v1.1 只是把这条"聚合层特权"从 §3 的一句话升格为一个命名层，并加上 α 分档表（护栏 1 约束其规格）。
  即 **SoftTrust 语义的提升**，而非新增语义。
- §3 隐含但未命名的 **claim 准入面**（"H1 过只允许引用机器判定产物"、`hypotheses_verdicts.json`）→
  v1.1 显式命名为 **Certification Policy 层**，即 Claim Ledger 的准入面（§8.3 第 2 条）。

一句话：**§3 是"证据→判决→处置"三权分立的内核裁定；v1.1 四层是同一裁定在"判决(Trust State) /
学习(Learning Policy) / 认证(Certification Policy)"三个策略出口上的命名展开**。枚举内核、truth 隔离、
六不变量全部照旧——四层拆的是策略面，不是内核类型。

### 8.9 完整架构图（14 层）——文字版占位，待原图收编

裁决原文称"完整架构图（14 层）在用户裁决原文"，但转达信 `020` 未含该图，红队信内亦不可及。
故此处按 **P0 / v1.1 / v2 三段**自绘文字版层次图占位，**原图可及后以其为准替换**：

```
【P0 数据平面加固（R3-B 前）】
  L1  Raw Event Log（append-only，唯一真相源）
  L2  Materialized View Health Scan → partial view / quarantine / rebuild   [P0-1]
  L3  Claim Ledger（pull 计算：artifact 指纹 → ClaimDecision，含 stale）      [P0-2]
  L4  Aggregation + Generation Label（代际标记）                             [P0-3]
  L5  UI Coverage / Staleness 警示                                          [P0-4]
  L6  fresh-clone E2E Gate（preflight_e2e.sh）                              [P0-5]

【v1.1 策略与执行（P1）】
  L7  QC Evidence         ┐
  L8  Trust State         ├─ Policy Layer 四层拆分（§8.1）                   [v1.1-1]
  L9  Learning Policy     │   （tempered w*，护栏 1）
  L10 Certification Policy┘
  L11 Protocol-as-Code Compiler（protocol.yaml → cells → campaign）         [v1.1-2]
  L12 Adapter/Driver ABI（execute/capabilities/…；红线：不回传 trust）       [v1.1-3]
  L13 Resource Scheduler / ExecutionBackend（local/slurm/dry-run；ssh 不形式化）[v1.1-4]
  L14 Observability：event / metric / trace 三分（trace=P2）                [v1.1-5]

【v2 推迟】真实湿实验设备 adapter · 插件市场 · 常驻服务 · 分布式库 · 交互式 agent 规划环境
```

> 注：以上 14 层为**待原图收编**的文字版重建，层序按"数据平面 → 策略 → 执行 → 观测"自下而上组织，
> 编号仅为占位定位，不代表用户原图的精确层名/层序。原图收编后本 §8.9 整体替换。
