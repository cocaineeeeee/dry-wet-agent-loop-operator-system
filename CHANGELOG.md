# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [SemVer](https://semver.org/)。
详细纪律见 `docs/ENGINEERING.md` §3。

## [Unreleased]

### Added
- 内核（M1）：两 schema（Design/Result）+ 事件日志 store + 提案-裁定 lifecycle。
- Design 层（M2）：space/sampler（Sobol）/layout/budget，所有添加须过预算。
- Adapter 层（M3）：双域模拟器 + 结构化偏差注入器 + ingest + crystal/coating YAML。
- 响应模型 + naive 闭环（M4）：端到端跑通 `scripts/run_loop.py`。
- QC 三级检查（M5）：edge/batch/sbb 三级检查 + 信任路由接线（os 模式）。
- Agent Orchestrator 层第四策略注入点（M8）：`_policies_for_mode` 返回四元组 `(VerdictPolicy, AggregationPolicy, PlannerPolicy, LoopAgentPolicy)`；os→`TemplateAgentPolicy`（after_round 提案 ACTION_PROPOSAL + 叙述 ROUND_RATIONALE，裁定走 planner），naive/robust→`NullAgentPolicy` 零行为。
- CLI 入口 `[project.scripts] expos = "expos.cli:main"`；M9 三臂（naive/robust-blind/os）compare 全量通过。
- 工程纪律：pre-commit（ruff/codespell/hygiene）+ 异常吞并代码棘轮 `scripts/ratchet.py`。

### Changed
- 里程碑重编号为 M0–M10（M2 规划补丁新增 Agent Orchestrator 层 M8）。
- 模拟器执行链集中到 `adapters/sim_base.py`（真值→噪声→注入→RawResult+truth）。
- QC 绝对阈值改为按实证分布重定标（edge 0.018/batch 0.022），信任路由 os 模式合法化。

### Fixed
- space.py 对数边界裁剪：修复 hypothesis 属性测试抓到的边界浮点微溢出。
- 归因批次分组对齐模拟器棋盘格 `(row+col)%n`（原 capture 序 `idx%n` 错位、稀释真批次效应、失真 t_batch）；新增缝隙守卫测试 `test_board_frame_batch_matches_simulator_labels`。
- 候选容量二次封顶 ≤ `n_cands`（`well_cost` 被 agent 提案 `n_wells` 低估时防物化超额），超额入 overflow 留痕；arbiter 新增公开 `materializes_candidate`。
- 污染度量量纲修复（`expos.eval.scoring`）：`bias` 由相对偏差 `y_measured/y_clean−1` 改绝对 `y_measured−y_clean`，`τ` 由 `noise_sd` 改 `3·noise_sd`（零伪影场景污染率虚高 ~0.72 → 纯噪声误报 ≈0.3%）。
- robust 臂 `MedianAggregation` 导入缺失补齐（M9 三臂全通）。
