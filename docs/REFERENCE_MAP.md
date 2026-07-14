# REFERENCE_MAP —— 外部参考系统地图

> 按架构角色分组的开源系统与文献参照。每条给出：用途 / 借什么设计模式 / 不抄什么 / 对应 `expos` 哪个模块。
> 调研截至 **2026-07-10**（六路并行 web 调研，逐条经页面/仓库核实；个别标注"未能核实"）。全部限定物理/材料方向，无生物语义。
> 阅读优先级见文末 §10。

---

## 1. 执行编排（execution orchestration）

| 参考 | 用途 | 借什么 | 不抄什么 | 对应模块 |
|---|---|---|---|---|
| **Bluesky RunEngine**（NSLS-II，活跃；2025–26 主推 ophyd-async v0.19 与 bluesky-adaptive） | 大科学装置实验编排的事实标准 | plan=generator、`Msg` 协议把"意图"与"执行"解耦；bluesky-adaptive 的 agent↔RunEngine lockstep/异步两种闭环接法 | 旧 ophyd 同步设备模型；完整 Msg 词表（read/set/trigger/save）对我们的 adapter 偏重 | `loop.py`、`adapters/base.py` |
| **MADSci**（Argonne AD-SDL，v0.8.0 2026-05，JOSS 2025） | 模块化自主科学框架，与本项目内核目标几乎同构 | Experiment/Workcell/Resource/Data/Event 的 manager 划分；设备即 Node 的标准化 REST 接口；分布式事件日志 + OpenTelemetry 可观测性 | 全套微服务部署——单机实验 OS 用进程内事件总线即可；beta 接口仍有 breaking change | `kernel/store.py`、`loop.py` |
| **AlabOS**（Berkeley Ceder 组，Digital Discovery 2024） | 支撑 A-Lab 1.5 年 3500 样品的工作流管理 | "样品–任务–设备资源"内核对象化；资源预约消解并发冲突；任务状态机天然承载 provenance | 与单一实体实验室硬件深耦合；仿真/计算任务无抽象 | `kernel/objects.py`、`kernel/lifecycle.py` |
| **HELAO-async**（Caltech HTE 组，持续维护） | 分布式仪器的分层异步编排 | 每仪器/orchestrator 一个 FastAPI server 的层级：server→action→experiment→sequence | 与该组硬件绑定的 driver 层 | `adapters/`（多执行器扩展时） |
| **ChemOS 2.0**（Matter 2024，仓库基本冻结） | "实验室即 OS"叙事的先行者 | 计算/实验/统计三方闭环的模块总线架构 | 研究级一次性代码的工程质量 | 整体架构对照 |
| **IvoryOS**（UBC，Nature Comms 2025） | 给任意 Python SDL 自动生成 web 编排界面 | 反射式"从代码生成 UI"，降低操作层门槛 | 把编排逻辑放浏览器侧 | `ui/app.py` |
| **Temporal / Prefect**（活跃） | 通用 durable-execution 工作流引擎 | durable execution：状态可重放、失败从断点恢复——正是 `checkpoint.json` + 事件日志重放的语义 | 面向云数据管道的调度器/部署栈 | `kernel/store.py`、`loop.py` 断点续跑 |
| UniLabOS（2025-12 arXiv） | AI-native 实验室 OS（边云架构） | 事务性 CRUTD 协议的提法 | 代码未见开源（未能核实），仅作方向参照 | — |

## 2. 仪器 / 协议适配（instrument & protocol adapters）

| 参考 | 用途 | 借什么 | 不抄什么 | 对应模块 |
|---|---|---|---|---|
| **SiLA 2**（标准存续，Python 参考实现在 GitLab） | 实验设备统一接口标准 | 能力=Command+Property 的最小二分；server 自描述 + 运行时能力发现两步走 | gRPC/HTTP2/证书全栈——用 JSON Schema 校验替代 protobuf | `adapters/base.py` 能力描述 |
| **LAP（Lab Agent Protocol）**（arXiv 2606.03755，2026-06） | 补 MCP/A2A 之外的 agent-to-instrument 一环 | `InstrumentCard`（capability + `safetyClass` S0–S3 + `reversible` + `physicalLimits`）；`MeasurementResult` 强制带单位/不确定度/`provenance.paramsHash`；独占 reservation 锁 | JWS 签名/DID 体系——演示级项目只取字段设计 | `adapters/base.py`、`kernel/objects.py`（InstrumentMeta/RawResult 字段） |
| **OPC UA** | 工业设备信息模型 | Companion Specification 思路：能力描述（nodeset）与传输解耦 | 通用地址空间模型的复杂度 | `adapters/base.py` |
| **Tango Controls** | 物理装置控制系统 | 命令执行前 `is_allowed` 状态校验，防非法操作 | CORBA + 中心化数据库 | `kernel/lifecycle.py` 状态机守卫 |
| **EPICS（CA/pvAccess）** | 加速器级控制 | PV 为可寻址原子单元；无中心注册表的广播发现 | 实时协议栈 | （规模扩展时参照） |
| **yaq daemons** | 小实验室轻量仪器 daemon | trait 组合式能力声明（如 `is-position`），client 按 trait 识别硬件；Avro 天然可校验 | 每设备一个 OS 进程的运维成本 | `adapters/base.py`（capability descriptor 用 trait 列表） |

## 3. 计算设计与优化（computational design & optimization）

| 参考 | 用途 | 借什么 | 不抄什么 | 对应模块 |
|---|---|---|---|---|
| **Ax 1.0 / BoTorch**（Meta，Ax 1.0 于 2025-11 重写 API） | 自适应实验平台标杆 | GenerationStrategy：把"用什么模型/策略产生下一批点"做成可配置的分阶段声明（Sobol→GP 正是我们的 M4 路径） | 整库依赖与偏 ML 调参的 trial 语义 | `design/sampler.py`、`planner/` |
| **BayBE**（Merck EMD，v0.15.0 2026-06，很活跃） | 面向材料/配方的贝叶斯 DoE | Campaign 可序列化（断点续跑同款哲学）；离散/混合空间的约束 DSL（含混合物基数约束）；pending/部分测量的异步 campaign | 化学 encoding 硬编码进核心 | `design/space.py` 约束、`kernel/store.py` |
| **Atlas**（Aspuru-Guzik 组，Digital Discovery 2025） | SDL 专用 BO 库 | planner 与执行层完全解耦的可插拔服务；输入噪声鲁棒（并入了 Golem 思路）、已知/未知约束、多保真列成统一菜单 | 对 Olympus 的紧耦合；无失败因果归因 | `planner/planner.py` |
| **Anubis**（Digital Discovery 2025） | 未知可行性约束下的 BO | feasibility-aware 采集函数：同时学"哪里会失败"与"哪里最优"——失败样本进模型而非丢弃，与我们双模型架构直接同构 | 把一切失败都当"参数不可行"——会掩盖设备/流程性故障；必须与 QC 分类配合 | `qc/failure_model.py` + `planner/` 风险贴现 |
| **Golem**（2021，思路已并入 Atlas） | 输入不确定性下的稳健优化 | "测量/控制不确定性应折算进目标"的概念 | 作为运行时依赖（项目停滞） | `planner/` 风险贴现的理论出处 |
| **Olympus**（停滞但可用） | 优化基准 surface 集 | benchmark surface + 噪声模型的评测方法学——我们模拟器+伪影注入是它的"系统性误差"扩展 | 作为运行时依赖 | `adapters/sim_*.py` 评测方法学 |
| **Honegumi**（Sparks 组，v0.4.3，npj Comput. Mater. 2026） | BO 模板生成器 | 按问题特征（噪声/多目标/混合变量）选模板的决策树——可作 planner 配置文档的组织方式 | 它是代码生成器而非库 | `domains/*.yaml` planner 配置设计 |

## 4. 协议 / 流程表示（protocol representation）

| 参考 | 用途 | 借什么 | 不抄什么 | 对应模块 |
|---|---|---|---|---|
| **XDL/χDL**（Cronin 组，标准 2.0 在 GitLab） | 声明式流程语言的标杆 | 协议=纯数据 AST（Step 序列 + Hardware/Reagent 声明），硬件绑定推迟到编译期；"machine and human-readable"同一份文本 | 合成化学专有动词集；完整图编译器 | `kernel/objects.py`（ExperimentObject 即声明式 AST）、`adapters/bench_manual.py` |
| **Bluesky event-model Documents** | 事件流数据模型标杆 | descriptor/event 分离（schema 只写一次）；字段自带 units/dtype/source；大文件用 resource+datum 外部引用不内嵌——正是 `RawDataRef` 的设计依据 | 8 类文档对两对象内核偏繁，取"两级 schema + 外部引用"即可 | `kernel/store.py` 事件日志、`ObservationObject.raw_ref` |
| **worklist 双形态模式**（XDL/Bluesky 共识） | 机器可执行+人类可读 | 人类可读 worklist 是事件日志/实验对象的**派生视图**，不单独维护第二份文档 | 为人类另写一份会漂移的平行文档 | `adapters/bench_manual.py` |

## 5. 元数据与 provenance

| 参考 | 用途 | 借什么 | 不抄什么 | 对应模块 |
|---|---|---|---|---|
| **W3C PROV** | 溯源数据模型标准 | entity/activity/agent 三元素 + used/wasGeneratedBy/wasAttributedTo 关系；观测=entity、QC 执行=activity、仪器/算法版本=agent 的映射 | 完整 PROV-O 本体栈 | `kernel/store.py` 事件词汇表 |
| **PROV-AGENT**（ORNL/ANL，IEEE e-Science 2025） | 把 LLM 的 prompt/response/decision 纳入 PROV 工作流溯源 | **DecisionRecord 的最佳现成参照**：agent 决策与下游产物的因果链统一建模 | 面向 HPC 工作流的字段体量 | `kernel/objects.py` DecisionRecord |
| **OpenLineage**（LF AI，活跃） | 数据血缘事件标准 | run 事件至少 START+COMPLETE/FAIL；自定义 facet 加前缀+`_schemaURL` 版本化——QC 判定/归因结论作为版本化 facet 挂在 run 上，**修订不覆盖历史** | 数据管道术语体系 | `kernel/store.py`（改判/翻案机制） |
| **AiiDA**（活跃，2.x） | 计算材料工作流全溯源 | 节点级自动 provenance 图；区分"数据 provenance"（可复现）与"逻辑 provenance"（意图）两层；QC/归因判定本身也应是图中节点 | 强 DAG 假设 + PostgreSQL/RabbitMQ 重栈 | `kernel/store.py` |
| **NOMAD**（FAIRmat，v1.4+，活跃） | 材料数据 schema 化管理 | schema-based entry 连接"制备条件↔表征数据" | 整套平台体量 | `domains/*.yaml` schema 设计 |
| **MLflow**（3.0，2025-06） | 运行追踪最小模型 | run/artifact/metric 三元组的最小记录模型 | 3.0 的 GenAI 观测新栈 | `kernel/store.py`、`ui/` |
| **OpenTelemetry GenAI semconv**（2025–26，beta） | agent/tool/LLM span 词表 | DecisionRecord 字段命名对齐它，换取工具链互操作 | 尚 beta，只对齐命名不引依赖 | `kernel/objects.py` DecisionRecord 字段名 |

## 6. 质量控制与失败归因（QC & failure attribution）

| 参考 | 用途 | 借什么 | 不抄什么 | 对应模块 |
|---|---|---|---|---|
| **阵列测量空间偏差统计**（中位数极化 B-score、loess 行列去趋势、边缘蒸发扩散模型；方法文献成熟） | 网格板行列/边缘效应的成熟处理 | **先去趋势、再对残差做检验**的顺序；蒸发导致的边缘效应有显式扩散模型可依 | 行列效应可加分离的默认假设——板上热/湿度传导需先验证 | `qc/checks.py` structural 检查 |
| **PySAL `esda`**（活跃） | Moran's I / LISA 开源实现 | **置换检验**生成参照分布（小样本网格比渐近 p 值稳健）；LISA 定位局部异常簇 | 地理学默认邻接权重（queen/rook）——按板上物理传导路径自定义邻接 | `qc/checks.py` 空间自相关 |
| **SPC：Western Electric/Nelson 规则、EWMA、CUSUM**（`py-qcc`/`pyspc` 有实现） | 哨兵控制带与漂移检测 | 规则引擎作特征提取器：缓慢仪器漂移用 EWMA，批次跳变用 CUSUM，游程规则抓系统性偏移 | 工业长期数据标定的默认阈值——需按小样本板重新做误报率仿真 | `qc/checks.py` reference 检查 |
| **pandera（v0.32，活跃）/ Great Expectations（GX 1.18，活跃）** | 声明式数据校验 | expectation suite=**声明式 QC 合同**；pandera 轻量适合内嵌做一级硬检查骨架 | GX 的 Cloud 导向 context 复杂度 | `qc/checks.py` hard 检查 |
| **PyOD（v3.6，活跃）/ alibi-detect（0.13）/ Evidently / river（0.25）** | 异常/漂移检测库 | PyOD 统一 fit/predict 做特征级异常评分；river 在线漂移检测器做跨批次持续监控 | 均不原生理解空间邻近结构——特征要自己造；alibi-detect 已改 source-available 许可需审慎 | `qc/checks.py`、`qc/failure_model.py` |
| **DoWhy refutation 模式**（PyWhy，活跃） | 归因假设的程序化检验纪律 | **"反驳器合同"**：每条失败假设标配 placebo（打乱位置/批次标签后效应应消失）、subsample（子集重采样应稳定）等程序化反驳器，通过才判定成立——直接升级我们的签名归因 | 完整因果图识别流程——材料 QC 缺完整因果图，只借框架不调用其估计器 | `qc/attribution.py` |
| **SDL 过程视觉异常数据集**（Scientific Data 2025） | 自动化工作流异常检测先例 | 按工作流**检查点**组织异常标注（step-specific）→ 每个内核状态迁移后置 QC 钩子 | 仅视觉模态、规模小 | `qc/` 整体组织 |
| 调研结论 | — | "三级 QC + 签名归因"**无现成整体替代品**，成熟构件（空间统计/SPC/反驳器/溯源）按材料网格场景拼装是合理路线；Beta-Bernoulli 位置/批次伪影概率模型未见 2025–26 直接先例，属较新颖设计，需用已知注入率的仿真做校准验证 | — | `qc/failure_model.py` 验证策略 |

## 7. UI / 运行看板

| 参考 | 用途 | 借什么 | 不抄什么 | 对应模块 |
|---|---|---|---|---|
| **MLflow UI** | run 对比视图 | 多 run 并排对比的导航（naive vs OS 对照页的参照） | 指标中心主义——我们要下钻到孔位级 provenance | `ui/app.py` Loop 页 |
| **Grafana** | 控制室式看板 | 告警 + 注释流（annotation）叠加在时间线上——事件日志的呈现方式 | 整套 TSDB 栈 | `ui/app.py` 决策日志页 |
| **Streamlit**（活跃） | 首版 UI 载体 | 快速只读视图 | 它的 rerun 模型不适合长驻状态机——**状态放内核，UI 永远只读 `runs/`** | `ui/app.py` |
| **IvoryOS**（见 §1） | 自动生成操作界面 | 反射式 UI 生成思路（后续演进方向） | 编排逻辑进 UI | `ui/` 演进方向 |

## 8. Agent 编排层（对应 `expos/agent/`，Agent Orchestrator）

### 8.1 propose–validate 边界的先例

| 参考 | 用途 | 借什么 | 不抄什么 | 对应模块 |
|---|---|---|---|---|
| **CARE**（arXiv 2606.14581，2026） | HTE 优化中 LLM 与传统 optimizer 共存 | **incumbent–challenger 模式**：传统 optimizer 为在位者，LLM 提挑战方案，执行前"公开证据 gate"裁决，每次裁决写 audit log——与 DecisionRecord + acceptance/rejection 设计学术同款 | 证据阈值需按域重新标定 | `agent/orchestrator.py` + `planner/` 提案仲裁 |
| **Deterministic Mediation**（arXiv 2605.13245，2026） | LLM 编排 + 确定性执行 | LLM 只选 typed tools 与参数、不生成分析代码，重跑逐位相同——TemplateBackend/LLMBackend 同一校验通道的依据 | 工具集靠人工固化、扩展慢 | `agent/backends.py` |
| **AtomAgents**（PNAS 2025，Buehler 组） | 多 agent 合金设计 | propose→simulate→validate：假设必须经物理仿真产数据裁决 | 自由对话式 agent 协作（轨迹不可控、token 失控） | `agent/` 与 `adapters/sim_*` 的关系 |
| **ORGANA**（Matter 2025） | 实验助手的人机交互 | 目标**消歧对话**（translate_goal 的交互模式）+ 自动实验报告生成；执行中感知 QC 拦截 | 感知/执行/规划紧耦合 | `agent/orchestrator.py` translate_goal / narrate_round |
| **Polybot AI advisor**（Nature Chem. Eng. 2025，Argonne） | 人机共驾模式 | 把"人"显式建模为可路由的决策节点；自治级别可配置而非全自动教条 | 对全自动的保守取舍不必照单全收 | `kernel/lifecycle.py` human override、UI 改判入口 |
| **AILA + AFMBench**（Nature Comms 2025） | agent 做仪器实验的评测 | "领域知识 ≠ 实验操作能力"——先建 benchmark 再上线 agent 的评估文化；LLM 直接下仪器指令的安全反例 | 默认 LLM 可直接驱动执行层 | `agent/` 守门测试的动机 |
| **El Agente**（Matter 2025）/ **ChemAgents**（2025）/ **MatPilot**（arXiv 2024） | 多 agent 分层编排样本 | 认知层/执行层分离；角色化 agent 与设备操作解耦；本地模型部署（数据不出域）选项 | MatPilot 守门描述薄弱勿当安全范本；静态角色划分 | `agent/` 演进方向 |
| **AISLE 路线图**（arXiv 2506.17510，2025） | 互联自主实验室社区共识 | 接口/provenance 标准化的方向 | 是路线图非实现，勿过早锁定协议细节 | 对外接口演进 |

### 8.2 LLM 先验注入优化器的稳妥形式

| 参考 | 结论 | 对应模块 |
|---|---|---|
| **LLAMBO**（ICLR 2024） | zero-shot warmstart（初始点推荐）风险最低；LLM 当数值 surrogate 不可靠 | `agent/orchestrator.py` propose_priors |
| **A Sober Look at LLMs for Material Discovery**（ICML 2024） | 负结果：裸 LLM 裁决探索/利用被证伪；保守用法=LLM 做特征提取喂给 GP | 设计红线 |
| **BORA**（IJCAI 2025） | 标准 BO 为主、**停滞才触发** LLM 注入假设 + 实时解说——介入时机与解释双职责与 Orchestrator 同构 | `agent/` 触发策略（演进） |
| **FM-informed Acquisition**（arXiv 2025） | 先验注入 **acquisition 加权**而非改核超参 | `planner/` 与 agent 提案的结合点 |

### 8.3 goal-to-spec 翻译

| 参考 | 借什么 | 对应模块 |
|---|---|---|
| **CLAIRify**（Autonomous Robots 2024） | generate–verify–repair 循环：verifier 是确定性程序，报错回喂重试直至合法——translate_goal 的实现模式 | `agent/orchestrator.py` |
| **Chemputation×LLM**（Comms Chem 2026，Cronin 组） | 三级验证：语法 parser → critique 比对 → 执行前仿真彩排（150 例 94.67% 全通过）；LLM-as-judge 只能预筛不能终审 | `agent/` 提案校验链 |

## 9. 演示域与模拟器参照（结晶 / 涂层干燥）

### 9.1 结晶闭环先例

| 参考 | 借什么 | 不抄什么 | 对应模块 |
|---|---|---|---|
| **AI-driven robotic crystal explorer**（Digital Discovery 2026） | "图像→质量标量→采集函数"三段式闭环结构 | 具体视觉方法（未能核实全文） | `adapters/ingest/image_metrics.py` + `loop.py` |
| **Automated Direct Nucleation Control 系列**（IECR/CGD） | "维持晶数于介稳区内"的控制目标表述 | FBRM 在线探头路径（俯拍成像场景不适用） | `domains/crystal.yaml` objective 设计 |
| **OPRD 2024 连续结晶平台 / CGD 2023 综述 / Adv. Intell. Discovery 2025** | 实时 CSD 反馈框架；背景引用 | 激光衍射测量原理 | 背景 |

### 9.2 涂层干燥（coating 真值面的函数形式，可直接采用）

- **Deegan 咖啡环理论**（Nature 1997 + PRE 2000）：接触线钉扎处蒸发通量 **J(r) ∝ (1−(r/R)²)^(−λ)，λ≈0.5** —— 作为"边缘增强蒸发"伪影与 coating 真值面的函数依据（特征长度约 1–2 孔）。
- **Phys. Rev. Fluids 2024（surface capture vs coffee ring）**：垂直平流/扩散时间尺度比作为**一维旋钮**，控制沉积从"强环"连续过渡到"均匀"——正好做 coating 域的核心设计变量。
- **G 参数均匀性判据**（IJHMT 2023）：无量纲 G，Gmax>10 均匀、Gmax<2 环状——coating 目标函数的阈值参照。

### 9.3 基准与模拟器

- **Olympus**（已核实源码结构）：planner–surface–emulator 接口 + 噪声注入 API 值得借用；但其噪声**只对输出叠加随机分布**（Gaussian/uniform/gamma），不建模空间系统性偏差。
- **Nature Communications 2024（SDL performance metrics）**：7 类 SDL 评估指标 + 算法-噪声敏感度扫描，是"测量误差下闭环对比"最接近的公开方法学先例——但同样只做随机噪声。
- **结论：没有任何公开基准显式建模边缘效应/温度梯度/仪器漂移/批次差异这类空间系统性偏差并做闭环方法对比。本项目模拟器+伪影注入正落在这个方法学空白上，是可发表的贡献点。**

### 9.4 图像→晶体指标（轻量非 DL 管线，可直接照做）

高斯滤波 + CLAHE 光照均衡 → 阈值分割 → 距离变换 + watershed 分离粘连晶粒 → 连通域标记（两遍 CCL）→ {晶粒数、覆盖率、尺寸分布}。误差传播框架参照 Comput. Chem. Eng. 的"分割误差→CSD 偏差"评估法（正好用于验证眩光/光照伪影对指标的影响路径）。

### 9.5 晶体质量指数（objective 的物理依据）

基于晶体尺寸分布（CSD）三要素的归一化组合：**低成核密度（单位面积晶核数）× 大平均晶粒尺寸 × 窄分布（CV=SD/mean）**，覆盖率作归一化辅助项——工业结晶共识"低 CV + 适中核密度 + 大尺寸"为优。边缘蒸发（Deegan 边界层机制，指数/幂律随离边距离衰减）与温度梯度（环境热传导，近似线性中心-边缘）机制不同，**必须分开建模**、不共用衰减函数。

## 10. 阅读优先级与趋势判断

**深读源码（按优先级）**：① Bluesky（Msg/plan 协议 + event-model documents + bluesky-adaptive 的 agent 接口）；② MADSci（manager 划分与事件日志，与本内核最同构）；③ BayBE（约束 DSL + 可序列化 campaign）；④ HELAO-async（分层异步编排）；⑤ Ax 1.0 只读 GenerationStrategy 抽象。
**只读论文/文档**：CARE、PROV-AGENT、Anubis、DoWhy refutation、LAP、AlabOS。

**2025–2026 架构趋势**（六路调研的收敛信号）：
1. **agent 认知层与设备执行层解耦成为共识**——LLM 止步于 planner/协调层，底下是带资源预约和事件日志的确定性工作流内核；
2. **失败升格为一等对象**——Anubis 学失败面、ORGANA 执行中拦截、异常数据集按检查点标注："QC→归因→再规划"闭环正在被拼出来，但**provenance 驱动的失败归因作为一等内核服务尚无成熟对标**（本项目的差异化空间）；
3. **全自动叙事退潮**——Polybot advisor 与 AFMBench 安全性结论共同指向"可配置自治级别 + 人在信任路由中"；
4. **验证器必须是确定性程序**——parser/仿真/统计检验终审，LLM-as-judge 只做预筛；默认策略永远是保守基线而非 LLM 提案；
5. **provenance 与接口标准化升格为系统骨架**（PROV-AGENT、LAP、OpenTelemetry GenAI、AISLE）。

本项目"两内核对象 + 信任路由 + 双模型 + 建议权 agent"与趋势 1–5 全部同构，且第 2 条正是差异化主张。

---

## 11. 第二轮深挖调研（实现细节级，2026-07-10）

> 第一轮（§1–§10）是广度扫描；本轮针对 M3 模拟器 / M4 优化器的落地细节，逐条经原文核实。

### 11.1 M4 响应模型与批量 BO 的默认配置（已核实）

- **sklearn GP**：`ConstantKernel * Matern(ν=2.5, length_scale_bounds=(1e-2,1e2)) + WhiteKernel(noise_level_bounds=(1e-3,1e0))`，`normalize_y=True`、`n_restarts_optimizer≈20`、另加 `alpha=1e-8` 数值抖动；**默认 bounds (1e-5,1e5) 在 <50 点时易学出退化长度尺度，必须收紧**。log 跨量级维在**空间层**取 log（我们的 space.py 已如此），与 Honegumi/BayBE 一致。
- **批量采集（q≈10–40、无 BoTorch）**：arXiv:2605.18819（2026）证明 KB/CL/fantasy 是同一条件化机制、隐式惩罚可匹敌 Local Penalization → **选 Kriging Believer**：选点后冻结 GP 超参、闭式条件化填后验均值伪观测；q>5 收益递减，q 到 20–40 时叠加最小两两距离阈值保多样性。
- **探索调度（≤10 轮）**：UCB κ 从 ~3 线性降到 ~1；理论式 κ_t 增长过快不适用；EI 噪声下易早熟，log-EI/固定 κ≈1.5–2 更稳。
- **异方差**：sklearn `alpha` 可传**逐点数组**——有副本时直接用每点 s²/n（我们的 replicate 设计正好供给这个）；无副本用两阶段噪声面（gp_extras 方案）。
- **二元类别维**：one-hot 有已证缺陷（acquisition 平坦区，arXiv:1805.03463）；首选**按二元取值分层双 GP**，类内样本 <15 时退 one-hot+round。
- **主参考**：He et al. 2026 *Adv. Intelligent Systems*"BO for the Experimental Sciences: A Practical Guide"（doi 10.1002/aisy.202501149）。

### 11.2 M3 结晶真值面的物理函数形式（已核实/标注工程近似）

- **CNT 成核速率**：J = A·exp(−B/ln²S)（Volmer-Weber/Becker-Döring 经典形式；无机盐-水 γ≈5–30 mJ/m² → B 量级 1–100，A~10²⁵–10³⁵ m⁻³s⁻¹）。定性：S 低时 J≈0，近临界窄 ΔS 内跨数量级陡升——"先缓后爆"阈值行为，公认结论。alum/KNO₃ 的精确 A/B **未能核实**（付费墙），用量级即可。
- **介稳区（MZW）**：Nývlt 关系 ΔT_max ∝ (冷却速率)^(1/m)，m 典型 2–5（KNO₃ 文献 m<4）；KNO₃ 实测过冷 ΔT≈10–17°C（体积依赖）；明矾具体值未能核实，工程近似 ΔT≈3–8°C。
- **籽晶**：有直接实证（ACS CGD/PMC10326855）——投籽晶走二次成核、重现性高、"少而大、CV 小"；未投籽初级成核诱导期长且波动大。
- **添加剂非单调**：低浓度促进、高浓度抑制有文献支撑（~0.005 ppm 量级转折）；标准抑制模型为 Kubota-Mullin G/G₀=1−α·θ（Langmuir 覆盖度），**促进段是工程外推**——真值面用倒 U 形核函数并标注。
- **质量指数**：**无公认单一标量**；行业惯例分别报 d50/CV/展宽。我们的加权几何平均 `quality = size^w1 · uniformity^w2 · yield^w3` 须在模拟器文档标注为**工程构造**。
- **边缘蒸发特征长度**：Deegan λ=(π−2θ)/(2π−2θ)（θ→0 时 1/2）为自由液滴结论；**孔阵列受限几何的衰减长度无定量文献**——工程假设：增强区宽 ≈ 孔半径 10–30%（参照 Hu & Larson 2002 边界层论证），文档须标注未经证实。
- **真值面骨架（M3 采用）**：`quality = w1·size_term(S, seeded) + w2·(1−CV_term(S, r_cool, seeded)) − w3·defect_term(f_add)`；size_term 按 CNT 阈值型（成核密度↑→均粒径↓）、CV_term 按 Nývlt（快降温→MZW 收窄→同时成核↑）、seeded 乘"少而大"因子、defect_term 用倒 U/Kubota-Mullin 衍生形；边缘/时间项独立成伪影注入器、不进主真值面。

### 11.3 M3 伪影注入器的定量先例与幅度（已核实/标注惯例）

- **先例复查（第二次独立核查）**：BO/闭环基准中"结构化非 iid 系统偏差"注入**仍未见公开先例**——最近的只有通用 y-腐蚀（arXiv 2511.15315 无界腐蚀 2025；Martinez-Cantin AISTATS 2018 离群点），SDL 基准综述（arXiv 2508.06642，Digital Discovery 2026）仅"呼吁"评估漂移鲁棒性未实现注入机制。方法学空白确认，可安心作为贡献点主张。
- **空间偏差场（边缘蒸发/温度梯度）**：强先例——Malo et al.（Briefings in Bioinformatics 2015，中性转述为阵列测量文献）的加性行列模型 x=μ+R_i+C_j+r 与二维中位数 polish/loess 趋势面；实测行列偏差影响率约 30%、中心-边缘可达 ~2 倍。注入幅度取信号量程 10–30%（压力档对齐 2 倍上限）。
- **仪器漂移**：随机游走被验证能捕捉仪器漂移统计特征；**AR(1)（系数≈1 弱均值回复）为默认**，叠加可选线性趋势作对照；幅度以相对满量程 %/h 表述（该单位为计量学惯例，具体数值文献未能核实——做敏感性扫描）。
- **批次效应**：批次随机截距（线性混合模型）是标准做法；典型幅度无强制文献值，取信号 SD 的 10–30% 并扫描。
- **眩光/离群**：Huber ε-contamination P=(1−ε)D+Q；模拟惯例 ε∈{5%,10%,20%}，压力测试 30%——默认温和档 ε=10%、高污染档 20%。
- **评价指标（M9 对比实验用）**：simple regret 之外可拼装——几何指标 Precision/Recall/AvgDegree/AvgDistance（arXiv 2401.01981，防"被几何结构误导"漏检）+ SDL 通用 Acceleration/Enhancement Factor（arXiv 2508.06642，文献中位 AF≈6）；**"污染样本利用率""错误最优命中率"无现成命名指标**——自定义并明说，恰是本项目贡献的一部分。
- **落地纪律**：所有幅度默认值多为工程惯例而非文献强制——M9 对比必须附幅度敏感性扫描（Slurm 跑）。

### 11.4 M5 三级 QC 的小样本统计配置（已核实，第三轮调研）

| 检查项 | 方法/参数 | 嫌疑分映射 |
|---|---|---|
| 空间自相关 | Moran's I：queen 权重（rook 对照）、行标准化、条件置换 9999 次、单尾正；48 格功效低→定位为"筛查/排序"，在 median polish 残差上跑 | Sellke-Bayarri-Berger 校准 α(p)=[1+(−e·p·ln p)⁻¹]⁻¹ |
| 行/列梯度 & 边缘效应 | Tukey median polish（na.rm、eps=0.01、maxiter=10；小板优先于 loess——8/6 点带宽不稳）取 row/col 效应与边缘残差 | 效应量/σ 阈值化 |
| 批次位移 & 时间漂移 | 哨兵 5 点/轮当子组：self-starting CUSUM(k=0.5, h=5) + EWMA(λ=0.2, L=3) 双挂【实况注记（R5 REF-4）：生产仅 CUSUM 单挂，ewma() 零调用方；EWMA 腿留档未接线】；**前 2–3 轮只记录不判警**（固定限需 20+ 子组，用 Q-chart/t 修正限过渡） | 越限→1 |
| 孤立离群 | median polish 残差的 MAD 稳健 z，\|z\|>3.5 | min(1, \|z\|/阈值) |
| n=2–3 副本边缘/中心配对 | **不做单轮显著性**（2ⁿ 置换最小 p=0.25/0.125 结构性无功效）——报 Cohen's d（0.2/0.5/0.8 档）+ 跨轮累积（估计统计学方向） | d 归一化 + 跨轮 Fisher |
| 反驳器 | placebo=打乱空间标签 ×999 判"效应塌零"；subsample fraction=0.8×100 判"效应稳定"（DoWhy 惯例） | 通过则降嫌疑分 |
| 合成 | 逐检查 SBB 校准 → 跨检查**取 max** 报警（Fisher/Cauchy 合并另存为总体证据；不要直接用 1−p） | max ∈ [0,1] |

### 11.5 M7 失败感知规划器的默认设计（已核实，第三轮调研）

- **采集折扣**：乘法形式 acq·P(feasible)（Gardner/Gelbart 2014 标准；Anubis 同族）；硬阈值筛选在小样本下会永久排除边界区，不用。
- **覆盖偏差警戒（关键）**：RAHBO（NeurIPS 2021）证明风险折扣下标准 BO 会系统性锁死低方差次优区——缓解：① 折扣项用 p_artifact 后验的**乐观置信界**而非点估计（桶数少时天然弱化折扣）；② 每轮强制预留 10–15% 预算给"折扣后排名靠后但认知不确定性高"的区域。
- **复测 vs 新点**：hetGP/IMSPE 判据——局部噪声方差主导 → 复测，跨点认知方差主导 → 新点；stochastic kriging 建议每点副本 ≥10 才用纯矩估计（似然框架可更少）。
- **Beta-Bernoulli 先验**：桶 <10 观测时用收缩先验 Beta(m·p̄, m·(1−p̄))，m≈5–10、p̄=全局伪影率（经验贝叶斯收缩，James-Stein 型）；避免 Beta(0.5,0.5) 边界尖锐。
- **动作仲裁**：四类动作（新点/复测/歧义消解/加对照）按**期望信息价值/单位预算**统一排序（knowledge-gradient 框架），优于固定权重轮询；最接近的先例是 SDL 的 restless bandit 测量资源分配（arXiv 2512.14930）。
- **解耦先例**：孔位/批次伪影率独立于设计参数的"两层优化"无直接文献——按传感器选择的贪心-MI 思路做二级孔位分配（工程判断，M2 LayoutPlanner 的 risk_map 通道已就位）。

---

## 12. 研究型 OS 深读地图（第四轮调研，2026-07-10；六类系统 × 指定焦点增量深挖）

> 格式：借什么机制（具体到机制名）→ 对应 expos 模块 → 影响里程碑。基础介绍见 §1–§5 原条目。

### 12.0 总裁定表（开源状态 × 采用方式）

采用方式三档：**参照**=只研究设计模式、零依赖；**可选后端**=保持接口兼容、将来可插拔接入；**集成**=直接引入依赖（目前没有——公理 1"不引服务栈"与 pyproject 约束优先）。

| 系统 | 开源 | 采用方式 | 影响里程碑 |
|---|---|---|---|
| Bluesky / Tiled / QueueServer | ✅ BSD | 参照（checkpoint/rewind、Catalog 懒加载、可编辑队列） | M4/M7/M8/M10 |
| AiiDA | ✅ MIT | 参照（每步 ctx 持久化、provenance 双图） | M4/M7/M8 |
| ARES OS 2.0 | ✅（2026 开源） | 参照（三类模块软边界） | M8 |
| MADSci | ✅ MIT | 参照；多仪器扩展期可升为**可选后端**（Node/REST 接口同构） | M4/M8 |
| OpenLineage | ✅ Apache-2 | 参照（事件终态语义、facet 版本化）；将来可加 OL 事件**导出器**作可选后端 | M5/M8 |
| OTel GenAI semconv | ✅（规范） | 参照（仅对齐字段名，不引 SDK） | M8 |
| MLflow | ✅ Apache-2 | 参照（tag/alias 版本链、source_run_id） | M4/M9 |
| FireWorks | ✅ BSD | 参照（FWAction detour/addition 动作语义；警惕 rerun 坑） | M5/M7/M9 |
| signac | ✅ BSD | 参照（statepoint 内容寻址、条件驱动幂等续跑） | M4/M8 |
| BayBE | ✅ Apache-2 | 参照；M9 后规划器最佳**可选后端**候选（Campaign 序列化/约束 DSL 与我们同构） | M4/M7 |
| BoTorch | ✅ MIT | 参照（fantasize 为 KB 对照）；原型期明确**不集成**（pyproject 约束），后期可选后端 | M7 |
| Ax 1.0 | ✅ MIT | 参照（GenerationNode+TransitionCriterion 声明式 FSM） | M7/M8 |
| CLSLab:Light / Frugal Twin | ✅ | 参照 + M9 demo 呈现蓝本、BenchAdapter 低配蓝图 | M9/M10 |
| A-Lab 案例（ARROWS3 失败链） | 论文+部分代码 | 参照（失败→诊断→因果账→剪枝链；其缺陷反证我们的分类路由价值） | M5/M7/M9 |

### 12.1 束线/大装置实验 OS

- **Bluesky（深挖）**：① `checkpoint` Msg + **deferred-pause**——暂停推迟到最近安全重入点生效，rewind 只回放 checkpoint 之后的步骤；② suspender 的 `request_suspend(fut, pre_plan, post_plan)`——外部信号自动挂起/恢复并跑收尾动作；③ descriptor/event 两级 schema + `StreamResource/StreamDatum` 增量外部引用；④ bluesky-adaptive 反馈分级 **per-event vs per-run**（expos = per-round，对齐 per-run）。→ `loop.py`（轮内可重入检查点）、`kernel/store.py` → **M4 硬化、M7、M8**
- **Tiled（新）**：Catalog/Adapter 分层——**不打开数据文件即可答元数据/搜索**，数据请求才懒加载 + 远程切片 + 格式协商；databroker 2.0 已退位为其兼容层。→ `ReadOnlyRunView`（从索引答查询、按需读 obs）、`ui/` → **M8、M10**
- **QueueServer（新）**：RE Manager↔Worker **进程分离**（执行崩溃不拖垮管理器）、Redis 持久化 plan queue + history、**队列运行时可编辑**（增删改移位）。→ `planner/` 动作队列（可编辑持久语义）、`loop.py` → **M7**

### 12.2 计算工作流与 provenance

- **AiiDA（深挖）**：① WorkChain `outline()` **每步之间持久化 ctx**（plumpy 引擎，崩溃从最后步续跑）；② **provenance 双图**——data provenance（data+calc，严格 DAG，可复现）vs logical provenance（含 workflow 与 CALL/RETURN 链，**承认非无环**）——expos 的改判环恰好落进 logical 层的合法区；③ entry-point 插件注册。→ `loop.py`、`kernel/store.py`（事件日志二分）、DecisionRecord（CALL 链语义）、`domain.py` → **M4、M7、M8**
- **FireWorks（新，M5/M7 最直接参照）**：`FWAction` 运行时改写工作流——**detours（插入原链继承 children）vs additions（另起分支）vs mod_spec**：精确映射 REMEASURE/REPEAT_CANDIDATE/ADD_CONTROLS 的语义差异；九态状态机（含 FIZZLED/DEFUSED）；`rerun/defuse/detect_lostruns` 失败恢复。**警戒**：FireWorks rerun 不回滚动态动作的坑——expos 的 event-sourced 全量重建天然规避。→ `planner/` 动作分类、`qc→next_action`、`lifecycle` → **M5、M7、M9**
- **signac（新）**：statepoint(JSON)→哈希→workspace 的**参数即身份、幂等续跑**；signac-flow 的 pre/post 条件驱动 operation（工作流=幂等操作+条件而非显式 DAG，重跑自动跳过）。**不抄**：内容哈希当唯一主键（expos 须保留改判历史）。→ `kernel/store.py` 内容指纹、`loop.py` → **M4、M8**

### 12.3 数据血缘、可观测性、trace

- **OpenLineage（深挖）**：RunEvent 六态 START/RUNNING/COMPLETE/ABORT/FAIL/OTHER，**COMPLETE/ABORT/FAIL 为终态**（之后同 run 禁发事件，澄清走新事件）——事件语义**累积式**不覆盖；facet 命名 `{prefix}{Name}Facet` + `_schemaURL` 指向**不可变版本**（git sha）。→ `kernel/store.py` 事件词汇硬化 → **M5、M8**
- **OpenTelemetry（深挖）**：trace/span/span-event 层级映射"一轮闭环=trace、一次决策=span"；GenAI semconv 1.40（2026-04）已定名 `gen_ai.agent.id/name`、`gen_ai.tool.*`、`invoke_agent`/`execute_tool`，但仍 Development 级——**只借字段名不引依赖**。→ DecisionRecord 字段命名 → **M8**
- **MLflow（深挖）**：**2.9 起 Stage 已弃用**，改 tags（状态标注）+ aliases（可多挂、非互斥）——比互斥 stage 更贴信任路由的多标签语义；ModelVersion 携 `source_run_id` 溯源。→ 模型快照升级为轻量版本链（`model_promoted` 审计事件）→ **M4、M9**

### 12.4 实验规划与优化层

- **BayBE（深挖）**：Campaign `to_json/from_json` **整对象往返**（含测量历史 DataFrame）；**GP 超参不存、靠测量历史懒重建**——与 expos "checkpoint 以观测为真相源、模型可重拟合"的做法互为印证；约束分族类型体系（Continuous/DiscreteSum/Cardinality/Dependencies/Exclude）。→ `kernel/store.py`、`design/space.py` → **M4、M7**
- **BoTorch（深挖）**：四层职责分离 Model.posterior→Posterior→AcquisitionFunction→optimize_acqf；**Kriging Believer 的正统落点 = `fantasize()`/`condition_on_observations`（冻结超参闭式条件化）**——M7 手撸 KB 时以此为对照标定简化边界；q-batch 正统语义是联合后验 MC（逐点贪心是其下位近似）。→ `design/sampler.py` → **M7**
- **Ax 1.0（深挖）**：GenerationStrategy = `GenerationNode` + `TransitionCriterion`（如 MinTrials）的**声明式 FSM**（多边、优先级、条件全满足才跳转）——M7 的 Sobol→GP→失败感知三阶段切换照此写成规则表而非 if/else；Client 每阶段自动落盘。→ `planner/` → **M7、M8**

### 12.5 物理材料自驱动系统

- **ARES OS**（AFRL；OS 2.0 arXiv:2604.03440, 2026，开源）：最早的全自主材料研究软件层。借：hardware/analytical/planning **三类模块软边界** + protobuf/gRPC 语言无关接口 + SQL 持久状态。不抄：C#/SOA 微服务栈；其失败处理未显式建模。→ 架构对照、`adapters/base.py` → **M8**
- **MADSci（深挖）**：**campaign→experiment→workflow→step 四级层级**；Resource Manager 四类实体（labware/assets/samples/consumables）+ **lock/tree/templates** 的资源对象化。→ `kernel/objects.py`（campaign 层级预留）、`lifecycle`（资源预约）→ **M4、M8**
- **HELAO（深挖）**：sequence⊃experiment⊃action⊃driver 层级 + **SOE（sequence of events）声明式动作序**；**通信严格自上而下、driver 不可越级**——正是"agent 不直驱执行层"红线的工程先例。→ `agent/orchestrator.py` 分层守卫 → **M7、M8**
- **A-Lab 案例（本轮最重要发现）**：**失败样品完整处理链**——产率<50% 触发 → ARROWS3 低温诊断跑辨识中间相 → **pairwise 反应记入持久因果数据库**（88 条）→ 热力学再排序提新配方 → **冗余剪枝**（与既往失败同中间相则跳过，砍搜索空间最多 80%）→ 逐级升温重试。表征失败**不触发重合成**（与合成失败分路）。这是与 expos "QC→归因→再规划"最接近的同构先例，且"失败产生可复用因果知识"正是我们的核心主张；其**缺独立表征复核**（后被质疑处）恰反证 expos 把"设备/流程失败 vs 参数不可行"分类路由的差异化价值。→ `qc/attribution.py`、`planner/`（冗余剪枝）、`kernel/store.py`（失败因果账）→ **M5、M7、M9**

### 12.6 安全小型物理演示

- **自驾光学 demo**（arXiv:2603.21496 闭环激光腔装配对准 2026；BO beamline 自对准系列）：纯物理零试剂的最安全闭环样板——"可测目标子任务分解 + 物理可观测量迭代回路"。→ BenchAdapter 演进 → **M9/M10 后**
- **Frugal Twin / CLSLab:Light**（Sparks 组，Digital Discovery 2024）：**用光不用物质**的最小 SDL（RGB LED 混色逼近目标色）——"目标收敛即画面"：优化过程人眼直读。→ `ui/`、bench 低配蓝本 → **M9、M10**

### 12.7 本轮综合结论（回灌行动清单）

1. **M4 已达标但可硬化**（后续择机）：轮内状态迁移标为可重入检查点（Bluesky deferred-pause + AiiDA 每步 ctx）；
2. **M5 落地时**：事件词汇加终态语义（OpenLineage）；QC 触发动作用 FireWorks detour/addition 语义分类；
3. **M7 落地时**：动作队列=可编辑持久对象（QueueServer + FWAction）；阶段切换写成 Ax 式声明式规则表；KB 以 BoTorch fantasize 为对照；
4. **M8 落地时**：DecisionRecord 字段名对齐 OTel GenAI semconv；ReadOnlyRunView 按 Tiled Catalog 模式（索引答查询、懒加载）；
5. **M9 demo**：CLSLab "目标收敛即画面" + A-Lab 式失败样品分流时间线（每次改判显式画在时间轴上）；
6. 全部升级**零新依赖**，纯文件+JSONL 落地，与公理 1 不冲突。

---

## 13. 源码级走读笔记（第五轮，2026-07-10；仓库已 clone 至 `references/`，gitignored）

> 每条 = 机制的字段级结论 + 移植配方。完整走读报告见当轮 agent 交付（要点已全部蒸馏于此）。

### 13.1 Bluesky RunEngine / event-model / QueueServer（→ loop.py 硬化、M7 队列）

- **checkpoint/rewind 机制**（run_engine.py）：`_msg_cache` 缓存自上个 checkpoint 起的消息用于重放；`checkpoint` 处理时清空缓存=提交存档点；暂停请求只置标志、**推迟到 checkpoint 边界生效**；suspender 注入 pre/post plan 包住挂起。
- **expos 轮内检查点配方**：每轮五阶段后各追加 `phase_done` 事件（round_id, phase, produced ids）；resume 读该轮最后 phase_done、只续跑后续阶段（产物已落盘，"重放"退化为"跳过"）。**必修前置**：`save_truth` 现为 append 模式，阶段重做会重复追加——改为按 round 幂等（覆盖写）。checkpoint.json 保持轮粒度，phase 粒度只活在事件日志。
- **event-model**：父文档先生成 uid、子文档持外键（compose 工厂闭包保证一致）；schema 校验默认关是它的坑——expos 单写者量小，**默认开校验**。
- **QueueServer 队列**：`plan_queue`/`plan_history` + `_uid_dict` 去重（运行中项的 uid 仍占位防复排）；每次改动刷新队列版本戳；完成项补 `result{exit_status, run_uids, time_*}` 入 history。**expos M7 配方**：动作队列复用 events.jsonl（`action_enqueued`/`action_done` 靠 item_uid 关联），编辑=追加取消/替换事件引用旧 uid（与改判语义同构）；坑：queueserver 原子性只靠单进程锁——Slurm 多节点并发写同一 run 目录必须文件锁或分区。

### 13.2 FireWorks / AiiDA（→ M5 动作语义、事件日志分层）

- **FWAction 图语义**（firework.py L122/L1019）：**addition**=新子图不继承 children（纯扩展）；**detour**=新子图 leaf 继承父的原 children → 原 children 被门控退回 WAITING（插在父与孩子之间）；已 READY 的节点不可被 detour。
- **expos 动作队列项配方**：`QueueItem{action, semantics: detour|addition, target_obs/cand_id, params, created_by_action_id★, supersedes[]}`——REMEASURE/DISAMBIGUATION_REPEAT=detour（顶替旧判、门控归因）；ADD_CONTROLS/REPEAT_CANDIDATE=addition。★反向账：衍生 obs 回填 created_by_action_id，精确撤销/重跑——**补 FireWorks `_rerun` 不回滚动态节点的坑**（launch 无"我创建了哪些节点"的反向记录）。
- **AiiDA 检查点**：整进程 pickle 式 Bundle 存 node 属性——**别抄**（类定义耦合、不可审计）；expos 的"声明式最小 checkpoint + events 重建"是对的，坚持。
- **AiiDA provenance 两层**（links.py）：data 层（INPUT_CALC/CREATE，严格因果，只可追加）vs logical 层（INPUT_WORK/RETURN/CALL，编排裁决，可翻案）；查询按 link_type 过滤剥层。**expos 配方**：事件加 `layer: data|logical` 与 `link{type: supersedes|derived_from, target}`；审计"谁造了这个数"只遍历 data 层。**红线同构**：workflow 不得 CREATE 数据 ⇔ agent/planner 不得直写观测值。
- **M5 QC→动作接口**：纯函数 `propose_action(obs, qc, attr) -> RecommendedAction`（只读不写 store），产出带 semantics/supersedes 的队列项；agent 提案走 ACTION_PROPOSAL+配对，内生动作由 planner 直接生成。

### 13.3 MADSci / Tiled（→ 事件词表、ReadOnlyRunView 升级）

- **MADSci 事件**：关联 id 集中在嵌套 `OwnershipInfo`（非平铺）；`EventType` 扁平枚举 ~60 值前缀分层 + **描述 dict + 覆盖测试**（词表不漏描述）。资源锁=`locked_until/locked_by` 两列 TTL 租约软锁——**坑**：select→check→commit 非原子（TOCTOU），靠单调度器假设兜底；expos 单写者勿照抄。
- **Tiled 两跳懒加载**：node 元数据入索引（搜索谓词下推、不开数据文件）；`structure()` 只回元数据、`read()` 才实例化 adapter 开文件；readable_storage 路径逃逸防护值得抄。
- **expos M8 配方**：ReadOnlyRunView 从"全量加载"升级为"索引 + 懒加载"——视图持 `ObsRef{id, round, trust, path}` 清单，`observation(obs_id)` 按需读单文件（lru_cache），frozen/无写口/无 truth 不变。事件 kind 升级为 `EventKind(str, Enum)` + 描述 dict + 覆盖测试。
- **pydantic 模式**：discriminated union 判别键、`AliasChoices` 双轨别名、枚举 `_missing_` 兜底。

### 13.4 BayBE / Ax（→ checkpoint 字段集、M7 阶段 FSM）

- **BayBE Campaign**：attrs+cattrs 全对象入 JSON（含运行时计数器与 pickle+base64 的 DataFrame——**别抄**，不可审计）；RNG 状态不序列化。真相源=测量表+`_searchspace_metadata`（recommended/measured/excluded 三布尔列）。expos 的"观测为真相源+懒重建"更干净，保持；值得抄：checkpoint 加 `version` 键。
- **BayBE 约束**：ClassVar 区分**验证时机**（eval_during_creation vs modeling）+ DataFrame 级向量化过滤返回 invalid 索引——**拒绝理由可落事件日志**（哪条约束杀了哪行）。expos 约束加 `applies_at: {generation, arbitration}`。
- **Ax GenerationStrategy**：`GenerationNode` + 有序 `TransitionCriterion` 边（同边 AND、边间第一条全满足者胜、fallback 边放最后、自转移=完成）；"本节点产了几个 trial"靠数据标记懒算而非计数器；切换只改指针名、强制重 fit；快照只存 `curr_node_name`。**坑**：异常驱动控制流（DataRequiredError 当信号）——expos 用普通 if。
- **expos M7 FSM 配方**：`StageRule{name, generator, transitions: [(Criterion, to_stage)]有序}` 规则表 + 纯函数 `decide_stage(view, state)`，替换 loop 里的 `n_train >= MIN_TRAIN_FOR_BO` 硬分支；criterion 只吃 ReadOnlyRunView；切换记 `stage_changed` 事件；checkpoint 只增 `planner:{stage, entered_at_round}`；失败感知阶段=「gp 生成器 + 风险贴现 score_fn」组合，无需新节点类型。

### 13.5 esda / DoWhy（→ M5 Moran 检查、M6 反驳器；numpy 可移植配方已到手）

- **Moran's I 精确式**：z=y−ȳ（仅去均值）；行标准化 W 下 I=(zᵀWz)/(zᵀz)；EI=−1/(n−1)（n=40 时 ≈−0.026 **非零**，判显著要减 EI）。queen 邻接=网格 Chebyshev 距离 1。
- **置换检验的关键坑**：esda 默认 `p_sim` 是**折叠双尾**（directed，取 min(larger, P−larger)），其文档自认"uniformly too small, not advised"——expos 必须用**不折叠单尾 greater**：p=(#{sim≥I}+1)/(P+1)；9999 置换 → p 地板 1e-4。
- **小样本注意**：NaN 孔剔除须同步删 W 行列、在有效子集上算；岛屿单元（无邻接）lag=0 会稀释 I；置换在残差上做（先 median polish 去趋势）。
- **DoWhy 反驳器实现**：DEFAULT_NUM_SIMULATIONS=100、subset_fraction=0.8；placebo=洗 treatment 列重估、显著性检验"0 是否落在 placebo 分布内"；**坑**：n<100 时 DoWhy 切正态近似——40 观测不稳，expos 统一用经验分位。
- **移植接口（M5/M6 直接照此实现）**：`moran_check(y, grid=(6,8), P=9999, alt="greater")`；`refute_placebo(statistic_fn, labels, n=999)` PASS ⇔ p_zero>0.05 且 |placebo 均值|<0.1|obs|（效应塌零）；`refute_subsample(statistic_fn, data, frac=0.8, n=100)` PASS ⇔ p_in>0.05 且 std<0.5|obs|（效应稳定）——**两者判据方向相反，勿混**。

### 13.6 HELAO / bluesky-adaptive（→ M8 AgentBackend 接口）

- **HELAO 分层强制**：orchestrator→action server 是跨进程 HTTP（action server 无 orch 句柄，只能返回结果 + WS 单向推状态；**只有 orch 写 global_params**）；driver 被 action server 独占、orch 不可见——"agent 不直驱执行层"的工程先例。错误处理：**失败即停 + 显式重入队**（`action_dq.insert(0, A)` + `action_retry+1`），无隐式退避——expos 动作重试照此显式化。`ActionStartCondition` gate 枚举（no_wait/wait_for_all/…）可映射 M7 四类动作的执行条件。
- **bluesky-adaptive 接口**：tell/ask 已改名 **ingest/suggest**（元类向后兼容）；默认 per-run 触发（stop 文档→取 run→unpack→ingest→可选自动 suggest）。**关键差距**：`direct_to_queue=True` 时 agent 直接 item_add 进队列（违反我们的公理 7）；Adjudicator 是可选仲裁且**只留每 agent 最新一条提案（覆盖式，丢历史）、无接受/拒绝留痕**——expos 的 DecisionRecord 配对审计强于两者，**不要退化**。
- **M8 结论**：AgentBackend 采用 ingest/suggest 双方法形状（与 bluesky 现名对齐），但 suggest 产物是 DecisionRecord **返回值**（actor=agent, kind∈PROPOSAL_KINDS），由 loop 走 submit_proposal，仲裁权归 planner 的 validate_proposal——借接口形状、不借写权语义。

### 13.7 AlabOS / CLSLab:Light（→ 失败路径对照、M9 可视化配方）

- **AlabOS 关键对照结论（源码级实证）**：**没有信任路由的影子**——task_actor 用**同一个裸 except** 把一切异常收敛成 TaskStatus.ERROR；`DeviceTaskStatus.ERROR` 枚举定义了但 **grep 全库从未被写入**（vestigial）；可恢复/不可恢复判定外包给人（request_user_input）或 task 作者手写 try/except。设备故障与实验失败不分——**expos 分类路由的原创性有了源码级证据**（可写进论文 related work）。
- **值得抄**：①终态单点收敛（task_actor 是唯一终态真源）+ FINISHING 中间态 + finally 释放资源；②DAG 失败分流——ERROR 时仅剔除受影响样品、非连坐取消；③Device 的 task/pause 双轴状态解耦（操作员维护独立于任务队列）；④优先级队列 `≥100 保留给纠错/紧急重申请`（M7 动作队列可用：复测动作高优先级档）。
- **CLSLab:Light**：仪器抽象=**一个注入函数** `observe_sensor_data_fn(**params)->{channel:读数}`（构造器注入即换真机/模拟——BenchAdapter 终局蓝本）；simulation 模式是**真物理正演**（实测 LED 发射谱插值），非随机——与我们模拟器哲学一致。
- **M9 主视觉配方（直接抄）**：`np.mean(np.minimum.accumulate(obj, axis=seeds), axis=…)` 的 best-so-far 按种子平均 + std 误差带；plotly `line(color="method", error_y_mode="band")` 多臂同图——把 grid/random/bayesian 换成 naive/robust-blind/os。

### 13.8 Olympus / Atlas（→ M7 采集折扣实现、M9 轨迹格式）

- **Olympus Noise 接口**：`__call__(value)` 纯输出层、无状态、不依赖 x（源码 TODO 自认没做位置相关噪声）——我们的注入器签名 `apply(value, ctx, rng)` 正是它装不下的扩展，方法学空白在接口层再次确认。GammaNoise 的 lower_bound 保物理非负值得抄。**坑**：Noise 用 np.random 全局 RNG 无种子入口；Emulator 与 Surface 返回签名不一致靠 isinstance 分支。
- **Atlas feasibility 采集（M7 直接移植）**：`fwa` 策略 = 归一化后的 acqf × p_feas（乘法折扣），带两个关键细节——**乘法前 acqf 必须 min/max 归一化**（否则量纲失衡）+ **min-filter 把 p_feas 钳到 ≥0.5 防可行性项独裁**；`fca`=硬约束注入；`fia`=按不可行比例凸组合（"失败越多越保守"，与我们 ε 配额神似但机制不同）。**Atlas 没有的**（= expos 自研点，再次确认）：乐观置信界折扣、ε 强制探索配额、失败模式分类（它只有 NaN=infeasible 二元标签）。Golem 集成是替换回归目标而非包装 acqf。**坑**：只 np.random.seed 不 torch.manual_seed → BoTorch 采样不可复现——M9 评测要 np/伪影/layout 三 RNG 全记录。
- **M9 轨迹格式**：对齐 Olympus Campaign.to_dict 字段子集（planner_kind/goal/param_space/observations.params/values）便于复用其分析生态，但**逐轮 JSONL 追加**（弃其 pickle 全量覆写）；每条增补 arm/artifact_kind/伪影真值参数/seed 三元组/feasible。

### 13.9 botorch / plumpy（→ M7 KB 数学、lifecycle 状态语义）

- **KB 正统数学（botorch fantasize 三步）**：posterior(X, 含观测噪声) → sampler 采 Y_fantasy → condition_on_observations（gpytorch get_fantasy_model 低秩 Cholesky 增广 + deepcopy——**从不 refit，超参天然冻结**；fantasize_flag 只关形状校验，不是冻结开关）。
- **M7 最终决定**：n<300 **每步全量重分解** cholesky(K'+σ²I+jitter)——增量 Schur 补在 pending 点密集时开负根出 nan，全量更稳且成本可忽略；KB 伪观测严格取**冻结超参下的后验均值**（后验均值不变、仅方差收缩——取 μ+β·σ 就变 Constant-Liar，语义漂移）。X_pending/qEI 是 KB 的 MC 替身（改采集不改数据），走 sklearn 解析路线选 KB 不碰它。numpy 伪代码已在走读交付中。
- **plumpy 状态机**：6 态，**EXCEPTED（自身异常，携 traceback）与 KILLED（外部主动杀）严格区分**、都是终态——expos lifecycle 值得补的语义（失败 ≠ 被取消）；ALLOWED 集为空即终态的建模方式与我们 VALID_TRANSITIONS 同构。
- **检查点原则**：Savable 按**声明的字段名清单**收集快照、回调按**函数名**存 load 时反射重绑——**只存名字与数据、不存闭包/活对象**；expos checkpoint 照此纪律（现有实现已符合，写明为红线）。

### 13.10 sklearn GP 内部语义（→ 已修 M4，M5 指令）

- **正确性 finding（已修）**：`predict(return_std=True)` 的 std 含 WhiteKernel 噪声（kernel_.diag 参与预测）=**y-不确定度**；alpha 只进训练对角不进预测。UCB 用 y-std 会在高噪区把可复现噪声误当探索价值——已在 ResponseModel 加 `_f_std`（标准化域扣 noise_level·y_std² 再开根），score_pool 统一用 f-std。
- **M5 指令**：接副本方差时用**逐点 alpha 数组 + 去掉 WhiteKernel**（共存会双重计噪且异方差语义冲突）；normalize_y 不改 κ 语义（std 同域回放）；`fit(optimizer=None)+传入 fitted kernel_` = 正确的冻结重训（KB 用）；length_scale 贴上界警告是咨询性——真正影响是夹逼本身，样本多后复核 bounds。

### 13.11 Optuna / pandera（→ 事件日志多进程方案、QC 套件组织）

- **JournalStorage（events.jsonl 直系同类）**：10 个 op-code 一行 JSON + worker_id；**写侧 symlink 原子锁（NFSv2+ 最稳）+ fsync，读侧免锁**（stat 定长+行完整性校验跳半行）；30s grace 强拆死锁；trial_id 不落盘、replay 按行序分配。**expos 多进程方案（Slurm 时启用）**：抄 symlink 锁全套；seq 改 replay 分配。文件后端无 snapshot 每次全量重放——expos 的 checkpoint.json 已优于它。
- **pruned 数据去向（双模型主张的论据）**：Optuna 的 TPE **把 PRUNED trial 纳入代理模型**（排序靠后/降权），FAIL 一刀切丢弃——"坏中途数据保留并建模"是主流实证，但它是单模型软隔离；expos 双模型（值不进响应模型、事件进失败模型、可翻案）语义更强。
- **pandera lazy 聚合**：单布尔 `collect_error`（lazy=收集列表结尾打包 SchemaErrors 含 failure_cases 表/error_counts；否则首错抛）——**qc/checks.py 照此**：一个收集器贯穿三级检查、跑完全部再聚合，fail-fast 只是同一路径加短路。metaclass 三注册表对小词表过度工程，显式列表注册即可。

### 13.12 错误分类学与测试工程横向综合（→ 已落地 ExposError）

- **九仓库实证**：无一把 retryable 挂异常上——可重试性一律在状态/生命周期层（fireworks FIZZLED rank、plumpy ALLOWED 集、aiida ExitCode）。**已落地**：`expos/errors.py::ExposError(user_facing)` 基类，九错误类继承；LifecycleError/ModelError user_facing=False（内部不变量破坏，CLI 不吞）；CLI 六元组替换为基类捕获。retryable 不加（ExpStatus+Trust/Routing 已承载，避免两套真相）。
- **测试工程三模式（M5-M9 引入）**：① `tests/fake_domain/` 全家桶（学 alab fake_lab）——FakeAdapter 家族（正常/漂移/NaN/第 K 轮崩），QC/归因/规划器对确定性故障源做 e2e；② EventCollector fixture（学 bluesky DocCollector）——断言"QC→路由→动作"的**精确事件序列**而非只看终态；③ hypothesis 序列化往返 + **跑一轮后再往返**（学 BayBE/Ax）+ 同种子逐字节复现硬用例（M9 前置）。

### 13.13 IvoryOS + UI 通道设计（→ M10 终稿素材）

- **改判 override 安全通道（UI 零写句柄）**：两类干预分通道——决策级改判走 `overrides/pending/` 文件队列（轮边界消费，延迟=物理固有）；在途抢占走 `control/*.flag` touch 文件（step 边界 poll，学 IvoryOS 每-step 检查 stop_event、从不打断 step 内部）。pending 状态机：pending→processing（os.rename 原子抢占）→applied/rejected/stale/conflict；乐观并发靠 base_version==当前 version；同 record 多提案 last-writer-wins。
- **runs/ 只读契约**：三类写语义（append-only jsonl / 原子重写 / 写一次）+ **manifest.json 作 commit marker**（先写全 round 文件再 bump round_count——UI 见 N 即 N 完整可读）；UI 缓存以 (path, mtime) 为 key、只 poll manifest 当心跳。
- override 应用=追加同 record_id 的 version+1 新行带 original 快照（append-only 红线），读取按 max(version) 折叠——与内核改判语义完全同构。

---

## 14. 检出功效地板数据（实测预分析，2026-07-10；200 种子/格，FPR≈5% 统一阈值）

> 用真实模拟器（47 孔板、noise_sd=0.02）测 §11.4 最小版检测器。M5 阈值与 M9 幅度网格的实证依据。

- **关键机理**：候选间真值差（0→0.5）远超伪影乘性偏移——凡靠"全板原始值"聚合的检测器（行均值斜率、原始 MAD-z）被候选异质性淹没→**零功效**；只有**哨兵/副本配对**（去候选身份）或**独立证据通道**（曝光足迹）有功效。§11.4 的"残差上做检验"纪律由此获得实证。
- **50% 检出边界**：edge strength≈**0.11**；batch |shift|≈**0.09**；thermal 加强版（中位抛光行效应+哨兵首尾）≈**0.25**（字面版行均值 t 零功效）；glare 值通道不可达、**曝光通道 0.98 恒定**（检出由 prob 决定与 boost 无关）。
- **M5 阈值（FPR≈5% 定标）**：edge 配对差 >0.011；batch 均值差 t>0.35；glare 首选曝光计数（exposure>1.25, count≥1，FPR≈0）、MAD-z 仅副证；**梯度与漂移不设单轮硬警**——跨轮 EWMA/CUSUM 累积判警，前 2–3 轮 record-only。
- **demo 幅度判定**：edge 0.5=送分（检出 1.00，第一幕安全）；glare 0.35/prob .06=合理（走曝光通道）；drift 单轮不可辨系有意（跨轮累积）。
- **M9 网格修正**：edge 加 0.15 细化陡段（0.1–0.2）删饱和端；batch 加 0.07；**glare 改扫 prob{0.02,0.05,0.08,0.15} 而非 boost**；thermal 上探 0.3–0.5 或设为跨轮累积场景（否则曲线贴地板无区分度）。数据：scratchpad/power_analysis/results.json。

---

## 15. 经典计量学脉络：MSA / Gauge R&R / Shewhart（第六轮调研，2026-07-10）

> "测量可信度作为先于数据、必须独立证明的一等对象"的百年先驱——expos 信任路由的思想祖先与论文 related work 的第五个空白点。

- **Gauge R&R（AIAG MSA 手册）**：测量总方差分解为 repeatability（EV，设备/同条件重测）× reproducibility（AV，操作者/批次间）；%GRR=√(EV²+AV²)/总变差，验收三档 <10% 好 / 10–30% 边缘 / >30% 拒收 + NDC≥5 双判据。**expos 映射**：哨兵重复=repeatability 探针、批次维=reproducibility、p_artifact 对标 %GRR、三档可作 trust_tier；开源 `GaugeRnR`（PyPI）可做哨兵方差分解 PoC。
- **GUM 不确定度**：Type A（统计估计）/Type B（证书/规格/先验）本质等价均以方差量化——`MeasuredResult.uncertainty` 语义规范化方向（type/source/std/dof 子字段；**缺重复也能有 Type B 不确定度，不必因无副本判不可信**）。Backlog。
- **Shewhart–Deming 经典**：assignable vs chance cause（1931）= 伪影 vs 噪声的原始版本；**Deming tampering/漏斗实验**——对稳定过程过度反应反而放大方差——是"QC 税"与过度隔离的经典警示，直接引用支持 M5 的"不隔离阈值/滞后带"设计与 M9 的 QC 税指标。
- **学术定位（已核查）**：SDL 文献 2020–2026 谈可复现只是定性目标，**无人把 MSA/%GRR 验收纪律形式化进自主实验信任判定**——related work 空白点 #5（完整段落草稿见当轮 agent 交付，写论文时取用）。

---

## 16. 插件生态配方（pluggy / Home Assistant 走读，2026-07-10）

- **发现机制**：entry_points（学 pytest group "pytest11"）——四个 group：`expos.adapters`（命名单选）/`expos.domains`（配置资产）/`expos.qc_checks`（流水线全跑）/`expos.planner_stages`（命名单选）；**不引 pluggy 依赖**（我们的扩展点是单选/流水线，广播 multicall 用不上），但抄它的**注册期契约硬校验**（_verify_hook 模式：Protocol/签名不符加载期就炸）。
- **manifest**：插件包内 `expos_plugin.yaml`（pydantic extra=forbid）：name/kind/provides_metrics/required_variable_kinds/**safety_class（LAP 四档）**/expos_api_version/tier；load_domain 现有交叉校验升级为"域 yaml × 插件 manifest"对账，safety_class 超运行配置即拒载。
- **质量分级（学 HA quality_scale 三件套）**：official/community/experimental 三档——规则目录（含"无 truth 触碰守门测试"作 community 档 ratchet）× 插件自带 quality.yaml 逐条 done/exempt(带理由) × `expos-lint` CI grader（高档含低档、claimed-done 复核）；非 official 插件在 report 打水印。
- **红线守法（结构性）**：①插件只拿"内核调用它"的入口，注册表返回冻结 Mapping；②QC 插件签名只收只读快照；③**禁止插件覆盖内置名**（HA 允许 custom shadow 内置——对实验安全系统是注入通道，必须反着来）；④expos_api_version 不匹配拒载。
- **别抄**：HA async 全家桶（回合制闭环同步即可）、大而全 helpers 层（事实内核化后核心难改）、运行时 pip 自动装依赖（安全噩梦）。

---

## 17. 第七轮前沿复查：2026-01 → 2026-07 新文献（竞品扫描，2026-07-10）

> 专挖助手知识截止（2026-01）之后的半年盲区。**结论：无直接竞品，两条主张依然成立且背书更强。**

- **最接近的三个非竞品**：MADE 闭环发现基准（arXiv 2601.20996，Oxford/Gal——可组合评测但**不注入结构化偏差、无伪影/漂移**）；GIFTERS 七维可信度框架（arXiv 2512.01080，Kalinin 等——立场文/评分 taxonomy，发现文献中位仅 5/7，**没建内核机制**）；SDL 视觉异常数据集（Sci Data 2025-11——纯视觉过程异常，与数据级信任路由正交）。
- **强背书**：APL Machine Learning 2026 data-fusion perspective 明确呼吁 provenance-by-default + quality gates + 失败留元数据——**call-for-research 恰证无人构建**；Kalinin 立场文证明"自主实验信任"是公认空白。
- **M9 协议三处微调（已采纳）**：① 引 MADE 作"有基准无偏差注入"对照，强化空白叙事；② robust-blind 臂考虑纳入 multi-stage BO（arXiv 2512.15483，过程噪声）与 RCGP-UCB（arXiv 2511.15315，无界腐败）作更强 baseline——防"robust 已解决"的审稿质疑；③ 应用背书引 Artificial Coater（chemRxiv 2026-03，钙钛矿涂层 SDL 的失败涂层识别）与 Self-Supervised Instrument Calibration（arXiv 2606.29466，伪影物理来源侧）。
- LLM 编排侧：提案-校验边界已成社区共识（2025 Hackathon 总结）；artifact-aware peer review（arXiv 2605.19156）呼应"审计要看执行痕迹"。生态：MADSci 正式 JOSS 发表；Bluesky-adaptive/AISLE 半年无重大新论文。

### 17.1 四篇对标论文全文深读（第九轮，M9 论文级素材）

- **MADE（ICML，Walsh/Gal 组）**：指标 mSUN/AUDC/AF/EF（形式可借作对照写法）；5 episode×50 查询预算×每复杂度 10 系统。**关键引句到手**："inherit shared distributional biases that may simplify discovery relative to real-world settings"——MADE **自认有不可控共享偏差且列为局限**，expos 的可控注入正是其空白（比"它没做"更强的定位）。
- **RCGP-UCB**：频次有界/幅度无界腐败；P-IMQ 软剪裁权重 + 闭式加权后验；zero-cost robustness + T_c=O(T^{1/4}) sublinear。**扩展臂判定：做**——自写加权 GP + 加权 LOO 超参（sklearn 边际似然不适用），约 1-2 天，不引 GPyTorch；为 M9 补"理论保证"一格。
- **MSBO（KIT）**：级联过程噪声前向传播 + 嵌套 EI；解多保真级联而非批间不可复现。**判定：不做臂**，related work 一格对照（且其无 batch irreproducibility 讨论=又一空白佐证）。
- **GIFTERS 七维**：二元打分，63 篇中位 5/7。**expos 自评 ≈6/7**（F 公平维靠可控偏差注入拿分，强于均值）；短板=E 的"第二模型解释"严格形式、S 稳定性收敛证据——**M9 显式补多种子收敛一致性即可冲 7/7**。GIFTERS 表作 related work 定位表纵轴。
- **M9 定位表已成稿**（行=四篇+expos，列=偏差注入/信任路由/失败归因/可组合性/理论保证——expos 独占前三列，借 RCGP-UCB 臂补第五列）。完整表见第九轮 agent 交付。

---

## 18. 八族平台参考总表（第八轮，2026-07-10；四路 Opus 源码级；完整六栏表见各 agent 交付，此处存精华与处置）

> 30+ 仓库全部 clone 至 references/。§13 已录项不重复。内核不变量重申：两对象 + DecisionRecord 载荷 + RunStore + 信任路由——**没有任何外部项目动摇它**。

### 18.1 各族精华与处置

- **族1 采集引擎（+queueserver-api）**：读逻辑单点收敛（API_Base 集中、transport 只发请求）→ CLI/未来 HTTP 共用 ReadOnlyRunView 词表【M10 设计笔记】；UID 门控缓存（status 携版本戳、变了才拉全量）→ UI 轮询用事件计数作版本戳【M10】；**item_uid 服务端分配**——提案侧不带 uid、落盘时统一分配，防伪造关联【M7 立即，一行纪律】。
- **族2 工作流（+workgraph/signac/signac-flow）**：动态图**防失控界**（max_depth/max_jobs）→ 动作队列加每轮 detour 生成上限，防"归因→复测→再可疑"自激增殖【M7 立即】；error handler 的 retry_count/max_retries 门控【M7 立即】；signac 内容寻址 id==hash 校验/repair → `expos check` 对账命令【M10】；**flow 条件式 DAG："完成"是产物谓词不是执行断言**→ 见 §18.2 洞见；Slurm 扫描 run 确定性命名防重跑【M9，入 M9_PROTOCOL】。
- **族3 血缘/遥测（+OpenLineage/semconv/mlflow 源码）**：最小 facet 契约（只需 _producer+_schemaURL）→ OL 导出器一个自定义 RunFacet 就够【M8 设计笔记】；**semconv 词表治理**——每词条带 stability+requirement_level+生成文档同步检查【M5/M8 采纳进 EventKind 治理】；mlflow alias=指针文件（模型版本链落地形态）【M9】；两个**治理信号**：OTel 把 gen-ai 拆独立仓、mlflow FileStore 进维护模式——快变 spec 与稳定引擎分仓，且文件后端正是上游腾退的生态位（expos 反做一等支持=差异化）。
- **族4 优化 campaign（+optunahub）**：**git-repo-as-registry 插件市场**（registry=普通 git 仓、ref 钉 SHA、sparse-checkout 拉取）——版本=git SHA 直接落事件日志，"哪个插件版本产了这条观测"血缘天生可查【backlog；红线：远端加载必须签名/allowlist——optunahub 裸执行任意 ref 是反例】；质量阶梯=模板基类+编号 recipe+官方仓才统计。
- **族5 插件生态（+pytest/HA-architecture）**：pytest 的单 spec 模块+deny-list 屏蔽；**ADR 采纳**（HA architecture 仓：四段式、顺序编号、supersede 不删文件、~3 篇/年）→ 建 `docs/adr/`，首批回填 DEEP_REVIEW 三项已决取舍【立即-文档】；**产出 docs/PLUGIN_API_DRAFT.md**（157 行，post-M10 草案）。
- **族6 校验/配置（+pydantic/hydra）**：manifest 判别联合复用现有 `_Cfg(extra=forbid)` 基类（红线一处定义全局继承）；**hydra 判不采纳**——先并集 merge 后 set_struct，文件内拼错键被静默收下，与"未知键响亮失败"红线结构性冲突；现有 DomainConfig 一行 extra=forbid 已优于其默认。
- **族7 UI/文档壳（+streamlit/dash/mkdocs 源码）**：`st.fragment(run_every)` 局部无闪轮询【M10 立即】；AppTest 可测交互（补 selectbox/multiselect 交互测试）【M10】；Dash 迁移四触发条件清单（现均不满足，留 Streamlit）；文档站三升级：mkdocstrings API 文档、awesome-nav 自动导航、mike 版本化【backlog】。
- **族8 可靠性（+hypothesis/pre-commit/ruff 源码）**：**RuleBasedStateMachine stateful 测试**内核状态机（Bundle+consumes 精确建模"提案→一次性裁定"，三 invariant：配对完备/改判留痕/状态合法——Rule 草图已备）【整合期立即】；pre-commit local hook 落裸 except 棘轮【立即】；**RUF 全角标点地雷**——中文仓启用 RUF 前必配 allowed-confusables【立即】。

### 18.2 本轮四个新洞见（此前七轮未提）（接 §19 十二族）

1. **事件×产物双向对账**（族2）：resume 的"完成"应由**产物谓词**（文件存在且指纹与事件记载一致）× 事件断言联合裁定——事件只是索引、产物是真相源；不一致报 CorruptedRun 而非静默续跑（顺带修压测 finding D）。同一组谓词驱动 `expos check`。
2. **spec 与引擎分仓**（族3）：三个最像 expos 的上游不约而同在"快变规范/稳定运行时"间划界——事件词表、facet schema、插件 manifest 都应独立于内核版本演化。
3. **git-SHA 即插件血缘**（族4）：市场分发与 provenance 合流成同一个 git-ref 事实——零基建、天然可审计。
4. **平台体验跃迁最小步**（族7/8）：st.fragment 局部轮询 + stateful 内核测试——两个各半天的改动，分别是 UI 与测试面的"平台级"分水岭。

---

## 19. 十二族平台架构参考（第十轮，2026-07-10；四路 Opus；三份规范文档已产出）

> 完整六栏表见各 agent 交付；此处存处置结论。产出物：docs/{CAPABILITY_MODEL,RUN_MANIFEST_SPEC,EVENT_SCHEMA}.md（均为规范草案，post-M10 实施；现行强制以 OS_PRINCIPLES 为准）。

### 19.1 处置精华（按族）

- **K8s**：spec/status 分离——exp.status 只许 lifecycle 写【M5 已同构，收敛写权为整合项】；**level-triggered 幂等**——action_consumed 判据应为"是否已产生对应设计"而非边沿消费【M7 设计笔记】；reconcile 幂等=resume 重算等价【M6】。
- **Temporal**：determinism 纪律（禁裸 random/now）与 derive_seed 同类【已同构】；**重放校验**（events 重算 vs checkpoint 比对，偏移响亮失败）【backlog】；retry policy 五字段照搬 QueueItem【M7 立即】；heartbeat→长执行 adapter【post-M10】。
- **systemd**：Wants/Requires/After 强弱与次序正交→域-adapter 依赖声明【M8】；Restart 枚举+StartLimit 熔断→动作重试分类【M7】；**active/failed/degraded 三态健康入 checkpoint**【M6 立即】；journalctl -u→events 按对象过滤【M5】。
- **ROS2**：action 的 goal/feedback/result/cancel 生命周期=长执行 adapter 蓝本；QoS 可靠性分级=事件投递语义【post-M10】。
- **VS Code/浏览器**：contributes/activationEvents 分离；default-deny 权限清单 + unsafe 水印→CAPABILITY_MODEL 已落；运行时动态申请**不采纳**（回合制需加载期静态可判定）。
- **PG**：WAL 先行=我们"先事件后检查点"【已同构确认】；MVCC 版本共存=override version 链【已同构】；catalog version 不符拒运行→manifest_version 语义。
- **Git**：产物 merkle 化（逐文件 sha256 上卷单根）=事件×产物对账载体【M9】；tag=alias 指针文件【已同构】；blame 走链归因"哪轮引入"。
- **Nix**：输入闭包显式罗列（域哈希+代码版本+种子三元+依赖指纹）→RUN_MANIFEST_SPEC 三档已落；flake.lock→plugins.lock（git-SHA）。
- **数据面**：**M9 不需要 Parquet**——DuckDB read_json_auto 直查 jsonl（万级行）；导出仅在十万行级/对外发布时；Parquet 加列安全/改名破坏=ABI 纪律佐证。
- **编译器**：verifier 思想=每阶段边界跑不变量校验（完成性谓词的编译器版）【M6/M7 整合】；结构化 diagnostics 佐证 ExposError kind 维。
- **包管理**：checkpoint_version（整数,userspace ABI）×expos_api_version（MAJOR.MINOR,major 不符拒载）×插件 api_version 三版本矩阵；DeprecationWarning→obsolete→removed 跨一 minor 宽限。
- **合成建议（K8s+Temporal+systemd）**：resume 硬化三件套=**level 幂等重放 + 重放校验 + 三态健康**——phase_done 阶段化落地时的验收标准。

### 19.2 第十轮新洞见

1. **level-triggered 是 resume 的正确心智模型**：从"续消费增量"转向"按当前全量状态收敛"——失败模型 rebuild 已是此模式，推广到动作消费与派生态。
2. **三版本矩阵**是兼容性治理的最小完备集（run ABI / 内核 API / 插件 API 各一轴）。
3. **饱和态归因限制**（M6 实测）：>50% 板面污染时归因歧义化——"检测饱和易、归因有幅度窗口"成为 M9 新指标（归因精度-幅度曲线，附录 B）；几何混叠（批次×分层奇偶）是系统性误差源，已以棋盘格批次修复并列入 domain lint backlog。

## 20. 第十一轮：试点两 finding 的文献定标（2026-07-10；三路网络核实，正式引用前建议 find-paper 复核原文）

### 20.1 弱幅度档 OS 反输 → "软降权、不排除"有强文献支撑

M9 试点实锤：温和边缘蒸发（strength 0.2）档 os（硬隔离）末轮 regret 反高于 naive；强档（0.5）os 完胜。文献定标：

- **Robust BO/GP 前沿一致走软降权**：RCGP 家族全系连续权重不剔点（Altamirano ICML 2024；RCaGP NeurIPS 2025 arXiv:2505.21133 并指出近似 surrogate 上硬剔除会耦合放大偏差——正解释弱档失效）；最强单点支撑 **Tempered Posteriors BO**（arXiv:2601.07094，2026-01）：逐点自适应 α 降权、不丢样本、regret 界更紧——其"误差超模型不确定性→调低 α、校准恢复→回 1"机制即 suspicion→alpha 的理论化身。
- **SPC 侧定量复现本现象**：ML-TAE（Sci Rep 2025, doi:10.1038/s41598-025-33073-9）——软加权对小/中偏移（δ≤2.0）零收益甚至拖慢检出，仅强污染占优；Deming 漏斗实验给出经典解释（弱伪影≈共因噪声，据此干预=tampering 放大方差）。**含义：os-soft 臂在弱档可能也不赢——这本身是可发表的校准结论，预注册判据见 SOFT_TRUST_PROPOSAL。**
- **诚实负结果是文献空白**：SDL 文献尚无"QC 在弱伪影下适得其反"的直接报告；最接近 ORNL"协同优化噪声而非过滤"（Digital Discovery 2025, arXiv:2410.02717）；2026 同类 QC+AL 工作（arXiv:2603.29135）恰用硬排除且未做软硬对比，可作对照立论。

### 20.2 污染度量 → 双列报告是文献标准

Huber ε-污染模型的 ε 一致按**生成/注入标签**定义（Huber 1964；Chen-Gao-Ren EJS 2016；Diakonikolas-Kane 2023），无"偏差过阈比例"定义；RCGP/robust BO 实验协议按注入比例报 corruption level（RCGP-UCB 明确"按频率不按幅度"）。已修：`injected_in_training`（标签，判是否注入）与 `contaminated_in_training`（|bias|>3σ 绝对偏差，判注入是否有效）双列（scoring.py；量纲 bug：旧版相对偏差 vs 绝对 τ，零伪影场景虚高 ~0.72）。

### 20.3 2026H1 竞品复查（新颖性保住）

结构化偏差注入 benchmark 无直接竞品。需正面区分的近邻：**LAP**（arXiv:2606.03755，仪器凭证/签名层门禁 vs 我们逐观测连续信任路由）；**AL 工作流批判综述**（arXiv:2601.05946，诊断不造 benchmark）；**LLM 多智能体失败归因族**（2606.02060/2606.03467/2602.23701，软件轨迹 vs 物理测量 provenance）；Experiment-as-Code（2605.04375，编排可复现不做测量信任）。

## 21. 第十二轮：平台级系统家族深读（2026-07-10；六路 Opus 并行；族报告见 scratchpad，专属文档四份已落地）

> 各族五栏全表见 scratchpad/family{1..6}_*.md；此处存一览、可执行处置与增量。专属文档同轮已由各族 agent 落地，本节仅引用不复述。

### 21.1 六族一览

| 族 | 深读仓（source 级为主） | 最尖锐洞见（一句） | 专属文档去向 |
|---|---|---|---|
| 1 kernel spine | bluesky · plumpy · optuna（source）+ temporal · k8s（docs） | 三种 durable-execution 恰覆盖三个正交轴（optuna=事件重放 · plumpy=状态快照 · temporal=事件溯源+determinism），expos 的 events.jsonl+checkpoint.json 站在单机最小完备交点 | **CONTROLLER_MODEL.md（新，12 不变量）** + EVENT_SCHEMA §0.1 |
| 2 plugin | pluggy · home-assistant · vscode | HA 63 条质量规则仅 7 条有 validator、其余 56 条信任式 claimed-done；expos **反着抄**——机器判据须复核 claimed-done，无 validator 只加 taint 水印、不给「填个 done 就抬 tier」的通道 | **PLUGIN_API_DRAFT.md v2 增补**（四 hook 契约 + 三档 grader） |
| 3 storage | postgres · OpenLineage · mlflow · nix · git | 只有 checkpoint 路径是 log-before-data；advance_status/route_observation/reclassify 三条改判路径全是 **data-before-log**，故 events.jsonl 是只增审计日志、非严格 WAL（视图领先日志的单步偏斜） | **RUN_MANIFEST_SPEC.md 增补**（§2.1 环境指纹 / §7 registry / 附录 A OL 映射 / §6 WAL 诚实边界） |
| 4 optimization | Ax · botorch · baybe（source） | 逐点噪声膨胀、点永不删是业界**现行唯一**官方鲁棒方案（botorch 删除 HeteroskedasticGP=噪声GP路线反例），直接背书 SOFT_TRUST；RCGP 固定 0.75 分位阈 vs botorch 数据驱动 LOO 是实质短板 | 无专属文档；佐证 SOFT_TRUST_PROPOSAL / robust_gp.py（design-note） |
| 5 validation | pandera · pydantic · hypothesis · pre-commit · ruff | 「软/硬」开关应放在**检查声明处**而非消费处；综合五仓产出 `expos-lint` 22 条规则清单（error/warn/preview 三档，XT/XA/XS/XB/XL/XH 领域前缀） | 无专属文档；expos-lint 草案见族5报告 §6 |
| 6 dataplane | duckdb · arrow · streamlit · mkdocs-material | duckdb `read_json_auto` 直查现有 JSONL/JSON，一行 SQL 0.35s 聚合 532 个 score.json，完胜 compare.py 的 arm×seed×rounds 三重循环，**无需先建 Parquet** | 无专属文档；design-note（新 `eval/query.py`） |

### 21.2 本轮新增的可执行处置（合并优先级清单）

- **P1 纪律/低成本（多数本轮已落文档）**：① 显式 `seq` 的**并发正确性必要性**——optuna 位置游标能成立仅因后端锁串成单一全序，多进程写同 journal（§13.11）位置计数必错位，seq 不可退化（已写 EVENT_SCHEMA §0.1）；② item_uid 服务端落盘分配（M7 一行纪律，前轮 immediate 本轮 source 确认）。
- **P2 M9/整合期**：③ **WAL 改判路径翻转**——三条 data-before-log 路径（advance_status/route_observation/reclassify）M9 抬真 WAL 时须统一为 append_event 先行；在此之前 `expos check` 须**容忍此单步偏斜、响亮记 note、不误判 CorruptedRun**（RUN_MANIFEST_SPEC §6）；④ **duckdb 汇总替换 python 三重循环**——新 `expos/eval/query.py` 薄封装 + 标准 SQL（arm 对比/逐轮 regret/QC 税），可选依赖、不入热路径。
- **P3 design-note（post-M10）**：⑤ **Ax 转换判据组合子**——StageRule FSM 升级为「目标节点键控的 AND-束边 + 边间按序 OR」DAG，并拆出 `PausingRule`（`consecutive_high_suspect_rounds` 应映射为 pause 而非 advance）；⑥ **botorch LOO 离群判据**（`loo_error²−loo_var` 闭式）替换 RCGP 固定 0.75 残差分位阈 L（expos 已有 `_weighted_loo_score` 管道，选择器是小增量）；⑦ **expos-lint 规则清单**——8 条 error 先行（XT/XA/XS/XL 红线机器化，XS001/XS002 从 ratchet 平移）+ warn 双向棘轮 + preview 灰度，redirect 表第一天建。
- **P4 backlog**：⑧ `read_events` **torn-tail 容错**——events.jsonl 尾部半写行现抛 JSONDecodeError，照 optuna `_file.py` 范式（末行失败且无后继即按截断丢弃）容忍崩溃残留（post-M10，配合事件×产物对账裁定 CorruptedRun）；⑨ **KB 过自信**——`select_batch_kb` 均值插入把 pending 处方差压到 ~0=批内欠探索，可折中为「增广后验协方差上的解析联合 UCB」修正，不必上全 MC-qEI。

### 21.3 与既有 §18/§19 的增量（诚实说明）

- **重复确认（source 级坐实前轮 docs 级结论）**：K8s spec/status 分离与 level-triggered 幂等、Temporal determinism、PG「先事件后检查点」、duckdb 免 Parquet、item_uid 服务端分配——§18/§19 已录，本轮由 bluesky/plumpy/optuna/Ax/botorch/baybe/duckdb **源码逐文件**核实并背书，无翻案。
- **真新增（本轮首次）**：① 三种 durable-execution 正交轴的收敛表述 → 新建 CONTROLLER_MODEL.md 的 12 条可审计不变量；② optuna 位置游标 vs 显式 seq 的**并发正确性论证**（从设计偏好升级为必要条件）；③ WAL 完备性的**诚实边界**（三条改判路径 data-before-log，非严格 WAL）；④ botorch LOO 数据驱动离群选择替换 RCGP 固定分位阈；⑤ HA 信任式 claimed-done 的**反向采纳**（复核机器判据、无 validator 只加 taint）；⑥ expos-lint 22 条规则清单。

---

## 22. 终局定向 pass（2026-07-10；八参照五栏收敛表）

> 用户裁决：只这 8 个参照、五栏提取、不开新方向、不改 runtime 代码，clone 服务 M9/M10 收尾硬化。
> 六旧行（optuna/plumpy/pluggy/OpenLineage/duckdb/temporal）本轮**不重复劳动**——只给核对结论一句话 + 落地指针（源码级深读见 §13/§18/§19/§21 与 scratchpad/family{1,2,3,6}_*.md）。
> 两新行（bluesky/event-model、controller-runtime）为本 pass 唯一新深读，完整提取。仓库均已 clone 至 `references/`（gitignored；controller-runtime 为 Go 仓只读不跑）。
> 硬不变量守恒（六条全过）：两持久对象未增（conditions 是 observed-status 的病历式补充、非第三真相源）；run_start/run_stop 是**事件**非新对象；truth 决策模块不可见、agent 仅提案、插件不绕信任路由、无静默回退（TerminalError 恰是"响亮失败并记账"的背书）——均不动。

| 参照 | 借什么 | 不抄什么 | 对应 expos 文件/文档 | 处置 · 里程碑 |
|---|---|---|---|---|
| **optuna**（旧·核对） | 核对结论：JournalStorage op-code 重放 + 尾部容错 + 「位置游标能成立仅因后端锁串成单一全序」→ 显式 `seq` 是并发正确性**必要条件**，非偏好。结论不变，无翻案。 | 位置游标当 seq；文件后端无 snapshot 全量重放 | EVENT_SCHEMA §0.1；CONTROLLER_MODEL §3；store.py `append_event` | 已落 design-note；torn-tail 容错=**backlog**（post-M10，P4） |
| **plumpy**（旧·核对） | 核对结论：checkpoint 只存**名字 + 纯数据**、runtime-only 对象 load 时重建（不变量⑦′）；EXCEPTED(自异常) 与 KILLED(外杀) 严格分终态。既有实现已同构。 | 整进程 pickle Bundle（类定义耦合、不可审计） | CONTROLLER_MODEL 不变量⑦′；§13.9；lifecycle 状态语义 | design-note（追认既有实现）· post-M10 |
| **pluggy**（旧·核对） | 核对结论：**不引依赖**（扩展点是单选/流水线，广播 multicall 用不上）；只抄注册期契约硬校验（`_verify_hook` 式签名/Protocol 加载期即炸）。 | multicall 广播；插件覆盖内置名（安全注入通道，反着来禁止） | PLUGIN_API_DRAFT.md；§16 | design-note（草案已落）· post-M10 |
| **OpenLineage**（旧·核对） | 核对结论：RunEvent START/COMPLETE/FAIL **终态累积不覆盖**、facet `_schemaURL` 指不可变版本 = 改判追加不翻历史。既有事件词表已吸收。 | 数据管道术语体系；整套 facet 目录 | EVENT_SCHEMA §2/§3 终态语义；RUN_MANIFEST_SPEC 附录 A（OL 映射） | design-note；OL 导出器=可选后端 backlog · M8/post-M10 |
| **duckdb**（旧·核对） | 核对结论：`read_json_auto` 一行 SQL 直查现有 JSONL/JSON（万级行 0.35s），**免建 Parquet**；替换 compare.py 三重循环。 | 把 Parquet 拉进热路径；DuckDB 进内核依赖（仅 eval 可选依赖） | 新 `expos/eval/query.py`（薄封装 + 标准 SQL） | **immediate-ready design-note**（P2，M9 整合期，可选依赖不入热路径） |
| **temporal**（旧·核对） | 核对结论：event-sourcing + replay-determinism + workflow/activity 二分（loop=workflow 确定可重放、adapter=activity 可非确定造 truth）。determinism 已由 derive_seed 同构落地。 | 云工作流调度/部署栈；heartbeat 重试栈（长任务才需） | CONTROLLER_MODEL §3/§4/§6；不变量⑧ | design-note（追认）；重放校验 + BenchAdapter heartbeat=**backlog** · post-M10 |
| **bluesky/event-model**（新·完整提取） | ① **四文档词表**：`run_start`(必填仅 `time`+`uid`，其余全为自由 metadata，extra=allow)→`descriptor`(必填 `data_keys`+`run_start`+`time`+`uid`，每个 data_key 声明 dtype/units/shape/limits)→`event`(必填 `descriptor`FK+`seq_num`+`uid`，自身无 schema)→`run_stop`(必填 `exit_status`∈{success,abort,fail}+`run_start`FK+`time`+`uid`+`num_events`)。② **metadata-first 纪律**：run 上松（metadata 不进 schema 约束），data 上严（Event.data extra=forbid、逐 key dtype 先声明）。③ **descriptor 先行于数据**：每 stream 一份 descriptor，schema 只写一次，event 只带 FK+值，读者自描述无需外部字典。④ **jsonschema 校验器组织**：pydantic basemodels 生成冻结 JSON schema → 运行时 `schema_validators[DocumentNames.X]`(Draft202012，重定义 array type_checker 容 numpy) 在 `compose_*`/`emit` 时校验，`validate=True` **默认开**(opt-out)。⑤ **compose 一致性**：`compose_run` 先生成 parent uid、返回持父 doc 引用的工厂闭包→子文档 FK 天然一致（= expos item_uid/decision_id 落盘前分配、§18 已同构）。 | 全 12 类文档（datum/stream_* 外部引用对两对象内核偏繁——RawDataRef 已取其「两级 schema+外部引用」精华，§4）；DocumentRouter/Filler/RunRouter 分发栈（回合制单写者用不上） | EVENT_SCHEMA.md（新增 §5 run_start/run_stop 缺口 + descriptor 借鉴 + validate↔EXP010 互补）；ObservationObject.raw_ref | **design-note**：events.jsonl 缺「run 级 start/stop 文档」——config.json+末尾 checkpoint 是**散装等价物**、无 `exit_status` 终态枚举、无 provably-last 收口事件。补 `run_start`/`run_stop` kind（testing）是 M10 收尾最小硬化，与 OpenLineage 终态语义合流 · **M10** |
| **kubernetes-sigs/controller-runtime**（新·完整提取） | ① **reconcile 签名**：`Reconcile(ctx, Request)→(Result,error)`；Request **只含 NamespacedName**（身份，不含变更内容/事件）→ 强制重读全量状态 = **level-based**（源码注释明写"action isn't driven off changes in individual Events, but by actual cluster state"）——**源码级坐实** CONTROLLER_MODEL 不变量③。② **Result 语义**：`RequeueAfter>0`→按时长重排；`Requeue bool` 已 **Deprecated**（注释："ratelimiter requeue 造成困惑，应用显式时长/poll 间隔"）——直接映射 M7 动作重竞：延迟/未物化动作不应盲目 requeue，而应在**轮边界对全量状态重新仲裁**（action_skipped 留痕胜过隐式重排）。③ **TerminalError**：不重试但仍 log+记 metrics 的第三类错误 = expos ExposError 响亮失败并记账（retryability 在生命周期层非异常层，§13.12 已同构）。④ **status.conditions 词表**(metav1.Condition)：`{Type, Status∈{True,False,Unknown}, ObservedGeneration, LastTransitionTime, Reason(机器 CamelCase 枚举), Message(人类自由文本)}` **数组共存**——正交健康轴各带独立迁移时刻+原因；Reason 机器/Message 人类的二分与 expos「message 永不入 ABI」同构。⑤ **finalizer**：终态删除**门控于 keyed 幂等清理证毕**，清理失败可挂 condition（不静默）。 | manager/leader-election（单机单写者不借，不变量⑩）；predicate 事件过滤/watch 循环（expos 非常驻协调、回合制单写者——GenerationChangedPredicate 的 spec/status 写分离思想已由不变量⑪承载，但 predicate 机制本身**不借**）；finalizer 的 deletion-grace 机器（回合制无需） | CONTROLLER_MODEL.md（新增：exp.conditions[] 病历式补充 + RequeueAfter↔重竞 + finalizer↔CLOSED 收口守卫） | **design-note**：conditions 数组给 ExpStatus 线性枚举补「非主线状态」病历（QC_DEGRADED/RESUME_HEALED/ARTIFACT_SUSPECTED），不扰主状态机、只设计不实现；RequeueAfter 反例强化 M7「不盲 requeue、轮边界重仲裁」纪律 · **post-M10**（conditions）/ **M7**（重竞纪律追认） |

**本 pass 三条新增落地**（均 ≤20 行/处，不改 runtime 代码）：
1. EVENT_SCHEMA.md §5：run_start/run_stop 终态文档缺口（config.json+checkpoint = 散装 start/stop、缺 exit_status 枚举与收口事件）+ descriptor 先行思想对 obs schema 的可借 + event-model `validate`(默认开、逐文档结构校验) 与 expos-lint EXP010（CI 词表漂移守门）的互补分工。
2. CONTROLLER_MODEL.md：§1 补 conditions 病历式补充（post-M10 设计）、§6 补 RequeueAfter↔动作重竞与 finalizer↔CLOSED 收口。
3. 本表（§22）。

**M9/M10 收尾相关性小结**：两新参照唯一进入**当前收尾范围**的是 event-model 的 run_start/run_stop 终态文档——events.jsonl 至今无「provably-first run-open / provably-last run-close(exit_status)」事件，M10 补两个 testing kind 即闭合（与既有 OpenLineage 终态语义、manifest commit-marker 合流）。其余（conditions 病历、BenchAdapter heartbeat、重放校验、OL 导出器、torn-tail 容错）全部 post-M10 backlog，本 pass 不开新方向。

## 23. M11 内核加固：OS 本体架构参照（收编 O3-D 交接走读，2026-07-11）

> **收编声明**：本节六行由审查方 R3 参照路 **O3-D** 交接文档提供并经我方（有写权方）决定收编入权威索引。
> 来源文档：`/Data1/ericyang/r3_os_references/M11_HANDOFF_O3D.md`（含 file:line 参照与验证提示）；
> 源码 cite 均可在该目录下的浅克隆/稀疏检出仓复核：`vscode/`（src/vs/workbench/services/extensions）·
> `kubernetes/`（pkg/kubelet/prober + pkg/probe）· `otp/`（lib/stdlib/src）· `sqlite/`（src）· `redis/`（全仓）。
> 三条已落地本批（M11）：机制活性 **grade 三态**（建议 1+收紧 1）、**失活预算熔断**（建议 2）、**`expos check --fix`** 尾损自愈（建议 3+收紧 3）。

| 参照 | 借什么（file:line） | 不抄什么 | 对应 expos 文件/文档 | 处置 · 里程碑 |
|---|---|---|---|---|
| **VS Code** extension host | ① 每次激活发观测事件 `onWillActivateByEvent`（`abstractExtensionService.ts:76-77,509,1046`）与机制活性同形；② 宿主异常退出只停宿主不拖窗口（`:889-895`）；③ 重启熔断 `_CRASH_LIMIT=3`/「3 次·5 分钟」达阈停自动重启转人工（`:922-924,1568-1586`）；④ responsive/unresponsive 双态监测（`:855`） | 「声明了但从未激活」是**正常惰性语义**——与 EXP011（注册必发射）方向相反，只作反差立论 | `eval/activity_budget.py` 失活预算（CrashTracker 3/5 双参照）；未来 `mechanisms.py` 注册表 | **已落地**（activity_budget 熔断）· M11 / EXP011 · post-M11 |
| **Kubernetes** kubelet 探针 | ① `Result` 枚举含 **Warning**（"logically success, with debug info"，`probe.go:22-30`）——黄牌现成同构；② 处置分级：liveness 失败→重启、readiness 失败→仅摘流量、startup 门控（`worker.go:373-391`）；③ 去抖 Failure/SuccessThreshold；④ 探测/处置解耦：worker 只 `Set()` 进缓存经 channel 推送、从不直接动容器（`results_manager.go:27-47`） | 探针面向运行态存活/就绪，expos 面向决策生效证据——类比背书非机械移植 | `grade` 三态（absent/warning/active，EVENT_SCHEMA §6）；loop.py `_grade_*` 发射/裁决解耦 | **已落地**（grade 三态 + 解耦）· M11 |
| **Erlang/OTP** supervisor | ① 4 restart 策略；② `transient`（仅异常退出才重启、正常退出不动，`supervisor.erl:214-222`）呼应「干净轮恒等不硬崩」；③ 熔断 intensity/period 滑窗，窗内超限→supervisor 自身 terminate 向上逃逸（`:109-112`；`add_restart:2260-2281`、`can_restart:2283-2290`） | OTP 是进程级监督，expos 无常驻子进程；「重启失活机制」在回合制里对应下一轮重算/收敛 | `eval/activity_budget.py` `budget_breached`（intensity/period 滑窗，默认 3/5） | **已落地**（失活预算滑窗）· M11 |
| **SQLite** WAL | ① 逐帧 salt+累积 checksum 判有效（`wal.c:46-84`）；② 尾部自愈只读到最后一个 commit 帧收尾的帧（mxFrame，`:104-111,328`）其后残帧忽略；③ 恢复扫描 `walIndexRecover:1390-1530` 遇第一个无效帧即 break、**不跳帧打捞**；④ `mxFrame ≥ nBackfillAttempted` 佐证水位不倒退 | 页级二进制 WAL、随机改写；events.jsonl 行级 append-only 文本——累积校验简化为行级独立解析 | store.py `scan_events_tail`（水位=最后有效记录 byte/line，遇第一个坏行即停） | **已落地**（尾损诊断结构约束）· M11 |
| **Redis** AOF | ① 恢复水位 `valid_up_to`=最后完整命令 offset（`aof.c:1512`）；② 四档 OK/TRUNCATED/BROKEN_RECOVERED/FAILED（`:1738,1757`）；③ 两类尾损分治：干净短读可愈 vs 格式损坏仅小于阈值才愈、大损坏响亮失败（`:1748-1760`）；④ 记录级原子回退（MULTI 中途 EOF 整体回退，`:1708-1717`）=尾部半条 JSON 整条丢弃 | Redis 须回放重建内存态；expos 只需审计日志尾自愈——③在 jsonl 退化为「整行 JSON 解析失败」 | store.py `scan_events_tail`/`truncate_events_tail`；`expos check --fix` | **已落地**（尾部自愈骨架）· M11 |
| **Redis** check-aof 工具 | ① 诊断先行且双坐标：修前打印 `size/ok_up_to/ok_up_to_line/diff`（字节偏移+**行号**，`redis-check-aof.c:302`）；② 三重防误截：无 --fix 只诊断 exit(1)（`:326-328`）、--fix 交互确认 `[y/N]` 默认 N（`:312-317`）、只许截末文件（结构约束，`:306-309`）；③ 四档退出码（`:38-41`） | 交互确认在 CI 需 `--yes` 旁路（Redis 无、我方自补） | `cli.py` `cmd_check`（四档退出码 0/1/3/2、默认诊断 exit 1、--fix 交互默认 N、--yes 旁路） | **已落地**（check --fix UX 与防误截纪律）· M11 |

## 24. M16/M17 参照总合订（第十三轮，2026-07-12；主笔 bf315d15，B 复核转正 2026-07-12）

> **定位**：本节是**索引的索引 + 裁定汇总**——把两会话十六件 INDEX（A 侧 `r4_os_references/` 九件：`INDEX.md`+M16{WET,DRY,AGENT}+M17{STATS,CLAIMS,TRUTHTEST,METAMORPHIC}+M18_LLM_READY；B 侧 `m16_references/` REF-{W,P,K}、`m17_references/` REF-{S,C,B,I}）通读后合订。不复述各件正文（引用见各 INDEX 落款），只裁定汇总 + 标注跨件矛盾/重复（这是合订的独有价值，集中在 §24.4）。
> **约定**：本节措辞中性；每条带来源 INDEX 与落点工段；状态 ∈ {已落=有 file:line／施工图=有草案代码或字段清单／清单=仅字段/接口形状}。

### 24.0 库总览表

| 目录 | 定位 | 仓数 | 文献数 | 体量 | 落款期 | 索引件 |
|---|---|---|---|---|---|---|
| `/Data1/ericyang/r4_os_references/` | A 侧主库（平台+决策+M16/M17/M18 全线+文献层） | 64 | 51 | 1.9G | 2026-07-11 → 07-12（13 轮增量） | INDEX + M16×3 + M17×4 + M18 |
| `/Data1/ericyang/m16_references/` | B 侧 M16 波（wet/protocol/knowledge 三线） | 13 | 0（代码层） | 607M | 2026-07-08 → 07-12 | REF-{W,P,K} |
| `/Data1/ericyang/m17_references/` | B 侧 M17 波（stats/claim/belief/instrument 四线） | 14 | ~9（散于仓内） | 205M | 2026-07-12 | REF-{S,C,B,I} |
> 合计 ≈ 91 仓（含 opentrons/pylabrobot/nanopub/hypothesis 跨库重复克隆，见 §24.4 重复项）/ ~60 篇文献 / ≈2.7G。文献层集中于 A 侧；B 侧为代码级机制走读。

### 24.1 已裁定采纳表（按里程碑分组）

**M17 Evidence-to-Claim Compiler**
| 结论（一句话） | 来源 INDEX | 落点 | 状态 |
|---|---|---|---|
| ClaimDecision 内核 = 有界指标上 betting/经验-Bernstein **置信序列 + e-process**（supported⇔CS 排除零且 e≥1/α） | A·M17_STATS Q1 | K-B 轮内聚合器 | 施工图 |
| expos 现有精确置换检验经 `PToECalibrator` **一次闭式变换即 e 值化**并入（迁移成本≈零） | A·M17_STATS Q1 | eval/stats_tests | 施工图 |
| **insufficient 数据自适应判据**（CS 仍含零 ∧ e 未越阈；高噪单孔自动 insufficient），非 n<阈值拍定；与「未成裁决 norm_score=None 类型隔离」「ESS 预门」三支收敛 | A·M17_STATS Q2 / TRUTHTEST / REF-C S1 / REF-B B9 | K3 诚实语义 | 施工图 |
| **两轴正交 claim 形态**：版本链（supersede 双向，跨轮演进）× 证据列表（source_hash+epistemics，轮内多孔聚合） | A·M17_CLAIMS Q1 / REF-C N1 | ClaimDelta schema | 施工图 |
| **声明式合法性门**（SHACL 形态：约束表=数据+机读违规报告，实现走 jsonschema/pydantic，规则纳指纹）；判定留 decision_fn | A·M17_CLAIMS Q4 | 第七元 Certification Policy | 清单 |
| **supersede 授权门可机读**：`e_new≥1/α ∧ e_new/e_old≥R ∧ 同 filtration`；insufficient 结构性无 supersede 权 | A·M17_STATS Q3 | ledger supersede | 清单 |
| ClaimDelta = 单一 `derived_from(new,old)` 基边 + `kind` 枚举 + **prov 五元**(new,old,activity,usage,generation)；退役独立 invalidation 事件；effective_status 为纯派生函数 | REF-C P1/P2/P3/N3 | K-A 账本 | 施工图 |
| **stat_snapshot 单条自足记录**（statistic/df/p/CI/effect±se/achieved_power/evidence_factor/independence_assumed/seed）；BF 型连续证据量作 evidence_strength 底座（存连续、暴露离散带） | REF-S §Conv / REF-C Q2 | ClaimDecision 载荷 | 清单 |
| K1/K2 二值断言升级为 **D1-D3 分布判据**：D1 双场景裁决分布 C2ST 可分性 / D2 随机化真值面负控（permutation p，无信号须 insufficient）/ D3 双条件停机+CI 收窄 | A·M17_TRUTHTEST | K-E 测试套 | 施工图 |
| **K2 五合取断言集**（前置非退化 / 输入侧内省 / 指纹演进(两跑都要) / 输出侧差分反向 / 晋升层穿透）——缺一即被装饰性实现攻破 | REF-B §Conv(a) | K2 判别器 | 施工图 |
| **强度合成 = 精度当量线性相加 + 阈值预门**（弱证据不推翻强结论由算术自动涌现，非第三条规则） | REF-B B8/B9 | K3 近亲/supersede | 清单 |

**v1.1 架构演进（M16 真机就绪 + DRY 第二引擎）**
| 结论 | 来源 INDEX | 落点 | 状态 |
|---|---|---|---|
| **真机腿 = 换 RecoveryPolicy 策略对象 + 增 `AWAITING_RECOVERY` 态**，不写第二套驱动；未登记码/undefined 一律 fail-closed | A·M16_WET Q2 / REF-W headline / REF-I I-6 | W4 wet Adapter | 清单 |
| **labware 外置化**：照 Opentrons `ordering+wells` JSON Schema 把 96 孔硬编码抽成数据合同，校准偏移另存 keyed 记录 | A·M16_WET Q4 / REF-W | design/labware | 清单 |
| **机读错误分类学四支判别式**（VALIDATION/DEFINED/UNDEFINED/TRANSPORT）；码表不含 recoverable 位、恢复性是运行时策略输出；~70 码/4 类/前缀分段 | REF-I I-1/I-5/I-6 | `failure_detail()` ABI | 清单 |
| **QCSchema 引擎缝薄投影**（to_atomic_input/from_atomic_result），不动内部 ABI；绑 M17 引擎#2 落地而非提前 | A·M16_DRY Q1 | adapters/dry 引擎池 | 清单 |
| **幂等窄切片**：`spec_sha`-keyed collect 短路（崩溃续跑去重），借 AiiDA finished+sealed 门防半写命中；config 默认关 | A·M16_DRY Q3 | W1 scheduler | 清单 |
| 引擎 = 泛化 DryAdapter 后的可换 compute-core（pyscf\|xtb\|psi4）+ `implemented_metrics` 能力声明（ASE 同构）；接 TIMEOUT-keyed 有界重试 | A·M16_DRY Q2/Q4 | adapters/dry | 清单 |
| 能力 = 结构（ABC+chatterbox 钉可替换性）+ 机读清单（可规划）双层 | A·M16_WET Q3 | CAPABILITY_MODEL | 清单 |

**M18 LLM 后端接入**
| 结论 | 来源 INDEX | 落点 | 状态 |
|---|---|---|---|
| **LLM 接入九件清单**（ProposalSchema / LLMBackend / 测试替身 / G1 分布式判别器 / usage 块+必填 / 预算门 / provider 死亡降级 / prompt 溯源 / 模型无关红线测试） | A·M18 §7 | agent 插件位 | 清单 |
| 路线取 **instructor 式校验重试**（非 outlines 受限生成）：模型无关红线排除 logit 掩码硬保证；重试在模型侧 reask、发生在铸造 DecisionRecord 之前 | A·M18 Q1 | backends_llm | 清单 |
| G1 LLM 化 = **断言强度降级非重写**：知识面 bit-exact 原样保留（fps/promoted 纯函数），提案面「相等→分布可分离」；保留 TemplateBackend 金丝雀 | A·M18 Q2 | test_w8 判别器 | 施工图 |
| **usage 进事件溯源**（DecisionRecord.content 嵌 usage 块 + `EVENT_PAYLOAD_REQUIRED` 注册必填）→ token/延迟/成本成审计一等公民 + 预算门素材 | A·M18 Q4 | store.py/objects.py | 清单 |

### 24.2 已裁定不采纳表（防未来重议）

| 方案 | 不采纳理由（一句话） | 证据锚 |
|---|---|---|
| INDRA **belief 主分数**（noisy-OR） | 独立性假设在相关多孔场景系统性高估置信；连续分数无 insufficient 出口（与 K3 红线冲突） | A·M17_CLAIMS Q2 / REF-B |
| INDRA **一 claim 一证据列表原地追加折成一个分数** | 历史丢失，无法回答「轮 3 裁决是哪几条 obs 推的」，违 K4 provenance | A·M17_CLAIMS Q1 |
| INDRA **belief=1 钝覆盖**（人工一句话拍死分数） | 无痕通道；须留 decision_fn 复算痕 | A·M17_CLAIMS Q3 |
| outlines **受限生成**（logit 掩码合法 by construction） | 只对本地开源权重成立；经 LiteLLM 路由到闭源 API 即蒸发（Anthropic 无 output_type）；且有 10-15% 格式约束税 | A·M18 Q1 |
| **穷举 MR 化 / MR 自动生成**（gemtest 执行器 / MR-Coupler） | 最小完备 MR 子集 = Set-Cover NP-hard；自动合成只对代码结构 MR 有效、对语义 MR（真值面翻转）不对口 | A·M17_METAMORPHIC Q4 |
| AiiDA/QCFractal **全量内容寻址缓存**（图/DB/输出克隆） | jobflow 反例证明工作流无去重亦生产可用；expos 跨轮同 spec 主要出现在崩溃续跑（已被 reconcile+确定性目录覆盖） | A·M16_DRY Q3 |
| **纯 post-hoc power 作闸门 / 纯 n<k 硬门限** | post-hoc power 与 p 值单调、信息冗余；n<k 武断丢弃方差/效应量信息 | REF-S §Conv(b) |
| **固定阈值 BF 三分作随停判据**（expan `bf>3/<1/3`） | 固定阈值 BF 停时非 anytime-valid，落回可选停止谬误；须走 e-process/test-martingale（见 §24.4 张力①） | A·M17_STATS Q1 vs REF-S |
| 引整库依赖：statsmodels / ufloat 自动传播 / pystan-MCMC / RDF-SHACL 全栈 / gemtest 运行时 | 各自的核心公式/形态几十行自持；决定论 OS 用不上其随机/重路径；只借形态学 | REF-S/REF-B/A·CLAIMS |
| event-model **全 12 类文档 + DocumentRouter 分发栈** | datum/stream_* 外部引用对两对象内核偏繁；回合制单写者用不上分发栈（只取 descriptor 先行 + 终态枚举） | §22 / A·INDEX |
| **pandas.equals 懒比较 / 全局态种子** | expos append-only ledger+fingerprint 链强于懒比较；run manifest 钉死 seed，勿退化 | REF-B B4/B14 |

### 24.3 expos 领先项汇总（对外叙事素材集中页）

| 领先项 | 一行 | 证据锚 |
|---|---|---|
| 五平台整块领先 | MADSci/ChemOS/NIMS-OS/AlabOS/AG 决策栈**全部缺** trust 裁决内核 + 失败资产化 + claim ledger 三层 | A·INDEX §2.1-2.5 |
| agent 生效侧零反例 | 走读六框架+bluesky-adaptive 无一做到结构性「提案≠生效」分离；expos 三件套（模块无写句柄 / actor 日志层硬 raise / 提案-裁定配对审计）独有 | A·M16_AGENT Q2 |
| goal 层双坐实 | LabOP 与 autoprotocol 均止于「作者手写算子 AST」，声明式 goal→op 编译是 expos 差异点（两件独立确认） | A·M16_WET Q1 / REF-W |
| 比较面投影器 π | 通用 MT 框架默认「SUT 输出即比较面」缺声明位；expos `_decision_plane` 使决定论比较精确到逐位（非 `approximately`）——相对通用 MT 的可宣称增量 | A·M17_METAMORPHIC Q3 |
| MR 注册表判别性 | 四条支配性蜕变关系（恒等/反转/置换不变/通道分离）形式化为 `{name,τ,R,π,锚点}`，每行防一个具名历史 bug（W7 挂 R3 P0） | A·M17_METAMORPHIC Q2 |
| 内核裁决语义领先 | decision_fn 按名注册禁闭包 / refuting 支配 effective_status / override 理由与 ledger 共位 / fingerprint 输入序不变性——较 INDRA belief scorer 更可审计（do NOT regress） | A·M17_CLAIMS §3 / REF-K |
| 内容寻址身份 | 内容哈希身份（vs jobflow random UUID）+ 自动 compiler_source_sha（vs dagster 手写 code_version）；文件租约原子发布杀 TOCTOU | REF-P #5 / REF-K |
| 决定论重放口径 | run manifest 钉死 seed/RNG 使重放自足（BayBE seed 游离于 campaign JSON 外、Atlas 只 seed np——皆不如） | REF-B B14 |

### 24.4 开放命题（下波审查/研究选题）+ **跨 INDEX 矛盾/重复标注**

**A. 跨件张力（需下波裁定统一，合订独有发现）**
1. **insufficient/停时内核选型张力**：A·M17_STATS 坚持 e-process/betting-CS（anytime-valid，随停有效）；REF-S 推 expan 固定阈值 BF 三分（`bf>3/<1/3`）——后者停时**非** anytime-valid，正是 STATS 明确警告的可选停止谬误。e 值与 BF 有似然比桥（`EToPCalibrator`），但**固定阈值** BF 需升级为 test-martingale 才相容。**待裁**：K-B 内核统一到 e 值标度，BF 仅作离散展示带。
2. **supersede 门机制的五重形式化未收敛**：REF-K「same-decision_fn」（身份门）/ REF-C「权威等级≥原裁决」（特权门）/ A·M17_STATS「e_new/e_old≥R」（证据强度门）/ REF-B「精度合成方向翻转占比过半」（精度门）/ A·M17_CLAIMS「SHACL 声明式跨字段序关系」（形态门）——五个会话给同一门五种判据。**待裁**：授权层（谁有权）与证据强度层（够不够强）应是**合取**而非择一，声明式门承载序关系、e 值/精度承载强度。
3. **belief 负裁定 vs BF 正采纳的边界**：A·CLAIMS 否 belief 主分数，REF-S/REF-B 采 BF/精度作 evidence_strength——二者相容（否的是 noisy-OR 独立性聚合，取的是单假设 BF/精度当量），但**须在 schema 层明确 evidence_factor 非 belief**，防重议时混淆。

**B. 纯前瞻命题**
4. **filtration 相容性**（`2402.09698`）：跨轮/跨批次合并 e 值时 filtration 是否始终相容，否则合并证据悄悄失效——待验命题。
5. **跨 campaign 知识聚合**：DRY Q3 广义内容寻址缓存、CLAIMS 跨代 claim 聚合均推迟——M17 后选题。
6. **多假设在线 FDR 时机**：ClaimDecision 现在留 e 值出口 + snapshot 钩子，e-LOND/stopped-e-BH 接入时机待定（R4-I F3 教训前置，防返工）。
7. **证据级 override**（CLAIMS Q3 缺件）：现只能整条 claim 判，不能「轮 2 第 3 孔那条 obs 作废」——K1 在线路径引入证据列表后应补。

**C. 重复/冗余（维护提示）**
8. **重复克隆**：opentrons/pylabrobot（A 侧非 git 工作副本无 SHA + m16 git 克隆有 SHA，~700M 冗余）、nanopub（REF-K + REF-C + A·CLAIMS 三处走读，均声明不重复克隆——协调良好但结论三处并存）、hypothesis（A·INDEX 附录 + METAMORPHIC 两透镜）。三处 nanopub「supersede 双向链」结论、三处 opentrons「换策略不换驱动」结论为**三重独立印证**（非矛盾），可作稳健性背书。

### 24.5 维护纪律（Boy Scout 规则）

- 新参照波入库即在 §24.1/§24.2 补行（一条结论一行 + 来源 INDEX + 落点 + 状态），负裁定同样入 §24.2 防未来重议；跨件张力入 §24.4·A。
- §24 是「索引的索引」：只增裁定汇总，不复述各 INDEX 正文；正文级机制走读留在各件，file:line 均可回溯。
- 本节由主笔会话 bf315d15 起草，B 会话（2dd8db70）复核转正于 2026-07-12（red_to_blue/081：三处张力裁定随复核落账，五重计数笔误已修）。
