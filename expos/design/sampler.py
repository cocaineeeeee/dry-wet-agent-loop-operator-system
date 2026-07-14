"""候选生成：Sobol 空间填充 + 约束拒绝采样 + 最小距离去重（docs/ARCHITECTURE.md §5）。

BO 占位接口：propose_candidates 接受可选 score_fn（单位立方矩阵 → 分数，越大越好）。
M4 之前不实现响应模型——score_fn=None 时按 Sobol 序出点；接上 GP 后由 planner 传入
采集函数即可，本模块无需改动（参照 Ax GenerationStrategy 的分阶段思想）。

确定性：同 seed 同输入 → 同输出。不得 import 模拟器/QC/规划器/agent。
"""

from __future__ import annotations

from typing import Callable

import numpy as np
from scipy.stats import qmc

from expos.kernel.objects import Candidate, Constraint, DesignSpace
from expos.design.space import DesignError, check_constraints, dim, from_unit

ScoreFn = Callable[[np.ndarray], np.ndarray]


def _feasible_pool(
    space: DesignSpace,
    n: int,
    seed: int,
    restrictions: list[Constraint] | None,
    max_factor: int = 64,
) -> np.ndarray:
    """Sobol 采样 + 约束拒绝，返回 ≥n 行的可行单位立方矩阵；产不出即 DesignError。"""
    sobol = qmc.Sobol(d=dim(space), scramble=True, seed=seed)
    feasible: list[np.ndarray] = []
    drawn = 0
    batch = 1 << (max(n, 64) - 1).bit_length()  # 2 的幂，避免 Sobol 平衡性告警
    while len(feasible) < n:
        if drawn >= max_factor * max(n, 1):
            raise DesignError(
                f"约束拒绝采样失败：抽 {drawn} 点仅 {len(feasible)} 可行（需 {n}）——约束可能过紧"
            )
        u_batch = sobol.random(batch)
        drawn += batch
        for u in u_batch:
            if check_constraints(from_unit(space, u), restrictions):
                feasible.append(u)
    return np.asarray(feasible, dtype=float)


def _dedupe_min_dist(pool: np.ndarray, order: np.ndarray, n: int, min_dist: float) -> list[int]:
    """按 order 遍历，保留与已选点单位立方欧氏距离 ≥ min_dist 的前 n 个索引。"""
    chosen: list[int] = []
    for idx in order:
        u = pool[idx]
        if all(np.linalg.norm(u - pool[j]) >= min_dist for j in chosen):
            chosen.append(int(idx))
            if len(chosen) == n:
                break
    return chosen


def sobol_candidates(
    space: DesignSpace,
    n: int,
    seed: int,
    restrictions: list[Constraint] | None = None,
    min_dist: float = 0.0,
) -> list[Candidate]:
    """第 1 轮空间填充：scrambled Sobol + 约束拒绝（+ 可选去重）。"""
    return propose_candidates(
        space, n, seed, score_fn=None, restrictions=restrictions,
        pool_size=None, min_dist=min_dist, source="sobol",
    )


def propose_candidates(
    space: DesignSpace,
    n: int,
    seed: int,
    score_fn: ScoreFn | None = None,
    restrictions: list[Constraint] | None = None,
    pool_size: int | None = 2048,
    min_dist: float = 0.05,
    source: str = "bo",
) -> list[Candidate]:
    """统一候选生成入口（BO 占位）。

    score_fn=None：按 Sobol 序取前 n 个（空间填充）；
    score_fn 给定：在可行池上打分，从高到低取 n 个（含最小距离去重）。
    """
    if n <= 0:
        raise DesignError("n 必须为正")
    pool_n = pool_size if (score_fn is not None and pool_size) else n * 4 if min_dist > 0 else n
    pool = _feasible_pool(space, max(pool_n, n), seed, restrictions)
    if score_fn is not None:
        scores = np.asarray(score_fn(pool), dtype=float).ravel()
        if scores.shape[0] != pool.shape[0]:
            raise DesignError("score_fn 返回长度与池不符")
        order = np.argsort(-scores, kind="stable")
    else:
        order = np.arange(pool.shape[0])
    chosen = _dedupe_min_dist(pool, order, n, min_dist)
    if len(chosen) < n:
        raise DesignError(
            f"去重后仅得 {len(chosen)}/{n} 个候选——min_dist={min_dist} 过大或池过小"
        )
    rationale = "sobol space filling" if score_fn is None else "score-ranked (BO placeholder)"
    return [
        Candidate(params=from_unit(space, pool[i]), source=source, rationale=rationale)
        for i in chosen
    ]
