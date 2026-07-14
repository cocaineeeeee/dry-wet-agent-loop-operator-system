"""M6 失败模型验收测试（任务书规格 + docs/ARCHITECTURE.md §7.3 / REFERENCE_MAP §11.5）。

覆盖：正例/分母计数（混合 trust）；收缩行为（空桶=全局率、大样本桶→经验率）；改判后
rebuild 反映新裁决；乐观界 ≤ 均值且非负；risk_map 键与 layout 完全一致、值∈[0,1]；
边缘桶伪影多时边缘孔风险 > 中心孔（构造性场景）；确定性；依赖隔离红线。
"""

from pathlib import Path

import pytest

from expos.qc.failure_model import (
    Bucket,
    FailureModel,
    FailureModelError,
    round_band_of,
)
from expos.kernel.objects import (
    LayoutAssignment,
    LayoutMeta,
    MaterialMeta,
    MeasuredResult,
    ObservationObject,
    TrustLevel,
    WellAssignment,
)


# ---------------------------------------------------------------- 构造工具

def _obs(
    trust: TrustLevel,
    *,
    is_edge: bool = False,
    block_id: str = "Q0",
    solution_batch: str = "B0",
    round_id: int = 0,
    well_id: str = "C3",
    value: float = 1.0,
) -> ObservationObject:
    """构造一个已带 trust 裁决的观测（cand 侧）。"""
    return ObservationObject(
        exp_id="exp_x",
        round_id=round_id,
        cand_id="cand_x",
        result=MeasuredResult(metric="m", value=value),
        layout_meta=LayoutMeta(
            well_id=well_id, row=0, col=0, is_edge=is_edge, block_id=block_id
        ),
        material_meta=MaterialMeta(solution_batch=solution_batch),
        trust=trust,
    )


def _layout(specs: list[tuple[str, int, int, bool, str]]) -> LayoutAssignment:
    """specs: (well_id, row, col, is_edge, block_id) → LayoutAssignment。"""
    wells = [
        WellAssignment(
            well_id=wid, row=r, col=c, cand_id="cand_x", is_edge=e, block_id=b
        )
        for (wid, r, c, e, b) in specs
    ]
    return LayoutAssignment(rows=4, cols=4, seed=0, wells=wells)


# ---------------------------------------------------------------- round_band

def test_round_band_two_rounds_per_segment():
    assert round_band_of(0) == "r0-1"
    assert round_band_of(1) == "r0-1"
    assert round_band_of(2) == "r2-3"
    assert round_band_of(3) == "r2-3"
    assert round_band_of(4) == "r4-5"


# ---------------------------------------------------------------- 计数正确性

def test_counts_positive_and_denominator_mixed_trust():
    """正例=SUSPECT/FAILED；TRUSTED 只作分母；PENDING 跳过。"""
    obs = [
        _obs(TrustLevel.SUSPECT),
        _obs(TrustLevel.FAILED),
        _obs(TrustLevel.TRUSTED),
        _obs(TrustLevel.TRUSTED),
        _obs(TrustLevel.PENDING),  # 应被跳过，不进分子分母
    ]
    fm = FailureModel().rebuild(obs)
    s = fm.summary()
    # 全部落同一桶：k=2（SUSPECT+FAILED），n=4（含 2 TRUSTED，不含 PENDING）
    assert s["k_total"] == 2
    assert s["n_total"] == 4
    assert s["p_global"] == pytest.approx(2 / 4)
    assert s["n_buckets"] == 1
    bucket = s["buckets"][0]
    assert (bucket["k"], bucket["n"]) == (2, 4)


def test_rebuild_returns_self_fluent():
    fm = FailureModel()
    assert fm.rebuild([_obs(TrustLevel.TRUSTED)]) is fm


# ---------------------------------------------------------------- 收缩行为

def test_shrinkage_empty_bucket_equals_global_rate():
    """空桶后验 = 全局 p̄（收缩兜底）。"""
    # 全局率 = 3/6 = 0.5，但查一个从未观测过的桶
    obs = [_obs(TrustLevel.SUSPECT) for _ in range(3)] + [
        _obs(TrustLevel.TRUSTED) for _ in range(3)
    ]
    fm = FailureModel().rebuild(obs)
    p_empty = fm.p_artifact(
        is_edge=True, block_id="Q9", solution_batch="ZZZ", round_id=8
    )
    assert p_empty == pytest.approx(fm.summary()["p_global"])
    assert p_empty == pytest.approx(0.5)


def test_shrinkage_no_observations_falls_back_to_default():
    """无已裁决观测 → p̄=0.05 兜底。"""
    fm = FailureModel().rebuild([])
    assert fm.summary()["p_global"] == pytest.approx(0.05)
    assert fm.p_artifact(False, "Q0", "B0", 0) == pytest.approx(0.05)
    # 全 PENDING 同样兜底
    fm2 = FailureModel().rebuild([_obs(TrustLevel.PENDING) for _ in range(5)])
    assert fm2.summary()["p_global"] == pytest.approx(0.05)


def test_shrinkage_large_bucket_approaches_empirical_rate():
    """大样本桶：后验被数据主导，趋向经验率（先验 m=5 被稀释）。"""
    n_big = 500
    k_big = 400  # 经验率 0.8
    obs = [
        _obs(TrustLevel.FAILED, block_id="Q1", solution_batch="B1")
        for _ in range(k_big)
    ] + [
        _obs(TrustLevel.TRUSTED, block_id="Q1", solution_batch="B1")
        for _ in range(n_big - k_big)
    ]
    fm = FailureModel().rebuild(obs)
    p = fm.p_artifact(False, "Q1", "B1", 0)
    empirical = k_big / n_big
    # 收缩后应非常接近经验率（|误差| < 0.02），且严格落在先验均值与经验率之间
    assert abs(p - empirical) < 0.02
    p_global = fm.summary()["p_global"]
    assert min(p_global, empirical) <= p <= max(p_global, empirical)


def test_small_bucket_pulled_toward_prior():
    """小样本桶被先验拉向全局率（收缩强于经验率）。"""
    # 全局率低（大量 TRUSTED），小桶里 1/1 全失败
    obs = [_obs(TrustLevel.TRUSTED, block_id="Q0") for _ in range(20)]
    obs.append(_obs(TrustLevel.FAILED, block_id="Q7", solution_batch="rareB"))
    fm = FailureModel().rebuild(obs)
    p = fm.p_artifact(False, "Q7", "rareB", 0)
    # 经验率=1.0，但 n=1 被 m=5 先验强烈收缩，远低于 1
    assert p < 0.5
    assert p > fm.summary()["p_global"]  # 仍高于全局（数据把它往上抬）


# ---------------------------------------------------------------- 改判后重建

def test_reclassification_reflected_after_rebuild():
    """改判物化到 obs.trust 后重建自动反映新裁决。"""
    obs = [
        _obs(TrustLevel.TRUSTED, block_id="Q2"),
        _obs(TrustLevel.TRUSTED, block_id="Q2"),
    ]
    fm = FailureModel().rebuild(obs)
    p_before = fm.p_artifact(False, "Q2", "B0", 0)
    # 把一条 TRUSTED 改判为 SUSPECT（模拟 reclassify 物化）
    obs[0].trust = TrustLevel.SUSPECT
    fm.rebuild(obs)
    p_after = fm.p_artifact(False, "Q2", "B0", 0)
    assert p_after > p_before  # 正例增加 → 桶伪影率上升
    s = fm.summary()
    assert s["k_total"] == 1 and s["n_total"] == 2


# ---------------------------------------------------------------- 乐观界

def test_optimistic_bound_leq_mean_and_nonneg():
    obs = [
        _obs(TrustLevel.SUSPECT, block_id="Q3", solution_batch="B2")
        for _ in range(4)
    ] + [
        _obs(TrustLevel.TRUSTED, block_id="Q3", solution_batch="B2")
        for _ in range(6)
    ]
    fm = FailureModel().rebuild(obs)
    for (e, b, sb, rid) in [
        (False, "Q3", "B2", 0),  # 有数据的桶
        (True, "Qempty", "none", 4),  # 空桶
    ]:
        mean = fm.p_artifact(e, b, sb, rid)
        opt = fm.p_artifact_optimistic(e, b, sb, rid)
        assert opt <= mean
        assert opt >= 0.0


def test_optimistic_bound_sparse_wider_than_dense():
    """稀疏桶 std 更宽 → 乐观下界压得更低（RAHBO 折扣弱化）。"""
    # 稠密桶
    dense = [
        _obs(TrustLevel.SUSPECT, block_id="Qd", solution_batch="Bd")
        for _ in range(50)
    ] + [
        _obs(TrustLevel.TRUSTED, block_id="Qd", solution_batch="Bd")
        for _ in range(50)
    ]
    # 稀疏桶（同经验率 0.5，但 n=2）
    sparse = [
        _obs(TrustLevel.SUSPECT, block_id="Qs", solution_batch="Bs"),
        _obs(TrustLevel.TRUSTED, block_id="Qs", solution_batch="Bs"),
    ]
    fm = FailureModel().rebuild(dense + sparse)
    gap_dense = fm.p_artifact(False, "Qd", "Bd", 0) - fm.p_artifact_optimistic(
        False, "Qd", "Bd", 0
    )
    gap_sparse = fm.p_artifact(False, "Qs", "Bs", 0) - fm.p_artifact_optimistic(
        False, "Qs", "Bs", 0
    )
    assert gap_sparse > gap_dense


# ---------------------------------------------------------------- risk_map

def test_risk_map_keys_match_layout_exactly_and_in_range():
    layout = _layout(
        [
            ("A1", 0, 0, True, "Q0"),
            ("A4", 0, 3, True, "Q1"),
            ("B2", 1, 1, False, "Q0"),
            ("C3", 2, 2, False, "Q3"),
        ]
    )
    fm = FailureModel().rebuild([_obs(TrustLevel.SUSPECT) for _ in range(3)])
    rm = fm.risk_map(layout, round_id=0)
    assert set(rm) == {"A1", "A4", "B2", "C3"}  # 严格等于 well_id 集合
    for v in rm.values():
        assert 0.0 <= v <= 1.0


def test_risk_map_edge_higher_than_center_when_edge_artifacts():
    """构造性：边缘桶伪影多 → risk_map 边缘孔风险 > 中心孔。"""
    band_rid = 0
    obs = []
    # 每个 block 同时给边缘/中心样本；边缘高伪影（4/5），中心低伪影（0/5）
    for block in ("Q0", "Q1"):
        for _ in range(4):
            obs.append(_obs(TrustLevel.FAILED, is_edge=True, block_id=block))
        obs.append(_obs(TrustLevel.TRUSTED, is_edge=True, block_id=block))
        for _ in range(5):
            obs.append(_obs(TrustLevel.TRUSTED, is_edge=False, block_id=block))
    fm = FailureModel().rebuild(obs)
    layout = _layout(
        [
            ("A1", 0, 0, True, "Q0"),   # 边缘 Q0
            ("B2", 1, 1, False, "Q0"),  # 中心 Q0
            ("A4", 0, 3, True, "Q1"),   # 边缘 Q1
            ("C3", 2, 2, False, "Q1"),  # 中心 Q1
        ]
    )
    rm = fm.risk_map(layout, round_id=band_rid)
    assert rm["A1"] > rm["B2"]  # Q0 边缘 > Q0 中心
    assert rm["A4"] > rm["C3"]  # Q1 边缘 > Q1 中心


def test_risk_map_batch_hint_uses_specific_bucket():
    """给 solution_batch_hint → 用精确桶；不给 → 对批次维取边际（并池）。"""
    obs = []
    # 批 Bhi 边缘全失败，批 Blo 边缘全可信；同 block/is_edge
    for _ in range(5):
        obs.append(_obs(TrustLevel.FAILED, is_edge=True, block_id="Q0", solution_batch="Bhi"))
    for _ in range(5):
        obs.append(_obs(TrustLevel.TRUSTED, is_edge=True, block_id="Q0", solution_batch="Blo"))
    fm = FailureModel().rebuild(obs)
    layout = _layout([("A1", 0, 0, True, "Q0")])
    rm_hi = fm.risk_map(layout, round_id=0, solution_batch_hint="Bhi")
    rm_lo = fm.risk_map(layout, round_id=0, solution_batch_hint="Blo")
    rm_marg = fm.risk_map(layout, round_id=0)  # 边际：并池 5 失败 + 5 可信
    assert rm_hi["A1"] > rm_lo["A1"]
    # 边际落在两批之间
    assert rm_lo["A1"] < rm_marg["A1"] < rm_hi["A1"]


def test_round_prefixed_batch_label_hits_same_bucket_as_deround():
    """FM3 ②: the execution-face label ``R{round}-B{k}`` and the round-invariant ``B{k}``
    address the SAME bucket (both stored and queried through ``_batch_key``). A planning
    query for the batch about to be cast in a *later* round of the same band therefore
    hits the batch's exact history instead of being flushed to the batch-marginal."""
    obs = (
        [_obs(TrustLevel.FAILED, is_edge=True, block_id="Q0",
              solution_batch="R0-B0", round_id=0) for _ in range(4)]
        + [_obs(TrustLevel.TRUSTED, is_edge=True, block_id="Q0",
                solution_batch="R0-B0", round_id=0)]
        + [_obs(TrustLevel.TRUSTED, is_edge=False, block_id="Q0",
                solution_batch="R0-B0", round_id=0) for _ in range(5)]
    )
    fm = FailureModel().rebuild(obs)  # p̄ = 4/10 = 0.4; edge bucket empirical rate 4/5
    # round 1 is still band r0-1: derounded "R1-B0" -> "B0" hits the exact stored bucket.
    p_new_label = fm.p_artifact(True, "Q0", "R1-B0", 1)
    p_deround = fm.p_artifact(True, "Q0", "B0", 1)
    p_exact = fm.p_artifact(True, "Q0", "R0-B0", 0)
    assert p_new_label == pytest.approx(p_deround) == pytest.approx(p_exact)
    # discriminative: != global p̄ (pre-fix the round-prefixed label missed every bucket
    # and the edge failure history was averaged away into the batch-marginal).
    assert p_new_label > fm.summary()["p_global"] + 0.1
    # optimistic bound goes through the same key normalization
    assert fm.p_artifact_optimistic(True, "Q0", "R1-B0", 1) == pytest.approx(
        fm.p_artifact_optimistic(True, "Q0", "B0", 1)
    )
    # every level empty (no is_edge/block history at all) -> global p̄
    assert fm.p_artifact(False, "Q9", "R1-B0", 1) == pytest.approx(
        fm.summary()["p_global"]
    )


def test_batch_signal_survives_across_round_bands():
    """FM3 core fix: a batch's learned artifact rate reaches queries in a *different* round
    band (where the exact round-band bucket is empty) via the round-marginal fallback —
    the contaminated batch stays above the clean batch instead of collapsing to one
    batch-agnostic value. Pre-fix (round-prefixed keys + batch-marginal fallback) this
    difference was structurally marginalized to zero."""
    obs = []
    # Round 0 (band r0-1): batch B1 heavily contaminated, batch B0 clean; same edge/block.
    for _ in range(8):
        obs.append(_obs(TrustLevel.FAILED, is_edge=False, block_id="Q0",
                        solution_batch="R0-B1", round_id=0))
    for _ in range(8):
        obs.append(_obs(TrustLevel.TRUSTED, is_edge=False, block_id="Q0",
                        solution_batch="R0-B0", round_id=0))
    fm = FailureModel().rebuild(obs)
    # Query round 2 (band r2-3): NO round-2/3 history exists, so the exact bucket is empty
    # and the round-marginal fallback must supply each batch's cross-band history.
    p_b1 = fm.p_artifact(False, "Q0", "B1", 2)
    p_b0 = fm.p_artifact(False, "Q0", "B0", 2)
    assert p_b1 > p_b0, "contaminated batch signal lost across round bands (FM3 regression)"
    # It is genuinely a fallback (exact round-2 bucket empty), yet still batch-discriminative.
    assert p_b1 > fm.summary()["p_global"]
    assert p_b0 < fm.summary()["p_global"]
    # A batch with no history anywhere in this (is_edge, block) -> global p̄ (batch-marginal
    # is also empty because only B0/B1 were ever seen and both are queried above).
    assert fm.p_artifact(False, "Qnever", "B0", 2) == pytest.approx(
        fm.summary()["p_global"]
    )


def test_risk_map_unknown_batch_hint_falls_back_to_global():
    """精确桶模式下未见过的批次 → 空桶收缩回全局 p̄。"""
    obs = [_obs(TrustLevel.SUSPECT) for _ in range(2)] + [
        _obs(TrustLevel.TRUSTED) for _ in range(2)
    ]
    fm = FailureModel().rebuild(obs)
    layout = _layout([("A1", 0, 0, True, "Qnew")])
    rm = fm.risk_map(layout, round_id=0, solution_batch_hint="never_seen")
    assert rm["A1"] == pytest.approx(fm.summary()["p_global"])


# ---------------------------------------------------------------- 确定性

def test_deterministic():
    obs = [
        _obs(TrustLevel.SUSPECT, is_edge=(i % 2 == 0), block_id=f"Q{i % 3}")
        for i in range(30)
    ]
    fm1 = FailureModel().rebuild(obs)
    fm2 = FailureModel().rebuild(obs)
    assert fm1.summary() == fm2.summary()
    layout = _layout([("A1", 0, 0, True, "Q0"), ("B2", 1, 1, False, "Q1")])
    assert fm1.risk_map(layout, 0) == fm2.risk_map(layout, 0)
    # 同一模型重复查询稳定
    assert fm1.p_artifact(True, "Q0", "B0", 0) == fm1.p_artifact(True, "Q0", "B0", 0)


def test_invalid_prior_raises():
    with pytest.raises(FailureModelError):
        FailureModel(m_prior=0.0)
    with pytest.raises(FailureModelError):
        FailureModel(m_prior=-1.0)


def test_bucket_is_hashable_frozen():
    b = Bucket(is_edge=True, block_id="Q0", solution_batch="B0", round_band="r0-1")
    assert {b: 1}[b] == 1  # 可作字典键
    with pytest.raises(Exception):
        b.is_edge = False  # frozen


# ---------------------------------------------------------------- 依赖隔离（M6 红线）

def test_failure_model_source_has_no_forbidden_deps():
    """红线：失败模型只读 OS 可见 provenance 特征，绝不引用注入器内部参数或真值，
    且不 import adapters / planner / agent / models（双模型隔离，架构 §7.3）。"""
    src = (
        Path(__file__).resolve().parent.parent
        / "expos"
        / "qc"
        / "failure_model.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "expos.adapters",
        "expos.planner",
        "expos.agent",
        "expos.models",
        "truth",
    )
    hits = [f for f in forbidden if f in src]
    assert hits == [], f"failure_model.py 触碰禁区: {hits}"


def test_failure_model_import_graph_clean():
    """import 图不得污染出 adapters / planner / agent / models。"""
    import subprocess
    import sys

    code = (
        "import sys; sys.path.insert(0, '.');"
        "import expos.qc.failure_model;"
        "bad=[m for m in sys.modules if m.startswith("
        "('expos.adapters','expos.planner','expos.agent','expos.models'))];"
        "assert not bad, bad"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"import 图污染: {result.stderr}"
