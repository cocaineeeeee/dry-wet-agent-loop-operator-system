# 构建计划 —— 里程碑与验收标准

> 完整构建（非缩水原型）。每个里程碑 M0–M10 完成后，必须在根目录 `CHECKPOINTS.md` 落一条检查点记录：状态、日期、验证命令与实际输出摘要、偏离蓝图的决策。未过验收不得进入下一里程碑。
> 架构权威定义见 `docs/ARCHITECTURE.md`；外部参考系统见 `docs/REFERENCE_MAP.md`。

## 里程碑总表

| # | 里程碑 | 交付物 | 验收标准（可执行验证） |
|---|---|---|---|
| M0 | 项目骨架 | pyproject.toml、包目录、pytest 可跑、streamlit 就绪、.gitignore | `python -c "import expos"` 通过；`pytest` 收集到 0 失败 |
| M1 | Kernel | `kernel/objects.py`（两个 schema + 枚举全量字段 + DecisionRecord 事件载荷）、`kernel/store.py`（RunStore + events.jsonl + checkpoint.json + ReadOnlyRunView 导出）、`kernel/lifecycle.py`（状态机 + 裁决策略 + 改判） | `pytest tests/test_kernel.py` 全绿：序列化往返、事件追加、裁决表、改判写日志、DecisionRecord 落盘 |
| M2 | Design 层 | `design/{space,sampler,layout,budget}.py` | `pytest tests/test_design.py` 全绿：单位立方往返（log/类别）、约束拒绝、Sobol/BO 提议、哨兵固定位、副本跨区组、placement_hint、风险避让、预算不超支 |
| M3 | Adapter 层 | `adapters/{base,artifacts,sim_base,sim_crystal,sim_coating,bench_manual}.py`（sim_base 为实现期新增的共享基座，偏离备案见 CHECKPOINTS M3）、`ingest/{csv_loader,image_metrics}.py`、`domains/{crystal,coating}.yaml`、`domain.py` | `pytest tests/test_adapters.py` 全绿：真值面内部最优、6 种注入器方向正确、truth sidecar 与 OS 隔离、CSV/图像 ingest、worklist 生成 |
| M4 | 响应模型 + naive 闭环 | `models/response_gp.py`、`loop.py`（含断点续跑）、`scripts/run_loop.py`（naive 模式） | `python scripts/run_loop.py --domain crystal --mode naive --rounds 4` 端到端跑通，runs/ 目录结构齐全，checkpoint 续跑测试通过 |
| M5 | QC 三级检查 | `qc/stats.py`（纯统计原语）、`qc/checks.py`、`qc/policy.py`（VerdictPolicy 策略对象——裁决单一注入点，loop 主体零 mode 分支）；设计规格 docs/M5_DESIGN.md | `pytest tests/test_qc*.py` 全绿：边缘/梯度/Moran/批次/漂移/哨兵/副本各检查在合成板上命中；**零伪影场景 QC 税（假阳性 SUSPECT 率）≤5%**；NaivePolicy 下与 M4 行为逐字段回归一致（DEEP_REVIEW §2C/§3.2） |
| M6 | 归因 + 失败模型 + 路由 | `qc/{attribution,failure_model}.py`、lifecycle 接线 | `pytest tests/test_qc.py` 全绿：6 假设签名 top_cause 正确；失败模型 risk_map 单调合理；OS 模式下 SUSPECT 观测确实不进响应模型（断言训练集） |
| M7 | 失败感知规划器 | `planner/planner.py`（动作仲裁 + 风险贴现 + 对照增补 + ProposalQueue 消费） | `pytest tests/test_planner.py` 全绿：动作被消费、预算约束、DISAMBIGUATION 钉中心、provenance 决策链完整 |
| M8 | Agent Orchestrator 层 | `agent/{orchestrator,backends,views,policy}.py`：目标翻译、先验/理由提议、QC/归因解释、下轮叙述与动作提案、TemplateBackend（默认）+ LLMBackend（可选回退）、DecisionRecord 全链落盘；`policy.py` 落**第四策略注入点** `LoopAgentPolicy`（`_policies_for_mode` 返回四元组，naive/robust→`NullAgentPolicy`、os→`TemplateAgentPolicy`：after_round 导出视图→本轮 SUSPECT 的 ACTION_PROPOSAL 经 lifecycle 入队→narrate 落 ROUND_RATIONALE），裁定在下一轮 `TrustAwarePlanner._adjudicate_proposals` | `pytest tests/test_agent.py` 全绿：翻译确定性、提案校验与预算封顶、**守门测试**（无观测/模型写 API；未 accept 提案不影响下轮；提案与 acceptance/rejection 在事件日志成对；loop 红线禁 `expos.agent.backends`/`views` 直连） |
| M9 | 对比实验（关键 demo） | **三臂** compare 模式（naive / **robust-blind**（副本中位数+稳健统计、无 QC——防稻草人基线）/ os）+ 评分 + `report/{summary.json,compare.png}` + agent 裁决叙述；评测协议 docs/M9_PROTOCOL.md | 同种子三臂对比：os 最终推荐真值 ≥ 两基线且含边缘伪影场景 regret 显著更小；假最优判 SUSPECT + 归因 edge_evaporation + 人类可读解释；**四项诚实性指标必报**——零伪影 QC 税、留出伪影（签名库外的空间随机场）行为、检出率-幅度曲线（Slurm 扫描到失效边界）、标定/评估场景分离声明（DEEP_REVIEW §2） |
| M10 | UI + 文档收尾 | `ui/app.py` 4 页签（决策日志页含 DecisionRecord 链）、README、BenchAdapter 说明 | `streamlit run ui/app.py` 可加载 compare 运行目录并渲染 4 页签；README 含快速上手与 demo 剧本；`pytest` 全量绿 |

## 依赖顺序

M0 → M1 → M2 → M3 → M4（此时 naive 闭环可跑，是第一条端到端基线）→ M5 → M6 → M7 → M8（agent 层，依赖 M7 的提案消费通道）→ M9（定量论点）→ M10（呈现层）。

## 范围与决策纪律

- 不引入数据库服务、不引入 BoTorch、不引入 cv2；如实现中确需偏离蓝图，先改 `docs/ARCHITECTURE.md` 并在 CHECKPOINTS.md 的对应条目记录偏离原因。
- 仿真真值只允许出现在 `adapters/` 与评分代码；`qc/`、`models/`、`planner/`、`agent/` 中出现 truth 引用视为验收失败。
- Agent 层是建议层不是内核：`agent/` 不得 import lifecycle 裁决写 API 或 store 写句柄（守门测试保证）；LLMBackend 不得成为任何测试/对比实验的必要条件。
- 每个里程碑的测试与实现同一里程碑内完成，不留欠账。

## Backlog（M10 之后；来源 DEEP_REVIEW §3 与走读 §13）

- **偏差校正复归**：归因给出伪影类型+增益估计后，把被隔离观测去偏还原、以膨胀不确定度（per-point alpha）复归训练集——失败模型升格为测量误差模型；
- 连续信任降权（alpha × g(suspicion) 替代部分硬阈值）；
- 轮内可重入检查点（phase_done 事件 + 阶段级续跑，Bluesky 配方 §13.1）；
- ReadOnlyRunView 索引+懒加载（Tiled 配方 §13.3）；EventKind 枚举 + 描述 dict + 覆盖测试；
- OpenLineage 导出器 / BayBE 可选后端 / 真实台面 BenchAdapter（CLSLab 注入函数蓝本 §13.7）；
- resume 时观测文件完整性校验（压测 finding D：当前信任 checkpoint 不查账）。
- **【BA3 登记，2026-07-11】批次双锚主锚的升高型适用边界**：主锚（哨兵 vs expected_band
  几何中心）在升高型污染下因干净哨兵真值系统性偏离 band 中心（本域 ~0.10）会误指干净批
  （安全网有效：与回退锚冲突→record-only、false-accuse 恒 0；生产不可达：全库 batch_shift
  均降低型）。**引入升高型注入或换域（干净真值贴 band 高沿）前必修**：主锚 target 改哨兵池
  稳健中位数，或 target_unreliable 阈由半宽收紧至哨兵组内尺度量级（红队探针
  /tmp/claude-1128/dimba3/ 可复跑验证）。
- **【RES3 P2 登记】resident 漂移 rw_seed 从 run seed 派生**：需打通 loop→adapter 的
  run seed 通道（execute 只收 rng，属 API 变更）；现为固定 fixture（局限已在
  artifacts.py docstring 登记），跨 seed MC 不平均游走实现。
- **【OS3 登记，2026-07-11】权限矩阵与 quota 四件**（PLUGIN_API 落地前置，见 mailbox/red_to_blue/016）：
  ① eval 直写 report/ 的能力位登记（或由 loop 收口物化）；③ RunStore 写方法 default-deny
  闸门（CAPABILITY_UPPER_BOUND 落地——插件头号前置）；④ ARCHITECTURE §11 措辞区分
  "agent 推理层（结构无写）"与"装配 shim（可信持句柄）"；quota：human-only 提案
  （GOAL_TRANSLATION/PRIOR_PROPOSAL）未决上限+超限告警或终轮批量 reject 留痕（R2 U-3 同源）。
  正面认定存档：truth 四重结构强制、事件日志崩溃一致性、孔预算记账、run_cell 进程级隔离。
