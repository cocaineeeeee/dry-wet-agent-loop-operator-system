"""M5 裁决/聚合策略验收（docs/M5_DESIGN.md §3/§3B，DEEP_REVIEW §3.1/§3.2/§2B）。

- NaivePolicy 与现 loop._route_naive 逐字段等价（回归红线）；
- QCPolicy 三档裁决走 lifecycle.adjudicate、事件齐全、缺报告 → PolicyError；
- 三个聚合策略形状/值正确（median n=2 保守选、alpha 对应、无副本兜底）；
- 确定性；依赖隔离（不 import adapters/agent/planner、无 truth 字样）。
"""

import numpy as np
import pytest

from expos.kernel.lifecycle import TrustPolicy, adjudicate
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
    QCCheck,
    QCReport,
    Routing,
    TrustLevel,
    VariableDef,
)
from expos.kernel.store import RunStore
from expos.qc.policy import (
    AggregationPolicy,
    MedianAggregation,
    NaivePolicy,
    PassthroughAggregation,
    PolicyError,
    QCPolicy,
    ReplicateVarianceAggregation,
    SoftTrustAggregation,
    VerdictPolicy,
)


# ---------------------------------------------------------------- 构造器

def make_exp(direction: str = "maximize", round_id: int = 0) -> ExperimentObject:
    space = DesignSpace(
        name="crystal",
        variables=[VariableDef(name="x", low=0.0, high=1.0)],
    )
    return ExperimentObject(
        round_id=round_id,
        domain="crystal",
        objective=Objective(name="q", metric="quality", direction=direction),
        design_space=space,
        active_vars=["x"],
        candidates=[Candidate(cand_id="cand_A", params={"x": 0.5})],
        budget=Budget(wells_total=384, rounds_total=8),
        execution_req=ExecutionReq(adapter="sim_crystal"),
        provenance=DesignProvenance(generator="sobol"),
    )


def make_obs(
    exp: ExperimentObject,
    cand_id: str = "cand_A",
    value: float | None = 0.7,
    obs_id: str | None = None,
    secondary: dict | None = None,
    well: str = "C4",
    qc: QCReport | None = None,
) -> ObservationObject:
    kwargs = {}
    if obs_id is not None:
        kwargs["obs_id"] = obs_id
    return ObservationObject(
        exp_id=exp.exp_id,
        round_id=exp.round_id,
        cand_id=cand_id,
        result=MeasuredResult(metric="quality", value=value, secondary=secondary or {}),
        layout_meta=LayoutMeta(well_id=well, row=2, col=3),
        qc=qc,
        **kwargs,
    )


# ================================================================ VerdictPolicy

def test_protocol_conformance():
    assert isinstance(NaivePolicy(), VerdictPolicy)
    assert isinstance(QCPolicy(lambda e, o, h: {}), VerdictPolicy)
    assert isinstance(PassthroughAggregation(), AggregationPolicy)
    assert isinstance(MedianAggregation(), AggregationPolicy)
    assert isinstance(ReplicateVarianceAggregation(), AggregationPolicy)


def test_naive_policy_matches_route_naive(tmp_path):
    """NaivePolicy.judge 与 loop._route_naive 逐字段一致（回归红线）。"""
    from expos.loop import _route_naive

    exp = make_exp()
    base = [make_obs(exp, obs_id=f"obs_{i}", value=0.5 + 0.1 * i) for i in range(3)]

    store_a = RunStore(tmp_path / "a")
    obs_a = [o.model_copy(deep=True) for o in base]
    _route_naive(store_a, obs_a)

    store_b = RunStore(tmp_path / "b")
    obs_b = [o.model_copy(deep=True) for o in base]
    ret = NaivePolicy().judge(store_b, obs_b, exp)

    assert ret is obs_b
    # 观测状态逐字段一致
    for a, b in zip(obs_a, obs_b):
        assert a.model_dump() == b.model_dump()
        assert b.trust == TrustLevel.TRUSTED
        assert b.routing == Routing.TO_RESPONSE_MODEL
        assert b.trust_confidence == 1.0
    # 落盘一致
    for oid in ("obs_0", "obs_1", "obs_2"):
        assert store_a.load_observation(oid).model_dump() == store_b.load_observation(oid).model_dump()
    # routing_bulk 事件 payload 结构一致
    pa = [e["payload"] for e in store_a.read_events("routing_bulk")]
    pb = [e["payload"] for e in store_b.read_events("routing_bulk")]
    assert pa == pb == [{"mode": "naive", "n": 3, "round_id": 0}]


def test_naive_policy_empty(tmp_path):
    store = RunStore(tmp_path / "run")
    NaivePolicy().judge(store, [], make_exp())
    assert store.read_events("routing_bulk")[0]["payload"] == {"mode": "naive", "n": 0, "round_id": None}


# ================================================================ QCPolicy

def _three_tier_runner(exp, obs_list, history):
    return {
        "obs_trusted": QCReport(checks=[QCCheck(name="value_range", level="hard", passed=True)]),
        "obs_suspect": QCReport(
            checks=[QCCheck(name="edge_effect", level="structural", passed=False, score=0.4)]
        ),
        "obs_failed": QCReport(
            checks=[QCCheck(name="value_range", level="hard", passed=False, score=1.0)]
        ),
    }


def test_qc_policy_three_tier_adjudication(tmp_path):
    exp = make_exp()
    store = RunStore(tmp_path / "run")
    obs = [
        make_obs(exp, obs_id="obs_trusted", value=0.7),
        make_obs(exp, obs_id="obs_suspect", value=0.9, well="A1"),
        make_obs(exp, obs_id="obs_failed", value=2.0, well="H8"),
    ]
    QCPolicy(_three_tier_runner, TrustPolicy(suspect_high=0.6, quarantine_low=0.3)).judge(store, obs, exp)

    by_id = {o.obs_id: o for o in obs}
    # 三档裁决与 §7.4 阈值表一致（走 lifecycle.adjudicate）
    assert (by_id["obs_trusted"].trust, by_id["obs_trusted"].routing) == (
        TrustLevel.TRUSTED, Routing.TO_RESPONSE_MODEL)
    assert (by_id["obs_suspect"].trust, by_id["obs_suspect"].routing) == (
        TrustLevel.SUSPECT, Routing.QUARANTINE)
    assert (by_id["obs_failed"].trust, by_id["obs_failed"].routing) == (
        TrustLevel.FAILED, Routing.TO_FAILURE_MODEL)
    # 每观测一条 routing 事件（route_observation 所发）
    assert len(store.read_events("routing")) == 3
    # 一条 qc_report 汇总事件
    reports = store.read_events("qc_report")
    assert len(reports) == 1
    payload = reports[0]["payload"]
    assert payload["round_id"] == 0
    assert (payload["n_trusted"], payload["n_suspect"], payload["n_failed"]) == (1, 1, 1)
    assert payload["check_counts"] == {"edge_effect": 1, "value_range": 1}
    # 报告已挂上观测并落盘
    assert store.load_observation("obs_suspect").qc is not None


def test_qc_policy_high_suspicion_to_failure_model(tmp_path):
    exp = make_exp()
    store = RunStore(tmp_path / "run")
    obs = [make_obs(exp, obs_id="obs_x", value=0.5)]
    runner = lambda e, o, h: {
        "obs_x": QCReport(checks=[QCCheck(name="glare", level="structural", passed=False, score=0.7)])
    }
    QCPolicy(runner).judge(store, obs, exp)
    # 嫌疑分 ≥0.6 → SUSPECT + TO_FAILURE_MODEL（§7.4）
    assert (obs[0].trust, obs[0].routing) == (TrustLevel.SUSPECT, Routing.TO_FAILURE_MODEL)


def test_qc_policy_missing_report_raises(tmp_path):
    exp = make_exp()
    store = RunStore(tmp_path / "run")
    obs = [make_obs(exp, obs_id="obs_x", qc=None)]
    with pytest.raises(PolicyError):
        QCPolicy(lambda e, o, h: {}).judge(store, obs, exp)


def test_qc_policy_attribution_is_log_before_data(tmp_path, monkeypatch):
    """归因写入遵守 log-before-data（WAL 纪律，R1 P3；对齐 lifecycle.route_observation）：
    崩于 append_event/save_observation 之间时，attribution 事件已在日志中，
    而非 failure_attr 落盘却无事件解释。"""
    from expos.kernel.objects import FailureAttribution

    exp = make_exp()
    store = RunStore(tmp_path / "run")
    obs = [make_obs(exp, obs_id="obs_s", value=0.9, well="A1")]
    runner = lambda e, o, h: (
        {"obs_s": QCReport(
            checks=[QCCheck(name="edge_effect", level="structural", passed=False, score=0.4)])},
        object(),  # plate 占位（attributor 为桩，不读它）→ 触发归因分支
    )
    attributor = lambda o, r, p, e: FailureAttribution(
        hypotheses=[], top_cause="edge_evaporation", confidence=0.9)

    orig_save = store.save_observation

    def failing_save(o):
        if o.failure_attr is not None:  # 仅在"归因落盘"这步崩（route_observation 那步 failure_attr 尚为 None）
            raise RuntimeError("boom during attribution save")
        return orig_save(o)

    monkeypatch.setattr(store, "save_observation", failing_save)

    with pytest.raises(RuntimeError):
        QCPolicy(runner, TrustPolicy(suspect_high=0.6, quarantine_low=0.3),
                 attributor=attributor).judge(store, obs, exp)

    # 日志领先视图：attribution 事件已落，尽管 failure_attr 视图写入崩溃
    evs = store.read_events("attribution")
    assert len(evs) == 1
    assert evs[0]["payload"]["top_cause"] == "edge_evaporation"


def test_qc_policy_respects_thresholds_from_config(tmp_path):
    """trust 阈值来自构造参数（域配置 cfg.trust）。"""
    exp = make_exp()
    store = RunStore(tmp_path / "run")
    obs = [make_obs(exp, obs_id="obs_x", value=0.5)]
    runner = lambda e, o, h: {
        "obs_x": QCReport(checks=[QCCheck(name="c", level="structural", passed=False, score=0.4)])
    }
    # 抬高 quarantine_low 到 0.5：0.4 嫌疑分现在落回 TRUSTED
    QCPolicy(runner, TrustPolicy(suspect_high=0.9, quarantine_low=0.5)).judge(store, obs, exp)
    assert obs[0].trust == TrustLevel.TRUSTED


# ================================================================ 聚合策略

def _trusted(exp, cand_id, value, obs_id, secondary=None, well="C4"):
    o = make_obs(exp, cand_id=cand_id, value=value, obs_id=obs_id, secondary=secondary, well=well)
    o.trust = TrustLevel.TRUSTED
    o.routing = Routing.TO_RESPONSE_MODEL
    return o


def test_passthrough_aggregation():
    exp = make_exp()
    trusted = [_trusted(exp, "cand_A", 0.7, "o1"), _trusted(exp, "cand_B", 0.5, "o2")]
    out, alpha = PassthroughAggregation().prepare(trusted, [exp])
    assert out == trusted
    assert alpha is None


def test_median_aggregation_n2_conservative_maximize():
    exp = make_exp(direction="maximize")
    trusted = [
        _trusted(exp, "cand_A", 0.8, "o1", secondary={"g": 4}),
        _trusted(exp, "cand_A", 0.6, "o2", secondary={"g": 6}),
    ]
    out, alpha = MedianAggregation().prepare(trusted, [exp])
    assert alpha is None
    assert len(out) == 1
    m = out[0]
    # n=2 maximize → 保守取 min
    assert m.result.value == pytest.approx(0.6)
    assert m.cand_id == "cand_A"
    assert m.trust == TrustLevel.TRUSTED and m.routing == Routing.TO_RESPONSE_MODEL
    assert m.result.secondary["g"] == pytest.approx(5.0)  # 均值
    assert m.layout_meta == trusted[0].layout_meta  # 首个副本
    # 确定性派生 id
    assert out[0].obs_id == MedianAggregation().prepare(trusted, [exp])[0][0].obs_id


def test_median_aggregation_n2_conservative_minimize():
    exp = make_exp(direction="minimize")
    trusted = [_trusted(exp, "cand_A", 0.8, "o1"), _trusted(exp, "cand_A", 0.6, "o2")]
    out, _ = MedianAggregation().prepare(trusted, [exp])
    assert out[0].result.value == pytest.approx(0.8)  # minimize → 取 max 保守


def test_median_aggregation_n3_median():
    """use_huber=False 退回纯中位数（旧行为回归锚点）。"""
    exp = make_exp(direction="maximize")
    trusted = [
        _trusted(exp, "cand_A", 0.3, "o1"),
        _trusted(exp, "cand_A", 0.9, "o2"),
        _trusted(exp, "cand_A", 0.5, "o3"),
    ]
    out, _ = MedianAggregation(use_huber=False).prepare(trusted, [exp])
    assert out[0].result.value == pytest.approx(0.5)  # 中位数


def test_median_aggregation_n3_huber_outlier_closer_to_clean():
    """M9_PROTOCOL §1 L34-36（R1-3(c) 修复）：n=3 一个离群副本 →
    默认 Huber-median y 与纯 median 不同、更接近干净值，且保持稳健（远离均值）。"""
    exp = make_exp(direction="maximize")
    clean = 1.01  # 干净真值；两个好副本 0.98/1.00 带噪声，一个离群 2.00
    trusted = [
        _trusted(exp, "cand_A", 0.98, "o1"),
        _trusted(exp, "cand_A", 1.00, "o2"),
        _trusted(exp, "cand_A", 2.00, "o3"),
    ]
    huber_y = MedianAggregation().prepare(trusted, [exp])[0][0].result.value
    median_y = MedianAggregation(use_huber=False).prepare(trusted, [exp])[0][0].result.value
    mean_y = (0.98 + 1.00 + 2.00) / 3.0

    assert median_y == pytest.approx(1.00)
    assert huber_y != pytest.approx(median_y)              # Huber ≠ 纯中位数
    assert abs(huber_y - clean) < abs(median_y - clean)    # 更接近干净值
    # 稳健性：离群副本只获 δ/|r| 微小权重，μ̂ 应贴近中位数、远离被拉偏的均值
    assert abs(huber_y - median_y) < 0.05
    assert abs(huber_y - mean_y) > 0.25


def test_median_aggregation_huber_zero_mad_degenerates_to_median():
    """s=MAD·1.4826=0（副本全等）→ μ̂=m（协议 L36 s=0 分支）。"""
    exp = make_exp(direction="maximize")
    trusted = [
        _trusted(exp, "cand_A", 0.7, "o1"),
        _trusted(exp, "cand_A", 0.7, "o2"),
        _trusted(exp, "cand_A", 0.7, "o3"),
    ]
    out, _ = MedianAggregation().prepare(trusted, [exp])
    assert out[0].result.value == pytest.approx(0.7)


def test_median_aggregation_n2_conservative_unaffected_by_huber():
    """n=2 退化分支（协议 L44-47 保守选）不受 Huber 开关影响。"""
    exp = make_exp(direction="maximize")
    trusted = [_trusted(exp, "cand_A", 0.8, "o1"), _trusted(exp, "cand_A", 0.6, "o2")]
    on, _ = MedianAggregation(use_huber=True).prepare(trusted, [exp])
    off, _ = MedianAggregation(use_huber=False).prepare(trusted, [exp])
    assert on[0].result.value == off[0].result.value == pytest.approx(0.6)


def test_median_aggregation_passes_controls_through():
    exp = make_exp()
    ctrl = ObservationObject(
        exp_id=exp.exp_id, round_id=0, control_id="ctrl_1", is_control=True,
        result=MeasuredResult(metric="quality", value=0.4),
        layout_meta=LayoutMeta(well_id="A1", row=0, col=0),
    )
    ctrl.trust = TrustLevel.TRUSTED
    ctrl.routing = Routing.TO_RESPONSE_MODEL
    trusted = [_trusted(exp, "cand_A", 0.8, "o1"), _trusted(exp, "cand_A", 0.6, "o2"), ctrl]
    out, _ = MedianAggregation().prepare(trusted, [exp])
    assert ctrl in out  # 控制孔原样透传（未合并）
    assert len(out) == 2  # 1 合成 + 1 控制


def test_replicate_variance_alpha():
    exp = make_exp()
    # cand_A：两副本 0.6/0.8 → var=0.02, alpha=var/2=0.01（同组共享）
    # cand_B：单孔 → 组间中位方差兜底（仅一组 → median=0.02）
    trusted = [
        _trusted(exp, "cand_A", 0.6, "o1"),
        _trusted(exp, "cand_A", 0.8, "o2"),
        _trusted(exp, "cand_B", 0.5, "o3"),
    ]
    out, alpha = ReplicateVarianceAggregation().prepare(trusted, [exp])
    assert out == trusted  # 逐孔保留、原序
    assert alpha.shape == (3,)
    var_a = np.var([0.6, 0.8], ddof=1)  # 0.02
    assert alpha[0] == pytest.approx(var_a / 2)
    assert alpha[1] == pytest.approx(var_a / 2)  # 同组共享
    assert alpha[2] == pytest.approx(var_a)  # 无副本 → 组间中位方差兜底


def test_replicate_variance_no_replicates_fallback_zero():
    exp = make_exp()
    trusted = [_trusted(exp, "cand_A", 0.6, "o1"), _trusted(exp, "cand_B", 0.5, "o2")]
    out, alpha = ReplicateVarianceAggregation().prepare(trusted, [exp])
    # 全无副本 → 无组方差 → 兜底 0.0
    assert list(alpha) == [0.0, 0.0]


def test_aggregation_determinism():
    exp = make_exp()
    trusted = [
        _trusted(exp, "cand_A", 0.6, "o1"),
        _trusted(exp, "cand_A", 0.8, "o2"),
        _trusted(exp, "cand_B", 0.5, "o3"),
    ]
    a1 = ReplicateVarianceAggregation().prepare(trusted, [exp])[1]
    a2 = ReplicateVarianceAggregation().prepare(trusted, [exp])[1]
    assert np.array_equal(a1, a2)
    m1 = [(o.obs_id, o.result.value) for o in MedianAggregation().prepare(trusted, [exp])[0]]
    m2 = [(o.obs_id, o.result.value) for o in MedianAggregation().prepare(trusted, [exp])[0]]
    assert m1 == m2


# ================================================================ 软信任 0.3/0.6 边界（R1 P3）

@pytest.mark.parametrize("s,in_band,exp_routing,exp_w", [
    (0.299, False, Routing.TO_RESPONSE_MODEL, 1.00),  # <0.3 → TRUSTED，不入软信任带；w 夹到 1
    (0.300, True,  Routing.QUARANTINE,        1.00),  # 带下边界闭 → QUARANTINE；w=1 衔接满信任
    (0.450, True,  Routing.QUARANTINE,        0.50),  # 带内线性中点
    (0.599, True,  Routing.QUARANTINE,        0.05),  # 带内近上边界，已被 w_min 夹平
    (0.600, False, Routing.TO_FAILURE_MODEL,  0.05),  # 上边界开 → 硬隔离；w 触底 w_min（非 0）
])
def test_soft_trust_band_boundaries(s, in_band, exp_routing, exp_w):
    """0.3/0.6 边界（R1 P3）：SoftTrust 权重与 adjudicate 路由带 [0.3,0.6) 一致。

    路由带闭开：adjudicate 判 s<0.3→TRUSTED、s∈[0.3,0.6)→QUARANTINE（软信任唯一处理面）、
    s≥0.6→TO_FAILURE_MODEL（硬隔离，绝不复归）。权重：w(0.3)=1 衔接满信任、带内单调递减、
    下夹于 w_min=0.05；s≥0.6 不入软信任集（in_band=False）。"""
    agg = SoftTrustAggregation(suspect_high=0.6, quarantine_low=0.3, w_min=0.05)
    policy = TrustPolicy(suspect_high=0.6, quarantine_low=0.3)

    qc = QCReport(
        checks=[QCCheck(name="c", level="structural", passed=False, score=s)],
        suspicion=s,
    )
    _, routing, _ = adjudicate(qc, policy)
    assert routing == exp_routing
    assert (routing == Routing.QUARANTINE) == in_band

    w = agg._weight(s)
    assert 0.05 <= w <= 1.0
    assert w == pytest.approx(exp_w)
    # 与斜坡公式解析值一致（denom=0.3）
    assert w == pytest.approx(min(1.0, max(0.05, (0.6 - s) / (0.6 - 0.3))))


def test_soft_trust_upper_boundary_discontinuous():
    """上边界不连续（R1 P3）：w(0.6⁻)=w_min=0.05（非 0），而 s≥0.6 硬隔离完全剔除——
    刻意跳变，非平滑衔接（docstring 不吹"连续衔接"）。下边界 w(0.3)=1 才是真连续衔接。"""
    agg = SoftTrustAggregation(suspect_high=0.6, quarantine_low=0.3, w_min=0.05)
    # 上边界：w 触底于 w_min 且从 0.585 起一段被夹平——不趋于 0
    assert agg._weight(0.585) == pytest.approx(0.05)
    assert agg._weight(0.599) == pytest.approx(0.05)
    assert agg._weight(0.6) == pytest.approx(0.05)
    # 下边界连续：w(0.3)=1 == TRUSTED 满权重；<0.3 仍夹在 1
    assert agg._weight(0.3) == pytest.approx(1.0)
    assert agg._weight(0.299) == pytest.approx(1.0)


def test_soft_trust_inflates_alpha_only_for_quarantine(tmp_path):
    """软并入只作用 routing==QUARANTINE 带内观测：带内以 alpha_base/w 膨胀降权，
    带外（TRUSTED / TO_FAILURE_MODEL）绝不复归。此处直接驱动 prepare 验证边界并入语义。"""
    exp = make_exp()
    trusted = [
        _trusted(exp, "cand_A", 0.6, "o1"),
        _trusted(exp, "cand_A", 0.8, "o2"),  # 组方差 → alpha_base 非零
    ]
    # 一条带内 QUARANTINE 观测（s=0.45 → w=0.5），一条 TO_FAILURE_MODEL（不得并入）
    q_in = make_obs(exp, cand_id="cand_A", value=0.7, obs_id="q_in")
    q_in.trust, q_in.routing, q_in.trust_confidence = TrustLevel.SUSPECT, Routing.QUARANTINE, 0.45
    q_hard = make_obs(exp, cand_id="cand_A", value=0.9, obs_id="q_hard")
    q_hard.trust, q_hard.routing, q_hard.trust_confidence = (
        TrustLevel.SUSPECT, Routing.TO_FAILURE_MODEL, 0.8)

    out, alpha = SoftTrustAggregation(w_min=0.05).prepare(
        trusted, [exp], quarantine=[q_in, q_hard])
    # 恰好并入 1 条（带内那条），硬隔离那条被排除
    assert len(out) == len(trusted) + 1
    assert out[-1].obs_id == "q_in"
    # VNext batch-1: the ORIGINAL QUARANTINE object is admitted (no synthetic
    # TRUSTED copy) -- admission is carried by the explicit alpha vector, so
    # trust/routing stay untouched on the admitted observation.
    assert out[-1] is q_in
    assert out[-1].trust == TrustLevel.SUSPECT and out[-1].routing == Routing.QUARANTINE
    # 膨胀：并入点 alpha = alpha_base / w(0.45)=alpha_base/0.5
    var_a = np.var([0.6, 0.8], ddof=1)
    assert alpha[-1] == pytest.approx((var_a / 2) / 0.5)


# ================================================================ 依赖隔离

def test_dependency_isolation():
    import expos.qc.policy as mod

    src = open(mod.__file__, encoding="utf-8").read()
    for forbidden in ("adapters", "expos.agent", "planner", "truth"):
        assert forbidden not in src, f"policy.py 不得涉及 {forbidden!r}（依赖/真值红线）"
