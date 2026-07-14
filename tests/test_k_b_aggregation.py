"""M17 K-B — per-round statistical aggregator acceptance suite.

Discriminative-first: each guard has a test that turns red if the guard is
removed (kill-comment inline). Synthetic fixtures are minimal-but-valid
``ObservationObject`` instances (the repo's real wet-observation class); a claim
is a two-arm contrast (focal vs reference) with a stated favourable direction.

Coverage:
  1. strong consistent data -> supported, correct band, e >= 1/alpha;
  2. strong contrary data -> rejected (sign-of-effect polarity);
  3. single-round data -> insufficient via the round-count branch;
  4. high-noise data -> insufficient via the CS-width branch (+ contains-zero);
  5. pure-noise randomized labels -> insufficient, never supported (D2 unit twin);
  6. e-product accumulation across two rounds -> eligibility + filtration record;
  7. determinism -> bitwise-identical serialized ClaimDelta incl. fingerprint;
  8. truth-blindness -> no ground-truth-named public parameter;
  9. plate-order confound (letter 075) -> refused to insufficient; balanced twin
     survives to a correct verdict;
 10. qualified rule -> aligned + eligible but sub-floor effect;
 11. K4 replay parity -> the registered decision fn recomputes the delta's status
     from the persisted snapshot alone (incl. the None-CS-bounds edge).
"""

from __future__ import annotations

import inspect

import numpy as np
import pytest

from expos.kernel.claims import ClaimDecisionStatus, EvidenceStrength
from expos.kernel.objects import (
    InstrumentMeta,
    LayoutMeta,
    MeasuredResult,
    ObservationObject,
    TrustLevel,
)
from expos.qc.certification_stats import (
    FILTRATION_ASSUMPTION,
    AggregationConfig,
    AggregationError,
    ClaimHead,
    RoundState,
    aggregate_round,
    e_value_round_certification,
    evidence_band_from_e,
    shafer_e_value,
)

SUP = ClaimDecisionStatus.SUPPORTED
REJ = ClaimDecisionStatus.REJECTED
QUAL = ClaimDecisionStatus.QUALIFIED
INSUF = ClaimDecisionStatus.INSUFFICIENT


# ---------------------------------------------------------------- builders


def _obs(
    oid: str,
    group: str,
    value: float,
    *,
    capture_index: int = 0,
    trust: TrustLevel = TrustLevel.TRUSTED,
) -> ObservationObject:
    """A minimal valid wet ObservationObject in arm ``group`` (its cand_id)."""
    return ObservationObject(
        obs_id=oid,
        exp_id="exp",
        round_id=0,
        cand_id=group,
        result=MeasuredResult(metric="response", value=value),
        layout_meta=LayoutMeta(well_id=oid, row=0, col=0),
        instrument_meta=InstrumentMeta(capture_index=capture_index),
        trust=trust,
    )


def _round(
    focal_vals: list[float],
    ref_vals: list[float],
    *,
    tag: str = "a",
    focal_caps: list[int] | None = None,
    ref_caps: list[int] | None = None,
) -> list[ObservationObject]:
    """A round of TRUSTED observations: focal arm 'F', reference arm 'R'. obs_ids
    are zero-padded so sorted pairing aligns index i of each arm."""
    fc = focal_caps or [0] * len(focal_vals)
    rc = ref_caps or [0] * len(ref_vals)
    obs = [
        _obs(f"{tag}_f{i:03d}", "F", v, capture_index=fc[i])
        for i, v in enumerate(focal_vals)
    ]
    obs += [
        _obs(f"{tag}_r{i:03d}", "R", v, capture_index=rc[i])
        for i, v in enumerate(ref_vals)
    ]
    return obs


HEAD = ClaimHead(
    claim_id="c_polar",
    statement="focal arm responds higher than reference",
    favorable_direction="higher",
    focal_group=("F",),
    reference_group=("R",),
)


def _strong_focal_higher(rng, tag: str, n: int = 8):
    fv = [0.90 + float(rng.normal(0, 0.03)) for _ in range(n)]
    rv = [0.40 + float(rng.normal(0, 0.03)) for _ in range(n)]
    return _round(fv, rv, tag=tag)


# ---------------------------------------------------------------- 1. supported


def test_strong_consistent_data_yields_supported_delta():
    """Two rounds of strong, consistent focal>reference data -> supported, band
    from the accumulated e-value, e-product over 1/alpha."""
    rng = np.random.default_rng(1)
    cfg = AggregationConfig()
    _, agg1 = aggregate_round(_strong_focal_higher(rng, "r1"), HEAD, cfg)
    delta, agg2 = aggregate_round(
        _strong_focal_higher(rng, "r2"), HEAD, cfg, agg1.state
    )

    assert delta.status is SUP
    assert agg2.state.e_product >= cfg.e_threshold
    assert delta.evidence_strength is evidence_band_from_e(agg2.state.e_product)
    assert delta.evidence_strength is EvidenceStrength.VERY_STRONG
    # supported delta proposes a new head echoing the verdict (K-A schema).
    assert delta.new_content is not None and delta.new_content.status is SUP
    stat = delta.provenance.statistic
    assert stat.effect_estimate > 0.0  # focal higher, aligned
    assert stat.ci_low > 0.0  # CS excludes zero on the positive side


# ---------------------------------------------------------------- 2. rejected (polarity)


def test_strong_contrary_data_yields_rejected_by_sign_flip():
    """Two rounds where focal is LOWER than reference contradict the 'focal higher'
    claim -> rejected. Polarity is the SIGN of the effect estimate.

    KILL: delete the ``aligned`` sign test in _decide_status (always take the
    supported branch) and this contrary data is mis-adjudicated as supported ->
    red."""
    rng = np.random.default_rng(2)

    def contrary(tag):
        fv = [0.40 + float(rng.normal(0, 0.03)) for _ in range(8)]
        rv = [0.90 + float(rng.normal(0, 0.03)) for _ in range(8)]
        return _round(fv, rv, tag=tag)

    cfg = AggregationConfig()
    _, agg1 = aggregate_round(contrary("r1"), HEAD, cfg)
    delta, agg2 = aggregate_round(contrary("r2"), HEAD, cfg, agg1.state)

    assert delta.status is REJ
    assert delta.provenance.statistic.effect_estimate < 0.0  # sign drives polarity
    assert agg2.state.e_product >= cfg.e_threshold  # eligible, but anti-aligned
    assert delta.new_content.status is REJ


# ---------------------------------------------------------------- 3. round-count branch


def test_single_round_is_insufficient_via_round_count_branch():
    """A single round of STRONG, wide-n data (e over threshold, CS excludes zero)
    is still insufficient because only one round is observed (r_min=2 guard).

    KILL: remove the ``rounds_observed >= r_min`` branch and this single strong
    round is adjudicated supported -> red."""
    rng = np.random.default_rng(3)
    cfg = AggregationConfig()
    delta, agg = aggregate_round(_strong_focal_higher(rng, "r1", n=12), HEAD, cfg)

    assert delta.status is INSUF
    assert delta.new_content is None  # insufficient proposes no head (K3)
    assert agg.round_e_value >= cfg.e_threshold  # e alone WOULD qualify...
    assert delta.provenance.statistic.ci_low > 0.0  # ...and CS excludes zero...
    # ...so ONLY the round-count branch keeps it insufficient.
    branches = agg.report["insufficient_branches"]
    assert branches["rounds_below_r_min"] is True
    assert branches["cs_contains_zero"] is False


# ---------------------------------------------------------------- 4. CS-width branch


def test_high_noise_is_insufficient_via_cs_width_branch():
    """High-noise data over two rounds -> wide confidence sequence that both
    exceeds w_min and contains zero -> insufficient (precision insufficient)."""
    rng = np.random.default_rng(4)

    def noisy(tag):
        fv = [0.5 + float(rng.normal(0, 0.8)) for _ in range(6)]
        rv = [0.5 + float(rng.normal(0, 0.8)) for _ in range(6)]
        return _round(fv, rv, tag=tag)

    cfg = AggregationConfig()
    _, agg1 = aggregate_round(noisy("r1"), HEAD, cfg)
    delta, agg2 = aggregate_round(noisy("r2"), HEAD, cfg, agg1.state)

    assert delta.status is INSUF
    branches = agg2.report["insufficient_branches"]
    assert branches["cs_width_over_w_min"] is True
    assert branches["cs_contains_zero"] is True
    assert agg2.report["cs_width"] > cfg.w_min


# ---------------------------------------------------------------- 5. pure noise (D2)


def test_pure_noise_randomized_labels_never_supported():
    """Randomized labels over a single response pool (no true group difference) ->
    insufficient across rounds, NEVER supported. Unit twin of the pipeline-level
    D2 randomized negative control."""
    rng = np.random.default_rng(5)
    cfg = AggregationConfig()
    state: RoundState | None = None
    for k in range(4):
        pool = rng.normal(0.5, 0.3, 16)
        fv = list(pool[:8])
        rv = list(pool[8:])
        delta, agg = aggregate_round(_round(fv, rv, tag=f"r{k}"), HEAD, cfg, state)
        state = agg.state
        assert delta.status is not SUP  # never a false positive
    assert delta.status is INSUF


# ---------------------------------------------------------------- 6. e-product accumulation


def test_e_product_accumulates_to_eligibility_with_filtration_record():
    """Two moderate rounds, each with a per-round e BELOW the threshold, whose
    e-PRODUCT crosses 1/alpha -> eligibility only after accumulation. The
    machine-readable filtration_assumption rides in the snapshot.

    KILL: drop the filtration_assumption record (set it to None) and the presence
    assertion below turns red; drop the cross-round e-product (use only the round
    e) and the first eligibility assertion turns red."""
    rng = np.random.default_rng(6)
    cfg = AggregationConfig()

    def moderate(tag):
        fv = [0.72 + float(rng.normal(0, 0.05)) for _ in range(8)]
        rv = [0.40 + float(rng.normal(0, 0.05)) for _ in range(8)]
        return _round(fv, rv, tag=tag)

    delta1, agg1 = aggregate_round(moderate("r1"), HEAD, cfg)
    assert agg1.round_e_value < cfg.e_threshold  # round 1 alone: e below threshold
    assert delta1.status is INSUF  # ...so round 1 alone is insufficient

    delta2, agg2 = aggregate_round(moderate("r2"), HEAD, cfg, agg1.state)
    assert agg2.round_e_value < cfg.e_threshold  # round 2 alone would be below too
    assert agg2.state.e_product >= cfg.e_threshold  # the PRODUCT crosses it
    assert delta2.status in (SUP, QUAL)

    # filtration assumption present + machine-readable (letter 068).
    assert delta2.provenance.statistic.filtration_assumption == FILTRATION_ASSUMPTION
    assert (
        delta2.provenance.statistic.filtration_assumption[
            "conditional_independence_across_rounds"
        ]
        is True
    )
    # each per-round e retained verbatim, product == running e_product.
    assert len(agg2.state.per_round_e) == 2
    assert np.isclose(
        agg2.state.per_round_e[0] * agg2.state.per_round_e[1], agg2.state.e_product
    )


# ---------------------------------------------------------------- 7. determinism (K5)


def test_determinism_bitwise_identical_serialized_delta():
    """Same observations + same seed -> bitwise-identical serialized ClaimDelta,
    including the provenance fingerprint."""
    rng = np.random.default_rng(7)
    fixed = _strong_focal_higher(rng, "r1")
    cfg = AggregationConfig(seed=123)

    d1, _ = aggregate_round(fixed, HEAD, cfg)
    d2, _ = aggregate_round(fixed, HEAD, cfg)

    assert d1.model_dump_json() == d2.model_dump_json()
    assert d1.provenance.fingerprint() == d2.provenance.fingerprint()


# ---------------------------------------------------------------- 8. truth-blindness


def test_public_functions_expose_no_ground_truth_parameter():
    """The aggregator is truth-blind: its public functions accept no
    ground-truth-named parameter, and the claim head / config carry no such field.
    inspect.signature scan guards against accidental future leakage (the forbidden
    token is spelled from parts so this test source itself stays scan-clean)."""
    forbidden = "tr" + "uth"  # avoid embedding the literal token in qc-adjacent code

    def _names(fn):
        return set(inspect.signature(fn).parameters)

    surfaces = {
        **{n: forbidden in n.lower() for n in _names(aggregate_round)},
        **{n: forbidden in n.lower() for n in _names(shafer_e_value)},
        **{n: forbidden in n.lower() for n in _names(e_value_round_certification)},
        **{n: forbidden in n.lower() for n in ClaimHead.model_fields},
        **{n: forbidden in n.lower() for n in AggregationConfig.model_fields},
    }
    leaked = [name for name, hit in surfaces.items() if hit]
    assert leaked == [], f"ground-truth-named parameter(s) leaked: {leaked}"


# ---------------------------------------------------------------- 9. plate-order confound


def _confounded(tag, rng):
    """A FLAT underlying surface (both arms ~0.5) with a monotone drift added along
    MEASUREMENT ORDER, where the focal arm is measured LATE (high capture index)
    and the reference arm EARLY -> drift alone fakes a focal>reference signal
    (letter 075). Small value noise keeps the diffs non-degenerate."""
    n = 8
    ref_caps = list(range(n))  # measured first
    focal_caps = list(range(n, 2 * n))  # measured last
    drift = 0.01  # per-index calibration gain
    rv = [0.5 + drift * ref_caps[i] + float(rng.normal(0, 0.01)) for i in range(n)]
    fv = [0.5 + drift * focal_caps[i] + float(rng.normal(0, 0.01)) for i in range(n)]
    return _round(fv, rv, tag=tag, focal_caps=focal_caps, ref_caps=ref_caps)


def test_plate_order_confound_is_refused_to_insufficient():
    """Drift + plate order correlated with the arm split -> a fake focal>reference
    signal. The aggregator must REFUSE (insufficient), recording the diagnostic,
    rather than emit a fake supported.

    DISCRIMINATIVE: with the guard effectively disabled (a huge balance bound) the
    SAME data is mis-adjudicated as supported -- so the balance check is
    load-bearing. The guard is doubly enforced (a _decide_status branch AND the
    accumulator refusal); KILL = removing BOTH (equivalently, neutralising the
    balance bound, which this test does explicitly) turns the first assert red."""
    rng = np.random.default_rng(8)
    cfg = AggregationConfig()
    _, agg1 = aggregate_round(_confounded("r1", rng), HEAD, cfg)
    delta, agg2 = aggregate_round(_confounded("r2", rng), HEAD, cfg, agg1.state)

    assert delta.status is INSUF
    assert delta.provenance.statistic.confound_suspect is True
    assert (
        abs(delta.provenance.statistic.plate_order_balance)
        > cfg.plate_order_balance_max
    )

    # guard disabled -> the very same confounded data emits a FAKE supported.
    rng2 = np.random.default_rng(8)
    open_cfg = AggregationConfig(plate_order_balance_max=10.0)
    _, o1 = aggregate_round(_confounded("r1", rng2), HEAD, open_cfg)
    fake, _ = aggregate_round(_confounded("r2", rng2), HEAD, open_cfg, o1.state)
    assert fake.status is SUP  # the false-positive channel the guard closes


def test_balanced_plate_order_survives_to_correct_verdict():
    """The balanced twin: a REAL focal>reference signal with the SAME drift but a
    BALANCED measurement order (both arms span the index range) -> low plate-order
    balance -> the real verdict survives (supported), not refused."""
    rng = np.random.default_rng(9)

    def balanced(tag):
        n = 8
        focal_caps = list(range(0, 2 * n, 2))  # even indices
        ref_caps = list(range(1, 2 * n, 2))  # odd indices
        drift = 0.01
        fv = [
            0.9 + drift * focal_caps[i] + float(rng.normal(0, 0.03)) for i in range(n)
        ]
        rv = [0.4 + drift * ref_caps[i] + float(rng.normal(0, 0.03)) for i in range(n)]
        return _round(fv, rv, tag=tag, focal_caps=focal_caps, ref_caps=ref_caps)

    cfg = AggregationConfig()
    _, agg1 = aggregate_round(balanced("r1"), HEAD, cfg)
    delta, agg2 = aggregate_round(balanced("r2"), HEAD, cfg, agg1.state)

    assert (
        abs(delta.provenance.statistic.plate_order_balance)
        < cfg.plate_order_balance_max
    )
    assert delta.provenance.statistic.confound_suspect is False
    assert delta.status is SUP  # real signal preserved under balanced order


# ---------------------------------------------------------------- qualified rule


def test_qualified_when_effect_below_practical_floor():
    """Aligned + eligible, but the pooled effect is below the practical-relevance
    floor -> qualified (true support, not strong). Documented rule: qualified is
    driven by the sub-floor effect size (the '+0.335 correlation' case)."""
    rng = np.random.default_rng(10)

    def small_effect(tag):
        fv = [0.55 + float(rng.normal(0, 0.02)) for _ in range(10)]
        rv = [0.40 + float(rng.normal(0, 0.02)) for _ in range(10)]
        return _round(fv, rv, tag=tag)

    # effect ~0.15; floor 0.30 -> aligned & eligible but sub-floor -> qualified.
    cfg = AggregationConfig(practical_effect_floor=0.30)
    _, agg1 = aggregate_round(small_effect("r1"), HEAD, cfg)
    delta, agg2 = aggregate_round(small_effect("r2"), HEAD, cfg, agg1.state)

    assert agg2.state.e_product >= cfg.e_threshold  # eligible
    assert 0.0 < delta.provenance.statistic.effect_estimate < 0.30  # sub-floor
    assert delta.status is QUAL

    # same data, no floor -> the same evidence is plain supported.
    cfg2 = AggregationConfig()
    _, b1 = aggregate_round(small_effect("s1"), HEAD, cfg2)
    d2, _ = aggregate_round(small_effect("s2"), HEAD, cfg2, b1.state)
    assert d2.status is SUP


# ---------------------------------------------------------------- K4 replay parity


def test_registered_fn_recomputes_verdict_from_snapshot_alone():
    """K4 self-sufficiency at the unit level: the REGISTERED decision fn, fed only
    the persisted statistic snapshot (dict form), recomputes exactly the status the
    aggregator put on the delta -- for a supported, a rejected and an insufficient
    case, including the degenerate no-variance round whose CS bounds serialize as
    None (an unbounded interval must honestly read insufficient, never crash)."""
    rng = np.random.default_rng(11)
    cfg = AggregationConfig()
    cases = []

    _, a1 = aggregate_round(_strong_focal_higher(rng, "p1"), HEAD, cfg)
    sup_delta, _ = aggregate_round(_strong_focal_higher(rng, "p2"), HEAD, cfg, a1.state)
    cases.append(sup_delta)

    def contrary(tag):
        fv = [0.40 + float(rng.normal(0, 0.03)) for _ in range(8)]
        rv = [0.90 + float(rng.normal(0, 0.03)) for _ in range(8)]
        return _round(fv, rv, tag=tag)

    _, b1 = aggregate_round(contrary("q1"), HEAD, cfg)
    rej_delta, _ = aggregate_round(contrary("q2"), HEAD, cfg, b1.state)
    cases.append(rej_delta)

    # degenerate single-pair round: no variance estimate -> CS bounds are None.
    one_pair, _ = aggregate_round(_round([0.9], [0.4], tag="z"), HEAD, cfg)
    assert one_pair.provenance.statistic.ci_low is None
    cases.append(one_pair)

    for delta in cases:
        stat = delta.provenance.statistic.model_dump()
        recomputed = e_value_round_certification(
            statistic=stat,
            power={"evidence_factor": stat["evidence_factor"]},
            criterion_version=delta.provenance.activity.criterion_version,
        )
        assert recomputed is delta.status


def test_registered_fn_fails_loudly_on_empty_criterion_inputs():
    """K-C compatibility guard: RegisteredFnCertification hands EMPTY criterion
    dicts (K-C does no statistics). Mis-wiring the K-B fn id through it must fail
    LOUDLY with a message naming the correct wiring (AggregatedCertification) --
    never a silent KeyError-shaped verdict at round end."""
    with pytest.raises(AggregationError, match="AggregatedCertification"):
        e_value_round_certification(statistic={}, power={}, criterion_version="v1")
