"""M24 bio ruling ③ — the technical-vs-biological replicate evidence-independence
guard. Proves the qc-layer collapse (``expos.qc.replicate_collapse``) feeds the
domain-agnostic evidence compiler the correct INDEPENDENT-unit count, and that the
chemistry path (no ``replicate_kind``) is byte-identical.

The load-bearing discriminator is test 2, the INFORMATION-INFLATION guard: with the
collapse removed, N correlated technical re-reads of one biological unit are counted as
N independent evidence units, over-estimating the e-product and turning a run FALSELY
DECISIVE. With the collapse (replicate_kind="technical") each biological unit becomes
ONE observation, so the compiler pools n = (biological units), not (units × technical
reads), and the same run is honestly insufficient.
"""

from __future__ import annotations

import math

import pytest

from expos.kernel.claims import ClaimDecisionStatus, Ledger
from expos.kernel.objects import (
    InstrumentMeta,
    LayoutMeta,
    MeasuredResult,
    ObservationObject,
    TrustLevel,
)
from expos.planner.certification import AggregatedCertification, CertificationError
from expos.qc.certification_stats import (
    AggregationConfig,
    ClaimHead,
    aggregate_round,
)
from expos.qc.replicate_collapse import (
    collapse_technical_replicates,
    public_unit_key,
)

# ------------------------------------------------------------------ obs builders

_EXP = "exp_test"


def _obs(cand: str, value: float, capture: int, *, round_id: int = 0) -> ObservationObject:
    """A minimal TRUSTED candidate observation. ``capture`` is the measurement order
    (drives the aggregator's pairing + the plate-order guard); each replicate of one
    ``cand`` shares the cand_id (its biological-unit arm key) with a distinct capture."""
    return ObservationObject(
        obs_id=f"obs_{cand}_{capture}",
        exp_id=_EXP,
        round_id=round_id,
        cand_id=cand,
        result=MeasuredResult(metric="response", value=value, unit="au"),
        layout_meta=LayoutMeta(well_id=f"W{capture}", row=capture // 4, col=capture % 4),
        instrument_meta=InstrumentMeta(capture_index=capture),
        trust=TrustLevel.TRUSTED,
    )


#: Two focal units (F1,F2, higher) vs two reference units (R1,R2, lower), each with
#: 4 TECHNICAL replicates. The per-round read order cycles [F1,R1,R2,F2] so that BOTH
#: the full 16-read set AND the 4 collapsed representatives balance measurement order
#: against the arm split (corr(capture_index, arm) ~ 0) — the plate-order guard passes
#: in BOTH paths, so the ONLY thing that differs between them is the independent-unit
#: count. Tiny per-read jitter gives non-degenerate within/between variance.
_CYCLE = ("F1", "R1", "R2", "F2")
_BASE = {"F1": 1.0, "F2": 1.0, "R1": 0.0, "R2": 0.0}
_N_TECH = 4  # technical replicates per biological unit


def _round_observations(round_id: int) -> list[ObservationObject]:
    obs: list[ObservationObject] = []
    capture = 0
    for rep in range(_N_TECH):
        for unit in _CYCLE:
            # deterministic jitter, distinct per (unit, rep): real spread, sign kept.
            jitter = 0.01 * rep + 0.003 * _CYCLE.index(unit)
            obs.append(_obs(unit, _BASE[unit] + jitter, capture, round_id=round_id))
            capture += 1
    return obs


def _head() -> ClaimHead:
    return ClaimHead(
        claim_id="c_focal_higher",
        statement="focal units read higher",
        favorable_direction="higher",
        focal_group=("F1", "F2"),
        reference_group=("R1", "R2"),
    )


def _run_two_rounds(*, replicate_kind: str | None):
    """Drive AggregatedCertification for two rounds, threading cross-round state.
    Returns (last_delta, e_product)."""
    cert = AggregatedCertification(
        [_head()],
        config=AggregationConfig(run_fingerprint="m24_collapse", seed=0),
        replicate_kind=replicate_kind,
    )
    ledger = Ledger()
    state = None
    delta = None
    for r in range(2):
        deltas, state = cert.decide(
            _round_observations(r), ledger, state, r, "kfp"
        )
        delta = deltas[0]
    e_product = state["c_focal_higher"]["e_product"]
    return delta, e_product


# ------------------------------------------------------------------ 1. byte-identity

def test_chemistry_byte_identity_no_replicate_kind():
    """Regression anchor: with NO replicate_kind declared, the certification seam must
    pass observations to the compiler UNCHANGED — identical to calling aggregate_round
    directly on the raw observations (same count, same values, same verdict). Solvent /
    catalyst domains declare no replicate_kind, so this is their byte-for-byte contract.
    """
    obs = _round_observations(0)
    head = _head()
    cfg = AggregationConfig(
        run_fingerprint="m24_collapse", seed=0, consumed_knowledge_fingerprint="kfp"
    )
    direct_delta, direct_agg = aggregate_round(obs, head, cfg, None)

    cert = AggregatedCertification(
        [head],
        config=AggregationConfig(run_fingerprint="m24_collapse", seed=0),
        replicate_kind=None,
    )
    deltas, state = cert.decide(obs, Ledger(), None, 0, "kfp")

    # Same per-group n reaches the compiler (8 focal + 8 reference, uncollapsed).
    per_group = {g.group: g.n for g in direct_delta.provenance.statistic.per_group}
    assert per_group == {"focal": 8, "reference": 8}
    # The seam's None path == the direct compiler call, bit for bit.
    assert deltas[0].model_dump() == direct_delta.model_dump()
    assert state["c_focal_higher"]["e_product"] == direct_agg.state.e_product


# ------------------------------------------------- 2. THE information-inflation guard

def test_information_inflation_guard_technical_not_falsely_decisive():
    """Kill-comment: REMOVING the collapse makes the technical-only run FALSELY more
    decisive. 2 biological units per arm, each with 4 technical replicates.

      * WITHOUT collapse -> 8 correlated re-reads per arm are counted as 8 independent
        evidence units -> n_pairs=8 -> the e-product crosses 1/alpha by round 2 -> a
        DECISIVE verdict from what is really n=2 biological units per arm (over-estimated
        information).
      * WITH collapse (replicate_kind="technical") -> each unit -> ONE observation ->
        n_pairs=2 (the true biological n) -> the e-product stays far below threshold ->
        honestly INSUFFICIENT.
    """
    thr = AggregationConfig().e_threshold  # 1/alpha = 20

    naive_delta, naive_e = _run_two_rounds(replicate_kind=None)
    collapsed_delta, collapsed_e = _run_two_rounds(replicate_kind="technical")

    # The uncollapsed (technical-as-independent) path is falsely decisive...
    assert naive_delta.status is ClaimDecisionStatus.SUPPORTED
    assert naive_e >= thr
    # ...while the collapsed path is honestly not decisive.
    assert collapsed_delta.status is ClaimDecisionStatus.INSUFFICIENT
    assert collapsed_e < thr
    # The technical n inflated the evidence: strictly more e without the collapse.
    assert naive_e > collapsed_e


# ---------------------------------------------- 3. biological replicates NOT collapsed

def test_biological_replicates_reach_compiler_at_full_n():
    """replicate_kind="biological" => identity: biological replicates ARE independent
    evidence, so the full n reaches the compiler exactly as the None path does."""
    bio_delta, bio_e = _run_two_rounds(replicate_kind="biological")
    naive_delta, naive_e = _run_two_rounds(replicate_kind=None)
    assert bio_delta.model_dump() == naive_delta.model_dump()
    assert bio_e == naive_e
    # Full n (uncollapsed) reached the compiler.
    per_group = {g.group: g.n for g in bio_delta.provenance.statistic.per_group}
    assert per_group == {"focal": 8, "reference": 8}


# ------------------------------------------------------------------ 4. determinism (K5)

def test_collapse_is_deterministic():
    """Same observations collapsed twice => bitwise-identical result (no clock/random)."""
    obs = _round_observations(0)
    a = collapse_technical_replicates(obs, biological_unit_key=public_unit_key)
    b = collapse_technical_replicates(obs, biological_unit_key=public_unit_key)
    assert [o.model_dump() for o in a] == [o.model_dump() for o in b]
    # 4 biological units in, 4 collapsed observations out (16 reads -> 4 units).
    assert len(obs) == 16
    assert len(a) == 4


# ------------------------------------------------- 5. within-unit uncertainty (reducer)

def test_collapsed_uncertainty_is_within_unit_standard_error():
    """The collapsed observation's SE reflects within-unit spread: value = mean of the
    k technical reads, uncertainty = s/sqrt(k) (sample std over sqrt k). Removing the
    collapse would leave each raw read with no aggregated within-unit uncertainty."""
    reads = [10.0, 12.0, 11.0, 13.0]  # one biological unit, 4 technical reads
    obs = [_obs("U1", v, cap) for cap, v in enumerate(reads)]
    (collapsed,) = collapse_technical_replicates(obs, biological_unit_key=public_unit_key)

    k = len(reads)
    expected_mean = sum(reads) / k
    s = math.sqrt(sum((v - expected_mean) ** 2 for v in reads) / (k - 1))
    expected_se = s / math.sqrt(k)

    assert collapsed.result.value == pytest.approx(expected_mean)
    assert collapsed.result.uncertainty == pytest.approx(expected_se)
    # It stays ONE observation on the same biological-unit arm key.
    assert collapsed.cand_id == "U1"
    assert public_unit_key(collapsed) == "U1"

    # median reducer keeps the central value a median but the same within-unit SE.
    (median_collapsed,) = collapse_technical_replicates(
        obs, biological_unit_key=public_unit_key, reducer="median"
    )
    assert median_collapsed.result.value == pytest.approx((11.0 + 12.0) / 2)
    assert median_collapsed.result.uncertainty == pytest.approx(expected_se)


# ------------------------------------------------------------------ loud-enum guard

def test_unknown_replicate_kind_fails_loud():
    with pytest.raises(CertificationError):
        AggregatedCertification([_head()], replicate_kind="tech")  # typo, not a kind
