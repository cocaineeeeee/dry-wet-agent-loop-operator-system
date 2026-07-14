"""expos.eval —— M9 事后评分与轨迹层（评分是闭环之外的**叶子**）。

结构性地位（docs/ARCHITECTURE.md §11 评分方法 + 公理 6）：
- truth sidecar（`truth/round_*.jsonl`）的**唯一合法读者**是本包的事后评分器；
  qc/models/planner/agent/loop 一律禁读真值。本包在闭环**之外、之后**运行，
  从不参与任何跑内决策——因此读 truth 不违反公理 6，反而是"系统没被伪影骗到"
  这一论断可被定量证明的前提。
- 依赖方向单向：本包 import kernel/adapters/domain，**没有任何 expos 模块 import 本包**
  （红线：评分是叶子，见 tests/test_eval.py 的源扫描断言）。

对外导出：EvalError、load_truth/score_run（scoring）、write_trajectory（trajectory）。
"""

from __future__ import annotations

from expos.eval.scoring import EvalError, load_truth, score_run
from expos.eval.trajectory import write_trajectory

__all__ = ["EvalError", "load_truth", "score_run", "write_trajectory"]
