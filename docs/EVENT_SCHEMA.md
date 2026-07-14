# EVENT_SCHEMA.md — events.jsonl 词表注册表（规范草案 v1）

> 状态：**规范草案 v1**（2026-07-10）。本文件是 OS_PRINCIPLES §13.4 "ABI 注册表"的
> **events-vocab 首版实体**：把 `events.jsonl` 结构化词表从散落于代码的字面量收敛为
> 单一可审计契约。所有权：本轮研究只允许创建本文件。
> 权威蓝图见 ARCHITECTURE.md；治理红线见 OS_PRINCIPLES §10 / §13.1 / §13.4 / §13.5。

## 0. 两层协定（tracepoint 裁决，§13.4）

每条事件形如 `{"seq": int, "ts": iso8601, "kind": str, "payload": {...}}`
（seq 单调、ts UTC、append-only、永不覆盖——store.py `append_event`）。

- **ABI 层（进 stable 即冻结）**：`kind` 名 + 下表"必填字段"的**名/类型/枚举全集**。
  监控/UI/CLI/eval 只准依赖这一层；跨版本审计以此对齐。
- **非 ABI 层**：payload 内 `evidence`/自由文本/未登记的附加键。读者**不得硬依赖**，
  可无声增删。自由文本 `message` **永不入协定**（printk 教训，§13.1）。
- CI 守门（§13.4）：实写键集 ⊇ 该 kind 声明的必填键集（少键=违约、多键=非 ABI，放行）。

Stability 四态（照 Linux Documentation/ABI）：`testing`（默认）→ `stable` → `obsolete` → `removed`。
本 v1 十四 kind **全部 testing**；标注 "stable 候选"者已具真实 Consumer + 覆盖测试，
待补 §5 CI 样例后走 testing→stable 晋级。

## 0.1 追加日志的两条鲁棒性约束（optuna JournalStorage + PG-WAL 源码走读增补，2026-07-10）

事件日志是 append-only 单写者，但恢复读取仍须对两类现实容错——二者均有上游源码范本：

1. **`seq` 必须显式、不得退化为位置计数**。optuna JournalStorage 以「已应用记录数」作位置游标
   （`references/optuna/optuna/storages/journal/_storage.py:404-439` 的 `apply_logs`/`log_number_read`）
   能成立，**仅因**其文件/Redis 后端锁把所有 append 串成单一全序。expos 现用显式 `seq`
   （store.py `append_event`）**须保留**：一旦未来允许并发 appender（§13.11 多进程写同 journal），
   位置计数在交错半写下会错位，唯显式 `seq` 可对齐。读者以 `seq > last_seen` 增量追平，不按行号。

2. **读取须容忍尾部半写记录（torn tail）**。单写者崩溃于 append 中途会在 `events.jsonl` 末尾留下
   一行不完整 JSON。optuna 文件后端的做法（`_journal/_file.py:83-101`）：末行 `json.loads` 失败时
   **仅当其后仍出现合法记录才报错**，否则容忍为「崩溃点残留」丢弃；与 PG-WAL「重放到最后一条完整
   记录、截断残缺尾部」同构。当前 `RunStore.read_events` 对每行 `json.loads`，遇 torn tail 会抛
   `JSONDecodeError`——**已知鲁棒性缺口，记为 post-M10 backlog**（本轮不改代码；修法：末行解析失败
   且无后继时按截断处理，配合 §18.2 事件×产物对账裁定 CorruptedRun 而非静默续跑）。

> 控制器语义（desired/observed·level-triggered reconcile·durable replay）见 CONTROLLER_MODEL.md，
> 其不变量⑤⑥（WAL 先行、事件×产物对账）与本节同源。

## 1. Kind 注册表（Since=0.1，checkpoint_version=1）

字段类型：`str`/`int`/`float`/`bool`/`null`；`enum{…}` 给出全集。round_id 均为 `int`。

### status_transition — 实验对象状态机迁移（stable 候选：调度审计基石）
| 字段 | 类型 |
|---|---|
| exp_id | str |
| round_id | int |
| from / to | enum{DESIGNED,EXECUTED,QC_DONE,ROUTED,CLOSED} |
Consumers: UI, eval。语义：一个 ExperimentObject 走过一步合法生命周期迁移。

### routing — 逐观测信任路由判决 **（stable 候选）**
| obs_id | str |
| round_id | int |
| trust | enum{PENDING,TRUSTED,SUSPECT,FAILED} |
| routing | enum{TO_RESPONSE_MODEL,TO_FAILURE_MODEL,QUARANTINE,REMEASURE,REPEAT_CANDIDATE} |
| confidence | float |
Consumers: UI, CLI, eval。语义：`adjudicate` 纯函数对单条观测落裁决 + 处置。

### routing_bulk — naive 臂批量全信路由
| mode | str（"naive"） | n | int | round_id | int\|null |
Consumers: eval。语义：baseline 臂不做逐观测 QC，一次性把整轮标 TRUSTED（对照用）。

### qc_report — 每轮 QC 汇总
| round_id | int | n_trusted / n_suspect / n_failed | int | check_counts | object(str→int，非 ABI 明细) |
Consumers: UI, eval。语义：本轮裁决的信任分布 + 触发的检查计数。

### attribution — 失败归因 + 下轮动作建议（M6）
| obs_id | str | round_id | int | top_cause | str | confidence | float | next_action | str\|null |
Consumers: UI, eval。语义：对 SUSPECT/FAILED 观测给出首因与建议动作（证据非决策，不改 trust）。

### decision — DecisionRecord 载荷 **（stable 候选：配对不变量机器可查）**
| decision_id | str | round_id | int | actor | enum{agent,planner,human} |
| kind | enum{goal_translation,prior_proposal,qc_explanation,attribution_explanation,round_rationale,action_proposal,acceptance,rejection,override} |
| refs | list[str] | content | object(非 ABI) | accepted | bool\|null | validator | str\|null | created_at | str |
Consumers: UI, CLI, eval。语义：提案/裁定/解释/叙述统一载荷；提案须有 acceptance/rejection 配对方可影响设计（§4.5 审计不变量）。

### reclassification — 改判/翻案留痕 **（stable 候选）**
| obs_id | str | round_id | int | from_trust / to_trust | enum(TrustLevel) |
| from_routing | enum(Routing)\|null | to_routing | enum(Routing) | actor | enum{planner,human} | reason | str |
Consumers: UI, eval。语义：仅 planner/human 可发起；追加引用旧状态，永不覆盖，另落一条 OVERRIDE decision。

### reclassification_conflict — 高危翻案强留痕（testing）
| obs_id | str | from_trust / to_trust | enum(TrustLevel) | actor | enum{human} | reason | str |
Consumers: UI, eval。语义：reclassify 中高危翻案（提升信任方向，如 FAILED→TRUSTED，仅 human 可发起、reason 必非空）在 reclassification 之外额外落此事件，供单独检索审计（resolution_conflict 风格）。Since=当前 checkpoint_version。

### resolution_conflict — 提案裁定翻盘
| proposal_id | str | prior_accepted | bool | new_accepted | bool |
Consumers: UI, eval。语义：已裁定提案被 human override 翻转时留痕（§4.5）。

### checkpoint — 恢复点标记 **（stable 候选：resume 索引）**
| round_id | int\|null |
Consumers: CLI(resume), eval。语义：write_checkpoint 先记事件后原子写文件；事件是索引、产物 checkpoint.json 是真相源（§18.2 事件×产物双向对账）。

### resume — 断点续跑起点
| from_round | int |
Consumers: CLI, eval。语义：从已完成轮数续跑。

### round_designed — 本轮设计溯源
| round_id | int | exp_id | str | generator | str | n_candidates | int | wells | int |
Consumers: UI, eval。语义：本轮候选生成器与规模（设计溯源）。

### model_updated — 响应模型快照
| round_id | int | snapshot | object(非 ABI) | n_train | int |
Consumers: UI, eval。语义：本轮重训后的模型快照元数据 + 训练样本数。

### stage_changed — 规划器阶段 FSM 迁移（M7）
| from / to | str(stage 名) | criterion | str(判据名，不含闭包) | round_id | int |
Consumers: UI, eval。语义：阶段机按边序语义（第一条满足的出边胜）迁移。

### action_consumed — 动作仲裁消费（M7）
| item_uid | str | round_id | int | action | str(ActionType) | semantics | str | source | str |
Consumers: UI, eval。语义：仲裁器在预算内选中的建议动作被本轮设计消费。

### run_start — run 级开事件（M10 收尾，event-model run_start 纪律；testing）
| domain | str | mode | str | seed | int | rounds_target | int |
Consumers: UI, eval, 对账工具。语义：全新 run 的 provably-first 标记（resume 不重发——
一个 run 一个 start）；metadata-first：域/臂/种子先于任何数据落账。

### run_stop — run 级收事件（M10 收尾；R5 REF-1 终态枚举扩展；testing）
| exit_status | enum{success,abort,fail} | completed_rounds | int\|null | reason | str?（仅 abort/fail） | n_events_hint | int\|null |
Consumers: UI, eval, 对账工具, scan_view_health（terminal 分区）。语义：provably-last 收口标记。
success=正常收口；abort=用户/系统中止（KeyboardInterrupt/SystemExit）；fail=逻辑失败（其余异常），
两者带 reason 且 completed_rounds=null；**缺席=第四态 crash**（进程死于任何 handler 之前）——
四态从此在事件流层可分。发射自身被守护（best-effort），绝不掩盖原始异常、绝不伪造 success。
续跑延长轮数会追加新 stop（append-only，最后一条为准）。

### action_skipped — 非物化动作掉落留痕（M7 对抗审查修复；testing）
| item_uid | str | action | str(ActionType) | source | str | reason | str |
Consumers: UI（裁决日志页）。语义：装配路径未实现的动作（ADD_CONTROLS/NEW_CANDIDATES）
被排除出仲裁队列——占预算零物化是 bug，掉落必须留痕（每 item_uid 只记一次，无静默丢弃）。

### redo_reconciliation — 崩溃窗口重做轮对账（R1-5(b) 修复；testing）
| from_round | int | n_observations_removed | int | n_experiments_removed | int | exp_ids | list[str] |
Consumers: eval, 对账工具。语义：resume 时 level-triggered 对账——检测到物化视图中存在
round_id ≥ checkpoint.completed_rounds 的孤儿观测/实验（崩溃于"观测落盘后、checkpoint 前"
的残留），删除之并留痕，随后该轮被重做；events.jsonl append-only 不动（旧轮事件痕迹保留
是审计特性，仅物化视图被清）。无孤儿时不落本事件。

### view_quarantine — 物化视图坏文件隔离留痕（OS3 §一 修复；testing）
| n_quarantined | int | files | list[str] | errors | dict[str,str] |
Consumers: eval, 对账工具, UI（运行态告警）。语义：resume/运行期 list_observations 全量扫描
observations/ 时遇无法解析的观测文件（非 UTF-8 / 坏 JSON / 校验失败）——运行期已由
RunStore 故障隔离（隔离该文件 + logging.error 列名，跳过继续，单坏文件不 DoS 全 run）；
loop 首轮前把被隔离集合落本事件 + warning 防静默（坏文件使响应模型少喂样本，须可审计）。
`files` 为被隔离文件绝对路径，`errors` 为路径→错误类别。无坏文件时不落本事件。

### config_drift — 域配置漂移确认续跑（R1 P2 修复；testing）
| stored_fingerprint | str | current_fingerprint | str | acknowledged | bool |
Consumers: eval, 对账工具。语义：resume 时 domain_config 全文指纹与存储不符（阈值/注入器/
预算漂移），操作者显式 `--allow-config-drift` 确认带漂移续跑的留痕；未确认时响亮拒绝、
不落本事件。带本事件的 run 语义不再跨段等价——评测消费者应据此过滤或标记。

### risk_map_applied — 风险图活性观测面（机制活性先行版，ARCH_V2 §2；testing）
| round_id | int | exp_id | str | is_none | bool | n_wells | int | n_distinct | int | min | float\|null | max | float\|null | mean | float\|null | grade | enum{active,warning,absent} |
Consumers: eval, 对账工具, tests（机制活性断言）, eval/activity_budget（失活预算熔断）。语义：
本轮 plan_round 交给 LayoutPlanner 消费的风险图概括（``build_experiment`` 原样转手，无变换）——
``is_none``/``n_distinct`` 使"生产接线断开→None"或"恒常数空转"在环路层显形。``grade`` 是**派生
事实、非裁决**（发射/裁决解耦，k8s results_manager 同构）：is_none→absent、n_distinct≤1→warning、
n_distinct≥2→active（判档纯函数见 loop.py ``_grade_risk_map``；红/黄 CI 判档在消费端）。
纯派生只读量，不参与任何决策；所有 mode 统一发射（无风险图臂 is_none=True→grade=absent）。

### aggregation_alpha — 聚合 per-point alpha 活性观测面（机制活性先行版，ARCH_V2 §2；testing）
| round_id | int | aggregation | str(策略名) | grade | enum{active,warning,absent} | entries | list[{obs_id:str, cand_id:str\|null, alpha:float\|null}] |
Consumers: eval, tests（机制活性断言）, eval/activity_budget（失活预算熔断）。语义：本轮聚合策略
``prepare`` 产出的训练样本 per-point alpha（entries 与训练观测一一对应、同序）。软信任降权
（SoftTrustAggregation）是否真的发生据此可验：合成软副本的 obs_id 与落盘 QUARANTINE 观测同 id
→ 消费者按落盘 trust/routing 交叉分类降权组 vs TRUSTED 组的 alpha。``grade`` 是派生事实（同上
解耦）：无训练样本→absent、无降权条目/alpha=None（干净轮恒等）或降权后比值≈1（F-3 假活性）
→warning、软副本 alpha 中位显著超 TRUSTED 中位→active（判档纯函数 loop.py ``_grade_aggregation``）。
纯派生只读量，不改任何决策；所有 mode 统一发射（passthrough/median alpha=None → entries.alpha 记 null）。

### learning_weight_assigned — 显式学习权重传输面（VNext 批一，Part IV Q2 决议；testing）
| pv | int(=1) | round_id | int | aggregation | str(策略名) | entries | list[{obs_id:str, weight:float, alpha_inflated:float, basis:str, cert_class:str\|null}] |
Consumers: eval, tests（软准入权重断言）。必填键（EVENT_PAYLOAD_REQUIRED）= {round_id, entries}。
语义：软信任臂 ``AggregationPolicy.prepare`` 对 routing==QUARANTINE 观测赋的**学习权重**在此显式落账
——取代已删除的"合成 TRUSTED 副本"暗道（DEEP_REVIEW/Part IV Q2 决议：语义走 facet、传输走显式参数、
暗道删除）。每条 entry 对应一个被软准入的 QUARANTINE 观测：``weight``=信任斜坡 w(s)∈[w_min,1]（s 读自
``obs.qc.suspicion`` 单一真值源，**非** trust_confidence——HY-1 RESCUE 反转修复）、``alpha_inflated``=
alpha_base/w（乘性膨胀降权量）、``basis``=权重来源标识（当前 ``"trust_mapping_v1"``；**可升级为证据
函数**（EVIDENCE_TYPING）而不改 schema——basis 是开放的非破坏演进位）、``cert_class``=**预留位、当前无
消费者**（Certification Policy 层落地前恒 null，勿假消费——C2 教训）。发射纪律：**仅软准入轮发射**
（``last_learning_weights`` 非空时；base 策略从不设该面 → 零 mode 分支）；**resume 重建不重发**
（loop.py resume 位刻意静默，保 I4 续跑等价性）。``pv=1`` 出生即治理（REF-1）。

### knowledge_updated — 编译知识面更新（M16 W6 知识最小面；testing）
| pv | int(=1) | round_id | int | fingerprint | str(sha256 hex) | n_hypotheses | int | n_claims | int |
Consumers: agent（读 KnowledgeView 产 hypothesis/提案）、tests（G1 逐位断言）、``verify_run_chain`` 门 12（读 round_id，缺则退化按 seq 序）。必填键（EVENT_PAYLOAD_REQUIRED）= {round_id, fingerprint, n_hypotheses, n_claims}。
**``round_id`` 为 Phase 4 追加键，write-strict / read-tolerant**（store.ADDITIVE_SINCE）：新发射必带（emit_knowledge_updated 强制 round_id 入参 + 去重键），但 Phase 4 之前签署的历史 run 日志（runs/corun_*、runs/llm_smoke_stage3）合法缺此键、READ 侧校验仍须放行——历史日志是不可变证据，schema 追加绝不追溯性作废（append-only-evidence 纪律）。
语义：``kernel.knowledge.compile_knowledge`` 把 claim ledger + HypothesisObject 编译成冻结 KnowledgeView 后，
在此落一条知识更新留痕。``fingerprint``=sha256(canonical(pv+逐 hypothesis 的 stored/effective 状态+其证据 claim
状态))——**G1 机器基础**：同输入→逐位同 fingerprint（冻结知识→第二轮提案逐位相同）；翻转一条被引 claim
（supported→rejected）→受影响 hypothesis 的 effective 状态改 + fingerprint 变（注入反向 claim 可预期改变提案）。
``n_hypotheses``/``n_claims``=计数元数据，**不入 fingerprint**（未被任何 hypothesis 引用的 claim 不扰动编译知识）。
发射点由 W7/W9 loop 接线（本批只提供 ``emit_knowledge_updated`` 辅助函数，emission POINT 不在内核硬接）。
``pv=1`` 出生即治理（REF-1）。

### promotion_decision — Dry→Wet 晋升门决策（M16 W7 晋升策略；testing）
| pv | int(=1) | round_id | int | knowledge_fingerprint | str(sha256 hex) | policy | str(策略名) | promoted | list[{cand_id:str, basis:{convergence:float, window:float, acquisition_rank:float, risk:float}, wet_cost:{n_transfers:int, duration_s:float}}] | denied | list[{cand_id:str, basis:{…同上…}, deny_reason:str, wet_cost:{…}}] |
Consumers: eval、tests（G1 晋升逐位断言 + 平局决定论）、activity/监控面（合法安静 vs 失活辨识）。必填键（EVENT_PAYLOAD_REQUIRED）= {round_id, knowledge_fingerprint, promoted, denied}。
语义：``planner.promotion.decide`` 把 dry_view（逐候选 converged/in_window/acquisition/wet_cost）+ risk_map + knowledge_fingerprint + budget
经**四通道合取门**（convergence ∧ window ∧ acquisition_rank(top-k) ∧ risk，**不加权标量**——折叠标量再判必判反，M-basis 判别测试钉死）编译成晋升集，
在此落一条决策留痕。``promoted[]``/``denied[]`` 逐候选带 ``basis``（四通道值逐位存，审计可重建）；``denied`` 另带 ``deny_reason`` ∈
{gate_convergence, gate_window, gate_rank, gate_risk, budget_truncated, dry_failed}（**没有静默边**，denied 全留痕，设计点 5）与 ``wet_cost``
成本估计（修饰 2：截断在 k 的依据可从事件重建）。top-k 平局**显式决定论**：acquisition 降序 + cand_id 字典序次键（修饰 1，对齐 R3 P0
对称平局落插入序的最贵历史教训——晋升谁不得取决于枚举顺序）。``knowledge_fingerprint``=decide 显式消费的编译知识见证（G1 钩子：冻结知识→
决策逐位相同；翻转被引 claim→fingerprint 变+受影响候选 deny_reason 可预期变）。**promoted=[] 合法且必发**（修饰 3：零晋升轮=合法安静，
事件**缺席**才可疑——activity 门判据是"promotion_decision 存在且 promoted=[]"=合法、事件缺失=失活）。发射纪律：NullPromotion 臂
decide()->None → 不发（零 mode 分支，同 learning_weight_assigned 面缺席）；**resume 重建不重发**（I4）。发射点由 **W9 mcl 合龙接线**
（本批只提供 ``emit_promotion_decision`` 辅助函数 + 接线点 docstring，emission POINT 不硬接主循环）。``pv=1`` 出生即治理（REF-1）。

### wet_leg_skipped — 安静轮的 wet 腿显式跳过（M16 W9 --loop mcl；testing）
| round_id | int | reason | str（``no_candidate_promoted`` 零晋升轮 \| ``no_candidate_proposed`` 空提案轮） |
Consumers: eval、activity/监控面（合法安静 vs 失活辨识——同 promotion_decision 的"缺席才可疑"纪律）、``verify_run_chain`` 门 12 验收器。
语义：两类合法安静轮各在此**响亮**落一条跳过留痕（没有静默边）——``no_candidate_promoted``：某轮 ``promotion_decision`` 的 ``promoted=[]``，wet 腿无候选可跑；
``no_candidate_proposed``：llm 档提案空轮（提案全出池被丢/reask 耗尽），本轮无 dry/promotion。有晋升的正常轮不发本事件。
门 12 验收器（``scripts/verify_run_chain.py``）把安静轮视作**一等链节点**：``wet_leg_skipped{reason}`` 即该轮链证据，链在此合法截断（空提案轮免 promotion/claim、
零晋升轮免 claim），而非报 CHAIN BROKEN；但**严判**：``promoted=[]`` 却无本事件（静默零晋升）或安静轮又出现同轮 ``claim_decision``（矛盾）照旧 BROKEN。

### wet_leg_issued — wet 腿已下发的持久不重放标记（Phase 4 崩溃/恢复硬化；testing）
| round_id | int | exp_id | str | n_wells | int |
Consumers: ``mcl.run_mcl_loop`` resume（湿腿不重放对账）、eval、对账工具。必填键（EVENT_PAYLOAD_REQUIRED）= {round_id, exp_id, n_wells}。
语义：某轮**在下发 wet 命令之前**落一条持久留痕（湿腿=rewindable(False) 段，blue_to_red 092：「已 issued 湿命令不得重放」应为持久化不变量而非仅运行时保护）。``n_wells``=本轮 wet 布局孔数，
resume 据此证明持久化 wet 结果**完整**方可消费；若标记存在而持久观测数 < ``n_wells`` → 结果不完整且已下发命令不得重放 → 响亮 ``WetReplayError`` 拒绝续跑。resume 命中「已 issued」
的轮**只消费日志结果、绝不二次下发**（wet_leg_issued 该轮恒 1 条）。**write-strict from birth**（新 kind，无 legacy 容忍）。零晋升/空提案轮走 ``wet_leg_skipped``，不发本事件。

### physical_action_transition — 物理动作事务面态迁移（M23 Phase 1 真湿就绪；testing）
| action_id | str(确定性派生 hash(round_id,exp_id,well/action_idx)) | round_id | int | to | enum{PLANNED,PENDING,COMMITTED,ROLLED_BACK,AWAITING_RECOVERY,ABORTED} | （快照明细字段由 action_ledger.ActionRecord 携带，非 ABI） |
Consumers: ``expos/adapters/wet/action_ledger.py``（哈希链台账）、resume 对账、Phase 5 就绪报告、tests。必填键（EVENT_PAYLOAD_REQUIRED）= {action_id, round_id, to}。
语义：每次物理动作事务态迁移落一条（append-only 哈希链）；``action_id`` 兼作 driver 边界幂等键（同键同参跳过、同键异参响亮 IdempotencyError——与决策面去重护栏 NondeterminismError 同构双闸）。**write-strict from birth**（新 kind，不入 ADDITIVE_SINCE——无 legacy 日志可携带它，red 122 裁定）。

### claim_decision — 轮末证据→claim 在线裁决（M17 K-A 知识闭环；testing）
| pv | int(=1) | round_id | int | claim_id | str | claim_version | int\|null | decision_status | enum{supported,rejected,qualified,insufficient} | decision_fn_id | str(注册名) | decision_fn_version | str | criterion_version | str | input_observation_ids | list[str] | observation_fingerprints | object(str→str，非 ABI) | statistic | object{name,value,test,df,tail,p_value,effect_estimate,effect_se,ci,seed}(非 ABI 明细) | power | object{achieved_power,evidence_factor,evidence_strength,independence_assumed}(非 ABI 明细) | consumed_knowledge_fingerprint | str(sha256 hex) | provenance_fingerprint | str(sha256 hex，非 ABI) | deny_reason | str\|null(仅门拒/降级时非空) |
Consumers: agent（读更新后的 ledger 产下轮提案）、tests（K1-K5 判别）、对账工具。必填键（EVENT_PAYLOAD_REQUIRED）= {round_id, claim_id, claim_version, decision_status, decision_fn_id, input_observation_ids, statistic, power, consumed_knowledge_fingerprint}。
语义：``kernel.claims.apply_claim_deltas`` 把本轮 ``ClaimDelta`` 落入 append-only claim ledger 后，逐条裁决在此落一条留痕。``decision_status``∈{supported,rejected,qualified,insufficient}
（``insufficient`` 不改 target 有效状态——K3 缺证据≠支持）；``decision_fn_id``/``decision_fn_version`` 必须注册于共享 ``DECISION_FN_REGISTRY``（与离线 claim_compiler 同源治理，
在线不绕过），``deny_reason``∈{unregistered_decision_fn, decision_fn_version_mismatch, weak_cannot_retract_strong, append_only_violation}（弱判据不得撤强结论=强度单调门，
门拒/降级必留痕、无静默边）。payload 携 K4 全 provenance（输入观测 ids+内容指纹、统计量+功效侧信息、消费的旧 knowledge_fingerprint、产出 claim 版本）——**第三方仅凭事件流可重算裁决**
（K4 自足性；``provenance_fingerprint``=快照链审计抓手，K1 零注入自推导）。发射点由 **K-C**（Certification Policy + mcl 轮末 hook）接线（本批只提供 ``emit_claim_decision`` 辅助函数，
emission POINT 不硬接主循环，同 ``emit_knowledge_updated``/``emit_promotion_decision``）；**resume 重建不重发**（I4）。``pv=1`` 出生即治理（REF-1）。

### agent_shadow_proposal — shadow 档 LLM 并行提案审计（M18 agent-backend 开关；testing）
| 字段 | 类型 |
|---|---|
| round_id | int |
| schema_valid | bool |
| fingerprint_match | bool |
| basis_subset | bool |
| order_diff | object{template_order:list[str], llm_order:list[str], identical:bool, common:list[str], template_only:list[str], llm_only:list[str]}（非 ABI 明细） |
| usage | object（gen_ai 用量块：input_tokens/output_tokens/system_fingerprint/…，非 ABI 明细；provider 不 honor 为空块合法降级） |
| prompt_sha256 | str(sha256 hex) |
| validator_versions | list[str]（门版本 id，恒 ["fingerprint_echo@v1", "basis_subset@v1"]） |
Consumers: eval、Stage 2 shadow 验收脚本、监控面。必填键（EVENT_PAYLOAD_REQUIRED）= {round_id, schema_valid, fingerprint_match, basis_subset, order_diff, usage, prompt_sha256, validator_versions}。
语义：``mode=shadow`` 档决策**仍由模板路径出**（逐位不变），LLM 每轮并行产一次提案，本事件落一条审计（letter 086 §2 + 094/095 增补）。``schema_valid``=该提案能否铸出合法 DecisionRecord（结构合法 ∧ 指纹回显命中 ∧ basis⊆在账 claim_ids——故指纹错时 schema_valid∧fingerprint_match 双 False，判别性）；``fingerprint_match``/``basis_subset``=两门独立结果；``order_diff``=模板候选序与 LLM 提案序的决定论 diff 描述子；``prompt_sha256``=冻结知识的纯函数（同条件同哈希，跨轮稳定，Stage 3 前提）。shadow 腿**任何异常都不得影响决策路径**（捕获后记 schema_valid=false，环继续）。``decision 面构造性排除本事件``（其 usage/response id 天然非决定论——DECISION_FACE_KINDS.v1 白名单不含本 kind）。**resume 重建不重发**（I4：已完成轮不重跑 shadow 腿）。

### agent_generation_failed — llm 档提案生成失败留痕（M18 agent-backend 开关；testing）
| 字段 | 类型 |
|---|---|
| round_id | int |
| failure_kind | str（如 "reask_exhausted" / "provider_error" / "empty_proposal"） |
| attempts | int |
| usage | object（gen_ai 用量块，非 ABI 明细；provider 死时可为空块） |
| prompt_sha256 | str(sha256 hex) |
Consumers: eval、Stage 3 llm 档报告、对账工具。必填键（EVENT_PAYLOAD_REQUIRED）= {round_id, failure_kind, attempts, usage, prompt_sha256}。
语义：``mode=llm`` 档 LLM 提案生成经 validate-and-reask 耗尽 / provider 全灭 → **空提案 legal-quiet**（环由 ``if not cands`` 关轮，落 ``wet_leg_skipped``），并在此**响亮**落一条失败留痕（没有静默边）。``failure_kind``/``attempts`` 取自 backend 的 FailureRecord（reask_exhausted=重试预算耗尽、provider_error=调用抛错）。有合法提案的正常轮不发本事件。**resume 重建不重发**（I4）。

## 2. 新 kind 准入流程

1. 新 kind 一律以 `testing` 落地，Since=当前 checkpoint_version，登记入本表（含必填字段名/类型/枚举全集 + Consumers + 一句语义）。
2. 有真实 Consumer（UI/CLI/eval 之一在读）+ §5 覆盖测试后，方可申报 `testing→stable`（走 §13.1 高门槛，附兼容影响说明）。
3. `stable` 退场只经 `obsolete`（写替代品 + 移除版本），宽限=跨一个 minor 且历史 run 仍可读，再转 `removed`。
4. 改名/删字段=破坏，禁在原 kind 上做；新增**非必填**字段属非 ABI，可直接加。

## 3. 与 checkpoint_version 绑定

- 事件 payload 的**必填字段/枚举**变更 ⇒ 递增 `checkpoint.json` 顶层 `checkpoint_version`，并在 ENGINEERING.md §2 对应表登记 + 就位 `migrate_vN_to_vN+1`。
- RunStore 加载遇未知 checkpoint_version **响亮失败**（ExposError），绝不按新格式静默解析旧数据（CONTRIBUTING §3 无静默降级）。
- 本 v1 对齐 checkpoint_version=1 / 包 0.1.0。

## 4. CI 守门建议（词表覆盖测试）

```python
# tests/test_event_vocab.py —— 词表↔实现对账（应随晋级补全）
import re, json
REQUIRED = {  # 本表 §1 声明的必填键集（stable 候选先行纳管）
    "routing": {"obs_id", "round_id", "trust", "routing", "confidence"},
    "reclassification": {"obs_id","round_id","from_trust","to_trust",
                          "from_routing","to_routing","actor","reason"},
    "checkpoint": {"round_id"},
    # decision 用 DecisionRecord.model_fields 交叉核对
}
def test_manifest_superset_of_declared(run_dir):
    """实写键集 ⊇ 声明必填键集（§13.4）；少键即违约。"""
    for line in (run_dir / "events.jsonl").read_text().splitlines():
        ev = json.loads(line)
        need = REQUIRED.get(ev["kind"])
        if need:
            assert need <= set(ev["payload"]), f'{ev["kind"]} 缺字段: {need - set(ev["payload"])}'

def test_no_unregistered_kind(run_dir):
    """出现在 run 里的每个 kind 必须登记在本表（防未登记词表漂移）。"""
    REGISTERED = {"status_transition","routing","routing_bulk","qc_report",
        "attribution","decision","reclassification","reclassification_conflict","resolution_conflict",
        "checkpoint","resume","round_designed","model_updated",
        "stage_changed","action_consumed","action_skipped","run_start","run_stop","config_drift",
        "redo_reconciliation","risk_map_applied","aggregation_alpha","learning_weight_assigned",
        "knowledge_updated","promotion_decision","claim_decision",
        "wet_leg_skipped","wet_leg_issued","view_quarantine",
        "agent_shadow_proposal","agent_generation_failed","physical_action_transition"}
    kinds = {json.loads(l)["kind"] for l in (run_dir/"events.jsonl").read_text().splitlines()}
    assert kinds <= REGISTERED, f"未登记 kind: {kinds - REGISTERED}"
```

## 5. run 级 start/stop 文档缺口（bluesky/event-model 源码走读增补，2026-07-10）

event-model 四文档纪律（`references/event-model`）对 events.jsonl 有一处**收尾**可借（M10）：

- **缺口**：event-model 每个 run 以 `run_start`（必填仅 `time`+`uid`、其余全为自由 metadata）**provably-first** 开、以
  `run_stop`（必填 `exit_status`∈{success,abort,fail} + `run_start` 回引 + `num_events`）**provably-last** 收。expos 现无此二事件——
  config.json 是**散装 start 等价物**（domain/mode/seed 指纹即 metadata-first），末尾 `checkpoint` 事件是散装 stop，但**无 `exit_status` 终态枚举、无收口断言**。
  M10 收尾建议补两个 kind（testing）：`run_start{config_fingerprint,budget}`（首事件）、`run_stop{exit_status:enum{success,abort,fail},reason,n_rounds,n_obs}`（末事件），
  与 §2/§3 的 OpenLineage 终态语义、manifest commit-marker 合流；**本轮不实现**。
- **descriptor 先行于数据**：event-model 每 stream 一份 `descriptor` 声明 data_key 的 dtype/units/shape，schema 只写一次、event 只带 FK+值。
  expos 的 domain YAML/变量 schema 已承担此角色；可选借鉴=每 run 落一份「观测 schema 快照」事件（变量名→dtype/units）使读者脱离 domain YAML 自描述（design-note，非当前范围）。
- **validate 与 EXP010 互补**：event-model `compose_*` 在 `validate=True`（**默认开**）时用 `schema_validators[DocumentNames.X]` 对**每份文档**做结构校验（类型/必填在写出时即炸）；
  expos 侧对应物是 §4 的 CI 词表覆盖测试（实写键集⊇声明必填集）+ expos-lint EXP010（词表漂移守门），二者为 **CI/静态**层。分工：event-model=运行时逐记录结构校验（opt-out），
  expos=append 廉价 + CI/lint 后置守门——刻意不在 `append_event` 内联 jsonschema。此为已选权衡，记录以备将来若需运行时校验可参照 event-model 的 opt-out 开关形态。

## 6. 机制活性事件族协定（bluesky/event-model + OTel semconv 定向走读，ARCH_V2 §2 升级）

R2 后 `risk_map_applied` / `aggregation_alpha` 为两个 ad-hoc 活性观测面，字段结构各异；未来 8+ 机制
（`mechanisms.py` 注册表）会发散。本节定统一协定，二者标注**将迁移至本协定**（迁移 = 破坏性改字段，走 §2 准入 + §3 递增 checkpoint_version）。

- **统一字段词表（借 OTel semconv 命名律：小写·`.` 分命名空间·`{object}.{property}`）**。所有活性事件收敛为单一 ABI 形状，
  即 ARCH_V2 §2 的 `mechanism_effect{mechanism:str(注册名), round_id:int, grade:enum{active,warning,absent}, n_affected:int, effect:{min:float\|null, max, mean}}`。
  语义类型借 OTel instrument 选型：`n_affected` 是**计数语义**（可累加 counter），`effect` 是**gauge 语义**（本轮效应幅度分布快照，非单调、可恒等）。
  **`grade` 三态取代早期 `fired:bool` 二值**（O3-D 交接建议 1 落地，先行两事件 `risk_map_applied`/`aggregation_alpha` 已加 `grade` 字段）：借 k8s 探针 `probe.Result{Success,Failure,Warning}` 三档——
  `absent`=注册缺席/接线断开（≈liveness 失败，EXP011 红）、`warning`=发射了但效应恒等/被下游吸收（≈readiness 降级，单轮合法、sweep 级 `eval/activity_budget` 失活预算收口）、`active`=效应非恒等（`n_affected>0 ∧ effect 非恒等`）。
  **发射/裁决解耦**（收紧 1，k8s results_manager「worker 只 Set() 不处置」同构）：`grade` 是**派生事实**（loop 发射端按纯函数据本轮事实字段判档），红/黄的 CI 裁决收在消费端（tests + activity_budget）。机制专属明细（is_none/n_distinct/entries）一律降为**非 ABI** 自由字段。
- **descriptor 裁定：不引入 per-run `mechanism_descriptor` 事件**。event-model 的 descriptor 把**多事件共享的键 schema**（dtype/units/shape）提到 run 级只声明一次；
  但 expos 的 §1 ABI 注册表**已充当该 descriptor**（字段名/类型/枚举全集冻结、带外声明），且活性事件是定长标量、自描述、对 torn tail 鲁棒（§0.1）——再加一个 descriptor 事件要求读者 FK-join 一份可能半写丢失的声明，省下的仅是几个短键名，得不偿失。
  且 descriptor 拆分**治不了真正的膨胀**：`entries` 膨胀源是**逐观测的值**（数据），非重复的键（descriptor 只能提键）——见体量纪律。
- **体量纪律（实测外推，见 `scratchpad/ref_activity_events.md`）**。`aggregation_alpha.entries` 是**逐训练观测**（累积 TRUSTED，一行/孔），**非逐格**——48 孔×2700 格的真值网格从不入 entries，故上界是 O(累积孔×轮)≈千行，非 O(孔×格)。
  实测一个 os-soft 8 轮 run：每行 entry≈92 B，entries 随累积训练集增长（r0=43→r3=140→末轮≈180），单条末轮 alpha 事件≈16 KB（碾压 routing 均值 199 B），该事件族总量≈90–100 KB，**约翻倍**整份 events.jsonl。
  **裁定（借 OTel exemplar）**：ABI 只带 `effect` 摘要（`{min,max,mean,count,median}`）；明细走**有界 exemplar**——全部异常/降权行（QUARANTINE 软副本，是少数、即信号）+ 多数 TRUSTED 只留摘要，封顶。既砍掉 ~85% 体量，又保住 `test_mechanism_activity` F-3 的软副本↔TRUSTED 交叉分类（软 exemplar 明细 vs trusted 摘要中位）。
- **新机制准入模板**：① `mechanisms.py` 注册 {注册名, 属主策略, 应激活场景谓词}；② 环路发射 `mechanism_effect{...}`（本节 ABI 形状，所有 mode 统一发射、零 mode 分支、纯派生不改决策）；
  ③ 配一条断线变异入 `tests/mutants/`（返回 None/raise/权重≡1）+ 一个环路级击杀断言（差分测试准入，ARCH_V2 §2 三级守门之一）；④ EXP011：本 mode 注册集 == 发射集，缺席即红。
- **迁移映射**：`risk_map_applied`→`mechanism.risk_map_placement`（grade 已就位：absent/warning/active、n_affected=n_wells、effect={min,max,mean}）；`aggregation_alpha`→`mechanism.soft_trust_reweight`（grade 已就位：软副本 alpha 膨胀比值判档、n_affected=降权行数、effect=alpha 分布摘要+软副本 exemplar）。
