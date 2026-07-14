# ROADMAP V2 —— 下一纪元路线图（提案）

> **状态：提案，2026-07-11。** 本文是 `docs/BUILD_PLAN.md`（M0–M10，已全部关账）的续篇：回答"接下来什么值得做、什么顺序、什么坚决不做"。架构本质重审见并行提案 `docs/ARCHITECTURE_V2_PROPOSAL.md`（另一会话产出，两文分工：它管结构、本文管时序与取舍）。
> 输入：CHECKPOINTS 收官条目、`docs/STRESS_TEST_R1.md`（6 P0/P1 主轴 + 15 P2 + 14 P3）、REFERENCE_MAP §20–§22 处置清单、SOFT_TRUST_PROPOSAL、frontier_D 校准笔记。
> 纪律不变：每里程碑关账落 CHECKPOINTS 条目；未过验收不进下一里程碑；压测方（另一会话）复验拥有 P0/P1 的闭环裁定权。

## 0. 总取舍（北极星）

R1 之后，本项目最有力的资产**不再是**"os 全面赢"的原三主张，而是一个诚实得多的结果：**os 在多数结构性场景 regret 劣于 robust，但在污染防护 / 假最优拒斥 / 可审计性上碾压**——负结果 + 机理（M7 机制曾空转、硬隔离失覆盖）+ 解耦度量（regret vs 可信性），恰是 TMLR / NeurIPS E&D 轨制度化欢迎的形态（frontier_D Q3）。因此：**论文纪元是唯一优先级**，修复-重评纪元是它的前置，平台纪元只保留直接服务论文的两项，硬件纪元门槛制不排期。一切"让系统更大"的诱惑（插件生态、血缘导出、服务化、第三域）两年内不碰。

## 1. 纪元划分与并行关系

| 纪元 | 里程碑 | 与谁并行 |
|---|---|---|
| E1 修复-重评 | M11 机制修复 → M12 评测协议重建 → M13 场景补全+全量重扫 | 压测 R2 与 M11/M12 交错（修一批→复验一批）；os-soft v2 设计（frontier_D Q1）与 M11/M12 并行起草，M13 落地 |
| E2 论文 | M14 主张重写 → M15 初稿+内部终审 → M16 投稿包 | 压测 R3（论文数字终审）= M15 的验收方；ARCHITECTURE_V2 采纳项若改内核，必须在 M13 重扫**之前**合入（否则数字作废重跑） |
| 用户裁决 P0 五条映射（`ARCHITECTURE_V2_PROPOSAL §8.3`；R3-B 前立刻要有，横切 E1/E2） | ①视图容错=IDX3+OS3 合并路（扩 scope）→M11/M12；②Claim Compiler/Ledger（pull 计算非手维护）新路已派，headline_stats.json 为种子→并 M12 协议件；③聚合与代际标记→M13 全量重扫；④UI coverage/staleness 警示（UI3 I-1 已修+staleness 待并）→打扫批；⑤fresh-clone E2E gate（preflight_e2e.sh 在建）→M11 关账前 | 与 M11/M12 交错，P0 阻塞 R3-B；护栏见 `mailbox/red_to_blue/021` |
| E3 平台（最小） | 无独立里程碑——仅两项被吸收：duckdb 报告平面→M12、软信任在线校准→M13 | 其余全缓/砍（§4、§6） |
| E4 真实硬件 | M17（条件触发，不排期） | 门槛清单见 §7，五门全过才立项 |

**关键排序依据（来自 R1 修复优先级节）**：M11 会改变 M12/M13 的数字（M7 机制修好后 os 臂 regret 变化），M13 的数字决定 M14 的主张——**严禁先改门面**。

## 2. 里程碑总表

| # | 里程碑 | 交付物 | 验收标准（可执行验证；一律要"效果证据"，测试绿不算修好——STRESS_TEST_LOOP 规则 6） |
|---|---|---|---|
| M11 | 机制修复（"失效但无人知"清零） | R1-2 三合一：`summary()["p_global"]` 响亮读取、`p_artifact_optimistic` 的 `solution_batch=None` 边际分支（或 planner 改用 `risk_map`）、drift EWMA/CUSUM 跨轮接线（裁定：**接线**，因 M13 必扫 drift 场景）；R1-5 三硬伤：torn-tail 与 seq 判据统一、resume 轮级幂等 GC、`snapshot()` 纳入 `kernel_.theta`；P2 批 A（§5）：well_cost 下界、CLI override 消费端接线（硬件纪元也依赖它）、checks 吞错升格、writer.lock、reclassify 守卫、round_band 边际回退等 | ① 非零 FailureModel 下 risk_discount 臂候选评分 ≠ 无折扣臂；② `_plate_risk_map` 边缘孔 > 中心孔；③ drift 场景跨轮触发报警且 S1 不误触；④ torn-tail 崩溃注入后 `read_events` 不抛、seq 连续；⑤ mid-round 中止 resume 后该轮观测数 == 板容量；⑥ one-shot vs split 全轮模型指纹逐位相等；⑦ override 投递后 `status` applied 计数 >0 且落 OVERRIDE 事件；⑧ 压测 R2 对以上逐条复验通过 |
| M12 | 评测协议重建（口径中立化） | R1-3 四刀反向修复：aggregate 按 `seed_set` 拆分、主表/曲线/判据只报 B 集；污染率增"有效训练集"口径（重放各臂 `aggregation.prepare`）双列报告；配对置换检验 + BCa bootstrap CI + Holm 校正入 aggregate，判据输出 p 值与效应量；P2 批 B（§5）：f\* 按场景离线缓存、检出/归因与 truth 逐孔配对、resume 孤儿观测对账、compare QC 税死代码、wrong_optimum 极值校正、H3 量纲布尔判定、归因 FLOOR 作用于 raw score、glare 反驳器方向门、`_board_frame` 改读 obs 字段；**duckdb `eval/query.py`**（§21.2④）替换 compare 三重循环 | ① detection_curve 所有行 n=20 且 seed∈[1000,1020)；② 两副本一污染单测：robust 有效污染率 < naive；③ S1 零伪影臂间置换检验假阳性率 ≈ 名义 α；④ 协议每处修订以 deviation 显式记账（预注册纪律：跑后改判据必须留痕）；⑤ query.py 与旧循环在既有 1450 格上数字逐位一致 |
| M13 | 场景补全 + 全量重扫（论文数据定稿） | 补 M9_PROTOCOL §2 承诺全集：drift/dust 单伪影×幅度档、S3 留出伪影×5、S4 组合×4；robust 臂按冻结规格重跑（replicates=3 + median+Huber IRLS + alpha）；**os-soft v2 臂**：suspicion 门×tempered-BO 残差在线校准两段式 w（frontier_D Q1，arXiv:2601.07094 Eq.10），预注册判据沿 SOFT_TRUST §8 修订并跑前冻结；全网格 Slurm sbatch 重扫重评分 | ① aggregate_summary 场景集 = 协议承诺全集（41 配置量级），H1–H4 全部有数可裁（**无论正负**）；② robust 臂配置与 M9_PROTOCOL §2 冻结规格逐字段一致；③ os-soft v2 至少 edge0.20 档有带 p 值的判定（转正→"负结果诊断+修复验证"叙事；仍不达→纯负结果叙事，两者都可发表）；④ 重评分全程"发现-定性-修复-重评分"留痕 |
| M14 | 主张重写（门面与数字对齐） | R1-1：每处"H1"改写为场景集限定+统计状态（S2 预注册集上 regret 半边不成立如实写，与 regret-污染解耦 finding 合并）；R1-6：1.007 换实测数或越限 seed、README 电梯句改"被隔离观测结构上不可能入模"、headline 逐条加场景范围限定、诚实 finding 收录 batch−0.18 反超；PAPER_OUTLINE v3：主张改为①结构化偏差注入基准（六注入器补全版）②regret-可信性解耦度量与机理③软信任校准的预注册裁决（含 M13 结果） | ① R1-1/R1-6 各自"验证"栏逐条 grep 通过；② 新读者只读 README 能复述"os 多数场景 regret 更差、可信性指标碾压"；③ 压测方 A 路对 README/PAPER_OUTLINE 每个数字 vs report/ 产物逐位对账零 P1 |
| M15 | 论文初稿 + 内部终审 | 正文（TMLR 首选；备选 NeurIPS E&D / Digital Discovery——frontier_D Q3）；新图：(ε,δ) 相变图（Plateau-IMQ L 阈值理论线 + 实证点叠加，frontier_D Q2）、regret-可信性解耦双轴图；预注册协议全文作附录；limitation 节收编 P3 批 C（§5） | ① 压测 R3 = 终审：数字对账 + 主张-证据对赙零 P0/P1；② 每个 claim 句都能指到 report/ 产物或 limitation 声明；③ 预注册判据逐条"过/不过/修订(记 deviation)"三态裁决表入附录 |
| M16 | 投稿包 | REPRODUCE.md 字面可执行修复（默认臂、cells 文件补齐——P3）；rawdata 体积如实标注；代码 tag + 归档 artifact；投稿 | ① 干净环境按 REPRODUCE 字面执行产出主表数字；② 投稿系统提交回执 |
| M17 | 真实硬件（条件触发） | 见 §7 门槛清单，五门全过才立项；首步永远是"只读 ingest"而非闭环控制 | 立项时另写 BENCH_PLAN.md 与验收标准（本文不预支承诺） |

> **R2 裁定收编（答问 2：H4 需真跨族配置）**：`S3.wide_edge`（decay 3）**算"族内泛化"档不算"未见类型"**——同为指数/平滑衰减族，审稿人可说签名库 edge 检查"换个带宽就追上"；且它实测的是"参照组污染下的退化"（L-4：edge 参照组用 d≥1、牵连半径硬编 d≤1），口径须写明。故 **M13 的 H4 保留 wide_edge 但不让它独占**，另加一条**真跨族**配置：`drift×dust` 组合（时间结构×点状，两个当前检出最弱家族）或非单调空间模式（斑块/棋盘之外）。**前置**：真跨族的 drift 分量必须先解决注入器问题（有状态注入器加 `persist: true`，state 跨轮持续），否则组合里 drift 分量恒零、H4 白测。

## 3. 关键取舍裁定（A–E 问题逐条）

- **A 论文纪元：做，最高优先。** 依据：诚实负结果+机理+解耦度量的组合有制度化归宿（TMLR/E&D），且是 R1 后唯一与证据相容的主张。关键路径 = M11→M12→M13→M14→M15→M16（严格串行于数字依赖）；必补实验 = drift/dust/S3/S4、robust n=3 规格臂、os-soft v2 校准臂、置换检验/bootstrap（全在 M12/M13 验收物中）。
- **B 压测循环：做，作为 E1/E2 的验收机制而非独立纪元。** 依据：R1 头号教训是"测试绿≠机制生效"，独立复验是效果证据的唯一来源。R2 绑 M11/M12（复验+钻新表面），R3 绑 M15（数字终审）。批次化处置见 §5；R2/R3 新 finding 默认入对应批次，P0/P1 阻塞当前里程碑关账。
- **C 平台纪元：只保两项，其余缓/砍。** 排序公理"对论文有用 > 对可信性有用 > 对生态有用"的执行结果：duckdb 报告平面（论文用，入 M12）、软信任在线校准（论文核心，入 M13）——做；conditions 病历、血缘三出口、ADD_CONTROLS/NEW_CANDIDATES 装配路径、BenchAdapter 长任务——缓（前两者纯可信性/生态收益且无消费者；装配路径仅当 M13 后审稿人质疑 planner 完整性才升级；长任务归 E4 门槛制）；插件 API 落地——两年内不做（§6）。
- **D 真实硬件：门槛制，不排期。** 依据：硬件的边际成本（安全、标定、维护）只有在"软件主张已发表、人在环通道已真实接线"后才可辩护。门槛见 §7。
- **E 不做清单：见 §6，8 项。**

## 4. P2×15 + P3×14 批次化处置策略

| 批 | 内容 | 处置 | 归属 |
|---|---|---|---|
| 批 A（机制/安全 P2） | well_cost 下界、override 死投递、checks 吞错、E 路六条（writer.lock/obs_id 校验/resume 域哈希/st_mtime_ns/reclassify 守卫/torn-tail 判据）、round_band 空桶 | **批量修**（都是"失效但无人知"或单点小修） | M11 |
| 批 B（评测口径 P2） | f\* 每格不同、检出无配对、归因无配对、resume 孤儿×truth 错配、compare 死代码、wrong_optimum 阈、H3 量纲、归因 FLOOR、glare 反驳器、_board_frame 硬编码 | **批量修**（不修则 M13 数字仍带系统偏向） | M12 |
| 批 C（统计弱门 P3） | subsample 0.5 系数无标定、ΔR² 无 Bonferroni、正态近似小 α 失真、edge 方向硬编码、失效点非单调误导、inconclusive 26–46% 率、KB 过自信 | **定性不修，进论文 limitation/脚注**（修复无实验增益，诚实声明即可） | M15 limitation 节 |
| 批 D（一行改 P3） | REPRODUCE 字面执行、rawdata 体积标注、死导入、FileNotFoundError 包装、0.3/0.6 边界钉死测试、soft_trust"连续衔接"措辞、rebuild×supersedes 声明 | **顺手修**（合计 <1 天） | M14/M16 打扫时 |

## 5. 依赖图（文字版）

```
M11 机制修复 ──(数字会变)──► M12 协议重建 ──(口径定)──► M13 全量重扫 ──(数据定稿)──► M14 主张重写 ──► M15 初稿 ──► M16 投稿
   ▲                          ▲                                                              ▲
   └── 压测 R2 复验（交错） ────┘                os-soft v2 设计（并行起草，M13 落地）          └── 压测 R3 终审
ARCHITECTURE_V2 采纳项：若动内核 ⇒ 必须 ≤M12 合入（M13 重扫前）；否则顺延至 M16 后
M17 硬件：等待 §7 五门（其中门1 依赖 M11 的 override 接线、门2 依赖 M16）——与 E2 无资源竞争前提下 G3/G4 可提前勘察
```

## 6. 明确不做清单（两年内；每项一句为什么）

1. **插件 entry_points 生态落地**（PLUGIN_API_DRAFT）——零第三方用户时建市场是"生态先于用户"的倒置；草案封存，等第二个真实外部使用方出现再议。
2. **LLMBackend 实智能 agent**——三主张没有一条依赖 agent 智能，LLM 非确定性破坏同种子可复现与对照公平；TemplateBackend 已满足"提案-裁决边界"论证需要。
3. **血缘三出口 / OpenLineage 导出器**——无外部血缘消费者，导出器是摆设；events.jsonl 本身已是完备真相源，映射表（RUN_MANIFEST 附录 A）留档即可。
4. **BoTorch/GPyTorch 迁移与 MC-qEI**——重依赖换边际增益；KB 过自信有解析折中（§21.2⑨），sklearn GP 足够支撑全部论文实验。
5. **第三应用域**——"换域零内核改动"已由 crystal/coating 双域证明，第三域是重复证明、纯成本。
6. **HTTP 服务化 / Dash 迁移 / 多用户**——单写者是内核公理（不变量⑩），四条迁移触发条件（§18.1 族7）一条不满足。
7. **Parquet 数据仓库**——duckdb 直查 jsonl 万级行 0.35s 已实测（§21.1 族6），建仓是过度工程。
8. **提前采购/接线真实仪器**——§7 门槛未过之前，硬件是资金+安全+注意力三重歧路；ADAPTER_ACTIONS 设计稿的价值恰在"接线前想清楚"，不在"赶紧接"。

## 7. 真实硬件门槛清单（M17 立项前置条件，五门全过）

- **G1 人在环通道真实可用**：CLI override 消费端接线并有端到端测试（现为 R1 P2 死投递——硬件安全依赖此通道，M11 修）。
- **G2 论文已投稿**（M16 关账）：避免双线作战稀释唯一优先级。
- **G3 六态动作机以 sim-shim 先行落地**：ADAPTER_ACTIONS §5 的"阻塞 execute = 单 goal 退化"包装 + cancel→FAILED(reason) 路径过崩溃注入 CI——真仪器接入时协议已是回归测试覆盖的旧代码。
- **G4 目标仪器满足最小安全面**：safety_class ≤S1、有物理 E-stop 与人守、支持"只读 ingest 先行"（bench_manual CSV 路径已存在）——路径永远是 只读 ingest → 半自动（worklist 出、人执行）→ 闭环。
- **G5 真实标定协议先于闭环**：哨兵/副本的 MSA %GRR 三档验收（§15）在该仪器上跑通并落 domains/*.yaml，QC 阈值用真仪器数据重定标（A/B 分离纪律照搬）。

## 8. 范围与决策纪律（沿 BUILD_PLAN 续用）

- 修复顺序红线：**机制(M11) → 口径(M12) → 数字(M13) → 门面(M14)**，逆序即作弊（R1 整合报告结论）。
- 所有重扫/重评分走 Slurm sbatch + 确定性命名幂等格子；协议任何跑后修订必须记 deviation。
- 压测方对 P0/P1 拥有闭环裁定权；"测试绿"不构成关账证据，必须附实跑效果证据。
- 本文与 ARCHITECTURE_V2_PROPOSAL 冲突时：结构问题以彼为准、时序取舍以本文为准；两者对同一项的"做/不做"分歧提交用户裁决。
