# CONTROLLER_MODEL.md — expos 控制器模型（desired/observed·reconcile·durable replay）

> 状态：**设计笔记 v1**（2026-07-10）。第十轮 REFERENCE_MAP §19「合成建议(K8s+Temporal+
> systemd)」的具体化落地：把 K8s 的 spec/status·level-triggered reconcile 与 Temporal 的
> event-sourcing·replay-determinism 映射到 expos 的 checkpoint+events.jsonl，并给出未来
> BenchAdapter 长任务的 goal/feedback/result 语义草案。**不引入新特性、不改代码**——本文
> 只把既有 resume/checkpoint 语义提炼为一组可审计不变量。权威蓝图以 ARCHITECTURE.md 为准，
> 治理红线以 OS_PRINCIPLES §12 为准，冲突时以后两者胜。

---

## 0. 三个类比来源（学概念，不抄 source）

| 外部系统 | 借的概念 | expos 对应物 |
|---|---|---|
| Kubernetes controller | desired(spec) vs observed(status)、level-triggered reconcile、幂等 | 计划的 ExperimentObject vs 落账 ObservationObject 集；resume 按全量状态重建 |
| Temporal durable execution | event history 为真相源、replay 重建状态、determinism 纪律 | events.jsonl（有序日志）+ checkpoint.json（物化快照）；derive_seed 决定论 |
| systemd / PG-WAL | 三态健康、WAL 先行、catalog 版本拒载 | 「先事件后检查点」写序、resume 配置指纹校验、checkpoint_version 治理 |

关键界定：**expos 不是非终止控制循环**（K8s thermostat 是常驻 watch 循环）；expos 是有限回合制、
单写者、顺序驱动。故借的**不是「常驻协调」而是「level-triggered 幂等」**：派生态永远从当前全量
observation store 重算而非续消费增量、重做一轮必须幂等。reconcile 只在 **resume 边界**发生一次，
其余时段 loop 是确定性顺序驱动器（非 controller 竞技场）。

参考源路径（源码级走读锚点）：
- K8s 控制器模式：docs.k8s.io/concepts/architecture/controller（level-triggered reconcile；docs 级）
- Temporal durable execution：docs.temporal.io/workflows（event history replay + determinism；docs 级）
- plumpy「按名存引用、不存闭包」：`references/plumpy/src/plumpy/process_states.py:326`（Waiting 只存回调 `__name__`）、`persistence.py:533-537`（method 统一按名存）、`processes.py:650-660`（runtime-only 对象 load 时新建）
- optuna op-code journal + replay：`references/optuna/optuna/storages/journal/_storage.py:414-439`（`apply_logs` reducer）、`:107-112`（snapshot+尾部重放）、`_file.py:83-101`（torn-tail 容错）
- bluesky checkpoint/rewind：`references/bluesky/src/bluesky/run_engine.py:369-382`（`_UNCACHEABLE_COMMANDS` 白名单）、`:2437-2467`（checkpoint=重放缓存分段，拒在 bundle 中打点）

---

## 1. 状态二分：desired vs observed（K8s spec/status）

| K8s | expos | 载体（源） |
|---|---|---|
| `spec`（desired state） | **计划态 ExperimentObject**：design_space / candidates / layout / budget / execution_req | `expos/kernel/objects.py:ExperimentObject` |
| `status`（observed state） | **落账 ObservationObject 集** + `exp.status` 生命周期字段 | `objects.py:ObservationObject`、`ExpStatus` |
| controller 写 status | `exp.status` 只许 `lifecycle.advance_status` 迁移；观测 trust/routing 只许 `adjudicate` 纯函数 | `expos/kernel/lifecycle.py`、`loop.py:331-339` |
| 派生视图（cache） | model snapshot / aggregation α / best-so-far / QCHistory | `models/snapshot_r*.json`（可重算，非真相源） |

**不变量①**：内核持久 desired-state 载体唯一是 ExperimentObject，observed-state 载体唯一是
ObservationObject 集加 `exp.status`。派生态（模型快照、聚合 α、最优、QC 历史）**永远可从 observation
store + events.jsonl 重放重建，从不作为唯一真相源持久化**（loop.py:300-307：读 TRUSTED→prepare→fit）。

**conditions 病历式补充（controller-runtime 走读；post-M10 设计，只设计不实现）**：`exp.status` 是**线性**
生命周期枚举（DESIGNED→…→CLOSED）。K8s `metav1.Condition` 数组
（`{Type, Status∈{True,False,Unknown}, ObservedGeneration, LastTransitionTime, Reason(机器 CamelCase 枚举),
Message(人类自由文本)}`）给出**正交、可共存**的「病历式」状态轴——可为 exp 加一个 `conditions[]`
记录**非主线**状态（QC_DEGRADED / RESUME_HEALED / ARTIFACT_SUSPECTED），每条带独立 `last_transition`
与 `observed_round`（对标 ObservedGeneration=该条件算于哪轮观测），**不扰主状态机、不新增持久真相源**
（conditions 是 observed-status 的补充记录、可从事件重建）。纪律照抄：Reason 机器可读 / Message 人类自由文本
（与 EVENT_SCHEMA「message 永不入 ABI」同构），LastTransitionTime 仅状态翻转时更新（level 观测字段上的边沿记录）。

---

## 2. reconcile = resume 的 level-triggered 重建

K8s 控制器「repeatedly observe spec, adjust current state until they match」是 level-triggered：
只看**当前全量状态**，不看状态迁移的边沿。expos 把这条用于 **resume 重建**：

```
run_loop(resume=True):
  ckpt = read_checkpoint()                 # 物化快照：completed_rounds / budget / planner
  校验 domain/mode/seed 指纹一致           # 不一致 → LoopError 响亮失败（loop.py:276-280）
  trusted = list_observations(TRUSTED)     # 读当前全量观测（不是"上次之后的增量"）
  train, α = aggregation.prepare(trusted)  # 经同一聚合策略 → resume 等价性
  model.fit(train, ..., per_point_alpha=α) # 派生态从全量重算
  planner.restore_state(ckpt["planner"])   # 阶段 FSM 状态回灌
```

**不变量②**：resume 按**全量当前观测**重算派生态，绝不「续消费增量」。这保证「分段 resume」
与「一次跑完」在派生态上逐位等价（loop.py:299-307；κ 调度同理绑 campaign 视界，见不变量⑧）。

**不变量③（level 幂等判据）**：一个动作/阶段是否「已消费」，由**产物谓词**判定（该轮 exp 是否
已设计落盘、该 obs 是否已带 routing），**而非事件边沿计数**。edge-triggered「续消费自上次以来
的增量」在崩溃-重放下会漏做或重做；level-triggered「按当前全量收敛」天然幂等。
（REFERENCE_MAP §19.1 K8s 条、§19.2 洞见1；action_consumed 判据应为「是否已产生对应设计」）

**不变量④（重做幂等）**：重做一轮不得产生副作用累积——`save_truth` 按轮**覆盖写**（非 append），
resume 重做某轮不产双份真值（store.py:208-218，Bluesky 走读发现的坑）。

---

## 3. durable execution = checkpoint + events 双载体（Temporal event sourcing）

Temporal：event history 是真相源，replay 重建状态，recorded activity result 复用不重算。
expos 的双载体分工：

| 角色 | expos 载体 | 语义 |
|---|---|---|
| ordered event log（真相源） | `events.jsonl`（seq 单调、append-only、永不覆盖） | 每次迁移/裁决/改判/规划决策；EVENT_SCHEMA.md 词表 |
| materialized snapshot（加速点） | `checkpoint.json`（原子重写） | completed_rounds / budget / planner state |
| replay 重建 | resume 时读 TRUSTED 观测重 fit 模型 | 派生态不持久化为真相，重放得来 |

**不变量⑤（WAL 先行序）**：`write_checkpoint` **先 append `checkpoint` 事件、后原子写
checkpoint.json**；崩溃于两步之间时 checkpoint.json 落后于日志，resume 保守重做该轮（安全方向
偏斜）。这与 PG WAL「日志先落、页后刷」同构（store.py:183-189；REFERENCE_MAP §19.1 PG 条）。

**不变量⑥（事件×产物双向对账）**：checkpoint.json 是物化快照、events.jsonl 是有序日志。resume
的「完成」应由**产物谓词**（文件存在且指纹与事件记载一致）× **事件断言**联合裁定；不一致报
CorruptedRun 而非静默续跑（REFERENCE_MAP §18.2 洞见1；EVENT_SCHEMA `checkpoint` 条语义）。

**不变量⑦（改判即追加，MVCC 版本共存）**：观测改判/翻案只**追加** `reclassification` 事件引用
旧态，永不覆盖历史（PG MVCC / OpenLineage facet 版本化）。事件日志是不可变过去，快照是可重建现在。

**不变量⑦′（checkpoint 极简：只存名字与游标）**：checkpoint.json 只存**可重新解析的名字 +
纯数据游标**（completed_rounds 整数游标、budget、`planner.checkpoint_state()`=阶段名+数据，
loop.py:366），**不存**可重算/可重连的对象（模型权重、QC 历史、闭包）。plumpy 是此模式的范本：
Waiting 状态只存回调 `__name__`，恢复时 `getattr(process, name)` 重绑，runtime-only future/loop
一律 load 时新建（process_states.py:326、persistence.py:533-537）。expos 的「模型/QC 历史从
observation store 重建」= plumpy 的「runtime-only 对象恢复时新建」，二者同构。

**三个 durable-execution 范式的精确借鉴 / 不借鉴**（源码级走读结论）：
- **optuna**：`apply_logs` 单点 reducer（op_code→state，_storage.py:414-439）+ snapshot 每 100 条
  + 尾部重放（:107-112）**直接验证** checkpoint.json + events.jsonl 设计。但 optuna 用**位置游标**
  能成立仅因后端锁串成单一全序；expos 用**显式 seq**（并发 appender 下位置计数会错位，见 §0.1）。
- **bluesky**：checkpoint 是易失内存 + 命令重放（重驱硬件）、不跨进程恢复——**不借**；借两条纪律：
  checkpoint 只落在**原子写单元之外**（run_engine.py:2446 ↔ write_checkpoint 在轮末）、**可/不可重放
  命令白名单**（:369-382 ↔ save_truth 按轮覆盖=可重放、观测追加=幂等）。
- **plumpy**：状态快照 + state 边界幂等重执行、无 events.jsonl——expos 多一层事件重放，吸收其最强
  单点=checkpoint 极简（不变量⑦′）。

---

## 4. determinism 纪律（Temporal replay-safe）

Temporal：workflow 必须确定——同一 history 必产同一决策，故禁裸 time/random/未重放 IO。

**不变量⑧（决定论边界）**：可复现路径**禁裸 `random`/`now`**；一切随机经 `derive_seed(seed, *parts)`
稳定派生（同 (seed,parts) 恒同值，loop.py:76-79）。采集调度 κ 绑 **campaign 视界
`budget.rounds_total`** 而非 CLI `rounds`——否则「分段 resume」与「一次跑完」采集不等价（loop.py:82-90，
M4 压测 finding A）。此即 Temporal「replay 决定性」在科学采样可复现性上的落地。

**不变量⑨（配置指纹拒载）**：resume 时 domain/mode/seed 不匹配即 `LoopError` 响亮失败，绝不按
新配置续旧 run（K8s catalog version / PG catalog 不符拒运行，loop.py:276-280）。未知
`checkpoint_version` 同样拒解析（EVENT_SCHEMA §3）。

---

## 5. 边界与单写者（借来但收紧）

**不变量⑩（单写者）**：一个 run 目录同一时刻仅一个 `RunStore` 写句柄（loop 持有）；agent/UI/
插件**结构性无写句柄**，human override 走 `overrides/pending/` 文件通道零 store 写句柄
（store.py 模块 docstring；OS_PRINCIPLES §6/§13.13）。K8s 允许多控制器并发协调同类对象——**expos
不抄**：回合制单机无需锁竞技场，单写者+纯函数即可（OS_PRINCIPLES §11）。

**不变量⑪（reconcile 不变异 desired-state）**：driver/adapter **不得变异 ExperimentObject**
（测试断言）；reconcile 只读 desired、写 observed 与派生态。truth sidecar 只能由 sim_* 生成，
决策模块（qc/models/planner/agent）永不可读（公理 6）。

**不变量⑫（无静默回退）**：控制器不可行即响亮失败（ExposError 家族：LayoutError/BudgetError/
DomainError/LoopError…），静默兜底视为缺陷（OS_PRINCIPLES §12.6）。

---

## 6. 未来 BenchAdapter 长任务：goal / feedback / result 语义草案（post-M10 设计）

现行 sim adapter 是同步的（`adapter.execute(exp, rng)` 一次返回 raw_results + truth_records）。
真实台面 adapter 是长执行、异步、可失败的。借 **ROS2 action（goal/feedback/result/cancel）+
Temporal activity heartbeat + systemd Restart** 三源，给出未来语义（**此处仅草案，当前不实现**）：

| 阶段 | 语义 | 载体（拟） | 红线 |
|---|---|---|---|
| **goal** | 提交 desired：把 `ExecutionReq`（adapter/params/batches）投给长执行台面 | 现有 ExecutionReq（不新增对象） | goal = ExperimentObject 的 execution_req 快照，不可变 |
| **feedback** | 进度心跳：非终态进度上报（phase/fraction/eta） | **新事件 kind `adapter_progress`（testing）**：payload `{exp_id,round_id,phase,fraction}`，全属**非 ABI**自由字段 | feedback **不得**触碰 trust/routing/status；不是 observation、不是 decision |
| **result** | 终态回灌：raw_results + truth_records 不透明交还 | 走**现有 ingest 路径** `raw_to_observations` + `store.save_truth`（不透明透传） | result 只经既有信任路由入内核，**不得绕过 adjudicate**；truth 仍只写 sidecar |
| **cancel/timeout** | 超时/取消：重试策略在 **adapter 侧**（Temporal 五字段 / systemd Restart+StartLimit），非内核侧 | QueueItem 式 retry_count/max_retries（M7 待补字段）+ 终态事件 | 超时的一轮追加终态事件，resume 时按不变量③④**幂等重做**，不叠加 |

**determinism 边界划分**（Temporal workflow/activity 二分的直接映射）：**adapter = activity**（可非
确定、做 IO、造虚拟/真实世界、生成 truth sidecar，运行一次结果记录）；**loop = workflow**（确定、
可重放，只消费 result、从不自做非确定 IO）。长执行崩溃时：activity 结果若已落账（产物谓词成立）则
replay 复用不重跑，未落账则整轮 level-triggered 重驱——即 §19.1「resume 硬化三件套」在长任务上的展开。

**三态健康入 checkpoint（systemd active/failed/degraded，§19.1）**：长执行 adapter 的 per-round
健康态（executing/failed/degraded）应入 checkpoint，使 resume 能区分「未开始 / 执行中崩溃 /
已降级完成」，配合不变量⑥的产物对账精确定位重驱边界。**post-M10 backlog，非当前里程碑范围。**

**RequeueAfter ↔ 动作重竞（controller-runtime 走读增补）**：controller-runtime `reconcile.Result`
把 `Requeue bool` 标记为 **Deprecated**（源码注释："ratelimiter requeue 造成困惑，应用显式时长/poll 间隔"），
只留 `RequeueAfter time.Duration`——即「按明确时长回来、重读全量状态再决策」。映射到 M7 动作仲裁：延迟/未
物化的动作（如跨轮 REMEASURE 等待批次）**不应盲目 requeue**，而应在**轮边界对全量状态重新仲裁**
（action_skipped 留痕胜过隐式重排，EVENT_SCHEMA 已录）——这是不变量③ level-triggered 在动作层的直接推论
（M7 追认）。`TerminalError`（不重试但仍 log+记 metrics 的第三类错误）= expos ExposError 响亮失败并记账。

**finalizer ↔ CLOSED 收口守卫（controller-runtime 走读增补；post-M10 设计）**：K8s finalizer 把终态删除
**门控于 keyed 幂等清理证毕**、清理失败可挂 condition（不静默删）。映射：exp 迁 CLOSED 前应门控于收口
不变量证毕（truth sidecar 封存 / 全 obs 已路由 / checkpoint×manifest 对账一致），失败则挂 QC_DEGRADED
condition 而非静默 CLOSED——manifest.json 作 commit-marker（§13.13）是其单机轻量同构。deletion-grace 机器不借。

## 7. tombstone 语义（controller-runtime deletionTimestamp+finalizer 走读；R2 后正式化）

R2 J-2 的处置刚落「消费侧跳过留痕」（`policy.py:332 _skip_bad_proposal`：历史坏 accept 在消费期
reject-after-the-fact 降级、发 `action_skipped`、**绝不裸抛打停闭环**）；此处把 tombstone 从雏形升为正式语义。
范本＝controller-runtime 二段删除（`pkg/finalizer/finalizer.go:53-82`）：删对象**先置 `deletionTimestamp` 软标记**、
finalizer 逐个清理证毕才 `RemoveFinalizer`、全摘除后才真消失；清理失败保留 finalizer + 挂 condition，**绝不静默删**。

**不变量⑬（tombstone = 软标记事件，非物理删）**：tombstone 是一条 **append-only** 事件（`action_skipped` 是其
消费侧雏形；正式化建议独立/带标准字段 `{item_uid|obs_id, tombstoned_by, reason, supersedes_seq}`）。它对标
`deletionTimestamp`：被墓碑对象/事件**仍留在日志**，只标注「不再参与派生」——**与 append-only 相容**（只追加、
从不物理删；expos 无 GC，取 AiiDA「只追加」纪律而非 K8s 真删）。「墓碑」即 finalizer 二段之第一段的永久化。

**不变量⑭（消费者统一跳过 = level-triggered 集合差）**：所有派生消费者（模型 fit / arbiter 队列 / 聚合）读取时
先减去 tombstone 集，跳过是**当前全量集合差**而非「续消费时删除」——`policy.py:316-330` 已是范式（消费队列＝
flagged −`action_consumed`−`action_skipped`）。这是不变量③ level-triggered 在「作废项」上的直接推论：每 item 只
留痕一次（`already` 去重），无静默丢弃。

**级联与不借**：OwnerReference `BlockOwnerDeletion` 的级联（`controllerutil.go:70-91`）对应物是 exp→CLOSED 收口
门控（§6 finalizer↔CLOSED 收口守卫）；坏提案墓碑**不级联删其 refs**（单机无 GC，只标消费侧跳过）。deletion-grace
计时 / 真删 GC / 多 finalizer 并发一律**不借**（回合制单写者无需）。

## 8. provenance 图投影（AiiDA 二分图走读；离线审计工具草案，不改内核）

AiiDA provenance 是最成熟范本：**二分**——节点只有 Data（不可变数据）与 Process（Calculation 造数据 / Workflow
转手）两族，边永不连同族；**六边类型**（`links.py:19-27`）核心纪律是 CREATE（计算**产生**新数据）≠ RETURN（工作流
**转手**已有数据）；**不可变一旦存储**（`node.py:512` stored 即 immutable、`process.py:121` sealed 拒加边）——故
provenance 是只追加 DAG，改判不改边、只加节点。

**投影命题**：expos 的 events.jsonl + objects store **可投影出等价二分图**——对象（Exp/Candidate/Obs/model_snapshot）
＝Data 节点，事件（round_designed/routing/attribution/decision/action_consumed/reclassification/model_updated）＝
Process 节点，按固定边表连（`Obs.cand_id`→CREATE、`Candidate.parent_obs_id`→INPUT 血缘、`decision.refs`→INPUT +
acceptance/rejection→CALL 配对、`action_consumed.item_uid`→CALL 跨轮、`reclassification`→追加新版本节点引旧＝MVCC/
不变量⑦）。算法草案与完整边类型表见 `scratchpad/ref_provenance_tombstone.md §B/§C`。

**唯一曾缺的边＝obs→model_updated 训练成员边**（"这个观测进了哪轮响应模型"）——M12 `report/training_members.json`
（`scoring.py:391`，逐轮入模 obs_id 清单）恰补齐，使因果链 **提案→裁定→消费→候选→观测→入模→best-so-far** 全程
可查。审计查询即图可达：`ancestors(obs_id)` ＝该观测完整因果链、`descendants(obs_id)` ＝其下游影响。

**纪律（照抄 AiiDA）**：投影**只读不重算**（真相仍是 events+objects，图是派生视图）；节点/边一旦投影不回改；
reclassification 投成新版本节点 + 引旧、绝不覆盖（不变量⑦ 同构）。二分 + 无环可作自检断言。**离线工具、post-M10
backlog，不改内核。**

## 9. 处置与里程碑归属

| 洞见 | 处置 | 里程碑 |
|---|---|---|
| spec/status 分离、level 幂等、WAL 序、决定论、单写者（不变量①-⑫） | **本文档 = design note，确认既有实现已同构**，无代码改动 | 追认 M5/M6/M7（已关账） |
| 事件×产物双向对账驱动 `expos check`（不变量⑥） | design note（REFERENCE_MAP §18.2 已录） | post-M10 backlog |
| 重放校验（events 重算 vs checkpoint 比对，偏移响亮失败） | backlog（Temporal 条，§19.1） | post-M10 |
| BenchAdapter goal/feedback/result + 三态健康 + `adapter_progress` 事件 | **§6 草案**，待真实台面接线时落地 | post-M10 |
| tombstone 正式语义（软标记事件 + 消费侧集合差跳过，不变量⑬⑭） | **§7 正式化**；`action_skipped` 是消费侧雏形（已实现），标准字段/独立 kind 待接线 | 追认 R2 J-2 处置；字段化 post-M10 |
| 事件日志→provenance 二分图投影（离线审计工具） | **§8 草案 + `scratchpad/ref_provenance_tombstone.md` 算法**；obs→model_updated 边由 M12 training_members.json 补齐 | post-M10 backlog |

不变量①-⑭均有 loop.py/store.py/policy.py 支撑（追认既有实现），§6/§8 明确 post-M10——本文不新增任何当前里程碑内的实现承诺。
