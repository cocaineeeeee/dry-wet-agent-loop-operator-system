"""M9 RCGP-UCB 鲁棒臂验收测试：干净数据零成本（≈标准 GP）、幅度腐败鲁棒
（离群邻域预测仍近真值而标准 sklearn GP 被拉飞）、权重单调有界、TRUSTED 守门、
确定性、snapshot 数据敏感、依赖隔离。"""

from pathlib import Path

import numpy as np
import pytest
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel

from expos.kernel.objects import (
    Budget,
    Candidate,
    DesignProvenance,
    DesignSpace,
    ExecutionReq,
    ExperimentObject,
    LayoutMeta,
    MeasuredResult,
    Objective,
    ObservationObject,
    Routing,
    TrustLevel,
    VariableDef,
)
from expos.models.robust_gp import RobustGPError, RobustResponseModel

ROOT = Path(__file__).resolve().parent.parent


def make_space() -> DesignSpace:
    return DesignSpace(
        name="toy",
        variables=[
            VariableDef(name="x1", low=0.0, high=1.0),
            VariableDef(name="x2", low=0.0, high=1.0),
        ],
    )


def truth(p) -> float:
    return float(np.exp(-((p["x1"] - 0.6) ** 2 + (p["x2"] - 0.4) ** 2) / 0.08))


def make_training(n=30, seed=0, outlier_idx=(), outlier_shift=50.0,
                  trust=TrustLevel.TRUSTED, routing=Routing.TO_RESPONSE_MODEL):
    """返回 (space, exp, obs, points)。points[i]=(params, clean_value)——供对照真值。
    outlier_idx 中的观测 value += outlier_shift（幅度巨大的离群腐败）。"""
    rng = np.random.default_rng(seed)
    space = make_space()
    cands, obs, points = [], [], []
    oset = set(outlier_idx)
    for i in range(n):
        p = {"x1": float(rng.uniform()), "x2": float(rng.uniform())}
        clean = truth(p) + float(rng.normal(0, 0.01))
        val = clean + (outlier_shift if i in oset else 0.0)
        c = Candidate(params=p, source="test")
        cands.append(c)
        points.append((p, clean))
        obs.append(ObservationObject(
            exp_id="exp_t", round_id=0, cand_id=c.cand_id,
            result=MeasuredResult(metric="q", value=val),
            layout_meta=LayoutMeta(well_id=f"A{i + 1}", row=0, col=i),
            trust=trust, routing=routing,
        ))
    exp = ExperimentObject(
        round_id=0, domain="toy",
        objective=Objective(name="t", metric="q"),
        design_space=space, candidates=cands,
        budget=Budget(wells_total=100, rounds_total=2),
        execution_req=ExecutionReq(adapter="sim_crystal"),
        provenance=DesignProvenance(generator="test"),
    )
    return space, exp, obs, points


def _sklearn_gp(space, obs, points):
    """朴素 sklearn GP（对照组）：直接吃全部（含离群）观测。"""
    X = np.array([[p["x1"], p["x2"]] for p, _ in points])
    y = np.array([o.result.value for o in obs])
    k = ConstantKernel(1.0) * Matern(length_scale=0.3, nu=2.5) + WhiteKernel(1e-2)
    gp = GaussianProcessRegressor(kernel=k, normalize_y=True, alpha=1e-8,
                                  n_restarts_optimizer=2, random_state=0)
    gp.fit(X, y)
    return gp


# ------------------------------------------------------------ 零成本（干净数据）

def test_clean_data_matches_standard_gp():
    """零成本鲁棒：干净数据上与朴素 GP 预测同数量级（平台内退化为标准 GP）。"""
    space, exp, obs, points = make_training(n=40, seed=1)
    rob = RobustResponseModel(space, seed=0).fit(obs, [exp])
    gp = _sklearn_gp(space, obs, points)

    grid = np.random.default_rng(7).uniform(size=(200, 2))
    mu_rob, _ = rob.predict(grid)
    mu_gp = gp.predict(grid)
    truths = np.array([truth({"x1": x[0], "x2": x[1]}) for x in grid])
    mse_rob = float(np.mean((mu_rob - truths) ** 2))
    mse_gp = float(np.mean((mu_gp - truths) ** 2))
    # 同数量级：鲁棒臂在干净数据上不比标准 GP 差一个量级以上
    assert mse_rob < 10 * mse_gp + 1e-6
    assert mse_rob < 0.05  # 且绝对上都拟合得不错


# ------------------------------------------------------------ 幅度腐败鲁棒

def test_robust_to_large_outliers_vs_naive_gp():
    """注入 3 个幅度巨大离群（y+=50）：朴素 GP 在离群邻域被拉飞，
    RobustResponseModel 仍接近真值（对比断言）。"""
    outlier_idx = (5, 15, 25)
    space, exp, obs, points = make_training(n=40, seed=2, outlier_idx=outlier_idx)
    rob = RobustResponseModel(space, seed=0).fit(obs, [exp])
    gp = _sklearn_gp(space, obs, points)

    rob_errs, gp_errs = [], []
    for i in outlier_idx:
        p, clean = points[i]
        x = np.array([[p["x1"], p["x2"]]])
        mu_rob, _ = rob.predict(x)
        mu_gp = gp.predict(x)
        rob_errs.append(abs(float(mu_rob[0]) - clean))
        gp_errs.append(abs(float(mu_gp[0]) - clean))

    # 朴素 GP 被离群拉飞（误差量级 ~ shift 的一部分），鲁棒臂近真值
    assert max(rob_errs) < 2.0, f"鲁棒臂离群邻域误差过大: {rob_errs}"
    assert min(gp_errs) > 5.0, f"朴素 GP 未被拉飞（对照失效）: {gp_errs}"
    # 逐点鲁棒臂显著优于朴素 GP
    for re_, ge in zip(rob_errs, gp_errs):
        assert re_ < ge


def test_robust_clean_region_unaffected_by_outliers():
    """离群点不污染远端干净区域预测（对比无离群拟合仍接近）。"""
    outlier_idx = (5, 15, 25)
    space, exp, obs, points = make_training(n=40, seed=2, outlier_idx=outlier_idx)
    rob = RobustResponseModel(space, seed=0).fit(obs, [exp])
    # 峰值邻域（无离群）预测仍近真值
    mu, _ = rob.predict({"x1": 0.6, "x2": 0.4})
    assert mu[0] == pytest.approx(1.0, abs=0.25)


# ------------------------------------------------------------ 权重函数性质

def test_weight_monotone_and_bounded():
    """Plateau-IMQ 权重：平台内恒定=β，平台外随 |r| 单调不增，处处有界 (0, β]。"""
    space, exp, obs, _ = make_training(n=30, seed=3)
    rob = RobustResponseModel(space, seed=0, plateau_L=0.2, imq_c=0.1).fit(obs, [exp])
    beta = np.sqrt(rob._noise_var / 2.0)  # β = σ/√2

    r = np.linspace(-5.0, 5.0, 401)
    infl, _ = rob._corruption_terms(r)
    w = beta / np.sqrt(infl)  # w = β·(1+u²)^(−1/2) = β/√infl
    # 有界：0 < w ≤ β
    assert np.all(w > 0) and np.all(w <= beta + 1e-12)
    # 平台内 = β
    inside = np.abs(r) <= rob._L
    assert np.allclose(w[inside], beta)
    # |r| 增大 → w 单调不增（对 |r| 排序后）
    order = np.argsort(np.abs(r))
    w_sorted = w[order]
    assert np.all(np.diff(w_sorted) <= 1e-12)
    # 极端腐败 → 权重趋 0（软剪裁）
    assert w[np.argmax(np.abs(r))] < 0.05 * beta


# ------------------------------------------------------------ TRUSTED 守门

def test_fit_rejects_untrusted_structurally():
    space, exp, obs, _ = make_training(n=10, seed=4)
    bad = obs[3].model_copy(deep=True)
    bad.trust = TrustLevel.SUSPECT
    bad.routing = Routing.TO_FAILURE_MODEL
    with pytest.raises(RobustGPError):
        RobustResponseModel(space).fit(obs[:3] + [bad], [exp])
    pending = obs[4].model_copy(deep=True)
    pending.trust = TrustLevel.PENDING
    pending.routing = None
    with pytest.raises(RobustGPError):
        RobustResponseModel(space).fit([pending], [exp])
    quarantined = obs[5].model_copy(deep=True)
    quarantined.routing = Routing.QUARANTINE  # TRUSTED 但路由不对也拒绝
    with pytest.raises(RobustGPError):
        RobustResponseModel(space).fit([quarantined], [exp])


def test_fit_requires_value_and_known_entry():
    space, exp, obs, _ = make_training(n=5, seed=5)
    noval = obs[0].model_copy(deep=True)
    noval.result = MeasuredResult(metric="q", value=None)
    with pytest.raises(RobustGPError):
        RobustResponseModel(space).fit([noval], [exp])
    orphan = obs[1].model_copy(deep=True)
    orphan.cand_id = "cand_ghost"
    with pytest.raises(RobustGPError):
        RobustResponseModel(space).fit([orphan], [exp])
    with pytest.raises(RobustGPError):
        RobustResponseModel(space).fit([], [exp])


def test_predict_before_fit_raises():
    with pytest.raises(RobustGPError):
        RobustResponseModel(make_space()).predict({"x1": 0.5, "x2": 0.5})


# ------------------------------------------------------------ 采集与方向

def test_score_pool_and_kappa():
    space, exp, obs, _ = make_training(n=30, seed=6)
    model = RobustResponseModel(space, seed=0, kappa=0.0).fit(obs, [exp])
    pool = np.random.default_rng(0).uniform(size=(256, 2))
    s0 = model.score_pool(pool)
    assert s0.shape == (256,)
    best_at = pool[int(np.argmax(s0))]
    assert np.linalg.norm(best_at - np.array([0.6, 0.4])) < 0.3  # κ=0 逐利峰值
    model.kappa = 5.0
    s5 = model.score_pool(pool)
    assert not np.allclose(s0, s5)  # κ 生效


def test_direction_minimize_flips():
    space, exp, obs, _ = make_training(n=30, seed=7)
    m_max = RobustResponseModel(space, direction="maximize", seed=0).fit(obs, [exp])
    m_min = RobustResponseModel(space, direction="minimize", seed=0).fit(obs, [exp])
    mu_max, _ = m_max.predict({"x1": 0.6, "x2": 0.4})
    mu_min, _ = m_min.predict({"x1": 0.6, "x2": 0.4})
    assert mu_max[0] == pytest.approx(mu_min[0], abs=0.1)  # 预测换回原方向


# ------------------------------------------------------------ 确定性 & 指纹

def test_deterministic():
    space, exp, obs, _ = make_training(n=30, seed=8)
    m1 = RobustResponseModel(space, seed=0).fit(obs, [exp])
    m2 = RobustResponseModel(space, seed=0).fit(obs, [exp])
    pool = np.random.default_rng(3).uniform(size=(50, 2))
    assert np.array_equal(m1.score_pool(pool), m2.score_pool(pool))
    assert m1.snapshot() == m2.snapshot()
    assert (m1._length_scale, m1._signal_var, m1._noise_var) == (
        m2._length_scale, m2._signal_var, m2._noise_var)


def test_snapshot_stable_and_data_sensitive():
    space, exp, obs, _ = make_training(n=20, seed=9)
    m1 = RobustResponseModel(space, seed=0).fit(obs, [exp])
    m2 = RobustResponseModel(space, seed=0).fit(list(reversed(obs)), [exp])  # 行序无关
    assert m1.snapshot() == m2.snapshot()
    m3 = RobustResponseModel(space, seed=0).fit(obs[:19], [exp])  # 数据变 → 指纹变
    assert m3.snapshot() != m1.snapshot()
    fresh = RobustResponseModel(space)
    assert fresh.snapshot() != m1.snapshot()  # 未训练指纹不同


# ------------------------------------------------------------ 依赖隔离

def test_dependency_isolation():
    src = (ROOT / "expos" / "models" / "robust_gp.py").read_text(encoding="utf-8")
    forbidden = ("expos.adapters", "expos.qc", "expos.planner", "expos.agent", "ui.", "truth")
    hits = [f for f in forbidden if f in src]
    assert hits == [], f"robust_gp.py 引用了禁区: {hits}"
