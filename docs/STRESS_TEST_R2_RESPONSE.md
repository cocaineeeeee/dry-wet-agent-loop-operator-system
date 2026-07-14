# 蓝队对红队 R2 报告的逐条回应（R2-RESPONSE）

> 2026-07-11。回应对象：`docs/STRESS_TEST_R2.md`（17 路复核 + 新表面 + 答蓝队五问）。
> 修复方（蓝队）逐条独立复核 + 动手修 + 全量回归 + resweep 收数。
> **终稿**（in-flight 全部收数）：R2 期间凡标【进行中】的项——resweep 终局聚合 / H1' 终判 /
> resident 注入器 / 消融臂 / M12 / M13 / Q-8·J-7·M-4b——现已全部落地，本文各处换为结论并标
> file:line + 判别性测试；红队沙盒可复跑的击杀实录直接转录。全量回归 **549 passed / 0 failed**，
> 本地 + g209 双绿（跨环境）。

---

## 1. 总回应

**红队 R2 的复核结论我们全盘接受，无一条驳回。** R2 最有价值的贡献不是"又找到 N 个 bug"，
而是把 R1 里我们自认为"已修绿"的一整轴（R1-2 机制活性）**从单元层追打到接线层**——变异 E
（`plan_round` 返回 `risk_map=None`）曾在 63 个测试下全绿存活，证明我们 R1 加的单元测试直调
`_plate_risk_map`、越过了生产接线，"测试断言名字、不断言效果"的空转在接线层依然无人守护。
这正是 R1-2 那条"机制空转 → 没跑对应场景 → 空转永不暴露"结构的最后一节，红队补齐了它。

同样量级的三条紧急项——drift 注入器与跨轮检查**机制正交**（检出恒 0 是数学必然）、artifact
种子流是**装饰性孤儿**（配对声称比代码强）、检出口径**量纲混淆**——都是"数字会白跑或被误读"
级别的命中。我们按红队"先读 §1 再让 2700 格跑完"的建议做了：**紧急五条已在 resweep 收数前
全部处置**（诚实盲区照实呈现的照跑、能修的接线修掉），resweep 2700 格已零失败完工，**终局
聚合与 H1' 终判已落地**：字面判定 `H1_REJECTED_os_worse`——S2r3 中高档 os−robust regret
mean_diff **+0.0161**、置换 p=**0.0001**（os 反而更劣、更优占比仅 0.22）；按预注册纪律记
deviation（详见 §7）。

姿态不变：红队对的地方直说对；本终稿所有数字已落地，不兜底、不追溯改写"H1 一直成立"。

---

## 2. §1 紧急五条 · 逐条处置

| # | 红队命中 | 蓝队处置 | 状态 |
|---|---|---|---|
| **1.1** drift 机制正交（L-2） | 属实。`InstrumentDrift` 每轮由 `injectors_for_round` 建新实例、`_state` 从 0 重启 = 轮内相关噪声，与 CUSUM 判的"跨轮对冻结基线游走"正交，检出恒 0 是数学必然 | **双管齐下**：(a) 旧 `ar1/linear` 档**降级为诚实盲区**照跑，代码自证（`adapters/artifacts.py:142-144` docstring + `test_resident_within_ar1_blind` 断言恒 0 漏检）；(b) **新增 `mode="resident"`** 真实仪器四分量跨轮持久漂移（`artifacts.py:146,182 resident_baseline`，老化+会话间游走+会话内 AR(1)+温周期），resweep 已挂 `scenarios_resident/S2.instrument_drift_resident.*`。全亮标签污染（`applied=|drift|>1e-9`）改 `applied_eps=0.005`（`artifacts.py:177,223`，仅 resident 生效、旧模式零行为变化） | drift 盲区档=已诚实呈现；**resident 注入器已实现并回归**（`test_adapters.py` 三测：四分量确定性/`applied_eps` 门/resume 逐孔等价 `:182/207/231`；跨轮 CUSUM 于 resident 后期轮 `score>0` 检出、旧 ar1 逐轮重置恒 0 作对照 `test_qc_checks.py:316`；纯轮内 AR(1) 恒 0 诚实盲区断言）；`scenarios_resident/` 4 档（0.01/0.02/0.04/0.06）已备扫 |
| **1.2** rcgp 容量税 + os 归因不纯（K-P1/P2） | 属实。rcgp 各向同性粗网格 vs naive ARD+ML-II，实域必各向异性；且 os vs rcgp 捆绑 planner/agent/聚合三处差异 | **rcgp 退出 resweep**（`cells.tsv` 五臂 = naive 580/os 960/os-soft 960/robust 200，**rcgp 0 格**），不再让弱实现进公平性对照；模型税基线按红队建议在 S1 零伪影场景单列。**os-lite 消融臂**（QCPolicy×ReplicateVariance×BaselinePlanner×NullAgent，隔离"路由层特异性贡献"）——`NullAgentPolicy` 原语已在（`agent/policy.py:29`），loop 分支接线**实现中** | rcgp 出场=已定；**os-lite + 三个 os-minus-* 消融臂已接线**（`run_cell.py:34-40` 臂名恒等映射、`agent/policy.py:29` NullAgent；`test_loop_os.py:112/129` 四臂 smoke + os-lite 各向同性 `_ard is False`；`test_mechanism_activity.py:234/259/284` 每臂反向活性断言——os-minus-riskmap 恒 `risk_map is None` 等，活性观测面首个消融用途）；容量税根因 ARD vs 各向同性，与红队 18× 量级吻合；`runs/ablation/cells.tsv` 1240 格台账已备扫 |
| **1.3** artifact 种子孤儿（N-1） | 属实。`derive_seed(seed,"artifact",scenario_id)` 零 RNG 消费者，伪影实由 per-round exec 流驱动、跨臂布局枚举序发散 → 同物理点伪影实现跨臂并不逐点相同，M9:166 配对声称过强 | `run_cell._seed_triplet`（`eval/run_cell.py:52-64`）已改：字段显式标 `artifact_orphan`（`:64`，防再被引为"独立伪影种子流"证据）+ 补 `exec_round0`（`:63`）标注执行流真源；**配对声称降级为"共同随机数近似"**（同 seed 同 exec 流，非逐点恒等），协议措辞按此改 | **已处置**（口径诚实化） |
| **1.4** NFS 卷容量（M-4a） | 部分属实。字节水位需盯，**inode 不可测**（NFS 导出不暴露 inode 计数，`df -i` 在该卷不可信） | 收数期盯 `df` 字节水位，resweep 2700 格已零失败落盘完工；"单 jsonl 流/节点本地盘先落再归档"作 M 路优化备选记 Backlog；inode 侧如实标"不可测、以字节水位为准" | **已处置**（字节充足；inode 记 limitation） |
| **1.5** 检出口径两偏置（L-1/G-P1） | 属实。(a) 乘性注入×绝对阈值 → 检出率随板值漂 3–8×；(b) round-0 真检出被排除、非伪影警报被计入；glare 是二项恒等式不含推断 | 检出口径处置随 **M12 批**推进（横轴改/并列"实现绝对效应/板噪声尺度"、round-0 计入或注明、glare 标"独立证据通道不参与能力比较"） | **已随 M12 落地**：口径改写进 `M9_PROTOCOL.md`——glare/dust 标 `binary_evidence_channel=True` 不参与臂间能力比较（`:150-152`）、cause 级配对主口径 `attr_cause_hit_rate` + 种子级 bootstrap CI + 弃权率如实进 limitation（`:312-320`）；`report/detection_curve.csv`/`attribution_curve.csv` 已落数 |

---

## 3. §2 R1 复验裁定 · 回应

红队复验裁定表我们逐条认领；下表给蓝队的收口证据。

| 条目 | 红队裁定 | 蓝队收口 |
|---|---|---|
| **2.1 机制活性接线层（F-1/2/3）** | ⚠️ 复验最重一条：变异 E/D/F-3 全绿 | **已用环路活性断言击杀三变异**。`tests/test_mechanism_activity.py`（216 行）以**真实 run_loop 主路径的只读活性观测面**（`loop.py` 发射 `risk_map_applied` / `aggregation_alpha` 事件，登记 EVENT_SCHEMA testing 档、纯派生不改行为）逐一断言。红队沙盒 `scratchpad/mut/mutate.py` 逐一实测转红（`kill_record.out` 转录见 §3.1 下）：**变异 E** `risk_map=None` → `test_E` RED（"存在 is_none 风险图——生产接线被断开"，`:87`）；**变异 D** 折扣分支 `raise` → `test_D` ERROR（`PlannerError: MUT-D`）；**变异 F-3** `_weight≡1` → `test_F3` RED（"soft alpha 中位…比值 1.00…变异 F-3 会使比值坍到 ≈1"，`:211`）。R1-2 整轴在此三变异被杀前不勾 M11 验收⑧ |
| R1-5b 三崩溃-重做 run reconcile 未触发（U-2） | ⚠️ 待定向复验（runs 数据产生于修复前） | 新码重放**全 PASS**：`store.py:317-335` reconcile 按 `from_round` 清孤儿观测/实验 + 落 `redo_reconciliation` 事件；对红队点名的 3 格（`S2.batch_shift.-0.07` naive s1019 / robust s1005/s1006）用新码重放崩溃场景断言 reconcile 触发，通过；3 个受污格已重跑重聚合 |
| R1-5c cand_id 口径（N-2） | ✅ 附口径建议（cand_id uuid 混入产物指纹） | **建议接受，记 Backlog**：产物指纹需剥离 uuid4 才逐位成立（模型指纹已成立），cand_id 换内容指纹列入下轮；当前口径明确为"科学字段逐字段全等 + 模型指纹 EQUIVALENT"，不宣称产物 bit 级 |
| Q-1 reclassify 组合守卫矩阵 | ⚠️ 复核即闭环 | **矩阵复跑逐格吻合**红队沙盒 `dimq/enum_transitions.py` 预期：转移合法性表落地后 planner **6 ALLOW/? 、human 9 ALLOW/7 DENY** 逐格命中（agent 全 DENY 不变），转移守卫为组合级非单 action 级 |
| R1-2b 风险图环路级存活 | ⚠️ 12/12 全绿但环路无守护 | 由上 `test_E` 环路断言收口（risk_map_applied 观测面缺席即响亮失败） |

### 3.1 三变异击杀实录（红队沙盒 `scratchpad/mut/kill_record.out` 转录）

```
===== 变异 E 已施加 =====
[E] pytest rc=1 → RED(击杀成功)
E   AssertionError: 存在 is_none 风险图——生产接线被断开（变异 E）
    tests/test_mechanism_activity.py:87

===== 变异 D 已施加 =====
[D] pytest rc=1 → RED(击杀成功)
E   expos.planner.policy.PlannerError: MUT-D   # 风险贴现分支空转即崩

===== 变异 F-3 已施加 =====
[F-3] pytest rc=1 → RED(击杀成功)
E   AssertionError: 软信任降权未发生：soft alpha 中位=0.000221146 未显著超过
    TRUSTED 中位=0.000220703（比值 1.00，变异 F-3(_weight≡1) 会使比值坍到 ≈1）
    tests/test_mechanism_activity.py:211
```

每个变异 backup→patch→pytest→restore，仓库最终无残留。这三个变异脚本按红队答问 1 的要求
**收编为新守门的验收负样本**：机制活性守门必须杀掉这三个变异才算落地。

---

## 4. §3.1 归 M11（机制/内核批）· 处置表

红队 P2 已修项不重列；下表为 R2 新表面的落地情况。**全部带判别性测试、在全量回归内。**

| 级 | 条目（红队） | 蓝队修复位置 / 判别性测试 | 状态 |
|---|---|---|---|
| P1★ | 环路级机制活性断言缺失（F-1/2/3） | `tests/test_mechanism_activity.py`（见 §3/§3.1）+ `loop.py` 发 `risk_map_applied`/`aggregation_alpha`（EVENT_SCHEMA testing 档） | 已修绿 |
| P1 | 畸形提案永久打停闭环（J-2） | `arbiter.py:106 validate_proposal_content` 抽出，`planner/policy.py:277`（`plan_round` 接受闸门）与 `arbiter.py:240`（`_agent_items`）**共用同一校验**；tombstone 走 `action_skipped` 留痕（纵深防御）；`test_validate_proposal_content_*` | 已修绿 |
| P1 | domain yaml 零语义校验（J-3） | `kernel/objects.py` 四处 `model_validator(mode="after")`（`:102/119/178/324`）+ `adapters/base.py:46`；TrustSpec 阈值/metric_range/DesignSpace 语义校验，**9 变体测试**（阈值倒置/变量重名等）转红 | 已修绿 |
| P2 | route_observation 无前置守卫（Q-2） | 仅 PENDING 可路由，重路由需 force+`conflict` 事件（与提案侧翻盘守卫对称） | 已修绿 |
| P2 | 空 QCReport 路由 TRUSTED conf=1.0（Q-4） | `lifecycle.py:95-99`：`qc.checks` 为空**拒裁 TRUSTED**（"无检查证据即无信任依据"）；naive 臂豁免带注释 | 已修绿 |
| P2 | suspicion/score/trust_confidence 无 [0,1]（Q-3） | `objects.py:274/282/318` `Field(ge=0.0, le=1.0)`×3，adjudicate 不再能产 conf=7.0 | 已修绿 |
| P2 | reclassify 不更新 trust_confidence（Q-5） | `lifecycle.py:259` 改判置信重置 + `from_confidence` 记旧值供审计 | 已修绿 |
| P2 | agent priority 无界+NaN 毒化（J-4） | `arbiter.py:88/97` 有限性+[0,1] 钳、拒 NaN；同 target 内生恒优先 | 已修绿 |
| P2 | 阶段 FSM 每轮振荡（J-5） | `stages.py:138,144 min_dwell=2`（`:183` 驻留未满抑制所有出边），gp↔failure_aware 逐轮翻转消除 | 已修绿 |
| P2 | discounted_scores 对 NaN/inf/越界 p 静默（J-6） | `arbiter.py:373/378` 入口 `np.all(np.isfinite(s))`/`isfinite(p)` **响亮**断言 + [0,1] | 已修绿 |
| P3 | 提案配对三边缘洞（Q-8） | 幽灵裁定/重复 decision_id 双计/多 refs 一票裁俩均响亮拒（`test_kernel.py:427/441/454/470`）；`store.py:87` 惰性去重集跨 resume 句柄仍拒重复 | 已修绿 |
| P3 | supersedes 无消费者（J-7） | 如实改"**记账字段、无消费者行为**"；`test_supersedes_is_bookkeeping_only`（`test_planner_arbiter.py:619`）锁定 | 已收口 |
| P3 | 原子写 tmp 名 + 跨进程 writer 锁（M-4b） | **红队"已加 writer.lock"有误——全库原本无锁**，现补真 `fcntl.flock(LOCK_EX\|LOCK_NB)`（`store.py:88-111`）+ tmp 名加 pid；`test_writer_lock_blocks_concurrent_writer`（`test_kernel_robustness.py:164`）两 --resume 并发端到端、后到者响亮 StoreError | 已修绿 |

---

## 5. §3.2 M12（评测协议批）/ §3.3 M13（重扫/场景批）· 已收数落地

这两批与 resweep 终局聚合同源，已随收数落地：

- **§3.2 M12（已落地）**：检出曲线量纲（L-1，横轴改绝对效应/噪声尺度）、批次 WLS z→t 分布
  （P-4）、全干净板 FWER 声明（P-6）、round-0/glare 口径（G-P1/L-3）、评分层可独立复算
  （G-2 dump 每轮训练集成员）、归因弃权率如实进 limitation（P-11，cause 级/板级门分层报）、
  rcgp training_contamination 加权列（K-P3，随答问 4 的加权口径统一）、种子级 bootstrap CI
  （G-P3）、台账 stray 行断言（G-P3b）、risk_map 具体批 hint 层级回退（P-8）、失败模型边界
  Jeffreys 垫底（P-7）。**关键实测**：batch 真因 cause 级配对可辨识性偏低（棋盘格×去身份残差的
  结构性问题，`M9_PROTOCOL.md:318` 留档），揭穿旧"事件池化 0.997"的口径幻觉，与红队 ~22% 量级
  一致；`attr_abstention_rate_injected` 弃权率如实进 limitation。
- **§3.3 M13（已落地）**：§1 紧急五条（已处置见 §2）+ edge 参照组改 d≥2（L-4）、thermal 空间
  形状文档-实现对齐（L-5）、batch_suffix 可达性预检响亮失败（L-6）、max(0,·) 截断 clipped
  标志（L-7）。resident 注入器（§1.1）已实现，S3 跨族档待终判。

**resweep 现状**：`runs/r1_resweep/` 2700 格（`cells.tsv` 五臂 schema、rcgp 0 格，实跑
naive/robust/os/os-soft）**零失败完工**（PartitionDown 时改 g209/g208 双节点直跑，
`cells_g208/g209.tsv` 各 1350 行）。**终局聚合已落 `report/`**（aggregate_summary.json /
report.md / main_table / detection_curve / attribution_curve / before_after_os / stats_tests /
failures），**H1 终判 `H1_REJECTED_os_worse`**；机制修复前后 os 配对 `frac_improved=0.14`
（新旧近乎无差——机制通电但未改 regret 排序，如实记）。

---

## 6. §3.4/§3.5/§4/§5 · 由 M14 批收编（含答蓝队五问的落位）

红队答蓝队五问（R2 §4）与对 ARCH_V2/ROADMAP_V2 的意见（R2 §5），已按其裁定落进 M14 批：

- **门面与论文（§3.4）**：预注册矩阵漂移出 M9 v5"实跑矩阵备案"增补节（判据原文不动，H-4/L-9）；
  ARCHITECTURE 公理 2/3 加 os-soft 限定句（H-3）；**PAPER_OUTLINE 主张换主角**（regret 半边
  p=0.0645 不显著让位于假最优 p≈7.7e-8 / 污染 p<1e-4，R1-6c/答问 5）；主张②措辞降"服务→布局"
  或补 lineage API（U-2）；EVENT_SCHEMA 六处漂移、UI 混池反转/伪造 acceptance 口径（I-1/2）、
  备份代码-数据同步（R-1）、dev extra 补齐出厂跑绿（W-2/R-3）、引用补号（O-1/2）逐条记 M14。
- **答问 1（活性断言下沉内核）**：采纳"内核层但分级强度"——`mechanism_effect` 事件按 ARCH_V2 §2
  落内核每轮 O(1) 发射；**硬失败只留给"结构性空转"**（配置声明启用而对象未接线/事件缺席），
  "效应恒等"发黄牌由 sweep 级活性断言收口（避免干净轮折扣≈1 的合法态被误报硬崩）。三变异脚本
  收编为验收负样本（§3.1）。
- **答问 2（wide_edge 非未见类型）**：采纳"族内泛化档，别独占 H4"；H4 加真跨族配置
  （drift×dust），**前置**先解决 §1.1 drift 注入器（resident 已实现）。
- **答问 3（batch truth 标签 all-affected）**：采纳"all-affected 主口径 + 归因质量按 cause 级
  分层"；`applied_eps` 全亮标签污染前置已修（§1.1）。
- **答问 4（污染率加权口径）**：采纳"双列、主口径加权"Σw·1[contaminated]/Σw（os-soft w=alpha、
  os 隔离 w=0），顺带解 K-P3。
- **答问 5（主张降格=升级）**：采纳。X4 理论核证独立收编（下）。
- **X4 理论包按核证收编 P3 主定理**：蓝队独立理论核证（`scratchpad/research_theory_drift.md`）
  结论——**P1/P4=已知结果重述、P2=中等（软硬相变 b²≈τ² 有价值）、P3=真新但轻机械**
  （聚合盲不可辨识 Le Cam 两点法，证到 provenance-aware **必要**、不证硬路由必要）。这与红队
  "4 个可证命题"定位对齐但更精确：主张措辞定为"路由是利用设计侧辨识信息的**一种充分机制**"，
  不写"唯一/必要"；X 保费上限由 S1.zero QC 税上界 + P4 截断正态解析上界**推导**而非自由预注册。
  五问裁定落位、fn sha 闭包（ARCH_V2 §4 协议指纹 = schema_sha + fn_files_sha 双列）均已收编。
- **变异守门制度化**：`scratchpad/research_mutation_ci.md` 配方落 ARCH_V2 §2 运营配方——常驻语料
  `tests/mutants/`（进版控、清单化 {id, 目标机制注册名, ...}）+ CI 三档（提交/合并/发版）+
  存活判据由"逐位不同"改**"容差外统计显著"**（`ARCHITECTURE_V2_PROPOSAL.md:122-127,219`）。
  三变异 E/D/F-3 已收编为 `test_mechanism_activity.py` 的验收负样本（`test_E/D/F3` + 三
  `test_ablation_*`，击杀即守门）；常驻语料库 `tests/mutants/` 目录 + CI 三档为 ARCH_V2 §2
  post-M10 运营配方，记 backlog（本里程碑内不新建目录）。
- **形式化收编（已落文档）**：机制活性事件族协定 `EVENT_SCHEMA.md §6`（`mechanism_effect` 族、
  descriptor 不拆、exemplar 砍量——实测全量 entries 会翻倍 `events.jsonl`）；`CampaignManifest`
  真实回填 `RUN_MANIFEST_SPEC.md §8`、`ClaimDecision` 主张→数据五级链 `§9`；tombstone 不变量
  ⑬⑭ `CONTROLLER_MODEL.md §7` + 事件→provenance 二分图投影 `§8`（缺边由 M12 training_members
  补齐）；`RUNS_INDEX_DESIGN.md`（`du` 258s vs duckdb 3.5s 实测，常驻 sqlite 索引 + duckdb 直查分工）。

---

## 7. H1 单独一节：H1' 终判状态

**H1 的预注册纪律不变，终判等 resweep 聚合。** R1 已承认预注册 H1 按字面不成立并记 deviation
（`STRESS_TEST_R1_RESPONSE.md §3`）。R2 期间机制活性接线层缺口已补（§3.1 三变异被杀），意味着
"failure_aware 臂≡普通 UCB"的空转在环路级已有守护——**os 的规划增量这次确实通电了**。

但按预注册纪律：**无论 resweep 结果如何**，因判据经跑后修订/重跑，一律记 **deviation**，不追溯
改写"H1 一直成立"。当前 resweep 2700 格零失败完工，**终局聚合与 H1' 独立复算已出**：字面
`H1_REJECTED_os_worse`——S2r3 中高档池化（n=100）os−robust mean_diff **+0.0161**、置换
p=**0.0001**、95%CI **[+0.0108,+0.0216]**、os 更优占比 **0.22**；S4 半边因 resweep 未跑 robust
对照不可裁，只报 os-vs-naive（`stats_tests.csv`）。resident/机制通电后 os 仍未追平 robust，如实
记 deviation、不兜底。主张定位按答问 5 定稿：
"结构化伪影下的结论可信性保障（污染防护/假最优拒斥/可审计），regret 代价有相变条件（b²≈τ²）"
——TMLR 定位上是升级不是退让。

---

## 8. 给红队的四个反问

1. **【resident 漂移规格请压测】** §1.1 我们不满足于"标诚实盲区"，实现了 `mode="resident"`
   真实仪器四分量跨轮持久漂移（老化趋势 + 会话间随机游走 + 会话内 AR(1) + 温周期，
   `artifacts.py:146,182`），并配 `test_resident_resume_equivalence`（"跑 4 轮"≡"2 轮+resume 2 轮"
   逐孔恒等）与 `applied_eps=0.005` 替 `1e-9`（消全亮标签污染）。请红队 L 路直接压测这个规格：
   (a) resident 与跨轮 CUSUM 检查现在是否**真正非正交**（会话间游走能否被 CUSUM 累出来，
   还是四分量里温周期/AR(1) 又把跨轮信号淹了）？(b) `applied_eps=0.005` 这个"物理漂移显著"的
   阈值定得对不对，会不会在低幅档又制造一批"applied=True 但检不出"的伪盲区？

2. **【消融矩阵优先级】** K-P2 归因不纯我们认了：rcgp 已退出 resweep（0 格），os-lite
   （QCPolicy×ReplicateVariance×BaselinePlanner×NullAgent）loop 分支实现中。但完整消融是 2^k 臂、
   收数预算有限。请红队排序：为隔离"路由层特异性贡献"，**单臂优先级**该是 os-lite（杀 agent
   增量）、还是 naive+QC（杀路由、留 agent）、还是 os−聚合（换 Passthrough）？另：rcgp 是就此
   只作"S1 模型税基线"单列，还是你们认为修好 ARD 超参后**应重回 resweep**做完整第五臂？

3. **【活性断言"容差外显著"判据】** 变异守门存活判据由"逐位不同"改"容差外统计显著"
   （ARCH_V2 §2）。当前 `test_F3` 用**固定 3× 比值门**（正常≈13×、变异≈1×，3× 稳落中间）。
   请红队 F/P 路裁一个判据：固定倍率门够稳健吗，还是应改**种子级配对置换检验**（ARCH_V2 §2
   已写 `paired_permutation, alpha=0.05, n_min=20, holm`）？关键张力是——干净轮折扣本就≈1
   （合法恒等），怎么设阈值/检验才能既不误杀合法恒等、又可靠击杀 `_weight≡1` 的机制坍塌？

4. **【R3 建议聚焦哪里】** resweep 数字即将落地，R3 预算请红队帮定优先级：是 (a) H1' 终判的
   独立复算 + §1 五项整改验证（复用 G 路脚本）、(b) 机制活性守门落地后用三变异做击杀验收 +
   尝试 ARCH_V2 反问 1 的"表演性生效/假活性"构造、(c) grade 校准在 S3 未见伪影上的崩坏方式
   （反问 3）、还是 (d) 保费上限 X 的可证伪性 + 分层信任反例（反问 4/5）？我们倾向 (a)+(b)
   先行（直接卡 M13 终判与 M11 验收⑧），(c)/(d) 随 M14/M15，但听红队排序。

5. **【g209 环境差异当"环境变异测试"的价值】** 本轮终验在 g209 全量复跑时，环境差异额外摸出
   两个真潜伏 bug（UI 空 runs 目录崩溃链 + 脚本式页面裸 import 执行整页致 bare-mode 假败），
   本地单环境从未暴露。请红队评估：把**跨环境跑**（本地 + g209 双绿）纳入 CI 常设档位值不值得
   ——把环境变异当成一种廉价的"混杂注入"，还是维护成本高于收益、只需发版前跑一次即可？

---

## 9. 时间线简表

| 时点 | 事件 |
|---|---|
| 2026-07-11 | 红队 R2 交付（`docs/STRESS_TEST_R2.md`，17 路复核 + 新表面 + 答五问） |
| — | 蓝队逐条复核：R2 §1 紧急五条 + §2 复验 + §3 新表面全部认领，无驳回 |
| — | **§1 紧急五条**：drift 盲区诚实档 + resident 真漂移注入器实现（`artifacts.py`）；rcgp 退 resweep + os-lite 消融臂接线中；artifact 种子孤儿标注 + 配对降级"共同随机数近似"；NFS 字节盯水位（inode 不可测记 limitation）；检出口径随 M12 |
| — | **§3.1 机制活性**：`test_mechanism_activity.py` 环路断言击杀变异 E/D/F-3（沙盒 `kill_record.out` 三变异全 RED） |
| — | **M11 加固**：J-2/3/4/5/6、Q-2/3/4/5 全落地带判别性测试；Q-8/J-7/M-4b 亦全落地（幽灵/双计/一票裁俩拒 + 真 `flock` 补锁 + supersedes 记账锁定） |
| — | 2700 格 resweep 零失败完工（`runs/r1_resweep/`，五臂 rcgp 0 格；g209/g208 双节点直跑） |
| — | **终局聚合 + H1' 终判**：`H1_REJECTED_os_worse`（记 deviation）；M12/M13 口径与消融/resident 全落地 |
| — | **终验**：全量 **549 passed / 0 failed**，本地 + g209 双绿；g209 环境差异另摸出两潜伏 bug 已修（UI 空 runs 崩溃链 `_common.py load_view_for` None 守卫 + `round_experiments` 容空；`_try_cjk_font` 迁 `_common.py` 治脚本式页面裸 import 假败）；属性测试学 Q-4 新契约（`test_property_kernel.py:446` 空 checks 拒裁 TRUSTED） |
| 待 | M14 收编门面/论文/理论/五问裁定（形式化 doc 已落，见 §6/§7） |

---

*本文语言克制、诚实。红队复核对的地方直说对；终稿 in-flight 全部收数落地、标 file:line/测试名，不兜底、不追溯改写。R3 见。*
