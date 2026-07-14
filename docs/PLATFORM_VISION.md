# 平台愿景与架构再反思 —— 不受现有蓝图约束的一次重新思考

> 2026-07-10。回答两个问题：(1) "媲美 Android/iOS 的完整"对 expos 意味着什么；
> (2) 跳出 ARCHITECTURE.md 重新审视——哪些假设该松动、哪些该升格为平台法则。
> 本文件是方向性文档；落地项进 BUILD_PLAN backlog，权威蓝图仍是 ARCHITECTURE.md。

---

## 1. OS 隐喻的完整映射（我们到底在造什么）

| 操作系统概念 | expos 对应物 | 状态 |
|---|---|---|
| 内核 + 系统调用 | 两对象 + RunStore + lifecycle（裁决/路由是"特权指令"） | ✅ M1 |
| 进程 | 轮次（round，状态机管生命周期） | ✅ M4 |
| 调度器 | 规划器（预算=CPU 时间片，动作队列=就绪队列） | M7 |
| 驱动程序 | ExecutionAdapter（sim/bench；LAP 式能力描述） | ✅ M3 |
| 文件系统 | runs/ 目录契约（append-only 日志 + 原子写 + commit marker） | ✅ M4，§13.13 强化 |
| 权限模型 | actor 裁决权（agent 建议权/planner·human 裁决权）+ 真值隔离 | ✅ M1 |
| 信号/中断 | control/*.flag 抢占通道 + suspender 式安全暂停 | 设计就绪（§13.13） |
| 用户态应用 | 域（domains/*.yaml——"App"） | ✅ 双域 |
| SDK / 应用商店 | 插件体系（entry_points）+ 质量分级治理 | 设计中（本轮） |
| 系统助手 | Agent Orchestrator（Siri 有建议权、没有 root） | M8 |
| 系统 UI | 只读 UI + override 安全通道 | 首版在建 |

隐喻里**唯一原创的系统调用**是信任路由——其他 OS 没有"这个 syscall 的返回值可疑"的一等语义。这是平台的差异化内核特性，等价于 Android 当年的托管运行时。

## 2. 三种产品框架与排序（诚实的战略判断）

1. **研究成果**（现在→M9）：三臂对比证明"结构性偏差下信任路由不可被鲁棒统计替代"。这是内核论文，等价于"托管运行时值得存在"的论证。**一切平台野心以它为地基——M9 之前不动摇主线。**
2. **平台/SDK**（M10 后）：插件体系 + 稳定公共 API + 质量分级 + 文档站。让第三方"写 App（域）、写驱动（adapter）"而不改内核。
3. **实验室产品**（更远）：CLSLab 式低成本真实台面 + worklist 工作流 + UI，是进入真实实验室的楔子。

三者互相强化但顺序不可换：没有 1 的可信论证，2 是空壳；没有 2 的生态面，3 只是又一个孤立工具。

## 3. 再反思：五个该松动的假设

1. **网格不是宇宙**。Layout 深度绑定 rows×cols/edge/block——真实仪器还有序列进样、流动反应器、单样品台。应抽象为 **Topology**（位置集 + 邻接结构 + 分层），网格只是其一（queen_w 已参数化邻接，QC 层天然兼容）。→ backlog：`Topology` 协议，M2 布局与 M5 空间检查以它为参数。
2. **动作应升格为第三个"运行对象"**（非科学对象）。REMEASURE/复测/override 一直以字段+事件寄生——FireWorks/QueueServer 的实证是：动作队列值得一等持久身份（uid/状态/结果/反向账）。内核双对象公理不破：科学对象仍只有两个，动作是**运行时对象**（如同 OS 的进程控制块不是文件）。→ M7 落地（§12 结论 3 已备案）。
3. **信任不必永远三值**。连续证据→硬阈值三桶是 v1 简化；per-point alpha 桥 + 偏差校正复归是原则性演进（DEEP_REVIEW §3.1）。平台版的信任是**谱系**：trust_tier（MSA 三档）+ 连续降权 + 可翻案。
4. **单写者不是终点**。Slurm 多进程写的方案已备好（Optuna symlink 锁配方 §13.11）；QueueServer 的 manager/worker 分离是并发执行的模板。触发条件：M9 大规模扫描若需并发写同一 run 时启用。
5.5 **三条追加平台法则**（第八轮八族研究，§18.2）：**事件×产物双向对账**——resume 与 `expos check` 以"产物谓词×事件断言"联合裁定完成性，产物是真相源、事件是索引；**spec 与引擎分仓**——事件词表/facet/插件 manifest 独立于内核版本演化（OTel/mlflow/OL 三上游共同教训）；**git-SHA 即插件血缘**——市场分发与 provenance 合流为同一 git-ref 事实。架构决策自此以 ADR 记录（docs/adr/，HA architecture 仓模式）。

5. **每个域必须自带孪生**（新平台法则）。从 CLSLab 学到的最重要一课：**no domain without a frugal twin**——任何域 YAML 必须声明模拟器，任何 bench adapter 必须有 sim 回退。这让 CI 可测一切域、让用户零硬件试跑一切域——这是 Android "模拟器随 SDK 发布"的对应物。→ 已隐性成立（双域皆 sim），升格为显式校验：domain.py 加载期强制。

## 4. 平台完整性清单（大公司版的"完整"拆解）

| 维度 | 现状 | 缺口与去处 |
|---|---|---|
| 核心功能 | M0–M4 done，M5–M7 模块并行在建 | 主线推进 |
| 工程纪律 | 测试 160+，三路把关制 | CI/pre-commit/CONTRIBUTING（本轮 agent 在建） |
| 公共 API 稳定性 | 内核 schema 即 API | 0.x semver + checkpoint version 键（ENGINEERING.md 在建） |
| 扩展生态 | 硬编码注册表 | entry_points 插件体系 + manifest + 质量分级（本轮 agent 在建） |
| CLI UX | 单命令 run_loop | CLI v2 命令树（status/inspect/override/replay，在建） |
| UI | 只读四页签首版在建 | override 安全通道（§13.13 设计就绪） |
| 文档 | 9 份 docs + README | 文档站（backlog）；API reference 自动生成（backlog） |
| 可观测性 | events.jsonl + 决策审计 | OTel 命名对齐（M8）；OpenLineage 导出器（backlog） |
| 安全/权限 | actor 裁决权 + 真值隔离 + LAP safetyClass 思路 | 插件沙箱语义（随插件体系设计） |
| 国际化 | 中文注释/文档 | 论文与 README 英文版（M9 后） |

## 5. 过程自省：并行 fan-out 的整合债

20+ agent 并行是用户授权的速度换取面覆盖，但产生**整合债**：模块按"钉死契约"实现，漂移风险在接缝（stats↔checks 的 SBB 语义、policy↔loop、design docs↔实现）。纪律修正：**当前波次落地后冻结新 fan-out，进入整合里程碑**——由主线（我）亲手做 loop 接线 + 全量回归 + 接缝审计，把所有模块焊进内核后再开下一波。CHECKPOINTS 里为整合单独立账。

## 6. 一句话

expos 要成为的不是"更好的实验脚本"，而是**实验测量的可信操作系统**：
它对实验数据做的事，等于保护模式对内存做的事——没有经过裁决的观测不得触碰响应模型，
正如没有经过 MMU 的地址不得触碰物理内存。其余一切（插件、UI、CLI、生态）都是
这个内核特性的放大器。
