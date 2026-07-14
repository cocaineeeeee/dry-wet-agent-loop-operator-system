"""基于 hypothesis 的内核属性测试（REFERENCE_MAP §13.12：往返恒等 + 跑一轮后再往返）。

覆盖 7 条属性：
1. ExperimentObject JSON 往返恒等；
2. ObservationObject JSON 往返恒等（qc/failure_attr/next_action 可选字段组合）；
3. DecisionRecord JSON 往返恒等；
4. DesignSpace × 合法 params：to_unit→from_unit→to_unit 双程收敛；
5a. to_unit 输出恒在 [0,1]；
5b. from_unit 对任意 [0,1] 向量产出合法 params（边界裁剪修复后转正）；
6. adjudicate 纯函数性质（合法配对 / confidence∈[0,1] / hard→FAILED / 单调性）；
7. derive_seed 确定性 + 低碰撞。

曾发现真实 bug（log 边界浮点微溢出），已在 design/space.py 修复（边界裁剪），本属性转正。
"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

if importlib.util.find_spec("hypothesis") is None:  # dev extra 未装时整模块优雅跳过（照 test_ui_smoke find_spec 范本）
    pytest.skip("hypothesis 未安装（pip install -e '.[dev]'）", allow_module_level=True)

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from expos.design.space import DesignError, dim, from_unit, to_unit, validate_params
from expos.kernel.lifecycle import TrustPolicy, adjudicate
from expos.kernel.objects import (
    ActionType,
    Actor,
    Budget,
    Candidate,
    Constraint,
    Control,
    DecisionKind,
    DecisionRecord,
    DesignProvenance,
    DesignSpace,
    ExecutionReq,
    ExperimentObject,
    ExpStatus,
    FailureAttribution,
    FailureHypothesis,
    InstrumentMeta,
    LayoutAssignment,
    LayoutMeta,
    MaterialMeta,
    MeasuredResult,
    Objective,
    ObservationObject,
    QCCheck,
    QCReport,
    RawDataRef,
    RecommendedAction,
    ReplicatePlan,
    Routing,
    TrustLevel,
    VariableDef,
    WellAssignment,
)
from expos.loop import derive_seed

# 慢生成 / 复杂对象：统一放宽 deadline 与 health check（控 max_examples 保时长）。
SETTINGS = settings(
    max_examples=75,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

FINITE = dict(allow_nan=False, allow_infinity=False)


# ============================================================ 基础 strategies

#: JSON 可序列化标量（保证 dump→validate 逐值恒等；排除 NaN/Inf）。
json_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-10**6, max_value=10**6),
    st.floats(**FINITE),
    st.text(max_size=8),
)
json_dict = st.dictionaries(st.text(min_size=1, max_size=6), json_scalars, max_size=4)
float_dict = st.dictionaries(st.text(min_size=1, max_size=6), st.floats(**FINITE), max_size=4)


@st.composite
def variable_defs(draw, name: str) -> VariableDef:
    """1 个合法 VariableDef：linear / log(low>0) / categorical(distinct choices)。"""
    kind = draw(st.sampled_from(["linear", "log", "categorical"]))
    if kind == "categorical":
        k = draw(st.integers(min_value=1, max_value=5))
        choices = [f"c{j}" for j in range(k)]  # distinct → index 无歧义
        return VariableDef(name=name, kind="categorical", choices=choices)
    if kind == "log":
        low = draw(st.floats(min_value=1e-3, max_value=1e2, **FINITE))
    else:
        low = draw(st.floats(min_value=-1e3, max_value=1e3, **FINITE))
    width = draw(st.floats(min_value=1e-2, max_value=1e3, **FINITE))
    return VariableDef(name=name, low=low, high=low + width, transform=kind)


@st.composite
def design_spaces(draw) -> DesignSpace:
    n = draw(st.integers(min_value=1, max_value=6))
    variables = [draw(variable_defs(f"v{i}")) for i in range(n)]
    return DesignSpace(name=draw(st.text(max_size=6)), variables=variables)


def _legal_value(var: VariableDef):
    if var.kind == "categorical":
        return st.sampled_from(var.choices)
    return st.floats(min_value=var.low, max_value=var.high, **FINITE)


@st.composite
def legal_params(draw, space: DesignSpace) -> dict:
    return {v.name: draw(_legal_value(v)) for v in space.variables}


@st.composite
def space_and_params(draw):
    space = draw(design_spaces())
    return space, draw(legal_params(space))


# ============================================================ 属性 1-3：JSON 往返恒等

@st.composite
def objectives(draw) -> Objective:
    return Objective(
        name=draw(st.text(min_size=1, max_size=8)),
        metric=draw(st.text(min_size=1, max_size=8)),
        direction=draw(st.sampled_from(["maximize", "minimize"])),
        description=draw(st.text(max_size=10)),
    )


@st.composite
def experiments(draw) -> ExperimentObject:
    space = draw(design_spaces())
    n_cand = draw(st.integers(min_value=0, max_value=3))
    candidates = [
        Candidate(
            params=draw(legal_params(space)),
            source=draw(st.sampled_from(["manual", "sobol", "bo"])),
            rationale=draw(st.text(max_size=8)),
            placement_hint=draw(st.none() | st.sampled_from(["center_only", "edge_center_pair"])),
        )
        for _ in range(n_cand)
    ]
    n_ctrl = draw(st.integers(min_value=0, max_value=2))
    controls = [
        Control(
            kind=draw(st.sampled_from(["sentinel", "negative", "positive"])),
            params=draw(legal_params(space)),
            expected_band=draw(
                st.none()
                | st.tuples(st.floats(**FINITE), st.floats(**FINITE))
            ),
        )
        for _ in range(n_ctrl)
    ]

    # layout：None 或引用已生成 cand/control 的合法 wells（exactly-one 不变量）。
    layout = None
    ids = [("cand", c.cand_id) for c in candidates] + [("ctrl", c.control_id) for c in controls]
    if ids and draw(st.booleans()):
        n_wells = draw(st.integers(min_value=1, max_value=min(3, len(ids))))
        wells = []
        for w in range(n_wells):
            tag, ident = ids[w % len(ids)]
            wells.append(
                WellAssignment(
                    well_id=f"W{w}",
                    row=draw(st.integers(min_value=0, max_value=7)),
                    col=draw(st.integers(min_value=0, max_value=11)),
                    cand_id=ident if tag == "cand" else None,
                    control_id=ident if tag == "ctrl" else None,
                    is_edge=draw(st.booleans()),
                    block_id=draw(st.text(max_size=4)),
                )
            )
        layout = LayoutAssignment(
            rows=draw(st.integers(min_value=1, max_value=16)),
            cols=draw(st.integers(min_value=1, max_value=24)),
            seed=draw(st.integers(min_value=0, max_value=10**6)),
            wells=wells,
        )

    active = draw(st.lists(st.sampled_from([v.name for v in space.variables]), unique=True, max_size=6))
    restrictions = []
    if draw(st.booleans()):
        vname = draw(st.sampled_from([v.name for v in space.variables]))
        restrictions = [Constraint(name="r", kind="range", params={"var": vname, "min": -1e9, "max": 1e9})]

    return ExperimentObject(
        round_id=draw(st.integers(min_value=0, max_value=64)),
        domain=draw(st.text(min_size=1, max_size=8)),
        objective=draw(objectives()),
        design_space=space,
        active_vars=active,
        restrictions=restrictions,
        candidates=candidates,
        controls=controls,
        replicate_plan=ReplicatePlan(
            n_replicates=draw(st.integers(min_value=1, max_value=6)),
            strategy=draw(st.sampled_from(["across_blocks", "within_block"])),
        ),
        layout=layout,
        budget=Budget(
            wells_total=draw(st.integers(min_value=1, max_value=384)),
            wells_used=draw(st.integers(min_value=0, max_value=384)),
            rounds_total=draw(st.integers(min_value=1, max_value=16)),
            rounds_used=draw(st.integers(min_value=0, max_value=16)),
        ),
        execution_req=ExecutionReq(
            adapter=draw(st.text(min_size=1, max_size=8)),
            params=draw(json_dict),
            n_solution_batches=draw(st.integers(min_value=1, max_value=4)),
        ),
        provenance=DesignProvenance(
            generator=draw(st.text(min_size=1, max_size=8)),
            acquisition=draw(st.none() | st.text(max_size=6)),
            based_on_obs=draw(st.integers(min_value=0, max_value=100)),
            actions_consumed=draw(st.lists(st.text(max_size=6), max_size=3)),
            rationale=draw(st.text(max_size=10)),
        ),
        status=draw(st.sampled_from(list(ExpStatus))),
    )


@st.composite
def qc_reports(draw) -> QCReport:
    checks = draw(
        st.lists(
            st.builds(
                QCCheck,
                name=st.text(min_size=1, max_size=6),
                level=st.sampled_from(["hard", "reference", "structural"]),
                passed=st.booleans(),
                score=st.floats(min_value=0.0, max_value=1.0),
                evidence=json_dict,
            ),
            max_size=5,
        )
    )
    return QCReport(
        checks=checks,
        flags=draw(st.lists(st.text(max_size=6), max_size=3)),
        suspicion=draw(st.floats(min_value=0.0, max_value=1.0)),
    )


@st.composite
def failure_attributions(draw) -> FailureAttribution:
    hyps = draw(
        st.lists(
            st.builds(
                FailureHypothesis,
                cause=st.text(min_size=1, max_size=8),
                score=st.floats(min_value=0.0, max_value=1.0),
                evidence=json_dict,
                remedy=st.sampled_from(list(ActionType)),
            ),
            max_size=3,
        )
    )
    return FailureAttribution(
        hypotheses=hyps,
        top_cause=draw(st.none() | st.text(max_size=8)),
        confidence=draw(st.floats(min_value=0.0, max_value=1.0)),
    )


@st.composite
def observations(draw) -> ObservationObject:
    # exactly-one(cand_id, control_id) 且 is_control ↔ control_id 不变量。
    is_control = draw(st.booleans())
    cand_id = None if is_control else f"cand_{draw(st.integers(0, 999))}"
    control_id = f"ctrl_{draw(st.integers(0, 999))}" if is_control else None
    result = MeasuredResult(
        metric=draw(st.text(min_size=1, max_size=8)),
        value=draw(st.none() | st.floats(**FINITE)),
        uncertainty=draw(st.none() | st.floats(min_value=0.0, max_value=1e3, **FINITE)),
        secondary=draw(float_dict),
        unit=draw(st.text(max_size=4)),
    )
    return ObservationObject(
        exp_id=f"exp_{draw(st.integers(0, 999))}",
        round_id=draw(st.integers(min_value=0, max_value=64)),
        cand_id=cand_id,
        control_id=control_id,
        is_control=is_control,
        result=result,
        raw_ref=RawDataRef(
            uri=draw(st.text(max_size=10)),
            kind=draw(st.sampled_from(["sim", "real"])),
            sha256=draw(st.none() | st.text(max_size=8)),
        ),
        layout_meta=LayoutMeta(
            well_id=draw(st.text(min_size=1, max_size=4)),
            row=draw(st.integers(min_value=0, max_value=15)),
            col=draw(st.integers(min_value=0, max_value=23)),
            is_edge=draw(st.booleans()),
            block_id=draw(st.text(max_size=4)),
        ),
        material_meta=MaterialMeta(
            solution_batch=draw(st.text(max_size=4)),
            additive_lot=draw(st.text(max_size=4)),
            prep_order=draw(st.integers(min_value=0, max_value=100)),
        ),
        instrument_meta=InstrumentMeta(
            instrument_id=draw(st.text(min_size=1, max_size=6)),
            exposure=draw(st.floats(min_value=0.0, max_value=10.0, **FINITE)),
            illumination=draw(st.floats(min_value=0.0, max_value=10.0, **FINITE)),
            capture_index=draw(st.integers(min_value=0, max_value=100)),
        ),
        qc=draw(st.none() | qc_reports()),
        trust=draw(st.sampled_from(list(TrustLevel))),
        trust_confidence=draw(st.floats(min_value=0.0, max_value=1.0)),
        failure_attr=draw(st.none() | failure_attributions()),
        routing=draw(st.none() | st.sampled_from(list(Routing))),
        next_action=draw(
            st.none()
            | st.builds(
                RecommendedAction,
                action=st.sampled_from(list(ActionType)),
                params=json_dict,
                reason=st.text(max_size=8),
            )
        ),
    )


@st.composite
def decision_records(draw) -> DecisionRecord:
    return DecisionRecord(
        round_id=draw(st.integers(min_value=0, max_value=64)),
        actor=draw(st.sampled_from(list(Actor))),
        kind=draw(st.sampled_from(list(DecisionKind))),
        refs=draw(st.lists(st.text(max_size=8), max_size=3)),
        content=draw(json_dict),
        accepted=draw(st.none() | st.booleans()),
        validator=draw(st.none() | st.text(max_size=6)),
    )


@SETTINGS
@given(exp=experiments())
def test_experiment_json_roundtrip(exp):
    """属性 1：ExperimentObject 任意合法实例 dump→validate 逐字段恒等。"""
    assert ExperimentObject.model_validate_json(exp.model_dump_json()) == exp


@SETTINGS
@given(obs=observations())
def test_observation_json_roundtrip(obs):
    """属性 2：ObservationObject（含可选字段组合）JSON 往返恒等。"""
    assert ObservationObject.model_validate_json(obs.model_dump_json()) == obs


@SETTINGS
@given(rec=decision_records())
def test_decision_record_json_roundtrip(rec):
    """属性 3：DecisionRecord JSON 往返恒等。"""
    assert DecisionRecord.model_validate_json(rec.model_dump_json()) == rec


# ============================================================ 属性 4-5：设计空间

@SETTINGS
@given(
    space=design_spaces(),
    coords=st.lists(st.floats(min_value=0.01, max_value=0.99), min_size=6, max_size=6),
)
def test_unit_double_roundtrip_converges(space, coords):
    """属性 4：to_unit→from_unit→to_unit 双程收敛（连续 <1e-9，categorical 恒等）。

    由安全内点（unit 坐标 ∈[0.01,0.99]）反解出 params 再跑双程——刻意避开 log 变量
    的边界：那里 from_unit 不 clamp 输出会溢出范围，是 test_from_unit_produces_valid_params_XFAIL
    记录的已知 bug，而非收敛性本身失效。
    """
    u_safe = np.array(coords[: dim(space)])
    params = from_unit(space, u_safe)          # 内点合法 params
    u0 = to_unit(space, params)
    p1 = from_unit(space, u0)
    u1 = to_unit(space, p1)
    for i, var in enumerate(space.variables):
        if var.kind == "categorical":
            assert u1[i] == u0[i]              # 索引映射精确恒等
            assert p1[var.name] == params[var.name]
        else:
            assert abs(u1[i] - u0[i]) < 1e-9


@SETTINGS
@given(data=space_and_params())
def test_to_unit_output_in_unit_cube(data):
    """属性 5a：to_unit 对任意合法 params 输出恒落在 [0,1]。"""
    space, params = data
    u = to_unit(space, params)
    assert np.all(u >= 0.0) and np.all(u <= 1.0)


@SETTINGS
@given(space=design_spaces(), unit=st.lists(st.floats(min_value=0.0, max_value=1.0), max_size=6))
def test_from_unit_produces_valid_params_XFAIL(space, unit):
    """属性 5b（已知反例，xfail strict）：from_unit 对任意 [0,1] 向量应产出合法 params。

    真实 bug —— from_unit 反变换后不 clamp 到 [var.low, var.high]。log 变量在边界处
    exp(log(low)+u·(log(high)-log(low))) 因浮点误差微溢出范围（u=1 高于 high，u=0 低于 low），
    随后 to_unit 的严格范围检查 `var.low <= x <= var.high` 拒绝之 → validate_params 抛 DesignError。
    副作用：连"恰好落在 log 变量边界上的合法 params"都无法往返（见属性 4 为何避开边界）。

    两个已核实的最小反例：
      · u=[1.0] on log(low=1e-3, high=1e-2)：from_unit=0.010000000000000004 > high=0.01（溢出 +4e-18）
      · u=[0.0] on log(low=54.953125, high=55.953125)：from_unit=54.953124999999986 < low（溢出 -1.4e-14）
        —— 即"恰好落在 log 变量下界上的合法 params"也无法往返（属性 4 正因此避开边界内点）。
    修复建议：_from_unit_one 连续分支对结果 `min(high, max(low, x))` 裁剪后再返回。
    """
    # 一般性质抽样（多数情形通过）
    u = np.array(unit[: dim(space)] + [0.5] * (dim(space) - len(unit)))
    validate_params(space, from_unit(space, u))
    # 确定性触发已核实反例，保证 strict xfail 稳定为 XFAIL（不依赖随机抽到 log 变量的边界坐标）
    over = DesignSpace(name="x", variables=[VariableDef(name="v", low=1e-3, high=1e-2, transform="log")])
    validate_params(over, from_unit(over, np.array([1.0])))          # 上界溢出
    under = DesignSpace(name="x", variables=[VariableDef(name="v", low=54.953125, high=55.953125, transform="log")])
    validate_params(under, from_unit(under, np.array([0.0])))         # 下界溢出


# ============================================================ 属性 6：adjudicate 纯函数

#: adjudicate 可产生的全部合法 (trust, routing) 配对。
LEGAL_VERDICTS = {
    (TrustLevel.FAILED, Routing.TO_FAILURE_MODEL),
    (TrustLevel.SUSPECT, Routing.TO_FAILURE_MODEL),
    (TrustLevel.SUSPECT, Routing.QUARANTINE),
    (TrustLevel.TRUSTED, Routing.TO_RESPONSE_MODEL),
}
#: trust 优劣序（越大越好）；单调性：suspicion 更大 → trust 不会更好。
TRUST_RANK = {TrustLevel.FAILED: 0, TrustLevel.SUSPECT: 1, TrustLevel.TRUSTED: 2}


@SETTINGS
@given(qc=qc_reports())
def test_adjudicate_verdict_invariants(qc):
    """属性 6a：输出配对恒在合法集内、confidence∈[0,1]、hard 失败必 FAILED。
    R2 Q-4 新契约：空 checks 的报告若原本会裁 TRUSTED（无 hard 失败、suspicion
    低于隔离带）→ 响亮 LifecycleError（"无证据即无信任依据"），且仅此情形允许抛。"""
    from expos.kernel.lifecycle import LifecycleError

    policy = TrustPolicy()
    try:
        trust, routing, conf = adjudicate(qc, policy)
    except LifecycleError:
        assert not qc.checks, "非空 checks 不该触发空报告守卫"
        assert qc.suspicion < policy.quarantine_low, "会判 SUSPECT/FAILED 的路径不该触发该守卫"
        return
    assert (trust, routing) in LEGAL_VERDICTS
    assert 0.0 <= conf <= 1.0
    if any(c.level == "hard" and not c.passed for c in qc.checks):
        assert trust == TrustLevel.FAILED and routing == Routing.TO_FAILURE_MODEL
    if not qc.checks:
        assert trust != TrustLevel.TRUSTED  # 守卫的另一半：放行的空报告绝不是 TRUSTED


def _report_with_evidence(suspicion: float) -> QCReport:
    """带一条通过的结构检查的报告——满足 R2 Q-4"空 checks 不得裁 TRUSTED"守卫，
    使单调性属性在新契约下仍可对全 suspicion 区间断言。"""
    return QCReport(
        checks=[QCCheck(name="c", level="structural", passed=True, score=0.0, evidence={})],
        suspicion=suspicion,
    )


@SETTINGS
@given(susp=st.tuples(st.floats(min_value=0.0, max_value=1.0), st.floats(min_value=0.0, max_value=1.0)))
def test_adjudicate_monotone_in_suspicion(susp):
    """属性 6b：无 hard 失败时，suspicion 更大 trust 不会更好（单调不增）。"""
    s_lo, s_hi = sorted(susp)
    lo = adjudicate(_report_with_evidence(s_lo))[0]
    hi = adjudicate(_report_with_evidence(s_hi))[0]
    assert TRUST_RANK[hi] <= TRUST_RANK[lo]


# ============================================================ 属性 7：derive_seed

@SETTINGS
@given(
    seed=st.integers(min_value=0, max_value=2**31 - 1),
    parts=st.lists(st.one_of(st.integers(), st.text(max_size=8)), min_size=1, max_size=4),
)
def test_derive_seed_deterministic(seed, parts):
    """属性 7a：确定性 —— 同 (seed, parts) 恒得同值，且落在 32-bit 无符号区间。"""
    a = derive_seed(seed, *parts)
    b = derive_seed(seed, *parts)
    assert a == b
    assert isinstance(a, int) and 0 <= a < 2**32


@SETTINGS
@given(
    seed=st.integers(min_value=0, max_value=2**31 - 1),
    parts=st.lists(st.integers(min_value=-10**9, max_value=10**9), min_size=200, max_size=200, unique=True),
)
def test_derive_seed_low_collision(seed, parts):
    """属性 7b：200 组不同 parts 派生的子种子无重复（近零碰撞）。"""
    seeds = [derive_seed(seed, p) for p in parts]
    assert len(set(seeds)) == 200
