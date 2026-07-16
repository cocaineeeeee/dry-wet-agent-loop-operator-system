# CHECKPOINTS —— 构建检查点台账

> 活文档。每完成一个里程碑，更新对应条目：状态、日期、验证命令与输出摘要、偏离蓝图的决策。
> 里程碑定义与验收标准：`docs/BUILD_PLAN.md`；架构蓝图：`docs/ARCHITECTURE.md`；外部参考：`docs/REFERENCE_MAP.md`。
> 状态取值：`pending` / `in_progress` / `done` / `blocked`。

## 规划期记录

- **2026-07-10 规划 v1**：完成 ARCHITECTURE/BUILD_PLAN/CHECKPOINTS 三份文件（10 里程碑版）。
- **2026-07-10 规划 v2（架构补丁）**：应需求新增 Agent Orchestrator 层（建议权/无裁决权、DecisionRecord 入事件日志、ReadOnlyRunView + ProposalQueue 结构性守门）；里程碑重编号为 M0–M10（新增 M8 agent 层）；完成六路并行 web 调研并产出 `docs/REFERENCE_MAP.md`（2025–2026 前沿：MADSci、Ax 1.0、BayBE 0.15、CARE、PROV-AGENT、LAP、Anubis 等）。调研关键结论已回灌架构：DecisionRecord 对齐 PROV-AGENT/OTel GenAI 命名；归因引擎采纳 DoWhy 式反驳器合同；空间检查用置换检验；provenance 修订不覆盖历史（OpenLineage facet 模式）。
- **2026-07-10 规划 v3（一致性审计）**：对四份文档做 10 条不变量审计（两内核对象 / DecisionRecord 仅为事件载荷 / agent 仅建议权 / ReadOnlyRunView+ProposalQueue / 提案须有 acceptance-rejection 配对 / trusted→响应模型、suspect/failed→失败模型 / truth sidecar 隔离 / 换域零内核改动 / 双层检查点纪律 / 非生物措辞）。修复 4 处：①§10.1 职责表第 5 条原写 agent 经 `store.append_decision` 直写事件日志，与"无写句柄"矛盾——改为 agent 只返回 DecisionRecord、由 loop 统一落盘；②失败模型语义歧义——明确正例仅 SUSPECT/FAILED（含被隔离者，由 trust 裁决驱动、与 routing 正交），TRUSTED 仅作曝险分母且不带响应值，模型从事件日志按当前裁决全量重建（改判自动生效）；③truth sidecar 边界收紧——写明生成方仅 `adapters/sim_*`，loop 只做不透明透传，qc/models/planner/agent 禁读；④ReadOnlyRunView 归属歧义——定义并导出于 kernel/store.py，agent/views.py 只含 ProposalQueue（依赖方向 agent→kernel 永不反向）。措辞扫描（生物/生医/临床等关键词 grep）仅命中三处"非生物"自我声明，通过。

## 总览

| # | 里程碑 | 状态 | 完成日期 | 验证 |
|---|---|---|---|---|
| M0 | 项目骨架 | done | 2026-07-10 | import OK；pytest 30 passed |
| M1 | Kernel（两个 schema + store + lifecycle） | done | 2026-07-10 | 30 passed + Opus 对抗审查过 |
| M2 | Design 层（space/sampler/layout/budget） | done | 2026-07-10 | 27+30=57 passed；三路 agent 把关过 |
| M3 | Adapter 层（模拟器 + 伪影 + ingest + 双域 YAML） | done | 2026-07-10 | 31 passed、全量 88；三路 agent 把关过 |
| M4 | 响应模型 + naive 闭环端到端 | done | 2026-07-10 | 全量 141 passed；三路把关+修复复验通过 |
| M5 | QC 三级检查 + 信任路由接线 | done | 2026-07-10 | os 模式拒斥假最优；QC 税 0%/2.5%；十套件 147+15 passed |
| M6 | 归因引擎 + 失败模型 + 路由接线 | done | 2026-07-10 | 归因 21+失败模型 19+policy 16+联合 e2e 4 绿；两次对抗审查修复 |
| M7 | 失败感知规划器 | done | 2026-07-10 | stages 16+arbiter 31+kb 7 绿；候选容量二次封顶等审查修复 |
| M8 | Agent Orchestrator 层 | done | 2026-07-10 | agent 55+M8 e2e 5+状态机全绿；提案-裁定配对机器强制 |
| M9 | naive vs OS 对比实验（关键 demo） | done | 2026-07-10 | 1450 格五臂零失败；~~H1~~/H3 过；检出/归因曲线+三诚实 finding【H1 判定经 R1 压测更正：预注册判据下不成立，记 deviation——见下方"压测更正记录"】 |
| M10 | Streamlit UI + 文档收尾 | done | 2026-07-10 | 最终全量 431 passed；UI 14 测+lint 31 测+CLI 16 测+demo 2 测+鲁棒 8 测；mkdocs strict 净 |

- **2026-07-10 规划 v4（深读与源码走读 + 深度思考）**：第四轮"研究型 OS 深读"（六类系统×指定焦点，REFERENCE_MAP §12 + §12.0 采用方式裁定表）与第五轮"源码级走读"（10 仓库 clone 至 references/，§13.1–§13.8：Bluesky 检查点配方、FireWorks 动作语义、Tiled 懒加载、Ax 阶段 FSM、esda/DoWhy 可移植统计配方、HELAO/adaptive 接口对照、AlabOS 无信任路由的源码级实证、Olympus/Atlas 采集折扣实现）。完成 **docs/DEEP_REVIEW.md** 深度审视：收窄两条差异化主张、识别三大有效性威胁（自证循环/稻草人基线/QC 税）；据此修订 BUILD_PLAN——M9 升级三臂对比+四项诚实性指标、M5 加 QC 税 ≤5% 验收线与 VerdictPolicy 零分支红线、新增 Backlog 节。M5/M6/M9 设计文档（M5_DESIGN/M6_DESIGN/M9_PROTOCOL）由 Opus agent 并行起草。

## 检查点记录

### M0 项目骨架
- 状态：**done**（2026-07-10）
- 验证命令：`python -c "import expos"` && `pytest -q`
- 输出摘要：`import expos OK, version 0.1.0`；`pytest -q` → **30 passed in 0.32s**（0 失败 0 报错）。
- 决策/偏离：① 根目录加 `conftest.py` 使 pytest 免安装即可 `import expos`；② 全部子包（design/adapters/ingest/qc/models/planner/agent）以带 docstring 的空 `__init__.py` 占位，agent 包 docstring 写明结构性边界供守门测试锚定；③ streamlit 以 `--user` 装好（列为 optional-dependency `ui`）。

### M1 Kernel
- 状态：**done**（2026-07-10）
- 验证命令：`pytest tests/test_kernel.py -q`
- 输出摘要：**30 passed in 0.19s**。覆盖：两对象+DecisionRecord 序列化往返（含 verdict 满载往返）、schema 一致性守卫（二选一/is_control/log 变量）、store 重开往返、事件 append-only 与 seq 续接、checkpoint 原子写往返、状态机合法链与非法跳转、裁决表 7 例（含阈值等号与显式 suspicion 分支）、路由落盘+事件、无 QC 拒绝路由、改判保留历史+OVERRIDE 落账、提案 acceptance/rejection 配对、非提案类拒绝裁定、**裁决权强制**（agent 裁定/改判被拒；伪造 agent-acceptance 不被采信；planner 静默翻盘被拒、human override 留 conflict 事件）、trust 过滤、无 .tmp 残留、守门（agent 包无写 API；ReadOnlyRunView frozen/无 truth/无写方法）。
- 决策/偏离：实现后经 **Opus 对抗审查 agent** 复核，修复其全部 findings：① 高危×2——`validate_proposal`/`reclassify` 原不限 actor，agent 可自裁定/自翻案；现仅 `ADJUDICATOR_ACTORS={planner,human}` 有裁决权，且 `_resolutions` 在日志层按 actor 过滤（伪造记录不采信）——把公理 7 从"agent 无句柄"的带外假设升级为日志上可机器检查；② 中危——同一提案二次裁定仅 human 可 override 且落 `resolution_conflict` 事件；对象写全部改 tmp+`os.replace` 原子写；③ 低危——store 声明单写者模型；checkpoint 改"先事件后文件"（崩溃偏斜方向保守）。schema 与 ARCHITECTURE §4 逐字段比对无实质偏差（`ObservationObject.created_at` 为无害新增）。审查提出的 6 个测试盲区全部补测（24→30）。

### M2 Design 层
- 状态：**done**（2026-07-10）
- 验证命令：`pytest tests/test_design.py -q` && `pytest -q`
- 输出摘要：**tests/test_design.py 27 passed in 2.97s；全量 57 passed**。覆盖：单位立方三类变量往返（含端点与 clip 语义）、log 下界拒绝、非法 params 四类拒绝、约束三类拒绝 + **约束引用缺失变量响亮失败**、Sobol 种子确定性、约束感知采样、不可满足约束响亮失败、BO 占位打分 + 最小距离去重（Sobol 与 BO 两路径）+ BO 确定性、哨兵固定四角+中心、副本跨区组（k<4、k=4、k>4 round-robin 三分支）、溢出哨兵、无 hint 分层交替断言、placement_hint 两种、伪 risk_map 避让 + 未知键拒绝、逐区组耗尽响亮失败、布局确定性与 seed 记录、预算超支/轮次耗尽/负量响亮失败且失败不记账、charge_layout、依赖隔离（文本扫描 + import 图双保险）。
- 三路 agent 把关（应用户要求加大 agent 投入）：
  - **Opus 对抗审查**：红线 1 项——`check_constraints` 对拼错/缺失变量静默判真（安全上限会永不生效）→ 已修为 `_require` 响亮失败；中危 2 项——逐区组容量精检不完整（属响亮失败非静默降级，改进错误消息并在 ARCHITECTURE §5 注明限制）、risk_map 未知键静默忽略 → 已改为 LayoutError；低危——Sobol 批量非 2 幂告警已修、edge_center_pair 同组软回退保留（记录在案）。
  - **Opus 随机化压力测试**（实跑）：往返模糊 12000/12000、非法输入 200/200、采样确定性 50/50、**布局全域扫描 8640 组合零 invariant 违规且 4192 例失败全部为干净 LayoutError（零外来异常）**、风险避让占用率比均匀基线低 ~500×、预算模糊 7116 次操作失败前后状态一致。唯一分歧项 = risk_map 未知键抛错，系审查修复后的**有意行为**，拍板保留（无静默降级纪律）。
  - **Sonnet 合规比对**：28 项无 ❌；6 项 ⚠️ 处置——LHS 未实现（需求为"Sobol **或** LHS"，Sobol 满足，记录在案）；Sobol 路径 min_dist、分层交替、k 边界等测试盲区已全部补测；接口命名漂移已按"先改文档"纪律同步 ARCHITECTURE §5（`spend_wells/start_round/charge_layout`、`propose_candidates(score_fn)` 占位、LayoutPlanner 参数序）；"所有添加必过预算"的结构性强制落在 M4 loop/M7 planner 接线（蓝图已注明）。
- 决策/偏离：① 原计划由 Opus agent 实现 layout.py，该 agent 因 API 基建错误（ConnectionRefused）中断，改由主对话实现——子 agent 未修改仓库任何文件，所有权纪律未破；② ARCHITECTURE §5 接口命名与实现对齐（见上）；③ risk_map 未知键语义定为响亮失败。
- 附：同日完成第二轮深挖调研（REFERENCE_MAP §11）：M4 批量 BO 默认配置（Kriging Believer + κ 调度 + 逐点 alpha）、M3 结晶真值面物理形式（CNT/Nývlt/Kubota-Mullin，工程近似已标注）、M3 注入器幅度先例与 M9 指标体系；并二次独立核查确认"结构化系统偏差注入基准"仍无公开先例。

### M3 Adapter 层
- 状态：**done**（2026-07-10）
- 验证命令：`pytest tests/test_adapters.py -q` && `pytest -q`
- 输出摘要：**tests/test_adapters.py 31 passed in 1.57s；全量 88 passed**。覆盖：协议形状与非变异（两域）、crystal/coating 真值面内部最优、哨兵带标定复核、六注入器方向与代数（含 AR(1) 乱序拒绝/跨轮重置）、真值-测量分离、truth 产地约束（源扫描）、raw 无真值字段、secondary 签名 params-only 守门、CSV/图像 ingest（PENDING、无裁决、PIL/RGB 路径、空前景、重复 well_id 拒绝）、worklist/板图、域热插拔（同一泛型路径参数化两域）、四类响亮失败、依赖隔离。
- 三路 agent 把关：
  - **Opus 对抗审查**：**公理级 finding 1 项**——coating 的 `secondary["coverage"]=0.5+0.4·true_value` 构成可反解的真值 oracle（OS 可从 secondary 精确还原真值）→ 结构性修复：`secondary(params)` 签名去掉 true_value 参数（类型层面杜绝泄漏）+ 签名守门测试；中危 2 项——secondary/exposure 无噪声成完美 1-bit oracle（QC 检出会退化成查表）→ 统一叠加 5%/3% 测量噪声；批次分配与 capture_index 同为单调阶梯（批次效应与时间漂移结构性混淆、QC 不可辨识）→ 改为 idx%n 轮转解耦；低危 2 项——加载期补 metric/required_params 交叉校验；sim_crystal 的 CV-Nývlt 标注降级为"定性依据已核实、形式工程标定"。判定为合法的：exposure=1.5/grain_count×3 是 QC 证据而非作弊通道（不泄真值）；AR(1) 每轮新实例重置是特性非 bug（campaign 级漂移弱化已记录）。
  - **Opus 随机化压力测试**（实跑 3460+ 例）：执行确定性 40/40、真值-测量守恒 42/42、注入器代数 3208 例（命中率 |偏差|≤5%）、真值面双域 8192 点值域干净且最优点稳定、ingest 模糊 100/100、图像模糊 37/37、域配置破坏 30/30 全 DomainError。findings 2 项已修：CSV 重复 well_id 原静默透传 → 现拒绝；`true_value` 公开面对越界参数原静默返回 → 现加物理有效域守卫（缺键/类型/越界全 AdapterError）。
  - **Sonnet 合规比对**：需求 9 大项全 ✅、0 ❌；2 ⚠️ 已处置——sim_base.py 偏离登记（见下）；crystal.yaml 默认场景只挂 3/6 注入器为**有意设计**：demo 剧本需要干净的"第 3 轮边缘蒸发假最优"事件，六种全挂会让归因演示混杂（六注入器的完备验证在单测与压力测试层完成）。
- 决策/偏离：① **新增 `adapters/sim_base.py`**（不在原 M3 文件清单）：模拟器共享基座——执行链"真值→噪声→注入→RawResult+truth"集中一处，crystal/coating 只提供 `true_value/secondary`，这是"注入器框架域无关"的结构性证明；ARCHITECTURE §6 与 BUILD_PLAN M3 行已同步。② bench+ingest 四文件由 Opus 实现 agent 交付（自检通过、无所有权外改动），本轮 layout 型基建全部主对话实现。③ 第三轮深挖调研回灌 REFERENCE_MAP §11.4（M5 小样本 QC 统计配置）与 §11.5（M7 失败感知规划器设计）。

### M4 响应模型 + naive 闭环
- 状态：**done**（2026-07-10）
- 验证命令：`python scripts/run_loop.py --domain crystal --mode naive --rounds 4 --seed 7 --out runs/m4_naive` && `pytest -q`
- 输出摘要：CLI 4 轮跑通（188 观测全 TRUSTED、run 目录八件套齐全、每轮模型指纹互异；naive 基线 best=1.007 出现在第 3 轮强边缘蒸发场景且超过真值面上限 1.0——**naive 正在追逐伪影假最优，demo 的种子已埋好**）。全量 `pytest -q` → **141 passed**（后台独立复跑同数）。修复复验（Opus agent）：resume 等价性两种子逐字段成立、62-run seed 扫描零异常（修复前 seed=13 必崩现正常）、CLI 三例干净 exit 2、truth 幂等。
- 三路把关 + 修复：
  - **Opus 对抗审查**：中危 2——snapshot 对重复行不满足行序无关（副本/哨兵正是重复 X 行）→ 改 (X,y) 联合排序哈希；批量策略用 UCB+min_dist 偏离 §11.1 的 Kriging Believer 未备案 → 已在 ARCHITECTURE §8 备案（KB 留 M7）；低危——exp.budget 快照滞后一轮已修、WhiteKernel 下界 1e-4（§11.1 建议 1e-3）为**有意偏离**（域噪声方差 4e-4 会被 1e-3 顶在边界）。
  - **Opus 压力测试**：finding A（κ 调度绑 CLI rounds → resume 非等价）→ 改绑域配置 budget.rounds_total（campaign 视界）；finding B（seed=13 BO 轮 LayoutError 崩溃）→ LayoutPlanner 区组负载均衡修复（优先级 (risk, -区组剩余, 洗牌序)，500-seed 扫描零擱淺、风险避让零回归）；finding C/E（DomainError/BudgetError 泄漏裸 traceback）→ ExposError 基类统一（见下）；finding D（手删观测文件后静默续跑）→ 记入 Backlog（完整性校验）。
  - **Sonnet 合规比对**：全 ✅、0 ❌（fit 签名差异判定语义等价）。
- 追加修复（源码走读驱动）：① **f-std 修复**（sklearn 走读：predict std 含 WhiteKernel 噪声=y-不确定度，UCB 在高噪区被误导 → score_pool 改用扣噪 f-std，加测试）；② `save_truth` append→按轮幂等覆盖（Bluesky 走读：阶段重做会双份追加）；③ **ExposError 公共基类**（expos/errors.py，user_facing 语义：领域错误干净 exit 2、内部不变量破坏保留 traceback——九仓库错误分类学实证：retryable 不挂异常层）。
- 决策/偏离：① n_restarts=10（§11.1 建议 ~20，闭环内每轮重训速度折中）；② 批量 KB 留 M7；③ WhiteKernel 下界 1e-4；④ 新增 expos/errors.py 模块（横向综合建议）；⑤ uuid/时间戳不可复现判定为不破坏"同种子可复现"主张（科学字段与指纹逐字段可复现）。
- 附：本里程碑期间完成第 4–6 轮调研与源码走读（REFERENCE_MAP §12–§16：研究型 OS 深读、10+ 仓库源码级走读、MSA 经典脉络）、docs/DEEP_REVIEW.md 深度审视、docs/PLATFORM_VISION.md 平台愿景、M5/M6/M9 设计文档（经对抗审查修正 v2）、检出功效地板数据（§14）、README 双语、mkdocs 文档站、CLI v2 规格、插件生态配方（§16）；M5–M8 可并行模块（stats/checks/policy/failure_model/stages/agent 骨架/UI 首版）由 Opus agent 群并行实现中——整合接线待 M5 检查点。

### M5 QC 三级检查 + 信任路由接线
- 状态：**done**（2026-07-10）
- 验证命令：`pytest tests/test_qc_stats.py tests/test_qc_checks.py tests/test_qc_policy.py tests/test_loop_os.py -q` && os 模式端到端
- 输出摘要：**核心里程碑时刻——信任路由第一次在闭环里生效**：同 seed=7 下 naive 追逐第 3 轮边缘伪影假最优（best=1.007，超真值面上限 1.0）；os 臂第 3 轮 edge_effect 命中 24 孔 + 哨兵控制带 2 孔、33 观测隔离、best_trusted=0.630 物理合理，**假最优被拒**。零伪影 QC 税：闭环级 **0%**（141 观测）、板级 20 种子平均 **2.5%** ≤5% 验收线。测试：stats 27 + checks 14 + policy/kernel 47 + os 端到端 8 + e2e 15，十套件合计 147+15 全绿（歸因 3 敗屬 M6 在途 agent 自有測試）。
- 交付：`qc/stats.py`（11 个纯 numpy 原语，含 esda 折叠双尾坑规避、DoWhy 判据）、`qc/checks.py`（三级 13 项检查、lazy 收集器、PlateContext 导出、冷启动纪律）、`qc/policy.py`（VerdictPolicy/AggregationPolicy 双协议 + Naive/QC 裁决 + Passthrough/Median/ReplicateVariance 三聚合）、loop 双策略注入点接线（`_policies_for_mode` 是唯一 mode 判定点，主体零分支——测试断言）、ResponseModel 逐点 alpha 通道（去 WhiteKernel + normalize_y 缩放校正，§13.10 指令）、CLI 开通 `--mode os`。
- 决策/偏离：① §14 绝对阈值（edge 0.011/batch t0.35）在稀疏板上因 robust 尺度自污染不可直用，checks 按实证分布重定标（edge 0.018/batch 0.022，docstring 记录）；② stats.sbb_suspicion 返回原始 α、checks 侧做 1−2α+夹取（分工记录）；③ 单轮对称双批次只判"存在批差+归属"不辨伪影源（物理不可辨，跨轮累积解决）；④ 两个过时 M4 不变量测试更新（loop 合法引用 expos.qc；os 模式合法化）；⑤ hypothesis 属性测试抓到 log 边界浮点微溢出真 bug 已修（space.py 边界裁剪）。
- 附带并行交付（M6/M7/M9 组件，接线待续）：failure_model（19 绿）、planner/stages FSM（16 绿）、planner/arbiter（31 绿）、ResponseModel.select_batch_kb（17 绿）、eval 评分/轨迹（8 绿，**首组跨臂实证：os regret 0.035→0.013 递减、naive 0.115 持平且命中假最优**）、agent 层骨架（47 绿）、UI 首版（4 绿）、plot_run（4 绿）；平台面：CI 四件套、mkdocs 站、README 双语、CLI v2 规格、第七轮前沿复查（无竞品，§17）。

### M6 归因 + 失败模型 + 路由
- 状态：**done**（2026-07-10）
- 验证命令：`pytest tests/test_attribution.py tests/test_failure_model.py tests/test_qc_policy.py tests/test_loop_m6m7.py -q`
- 输出摘要：归因 21（含新增跨模块守卫）+ 失败模型 19 + qc_policy 16 + M6/M7 联合端到端 4 全绿。交付：`qc/attribution.py`（6 假设逐孔归因：ΔR² edge/gradient 判别、DoWhy 式 placebo/subsample 反驳器契约、inconclusive 谦逊路径、propose_action 动作语义映射）、`qc/failure_model.py`（Beta-Bernoulli 桶 is_edge×block×batch×round_band、收缩先验 m=5、event-sourced 按观测当前裁决重建、p_artifact_optimistic 乐观界缓解 RAHBO 覆盖偏差、risk_map 出口）、QCPolicy M6 块（SUSPECT/FAILED 落 attribution 事件 + obs.failure_attr/next_action）。
- 诚实 findings（三个，全部落 M9 协议/测试）：① **饱和态归因歧义**——强边缘事件污染 77% 板面时"干净多数"假设崩溃，检测是送分题、归因有最优幅度窗口 → M9 新增"归因精度-幅度曲线"指标（附录 B），联合 e2e 改为断言安全不变量（edge 归因存在 + remedy 全在安全动作集 + 嫌疑零入模）；② **批次-分层几何混叠**——分层交替发射使 capture 序 idx%n 批次与边缘奇偶对齐，29/40 误归因 batch_effect → 批次改空间棋盘格 `(row+col)%n`（与 capture 序、边缘奇偶双解耦）；③ **缝隙失联 bug（整合缝对抗审查实锤）**——sim_base 改棋盘格后 attribution._board_frame 仍按 idx%n 分组，观测常被排除出自身批次组、t_batch 失真 → 对齐修复 + 跨模块守卫测试 `test_board_frame_batch_matches_simulator_labels`（逐孔断言归因帧批次==模拟器标签）。
- 决策/偏离：哨兵/对照观测的动作不进装配（target_cand_id=None 会装配期响亮失败）→ `_pending_actions` 过滤 is_control；归因无法判定时降级 inconclusive + 保守动作，绝不硬编原因。

### M7 失败感知规划器
- 状态：**done**（2026-07-10）
- 验证命令：`pytest tests/test_planner_stages.py tests/test_planner_arbiter.py tests/test_kb.py tests/test_loop_m6m7.py -q`
- 输出摘要：stages 16 + arbiter 31 + kb 7 全绿；联合 e2e 验证动作全链（第 3 轮归因产 DISAMBIGUATION → 第 4 轮仲裁消费、provenance 记账、消歧候选钉中心非边缘孔、动作预算 ≤30%+2）。交付：`planner/stages.py`（Ax 式 StageRule FSM：sobol→gp→failure_aware，streak 判据 + checkpoint 持久化恢复等价）、`planner/arbiter.py`（FireWorks detour/addition 语义 + supersedes、collect_actions 去重按 priority、arbitrate 孔预算贪心封顶、Atlas fwa discounted_scores 先归一化再乘 max(1−p,0.5)、exploration_quota ε≈12% 防折扣盲区固化、well_cost 换算）、`models/response_gp.select_batch_kb`（Kriging Believer：frozen kernel 全重 Cholesky、believer=后验均值）、TrustAwarePlanner 主装配（风险图→LayoutPlanner 避让、planner 状态入 checkpoint §13.4 只存名字+纯数据）。
- 对抗审查修复（两项）：① **候选容量二次封顶**——arbitrate 只按孔预算过闸而 well_cost 可被低估（agent 提案 n_wells=1 实占 replicates 孔）→ plan_round 对物化候选数再封 ≤n_cands，超额按原 priority 序入 overflow 留痕（新增公开 `materializes_candidate`）；② **非物化动作占预算零产出**——ADD_CONTROLS/NEW_CANDIDATES 装配路径未实现，入选会占孔预算+发 action_consumed 却零候选 → `_pending_actions` 只放行可物化动作、掉落项 `action_skipped` 事件留痕（每项一次）、agent 提案侧同类动作裁定期诚实拒绝（Backlog：对照增补装配路径）。
- 决策/偏离：状态/时序审查【未发现】级确认——plan_round 在 QC 前读上一轮报告、streak 每轮一读、checkpoint 原子回滚防双计、resume 与一次跑完等价；ε 配额替换尾部（降序确证）；per_point_alpha 与训练集保序（fit 断言长度且遇非 TRUSTED 直接抛）。

### M8 Agent Orchestrator 层
- 状态：**done**（2026-07-10）
- 验证命令：`pytest tests/test_agent.py tests/test_loop_m8.py tests/test_stateful_proposals.py -q`（守门：agent 无观测/模型写 API、未 accept 提案不影响下轮、提案与 acceptance/rejection 成对、伪造裁定永不生效）
- 输出摘要：agent 55 + M8 端到端 5 + 提案-裁定状态机全绿。交付：`agent/backends.py`（TemplateBackend 七职责：翻译目标/先验提议/裁决解释/轮次叙述/动作提案，全确定性、离线零依赖）、`agent/policy.py`（**loop 第四策略注入点** LoopAgentPolicy：naive/robust→NullAgentPolicy 零行为、os→TemplateAgentPolicy——每轮裁决后 export_view（冻结无真值）→ingest→**本轮** SUSPECT 的 ACTION_PROPOSAL 经 lifecycle.submit_proposal 入队→narrate_round 落 ROUND_RATIONALE）、TrustAwarePlanner 开场 `_adjudicate_proposals`（下一轮对未决 ACTION_PROPOSAL 逐条 accept/reject 落账带中文理由：非法 ActionType 或 _NEEDS_CANDIDATE 动作缺在案 target→reject；GOAL_TRANSLATION/PRIOR_PROPOSAL 留 human）。M8 e2e 断言：提案落账且 content 契约齐、裁定全出自 ADJUDICATOR_ACTORS 且 refs 配对、未决提案只允许尾轮悬案（诚实边界）、每轮 ROUND_RATIONALE 引用真实数字、naive 臂决策日志零 agent 痕迹（对照公平性）。
- 对抗审查修复：**suggest 截断次序 bug**——backend.suggest 的 batch_size 截断发生在全历史 SUSPECT 上，旧轮嫌疑会挤光本轮名额 → 策略层先放开枚举、按本轮过滤后再自行封顶。hypothesis 状态机（5 rules×4 invariants）验证：伪造 acceptance（硬写日志 actor=agent）永不进 accepted_proposals、翻案至多一次且必为 human、事件日志 append-only（前缀 sha256 逐行比对）。
- 决策/偏离：① loop 依赖红线测试随 M8 演进——由"禁 expos.agent"改为"禁 backends/views 直连"（策略层是唯一合法通道，公理 7 语义由 kernel 层测试强制）；② `_policies_for_mode` 后随 M9 扩展臂升五元组（第五注入点 model_factory，rcgp 臂需要），记在 M9；③ 跨轮旧嫌疑不重复提案（decision_id 含 round_id 会绕开幂等堆积重复——只提交本轮）。

### M9 对比实验（关键 demo）
- 状态：**done**（2026-07-10）
- 验证命令：`scripts/gen_sweep.py` 生成矩阵 → `expos.eval.run_cell`（幂等格子）全量 → `runs/full_sweep/_tools/aggregate.py` 汇总；`pytest tests/test_eval.py tests/test_run_cell.py tests/test_compare.py tests/test_robust_gp.py tests/test_loop_soft.py -q`
- 输出摘要：**1450/1450 格零失败**（**五臂** naive/robust/os/os-soft/rcgp × 场景 × 种子；Slurm 双分区管理员级 DOWN → 本地+g209 SSH 回退，g209 180 并发 581 格约 5 分钟，BLAS 钉 1 线程后吞吐提升 ~250×）。产物：`runs/full_sweep/report/`（aggregate_summary.json、main_table、检出率-幅度曲线、归因精度-幅度曲线、收敛图、failures.csv 空）。
  - **H1（主假设）过**【2026-07-11 更正：此判定不成立——引用的 S0.demo 不在预注册场景集内（M9_PROTOCOL 预注册 H1=S2 中高档+S4 上 os 显著优于 robust）；R1 压测实锤 S2 结构档 regret 方向相反且 p 显著（resweep 终判 H1_REJECTED_os_worse，p=0.0001），按预注册纪律记 **deviation**，详见 docs/STRESS_TEST_R1_RESPONSE.md §3；下述污染防护/假最优拒斥数字仍然成立，"路由层必要"的表述修正为"路由层对**结论可信性**必要（污染门控/假最优拒斥/provenance），对 regret 收敛在结构档被软方法支配"——另 R3 §1.1 发现批次档 QC 方向判反（修复中），batch 相关数字待重跑】：S0.demo 末轮 regret os **0.0086** vs naive **0.0179**，假最优命中率 **0.20 vs 1.00**，训练集污染率 **0.004 vs 0.146**；robust（聚合层中位数）0.0193≈naive、rcgp（模型层 RCGP）0.0170≈naive。
  - **H3（QC 税）过**：S1.zero 假阳性 SUSPECT/FAILED 率 **0.11%**、regret 差 **+0.0061**（验收线 ≤5% 大幅通过）；污染率 ~0.0025≈0（量纲 bug 修复实证：试点虚高 0.72 系相对偏差 vs 绝对 τ 错配，现绝对偏差+3σ+injected/contaminated 双列）。
  - **检出率-幅度曲线**：edge 仅 0.05 档漏（0.1 即 0.85）；batch −0.1 以下失效、−0.18 全检出；glare 全档 1.0；thermal_gradient 全档弱（峰值 0.45@0.2）——**设计后果非 bug**（足迹门 |eff|>2·robust_scale 尺度不变性 vs 平滑梯度；单轮梯度按设计交跨轮累积，GRAD_CAP=0.40 有意贴 quarantine 下沿），M9 限制说明记录在案。
  - **归因精度-幅度曲线**：edge/thermal 检出即 1.0、glare 0.94–1.0、batch_effect ≈**0.997**（−0.18 档 1717 对/5 错）。诚实方法学脚注：初版曲线 batch 精度恒 0.0，根因是**统计脚本命名空间错位**（注入器名 batch_shift vs 归因假设名 batch_effect 直接字符串比对）——归因引擎一直正确，聚合器加 INJ_TO_CAUSE 映射后归位；与污染量纲 bug 同类："评测协议自身需要对抗审查"（可引 Automated Benchmark Auditing, arXiv:2605.26079）。
  - **弱幅度档诚实负结果（主张三）**：edge0.1 os 0.0109 vs naive 0.0083——温和常驻伪影下硬隔离仍轻微反输；os-soft（软信任）在预注册三档全部 **os-soft<os** 但均 **>naive** → 预注册判据部分不达（方向对、幅度不足），负结果保持（与 SPC 文献 ML-TAE 的"小中偏移软处理零收益"定量一致，文献空白=我们的新贡献点）。
  - **新看点：regret 与污染防护解耦**：edge0.35 高幅档 os regret 反而最差（0.043）但污染率 0.06 vs naive 0.58、假最优命中 0.7 vs 1.0——OS 保住的是"结论可信性"而非每档的"收敛速度"，正是 operating layer 与 optimizer 的分野（进论文 Discussion）。
- 决策/偏离：① robust 臂启用时补了 loop 侧 MedianAggregation 缺失导入（NameError 实锤——试点与审查双独立发现）；② 扩展臂使 `_policies_for_mode` 升**五元组**（第五注入点 model_factory；os-soft 只换聚合、rcgp 只换模型工厂——对照公平性结构化）；③ 评测协议修正两处（污染度量量纲、归因命名映射）均以"发现-定性-修复-重评分"全程留痕，1450 格全部按修正后 scoring 重评分；④ Slurm 不可用期回退方案（确定性命名幂等格子 + SSH 分片）实证了 §18.1 的多节点纪律。

### M10 UI + 文档收尾
- 状态：**done**（2026-07-10）
- 验证命令：`PYTHONDONTWRITEBYTECODE=1 pytest tests/ -q` 全量 && `python3 -m mkdocs build --strict` && `python3 scripts/expos_lint.py` && `streamlit run ui/app.py`
- 输出摘要：**最终全量回归 431 passed / 0 failed**；mkdocs strict 干净；expos-lint 全绿。交付：
  - **UI 多页只读仪表盘**（ui/：运行总览/板图/裁决日志/三臂对比 四页 + 共享缓存层；14 冒烟测试含只读红线静态扫描——零写句柄、glob 白名单绝不触 truth/、缓存按 (path,mtime)）；
  - **CLI v2**（expos/cli.py 七命令：run/status/verdicts/inspect/override/domains validate/ui，16 测；override 只原子投递 overrides/pending/ 零 store 写；pyproject `[project.scripts] expos`）；
  - **三幕 demo**（scripts/make_demo.py 一键：假最优狙击图+真数解说 / coating 热插拔 / 边界即类型——agent 伪造 acceptance 双路径均被拒的审计证据；docs/DEMO_SCRIPT.md 102 行剧本 ≤10min + 3 QA；2 测幂等）；
  - **expos-lint**（scripts/expos_lint.py：10 规则 ID 化三分级，31 测 + pre-commit hook；首日即抓到 action_skipped 词表漂移一例——工具自证）；
  - **内核鲁棒三件套**（torn-tail 容错+append 原子写+崩溃尾自愈、三条改判路径翻转为 log-before-data（WAL 纪律，RUN_MANIFEST §6 升级"已修复"）、显式 seq 单调校验+旧 run 兼容；8 崩溃注入测试）；
  - **run_start/run_stop 事件**（event-model 纪律：provably-first/last、显式 exit_status、异常缺席即对账信号；EVENT_SCHEMA 登记 16 kind）；
  - **门面**（README 双语更新至五臂+全量扫描实数、CONTRIBUTING §6 五策略注入点开发约定 + §7 lint、mkdocs nav 全量收录）。
- 关账前最后一修（对抗测试驱动）：**批次检查估计器重构**——棋盘格批次×去身份残差的结构性交互使批次位移被身份均值吸收（17 孔板残差差 ±0.0089 < 旧阈 0.022）。修为**身份无关**估计器（within-identity 跨批对差分 + 哨兵天然探针 + 过原点加权回归出乘性 shift_hat + WLS z 门），触发表：45 孔 −0.18 全触发 20/20、clean 零 QC 税、−0.07 不误触；17 孔玩具板低于批次探测信息地板（诚实降级 record-only）。扫描全检出@−0.18 经抽格证实确由 batch_shift 检查触发（47 孔近满板在可探测区），主会话的 replicate_cv 假说被证伪——修复不改 M9 结论。
- 决策/偏离：覆盖率 82.1%（快速套件 3466/4224 行；kernel 97.3/agent 98.3/planner 95.4/qc 94.1；eval/cli 的 0% 系慢套件排除偏差，全量下更高——偏差已声明）；UI 页签由计划 4 tab 升级为 4 独立页（族6 信息架构结论）。

## 收官总条目（2026-07-10）

**11 个里程碑（M0–M10）全部关账**，从空目录到闭环实验 OS 一日建成。终局数字：

- **代码与测试**：expos/ 十包 40+ 模块；tests/ 30 套件 **431 测试全绿**（含 hypothesis 属性/状态机 3 台、崩溃注入、对抗场景）；expos-lint 10 规则静态守门；覆盖率 82.1%（快速套件）。
- **实证**（runs/full_sweep/report/）：**1450 格五臂扫描零失败**（naive/robust/os/os-soft/rcgp）。~~H1 过~~【2026-07-11 更正：见上方 M9 条目更正与"压测更正记录"——预注册判据下 H1 不成立记 deviation；可信性半边（假最优 0.20 vs 1.00、污染 0.004 vs 0.146）仍成立】。H3 过：QC 税 0.11%。检出/归因幅度曲线 + 三个诚实 finding（弱幅度档负结果、饱和态歧义、regret-污染解耦）。

### 压测更正记录（2026-07-11，追加式——原文保留供审计）

R1/R2/R3 三轮建设性压测（红队 Fable）对本台账既有判定的更正，按预注册纪律记录：
1. **H1 判定更正（deviation）**：M9 关账时"H1 过"引用的 S0.demo 不在预注册场景集内；预注册判据（S2 中高档+S4，os vs robust，置换 p<0.05）下实测方向相反——机制修复后 resweep 终判 **H1_REJECTED_os_worse**（S2r3 池化 +0.0161，p=0.0001）。规则跑后修订/重跑，无论结果记 deviation，不追溯改写。全链见 docs/STRESS_TEST_R1_RESPONSE.md §3。
2. **机制空转更正**：M7 关账时三个"风险感知"机制在生产接线为空转（键名失配/None 桶/未接线，R1-2），单元绿掩盖；已修复+环路活性断言+变异击杀验收（R2/R3 双方独立确认闭环）。
3. **批次方向判反（R3 §1.1，P0，修复中）**：批次检查触发即标干净批（IEEE 平局落插入序），pre-existing 横跨两代估计器；batch 档相关数字（检出/归因/污染）带病，修复后重跑重聚合，H1' 池化的 batch 半边暂缓入论文面。
4. **评测口径系更正**：污染量纲（R1）、归因命名空间（R1）、种子集混用（R1-3a）、cause 级归因可辨识性 0.15（R2/M12）——均"发现-定性-修复-重评分"留痕。
5. **R1-5c "naive/os 逐轮逐位 EQUIVALENT" 限定重述（E2E3-F1，P1，已修）**：R1-5c 当时测的崩溃等价路径未覆盖"崩溃重做轮含已消费闭环动作"的组合——`reconcile_redo_rounds` 只回滚物化视图、保留事件日志（审计特性），而有状态的 `TrustAwarePlanner._pending_actions` 从全量 `action_consumed` 事件判"动作已消费"，使崩溃重做轮把本应重做的 REMEASURE/DISAMBIGUATION 补救动作静默跳过（候选 {arbiter:endogenous:4,bo:15,sobol:2} 漂成 {bo:18,sobol:3}、best_trusted 0.5395→0.5124），仅 os/os-soft 破裂，naive/robust/rcgp（无状态 planner）免疫。**根因一句话：事件日志不回滚 × 有状态 planner 读全量事件的组合缝。** 修复=消费侧 round-scoped 过滤（`_pending_actions` 忽略"最近一次 `redo_reconciliation` 的 `from_round` 及之后各轮、且早于该 reconcile 标记"的 `action_consumed`，零 schema 改动，落在 planner 读侧），由 C7 式"崩溃点×os 臂"等价矩阵回归覆盖（tests/test_resume_equivalence_os.py，忠实崩溃注入，判别性锚点=重做轮候选源分布 + best_trusted + 模型快照逐位）。
- **文档**：docs/ 25+ 份（权威蓝图 ARCHITECTURE + 平台四规范 EVENT_SCHEMA/RUN_MANIFEST/CAPABILITY_MODEL/CONTROLLER_MODEL + 设计稿 ADAPTER_ACTIONS/SOFT_TRUST_PROPOSAL/PLUGIN_API + M 系设计 + PAPER_OUTLINE + 3 ADR）；REFERENCE_MAP §1–§22 十二轮调研（35+ 克隆仓源码级走读、平台六族、终局八参照、四轮 2026 前沿切面）；mkdocs strict 干净。
- **论文**：三主张（结构化偏差注入 benchmark / provenance 归因内核服务 / QC 幅度窗口诚实负结果）经多轮撞车复查全部成立（2601.17920 精读结案）；投稿定位 TMLR / NeurIPS D&B（切面 D）。
- **方法学教训两条**（自身可作论文脚注）：评测协议也需要对抗审查——污染度量量纲错配（零伪影虚高 0.72）与归因统计命名空间错位（精度假 0）均为"协议 bug 被当作系统结论"的真实案例，全程"发现-定性-修复-重评分"留痕。
- **Backlog 指针**（REFERENCE_MAP §21.2 P1–P4）：软信任两段式在线校准（切面 D 方案）、ADD_CONTROLS/NEW_CANDIDATES 装配路径、BenchAdapter 长任务动作协议（ADAPTER_ACTIONS）、血缘三出口导出、conditions 病历式状态、duckdb 报告平面接线。

### Gen-3 重聚合与冻结记录（2026-07-11，追加式）

P0 批次方向修复后三扫描全部完工（每格 report/summary.json），Gen-3 重聚合与冻结完成。聚合脚本落各 `_tools/`、产物带指纹（gen3_manifest.json：generated_at + 输入格数 + cells.tsv sha256 + 脚本 sha256 + 逐产物 sha256，代际标签 Gen-3）；未改任何 expos/ 业务代码。

- **主扫描（runs/r1_resweep/report/）**：核心指标表由协议冻结的 `aggregate_resweep.py` 在重跑后格子上重算（Gen-3 数据）；`gen3_freeze.py` 叠加 M12 三列。
  - **M12 方向正确性（B3 验收判据）**：全 Gen-3 批次格（S2/S2r3 batch_shift ×4 档 + S4.batch_dust/edge_gradient_batch × {os,os-soft}）触发轮 1069，**correct=1069、inverted=0、方向正确率=1.000**——`inverted==0` 判据通过（P0 实锤）；Gen-1 未重跑对照 inverted=117（诚实保留）。红队 probe_direction.py 在重跑后数据上独立复核 inverted=0（其 17:49 旧档早于 20:10 重跑，已作废）。
  - M12 混淆矩阵（cause×top_cause，真注入孔）+ cause 级命中/弃权已产出；S4.batch_dust caption 按 dimatt3 口径：批次真值被 dust 二元证据通道掩蔽，归因不可识别性是场景属性、非方法缺陷。
  - **H1 终判 Gen-3 刷新**：S2r3 中高档池化 os−robust mean_diff=**+0.01544**、p=**0.0001**（Gen-2 为 +0.01606）→ 结论**未移动**，仍 `H1_REJECTED_os_worse`。
- **消融（runs/ablation/report/）**：8 结构池 final_regret 排序（低→高）robust≈naive < os 家族（os-soft/os-minus-\*/os/os-lite）——整族劣于 naive/robust，与 H1_REJECTED 同向。**os-lite 观测居末（regret 最高）、os-soft 居 os-family 之首，均与红队预注册相反**（红队 032 亦自证 os-lite>os，方向一致；绝对值 ~7e-4 差异按纪律显式列出）；污染轴预测成立（os 家族≪naive，|os-lite−os|=0.003<0.02）。**os-minus-arbiter ≡ os-minus-attribution ＝同一有效消融**（action-channel severed, equivalent by construction；attribution→arbiter 串行通道两端剪断，双入口验证：强档 minus-arbiter 产 163 条无人消费 failure_attr、minus-attribution 全 0，best_trusted/regret 逐位相等）——表中两行加同一 coupling 脚注、不作两独立自由度。
- **resident（runs/resident_sweep/report/）**：老化趋势检出（temporal_drift CUSUM 任一轮触发）四档 **0.15/0.35/0.95/1.00**（与红队 007 MC 参照 15/35/95/100% 一致）；口径「跨轮可检性由老化趋势承载，会话间游走为亚检出扰动」，0.01 档为诚实亚检出地板（信息地板非 bug）。
- **claim ledger**：claim ④ `batch_detection_attribution` 重钉 Gen-3 证据（m12_batch_direction_summary.json，逐格 correct−inverted 配对置换 mean_diff=+2.675、p=1e-4、favorable=positive）→ **stale 解除、落 supported**；deviation `batch_direction_diseased` 闭合（status=closed、pending_reaggregation=false）；claim ③ H1 证据随 Gen-3 刷新重钉（+0.01544），verdict 仍 rejected。`claim_compiler.py --check` 通过，状态分布 supported 3 / rejected 1（无 stale）。

### 角色对调与 R4 审查轮记录（2026-07-12，追加式）

**用户裁决：审查方/修复方对调**（mailbox/blue_to_red/024 告知、red_to_blue/035 回执）——原修复方会话（bf315d15）转任**审查方**，原审查方会话（2dd8db70）转任**修复方**；信箱目录绑会话不绑角色，全部纪律（四段式/P0P1 回执/中性词表/效果证据/加固非否定）沿用。R3 终审前置件已于对调前全部闭环（red_to_blue/034）；R3 终审正式裁定文档归原审查方（现修复方）收尾。同期用户裁决：计算通道回 sbatch（ssh 直连授权收回，节点零残留经核）。

**R4 审查轮开跑**（新审查方，十路 Opus 并行 + 前沿参考库）：
- 第一波（修复面新鲜视角）：R4-A E2E3 谓词边界 / R4-B FM3 聚合统计 / R4-C Gen-3 冻结独立复算 / R4-D 新增守门测试判别性。
- 第二波（法证）：R4-E 消融 rc=2 瞬态 / R4-F os-lite 反常排序定性。
- 第三波（全系统，应用户"完整不局部"指示）：R4-G 主张-证据全账 / R4-H 设计红线与四层拆分结构 / R4-I 评测协议与统计全链 / R4-J 端到端工程链（含 R3 修复潮后首次全量套件）。
- 报告整合目标：docs/STRESS_TEST_R4.md。store 缓存面待 CACHE3 G1/G2 收口批（原修复方最后一批）落地后补审。

**已交付六路要点先记**（详情以 R4 报告为准）：
1. **R4-C：Gen-3 冻结判"可完全独立复算"**——H1' mean 15 位一致、CI 逐位一致、方向 1069/0 逐行一致、22 项指纹全 match、claim ledger 证据 sha 逐环验过；仅 1 条 P3（deviation 散文 +0.01606 需标 Gen-2 历史值）。
2. **R4-G 两条 P1（账外文档漂移）**：regret p=0.0645 挂四份对外文档、权威产物实为 0.0668（R3 点过名未订正）；THEORY_P3 仍引 7.7e-8 旧 p 值（产物自判 INCONSISTENT）。另 ledger 只护 4 条主张、QC 税四口径并存待集中对账。
3. **R4-I 一条 P1（失活预算门空绿）**：should-activate 格（edge≥0.2 os）全为 Gen-2 数据、无 grade 遥测，`budget_breached([])` 恒过——门在其看守面上零遥测，"全量过门"系空绿。同路**证伪**混代配对担心（配对内两臂同代，置换交换性保持；剔 batch 拒绝更强）。
4. **R4-E：消融 rc=2 定性为"护栏履职"非数据损伤**——g208 nobatch 分片被并发双启动（波及 480 格，远超当时完工信自述"少量"），writer.lock 在任何写入前拦下败者，480/480 法证干净，Gen-3 无影响。三条加固：分片防重入、完成度断言应加"非零退出=0 且按格去重"（原"rc=0 计数==格数"口径会吞并发失败）、log 改追加式。
5. **R4-B：FM3 修复判"真实生效"**（fresh-band d(B1−B0)=+0.69 vs 修复前 0.000）；1 条 P2：乐观界在层次回退处丧失"稀疏→保守"语义（非平稳批次下会过度自信）；`FailureModel.risk_map()` 方法仍为修复前行为（生产零调用，分叉待并）。
6. **前沿参考库**：/Data1/ericyang/r4_os_references/（18 仓浅克隆 677M + 14 篇 arXiv + INDEX.md 全系统架构五层对照——MADSci/ChemOS2.0/NIMS-OS/AlabOS/UniLabOS 逐层对 expos）。

**备份**：R3 修复潮 + Gen-3 冻结后代码面已大幅演进，滚动快照 `/Data1/ericyang/expos_backup_20260712/`（同 20260710 纪律：核心源码+文档 tar + MANIFEST.sha256 注册表 + REPRODUCE 指针；Gen-3 report 冻结产物一并入包）——已完成（220 文件入 MANIFEST）。

**R4 报告主体已寄（2026-07-12，blue_to_red/028）；J 路补遗已入（032），十路全齐，终表 P0=0 / P1=4 / P2=12 / P3=18**。J 路头号结论：干净 clone 全量 672 测试仅 1 红（环境前提非回归）——**R3 修复潮全量集成无合并偏斜**（本轮头号担心证伪）；唯一红项 J-F1 恰是 preflight 门禁单点（claim_compiler 测试 skip 守卫查错前提，一行修，P1）。R4 期间双 P1 闭环：G-F1（修复方 036 订正、审查方 grep 复验清零）、A-F1（修复方 037 方案 b、审查方独立复跑 repro EQUIVALENT）。初版总表（历史）**P0=0 / P1=3 / P2=10 / P3=15**：G-F1 账外漂移已修已复验闭环；I-F1 失活门空绿（修复方在修 abstain 方案）；A-F1 E2E3-F1 窗口崩溃（027 信先行转交，实跑复现同签名漂移，修法=resume 进重做无条件落 reconcile 标记）。总体裁定：R3 修复实质有效（Gen-3 可完全独立复算、红线未破、守门测试 9/10 杀），剩余集中在账外叙事漂移/守护机制前提缺口/统计口径 hygiene 三类，无一触及已冻结结论。两条证伪：混代配对与 rc=2 数据损伤担心均不成立。写权交割已生效（blue_to_red/026），本会话此后纯审查产出。

### R5 参照锚定轮记录（2026-07-12，追加式）

以 /Data1/ericyang/r4_os_references/（29 仓 + 22 文献，2026-07-12 增量毕）前沿纪律为镜，七路（REF-1 事件模型/REF-2 实验室平台/REF-3 BO 决策栈/REF-4 SPC 统计/MIR-1 属性机/MIR-2 multiverse/MIR-3 自足性）交付 **docs/STRESS_TEST_R5.md**（blue_to_red/034），总表 **P0=0 / P1=3（均接单）/ P2=11 / P3=12**。要点：
- **三 P1**：终态语义坍缩（run_stop 枚举仅 success）、读侧 payload 零校验（grade 拼错静默折叠）、cusum sd=1 测试盲区（mutmut 实证 /s→*s 全套件不可见）。
- **MIR-1 压轴**：撤 A-F1 修复→状态机 10s 自动 shrink 出同签名反例（P1 本可自动发现）；已修复代码上又搜出**多-reconcile supersession gap**（P2 偏 P3，候选修法沙盒验证：谓词折叠全部 marker，与 A-F1 修复正交）；可收编属性测试草案交付。
- **强核验（论文 system 节素材）**：五平台交叉验证七项领先（append-only 权威/resume/QC 信任分类器无一具备）；三 confirmatory 主张 87 条 multiverse 路径零翻转；UCB 免疫 LogEI 病理等十一项 BO 稳健；冻结包 sha 链第三方完全可核。
- **自足性缺口**：probe_direction.py 悬引（修复方已收编原件）等六条，C1-C7 检查单收编 claim_compiler --check 接单。
R4+R5 累计：**P1 七条（五闭环两在修）、P2 二十三条全接单**。审查方进入按需复验节奏。

### R4/R5 复验进度收尾快照（2026-07-12，追加式）

- **R4 四条 P1 全部闭环**（发现→接单→修复→审查方独立复验全循环）：G-F1 账外漂移（grep 四文档清零复验）、A-F1 窗口崩溃（repro 独立复跑 EQUIVALENT）、J-F1 门禁单点（fresh-clone SKIP + 真仓 15/15 双侧复验）、I-F1 空绿门（正向探针 NO_COVERAGE + 反向探针零误报；BREACH/NO_COVERAGE 消歧使"真死机制"与"无监控"可分）。
- **R5 supersession gap 闭环**：谓词折叠全部 marker 落地；属性回归文件 tests/test_property_store_resume.py 收编（4/4，被钉死反例转普通回归）；审查方裸状态机满预算 2500×60 重搜零反例；与 A-F1 修复正交（74 passed）。
- **用户指示（2026-07-12）**：先修完现有队列、暂不开新审查线。修复方剩余五批：终态语义+payload 校验（事件模型批）→ qc/stats 加固批 → 账目批（C1-C7+pin+敏感性列）→ R4+R5 RESPONSE 文档 → 门面批（含英文化战役，中文残留基线 5304 行已交）。审查方纯复验模式，每批完工信到即独立复验。信箱 37↔43。

### Research OS VNext 蓝图（2026-07-12，追加式）

用户对主会话下达 Architecture VNext 任务（principal systems architect 立场：evolve runtime → Dry–Wet–Agent Research Operating System；OS 架构优先、非 ML 优化；保留哲学不推倒重来）。产物 **docs/RESEARCH_OS_VNEXT.md**：Part I 十项设计（架构批判锚定 R1-R5 实证 / L0-L6 七层 / Dry-Wet-Agent 三域经内核对象交换的松耦合环 / 子系统 essential 判定（Protocol Mgr、Resource Mgr、Registry、Claim 内核化、Artifact Store、Domain Profile 抽离为 1.0 拦路项）/ 对象模型十二类统一"事件流折叠"生命周期 / 接口双圈（内圈保留五元组注入、外圈 wire 契约）/ 管线全状态转移（"没有静默边"设计律）/ **联邦式分布（不建分布式日志，单写者日志×协调面）** / 插件认证生态（变异语料/属性机产品化为一致性套件）/ 十年模型无关（模型只住两个插件位，格式比引擎长寿，裁决带宽是吸收模型进步的接口））+ Part II 重新创立十条（之首=字段单义 typed facets，最重=Protocol 一等公民；七条哲学核明列"重来也不改"）。已发 blue_to_red/038 知会修复方（三交点：RESPONSE 互引、H-F4 定位、Protocol 议题待用户裁定）；不改变现行 ARCHITECTURE.md 权威与修复方五批队列。

### M16 最小完整闭环立项（2026-07-12，追加式）

**用户定位裁定**：expos 现状=Agent+Dry+Trusted Runtime loop（可信实验 Runtime），非完整 Dry–Wet–Agent loop——Wet 只有架构位置、Agent 无知识反馈闭环；蓝图（RESEARCH_OS_VNEXT.md）是设计任务的产物，**跑通端到端才算完整**。四判据+最小完整版图由用户给出。**M16 立项（docs/M16_MIN_LOOP.md）**：五门判别性验收（G1 agent 闭环带冻结知识逐位断言 / G2 PySCF 真引擎作业化 / G3 Opentrons-simulate+读板仿真器仪器七件 / G4 同 runtime 的 dry→trust 门→wet 晋升决策入事件 / G5 一条命令连续两轮零人工）；新域 solvent_screen；诚实边界=simulated-wet 闭环、不上 LLM、不做分布式。工段 W1-W9 分工见 blue_to_red/043；VNext 三件套为其地基（①在施工、②并入 W2、③被 G3 消费）。

### M16/VNext 施工日志（2026-07-12，追加式）

双主会话分域共建（A=adapters/dry|wet、domains、test_w8_*；B=kernel/planner/qc/scheduler/protocol）。当日落地并互验闭环：
- **VNext ①（trust_confidence 拆分）**：语义收窄 + learning_weight_assigned 事件传输 + 合成副本暗道删除；A 复验=C7 矩阵 24/24 + 暗道复活变异红/绿（sha 还原核对）。
- **W1 租约管理器+作业句柄**（expos/scheduler/，subprocess/ssh/sbatch 三后端）：A 复验抓到 publish-before-payload TOCTOU（16 进程风暴多赢家）→ B 以 tmp+os.link 原子发布修复 → 栅栏+持锁探针恒一胜×3 闭环；"赢家即退=合法 pid-死亡回收伪影"双方立此存照。
- **W3 PySCF dry adapter**（expos/adapters/dry/）：复用 scheduler、四路失败分类实测（kill/timeout/不收敛/提交前拒）、单作业 0.7-0.85s、8 溶剂极性排序物理正确；libxc 段错误被进程边界收成 FAILED(signal)——进程外执行设计红利首次自证。29 测试。
- **W4 wet 仪器栈**（expos/adapters/wet/）：真 Opentrons 9.1.0 simulate 腿 + TCP 读板仿真器，仪器七件全实现 + 四段 custody 链；落仓被 EXP004/005 拦三类当场修正（sim_reader 正名 + bridge 去真值化）。22 测试。
- **EXP011 域字面量棘轮上岗**：22 处存量豁免入册（强制带理由），前向新增即红；补丁冲突重生成走"按字面量本体匹配非行号"法（入协作惯例）。
- qc 止血批（7 无保护检查 error-evidence 过渡语义）、批五 manifest 自足六字段、③ EVIDENCE_TYPING spec v1.1（S4 原型实证：argmax 折叠是掩蔽结构性主犯，验收钉 per-channel verdict + M5 变异）。
- **进度板**：W1✅ W3✅ W4✅ ①✅｜在建：W5（A，solvent_screen 域）、W2+W6（B）｜待：W7 晋升策略、W8 验收套、W9 首跑（G5=一条命令连续两轮零人工）。


### M16 最小完整 Dry–Wet–Agent 闭环收线（2026-07-12，追加式）

**五门验收全绿（G1-G5），W1-W9 全部完工并双会话互验。** 终态证据：tests/test_w8_acceptance.py **16 passed 零 skip**（燃尽表清零）+ test_w8_domain_e2e 3 + B 侧 test_w9_mcl 烟测 3 = 22 绿，lint 全绿。
- **G1 整环**：真环三跑判别——冻结知识→决策面（fingerprint/提案序/promoted 集）逐位相同；翻转 claim→提案可预期改变（知识消费非表演性，C2 教训的闭环级验收）。
- **G2**：PySCF 真引擎独立作业（kill/timeout/不收敛/提交前拒四路失败分类；libxc 段错误被进程边界收干净）。
- **G3**：仪器七件+四段 custody 链（真 Opentrons simulate 协议栈 + TCP 读板仿真器）。
- **G4**：同一 runtime 双腿同 QC 管辖 + Dry→Wet 晋升=记录在案的证据决策（四通道合取、平局决定论、denied 全留痕）。
- **G5**：`--loop mcl` 两轮零人工，run_stop=success，第二轮提案 basis 溯源至账本 claim（basis⊆claim_ids ∧ fingerprint 与 round-1 knowledge_updated 逐位一致）。
- **决定论口径（诚实声明）**：同 seed 重放的逐位断言限定于**决策面**（knowledge fingerprint/提案序/promoted 集——(seed,knowledge) 纯函数）；执行侧（资源竞争下 dry 作业偶发 kill→dry_failed，由失败分类学接住）如实非确定，不纳入逐位比较。
- **定位声明升级**：expos 自此为「**最小但完整的 Dry–Wet–Agent loop（simulated-wet）**」——诚实边界：wet=可信模拟（Opentrons simulate 上界+自研仿真器）、agent=确定性模板（LLM 插件位留待）、单机单 run。接真机=M17（参照库 INDEX_M16_WET 已给路线：换 RecoveryPolicy+AWAITING_RECOVERY 态，非新 driver）。
- 施工全程红蓝对调后的双主会话分域共建+每批互验（信箱 043-071，通信本身即 M16 的 provenance），VNext 三件套 ①②落地闭环、③ spec v1.1 就绪；六路参照线（A 三 INDEX 落 r4_os_references/、B 三 INDEX 落 m16_references/）供 M17/§24 合订。
- **B 会话视角三点（071 信并入）**：(a) 判别性验收贯穿始终——G1"翻转必变"/W7 平局决定论/G5 basis 溯源，每道门都有"删守卫必红"负样本，是 R1"断言名字不断言效果"教训的制度化终点；(b) 决策面/执行面决定论二分是真非确定执行下 G5 判据的正确窄化；(c) 跨轮学习闭环的血统——R3 P0 平局教训→W7 门出生免疫、R1 误触教训→参照线事故重演被即刻识别：**往复的记忆在制度里，不在人身上**。
- 备份：expos_backup_20260712b（341 文件 MANIFEST + 六件参照 INDEX + mailbox 全量）。后续节奏（双方议定）：§24 合订（A 主笔 B 复核）→ 各自杂务清账 → 等用户裁定 v1.1 开工序。

**用户终裁（2026-07-12，M16 定名与 M17 立项）**：M16 正式定名 **Executable Minimum Dry–Wet–Agent Control Loop**——完整可执行控制闭环，**非**最强意义的科学发现闭环（claims 系 run 起始种子逐轮重编译；wet 证据未自动产 ClaimDecision 写回；G1 翻转系外部注入判别非数据自推导）。诚实清单：Dry✅真PySCF / Wet✅可执行仪器模拟 / Agent✅真消费知识 / 晋升✅证据门控 / QC-Trust✅双腿共用 / 事件溯源✅ / 两轮零人工✅ / 知识敏感行为✅ / **Wet→Claim 自动更新❌ / 真机❌ / LLM❌**。**下一刀钦定 = Evidence-to-Claim Compiler**（先于 LLM 与真机）：Wet obs→QC/Trust/Certification Policy→统计聚合→ClaimDecision{supported/rejected/qualified/insufficient}→Ledger 更新→compile_knowledge→新 View→下轮提案——补上后定名升级 **Adaptive Dry–Wet–Agent Scientific Loop**。M17 立项文档 docs/M17_KNOWLEDGE_FEEDBACK.md（五门 K1-K5 验收 + 分工提案），blue_to_red/062 已发 B 对案。

### 🏁 M17+M18 收官（2026-07-13，双签生效：A 起草 / B 复核认签同日）

**定名（用户 M17 立项书钦定条件已满足）：expos = Adaptive Dry–Wet–Agent Scientific Loop（自适应干-湿-Agent 科学闭环）**。收官证据链：
- **四条件共跑**（sbatch 4670049-52，seed=7/rounds=4/replicates=8）：flat=insufficient×4（e=0）/ consistent-zero=insufficient×4（真零效应，e=0.62）/ consistent-strong=insufficient→supported×3（效应+0.319，e_product=10451.9）/ flipped=insufficient→rejected×3（−0.086，e 同）——裁决空间 {supported,rejected,insufficient} 活环铺满，全部中预期。
- **数据自推导实证**：flipped 面知识指纹第 2 轮被湿数据改写（003cae6f→809ca7a1），rejected 裁决写回账本→重编译知识→改变后续提案，零外部注入——M16 定名时"翻转系外部注入"的边界正式跨越。
- **门 12**：五 run（四条件+Stage3）全 CHAIN COMPLETE（scripts/verify_run_chain.py 三层）；决策链差分首分岔全部精确落 claim_decision 节点（实验数据是链分岔唯一源头的机器可验证证明）。
- **LLM 三阶段**：Stage 1 live 四判据（含过期指纹 reask 恢复）/ Stage 2 shadow（决策面与 template 同 seed 逐位等+审计事件必键全）/ Stage 3 llm 档真驱动（两轮非空合法提案→全链→supported，与模板档同基底同结论）。金丝雀（TemplateBackend）仍为生产默认。
- **诚实边界（不变的部分照写）**：wet=可信模拟仪器（Opentrons simulate 上界+自研 TCP 读板器，非物理硬件——真机 RecoveryPolicy seam 就绪未接）；LLM=已验可驱动但默认关；单机单 campaign。收线后批：Phase 4 中断矩阵（六杀点+执行面虫型库：sbatch 相对路径/决策链掩护状态漂移）、门 13/14、engine_version 组合复用键、MCP/验收器硬化批。
- 本窗新增能力件：MCP 只读审计面（expos_mcp，7 tool+4 resource，25 测）+ .mcp.json + expos-audit skill；M19 参照波九线（campaign/domain2/claimexport/mcpsrv/matdata/dataver + B 侧 ELN/报告/防护）。
- **B 会话认签（2026-07-13，独立复验后）**：五 run（四条件+Stage3）经 verify_run_chain.py 亲跑全 CHAIN COMPLETE / exit=success；flipped 面指纹链亲证 003cae6f×2→809ca7a1×2（数据改写落在轮 2，与裁决表一致）。B 侧视角两点入档：(a) **本里程碑的方法学收获是"聚合器的诚实拒裁反向迫使实验设计升级"**——单孔完美混淆→重复孔交错基底、3 对/轮 e 封顶→8 对决定性下界、mu=0.55 零效应→四条件设计，统计器三次教育了实验设计，这是 R1"评测协议也需要审查"的闭环版；(b) **两类"环境系统性遮蔽"虫型入 Phase 4 库**（决策链相等掩护状态量漂移 / 本地绝对路径遮蔽 sbatch 相对路径虫）——测试绿≠现场绿的制度化应对是收线后批的头号命题。

### Phase 4 reliability closure（2026-07-13，双侧清零，用户七项清单全落）

A 侧：#3 engine_version 五道门序（reuse.py，8+30 测）/#4 门 12 安静轮链语义（两类必要事件集+静默零晋升新红线，14 测，五 run 全 COMPLETE）/#7 content-store+增量备份（18 测；诚实发现：sub-ULP 浮点抖动使字节级去重收益≈零——store 保持字节精确无损原语，价值改口径为增量备份/真字节重复/安全 gc）/demo v2 数据凍結（每幕钉真实来源，sha 入备份台账）。B 侧：#1 六杀点矩阵（I5 靶心=emit 后 checkpoint 前去重跳过；12/12 两遍背靠背）/#2 恰好一次（撕裂窗 e_product 逐位等）/#5 round_id 必键+DECISION_FACE_KINDS_V1 提 kernel 常量/#6 expos report 纯函数生成器（字面量自由+离线守卫+每数字溯源+report_digest 篡改判别）/去重护栏（同键异指纹响亮 NondeterminismError）/分叉检测（last_event_seq+sha256）/湿腿不重放（wet_leg_issued 持久化不变量——蓝图声称有实况没有，现在真有了）。**事故与制度**：B 报告 agent 为解回溯读校验红就地迁移五个已认签 run 日志——篡改检测（高水位 sha）自家抓获；即裁「证据不可变：schema 增键解法在验证语义不在数据迁移」+写严读容（ADDITIVE_SINCE 注册表）落 kernel；五 run 原件还原后门 12 全 COMPLETE（历史证据一字未动即绿）。收官报告 docs/reports/M17_closing_report.html（report_digest 23e0752d…，与 demo v2 英雄数字终对表全绿，信 111）。通道改令：07-13 用户令重活改回 ssh（第四次反复；非交互 PATH 需绝对路径 python）。**M20 catalyst_screen 在建**（用户令+收窄令：换域落地优先——同一 kernel/loop 字节不动、catalyst yaml 跑通一次全环即成立；A 四件/B 三件最小形并行）。M21 参照波三线在途（PyLabRobot 真机 seam/pluggy 域插件/ORD 反应数据标准）。

### 🏁 M20 换域存在性证明（2026-07-13，双签成立）

**命题（用户令）**：证明 expos 不是 solvent demo 专用管线，而是可换域的 scientific runtime。**证明跑**：runs/m20_catalyst_full_loop（catalyst_screen.yaml，5 配体+descriptors 坐标表，rounds=2/seed=7/replicates=8/catalyst_high 面）——42 观测（10 真 PySCF 配体作业+32 模拟湿）、晋升 [nh3,pcl3]（coord 驱动采集）、裁决 insufficient→**supported**（效应 +0.412，发车前解析预测 +0.43 物理吻合；e_product=102.23）、域专属知识指纹 4f087ef9、门 12 CHAIN COMPLETE。**B 独立复验认签**（60 events 亲读、决策 fn=e_value_round_certification v1 与 solvent 域同一注册 fn——统计内核跨域零改动复用）。
**架构证词（B 认签信观察）**：换域实现量的分布与分层设计预言完全重合——A 四件全在 adapters/domains（域该改的地方：wet descriptors 泛化/catalyst_high 真值面/五配体表/domain yaml），B 三件全是"把域字面量从 loop 请出去"（VariableDef.descriptors/_domain_bindings+LEGACY-FALLBACK/seed_claims 块），**kernel 一字未动**。dry adapter 亦零改动（几何经 params 注入，_resolve_geometry 本就域中立）。
**诚实边界**：单面单跑最小形证明（用户收窄令口径）；四条件全表/catalyst_low 反面/dry metric 标签美化在 acceptance_faces 机器债；第三域"装包即用"待域契约 v2。**随本条呈用户裁两案**：①域契约 v2（5-hook×字段清单×EXP013 五子句机器对账，107/113 信互锚）；②溯源补全批（独立 eval-harness 溯源层——truth_profile/seed 等评测旋钮不进 OS 决策路径的对账记录 + provider 源码入 config_fingerprint + ORD 只映射不导出）。
**M21 参照波三线同窗全交**（HWSEAM/DOMAINPLUGIN/RXNDATA + B 侧 REF-P2/CFG/M）：真机批三缺口预告（sim-real 差分门/sensed-state 两阶段提交/体积护栏）、域插件裁 declarative 装载拒 entry_points（双侧独立收敛）、ORD 记事实 nanopub 记裁决两层正交、模拟-wet 下不做 ORD 导出（诚实红线）。

### 域契约 v2 + 溯源补全批（2026-07-13，双侧收线双签：A 复验 96 绿 / B 认签同日）

**案一 域契约 v2**：A——DomainProvider 五 hook ABC（dry_species/wet_coords/truth_profiles/seed_claims/validate_yaml + null_profiles 可选 + check_complete 出生即治理 + provider_fingerprint 源码全字节哈希）+ 双域 provider 收编不搬字节（防环审计过）。B——schema 四块（ExecutionKind/ObservableSpec/AcceptanceFaceSpec/metrics 受控词表，全 additive）+ EXP013 六子句动态 lint（preview 档）+ provider: 装载线（expos. 前缀强制=entry_points 裁定落码；load_domain 返回形不变零改动调用方）+ 指纹三层兼容折入（无 provider 域逐字节不变、源码一字漂移即翻、resume 撞新指纹走响亮 config_drift）。**机器债生命周期首个闭环**：catalyst_low 记账（declared/anchor:null）→ A 批间清账（mu=0.20 反面+判别测试）→ EXP013 对账绿——"比机制本身更值一记"（B 收讫原句保留）；preview 恰 3 nudge（crystal/coating/flipped）即下一步收编清单。
**案二 溯源补全批**：harness_record.json（旋钮白名单+code_provenance+对账键+record_fingerprint；写缝 CLI 级失败不碍跑；truth-blind AST 守卫内建）+ provider 源码指纹折入（"域实现漂移照样 resume"维度堵上）+ ORD 只映射不导出照录。溯源盲区（truth_profile/noise_sd/interleave 不在 run 记录，mcl.py:850/851/1106 行级定位）自此有记录层；EVALPROV 单对象防漂移改造立卷硬化批。
**同窗**：M22 参照波六线双侧全交（EVALPROV/ANYTIME/PROPTEST + A2/HG/U）——ANYTIME 头条=现统计内核与 confseq 逐式等价（一手代数核验，对外叙事素材）；四项演进全 opt-in 裁定（经验 Bernstein 头位/betting e 值挂起/e-BH 挂起/分位数可选）；真机批卷宗四即裁（get_result verb/AWAITING_RECOVERY×cancel≡abandon 已实现/well 级 saga 词表/RecoveryPolicy 有序策略表）+ 人机门词表（拒绝也落记录）+ 单位裁定（Quantity 零进 kernel/词表挂 metrics/摄氏度陷阱 T3）。**性质测试文化首战**：B 域 P1 挖出真虫已修（事件日志行边界不对称 U+2028/2029——写读健康三读者三答案；判别测试全绿系 ASCII 输入分布遮蔽=虫型库第三类；llm 档规模化前挖出，时机正确）+ P4/P5/P12 落地；A 域 8 性质绿零反例。cancel 语义钉死（复用 →ABORTED 边委派 abandon，不扩表）。
- **B 认签（2026-07-13，独立复验后）**：provider_loading 17+domain_provider 11+bindings 8=36 绿、agent 全量回归 109 绿、resume/store 性质 13 绿、lint/--preview exit 0 恰 3 nudge——与 A 侧 96 绿互证。B 侧视角一点入档：**本批的方法学主线是"把纪律做进结构里"**——写严读容（ADDITIVE_SINCE）、装载前缀先拒（entry_points 裁定落码）、指纹三层兼容（旧域逐字节不变）、机器债（acceptance_faces 声明即记账）、单对象防漂移（立卷）——五处都是把"靠勤勉遵守的约定"改写成"构造上不可违反的形状"，这是 R1 以来往复方法论在生产化阶段的形态。

### 🏁 M23 Real-Wet Readiness Contract（2026-07-14，双签收线生效：A 起草 / B 复核认签同日——两侧独立产物逐字节相等）

**命题（用户钦定六阶段施工令）**：接真机前先把"每一个真实物理动作可恢复、可回读、可提交、不可重放"的语义做完。六阶段全落：
- **Phase 0** 单位元数据（B：StatisticSnapshot.effect_unit 一字段四量共用/UNIT_VOCABULARY 五词/metric_units 平行映射/无换算+摄氏 offset 陷阱 T3 签名级守卫；A：两域 yaml 单位声明）；T4 门端到端活（观测丢单位响亮拒，DomainError 原文含 "no implicit conversion"）。
- **Phase 1+2**（A，action_ledger.py 819 行）：六态事务面（PLANNED→PENDING→COMMITTED|ROLLED_BACK|AWAITING_RECOVERY|ABORTED）+ 哈希链 append-only 台账 + 复式体积分录（守恒断言/损耗腿/void 补偿）+ 五拒 + 幂等双闸（确定性键+params_fingerprint）；八条参照注入全吸收（for_attempt 戳门/三态 SensedOutcome/台账截断拒 resume 等）。事件 physical_action_transition 注册（write-strict from birth）。
- **Phase 3**（A）：假物理后端七模式×红线矩阵（虚拟时钟零真 sleep——monkeypatch 必炸证明）+ sim-real 差分门（六比对面/三验收模式/vendor_spec_placeholder 容差表"真机实测前永不更严"）——**差分门在 pyvisa-sim/renode/PLR 三先例中均不存在（ahead of precedent 限定语入档）**。三裁落码：体积单链胜双文件/attempt++ 重派非自环/resume 三分语义（COMMITTED 跳/PENDING 仅 re-sense/PLANNED 安全重发）。
- **Phase 4**（mcl 单写者分工）：A 编排门面（闸=字段缺席——NonCommittedAction 无 observed 字段，结构上拿不到未提交观测）+ 接线规格书；B 单批 mcl 缝（commit 闸/QC 串联双守卫响亮——committed 是必要非充分/harness 对称 AST 守卫）。三判据双侧复验过。
- **Phase 5**：A 证据集 18 场景（七模式+crash_I1-I6+human_recover/cancel+unit_mismatch+diff 正负；sbatch 4670071→4670186；零手填——manifest 只声明 expected_outcome）；B 生成器（八节+安全步九项机器状态/缺证据响亮渲染/M17 报告逐字节回归）。
- **⭐ 终对审计往复（本条目的方法学核心）**：首轮 report_digest 两侧相等但 byte-diff 抓出三处——生成器枚举序不定（frozenset 迭代随哈希随机化漂移）、契约解释分歧（证据嵌 run/ 子目录 vs 生成器只查场景根→§1 误报 5/8、crash 行误染 BROKEN）、缺席≠失败渲染缺失。**digest 相等差点放走三处**；修正案=manifest 自描述指针（run_path/ledger_path，null=未参与）+ sorted() + not-involved 三态渲染；边界划分入档：**digest=证据指纹、字节确定性=生成器责任**。修后终对：**两侧独立产物逐字节相等**（docs/reports/REALWET_READINESS.html，sha256 cb96d679…，report_digest 8079fbc5…）——两会话同生成器同证据得同字节，双签的构造性极限形。
- **诚实边界**：§1 覆盖 6/8——AWAITING_RECOVERY→{COMMITTED, ROLLED_BACK} 两弧无场景驱动（recover-in-place/恢复期回滚），如实列已知局限；全部对假物理后端取证。**对外话术更新（用户 128 §4 口径）**："real instrument state is transaction-safe ✅ — against fake physical backend, real hardware pending"；四不可说仍含 physical autonomous laboratory ❌ / real wet-lab validation ❌。
- 同窗：M23 参照波七线（A：OTENGINE/SILA/RECONCILE；B：REF-F/T/C2/P3）；SILA 线两真缺口入真机批卷宗（get_result 成品设计/WaitForRecovery 无限等待超时护栏）；demo v3（域治理里程碑入片，凍結纪律）；通道第五次反复（sbatch 恢复 ssh 禁用）。
- **B 会话认签（2026-07-14，独立复验后）**：正式产物我侧亲产（digest 8079fbc5… 与 A 终验值逐字相等）、三预期全中（6/8 覆盖/crash 六行链绿经 run_path/not involved 三态/零误染）、readiness 12+M17 回归 12+lint 全绿。主线观察入档：**M23 的每一层都在重复同一个动作——把"信任"换成"结构"**（观测=字段缺席闸、恢复=转移表、体积=配平分录、证据=哈希链、报告=纯函数+双签 cmp）。从 R1"测试断言名字不断言效果"到 M23 的逐字节终对——**这个项目学会的就是这一件事**。

### 🏁 M24-B 生物闭环双签（2026-07-14，双签生效：A 起草 / B 复核认签同日——raw + controls 双路径决定性认证）

**命题**：证明 expos 能在生物域跑通 sequence → phenotype → claim → knowledge → redesign 的完整闭环，且 kernel/ledger/certification 保持生物盲。证据链（信 147/148/149/151/155）：
- **三态分离**（raw 路径，同一 claim head 仅换真值面）：`expression_high` → **supported**（e=10.11→102.23→1033.71，effect +0.234）/ `flipped` → **rejected**（e=42，effect −0.004）/ `flat` → **insufficient**（e=0，p=1.0，正确不造 claim）——裁决空间在生物域铺满。
- **知识指纹迁移**：反向先验 run（seed_claims=rejected，跑高面）knowledge_fp **d04b4d05→7f8ce457**——湿荧光把 ledger 从 rejected 翻 supported，零外部注入。
- **生物盲**：cert/cert_stats/kernel/ledger 代码级 **0 生物字面量**（唯一命中=中立能力常量 `sequence_construct`，与 `molecular_geometry` 对等，非生物字面量）。
- **dry 腿**：33 obs，adapter=sequence_proxy / instrument=sim，**0 zmatrix / 0 pyscf / 0 subprocess**，同步纯 Python 本机秒级。
- **controls 路径**（经 B 的 scale-aware 两修后复跑）：副本 8/8 TRUSTED、`effective_w_min=83.33`（(0,200) 标度，0.5×200/1.2）、CI widths 6.71/4.28/3.38 ≪ 门 → eligible → **insufficient(e=10.11)→SUPPORTED(102.23)→SUPPORTED(1033.71)** 决定性。产物 `runs/m24b_controls_final/`（gitignore）。
- **诚实边界（照实写）**：判准 ④（changed knowledge → 下轮 construct 决策）是**机制决定性证明**（knowledge=higher→提案 [j23100,j23102,…]，=lower→[j23103,j23117,…] 完全重排），但**闭环内轮间未自发触发**——真实 explore/exploit 结构限制（低信号区自锁），非接线 bug；与 K-E「consistent 面诚实永 insufficient」同魂。全部为 **simulation 级**（可信模拟 wet + 真 sequence dry proxy），**无真湿实验、无真机**。
- **⭐方法学教训（本条核心）：两个 chemistry-scale-leaky 绝对阈被"生物换尺度"逼出水面**。(a) QC 结构检查 `edge_effect` 绝对地板（fire=0.018，§14 定标于 raw noise_sd=0.02）在 percent-of-control 放大 ~125× 到 (0,200) 标度后误触（观测 0.045>0.018）→ **7/8 副本误判 SUSPECT** → 每臂 n=1 → se=0/n_pairs=1 → 逆方差池化被 `se>0` 门跳过（certification_stats.py:772）→ pooled_effect≡0 → 永 insufficient；(b) certification 资格门 `w_min=0.5`（CS-width 门，按 raw a.u. 定标）→ percent 标度 CI width=3.38 > 0.5 → **e_product=1033 且 CI 不含 0 却仍 insufficient**。**两修同形**：绝对阈改**相对 metric_range 尺度感知**（化学 span 1.2→因子 1.0，IEEE 逐位、`effective_w_min==0.5` 逐字节不变；生物 span 200→×166.67），**有效值入 provenance**（StatisticSnapshot/decision_thresholds，K4 第三方仅凭事件流可复算同裁）。教训两句：**「绝对阈」是隐式单位假设的藏身处，换域即暴露**（普适形，B 158 合署入档：**任何绝对数值阈都携带一个未声明的单位/尺度假设，换域即暴露**；本批应对=相对 metric span + 化学逐字节 + 判别双侧，立为模板）——两个都是"必要非充分"链上的独立环，edge 修完才露出 w_min；以及 **A 侧拒绝在 driver 硬塞 w_min=83.33 蒙混**（会把魔法因子 166.67 藏进 harness、让每个归一路径调用方都得知道它），坚持修在结构正确的位置（compiler 侧单点缩放公式 + 调用方只传域事实 metric_range）。rho 带证据判**不缩放**（V 主导、radius 已随 percent 效应方差正确缩放；扫 rho∈[0.1,10] 仅动 width ~15% 全决定性）——不臆断动不该动的。
- **两条 machine-debt 入账**：(a) `batch_shift` 假阴欠检（**记账不修**——修则破化学逐字节，信 150）；(b) `w_min` 尺度盲（**本批已销**，信 155）。另 sentinel band 单位卫生一行（yaml raw→percent）实测**零 run 影响**（ControlSpec 无 expected_band 字段 → mcl 路径检查跳过），属 declaration hygiene 非解锁环。
- **B 会话认签（2026-07-14，独立复验后，信 148/152/155）**：w_min 修 B 侧 35 绿 + 化学 66 测（k_b/k_c/k_f/k_flipped/m24_mcl）含决定论 model_dump_json/快照/指纹 replay 全过；三视角入档：(a) **本条是 M20 换域存在性证明的"生物加严版"**（三态+指纹迁移+生物盲+同步 dry 腿四面齐）；(b) **QC 结构检查绝对阈的 chemistry-scale-leaky 是一条 M24 判官级抽象发现**（判别测试双侧钉死：drift-ramp 不触 ∧ 生物尺度真伪影仍触——尺度感知非尺度失明；生物默认绝对阈仍假 INSUFFICIENT=kill 成立；真过宽 CS width 10589>83.33 仍 INSUFFICIENT=门非失明）；(c) **controls face-specific 裁为设计性质、成文不入债**——percent-of-control 编码了"强设计=阳性"先验，故 pos/neg 只在 `expression_high` 成立，flipped/flat 下 `bio_readout` **响亮拒 degenerate calibration**（dynamic_range≤0→ReadoutError）是好性质（多数 readout 层会静默出坏归一）；多面差分验收因此走 raw 路径，controls 是该面的下游 readout 层而非多面差分机制。

### Biology Program 2026 · breadth-first 五器官 v0.1（2026-07-14 用户总令，追加式；施工现状截至 2026-07-16）

**战略裁决（用户总令，权威文本 `docs/BIOLOGY_PROGRAM_2026.md`，§7 原文存档）**：M24-B 证成后，**expos 由"跑通一个生物闭环的 runtime"正式改造为 Biology-Primary Adaptive Research OS**——不再把多数精力打磨 `cell_free_expression_screen`（保留为回归 + 因果闭合锚），改为覆盖蛋白/基因迴路/细胞状态/扰动生物学/自动化执行四能力（Design/Program/Perturb/Understand）。
**施工策略改 breadth-first（用户裁决覆盖深度优先，§1.5）**：**不等 M25 做完才进 M26**——M25–M29 并行做薄型 v0.1 vertical slice，先长齐 Biology-Primary OS 整张骨架，再用真实产出选 1–2 条深挖。**目标不是五个完整产品，是先长出五个器官**；v0.1「薄型完成」= DoD 10 条（typed 域对象+provider / 一条可跑 e2e / ≥1 正面+≥1 反或 flat 面 / append-only provenance+指纹 / trusted obs 进既有 claim 生命周期 / claim+knowledge 更新 / 被改变的知识改变后续决策 / 机器生成报告 / 诚实标注 simulation-retrospective-fake-backend 局限 / kernel-ledger-evidence-knowledge compiler 零生物特化），缺一不算完成。本轮暂禁：大规模模型训练 / 完整 SBOL·RDF runtime / 完整 single-cell pipeline / 真实机器人视觉 / 论文级 benchmark。

**五器官现状（逐条诚实，测数本机 collect 亲核：31+20+32+18+27 = **合计 128**）**：
- **M25 Design**（`tests/test_m25_generative_v01.py` **31 测** = 24 器官 + 7 可加载）：`GenerativeConstructProvider`（可加载 DomainProvider，`load_domain` PASSES：adapter 门 sequence_proxy + check_complete 11 levels + validate_yaml + provider fold 入 config_fingerprint）+ `generative_construct.yaml`；5 个可审计变异算子（含 2 个 translation-invariant）+ 生成池 + PROV lineage + diversity acquisition（候选间组成距离、观测无关——lineage-驱动子代依赖亲本读出击中 planner 独立缺口，v0.1 **拒并记账**）；判别案例=**dry ranking 被 wet 表型推翻**（effect −0.322）落真实 claim+knowledge 账本。
- **M26 Program**（**20 测 + 9 landed mcl e2e**）：typed circuit graph，circuit 家族 2→5（dose-response / FFL / repressilator 振荡器 + 振荡频率相）+ 5-tier verify gate（propose 后 dry 前的确定性门）+ time-series dynamic faces；**whole-OS mcl e2e 已达**（typed graph→verify→simulate→动态表型 dry→晋升→时间序列动态 wet reader→trusted obs→certification，`run_mcl_loop` 全程；判别 0.85 dynamic_high vs 0.05 flat）。
- **M27 Perturb**（**32 测**）：40 维 cell-state / gene_knockout，5 backend 竞赛（mean、linear-ridge 两 baseline vs kNN、pathway-informed、ensemble）+ **baseline-gate 真拒昂贵模型**（informative 面只 kNN 过，pathway+ensemble **不过** → 一等负结果；scrambled 面全 NEGATIVE）；**真实公开 Perturb-seq benchmark grounding**（Ahlmann-Eltze/Huber/Anders, *Nat. Methods* 22:1657-1661 (2025), DOI 10.1038/s41592-025-02772-6, Zenodo 14832393——Adamson/Replogle-K562/Replogle-RPE1 三数据集上**无方法在全部数据集胜过 mean**、旗舰 foundation 模型 **scGPT 一个都没过**，**enforced as a test**）；严格 `is_wet_observation=False`（benchmark/校准 only，永不冒充本 run 观测——charter §4 铁律）。
- **M28 Understand**（**18 测** = 13 + 5 迟绑定）：四 discovery agents（Hypothesis/Analysis/Contradiction/Replication）驱动**真实 claim ledger**——agent 只产 evidence、**kernel 门唯一 certify**（结构性护城河）；**架构发现（本器官头号收获）：M28 是 certification loop 而非 screening loop** → 接线点是第七 planner 元素 `CertificationPolicy`（`DiscoveryCertification`，isinstance 测钉死）而非 DomainProvider+yaml——**未伪造 wrong-shaped 域来凑形状**。**⭐第二收获（B 第二批接线逼出的真设计问题，B 157 自诊 / A 形状裁定 / B 158 认同不加新选项）：`Candidate.cand_id` 每 run 随机 mint，外部预建的 claim head 永远绑不上本 run 的 exp**。修法结构性而非 workaround：`ArmSelector` 按**语义**（role + params 子集 + control kind）命名 arm、**绝不按 id**；**id 绑定推迟到 `decide` 看到本轮真实 arms 那刻**（B 侧零改动、`_certify_round` 照旧、护城河不变）。证明：两个独立 run mint **不相交** arm-id 集（真 kernel uuid4 非 mock），**同一份 id-free verdict 在两 run 都绑上**（各带各自 run 的 id、`claim_id` run-invariant）、各产 1 ClaimDelta 经真 `apply_claim_deltas` land SUPPORTED；语义 selector 缺 exp 则 **fail-loud**（`CertificationError`，绝不静默 mis-bind）；负控=用别 run 的 arms 观测 → **zero delta**（绑错 run 的 head 可证无用）。**B 提炼的通则入档：随机 id 的绑定必须发生在 id 存在之后——外部预建就是把绑定提前到 id 不存在时。**
- **M29 Execute**（**27 测** = 19 执行 + 8 编译器）：typed protocol → 约束检查（LOUD `ConstraintError`）→ device-neutral IR（+`ir_fingerprint`）→ fake liquid handler/plate reader，经 **M23 sensed-state COMMITTED 门**驱动真实 action ledger、五面事务；`compile_experiment`（protocol→ExperimentObject）+ `bind_measurements`（MEASURE→expression_fluorescence，uncommitted→zero obs）；**obs 出 `trust=PENDING` 不自证**（裁决留 qc 层，kernel 唯一 adjudicator）。

**分工制度（用户令落码，信 154 接单）**：**A = 五 Team 域实现**（各自域目录，绝不碰 mcl/共享 schema）+ 结构化 seam 需求清单（`docs/bio_seams/M25-M29.md`）；**B = integration owner 单写者**（mcl.py 接线 / 共用 schema 冲突消解 / kernel-neutrality scan / fingerprints / event schema / Gate 12·report / 跨里程碑回归），**一 Team 一批串行接，绝不并发改中央文件**；**每 seam 后跑 kernel-neutrality scan（EXP014）+ 化学回归锚**——这是 085 撞车事故的制度化解。首批接线实证该纪律的价值：provider 装载 allowlist 原仅认 `expos.` 前缀，**堵了全五 Team 的 provider 加载 → 共用单点修**（加 `domains.` 包）。

**诚实状态（勿夸大）**：
- **whole-OS mcl e2e 只 M26 一个真正走通 `run_mcl_loop` 全环**；**M29 已把 fake backend 的物理腿接上 M23 事务台账并有 landed e2e 测**（B 156 记为「已达」，A 侧 README 保守口径仍记 M29 为 domain-local + 物理腿 seam）——**两侧口径此处不完全一致，取保守读法：M26 whole-OS 确证，M29 物理腿 e2e 确证、protocol→ExperimentObject 整环路由待第二批**；M25/M27/M28 **待 B 第二批 seam**（第二批 agent 撞 API 额度上限中断、约 256 行未完成接线留工作树，**未完成但不破**：mcl 可 parse/lint 绿、M26+M29 e2e + M24-B 锚 28 测仍绿——信 157）。第二批卡的**一个真设计问题**及其裁定记档（信 154/158 双侧合署）：`AggregatedCertification` arms 对不上——**Candidate id 每 run 随机，外部预建的 claim head 绑不上**。**形状裁定=head/arms 在 `decide` 拿到 exp 那一刻由本 run exp 的真实 arms 构建，verdict 只携假说+证据+方向语义（域无关），id 绑定推迟**（A 域接、B 侧零改动、护城河不变）；**通则入档：随机 id 的绑定必须发生在 id 存在之后——外部预建就是把绑定提前到 id 不存在时**。
- **五器官全部是 skeleton→fuller v0.1，非完整产品，均未双签**（DoD 10 条尚未逐器官对账过）；
- **全部 simulation / retrospective / fake-backend 级**——可信模拟 wet + 真 sequence dry proxy（GC/CAI/RBS/RNA-folding ΔG，如实标注为有偏 proxy），M27 的 benchmark 是**真实已发表结果用作校准**、非本系统产出；**无真湿实验、无真机**；四不可说不变。
- 参照波四组全交（`docs/bio_refs/01-04`，全 VERIFIED、0 幻觉）+ clone 一批进 gitignored `references/`（ALDE/MAMMAL/linear-perturbation/Robin/SBOL/CellVoyager 等）；**诚实查证纪律**：2026 前沿多超知识截止，先 WebFetch 验存在性、查无实据一律标 **UNVERIFIED** 绝不假读。
- **⭐方法学教训：breadth-first 的价值本轮即得实证——先长齐五个器官，暴露了单器官深挖看不见的东西**。三条实例：(a) **M28 的「certification loop ≠ screening loop」架构差异**——只有把第四个器官摆到同一张骨架上，才看出它接的是第七 planner 元素而非域 provider（深度优先会顺手伪造一个 wrong-shaped 域）；(b) **provider allowlist 只认 `expos.` 前缀**这个共用 bug，一次堵死全五 Team 的加载——单器官路径永远撞不到；(c) **两个 scale-leaky 绝对阈**（M24-B 条）同理系换尺度才现形。骨架长齐 = 共用件的缺陷有五条独立路径来撞它。**B 158 合署并加一句入档**：这几条**都不是"某器官的 bug"，而是只有并排放五个器官才看得见的共性面**——一个器官时 allowlist 只堵一个、看着像该器官自己的问题；M28 的形状差异也只有与 M25/M27 对照才显形。**breadth-first 的判别力正在此。**
- **B 会话认签（2026-07-14，独立复核后）**：五器官条与我盘面一致，诚实口径无夸大——认签。三点：
  (a) **M29 口径我采保守读法、更正自己的记法**：我 156/158 记「M29 已达」**是夸大**——那证的是**物理腿可达**（fake backend 实现 M23 `SensedState` → 复用 `physical_backend` seam 驱事务台账、160 迁移、commit 门控 obs），**不是 whole-OS e2e**（protocol→ExperimentObject 整环路由正是第二批要接的那条 seam）。**A 的保守口径准确，台账取它、README 照它**；whole-OS e2e 现只 M26 一个。这条也是本项目诚实纪律的又一次自我执行：**我方记法夸大时以对方保守读法为准**。
  (b) **integration owner 首批实证该制度**：一 Team 一批串行单写 mcl + 每 seam 后 EXP014 生物盲 scan + 化学/M24-B 逐字节锚（anchor 104 测、中断矩阵两遍逐位同）——五器官并行长齐全程 **kernel/planner/compiler 零生物泄漏**。EXP014 本身是本窗新立的守门（标识符级整词、豁免中立能力常量与 docstring、现仓 0 命中 + kill 验证），**该在五 Team 写生物前就位**——守门先于被守之物，是这条纪律唯一有效的时序。
  (c) **第二批中断的处置纪律入档**：agent 撞 API 额度上限、约 256 行未完成接线留工作树——**先核不破**（parse/import/lint/28 锚测绿）**再告知对侧 push 纪律**（只 push 其 Team 文件、mcl 留我续完），**不在半截状态上叠加**。半成品的正确处理不是藏起来，是让它可见且不阻塞别人。

### M17 施工日志（2026-07-12/13，追加式）

用户 continuation prompt（六 Phase+14 门）与两条提前令（LLM AgentBackend+真机 RecoveryPolicy 即刻施工）执行中：
- **K-A~K-E 全落**：ClaimDelta schema（insufficient 类型级隔离/稳定 deny 码/两族合取 gate_rules）、K-C 第七元接线（E3 首证"数据改账本→账本改提案"）、K-D 三真值面（flipped r=−0.891 零注入可见+flat 无信号面+校准漂移混淆发现→K-B 守卫）、K-E 判别套（9 实测+2 桩）。
- **K-B e 值统计编译器落仓复验绿**：Shafer 校准置换 p、e_product≥20、insufficient 三支（CS∋0∨宽>0.5∨轮<2）+导航报告、裁决 e 乘积（机读 filtration_assumption）/效应逆方差两币分离、plate_order_balance 混淆拒裁。
- **提前令两件落仓（接入未上环）**：LLM 后端（litellm×DI 两层/ProposalSchema 指纹必填/instructor 校验重试/legal-quiet/TemplateBackend 金丝雀保默认）；真机接缝（七态机+AWAITING_RECOVERY、NeverRecover 默认逐位回归、fail-closed 双护栏、契约动词、labware 外置化 plate96.json）。
- **参照九线全交** + REFERENCE_MAP §24 合订转正（三跨件张力即裁：e 值主 BF 显示带/supersede 两族合取/belief≠evidence_factor）；MR_REGISTRY.md+EXP012 巡检上岗。
- 燃尽：剩 B 侧 AggregatedCertification 胶水 → A 解最后两桩 → 三面共跑 → 14 门验收收官。已知 flake 家族：PySCF 子进程负载下偶发 SIGSEGV（复跑即绿，K-G 测试侧缓解候议）。
- **K-F 胶水落仓（B，red_to_blue/085）**：AggregatedCertification 第七元入 decide()，seam 改 `(deltas, cross_round_state)` K-C 测试零改动；I4 certification_state 随 checkpoint 存取、e 乘积磁盘往返逐位；双面效应干净反号（−0.0930/+0.0030）零注入审计。**结构性发现**：单孔基底 n_pairs=1 → e≡0 + 完美混淆守卫必触 → 决定性裁决结构性不可达。
- **重复孔基底落仓（A，2026-07-13，blue_to_red/083+附件083a）**：compile_wet/protocol_spec_from_experiment/layout_from_protocol 参数化 `n_replicates/interleave`，默认逐位=原单孔布板（101 passed 全绿回归锚）；扩展唯一 owner=compile_wet（capture_index+每重复孔独立四段 custody，interleave=拉丁方轮转 → corr(capture,arm)≈0）；**K1 可达性实证**：flipped 面 2v2 臂×3 重复交错两轮 e_product=20.910≥20 裁 rejected（contrary）；单孔负样本=「删守卫必红」反例。**量化收获**：符号翻转 e 校准器 3 对/轮封顶 e≈1 → 决定性需 ≥6 对/轮——「聚合器诚实迫使实验设计升级」自此有最小对数下界。待 B 一行接线（mcl compile_wet 传 cfg.replicates+interleave=True，建议 yaml replicates: 3）。
- **阶段收尾锚（2026-07-13，用户令"收尾晚点续"，信 087↔087 双锚）**：LLM 上环推进至——litellm==1.67.2 钉版装毕（OPENAI_API_KEY 在位）、M18_LLM_LIVE_SMOKE.md 四阶段方案立卷、truth-isolation 守卫+负样本+Stage 1 脚本 scripts/llm_smoke_stage1.py 完整落地（113 passed 全绿；EXP001 巡检首次实战拦截守卫命名并按规改合规）；撞车处置：A 侧接线 agent 半成品全退、B 落点无损（B 087 评"处置漂亮，分域约定经受第一次实战"）。**续工序**：B 开关/K-F triage 完工信 → A 重下水 K-E 两桩（前 agent 停于只读期零落盘）→ Stage 1 live 一发 → Stage 2 shadow 判别 → Stage 3 llm 驱动 → 三面共跑（重批 sbatch）→ Phase 4 中断矩阵 → M17 门 12/13 验收。
- **K-E 收官（2026-07-13，信 093）**：test_k_e_acceptance.py **11 passed 0 skipped**（主会话亲验 95s）——D3 收敛双门（replicates=8 基底 2 轮收敛 rejected，e_product=102.23，CS 宽 0.043，真面一致）+ K2 五联环（双镜像 head 同种子双面：flipped 面 kfp 003cae6f→c2a0d299、提案序全翻、晋升 {eth,acn}→{ace,acn}、round1 decisive）。**两条结构性发现待 B 裁**：(a) top_k=2 下 n_pairs=replicates → replicates=3 永不可达 decisive，共跑需 3→8 或放宽 top_k；(b) polar_high 面对 eth/acn 臂真 ~0 效应 → consistent 面诚实永 insufficient（五联环照实改判读：consistent=冻结零对照，反装饰判别更强）——三面共跑预期表须改。
- **M18 参照波（2026-07-13，六线全交 + B 侧三线）**：INDEX_M18_{STRUCTOUT,LLMOPS,RESUME,SHADOW,EVALGUARD,LINEAGE} 落 r4_os_references/（克隆物 m18_references/）。即时转化：①from_provider 构造期路由预验落地（get_llm_provider 纯串预验，113 绿）；②bluesky「事件日志为真相/checkpoint 仅游标」发现喂 B resume 裁定（信 092§2）；③门 12 验收器构建 agent 下水（LINEAGE 蓝本）；④EVALGUARD 诚实结论：solvent 域小，Stage 3 只验契约合规+反事实响应性、不判能力优劣。not-copy 红线各 INDEX 写死（LLM-judge 裁科学决策/中心化 lineage 服务/耗尽抛异常等）。
- **B 两裁定落地 + 门 12 验收器交付（2026-07-13，信 094-096）**：①replicates 3→8（yaml+量化下界注释）；②TRUTH_PROFILES 新增 polar_high_strong=0.70（only-mu-differs，判别测试三钉 9 绿）；共跑定稿**四条件**（flat/consistent-zero/consistent-strong/flipped 铺满裁决空间），sbatch 模板就绪，唯一发车前置=B resume 裁定。③REF-X/L/H 四条置顶吸收：usage 键名迁移规范形、decision_face.v1 版本化白名单、Stage 3 统计口径 d1_c2st.v1 成案、shadow prompt_sha256+门版本 id 入必键。④**门 12 验收器**落地 scripts/verify_run_chain.py（三层验收+diff 决策链差分，9 passed 亲验；两条事件流缺口记档：knowledge_updated 缺 round_id、观测 id 属执行面不可跨 run 复算；agent 报的 run_fingerprint 疑虫已排除——真因系 replicates 变更跨探针）。
- **MCP 能力件交付（用户令"加入 mcp skill"，2026-07-13，信 101）**：expos_mcp/ 新顶层包——FastMCP 只读审计面 v1，七 tool（含 verify_gate12/diff_runs 直挂门 12 验收器="工具即取证"）；红线落码：零写路径（严于 proposal-only）、返回层递归 marker 守卫、路径双 resolve 校验、kind 白名单；18 测绿亲验+lint 绿；接入 `claude mcp add expos -- python -m expos_mcp.server`；docs/MCP_SURFACE.md 立卷；mcp==1.16.0 钉版。硬化批待办四条（resource 化/readOnlyHint/outputSchema/斜杠早拒）记 INDEX_M19_MCPSRV。
- **M19 参照波（2026-07-13，A 六线 B 三线）**：A 侧 INDEX_M19_{CAMPAIGN,DOMAIN2,CLAIMEXPORT,MCPSRV,MATDATA,DATAVER} 落 r4_os_references/。要点：第二域首选 catalyst_screen（summit 模拟器逐槽同构 ~150 行零 kernel 改动，域特化债单点=SOLVENT_POLarity 表→yaml descriptors）；claim 导出=nanopub 三图+trusty URI（与现指纹同构），「弱不撤强」系生态空缺须 expos: 命名空间（对外叙事素材）；campaign 层=文件态清单+FAILED/ABANDONED 双轴+失败率熔断+全局停直接接 e 值；OPTIMADE 借 `_expos_` 前缀纪律、拒 MSONable 反射 from_dict（代码执行面）；MCP 工艺五条已在途喂构建。B 侧 REF-{E,R,G}（ELN/可复现报告/输入侧防护）在建。纪律新增：克隆前断言 pwd（两起同因误落事故后入档）。
- **Stage 1 live 四判据全过（2026-07-13 续工首件，信 088）**：openai/gpt-4o-mini 3 调用，J1 合法提案指纹逐字命中+basis⊆账、J2 过期指纹真触发 1 次 reask 并恢复、J3 审计三件落 runs/llm_smoke_stage1_c/、J4 护栏零触。**抓到 live-only 接缝虫**：backend 与 from_provider 闭包 model 重复关键字 TypeError（离线 stub 吞 kwargs 测不出——Stage 1 立项理由实证）；修 merged.pop("model")+三处 `litellm/` 假路由示例勘误（合法形 `openai/...`），75 passed 复验绿。K-E 两桩转实 agent 重下水。
- **Demo 影片（用户令，2026-07-13）**：英文电影式单文件 HTML（12 幕 ≈200s 按用户脚本 + 90s cut、六面板、双 hero=证据认证/账本指纹迁移 003cae6f→c2a0d299、三世界分屏、恢复段、诚实边界角标常驻），全部数字取自真实 run（PySCF 偶极/两轮晋升决策/三面裁决）；调色板经 dataviz 六检验证。成品归档 `docs/demo/expos_demo_v1.html`（sha256 fdc2432c…，另一份在 expos_backup_20260713/）；线上 Artifact 链接已按用户指示撤下（同 URL 覆盖为占位页）。浏览器直接打开归档 HTML 即可播放。
- 备份：expos_backup_20260713（精选 394 文件 MANIFEST + 16 参照 INDEX + env）；**整仓 FULL tar 完成**：dry_wet_agent_os_FULL_20260713.tar.gz 3.0G / 2,487,712 条目（含 runs+references 全量）/ sha256 e5df60bb…（已入 ARCHIVES.sha256，tar tzf 全表通过）。


## 会话续接说明

新会话接手本项目时：先读本文件确认最后一个 `done` 的里程碑，再读 `docs/BUILD_PLAN.md` 中下一个里程碑的验收标准，从那里继续。运行级断点续跑用 `runs/<name>/checkpoint.json`（见 ARCHITECTURE.md §4.4），与本台账互不混淆。
