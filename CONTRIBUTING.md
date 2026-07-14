# 贡献指南（CONTRIBUTING）

expos 是一个正在从单机科研代码走向平台的**闭环材料实验操作层**。内核小而稳（两对象 + 事件日志 + 信任路由），外围可扩展。贡献的第一原则是：**不破坏内核的可审计性与真值隔离**。

权威蓝图见 `docs/ARCHITECTURE.md`，工程规范见 `docs/ENGINEERING.md`，里程碑台账见 `CHECKPOINTS.md`。

---

## 1. 环境准备

```bash
git init                                  # 若当前检出还不是 git 仓：先建仓，否则下一步 pre-commit install 无 .git 可挂
python -m pip install -e ".[dev]"        # 3.11+
pip install pre-commit && pre-commit install   # 钩子装进 .git/hooks，必须在 git 仓内执行
```

> 文档预览需要额外依赖：`pip install -e ".[docs]"` 后 `mkdocs serve`（或 `mkdocs build --strict`）。

提交前本地自检：

```bash
pre-commit run --all-files                # ruff lint+format + hygiene + codespell
pytest -q                                 # 全量（含 e2e，稍慢）
```

快反馈子集（对齐 CI 的 fast 层，<1min）：

```bash
pytest -q tests/test_kernel.py tests/test_design.py tests/test_adapters.py tests/test_planner_stages.py
```

---

## 2. 贡献流程

1. **对齐里程碑**：改动前确认它落在 `CHECKPOINTS.md` 的某个里程碑内；架构偏离必须先在 `CHECKPOINTS.md` 备案（写明偏离项与理由），事后补案不被接受。
2. **开分支**：从 `main` 切 `feat/<milestone>-<slug>` 或 `fix/<slug>`。
3. **小步提交**：提交信息用祈使句概述“做了什么、为什么”；一个提交只做一件事。
4. **本地绿灯**：`pre-commit run --all-files` 与 `pytest -q` 全过再推。
5. **更新 CHANGELOG**：面向用户的改动写进 `docs/CHANGELOG.md` 的 `[Unreleased]`（Keep a Changelog 格式，规则见 `docs/ENGINEERING.md`）。
6. **开 PR**：按下方模板填写；CI（fast → full 矩阵）必须全绿。

### PR 自检清单

- [ ] 改动对齐某里程碑；架构偏离已在 `CHECKPOINTS.md` 备案
- [ ] `pre-commit run --all-files` 通过
- [ ] `pytest -q` 全量通过（含 e2e）
- [ ] 新模块带**对抗场景测试**（见 §4）
- [ ] 面向用户改动已写入 `docs/CHANGELOG.md` 的 `[Unreleased]`
- [ ] 未触碰下方任一条红线（触碰即被拒）

---

## 3. expos 红线清单（违反即 reject）

这些不是风格建议，而是项目论点成立的前提。任一条被触碰的 PR 直接拒绝，无论其它部分多完善。

- **真值隔离**：仿真真值只由 `adapters/sim_*` 写入 `truth/` sidecar。`qc/`、`models/`、`planner/`、`agent/` **一律禁读** truth；`loop.py` 只把 `truth_records` 当**不透明块**原样落盘，不解析、不转发。只有事后评分脚本可读。这是“系统没被伪影骗到”能被定量证明的唯一前提——破坏它等于伪造实验结论。
- **Agent 无写权**：Agent Orchestrator 是“外交层”，只产出 `DecisionRecord`（只读视图 + 提案队列）。它**不得**直接改写实验对象、观测对象或运行状态；任何生效动作必须经内核的生命周期状态机与信任路由。Agent 不是内核。
- **无静默降级**：约束缺失变量、预算超支、未知 `risk_map` 键、越界参数等一律**响亮失败**（抛干净的领域异常，见 `expos/errors.py`），**绝不**静默判真、静默兜底或降级返回默认值。宁可崩，不可骗。
- **检查点纪律**：构建级（`CHECKPOINTS.md` 台账）与运行级（`checkpoint.json` 断点续跑）双层齐备。任何进度都必须可恢复、可审计；不得引入让运行无法从最后完成轮恢复的改动。

新增 `except` 兜底、给决策模块传入 truth、让 Agent 直接落盘、用 `try/except: pass` 吞异常——都属于红线范畴。

---

## 4. 测试要求

- **新模块必须带对抗场景测试**：不只测 happy path，还要测“系统被伪影/异常/边界攻击”时的行为。参照 `docs/ARCHITECTURE.md` 第一幕（假最优狙击）与 DoWhy 式反驳器纪律——例如注入器把边缘孔读数抬成全场最高时，OS 应判 SUSPECT 并证伪，而非围绕假最优烧预算。
- **响亮失败要有测试**：每条“应当抛异常”的红线路径，配一条 `pytest.raises` 断言，防止后人把它悄悄改成静默降级。
- **e2e 归 e2e**：闭环级用例放 `tests/test_loop_e2e.py`，它进 CI 的 full 层；不要把慢闭环塞进快测试子集。
- 修 bug 先补一条能复现该 bug 的回归测试，再修。

---

## 5. 代码与注释约定

- **风格由 ruff 裁决**：`ruff format` 是唯一格式化器，`ruff check` 是唯一 linter。不要手工对抗它，也不要叠加 black/flake8。
- **中文 docstring / 注释**：本项目 docstring 与行内注释以**中文**为主，这是刻意选择，便于科研团队协作，请延续。对外 API 名、异常类名、日志键仍用英文以保证机器可检索。近似/工程假设要逐项标注 `【已核实】` / `【工程近似】`（沿用 `adapters/sim_*` 的约定），让审阅者一眼看出哪些数字可信。
- 公共函数写清 `Raises:`，尤其红线相关的响亮失败。
- codespell 只查英文，中文不受影响；若领域缩写被误报，加进 `.pre-commit-config.yaml` 的 `--ignore-words-list`。

---

## 6. 五策略注入点开发约定（新增/改动实验臂必读）

闭环的全部行为差异都收口在一个函数：`expos/loop.py` 的 `_policies_for_mode(mode, cfg, seed)`。它按 `mode` 返回一个**五元组**，`run_loop` 逐一注入：

```
(verdict, aggregation, planner, agent, model_factory)
   裁决        聚合        规划     agent     模型工厂
```

历史上是四个注入点；`rcgp` 臂把**模型工厂**也变成可注入项（既有臂用 `ResponseModel`，rcgp 臂用 `RobustResponseModel`），于是升为五个。当前五臂：

| mode | 裁决 | 聚合 | 规划 | agent | 模型工厂 |
|---|---|---|---|---|---|
| `naive` | 全信 | 直通 | Baseline | Null | Response |
| `robust` | 信任盲 | 副本中位数 | Baseline | Null | Response |
| `rcgp` | 信任盲 | 直通 | Baseline | Null | **Robust(RCGP)** |
| `os` | 三级 QC | 副本方差 | 阶段 FSM+仲裁 | 编排层 | Response |
| `os-soft` | 三级 QC | **软降权隔离** | 阶段 FSM+仲裁 | 编排层 | Response |

约定：

- **新增一臂 = 加一个返回五元组的分支**，并把 mode 串登记进末尾未知-mode 报错的可用清单；别在别处硬编码 mode 判断。评测侧同步：`expos/eval/run_cell.py` 的 `_mode_for_arm` 负责 arm→mode 映射。
- **对照公平性是硬纪律**：一个对照臂相对被比臂只能在**受检维度**上有差异，其余四个注入点必须逐一对齐。例：`os-soft` 除聚合（软降权 vs 副本方差）外与 `os` 全同；`rcgp` 把稳健性放在**模型层**、与 `robust` 的**路由/聚合层**稳健形成干净对照。破坏这条 = 对比结论不成立。
- **CLI 暴露面有意收窄**：`expos run --mode` 只开放 `naive/robust/os`；`rcgp/os-soft/compare` 经 `scripts/run_loop.py`（全六档）或 `expos.eval` 侧使用——加臂时想清楚它进不进 `expos.cli` 的 `--mode` choices。
- **红线不因新臂松动**：任何臂的裁决/聚合/规划都**不得读 truth**，agent 分支必须仍是无写权的提案层；新臂照 §4 配对抗场景测试（至少一条假最优狙击断言）。

## 7. expos-lint（不变量静态守门）

提交前跑 `python3 scripts/expos_lint.py`（pre-commit 已挂同名 hook，`--preview` 开灰度规则）。
10 条规则 ID 化三分级（error/warn/preview），覆盖本仓真实踩过的坑：truth 字面泄漏（EXP001）、
mode 判定越出 `_policies_for_mode`（EXP002）、批次公式失联（EXP003）、truth_records 产地越界
（EXP004）、静默 except（EXP005）、依赖方向倒挂（EXP007）、shadow 内置名（EXP008）、
事件词表漂移（EXP010，warn——新事件 kind 必须先登记 `docs/EVENT_SCHEMA.md`）。
error 命中 = CI/pre-commit 挡下，别绕过；规则本身有测试（`tests/test_expos_lint.py`）。

---

有疑问先读 `docs/ARCHITECTURE.md` 与 `docs/ENGINEERING.md`；仍不清楚就在 PR 或 issue 里问，别猜。
