"""M17 K-F — B-side glue: K-B's real aggregator wired into K-C's seventh-element seam.

These bodies prove the full arrow closes through ``AggregatedCertification``:
TRUSTED wet observations -> ``qc.certification_stats.aggregate_round`` e-value
adjudication -> ClaimDelta -> ledger update -> next-round re-steer, plus the I4
RoundState-persistence resume seam. All decisions here derive from REAL mcl runs
(deterministic in-process reader, noise_sd=0) — ZERO test-side claim/statistic
manipulation.

REPLICATE-SUBSTRATE OUTCOME NOTE (updated for the M17 K-F multi-replicate wet leg;
supersedes the earlier single-well note). The live MCL wet leg now expands each
promoted candidate into ``cfg.replicates`` INTERLEAVED wells (``compile_wet(...,
n_replicates=cfg.replicates, interleave=True)``), so a polarity claim's two arms
(focal=ethanol, reference=acetonitrile) each hold ``replicates`` observations per
round and the paired contrast has ``n_pairs == replicates`` (8 on the current
solvent_screen substrate). Three consequences, all verified below:

  * within-arm variance now exists => a finite se => the normal-mixture confidence
    sequence FORMS (ci=[lo, hi], not [None, None]) and the sign-flip permutation p
    can be small => a non-trivial per-round e-value that ACCUMULATES across rounds.
  * the interleaved plate order balances measurement order against the arm split
    (corr(capture_index, arm_indicator) ~ 0), so the letter-075 plate-order guard
    PASSES (confound_suspect=False). Insufficient, where it still arises, comes from
    the CONFIDENCE-SEQUENCE branch (CS contains zero / e-product below 1/alpha /
    rounds below r_min), never from a confound refusal.
  * a DECISIVE verdict is therefore reachable: on the flipped (nonpolar-high) truth
    face the focal (polar) arm reads LOWER (effect NEGATIVE), the CS excludes zero on
    the negative side, and by round 2 the accumulated e-product crosses 1/alpha => the
    seed "polar responds higher" claim is decisively REJECTED (contrary).

The GLUE-LEVEL discriminator (the K1/K2 seed) is the truth-face-driven DECISION
sequence: the flipped face progresses insufficient (round 0, evidence still starved:
rounds < r_min) -> REJECTED (round 1, decisive contrary), flipping the seed claim's
effective status to rejected; the default (polar-high) face stays insufficient on
both rounds (true effect ~ 0, so the CS straddles zero and never clears eligibility).
Zero test-side statistic manipulation: each recorded effect equals the raw wet
mean(focal)-mean(reference) difference. The broader multi-candidate DECISIVE-verdict
acceptance remains K-E's D1/D2.

PAIRING-DETERMINISM NOTE (K-F resume-red ruling, red_to_blue/089↔090). The aggregator
pairs focal[i] with reference[i] in MEASUREMENT order (capture_index), not obs_id:
obs_id is a per-run random identifier, and pairing on it scrambled the paired-diff
VARIANCE (se -> the persisted info_sum / weighted_effect_sum cross-round state)
differently every run, breaking resume equality and two-run reproduction while the
pairing-invariant per-round effect stayed stable. See ``_arm_observations`` in
``expos.qc.certification_stats``; the resume test below now holds bitwise.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from expos.kernel.claims import (
    ClaimDecisionStatus,
    ClaimRecord,
    Ledger,
)
from expos.kernel.objects import (
    InstrumentMeta,
    LayoutMeta,
    MeasuredResult,
    ObservationObject,
    TrustLevel,
)
from expos.kernel.store import RunStore
from expos.mcl import run_mcl_loop
from expos.planner.certification import (
    AggregatedCertification,
    CertificationError,
    NullCertification,
)
from expos.qc.certification_stats import (
    E_VALUE_CERTIFICATION_FN_ID,
    PLATE_ORDER_BALANCE_MAX,
    AggregationConfig,
    ClaimHead,
    RoundState,
)

_DOMAIN = Path(__file__).resolve().parents[1] / "domains" / "solvent_screen.yaml"

_POLAR = "c_polar_responds_higher"
#: The two in-window solvents the default (polar-preferring) knowledge promotes to
#: the wet leg every round: ethanol (higher polarity) vs acetonitrile (lower).
_FOCAL = "cand_ethanol"
_REFERENCE = "cand_acetonitrile"


def _polar_claim_head() -> ClaimHead:
    """The seed polarity claim as a two-arm contrast: focal = the polar solvent,
    reference = the less-polar one; "higher" = polar responds higher supports it."""
    return ClaimHead(
        claim_id=_POLAR,
        statement="polar solvents give a higher plate-reader response",
        favorable_direction="higher",
        focal_group=(_FOCAL,),
        reference_group=(_REFERENCE,),
    )


def _aggregated_cert() -> AggregatedCertification:
    # Default AggregationConfig (guard closed, r_min=2) — the aggregator's own
    # thresholds, unmodified. run_fingerprint pins the run into the provenance.
    return AggregatedCertification(
        [_polar_claim_head()], config=AggregationConfig(run_fingerprint="k_f_glue")
    )


def _claim_decisions(run_dir: Path) -> list[dict]:
    return [
        e["payload"]
        for e in RunStore(run_dir, create=False).read_events("claim_decision")
    ]


def _knowledge_fps(run_dir: Path) -> list[str]:
    return [
        e["payload"]["fingerprint"]
        for e in RunStore(run_dir, create=False).read_events("knowledge_updated")
    ]


def _round_wet_effect(run_dir: Path, round_id: int) -> float:
    """The raw wet-measured focal-minus-reference effect for a round — the MEAN focal
    reading minus the MEAN reference reading, recomputed DIRECTLY from the stored
    observations, so a test can prove the aggregator's recorded statistic (the mean
    paired difference == mean(focal) - mean(reference) for equal-n arms) derives from
    the wet data (zero injection). Under the multi-replicate substrate each arm holds
    ``cfg.replicates`` readings per round, so the arms are averaged."""
    store = RunStore(run_dir, create=False)
    vals: dict[str, list[float]] = {_FOCAL: [], _REFERENCE: []}
    for o in store.list_observations():
        if (
            o.raw_ref.kind == "wet"
            and o.round_id == round_id
            and o.cand_id in (_FOCAL, _REFERENCE)
            and o.result.value is not None
        ):
            vals[o.cand_id].append(o.result.value)
    focal_mean = sum(vals[_FOCAL]) / len(vals[_FOCAL])
    reference_mean = sum(vals[_REFERENCE]) / len(vals[_REFERENCE])
    return focal_mean - reference_mean


def _cert_records(run_dir: Path) -> list[ClaimRecord]:
    """The polarity-claim ledger records carrying a REAL K-B statistic (one per
    certified round), in append (version) order — the records the aggregator
    produced, distinguished from the seed record by a populated ``evidence_factor``."""
    return [
        r
        for r in _ledger_from_ckpt(run_dir).claims
        if r.claim_id == _POLAR and r.provenance.statistic.evidence_factor is not None
    ]


def _ledger_from_ckpt(run_dir: Path) -> Ledger:
    ckpt = RunStore(run_dir, create=False).read_checkpoint() or {}
    return Ledger(
        claims=tuple(
            ClaimRecord.model_validate(r) for r in ckpt.get("claim_ledger", [])
        )
    )


@pytest.fixture(scope="module")
def two_face_runs(tmp_path_factory) -> tuple[Path, Path]:
    """Run the SAME AggregatedCertification setup twice — once on the default
    (polar-high) truth face, once on the flipped (nonpolar-high) face — two real
    two-round mcl runs. Shared across the K1/K2 bodies to keep runtime sane."""
    default_dir = tmp_path_factory.mktemp("kf_default") / "run"
    flip_dir = tmp_path_factory.mktemp("kf_flip") / "run"
    run_mcl_loop(
        _DOMAIN, rounds=2, seed=7, out_dir=default_dir, certification=_aggregated_cert()
    )
    run_mcl_loop(
        _DOMAIN,
        rounds=2,
        seed=7,
        out_dir=flip_dir,
        certification=_aggregated_cert(),
        truth_profile="nonpolar_high",
    )
    return default_dir, flip_dir


# ============================================================ K1 substrate (flipped)


def test_flipped_face_reaches_decisive_contrary_rejected(two_face_runs):
    """K1 substrate at the glue level: on the flipped (nonpolar-high) truth face the
    seeded "polar responds higher" claim is CONTRADICTED by the wet data. Under the
    multi-replicate interleaved substrate (n_pairs == cfg.replicates per round) the
    letter-075 plate-order guard PASSES (balanced order) and a confidence sequence
    FORMS, so the honest verdict is NOT a confound refusal: the focal (polar) arm
    reads LOWER, the CS excludes zero on the negative side, and the accumulated
    e-product clears eligibility by round 2 — the claim is decisively REJECTED
    (contrary). Round 0 is still honestly insufficient (evidence starved:
    rounds_observed < r_min) even though its CS already points negative — a clean
    "not yet enough, then decisive" progression.

    Zero injection: each recorded effect EQUALS the raw wet mean(focal)-mean(reference)
    difference, and no ClaimDelta/statistic is built test-side."""
    _default_dir, flip_dir = two_face_runs
    cds = _claim_decisions(flip_dir)

    # one claim_decision per round for the single target claim, from the REAL K-B fn.
    assert len(cds) == 2
    assert all(c["claim_id"] == _POLAR for c in cds)
    assert all(c["decision_fn_id"] == E_VALUE_CERTIFICATION_FN_ID for c in cds)

    # the truth-face-driven decision sequence: honest insufficient while evidence is
    # still starved (round 0), then a DECISIVE contrary rejection (round 1).
    assert [c["decision_status"] for c in cds] == ["insufficient", "rejected"]

    # the wet data CONTRADICTS the claim: every recorded per-round effect is negative
    # and EQUALS the raw wet mean(focal)-mean(reference) difference (zero-injection).
    for c in cds:
        recorded = c["statistic"]["value"]
        assert recorded < 0.0  # polar arm reads lower -> contrary to "polar higher"
        assert math.isclose(
            recorded, _round_wet_effect(flip_dir, c["round_id"]), abs_tol=1e-9
        )

    # the certification-produced ledger records (each carrying a real K-B statistic),
    # keyed by the round count folded in so far (1 => round 0, 2 => round 1).
    recs = _cert_records(flip_dir)
    assert len(recs) == 2
    by_round = {r.provenance.statistic.rounds_observed: r for r in recs}
    for rec in recs:
        st = rec.provenance.statistic
        # guard PASSED (interleave balanced plate order) -> NOT a confound refusal.
        assert st.confound_suspect is False
        assert st.plate_order_balance is not None
        assert abs(st.plate_order_balance) < PLATE_ORDER_BALANCE_MAX
        # a CONFIDENCE SEQUENCE formed AND excludes zero on the NEGATIVE side: the
        # contrary direction is decisively estimated, not "undecided / contains zero".
        assert st.ci_low is not None and st.ci_high is not None
        assert st.ci_high < 0.0

    # round 1 (rounds_observed == 2): the accumulated e-product cleared eligibility
    # (>= 1/alpha) and the negative CS -> a DECISIVE contrary REJECTION, landed as a
    # mutating verdict (not an annotation).
    decisive = by_round[2]
    st_dec = decisive.provenance.statistic
    assert decisive.status is ClaimDecisionStatus.REJECTED
    assert decisive.is_annotation is False
    assert st_dec.evidence_factor >= st_dec.decision_thresholds["e_threshold"]

    # round 0 (rounds_observed == 1): honestly insufficient because evidence is still
    # STARVED (a single round < r_min), even though its CS already excludes zero — the
    # decisive rejection is withheld until enough rounds accumulate (gate K3 honesty).
    starved = by_round[1]
    st_starved = starved.provenance.statistic
    assert starved.status is ClaimDecisionStatus.INSUFFICIENT
    assert starved.is_annotation is True
    assert st_starved.rounds_observed < st_starved.decision_thresholds["r_min"]

    # K4 chain closure: the adjudication consumed the round's knowledge fingerprint.
    fps = _knowledge_fps(flip_dir)
    assert cds[0]["consumed_knowledge_fingerprint"] == fps[0]

    # the decisive contrary OVERTURNED the seed (K3: rejected IS a mutating status):
    # the effective status of the polarity claim flips supported -> rejected.
    assert _ledger_from_ckpt(flip_dir).effective_statuses()[_POLAR] is (
        ClaimDecisionStatus.REJECTED
    )


# ============================================================ K2 seed (differential)


def test_consistent_vs_flipped_decision_sequences_differ(two_face_runs):
    """K2 seed (mini-differential): the ONLY thing changed between the two runs is
    the hidden truth face; the knowledge, seed, config and glue are identical. The
    wet-derived per-round effect the aggregator records must therefore FLIP SIGN
    across the faces — negative on the flipped (nonpolar-high) face, non-negative on
    the default (polar-high) face — and the two faces reach GENUINELY different
    decision sequences: the flipped face's negative CS clears eligibility and lands a
    DECISIVE rejected, while the default face's near-zero effect keeps the CS
    straddling zero so it stays insufficient. Both faces pass the plate-order guard,
    so the divergence is truth-driven, not a confound artefact."""
    default_dir, flip_dir = two_face_runs

    default_cds = _claim_decisions(default_dir)
    flip_cds = _claim_decisions(flip_dir)

    # the decision-STATUS sequences genuinely differ, face-driven: the default face
    # never leaves insufficient (effect ~ 0), the flipped face reaches decisive rejected.
    assert [c["decision_status"] for c in default_cds] == ["insufficient", "insufficient"]
    assert [c["decision_status"] for c in flip_cds] == ["insufficient", "rejected"]

    # the wet-derived effect sequence flips SIGN: strict, face-driven.
    default_effects = [c["statistic"]["value"] for c in default_cds]
    flip_effects = [c["statistic"]["value"] for c in flip_cds]
    assert all(e >= 0.0 for e in default_effects)  # polar-high: focal not lower
    assert all(e < 0.0 for e in flip_effects)  # nonpolar-high: focal lower
    assert default_effects != flip_effects

    # each recorded effect equals its own run's raw wet mean difference (no injection).
    for run_dir, cds in ((default_dir, default_cds), (flip_dir, flip_cds)):
        for c in cds:
            assert math.isclose(
                c["statistic"]["value"],
                _round_wet_effect(run_dir, c["round_id"]),
                abs_tol=1e-9,
            )

    # the CONFIDENCE SEQUENCE discriminates the faces at the estimate level, and both
    # faces pass the plate-order guard (confound_suspect=False) — so the split is the
    # hidden truth face, never a confound. Default: CS straddles zero (direction
    # undecided). Flipped: CS excludes zero on the negative side (decisive contrary).
    for rec in _cert_records(default_dir):
        st = rec.provenance.statistic
        assert st.confound_suspect is False
        assert st.ci_low is not None and st.ci_low <= 0.0 <= st.ci_high  # contains zero
    for rec in _cert_records(flip_dir):
        st = rec.provenance.statistic
        assert st.confound_suspect is False
        assert st.ci_high is not None and st.ci_high < 0.0  # excludes zero, negative


# ============================================================ I4 resume: RoundState


# ---- synthetic multi-pair helpers (UNIT level: exercise the persistence path with
# ---- a NON-trivial accumulated e-product the live single-pair wet leg cannot make)


def _obs(oid: str, arm: str, value: float, cap: int) -> ObservationObject:
    return ObservationObject(
        obs_id=oid,
        exp_id="u",
        round_id=0,
        cand_id=arm,
        result=MeasuredResult(metric="response", value=value),
        layout_meta=LayoutMeta(well_id=oid, row=0, col=0),
        instrument_meta=InstrumentMeta(capture_index=cap),
        trust=TrustLevel.TRUSTED,
    )


def _balanced_strong_round(tag: str, seed: int) -> list[ObservationObject]:
    """A strong focal>reference round with a BALANCED measurement order (each arm
    spans the capture range) so the letter-075 guard passes and the e-product
    accumulates — the multi-replicate substrate the live wet leg lacks."""
    import numpy as np

    rng = np.random.default_rng(seed)
    n = 8
    focal_caps = list(range(0, 2 * n, 2))  # even indices
    ref_caps = list(range(1, 2 * n, 2))  # odd indices
    obs: list[ObservationObject] = []
    for i in range(n):
        obs.append(
            _obs(
                f"{tag}_f{i:03d}", "F", 0.90 + float(rng.normal(0, 0.03)), focal_caps[i]
            )
        )
        obs.append(
            _obs(f"{tag}_r{i:03d}", "R", 0.40 + float(rng.normal(0, 0.03)), ref_caps[i])
        )
    return obs


def test_roundstate_persists_bitwise_through_checkpoint_json():
    """I4 unit: the per-claim RoundState threads through ``decide`` as the exact
    JSON dict the mcl checkpoint stores, and a full JSON round-trip (what
    write_checkpoint/read_checkpoint do) preserves the accumulated e-product
    BITWISE. Continuing an accumulation from the disk-shaped state must be identical
    to continuing from the in-memory state."""
    head = ClaimHead(
        claim_id="c_u",
        statement="focal higher",
        favorable_direction="higher",
        focal_group=("F",),
        reference_group=("R",),
    )
    cert = AggregatedCertification([head], config=AggregationConfig(seed=3))
    led = Ledger()
    r1 = _balanced_strong_round("u1", 1)
    r2 = _balanced_strong_round("u2", 2)

    _d1, state1 = cert.decide(r1, led, None, 0, "fp0")
    # the state decide returns is already JSON-shaped; a checkpoint round-trip is
    # exactly json.loads(json.dumps(...)).
    state1_on_disk = json.loads(json.dumps(state1))
    assert state1_on_disk == state1  # dict survives the round-trip verbatim

    # non-trivial accumulation actually happened (balanced order => not refused).
    assert state1["c_u"]["rounds_observed"] == 1
    assert state1["c_u"]["e_product"] > 1.0

    # continuing from disk-restored state == continuing from in-memory state, bitwise.
    _d2_disk, state2_disk = cert.decide(r2, led, state1_on_disk, 1, "fp1")
    _d2_mem, state2_mem = cert.decide(r2, led, state1, 1, "fp1")
    assert state2_disk == state2_mem
    assert (
        state2_disk["c_u"]["e_product"] == state2_mem["c_u"]["e_product"]
    )  # bitwise-identical accumulated e-product across the resume boundary
    assert state2_disk["c_u"]["rounds_observed"] == 2
    # and RoundState reconstructs from the persisted dict identically.
    assert RoundState.model_validate(state2_disk["c_u"]) == RoundState.model_validate(
        state2_mem["c_u"]
    )


def test_resume_mid_run_no_duplicate_events_and_equal_decision_face(tmp_path):
    """I4 mcl level: interrupt after round 0, resume for round 1 with
    AggregatedCertification. The resume rebuilds ledger + certification_state from
    the checkpoint snapshot and RE-EMITS nothing; the claim_decision surface,
    knowledge-fingerprint chain and persisted certification_state all replay equal
    to the uninterrupted run (K5)."""
    whole = tmp_path / "whole"
    part = tmp_path / "part"

    run_mcl_loop(
        _DOMAIN, rounds=2, seed=7, out_dir=whole, certification=_aggregated_cert()
    )

    run_mcl_loop(
        _DOMAIN, rounds=1, seed=7, out_dir=part, certification=_aggregated_cert()
    )
    run_mcl_loop(
        _DOMAIN,
        rounds=2,
        seed=7,
        out_dir=part,
        certification=_aggregated_cert(),
        resume=True,
    )

    whole_cds = _claim_decisions(whole)
    part_cds = _claim_decisions(part)

    # no duplicate emission: resume did not re-emit round-0's claim_decision.
    assert len(part_cds) == len(whole_cds)
    assert sum(c["round_id"] == 0 for c in part_cds) == sum(
        c["round_id"] == 0 for c in whole_cds
    )

    # decision face replays: knowledge fp chain, claim_decision (round, status,
    # effect) sequence, and the persisted certification_state are all equal.
    assert _knowledge_fps(part) == _knowledge_fps(whole)

    def _seq(cds):
        return sorted(
            (c["round_id"], c["decision_status"], c["statistic"]["value"]) for c in cds
        )

    assert _seq(part_cds) == _seq(whole_cds)

    def _cert_state(run_dir):
        return (RunStore(run_dir, create=False).read_checkpoint() or {}).get(
            "certification_state"
        )

    assert _cert_state(part) == _cert_state(whole)  # RoundState restored bitwise


# ============================================================ NullCertification twin


def test_null_certification_leaves_state_key_inert(tmp_path):
    """The new certification_state checkpoint key is INERT under the default
    NullCertification: zero claim_decision events and a null state snapshot — the
    M16 regression twin is undisturbed by the K-F seam extension (K-C's E1 already
    pins the fingerprints; this pins the new field's inertness)."""
    run_dir = tmp_path / "run"
    run_mcl_loop(
        _DOMAIN, rounds=1, seed=7, out_dir=run_dir, certification=NullCertification()
    )

    assert _claim_decisions(run_dir) == []  # seventh element inert
    ckpt = RunStore(run_dir, create=False).read_checkpoint()
    assert ckpt["certification_state"] is None  # no state accumulated, byte-inert


# ============================================================ construction governance


def test_aggregated_certification_requires_registered_kb_fn():
    """Fail-loud-at-construction sanity: the aggregator's decision fn is resolved
    against the shared registry at wiring time; an empty target set is likewise a
    loud construction error (letter 072 discipline)."""
    # a valid head constructs fine (the K-B fn is registered on import).
    assert AggregatedCertification([_polar_claim_head()]).name == (
        "aggregated_certification"
    )
    with pytest.raises(CertificationError):
        AggregatedCertification([])  # no target claims
    with pytest.raises(CertificationError):
        AggregatedCertification(  # duplicate claim ids
            [_polar_claim_head(), _polar_claim_head()]
        )
