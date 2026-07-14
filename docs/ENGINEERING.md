# 工程规范（ENGINEERING）

本文件是 expos 从单机科研代码走向平台的**工程纪律基线**：版本策略、CHANGELOG 纪律、发布清单、代码棘轮（code ratchets），以及对 `pyproject.toml` 的**待批建议**。规范取自 references/ 下工业级与科学软件的实践（baybe / Ax / MADSci / aiida-core），按 expos 现实裁剪：0.x 早期、Python 3.11+、130+ pytest 用例其中 e2e 慢、无 CI 历史、中文注释多。

配套文件：`.pre-commit-config.yaml`（本地钩子）、`.github/workflows/ci.yml`（两层 CI）、`CONTRIBUTING.md`（流程与红线）。

> 注：`pyproject.toml` 本文件**不修改**，§6 给出建议字段，待维护者批准后单独提 PR 落地。

---

## 1. CI 分层（已落地于 ci.yml）

| 层 | 触发 | 内容 | 目标 |
|----|------|------|------|
| **fast** | 每次 push/PR | ruff check + ruff format --check + 快测试子集（`test_kernel/test_design/test_adapters/test_planner_stages`，本机 ~18s） | 秒级反馈，挡住 90% 低级错误 |
| **full** | fast 通过后 | 全量 `pytest`（含 e2e 闭环），Python **3.11 / 3.13** 矩阵，pip 缓存，每用例 `--timeout=300` | 回归保护；e2e 慢故单列 |

本机实测：快子集 105 用例 ~18s；全量 212 用例 ~3m48s（e2e 占主要耗时）。故 full job 设 `timeout-minutes: 30`、per-test 300s，留足余量。`references/` 与 `runs/` 改动经 `paths-ignore` 不触发 CI。

**分层演进方向**（参照 aiida-core 的 nightly 层）：待 e2e 进一步变慢，可拆出 `nightly` 定时层跑最慢的闭环对比（compare 模式），把 full 层压回 10min 内。

---

## 2. 版本策略：0.x 期 semver-ish

**结论：采用 SemVer 语义、停在 0.x，直到内核冻结。** 对齐 baybe / MADSci（均 pre-1.0 + SemVer + Keep a Changelog），不取 Ax 的 date-stamped 自定义格式。理由：expos 论点靠“内核稳定 + 可审计”，SemVer 的 MAJOR/MINOR/PATCH 语义正好承载“内核破坏性变更”这一最该被显式标注的信号。

0.x 期约定（业界惯例）：

- `0.MINOR.PATCH`。**0.x 阶段 MINOR 承担 breaking 语义**：内核对象/事件 schema/信任路由的破坏性变更 → 升 MINOR（`0.1 → 0.2`）；向后兼容的新功能/修复 → 升 PATCH。
- **不轻易上 1.0**：只有当两对象 + 事件日志 + 信任路由的公共契约稳定、外部有人依赖时才发 `1.0.0`。此后严格 SemVer（breaking 升 MAJOR）。
- 破坏性变更在 CHANGELOG 对应条目前缀 `**BREAKING**:`（学 MADSci keepachangelog skill），即便藏在 Changed/Removed 里也要标。

### Schema 演化用 checkpoint version 键

运行目录（`checkpoint.json`、事件日志、`truth/` sidecar）是 expos 的持久化契约，其演化**独立于**包版本，用显式版本键治理，避免“旧 run 目录被新代码静默误读”（这本身是一条红线的延伸）：

- 在 `checkpoint.json` 顶层写 `"checkpoint_version": <int>`（若尚无，建议补）。
- 事件 schema 演化时递增该键；`RunStore` 加载时**校验**版本：不认识的版本**响亮失败**（`ExposError`），绝不静默按新格式解析旧数据——这与 CONTRIBUTING §3“无静默降级”一致。
- 需要兼容旧 run 时提供**显式**迁移函数（`migrate_v1_to_v2`），而非隐式猜测。
- 包版本与 checkpoint_version 的对应关系记入本文件的下表（演化时维护）：

  | checkpoint_version | 引入包版本 | 变更摘要 |
  |--------------------|-----------|---------|
  | 1 | 0.1.0 | 初始：round/status/预算余额 |

---

## 3. CHANGELOG 纪律（Keep a Changelog 1.1.0）

**建立 `docs/CHANGELOG.md`，用 Keep a Changelog 1.1.0 格式**，规则取自 MADSci 的 `keepachangelog` skill：

- 六个固定标题、固定顺序，空的省略、不虚构：
  **Added → Changed → Deprecated → Removed → Fixed → Security**
- `[Unreleased]` 永远置顶；条目按时间倒序；日期用 ISO-8601（`## [0.2.0] - 2026-08-01`）。
- **已发布小节不可变**：改历史用“回填进 Unreleased 并注明”，绝不编辑旧版本段。发布后立刻留一个空的 `[Unreleased]`。
- 破坏性变更行内前缀 `**BREAKING**:`。
- **不记**：merge commit、纯 lint/格式 churn、仅测试改动、依赖 bump（除非修 CVE → 记 Security）、生成文件。
- **每个面向用户的 PR 必须动 CHANGELOG**（baybe 用 CI 强制；expos 现阶段先靠 PR 清单，见 §5 可选加 CI 校验）。

骨架（新建时）：

```markdown
# Changelog
本项目遵循 [Keep a Changelog](https://keepachangelog.com/) 与 [SemVer](https://semver.org/)。

## [Unreleased]
### Added
### Changed
### Fixed

## [0.1.0] - 2026-07-10
### Added
- 内核：两对象 + 事件日志 + 信任路由；naive 基线闭环。
```

---

## 4. Code Ratchets（代码棘轮，建议引入）

借 MADSci 的 `code-ratchets` skill：对**该逐步清退的模式**设一个硬编码期望计数，pre-commit 里 grep 计数比对。**双向失败**——

- `actual > expected` → “RATCHET FAILED”：阻止该模式扩散（比如新增裸 `except`）。
- `actual < expected` → “RATCHET DOWN”：也返回非零，强制把 `expected` 调低，把“清理进度”固化进配置（防止有人偷偷把改进又退回去）。

expos 最该上棘轮的模式（直接服务红线）：

| 模式 | 正则 | 理由 |
|------|------|------|
| 裸/宽异常吞掉 | `except\s*:` 或 `except Exception` 后接 `pass` | 对抗“无静默降级”红线，计数不得增加 |
| 决策模块读 truth | `truth` 出现在 `expos/qc\|models\|planner\|agent` | 对抗“真值隔离”红线，期望恒为 0 |
| Agent 直接落盘 | `open(` / `.write(` 出现在 `expos/agent/` | 对抗“Agent 无写权”红线，期望恒为 0 |

落地方式：`scripts/ratchet.py`（`RATCHETS` 字典：pattern/expected/glob/reason，支持 `--init`）+ 一个 `pass_filenames: false, always_run: true` 的本地 pre-commit 钩子；急救可用 `RATCHET_BYPASS=1` 临时放行。**红线类棘轮的 expected 应设为 0 并配注释说明“动它先读 CONTRIBUTING §3”。** 这是把三条红线从“评审靠人眼”升级为“机器逐提交拦截”的关键一步，建议尽早补 `scripts/ratchet.py`（不属本次四文件范围，单独 PR）。

---

## 5. 发布清单（cut a release）

0.x 期发布轻量，但每步都要留痕：

1. `pre-commit run --all-files` 全绿；`pytest -q` 全量绿（含 e2e）。
2. `CHECKPOINTS.md`：本次里程碑标记完成，无未备案的架构偏离。
3. `docs/CHANGELOG.md`：把 `[Unreleased]` 切成 `## [X.Y.Z] - <ISO 日期>`，破坏性项已标 `**BREAKING**:`；留新的空 `[Unreleased]`。
4. 若动了运行目录/事件 schema：递增 `checkpoint_version`，更新 §2 对应表，确认迁移函数就位。
5. 升版本号（见 §6 关于 dynamic version 的建议；手动则同步 `pyproject.toml` 的 `version`）。
6. 打 tag `vX.Y.Z`（与 CHANGELOG 小节一致），推送；确认 CI 全绿。
7. （可选）CI 增一个 `changelog` job 校验“每个 PR 是否动了 CHANGELOG”，学 baybe。

---

## 5b. 投稿前门禁：fresh-clone 整体 E2E（`scripts/preflight_e2e.sh`）

红队 WHO3 整体裁定风险#3（`mailbox/red_to_blue/014_who3-holistic-verdict.md`）：**干净环境整体
E2E 从未跑过**——本机 site-packages 会掩盖依赖声明缺口（典型如 W3-1：dev extra 缺 hypothesis，
本机全绿、干净 runner 收集阶段即炸）。`scripts/preflight_e2e.sh` 把这道门机器化：

```bash
bash scripts/preflight_e2e.sh                 # 完整跑；任一段失败即停、非零退出
bash scripts/preflight_e2e.sh --keep-workdir  # 保留临时目录（失败时默认保留）
bash scripts/preflight_e2e.sh --workdir DIR   # 指定工作目录（默认 mktemp）
```

五段流水（顺序执行，失败即停并打印失败段落名与日志路径，结尾输出 PASS/FAIL/SKIP 汇总表）：

| 段 | 内容 | 抓什么 |
|----|------|--------|
| 1 fresh-clone-sim | rsync 仓库到临时目录，排除 `runs/ references/ __pycache__ .hypothesis`（本仓非 git，等价 clean clone） | 隐性依赖被 gitignore 的产物（W3-3 类问题） |
| 2 venv-install | 全新 venv + `pip install -e ".[dev]"` | dev extra 依赖声明缺口（W3-1 类问题） |
| 3 pytest-full | 全量 `pytest -q`（`PYTHONDONTWRITEBYTECODE=1`、BLAS 线程钉 1） | 干净环境下的收集/夹具/顺序问题 |
| 4 smoke-run-cell | `gen_sweep` 出 S0.demo + `run_cell`（naive, seed=3, rounds=2）产出 `score.json` | 安装后的科学主路径真能跑 |
| 5 lint-docs | `expos_lint`（全仓 error+warn）+ `mkdocs build --strict`（pyproject 声明了 `docs` extra 则装它，否则临时装 mkdocs+mkdocs-material 兜底） | 红线静态守门 + 文档站出厂态 |

**门禁纪律**：**投稿、发版（§5 发布清单第 1 步之前）、以及归档快照（REPRODUCE.md 更新）前，
本脚本必须五段全 PASS**。任何一段红都是 blocker——不许"本机全绿所以没事"绕过；失败段落的
日志在工作目录 `_logs/` 下，按段落名对号。脚本本身不改仓库（全部产物落临时目录），可重复跑。

---

## 6. pyproject.toml 待批建议（本次不改，供审批）

维护者批准后单独提 PR 落地，避免与本次“四文件”改动纠缠：

- **`license` 与 `classifiers`**：现缺失。建议加
  ```toml
  license = { text = "MIT" }   # 或团队选定协议
  classifiers = [
    "Development Status :: 3 - Alpha",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.13",
    "Intended Audience :: Science/Research",
    "Topic :: Scientific/Engineering",
  ]
  ```
- **`dev` extra 补齐工具链**（当前只有 `pytest`），与 CI/pre-commit 对齐、版本锁步（学 baybe 的 lint extra 与钩子 rev 同步）：
  ```toml
  [project.optional-dependencies]
  dev = ["pytest", "pytest-timeout", "pre-commit", "ruff==0.15.20", "codespell==2.4.1"]
  ```
  `ruff`/`codespell` 版本务必与 `.pre-commit-config.yaml` 的 `rev` 保持一致，否则本地跑 pre-commit 与直接 `ruff check` 结果会漂移。
- **`[tool.ruff]`**：显式化规则，别吃隐式默认。建议 `line-length = 100`、`target-version = "py311"`、`lint.select = ["E", "F", "I", "W", "UP", "B"]`（含 flake8-bugbear `B`，能抓不少“静默降级/可疑 except”味道的坑）、`lint.exclude = ["references", "runs"]`。
- **`[tool.codespell]`**：把 `.pre-commit-config.yaml` 里的 `skip`/`ignore-words-list` 同步一份进 pyproject，使命令行 `codespell` 与钩子行为一致。
- **`[tool.pytest.ini_options]` 注册 `slow` marker**：给 e2e 打 `@pytest.mark.slow`，则 CI 快层可用 `-m "not slow"` 精确切分，替代现在按文件名硬列（更稳，不怕新增慢用例漏进快层）：
  ```toml
  markers = ["slow: 慢用例（e2e 闭环），CI full 层跑"]
  ```
- **`dynamic version`（可选）**：如引入 `setuptools_scm`，让版本从 git tag 派生（baybe/Ax 均如此），发布时只打 tag、不手改 `version`，减少漂移。

---

## 7. 参照来源

- **baybe**（Merck，工业级）：ruff 唯一 lint、tox 分层、SHA-pin actions、CI 强制改 CHANGELOG、覆盖率门槛、Keep a Changelog + SemVer + 0.x。
- **MADSci**：`keepachangelog` 与 `code-ratchets` 两个 skill 是本文件 §3/§4 的直接蓝本；pre-1.0 + ruff-only + PR 模板含开发者清单。
- **aiida-core**（老牌科学软件）：CI 真分层（matrix + service 容器 + nightly 慢测试）、`timeout-minutes`、`paths-ignore docs/`、mypy 渐进式（长 exclude 列表）——expos e2e 变重后可循此路演进。
- **Ax**（Meta）：作反例——date-stamped 自定义 changelog 与 ufmt-only，expos **不**采用其 changelog 格式。
