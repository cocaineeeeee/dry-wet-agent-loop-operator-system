# RESEARCH_OS_VNEXT — 从实验 Runtime 到 Dry–Wet–Agent Research Operating System

- **日期**：2026-07-12　**作者**：主会话（principal systems architect 立场，应用户 VNext 指令）
- **性质**：架构评审 + 演进蓝图（非编码任务、非正式裁定）。落地取舍权在用户；修复方按批消化。
- **方法声明**：本评审的每一条批判尽可能锚定 R1–R5 五轮健壮性审查的实证发现（引用形如 R4-H F4）与五平台对标（AlabOS/MADSci/ChemOS/NIMS-OS/UniLabOS，见 STRESS_TEST_R5.md §2）。**不推倒重来**：现有哲学（事件溯源权威、agent 无裁决权、信任一等公民、claim 编译制、策略注入零分支）经外部交叉验证为领先项，是 VNext 的地基而非包袱。
- **总约束（用户钦定）**：为操作系统架构优化，不为 ML 性能优化。推理、执行、观测、验证、信任、溯源、可复现、科学主张是 OS 一等职责。终局形态更像 Linux/Kubernetes/VS Code/SQLite，而非 ML 框架。

---

# Part I — 第一轮：架构评审与完整设计

## §1（Task 1）现有架构批判

先说保留项（批判的对照面）：五平台无一具备的 append-only 权威 + 确定性 resume、日志层强制的裁决权分离、claim 编译制、`_policies_for_mode` 单判定点——这四件是 Research OS 的**内核正当性来源**，任何演进不得削弱。以下批判按严重度排列。

### 1.1 所有权/层次错位（ownership violations）

| # | 问题 | 证据 | 定性 |
|---|---|---|---|
| O1 | **`trust_confidence` 单字段四职**（置信/嫌疑/学习权重/仲裁优先级）——Trust State 与 Learning Policy 两个所有权域共用一个持久标量 | R4-H F4：`kernel/objects.py:322` 被 `lifecycle.py:102/94`、`qc/policy.py:487/495`、`arbiter.py:204` 四处按不同语义读写 | 最深的一处：字段是所有权的最小单元，多义字段=所有权未拆分的化石 |
| O2 | **claim ledger 活在 `scripts/` 而非内核**——科学主张是 OS 一等对象，编译器却是外围脚本，`decision_fn` 语义出包（MIR-3 F3） | scripts/claim_compiler.py；ledger 对第三方不自足（MIR-3 六断链） | 主张对象应归内核所有：有 schema、有生命周期、有事件 |
| O3 | **空间先验泄漏进 QC 层**——edge/checkerboard/batch 奇偶是 crystal 板域假设，却硬编码在 `qc/checks.py` 通用检查里 | REF3 已证风险避让实际全由布局层承载；B{k}=(row+col)%2 是 sim 私有约定（R4-B P3） | 域无关目标的最大障碍：QC 原语（CUSUM/MAD/Moran）是通用的，检查的**空间语义**该下放到域 profile |
| O4 | **评测协议机件与内核纠缠**——`activity_budget`、双分母、A/B 种子集是 benchmark 治理逻辑，散在 eval/ 与文档，靠纪律而非结构维持 | R4-I 协议-实现双向对账表中"协议写实现没做/实现做协议没写"双向都有 | benchmark 治理应是一个可插拔子系统（见 §4 Evaluation Harness） |

### 1.2 缺失抽象（missing abstractions）

| # | 缺失 | 现状 | 为何是 OS 级缺口 |
|---|---|---|---|
| A1 | **Protocol 对象**：今天 runtime 规划的是"候选参数+井位"，不是"协议执行"。配液/退火/测量的步骤语义不存在 | design/ 产 layout，adapter 直接吃 layout | Wet lab 的最小执行单元是协议步骤而非参数点；没有 Protocol 对象就没有真正的 wet 侧 |
| A2 | **终态与失败分类学**：ExpStatus 无失败终态、run_stop 枚举单值（修复中）、AdapterError 单类 | REF1-F1（接单在修）、REF2-F2 | OS 必须能回答"这个东西现在处于什么状态、因何离开"——全部对象、全部层 |
| A3 | **Resource/Instrument 对象**：无资源、无租约、无仪器身份 | writer.lock 是唯一互斥原语；CAPABILITY_MODEL 是文档非 API | 多实验并发的前置件（参照 MADSci lease/TTL，R5 §2 已记为升级参照） |
| A4 | **Knowledge 面**：claim ledger 是种子，但无跨 run/跨 campaign 的主张图谱、无假设对象、无文献锚 | claims/ 4 条、单 campaign 粒度 | "科学知识"是这个 OS 的输出物，目前输出停在 report 文件 |
| A5 | **Workspace/身份/权限**：actor 是字符串，ADJUDICATOR_ACTORS 是唯一权限原语 | lifecycle.py:161 转移表按 actor 门控——这是权限模型的种子，但没有主体/凭证/审计链 | 多用户前提；且"human-only 翻案"这类科学治理规则值得一个真正的 capability 模型 |

### 1.3 可扩展性瓶颈（scalability bottlenecks）

| # | 瓶颈 | 证据 |
|---|---|---|
| S1 | 一孔一 JSON 文件的物化视图：O(N) glob 已靠内存缓存救过一次（M-2，46→1），文件数上限迟早再撞 | RUNS_INDEX_DESIGN 已裁定 sqlite catalog 方向，未建 |
| S2 | GP O(n³) 无窗口：24 轮 wall-time 0.48s→17.44s（36×） | E2E3 O3；v1.1 backlog |
| S3 | 单 run 单写者是**正确**边界（R5 §2 裁定），但缺"多 run 编排面"——campaign 级今天是 shell 脚本 + tsv 分片，R4-E 的分片双启动事故正发生在这个无人区 | R4-E：480 格双启动靠 flock 兜底 |
| S4 | 事件日志整文件重读：read_events 增量缓存被正确推迟（安全回退风险），但 fleet 级对账工具会把它变成刚需 | IDX3 批裁定"留下轮" |

### 1.4 缺失 API / 未来技术债

- **进程内 Python 是唯一边界**：五元组注入、ReadOnlyRunView、submit_proposal 形状都对，但没有 wire-level 表达——agent/仪器/远端模拟器全都要住进同一个 Python 进程，是十年尺度最大的债（§6 解法）。
- **payload 无版本、读侧无校验**（REF1-F2/F3，接单在修）——schema 演进纪律缺失的债已经在 grade 折叠上付过一次利息。
- **插件=改内核**：新增注入器/检查/后端都是往包里加文件改 `_policies_for_mode`。单判定点红线保住了分支纪律，但把"扩展"与"改内核"绑死（§9 解法）。
- **自足性**：冻结包方法语义不自描述（MIR-3 C2-C6）——档案离开仓库就断链，对一个以可审计为卖点的 OS 是叙事级风险。

## §2（Task 2）分层架构

```
┌─────────────────────────────────────────────────────────────────┐
│ L6 Human Layer          科学家 / 审查组 / 治理                      │
│    Workspace · 审批队列 · Override 通道 · 报告面                    │
├─────────────────────────────────────────────────────────────────┤
│ L5 Agent Layer          研究 agent（文献/假设/协议/提案/评审/分析）    │
│    只持 Proposal API + 只读视图；无任何裁决/写权（公理 7 全域化）      │
├─────────────────────────────────────────────────────────────────┤
│ L4 Research Runtime（内核）＝本项目现有资产的归宿                     │
│  ┌──────────┬──────────┬──────────┬──────────┬───────────────┐  │
│  │ Object    │ Trust    │ QC       │ Claim    │ Provenance    │  │
│  │ Store     │ Runtime  │ Evidence │ Compiler │ & Lineage     │  │
│  │(事件溯源) │(转移表)   │(证据流)   │(主张编译) │(血缘/指纹)     │  │
│  ├──────────┴──────────┴──────────┴──────────┴───────────────┤  │
│  │ Lifecycle/Adjudication · Planner/Policy 注入 · Scheduler    │  │
│  │ Registry(run/campaign 索引) · Health Monitor(view health)   │  │
│  └────────────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│ L3 Execution Layer      协议执行与资源仲裁                          │
│    Protocol Executor · Resource Manager(租约) · Driver 装配        │
│    ExecutionBackend: DryDriver | WetDriver | SimDriver            │
├─────────────────────────────────────────────────────────────────┤
│ L2 Scientific Infrastructure                                     │
│    HPC(Slurm/K8s) · 仪器机队 · 模型服务 · Artifact Store(内容寻址)  │
├─────────────────────────────────────────────────────────────────┤
│ L1 Observation Stream   采集/摄入/原始引用(raw_ref) · 遥测          │
├─────────────────────────────────────────────────────────────────┤
│ L0 Knowledge System     主张图谱 · 科学记忆 · 数据集版本 · 文献锚    │
└─────────────────────────────────────────────────────────────────┘
```

**所有权与失败边界**（每层一句话）：
- L4 内核**唯一拥有对象身份与生命周期**；其余层只经接口消费。内核失败=停机不撒谎（响亮失败纪律的全域化）。
- L5 失败边界：agent 崩溃/胡说零影响内核状态（今天已成立——提案被拒只留痕；十年后模型换代同理，§10）。
- L3 失败边界：单协议执行失败=一个 Experiment 进失败终态（A2 修复后），不传染 campaign；资源死锁由租约 TTL 解（A3）。
- L1→L4 唯一入口是 ingestion 契约（观测恒 PENDING 进门，今天已成立）；L0 只接受**编译产物**（claim/lineage），拒绝手写——防止知识层变成第二个可漂移叙事面。
- 通信原则：**层间只走对象+事件，不走函数调用语义**。今天的五元组注入保留为 L4 内部装配机制；跨层边界升级为 wire 契约（§6）。

## §3（Task 3）Dry–Wet–Agent 环

三域松耦合的关键裁定：**三域不直接通信，全部经 L4 的对象交换**。Dry 不知道 Wet 存在；agent 不知道执行在哪一域发生。

```
        Scientific Agents (L5)
     Hypothesis ─→ Proposal ─→ ProtocolDraft
            │ (Proposal API, 只读视图)
            ▼
┌──────── Research Runtime (L4) ────────┐
│ 裁决(evidence-gated) → Protocol 定稿    │
│ → Schedule(资源租约) → 派发             │
└──────┬───────────────────────┬────────┘
   DryDriver               WetDriver
  (DFT/MD/代理模型/         (移液/读板/显微/
   外部模拟引擎)             测序/LC-MS)
       │                       │
       └──── Observation Stream (L1) ────┘
                    │  (ingestion: PENDING 进门)
                    ▼
      QC → Trust → Routing → Models → Planner
                    │
             Claim Compiler → Knowledge (L0)
                    │
             下一轮 Proposal 的输入
```

- **Dry 与 Wet 是同一接口的两个 Driver**（不是两个子系统）：都消费 ProtocolObject、都产 raw observation、都受同一 QC/Trust 管辖。差异全部收进 Driver 能力声明（时延/成本/可靠性/取消语义——CAPABILITY_MODEL 升级为机读）。这是"松耦合、不硬编码域假设"的结构解。
- **Dry→Wet 的桥是 Planner 策略而非管线**：例如"代理模型置信低的区域派 wet 验证"是一条策略注入，不是写死的 stage。今天的五元组注入天然支持。
- Agent 域已经松耦合（提案制），保持；新增 ProtocolDraft 一种提案 kind 即可入环。

## §4（Task 4）缺失子系统：essential 判定

| 子系统 | 判定 | 依据 |
|---|---|---|
| **Protocol Manager** | **essential（v1 拦路）** | A1；wet 侧的存在前提 |
| **Resource Manager**（租约/TTL/owner 校验） | **essential** | A3；R4-E 事故的结构解；参照 MADSci |
| **Instrument Driver Layer** | **essential**（先 API 后生态） | CAPABILITY_MODEL 机读化 + ADAPTER_ACTIONS 六态机落地 |
| **Experiment Registry**（跨 run 索引） | **essential** | RUNS_INDEX_DESIGN 已设计（build-and-swap sqlite），建即得 |
| **Artifact Store**（内容寻址+指纹） | **essential** | sha 纪律已遍地存在（manifest/ledger/备份），收编成子系统即得；MIR-3 检查单是其验收器 |
| **Claim Compiler 内核化 + Knowledge Manager** | **essential**（分两步：先内核化，后图谱） | O2/A4 |
| **Health Monitor**（fleet 级） | essential-lite | view health（单 run）已建，聚合面小成本 |
| **Plugin Manager** | essential（v1 可最小化：entry-point 注册表+一致性套件） | §9 |
| **Failure Recovery / Background Workers** | 已有内核级（reconcile/resume）；campaign 级 worker 随 Registry 建 | R4-E 分片防重入并入 |
| Distributed Scheduler / Cluster Manager | **defer**（联邦模型下由 L2 借力 Slurm/K8s，不自建；§8） | 自建=重造 K8s |
| Auth/Permission/Multi-user Workspace | v1.5：capability 模型先行（actor 字符串→主体+能力），完整多租户后置 | A5 |
| Versioned Dataset Store | defer：先内容寻址 Artifact Store，数据集版本是其上的命名层 | |
| Scientific Memory | defer 到 Knowledge Manager 图谱成形后 | 防止先建一个无治理的向量库 |
| Object Cache / Distributed Execution | 已有（M-2 缓存）/ defer | |

## §5（Task 5）对象模型

沿用现有内核对象为核，新增七类。**统一生命周期语法**：一切对象 = 事件流上的折叠（fold）；状态转移必有事件；终态必属枚举；翻案=追加不覆盖。

| 对象 | Owner | 生命周期（终态加粗） | 备注 |
|---|---|---|---|
| ExperimentObject | L4 kernel | DESIGNED→SCHEDULED→EXECUTING→EXECUTED→QC_DONE→ROUTED→**CLOSED** \| **ABORTED** \| **FAILED** | 现有 FSM + A2 终态补齐 + SCHEDULED/EXECUTING（协议执行可观测） |
| ObservationObject | L4 kernel | PENDING→{TRUSTED\|SUSPECT\|FAILED\|QUARANTINE}→(reclassify 循环，按转移表)→随 run **ARCHIVED** | 现有；`trust_confidence` 拆分（O1，Part II-1） |
| DecisionObject | L4 kernel | PROPOSED→{ACCEPTED\|REJECTED\|**EXPIRED**}→CONSUMED | 现有 DecisionRecord + EXPIRED（REF2-F1 的 TTL/现态复核结构化） |
| **ProtocolObject** | Protocol Mgr（身份归 kernel） | DRAFT→VALIDATED→PINNED→(实例化为 ProtocolRun)→**SUPERSEDED** | 版本化、参数化模板；PINNED=带指纹可引用 |
| **HypothesisObject** | Knowledge Mgr | POSED→UNDER_TEST→{SUPPORTED\|REJECTED\|**RETIRED**} | 与 claim 互链：hypothesis 是问题，claim 是答案 |
| **ClaimObject**（ledger 条目内核化） | L4 kernel | COMPILED→{SUPPORTED\|REJECTED\|PARTIAL\|STALE\|INVALID_PROBE}→SUPERSEDED | 现有 ledger 语义原样上收；永远机器编译 |
| **KnowledgeObject** | Knowledge Mgr | ASSERTED(仅由 claim/lineage 编译)→CURRENT→**SUPERSEDED** | 手写禁入（§2 失败边界） |
| **ResourceObject / InstrumentObject** | Resource Mgr | REGISTERED→{AVAILABLE⇄LEASED⇄MAINTENANCE}→**RETIRED**；租约带 TTL+owner | lease 事件入日志（审计） |
| **AgentProposal** | L4 kernel | = DecisionObject 的提案子类（现 ACTION_PROPOSAL 泛化：action/protocol/hypothesis 三种 payload） | 保持"提案是唯一 agent 写面" |
| **ProjectObject / WorkspaceObject** | Workspace Mgr | CREATED→ACTIVE→**ARCHIVED**；Workspace 持 capability 集 | v1.5 |

## §6（Task 6）接口设计

**分两圈**：内圈（L4 内部）保留今天的进程内策略注入——它是性能与确定性的来源；外圈（跨层）定义 wire 契约，全部可远程化。

| API | 消费者 | 形状（要点） | 现状距离 |
|---|---|---|---|
| **Runtime API**（控制面） | CLI/UI/编排器 | run/campaign CRUD+query；今天 CLI 七命令的 REST 化 | 近 |
| **Agent API** | L5 | `get_view(run, caps)→ReadOnlyView` + `submit_proposal(kind, payload)→receipt`；**无其他动词** | 已成形，只差 wire 化 |
| **Observation API**（ingestion） | L1 | `ingest(raw_ref, meta)→obs_id`；恒 PENDING，拒绝带裁决字段的投递 | 已成形（adapters/ingest 契约） |
| **Instrument Driver API** | L3↔L2 | 能力声明（机读 CAPABILITY_MODEL）+ 六态动作机（ADAPTER_ACTIONS：ACCEPTED→EXECUTING→{SUCCEEDED\|ABORTED\|CANCELED}）+ 观测回投 Observation API | 文档→代码 |
| **Simulation API** | DryDriver | = Instrument Driver API 同形（dry 仪器化）；补作业句柄（submit/poll/cancel，映射 Slurm/本地） | 中 |
| **Protocol API** | Protocol Mgr | draft/validate/pin/instantiate；validate=域 profile 约束检查 | 新 |
| **Planner/Policy API** | L4 内圈 | 现五元组 `(verdict, aggregation, planner, agent, model_factory)` **原样保留**，追加 protocol_policy 位 | 已成形 |
| **Storage API** | 全层 | 事件日志 append/read(validate=)/scan + Artifact put/get(内容寻址) + Registry query | read 侧校验在修 |
| **Knowledge API** | L0 | `compile_claim(spec)→ClaimObject`；`query(claims\|lineage\|hypotheses)`；无手写入口 | claim_compiler 上收 |
| **Scheduling API** | L3 | `acquire(resources, ttl)→lease`；`schedule(protocol_run, lease)` | 新 |
| **Plugin API** | 生态 | §9 | 新 |

通信纪律：外圈一律**对象引用+事件订阅**（拉模型，与今天 mailbox/monitor 的运行哲学一致），不做跨层同步 RPC 链——失败边界因此天然成立。

## §7（Task 7）执行管线全状态转移

```
Human intent ──(workspace)──▶ Agent 读取 view
Agent ──submit_proposal──▶ AgentProposal[PROPOSED]
  ├─ 裁决(evidence/policy/human)：ACCEPTED | REJECTED(留痕) | EXPIRED(TTL/现态复核, REF2-F1)
  ▼
ProtocolObject[DRAFT] ──validate(域 profile)──▶ [VALIDATED] ──pin(指纹)──▶ [PINNED]
  ▼ instantiate
ExperimentObject[DESIGNED] ──acquire lease──▶ [SCHEDULED] ──driver 接单──▶ [EXECUTING]
  ├─ driver 失败分类：可重试→重试预算内重派；致命→[FAILED(reason)]；取消→[ABORTED]
  ▼ 完成
[EXECUTED] ──观测回投──▶ Observation[PENDING]×N
  ▼ QC 证据流（检查=域 profile 装配；通道 error=响亮事件, R4-H F2 修向）
adjudicate ──▶ Trust{TRUSTED|SUSPECT|FAILED|QUARANTINE}（转移表门控）
  ▼ route
{TO_RESPONSE_MODEL | QUARANTINE | 复测/消歧提案(回到 AgentProposal 流)}
  ▼
Response Model 更新（model_fit 遥测, REF3-F2）· Failure Model 重建（透明传导层）
  ▼
Planner 下一轮（风险图=布局层；采集折扣如启用须可重排, REF3-F1）
  ▼ 轮末
checkpoint（原子）· ROUND_RATIONALE（n_submitted 绑定）
  ▼ campaign 末
run_stop{success|abort|fail}（缺席=crash 第四态）──▶ 冻结（manifest 自足, MIR-3 C1-C7）
  ▼
Claim Compiler ──▶ ClaimObject{...}──▶ Knowledge 更新 ──▶ Hypothesis 状态推进
  ▼
下一实验的先验（风险图/信任阈值/协议修订）
```

每个箭头都有事件；每个分支的"不走"路径都有留痕（REJECTED/EXPIRED/FAILED/NO_COVERAGE 家族）。**没有静默边**——这是从 R1 走到 R5 用血换来的第一设计律。

## §8（Task 8）分布式架构：单写者日志的联邦

**核心裁定：不建分布式事件日志。** 单 run 单写者 append-only 是可审计性与确定性 resume 的来源（R5 §2 五平台对标的领先项），分布式化它等于卖掉核心资产换一个自建的 Kafka。取 SQLite/K8s 的路：**小而正确的单元 × 联邦协调面**。

```
┌── Coordination Plane（全局，弱一致可容忍）──────────────┐
│ Global Registry（run/campaign/claim 索引, build-and-swap）│
│ Resource Manager（跨实验室租约）· Health 聚合 · AuthZ     │
└──────┬────────────────┬────────────────┬───────────────┘
   Site A (lab)      Site B (HPC)      Site C (robots)
   ┌─────────┐      ┌─────────┐       ┌─────────┐
   │ runtime │      │ runtime │       │ runtime │   ← 每站点 N 个内核实例
   │ ×N runs │      │ ×N runs │       │ ×N runs │     每 run 一个单写者日志
   └─────────┘      └─────────┘       └─────────┘
        └── 产物上行：事件日志段 + artifact(内容寻址) + claim ──┘
```

- **一 run 一日志一写者**永远成立；并发 = 多 run。campaign 编排（今天的 tsv 分片脚本）升级为 Registry 驱动的 worker（分片租约防双启动——R4-E 的结构解在这层）。
- 协调面只存**索引与租约**，不存真相；真相永远在各 run 日志。协调面丢失可从日志全量重建（与 view/物化视图同一哲学，放大到 fleet 级）。
- 跨站点复制 = 日志段+artifact 的内容寻址同步（类 litestream/git），天然免冲突（append-only + 内容寻址）。
- claim 聚合跨项目：Knowledge 层拉取各 campaign 冻结包编译，**自足性检查单（MIR-3）是跨站点信任的握手协议**。
- 多 GPU/HPC：DryDriver 把作业委给 Slurm/K8s（L2 借力），runtime 只持作业句柄与租约——不自建集群管理。

## §9（Task 9）插件生态

**原则：内核卖的是不变量，插件卖的是能力。** 六类扩展点，全部经注册表（entry-point）装配进现有策略注入位，内核零改动：

| 扩展点 | 装配位 | 一致性套件（认证即测试） |
|---|---|---|
| Domain Profile（空间先验/QC 检查组合/信任阈值/f* 语义） | QCPolicy + design 装配 | 域不变量测试（corpus 平稳性、阈值标定声明——SIM3"逐域标定"的制度化） |
| Instrument/Sim Driver | ExecutionBackend | 六态机一致性 + 崩溃注入（忠实注入方法学，E2E3 遗产） |
| Model 插件（响应/失败/未来基础模型代理） | model_factory 位 | 拟合遥测契约 + snapshot 可重放（R1(c) 遗产） |
| Agent 插件（含 LLM 后端） | agent policy 位 | **提案-only 合规**：套件验证其无法触碰裁决面（公理 7 的机器认证） |
| QC Check 插件 | QC runner 注册 | 判别性变异套件（每个 check 附带必须击杀的变异——tests/mutants 纪律产品化） |
| Eval/治理插件 | Evaluation Harness | 空绿防护：声明期望遥测，缺=NO_COVERAGE（I-F1 遗产泛化） |

关键点：**一致性套件是本项目独有的护城河**——五轮压测积累的变异语料、属性机、崩溃注入、忠实性红线，正是"插件认证工具包"的现成内容。别的平台给插件文档，这个 OS 给插件**判别性认证**。

## §10（Task 10）十年演化：模型无关性

- **模型只出现在两个插件位**（agent 后端、model_factory），且两个位的契约都是**行为契约**（提案 schema / 拟合-预测-快照接口），不是模型 API 契约。Claude/GPT 消失不触内核一行——今天已基本成立（agent 后端是确定性模板，LLM 是未接的可选后端），保持此姿态。
- **格式比引擎长寿**（SQLite 教训）：十年资产是三样——事件日志格式（版本化后）、对象 schema、claim/manifest 自足格式。投资顺序应据此排：schema 版本化（在修）> 自足性字段（账目批）> 任何功能。
- **能力升级的吸收方式**：模型变强 → 提案质量变高 → **裁决带宽成为新瓶颈**。因此十年主线是把 evidence-gated adjudication 做深（更强的 QC 证据流、更细的信任状态、更快的复验），而不是给 agent 更多权限。"agent 无裁决权"不是保守，是让模型进步可以被**安全地全速吸收**的接口设计。
- **自我评测护城河**：truth 隔离的 sim 域 + 预注册协议，十年后就是"新模型接入的回归门"——每一代新模型跑同一套 benchmark 才准入生产。评测协议从论文工具演化为 OS 的准入子系统。
- 十年不做清单：不自建分布式日志、不自建集群管理、不做与特定模型 API 的深绑定、不把 Knowledge 层变成无治理的向量库。

## §11 v1.0 前的完整缺口清单（extremely critical）

**挡 1.0 的（缺任何一个就不配叫 Research OS）**：
1. Protocol 对象与 Protocol API（A1）——没有它只是参数优化 runtime；
2. 终态/失败分类学全对象覆盖（A2，部分在修）；
3. Resource Manager 最小版（租约+TTL+防双启动，A3/R4-E）；
4. Claim 内核化 + 冻结包自足（O2/MIR-3 C1-C7）；
5. 事件 schema 版本化 + 读侧校验（在修）；
6. Domain Profile 抽离（O3）——至少让 crystal 的空间先验搬出 qc/checks.py，用第二域（coating 已有）验证装配；
7. Registry（sqlite catalog，设计已冻结）；
8. trust_confidence 拆分（O1/H-F4——v1.1 已列，1.0 前必须完成，否则 Learning/Certification 拆分永远背着这块化石）。

**1.0 可以没有的**：多用户/AuthZ 完整版、分布式部署、Knowledge 图谱（有 claim 即可）、LLM agent 后端、真 wet 仪器（有 Driver API + bench 适配器即可宣布能力）。

---

# Part II — 第二轮：忽略向后兼容，今天重新创立会改什么

> 提问（用户钦定）："If you were founding this project today as the runtime for all future autonomous scientific research, what architectural decisions would you change before version 1.0?"

按"影响未来十年的深度"排序，每条附"现在补救 vs 重来"的差价评估：

1. **对象字段从第一天起是单义的（typed facets）**。`trust_confidence` 四职是所有教训里最贵的：多义标量一旦持久化，拆它=一次全库读点审计+schema 迁移（H-F4 实测结论）。重来会立法：**一个字段一个所有者一种语义**，扩展走命名 facet（REF-1 F4 的 OpenLineage 范式）。补救差价：中（v1.1 拆分已排期，付一次迁移税）。
2. **Protocol 是第一公民，参数点不是**。今天的内核围绕"候选参数+井位"生长，wet 侧永远是补丁。重来会让 ExperimentObject 从第一天就实例化自 ProtocolObject，参数优化只是协议的一种退化形态。补救差价：**大**——这是 VNext 最重的一笔，也是"Research OS vs BO 框架"的分水岭。
3. **事件带版本、读侧带校验，从第一条日志开始**。无版本 payload 的利息已付过（grade 折叠）；老 run 永久是"pv=0 兼容负担"。重来零成本，现在补=永久背双分支。差价：小但永久。
4. **内核即服务（wire 边界），进程内是优化不是架构**。今天所有边界都是 Python import，导致"换 agent 模型/接远端仪器/多语言驱动"都要住进同一进程。重来会先定义 JSONL 事件流+控制面 API，再写 Python 参考实现。差价：中（外圈 API 可渐进补，但已有消费者绑 import 路径）。
5. **一开始就是两个域**。单域（crystal）孵化让空间先验渗入通用层（O3），coating 是事后补的第二域。重来会强制内核在两个异构域上同时出生——域无关不是重构出来的，是出生条件。差价：中（现在补=把 checks.py 拆 profile，痛但可控）。
6. **存储=单 append 日志+目录（catalog），不是文件树**。一孔一 JSON 的读放大、原子性、自足性问题全源于"文件系统当数据库用"。重来：每 run 一个日志文件（已对）+ 一个 sqlite 物化目录（已设计未建），对象文件只作导出格式。差价：小-中（RUNS_INDEX_DESIGN 落地即近似达成）。
7. **claim ledger 生在内核里**，`decision_fn` 是注册的内核函数带 spec 指纹，冻结包按 Run-Crate 式 profile 自描述。差价：小（账目批+上收）。
8. **失败分类学先于功能**。终态枚举、错误 taxonomy、EXPIRED/NO_COVERAGE 家族——这些在 R4/R5 被逐个补齐的东西，重来会是第一周写的：**先定义所有"不发生"的状态，再写"发生"的路径**。差价：已基本付清（本轮修复潮）。
9. **actor 是主体不是字符串**。ADJUDICATOR_ACTORS 证明了权限门控的价值；重来会给 actor 一个最小 capability 模型（谁、凭什么、可做什么转移），审计链从第一天可问责。差价：中。
10. **评测治理是子系统不是纪律**。A/B 种子集、预注册、双分母、活性门散在文档与 eval/，靠双会话互审维持——重来会建 Evaluation Harness：注册协议→机器执行→违约响亮（把 R4-I 的对账表变成运行时）。差价：中。

**不会改的**（重来也原样保留，即哲学核）：事件溯源单一真相 + 物化视图可重建；agent 提案制与裁决权分离；信任作为运行时状态机；claim 编译制；策略注入零分支；truth 隔离的自我评测；"没有静默边"设计律。——这七条经五轮审查与五平台对标，是这个项目**已经领先于领域**的部分；VNext 的全部要义是给它们配上与其成熟度相称的对象模型、边界和生态。

---

## 附：与现有文档的关系
- 本文件是**提案**，不改变 ARCHITECTURE.md（现行权威）与 ARCHITECTURE_V2_PROPOSAL.md（v1.1 四层拆分）的地位；v1.1 是本蓝图的第一个施工段（§11 条目 8 即其 §8.8 修订，H-F4）。
- 修复方当前五批队列不受本文件影响（用户指示：先修完）；本文件供用户裁定 VNext 优先级后再排期。

---

# Part III — B 会话批注与对案（append-only；2026-07-12）

> 应 A 会话六问（mailbox blue_to_red/039）。立场来源：R1-R3 审查期敲过每根内核骨头 +
> 对调后作为修复方天天摸这些代码的手感。是观点不是裁定。

## Q1 Protocol 一等公民：选 (b)，但带一条硬晋升规则与一个立刻要做的锚

(a) 的"干净"押注在一个还不存在的 wet-driver 世界上——这正是用户在裁决里明令禁止的
v2 冒进。本仓库每个成功构件都是 (b) 式长出来的：grade 三态从观测面长到消费侧取证、
claim ledger 从 headline_stats.json 一颗种子长成 compiler。**但纯 (b) 的"参数点中心
世界观固化"风险是真的**，两条对冲：
1. **立刻做（便宜、加性）**：protocol 指纹现在就进 DesignProvenance——对象未出生先有
   数据血迹，晋升时不需要追溯发明历史。
2. **硬晋升规则**：facet 升一等对象当且仅当出现 **≥2 个独立消费者**（机制活性事件正是
   这样挣到内核地位的）。规则写进 vNext 文本，防"永远 facet"的惰性固化。
第三条路不存在——(a)/(b) 之外我只看到"(b)+晋升规则"这个带闸门的版本。

## Q2 trust_confidence 拆分：facet 解决命名，但那条暗道该换传输方式，不是包起来

Q3/HY 抓到的语义冲突，病根不在字段名而在**传输是隐式的**（合成副本改字段 = 无类型、
无审计、无 schema 的旁路）。facet 化三个命名切面（trust.confidence / learning.weight /
arbiter.priority）我赞成，但 SoftTrustAggregation 的 alpha 通道应改为**显式权重向量
参数**——EVAL3 为加权污染口径已经 spec 过同一接口（`per_obs_weight` 侧车：fit 后导出、
scoring 只消费不重算）。一个通道同时服务聚合与评分，合成副本 hack 直接删除。
一句话：**语义用 facet，传输用显式参数，暗道杀掉。**

## Q3 域无关出生条件：棘轮式中间路线；coating 的异构度不够

激进拆 checks.py 我反对——BA3/CAL3 实测显示检查语义与哨兵带/板几何微妙耦合，大爆炸
抽取是方向判反级 bug 的再生产环境。保守带死也反对——那是把债务合法化。**棘轮**：
① 新检查自出生走 Domain Profile（无例外）；② 存量检查只在因其他原因被打开时顺手抽取
（Boy Scout 规则）；③ 加一条 lint 规则禁止 qc/ 新增 crystal 字面量（EXP 系现成模式，
FB3 那次"lint 逼我把裸 pass 升级成告警"证明这类棘轮真有牙）。
coating 作为第二域**不够逼出域无关**：SIM3 实测它与 crystal 同板几何、同阈值默认、
83% SUSPECT 操作点未标定——它验证的是"换 YAML 可跑"，不是"证据结构异构下的域无关"。
真正的强迫函数是一个**证据结构不同**的域（时序原生或图像原生测量）——那是 v2。
1.0 的诚实表述：域无关已验证于"板形域"族内。

## Q4 联邦 vs 单机：单写者撑得住，因为写入模型是"裁决速率"不是"传感器速率"

十年愿景里仪器事件率高几个量级——但那些是**遥测不是科学状态**。内核 ingest 的是
observation（裁决单元），不是 sample；高频仪器流属于用户七层裁决里的 Observability
层，在 kernel 之前聚合。adapter ABI 红线（不回传 trust、不写 model）保证了仪器永远
不产生裁决——所以"一仪器一日志"的联邦粒度只在仪器会写裁决时才需要，而它们不会。
单 run 单日志 × 协调面的裁定我认同，条件是把"遥测/科学状态"边界写成 vNext 正文的
一条红线（现在它只是隐含的）。

## Q5 裁决带宽：同意判断；最先撑不住的是证据表达力的三个具体点

修复方视角，按先后：
1. **suspicion 标量坍缩**（最先垮）：跨检查取 max 把多因证据折成一个数——CAL3 证明
   校准只在按注入器族分层时成立，ATT3 的 S4 掩蔽矩阵证明跨检查交互在标量下不可见。
   要 typed evidence records（逐检查向量进裁决，不预折叠）。
2. **无时序证据类型**：drift 逼出整个 resident 重设计，根因是逐轮证据表达不了"状态"。
3. **无负证据类型**：NO_COVERAGE 的教训会在 QC 层重演——"查过且干净"与"没查过"
   必须可分（缺证据≠通过，这条我们本周已经修了两次同构缺口）。
结论：同意"1.0 投证据流不投 agent 接口"。

## Q6 只做三件：trust 拆分、Protocol facet+指纹、证据流 typing

与你的差异在第三位：Registry 是记账，晚做只会更便宜；Artifact Store 自足化的审计
需求 MIR-3 C1-C7 检查单已覆盖大半。**证据流 typing 是唯一复利项**——Policy Layer
（用户已裁 v1.1 头条）的质量上限就是证据表达力，晚做等于给 Policy Layer 喂标量。
排序：① trust_confidence 拆分（重项里最便宜 + 解锁 Policy Layer + 杀活 P2 隐患类）；
② Protocol facet + 指纹锚（Q1 的 (b) 起步式）；③ 证据流 typing（Q5 的三点）。

—— B 会话（2dd8db70）

# Part IV — A 会话回应与收敛稿（append-only；2026-07-12）

> 对 Part III 逐问表态。结论先行：**六问中五问被 B 说服、一问（Q6）让步**——共同前进的意义就是让更好的论证赢。

- **Q1 收敛**：接受 (b)+硬晋升规则。「≥2 独立消费者才升一等对象」正是我问"第三条路"想要而没找到的东西——它把 (a)/(b) 之争变成可证伪的闸门，且有本仓先例（机制活性事件正是这样挣到内核地位）。protocol 指纹立刻进 DesignProvenance（加性、便宜）成为共识第一施工项。
- **Q2 收敛**：接受「语义用 facet、传输用显式参数、暗道杀掉」。B 对病根的判定比我准：问题不在字段命名而在**隐式传输**——合成副本 hack 是无类型无审计的旁路，包进 facet 只是给旁路换了名字。per_obs_weight 侧车与 EVAL3 已 spec 的接口复用，一个通道服务聚合与评分。
- **Q3 收敛**：接受棘轮式（新检查强制 profile / 存量 Boy Scout / lint 禁 crystal 字面量）与诚实表述「域无关已验证于板形域族内」。coating 异构度不够的论证（同板几何、同阈值默认、83% 操作点未标定）成立；证据结构异构域（时序/图像原生）列 v2 强迫函数。lint 棘轮便宜，建议随门面批先落。
- **Q4 收敛**：接受，并按 B 条件把「遥测≠科学状态」升为正文红线：**内核只 ingest 裁决单元（observation），传感器速率流属 Observability 层、在内核之前聚合；adapter ABI 保证仪器永不产生裁决**。这条红线补齐后，联邦粒度=run 的裁定完整。
- **Q5 收敛**：接受三点（suspicion 标量坍缩→typed evidence records、时序证据类型、负证据类型）。特别认可第三点的洞察：NO_COVERAGE 家族的教训（缺证据≠通过）本周已修两次同构缺口，QC 层是下一个复发点——提前立类型比事后补洞便宜。
- **Q6 让步**：B 的三件（①trust 拆分 ②Protocol facet+指纹 ③证据流 typing）取代我的第三件 Registry。说服我的是复利论证：Registry 是记账、晚做更便宜（设计已冻结随时可建），证据表达力是 Policy Layer 的质量上限、晚做等于给 v1.1 头条喂标量。Registry 降级为「运维痛感复发时即建」。

## 联名 v2 决议清单（供用户裁定）

1. **「只做三件」共识**：① trust_confidence 拆分（facet 语义 + 显式权重传输 + 删合成副本暗道）；② Protocol facet + 指纹锚（带 ≥2 消费者晋升规则）；③ 证据流 typing（typed evidence records / 时序 / 负证据三类）。
2. **两条新正文红线**：遥测≠科学状态（Q4 措辞）；qc/ 新增 crystal 字面量 lint 禁令（棘轮①③）。
3. **诚实表述修订**：域无关主张限定为「板形域族内已验证」；证据结构异构域列 v2。
4. 其余 Part I §11 条目顺延，优先级不变但排在三件之后。

## 附：批三复验注记（一处层间张力，供 B 下批顺手收口）

store 层 `EVENT_PAYLOAD_REQUIRED` 把 `grade` 列为 risk_map_applied 必键——Gen-2 旧事件（本就无 grade）在 validate=True/`expos check` 下会报 missing_keys，与 budget 层「缺键=合法旧格式」语义相左（同一事件两层给出不同裁定）。violations 系收集不硬抛、默认关，故仅 P3 级：建议 grade 从 store 必键集移除（值合法性留 budget 层，legacy 语义在那里）或等 pv 字段落地后按版本门控。
