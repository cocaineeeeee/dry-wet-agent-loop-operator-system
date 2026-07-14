# M16 — 最小完整 Dry–Wet–Agent 闭环（MCL）施工计划

- **日期**：2026-07-12　**地位**：VNext 落地里程碑（用户裁定：蓝图不算数，跑通才算数）
- **准确定位（用户钦定，双会话共识）**：expos 现状 = **Agent + Dry + Trusted Runtime loop**（可信实验 Runtime）；Wet 端只有架构位置、Agent 端只有提案没有知识反馈闭环。M16 的唯一目标：**把真实 Wet execution 与 Agent knowledge feedback 接上，端到端跑通一条最小但完整的 Dry–Wet–Agent loop，且连续跑两轮（第二轮的假设可证明地依赖第一轮的知识更新）**。
- **红线继承**：事件溯源权威、agent 无裁决权、trust 一等、claim 编译制、零 mode 分支、没有静默边——MCL 的每个新构件出生即受这六条管辖。

## §0 四条验收门（用户四判据的可测化）

| 门 | 判据 | 判别性验收（不许自证） |
|---|---|---|
| **G1 Agent 闭环** | Observation/Claim/Failure → Knowledge 更新 → agent 读更新后的知识 → 新 hypothesis → 新 protocol 提案 | 冻结知识层 → 第二轮提案必须与第一轮**逐位相同**；注入一条相反 claim → 提案必须**可预期地改变**（知识消费的判别测试，杜绝表演性反馈——C2 教训） |
| **G2 Dry 真执行** | 真实外部模拟引擎，由 runtime 排程/执行/收结果（独立进程或作业，非进程内函数、非手动拷贝） | 事件日志含完整作业生命周期（submitted/running/collected）；作业中途 kill → 失败分类学接住（重试或 FAILED 终态），不裸崩 run |
| **G3 Wet 真执行** | 真实或可信模拟的仪器栈：driver health check / calibration / resource reservation / protocol validation / timeout+retry / device failure handling / sample identity 链 | 七件逐一有事件留痕；注入设备故障（驱动进程 kill / 超时 / 校准漂移）→ 各走各的失败路径；样本 ID 从 protocol→instrument→raw→observation 四段可追（chain of custody 查询一条命令可出） |
| **G4 同一 Runtime 串联** | hypothesis → **Dry 筛** → runtime 依 trust/证据选出值得 Wet 的候选 → **Wet 执行** → QC/Trust/归因 → 模型+知识更新 → 下一 hypothesis，全程一个 run 的事件日志 | Dry→Wet 的候选晋升是**记录在案的证据决策**（谁被晋升、依据什么置信度、谁被淘汰）；两域观测进同一 QC/Trust 管辖（同一 adjudicate 路径，事件可证） |
| **G5 整环** | G1-G4 串成整环连续跑两轮，零人工干预 | `expos run --loop mcl` 一条命令；第二轮提案的 basis 字段引用第一轮 claim id |

## §1 构件选型（最小但真实）

| 构件 | 选型 | 为何满足"真" |
|---|---|---|
| **Dry adapter** | **PySCF**（pip 装，真量子化学引擎）跑小分子性质计算（如溶剂化能/偶极矩代理目标），由 runtime 以**独立进程作业**派发（本机 subprocess 起步，作业句柄+超时+kill 语义；ssh/sbatch 分发为同接口第二后端——通道已授权） | 真引擎、真进程边界、真作业生命周期；不是 sim_base 的进程内函数 |
| **Wet adapter** | **Opentrons Python API 的 simulate 模式**（官方模拟器，真协议栈：labware/pipette/deck 校验全真）作为移液执行 + **独立进程 plate-reader 仿真器**（自研小服务：health/calibrate/measure/故障注入接口，走文件或 socket 协议） | 协议编译/校验用真 Opentrons 栈（"可信模拟"的上界）；仪器七件（health/calibration/reservation/timeout/retry/failure/custody）在 reader 仿真器上全真实现——方框变成有行为的进程 |
| **域** | 新建 `solvent_screen` 域：dry=PySCF 算候选溶剂性质，wet=按 protocol 配液+读板测响应（仿真器内置隐藏真值面+伪影注入——复用 expos 六注入器资产） | 一个域同时有 dry 可算量与 wet 可测量，dry 预测与 wet 观测天然可对账 |
| **Agent** | 确定性模板 agent 升级：读 Knowledge 视图（claims + hypothesis 状态 + failure 归因摘要）→ 产 hypothesis + ProtocolDraft 提案（LLM 后端留插件位不入 M16） | G1 判别测试对确定性 agent 才能逐位断言；模型无关红线顺带自证 |
| **Protocol compiler** | ProtocolObject（VNext② 的指纹锚即其出生证）→ 两张编译目标：PySCF 输入卡 / Opentrons protocol + reader 指令单 | A1 缺口的最小落地 |
| **Scheduler/Resource** | 最小租约管理器：instrument/compute 两类 ResourceObject，acquire(ttl)/release/过期清扫（MADSci 参照，R4-E 结构解顺带落地） | A3 最小落地 |
| **Knowledge 最小面** | HypothesisObject（POSED→UNDER_TEST→SUPPORTED/REJECTED）+ claim ledger 挂 hypothesis_ref + agent 可读的 KnowledgeView（编译产物，禁手写） | A4 种子；G1 的消费端 |

## §2 施工分解与分工（与 VNext 三件套的关系）

三件套是 MCL 的地基而非并行线：①（trust 拆分）先行不变；②（Protocol 指纹）直接并入 W2；③（证据流 typing）的 ABSENT/ERROR 语义 G3 失败路径直接消费（spec 定稿后按试点节奏入，不阻塞 MCL 骨架）。

| 工段 | 内容 | 归属 | 依赖 |
|---|---|---|---|
| W1 | 最小租约管理器 + 作业句柄抽象（subprocess/ssh/sbatch 三后端同接口） | B（内核写权） | 无 |
| W2 | ProtocolObject + 两目标编译器（含②指纹锚） | B | ①之后 |
| W3 | PySCF dry adapter（作业化执行+收集+失败分类） | A 侧 agent 沙盒开发 → 交 B 合入（或届时按包分域 adapters/dry/ 归 A） | W1 |
| W4 | Opentrons-simulate wet adapter + plate-reader 仿真器（七件全实现+伪影注入面） | A 侧 agent 沙盒开发 → 同 W3 交接 | W1/W2 |
| W5 | solvent_screen 域 profile（dry 可算量+wet 可测量+隐藏真值+注入器复用） | A | W3/W4 接口冻结即可并行 |
| W6 | Knowledge 最小面（HypothesisObject + KnowledgeView 编译 + agent 消费改造） | B（内核） + A（agent/视图） | ① |
| W7 | Dry→Wet 晋升策略（planner 注入一条证据门策略：dry 置信度+风险图→wet 候选集，决策入事件） | 共同设计，B 落 | W3-W6 |
| W8 | G1-G5 验收测试套（判别性优先：冻结知识逐位断言/故障注入矩阵/custody 链查询） | A 主笔（延续审查方测试判别性资产） | 各工段 |
| W9 | 端到端首跑 + 两轮连续 + 事件日志审计 + 台账/备份 | 双会话共同 | 全部 |

## §3 诚实边界（M16 明确不做）

- 不接真硬件（Opentrons simulate 是"可信模拟"的诚实上界，G3 判据按仿真器行为验收——对外表述为 **simulated-wet 闭环**，接真机是 M17 的事）；
- 不上 LLM agent（插件位留好，确定性 agent 先证闭环语义）；
- 不做多用户/分布式（单 runtime 单 run 串两域，联邦是 v2）；
- solvent_screen 的 PySCF 计算取最小可行体量（分钟级/候选），科学新颖性零要求——M16 验收的是 **loop 的完整性**，不是化学结论。

## §4 验收产物

一次 `expos run --domain solvent_screen --mode os --loop mcl --rounds 2` 产出：完整事件日志（含 dry 作业生命周期、wet 七件、晋升决策、知识更新、第二轮提案 basis 引用第一轮 claim）+ G1-G5 判别测试全绿 + custody 链一条命令可查 + CHECKPOINTS 里程碑条目 + 滚动备份。届时定位声明才可以从「可信实验 Runtime」升级为「**最小但完整的 Dry–Wet–Agent loop（simulated-wet）**」。
