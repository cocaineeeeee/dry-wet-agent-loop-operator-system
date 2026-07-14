"""M7 Kriging Believer 批量选点验收：q=1 退化为 score_pool argmax、q>1 去聚集、
不改 self 状态（snapshot/predict 前后一致）、两种噪声模式、确定性、非法参数响亮失败。"""

import numpy as np
import pytest

from expos.kernel.objects import Routing, TrustLevel
from expos.models.response_gp import ModelError, ResponseModel

from tests.test_response_model import make_training


def _pool(n=200, seed=7):
    return np.random.default_rng(seed).uniform(size=(n, 2))


def test_q1_matches_score_pool_argmax():
    space, exp, obs = make_training(n=30, seed=2)
    model = ResponseModel(space, seed=0).fit(obs, [exp])
    pool = _pool()
    got = model.select_batch_kb(pool, 1)
    assert got == [int(np.argmax(model.score_pool(pool)))]


def test_q5_distinct_and_dispersed():
    """KB 的去聚集效应：q=5 两两最小间距 > 纯 top-5-by-score 的最小间距。
    构造一簇挤在峰值 (0.6,0.4) 附近的候选：纯 top-5-by-score 会全落在簇内（间距极小），
    KB 选一个后条件化使簇内方差塌缩、UCB 下降 → 后续点散开。"""
    space, exp, obs = make_training(n=15, seed=2)
    model = ResponseModel(space, seed=0).fit(obs, [exp])
    rng = np.random.default_rng(11)
    cluster = np.array([0.3, 0.7]) + rng.uniform(-0.03, 0.03, size=(25, 2))
    pool = np.vstack([cluster, rng.uniform(size=(175, 2))])
    kb = model.select_batch_kb(pool, 5)
    assert len(kb) == 5 and len(set(kb)) == 5  # 互异

    def min_pair_dist(idxs):
        pts = pool[idxs]
        return min(
            np.linalg.norm(pts[i] - pts[j])
            for i in range(len(pts))
            for j in range(i + 1, len(pts))
        )

    top5 = list(np.argsort(model.score_pool(pool))[::-1][:5])
    assert min_pair_dist(kb) > min_pair_dist(top5)


def test_deterministic():
    space, exp, obs = make_training(n=30, seed=2)
    model = ResponseModel(space, seed=0).fit(obs, [exp])
    pool = _pool()
    assert model.select_batch_kb(pool, 5) == model.select_batch_kb(pool, 5)


def test_does_not_mutate_model():
    space, exp, obs = make_training(n=30, seed=2)
    model = ResponseModel(space, seed=0).fit(obs, [exp])
    pool = _pool()
    snap_before = model.snapshot()
    pred_before = model.predict({"x1": 0.6, "x2": 0.4})
    n_before = model.n_train
    model.select_batch_kb(pool, 5)
    assert model.snapshot() == snap_before
    assert model.n_train == n_before
    mu_a, sd_a = model.predict({"x1": 0.6, "x2": 0.4})
    assert np.allclose(mu_a, pred_before[0]) and np.allclose(sd_a, pred_before[1])


def test_per_point_alpha_mode():
    """per-point alpha（去 WhiteKernel）模式下 KB 也工作。"""
    space, exp, obs = make_training(n=30, seed=4)
    alpha = np.full(len(obs), 4e-4)
    model = ResponseModel(space, seed=0).fit(obs, [exp], per_point_alpha=alpha)
    assert model._noise_var_y == 0.0  # 无 WhiteKernel
    pool = _pool()
    kb = model.select_batch_kb(pool, 5)
    assert len(set(kb)) == 5
    assert kb[0] == int(np.argmax(model.score_pool(pool)))  # 首点仍是 score argmax


def test_illegal_q_and_pool_raise():
    space, exp, obs = make_training(n=10, seed=2)
    model = ResponseModel(space, seed=0).fit(obs, [exp])
    pool = _pool(n=8)
    with pytest.raises(ModelError):
        model.select_batch_kb(pool, 0)
    with pytest.raises(ModelError):
        model.select_batch_kb(pool, 9)  # q > len(pool)
    with pytest.raises(ModelError):
        model.select_batch_kb(np.empty((0, 2)), 1)  # 空池
    with pytest.raises(ModelError):
        model.select_batch_kb(pool, 2.0)  # 非整数


def test_untrained_raises():
    space, _, _ = make_training(n=5)
    with pytest.raises(ModelError):
        ResponseModel(space).select_batch_kb(_pool(n=10), 3)
