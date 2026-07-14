"""变异语料击杀：models/robust_gp.py RCGP 采集方向（MU2 R6/R1）。

- R6 [P1]：score_pool = μ^R + κ·σ^R。符号翻转成 μ − κ·σ^R 后，高 f-std 池点分数随 κ
  **下降**。旧测试只查"κ 有影响"不查方向。钉：高不确定池点分数随 κ 单调增。
- R1 [P2]：select_batch_kb 首选 UCB-argmax。改成 argmin 选到最差点。钉：首选 == argmax。
"""

import numpy as np

from expos.models.robust_gp import RobustResponseModel

from tests.test_robust_gp import make_space, make_training


def test_score_pool_ucb_increases_with_kappa():
    """UCB 探索项符号：高 f-std 池点分数随 κ 单调增（μ + κσ）。
    翻转成 μ − κσ 后，s(κ=5) < s(κ=0) 于高 σ 点 → 断言必红。"""
    space, exp, obs, _ = make_training(n=30, seed=6)
    model = RobustResponseModel(space, seed=0, kappa=0.0).fit(obs, [exp])
    pool = np.random.default_rng(0).uniform(size=(256, 2))

    model.kappa = 0.0
    s0 = model.score_pool(pool)  # = μ^R
    model.kappa = 5.0
    s5 = model.score_pool(pool)  # = μ^R + 5σ^R（正确）/ μ^R − 5σ^R（变异）
    diff = s5 - s0               # = 5σ^R ≥ 0（正确）
    assert np.all(diff >= -1e-9), "UCB 探索项符号翻转：增大 κ 反而降低分数"
    assert np.max(diff) > 1e-2, "池中存在高 f-std 点，κ 应显著抬高其分数"


def test_select_batch_kb_first_pick_is_ucb_argmax():
    """KB 批量首选 = 池 UCB-argmax（μ^R+κσ^R 最优点）。argmax→argmin 变异选到最差点。"""
    space, exp, obs, _ = make_training(n=30, seed=6)
    model = RobustResponseModel(space, seed=0, kappa=1.0).fit(obs, [exp])
    pool = np.random.default_rng(1).uniform(size=(64, 2))
    sel = model.select_batch_kb(pool, q=1)
    assert sel[0] == int(np.argmax(model.score_pool(pool)))
