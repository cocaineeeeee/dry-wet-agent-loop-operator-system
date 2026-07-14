# 常驻变异语料库（mutation corpus）

红队定向语义变异（MU 第一波 + MU2 第二波 + F3b 表演性四变异）的**存活变异清单化**
（ARCHITECTURE_V2 §2 运营配方 / `docs/research_mutation_ci.md` 的语料清单化设计）。每条
存活变异都对应一处**测试守门缺口**（产品码正确——是断言没盯住，不是 bug），本目录把这些
缺口固化成可夜跑回归的语料，防止守门断言在后续演化里被误删/削弱而无人察觉。

## 文件

- **`MANIFEST.tsv`** — 人读台账：`id / wave / 目标 file:line / 变异摘要 / 击杀测试 /
  状态 / 理由`。38 行 = MU2 存活 17 + MU 第一波存活 17 + F3b 四变异。
- **`corpus.json`** — 机读台账：外加内联的 `old/new` 锚点（脱离红队 scratch，常驻自足）
  与 `auto` 标志（是否参与自动 patch-test-restore）。
- **`run_corpus.py`** — 夜跑驱动：逐条 `patch → 跑击杀测试期望转红 → 无条件恢复`。

## 状态口径

| 状态 | 含义 | 是否驱动 |
|---|---|---|
| `killed` | 已由 `kill_test` 覆盖：施加变异必转红 | 是（`auto=true`） |
| `waived` | 等价/近等价或"钉常数会僵化标定"——豁免（理由在 MANIFEST） | 否 |
| `deferred_p0` | qc/checks.py 变异，归 P0 修复 agent（`tests/test_qc_checks.py`） | 否 |
| `open` | 真缺口但本波未建断言（需重夹具）；建议断言见 `REPORT_R3_MU.md` | 否 |

当前：**killed 22 / waived 8 / deferred_p0 7 / open 1**。

## 夜跑用法

```bash
# 全量语料回归（对每条 killed 变异 patch-test-restore；任一 SURVIVED → 退出码 1）
PYTHONDONTWRITEBYTECODE=1 python tests/mutants/run_corpus.py

# 只跑子集
PYTHONDONTWRITEBYTECODE=1 python tests/mutants/run_corpus.py V4 Y1 D1

# 只列语料（不施加变异）
python tests/mutants/run_corpus.py --list
```

产品码全程只读：`run_one` 的 `finally` 无条件写回原文件，异常/中断亦不残留。若报
`ANCHOR 锚点计数≠1`，说明产品码演化使 `old` 锚点漂移——更新 `corpus.json` 的 old/new
（不是放松断言）。

## 与常规测试套件的关系

每条 `killed` 变异的击杀断言**已并入常规套件**（`tests/test_mut_*.py`，正常码下全绿，
随 `pytest tests/` 一起跑）。`run_corpus.py` 是**额外**的健壮度校验：确认这些断言确实
"抓得住"对应变异，而非恰好在正常码下为真的空断言。二者分工 = 常规套件保功能、语料保守门。

## 维护

- 新一波红队存活变异到货：把 `id/file/old/new/summary/kill_test/status/reason` 追加进
  `corpus.json`（生成器 `scratchpad` 留档），补对应 `tests/test_mut_*.py` 断言，跑
  `run_corpus.py <新id>` 确认转红。
- `open` 条目补上断言后改 `status=killed` 并填 `kill_test`。
- `deferred_p0` 条目由 P0 修复 agent 在 `tests/test_qc_checks.py` 落断言后回收。
