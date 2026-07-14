# OS 原则 —— expos 的 Linux 式平台架构映射

> 2026-07-10。Linux 只作**平台设计类比**，不是实现目标。每条映射落到 expos 的
> 真实符号（模块/类/函数），当前已实现的标 ✅，规划中的标里程碑。
> 底线不变量见 §12——与 ARCHITECTURE.md 公理一一对应，冲突时以 ARCHITECTURE 为准。

---

## 1. 内核边界（kernel boundary）

| Linux | expos | 状态 |
|---|---|---|
| 内核态数据结构 | `ExperimentObject` / `ObservationObject`（仅有的持久科学对象） | ✅ M1 |
| 内核日志载荷 | `DecisionRecord`（事件载荷，**不是第三对象**——如同 dmesg 行不是内核对象） | ✅ M1 |
| 内存/存储管理 | `RunStore`（append-only 事件日志 + 原子写 + 检查点） | ✅ M1 |
| 进程生命周期 | `lifecycle`（轮次状态机 `VALID_TRANSITIONS`） | ✅ M1 |
| **MMU/保护模式** | **信任路由**（`adjudicate`：未经裁决的观测不得触碰响应模型——正如未经 MMU 的地址不得触碰物理内存） | ✅ M5 |

信任路由是 expos **唯一原创的"系统调用"**：其他 OS 没有"该 syscall 返回值可疑"的一等语义。

## 2. 系统调用面（受控 API）

内核只暴露这些"syscall"，其余一切经由它们：

| syscall | expos 入口 | 特权检查 |
|---|---|---|
| append observation | `RunStore.save_observation`（经 ingest→PENDING） | 单写者（loop 持句柄） |
| submit proposal | `lifecycle.submit_proposal` | kind∈PROPOSAL_KINDS |
| accept/reject proposal | `lifecycle.validate_proposal` | **actor∈{planner,human}**，agent 调用即 LifecycleError |
| reclassify observation | `lifecycle.reclassify` | 同上 + 追加事件永不覆盖历史 |
| write checkpoint | `RunStore.write_checkpoint` | 先事件后原子写（崩溃偏斜保守） |
| export run | `RunStore.export_view` → `ReadOnlyRunView` | frozen、无写方法、**无 truth** |

类比"we don't break userspace"：**run 目录 schema 与事件词表是 expos 的 userspace ABI**
（UI/CLI/eval 都消费它），演化必须向后兼容（checkpoint_version 键治理，ENGINEERING.md）；
模块间 Python 接口是"内核内 API"，0.x 期允许重构。

## 3. 驱动模型（driver model）

| Linux | expos | 状态 |
|---|---|---|
| 设备驱动 | `ExecutionAdapter` 协议（`adapters/base.py`） | ✅ M3 |
| 虚拟设备 | `CrystalSim`/`CoatingSim`（共享 `SimulatorBase` 执行链） | ✅ M3 |
| 人工回灌通道 | `BenchManualAdapter`（worklist 出、CSV/图像回） | ✅ M3 |
| 未来物理驱动 | 真实台面 adapter（CLSLab 注入函数蓝本，§13.7） | post-M10 |
| 设备能力发现 | manifest 的 capability/safety_class（LAP 四档，§16） | post-M10 |

驱动纪律：驱动**不得变异** ExperimentObject（测试断言）；truth sidecar 只能由 sim_*
生成（"驱动可以造虚拟世界，但不能读别人的"）。

## 4. VFS 式对象模型

`runs/<name>/` 即挂载点，统一寻址：

```
experiments/exp_*.json     # inode：实验对象（写一次+状态原子更新）
observations/obs_*.json    # inode：观测对象（当前态物化视图）
events.jsonl               # 日志型文件（append-only）
models/snapshot_r*.json    # 模型快照元数据（版本链，alias=指针文件模式 §18.1）
checkpoint.json / config.json  # 挂载元数据（原子重写/写一次）
report/                    # 派生视图（summary/score/trajectory——可重算）
truth/                     # 隔离分区：决策模块无读权限（公理 6）
```

三类写语义（§13.13）：append-only / 原子重写 / 写一次；manifest 充当 commit marker。

## 5. /proc 式只读内省

| /proc 类比 | expos | 状态 |
|---|---|---|
| /proc/<pid>/* | `ReadOnlyRunView`（frozen 快照；M8 升级 Tiled 式索引+懒加载） | ✅/M8 |
| /proc/meminfo | `report/summary.json`（轮次/预算/最优一屏） | ✅ M4 |
| dmesg | `expos inspect events`（CLI v2 规格）+ UI Tab4 | M10 |
| /proc/modules | 模型快照元数据表（UI Tab3） | ✅ 首版 |

sysfs 教训（一文件一值 vs /proc 混杂）：新增暴露面走**结构化文件+schema**，
不往 summary 里塞杂项。

## 6. 权限/能力模型

| 能力 | agent | planner | human | kernel |
|---|---|---|---|---|
| 提案（propose） | ✅ | ✅（内生动作） | ✅ | — |
| 解释（explain） | ✅ | — | — | — |
| 裁定提案（accept/reject） | ❌ LifecycleError | ✅ | ✅（可 override 翻盘+conflict 事件） | 执行 |
| 改判观测（reclassify） | ❌ | ✅ | ✅ | 执行 |
| trust/routing 语义 | ❌ | ❌ | ❌ | **独占**（`adjudicate` 纯函数） |

强制是**日志层机器检查**而非调用约定：`_resolutions` 忽略 agent 署名的裁定记录，
伪造 acceptance 不生效（test_kernel 有伪造攻击用例）。UI/CLI 的 override 走
`overrides/pending/` 文件通道，零 store 写句柄（§13.13）。

## 7. 调度器类比

| Linux 调度 | expos（`planner/`，✅ M7 接线） |
|---|---|
| 就绪队列 | 动作队列（`arbiter.collect_actions`：内生 next_action + 已 accept 提案） |
| 优先级 | `ActionItem.priority`（归因置信度；AlabOS 式"≥高档保留给纠错"备选） |
| CPU 配额（cgroup.cpu） | 动作预算封顶 ≤30% 孔位（`arbitrate`）+ `BudgetManager` 响亮超支 |
| 重试策略 | 显式重入队（HELAO 式）+ retry_count/max_retries（M7 待补字段） |
| 亲和性/绑核 | `placement_hint`（DISAMBIGUATION 钉中心）→ LayoutPlanner |
| 防饥饿 | ε 探索配额（`exploration_quota`，RAHBO 缓解）+ 每轮 detour 生成上限（§18.1 防自激增殖，M7 待补） |
| 调度类切换 | 阶段 FSM（`stages.decide_stage`：sobol→gp→failure_aware，Ax 式声明规则表） |

## 8. namespace / cgroup 类比

| Linux | expos | 状态 |
|---|---|---|
| mount namespace | 域隔离（`domains/*.yaml`——换域零内核改动） | ✅ M3 |
| pid namespace | run 隔离（run 目录互不可见；resume 校验 domain/mode/seed 匹配） | ✅ M4 |
| cgroup 限额 | 预算隔离（wells/rounds per run；动作 30% 子限额） | ✅ M2/M7 |
| 多租户 | Slurm 扫描隔离（每 cell 独立 run 目录 + 确定性命名防重跑 §18.1；多进程写同 journal 用 symlink 锁 §13.11） | M9 |

cgroup-v2 的"no internal process"启示：预算只在叶子（单 run）记账，
compare/扫描层只做汇总不直接持预算。

## 9. 插件/模块类比

| Linux | expos（§16 + PLUGIN_API_DRAFT.md，post-M10） |
|---|---|
| 内核模块 | 四类 entry_points：adapters / domains / qc_checks / planner_stages |
| modinfo | `expos_plugin.yaml` manifest（kind/provides/safety_class/api_version） |
| 模块签名 enforce | 加载期契约硬校验 + allowlist（远端 git-ref 加载必签名，§18.1 红线） |
| **taint 标志** | 非 official 插件参与的 run 打水印（report 层 taint 字段——bug 报告先看 taint） |
| 稳定性分级 | official / community / experimental 三档 + 规则目录 + `expos-lint` grader |
| 禁止符号劫持 | **插件不得覆盖内置名**（与 HA 相反——对实验安全系统 shadow 即注入通道） |
| 报表渲染器 | report renderer（plot_run/未来模板化）——用户态工具，不进内核 |

## 10. 内核日志/审计账类比

`events.jsonl` = dmesg + auditd 合一（append-only、seq 单调、永不覆盖）：

| 事件 kind | 审计语义 |
|---|---|
| status_transition | 进程状态迁移 |
| routing / qc_report | **信任路由判决**（每观测 + 每轮汇总） |
| decision（DecisionRecord 载荷） | 提案/裁定/解释/叙述——配对不变量机器可查 |
| reclassification / resolution_conflict | 改判与翻盘留痕（引用旧事件，不覆盖） |
| model_updated / checkpoint | 模型快照与恢复点 |
| stage_changed / action_consumed | 调度器决策（M7） |

词表治理采 OTel semconv 模式（§18.1）：每 kind 带描述 + stability + 覆盖测试；
spec 与引擎分仓是长期方向（§18.2）。

## 11. 不从 Linux 抄什么

- **不抄 C 内核复杂度**：无内核态/用户态进程切换、无锁竞技场——单写者 + 纯函数即可；
- **不引过早的守护进程**：回合制闭环不需要常驻 daemon；QueueServer 式 manager/worker
  分离只在 M9 多节点扫描确有需要时启用（触发条件明确前不做）；
- **插件不得绕过内核语义**：加载器只给"内核调用插件"的入口，注册表冻结、
  禁覆盖内置、api_version 不符拒载；
- **不允许直写观测/模型状态**：一切变更走 §2 的 syscall 面；agent/UI/插件
  结构性无写句柄；
- **M5/M6/M7 稳定前不过度抽象**：Topology 泛化、spec 分仓、插件市场全部
  排在核心论证（M9）之后——先证明内核值得存在，再长生态。

## 12. expos 不变量（终局承诺）

1. 内核持久科学对象**永远只有** ExperimentObject 与 ObservationObject；
2. DecisionRecord **永远是事件载荷**，不升格为第三对象；
3. truth sidecar 对决策模块（qc/models/planner/agent）**永远不可见**——
   唯一合法读者是事后评分（`expos/eval`，叶子模块，无人可 import 它）；
4. actor 能力**在代码与测试中强制**（`ADJUDICATOR_ACTORS`、日志层 `_resolutions`
   过滤、守门测试含伪造攻击用例）——不是文档约定；
5. 插件**不得覆盖内置信任语义**（禁 shadow 内置名、adjudicate 无扩展点）；
6. **无静默降级**：不可行即响亮失败（LayoutError/BudgetError/DomainError/…
   统一 ExposError 语义），静默兜底视为缺陷。

---

## 13. 借鉴治理模式（Linux Documentation/ 走读提取，references/linux sparse clone）

### 13.1 稳定面红线（stable-api-nonsense + printk-index）
- **expos 的 "userspace ABI"（永不破坏）**：run 目录 schema、events.jsonl 结构化词表（kind/字段/枚举）、DecisionRecord 持久化 payload、ReadOnlyRunView 契约——凡被历史 run 落盘、被外部读者依赖者。
- **"内核内 API"（0.x 期可自由重构，重构时同树调用点一起改）**：模块间 Python 接口、信任路由内部函数、RunStore 内部结构。
- 事件的**自由文本 message 永不入协定**（printk 教训：监控只准依赖结构化词表，不 grep 文本）；配版本化词表索引（每个 kind 的引入版本），跨版本审计可对齐。
- 稳定面变更走高门槛（stable-kernel-rules 精神）：附兼容影响说明、拒绝无收益改名。

### 13.2 信任水印 taint 位（tainted-kernels + module-signing）
- 每条 run 一个**单调不可逆 taint 位域**（run 元数据 + report）：`community_plugin_used` / `unsigned_plugin` / `allowlist_override` / `agent_suggestion_auto_applied` / `out_of_budget_forced` / `manual_data_edit`——置位后本 run 及派生产物永久带水印，**分诊先看 taint、bug 先在 untainted 配置复现**。
- 插件签名三态（照 module-signing）：验签通过→正常；未签名 + permissive→加载并置 taint；enforce→仅 allowlist+验签者可加载。校验在内核侧（"不必信任 agent/userspace"）。早期只用 manifest SHA-256 + TOML allowlist，不上 PKI；enforce 默认关。
- taint 只标注不阻断——硬拒绝是 enforce 开关的事，别混淆两种语义。

### 13.3 其余提取（第二梯队）
- **capabilities bounding-set**（预留，不立即切换）：对外保留 actor 角色枚举，内部解糖为能力位集合检查；agent 的能力上界永不含 apply/spend/override 类——"仅建议权"的制度化形态，第三方 actor 类型增多时启用。
- **cgroup top-down + no-internal-process**：预算树只在叶子 run 消费，扫描协调层不持预算（M9 已按此设计）。
- **一贡献一逻辑改动 + Fixes/Refs-run 因果 tag**：代码变更与触发它的 run/event 双向可追（CONTRIBUTING 补充项）。
- 不抄：对树外生态的敌意语气（expos 欢迎第三方）、X.509/keyring 重型 PKI、cgroup 多控制器、邮件列表流程。

### 13.4 ABI 注册表落地形态（Documentation/ABI 四级制走读）
- `docs/abi/{stable,testing,obsolete,removed}/` 每个稳定面族一个契约文件、每条目一个 What 块：`What / Since-version(=checkpoint_version) / Stability / Consumers / Description`（弃 Date——git 史更准）。
- 升降级规则照 ABI README：新 kind 默认 testing；testing→stable 需真实 Consumer+覆盖测试；stable 只能经 obsolete（写替代品+移除版本）退场，宽限=跨一个 minor 且历史 run 仍可读。
- **tracepoint 裁决**：kind+必填字段（名/类型/枚举）=ABI 进 stable；evidence 内自由字段=非 ABI，读者不得硬依赖。CI 守门：manifest 实写键集 ⊇ stable 声明键集。
- 别抄：日历时限、物理搬文件的重活（字段+目录双写 CI 校验）、"事后吵是不是 ABI"的模糊态。

### 13.5 TrustPolicy 的 LSM 式半可插边界（security/lsm 走读）
- **策略层（可插，post-M10）**：阈值、证据权重、新增 QC 检查的评分——插件只能产出"多疑分数"。
- **裁决骨架（永不可插）**：adjudicate 唯一入口且纯函数；TrustLevel/Routing 枚举及配对语义；默认拒绝（qc=None 不路由、裁决权仅 planner/human）。插件不能改枚举、不能新增路由目标、不能绕 adjudicate 写 trust；**无"卸载/替换钩子"能力**（LSM 教训：注册即不可逆受骨架约束）。
- **errno 结论**：ExposError 加第二维 `kind`（invalid_input/not_found/permission/state_conflict/resource_exhausted）作分类标签、不承载重试语义（映射表见走读交付）；lockdown 的"运行时单调收紧"对应 enforce 开关语义。StatsError 需补挂 ExposError（整合期修）。

### 13.6 调度与工程纪律（scheduler/dev-tools/reporting-issues 走读）
- **仲裁排序键 backlog（EEVDF 防饿死）**：`eff = w(semantics)·confidence + α·wait_rounds`（detour w=2/addition w=1、滞留轮数线性抬升、tie-break 仍 item_uid 保确定性）；更硬版：wait≥N 作资格闸门强制入选一次。
- **测试六层归属判据**（KUnit/kselftest 分层）：单元=无 I/O 单码路；属性=纯函数不变量；stateful=跨轮状态机；合成场景=注入驱动整 feature；端到端=真实闭环产物对账；压力=真 agent/Slurm。**SKIP≠FAIL**：统计层低配机 skip 且响亮记账，绝不静默 pass。
- **CorruptedRun 分诊顺序**（docs/TRIAGE.md 骨架）：taint → 事件账时间线定位首个异常判定 → 产物对账锁 corruption 边界 → 最小复现 seed → 未污染基线复现 → 按轮 bisect。

---

## 14. 控制器模型与 durable-execution 正交轴（第十二轮 source 级深读，2026-07-10）

### 14.1 控制器模型（desired / observed，详见 CONTROLLER_MODEL.md）
借 K8s spec/status 二分：**desired-state 载体唯一是计划 `ExperimentObject`、observed-state 载体唯一是落账观测集**（+ `exp.status`，只由 lifecycle 写）；reconcile = resume 的 level-triggered 重建（按全量当前观测重算派生态，不「续消费增量」）。此心智模型的 12 条可审计不变量（spec/status 分离、level 幂等、WAL 先行序、determinism、单写者、无静默回退）已收敛入 **`docs/CONTROLLER_MODEL.md`**——该文档确认既有 loop.py/store.py 实现已同构，为 design note，无代码改动。

### 14.2 三种 durable-execution 的正交轴
optuna、plumpy、temporal 恰好各占一个正交轴：**optuna = 事件重放**（op-code journal + reducer，但无状态机）；**plumpy = 状态快照**（按名存引用 + 幂等重执行，但无事件日志）；**temporal = 事件溯源 + determinism**（分布式，重）。expos **同时**持有 events.jsonl（事件重放）+ checkpoint.json（状态快照）+ `derive_seed`（determinism），且是单机单写者——**站在三者的最小完备交集**上。推论：checkpoint 极简性（只存可重解析名字 + 纯数据游标、把可重算/可重连之物排除出快照）有了一等外部范本（plumpy），派生态从观测重建与之同构（CONTROLLER_MODEL 不变量⑦′）。

### 14.3 本轮「不抄什么」增补（跨项目，接 §11）
- **不学 HA 的信任式 claimed-done**：HA 63 条质量规则仅 7 条有 validator、余下靠 reviewer 自证；expos 反着抄——机器判据须**复核** claimed-done，无 validator 的判据只能加 taint 水印、不能抬 tier。
- **不学 optuna 的位置游标**：其「已应用记录计数」作 cursor 能成立仅因后端锁串成单一全序；expos 多进程写同 journal 下位置计数必错位，坚持**显式 seq**（EVENT_SCHEMA §0.1）。
- **不学 pluggy 的 tryfirst/trylast 隐式序**：QC 证据与 renderer 产物顺序须确定、可重放，插件不得抢排序——**registration order 冻结**，不给隐式优先级通道。
