"""逐轮轨迹 JSONL（docs/M9_PROTOCOL.md §4.1）—— Olympus `Campaign` 字段子集 + expos 增补。

从 run 目录（experiments / observations / events / report/score.json）逐轮**综合一行**
到 `report/trajectory.jsonl`：轮次、臂、场景、种子三元组、best_trusted、best_true_so_far、
regret（事后回填自 score.json）、n_by_trust、generator/kappa（设计溯源）、contaminated_ratio。

**幂等覆盖写**：整文件重写（非追加），同一 run 多次调用产出恒等——断点续跑/重评分不留脏行。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from expos.eval.scoring import score_run
from expos.kernel.store import RunStore

_KAPPA_RE = re.compile(r"kappa\s*=\s*([0-9.]+)")


def _kappa_of(acquisition: str | None) -> float | None:
    if not acquisition:
        return None
    m = _KAPPA_RE.search(acquisition)
    return float(m.group(1)) if m else None


def write_trajectory(
    run_dir: str | Path,
    domain_yaml: str | Path,
    arm: str,
    scenario_id: str,
    seeds: dict,
) -> Path:
    """逐轮一行 JSONL 到 `run_dir/report/trajectory.jsonl`，幂等覆盖写，返回其 Path。

    regret / best_true_so_far / contaminated_ratio 来自 score.json（若缺则现算 score_run），
    generator / kappa 来自各轮 ExperimentObject 的设计溯源，n_by_trust 来自逐轮信任计数。
    """
    run_dir = Path(run_dir)
    score_path = run_dir / "report" / "score.json"
    if score_path.exists():
        score = json.loads(score_path.read_text(encoding="utf-8"))
    else:
        score = score_run(run_dir, domain_yaml)
    per_round: dict[int, dict[str, Any]] = {d["round"]: d for d in score["rounds"]}

    store = RunStore(run_dir, create=False)
    exp_by_round: dict[int, Any] = {e.round_id: e for e in store.list_experiments()}

    lines: list[str] = []
    for r in sorted(per_round):
        d = per_round[r]
        exp = exp_by_round.get(r)
        generator = exp.provenance.generator if exp is not None else None
        kappa = _kappa_of(exp.provenance.acquisition) if exp is not None else None
        row = {
            "round": r,
            "arm": arm,
            "scenario_id": scenario_id,
            "seeds": seeds,
            "best_trusted": d.get("best_trusted"),
            "best_true_so_far": d.get("best_true_so_far"),
            "regret": d.get("simple_regret"),
            "n_by_trust": {
                "trusted": d["n_trusted"],
                "suspect": d["n_suspect"],
                "failed": d["n_failed"],
            },
            "generator": generator,
            "kappa": kappa,
            "contaminated_ratio": d.get("contaminated_in_training"),
        }
        lines.append(json.dumps(row, ensure_ascii=False))

    out = run_dir / "report" / "trajectory.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return out
