"""M4 响应模型验收测试：拟合/预测、TRUSTED 结构性守门、指纹稳定性、依赖隔离。"""

from pathlib import Path

import numpy as np
import pytest

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
from expos.models.response_gp import ModelError, ResponseModel

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


def make_training(n=30, seed=0, trust=TrustLevel.TRUSTED,
                  routing=Routing.TO_RESPONSE_MODEL):
    rng = np.random.default_rng(seed)
    space = make_space()
    cands, obs = [], []
    for i in range(n):
        p = {"x1": float(rng.uniform()), "x2": float(rng.uniform())}
        c = Candidate(params=p, source="test")
        cands.append(c)
        obs.append(ObservationObject(
            exp_id="exp_t", round_id=0, cand_id=c.cand_id,
            result=MeasuredResult(metric="q", value=truth(p) + float(rng.normal(0, 0.01))),
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
    return space, exp, obs


def test_fit_predict_on_synthetic():
    space, exp, obs = make_training()
    model = ResponseModel(space, seed=0).fit(obs, [exp])
    assert model.n_train == 30
    mu, sd = model.predict({"x1": 0.6, "x2": 0.4})
    assert mu[0] == pytest.approx(1.0, abs=0.1)  # 峰值附近
    mu_far, _ = model.predict({"x1": 0.05, "x2": 0.95})
    assert mu_far[0] < mu[0]
    mu2, sd2 = model.predict(np.array([[0.6, 0.4], [0.05, 0.95]]))  # 单位阵输入
    assert mu2.shape == (2,) and sd2.shape == (2,) and np.all(sd2 >= 0)


def test_fit_rejects_untrusted_structurally():
    space, exp, obs = make_training(n=10)
    bad = obs[3].model_copy(deep=True)
    bad.trust = TrustLevel.SUSPECT
    bad.routing = Routing.TO_FAILURE_MODEL
    with pytest.raises(ModelError):
        ResponseModel(space).fit(obs[:3] + [bad], [exp])
    pending = obs[4].model_copy(deep=True)
    pending.trust = TrustLevel.PENDING
    pending.routing = None
    with pytest.raises(ModelError):
        ResponseModel(space).fit([pending], [exp])
    quarantined = obs[5].model_copy(deep=True)
    quarantined.routing = Routing.QUARANTINE  # TRUSTED 但路由不对也拒绝
    with pytest.raises(ModelError):
        ResponseModel(space).fit([quarantined], [exp])


def test_fit_requires_value_and_known_entry():
    space, exp, obs = make_training(n=5)
    noval = obs[0].model_copy(deep=True)
    noval.result = MeasuredResult(metric="q", value=None)
    with pytest.raises(ModelError):
        ResponseModel(space).fit([noval], [exp])
    orphan = obs[1].model_copy(deep=True)
    orphan.cand_id = "cand_ghost"
    with pytest.raises(ModelError):
        ResponseModel(space).fit([orphan], [exp])
    with pytest.raises(ModelError):
        ResponseModel(space).fit([], [exp])


def test_snapshot_stable_and_data_sensitive():
    space, exp, obs = make_training(n=20, seed=1)
    m1 = ResponseModel(space, seed=0).fit(obs, [exp])
    m2 = ResponseModel(space, seed=0).fit(list(reversed(obs)), [exp])  # 行序无关
    assert m1.snapshot() == m2.snapshot()
    m3 = ResponseModel(space, seed=0).fit(obs[:19], [exp])  # 数据变 → 指纹变
    assert m3.snapshot() != m1.snapshot()
    fresh = ResponseModel(space)
    assert fresh.snapshot() != m1.snapshot()  # 未训练指纹不同


def test_snapshot_row_order_invariant_with_duplicate_rows():
    """闭环里副本/哨兵产生相同 X 行、y 各异——指纹必须对输入顺序不变。"""
    space = make_space()
    p = {"x1": 0.5, "x2": 0.5}
    cands = [Candidate(cand_id=f"c{i}", params=p, source="t") for i in range(3)]
    exp = ExperimentObject(
        round_id=0, domain="toy", objective=Objective(name="t", metric="q"),
        design_space=space, candidates=cands,
        budget=Budget(wells_total=10, rounds_total=1),
        execution_req=ExecutionReq(adapter="sim_crystal"),
        provenance=DesignProvenance(generator="test"),
    )
    obs = [ObservationObject(
        exp_id="e", round_id=0, cand_id=c.cand_id,
        result=MeasuredResult(metric="q", value=v),
        layout_meta=LayoutMeta(well_id=f"A{i + 1}", row=0, col=i),
        trust=TrustLevel.TRUSTED, routing=Routing.TO_RESPONSE_MODEL,
    ) for i, (c, v) in enumerate(zip(cands, [0.3, 0.5, 0.4]))]
    s1 = ResponseModel(space, seed=0).fit(obs, [exp]).snapshot()
    s2 = ResponseModel(space, seed=0).fit([obs[2], obs[0], obs[1]], [exp]).snapshot()
    assert s1 == s2


def test_score_pool_and_kappa():
    space, exp, obs = make_training(n=30, seed=2)
    model = ResponseModel(space, seed=0, kappa=0.0).fit(obs, [exp])
    pool = np.random.default_rng(0).uniform(size=(256, 2))
    s0 = model.score_pool(pool)
    assert s0.shape == (256,)
    best_at = pool[int(np.argmax(s0))]
    assert np.linalg.norm(best_at - np.array([0.6, 0.4])) < 0.25  # κ=0 时逐利峰值
    model.kappa = 5.0
    s5 = model.score_pool(pool)
    assert not np.allclose(s0, s5)  # κ 生效


def test_direction_minimize_flips():
    space, exp, obs = make_training(n=30, seed=3)
    m_max = ResponseModel(space, direction="maximize", seed=0).fit(obs, [exp])
    m_min = ResponseModel(space, direction="minimize", seed=0).fit(obs, [exp])
    mu_max, _ = m_max.predict({"x1": 0.6, "x2": 0.4})
    mu_min, _ = m_min.predict({"x1": 0.6, "x2": 0.4})
    assert mu_max[0] == pytest.approx(mu_min[0], abs=0.05)  # 预测值换回原方向
    pool = np.random.default_rng(1).uniform(size=(128, 2))
    m_min.kappa = 0.0
    worst_at = pool[int(np.argmax(m_min.score_pool(pool)))]
    assert np.linalg.norm(worst_at - np.array([0.6, 0.4])) > 0.3  # minimize 内部找谷


def test_acquisition_uses_noise_free_f_std():
    """UCB 的 σ 必须是扣噪 f-std：y-std 含观测噪声会误导高噪区采集。"""
    space, exp, obs = make_training(n=40, seed=5)
    model = ResponseModel(space, seed=0).fit(obs, [exp])
    assert model._noise_var_y > 0  # WhiteKernel 学到了噪声
    pool = np.random.default_rng(2).uniform(size=(64, 2))
    _, y_sd = model._gp.predict(pool, return_std=True)
    f_sd = model._f_std(y_sd)
    assert np.all(f_sd <= y_sd + 1e-12) and f_sd.mean() < y_sd.mean()  # 严格扣噪
    assert np.all(np.isfinite(f_sd)) and np.all(f_sd >= 0)


def test_predict_before_fit_raises():
    with pytest.raises(ModelError):
        ResponseModel(make_space()).predict({"x1": 0.5, "x2": 0.5})


def test_response_model_dependency_isolation():
    src = (ROOT / "expos" / "models" / "response_gp.py").read_text(encoding="utf-8")
    forbidden = ("expos.adapters", "expos.qc", "expos.planner", "expos.agent", "ui.", "truth")
    hits = [f for f in forbidden if f in src]
    assert hits == [], f"response_gp.py 引用了禁区: {hits}"
