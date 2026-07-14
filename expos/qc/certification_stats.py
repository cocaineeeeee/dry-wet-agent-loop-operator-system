"""M17 K-B — per-round statistical aggregator (TRUSTED wet obs -> ClaimDelta).

This is the run-internal Evidence-to-Claim aggregator: at round end it turns the
round's TRUSTED wet observations into a test statistic, an anytime-valid e-value
and confidence sequence, and finally a ``ClaimDelta`` (the K-A schema, consumed
here verbatim). K-C wires the emission point; K-E writes the acceptance suite.

Design (cosigned, letters red_to_blue/076 + blue_to_red/068; math anchored to
``r4_os_references/INDEX_M17_STATS.md`` and ``m17_references/INDEX_REF_S.md`` --
no improvised statistics):

  * DECISION KERNEL = e-value. The repo's exact permutation machinery
    (``paired_permutation_test``, now in ``expos.qc.stats`` -- the same sign-flip test
    the offline compiler's ``paired_significance_verdict`` uses) produces a valid
    per-round two-sided p-value; that p is converted to an ADMISSIBLE e-value by
    the closed-form Shafer calibrator ``e = p**(-1/2) - 1`` (INDEX_M17_STATS
    §1.2/§Q1, ``expectation/modules/calibrators.py`` PToECalibrator). No test is
    rewritten. supported/rejected requires ``e >= 1/alpha`` AND a confidence
    sequence that excludes zero; POLARITY (supported vs rejected, relative to the
    claim's stated direction) is the SIGN of the effect estimate -- direction +
    effect size decide polarity, the e-value decides only eligibility to
    adjudicate (INDEX_M17_STATS §Q1, offline ``_favorable`` semantics reused).

  * insufficient is a THREE-BRANCH disjunction (INDEX_M17_STATS §Q2 + TRUTHTEST
    D3, letter 077): ``CS contains zero`` OR ``CS width > w_min`` OR
    ``rounds_observed < r_min``. The first two are data-adaptive readings of the
    same confidence sequence (contains-zero = direction undecided; too-wide =
    precision insufficient); the third guards single-round flukes. Any one true =>
    insufficient, and (K-A schema, gate K3) an insufficient ClaimDelta carries
    ``new_content=None`` -- it never proposes a head.

  * qualified is the "true support but not strong" tier (REF-S §Convergence(b)):
    eligible-and-aligned, yet the pooled effect is below a practical-relevance
    floor (``practical_effect_floor``; the |r|>=0.5 gate of the K-D discriminator
    is exactly this kind of floor). See ``QUALIFIED`` note in ``_decide_status``.

  * CROSS-ROUND evidence accumulation = e-value PRODUCT (INDEX_M17_STATS §Q1).
    The product is anytime-valid under optional stopping ONLY under filtration
    compatibility, so ``aggregate_round`` carries an EXPLICIT runtime assert AND
    attaches a machine-readable ``filtration_assumption`` record onto the
    statistic snapshot (cosign annotation, letter 068: assumptions are first-class
    chain citizens, not a bare assert). The accumulated e-product is what is
    compared to ``1/alpha`` for cross-round adjudication; each per-round e is
    recorded on the state.

  * EFFECT-ESTIMATE pooling across rounds = inverse-variance (precision-weighted)
    meta-combination (REF-B ruling, letter 076) -- a SEPARATE currency from the
    decision e-product; both live in the statistic snapshot.

  * evidence_strength BAND is derived from the ACCUMULATED e-value via the fixed
    Jeffreys/Rouder cut points (``_EVIDENCE_BANDS``; REF-S §Convergence(c),
    INDEX 076 pt.3 -- a Bayes factor is the e-value under the null, so the two
    lanes converged on the same cut points). The continuous ``evidence_factor``
    field stores the e-value itself.

  * The interpretability report (the report dict adjacent to the delta, returned
    in ``RoundAggregate.report``) back-solves, for insufficient rounds, a
    minimum-detectable-effect / information-still-needed figure -- the
    statsmodels ``solve_power`` role, "how much more evidence is needed" (REF-S
    §1 S-A). ``achieved_power`` (also stored in the snapshot) is DISPLAY-ONLY,
    NEVER a gate (K3).

TRUTH-BLIND INVARIANT (cited by K-E acceptance): the aggregator's inputs are ONLY
the TRUSTED observation set + statistical config + the claim head being
adjudicated. It never receives, reads, or references a hidden reference-surface
surface, a domain oracle, or any QC-internal suspicion value. Its public
functions expose no hidden-surface-named parameter. Absence of evidence is reported
as insufficient, never as support.

Layering: this module lives in ``expos.qc`` (the qc/eval workpiece layer of the
M17 §3 split, never kernel) — the IN-LOOP, hidden-surface-forbidden layer (lint
EXP001), which is exactly the aggregator's contract. The eval package would be
the wrong home: eval is the post-hoc leaf that reads the scoring sidecar and that no
expos module may import (tests/test_eval.py red line), so K-C's loop wiring
could never reach it there. The reused permutation primitive was relocated to
``expos.qc.stats.paired_permutation_test`` for the same reason
(eval's ``stats_tests`` re-exports it verbatim; one implementation, zero
caller change). The K-A schema is imported from ``expos.kernel.claims``
(consume, never reshape); kernel is a lower layer.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from expos.errors import ExposError
from expos.qc.stats import paired_permutation_test
from expos.kernel.claims import (
    ClaimDecisionStatus,
    ClaimDelta,
    ClaimVersionContent,
    EvidenceStrength,
    GroupSummary,
    ObservationFingerprint,
    ProvenanceActivity,
    ProvenanceSnapshot,
    ProvenanceUsage,
    StatisticSnapshot,
    register_decision_fn,
)
from expos.kernel.objects import ObservationObject, TrustLevel

# ---------------------------------------------------------------- registered id

#: The K-B statistical decision fn id + version (registered into the SAME
#: ``DECISION_FN_REGISTRY`` the offline compiler and K-A reference fn use, so the
#: online governance gate recognises deltas this aggregator produces). Distinct
#: from K-A's honest-null ``reference_round_certification``.
E_VALUE_CERTIFICATION_FN_ID = "e_value_round_certification"
E_VALUE_CERTIFICATION_FN_VERSION = "1"

#: The statistical-criterion version pinned into every provenance activity. It
#: keys the frozen threshold table a third party replays the verdict against
#: (K4 self-sufficiency). Bumping it is a registered criterion change.
CRITERION_VERSION = "e_value_cs_v1"

#: Permutation test method label recorded in the snapshot (matches the reused
#: ``expos.qc.stats`` primitive, formerly homed in eval's stats_tests).
TEST_METHOD = "sign_flip_permutation"

#: Plate-order-balance bound (letter blue_to_red/075). Reader calibration drift is
#: monotone along MEASUREMENT ORDER; if plate order correlates with the contrast
#: covariate, drift alone fakes a direction signal (a flat surface measured at
#: |corr(measurement_index, covariate)|=0.887 produced a fake |r|=0.887). A round
#: whose |plate_order_balance| exceeds this conservative bound is confound-suspect
#: and REFUSED (degraded to insufficient) — honest refusal beats a fake verdict.
#: We do NOT regress the drift out (that is the ③ temporal-channel milestone); we
#: only detect and refuse. plate_order_balance is the forward hook for ③.
PLATE_ORDER_BALANCE_MAX = 0.3

#: Reference metric span the CS-width eligibility bound ``w_min`` was calibrated on --
#: the chemistry RAW measurement scale (``metric_range`` span ~1.2 a.u.). ``w_min`` is an
#: ABSOLUTE effect-unit bound (an effect estimate and its confidence sequence carry the
#: readout's units), so exactly like the edge floor (``qc.checks._EDGE_REFERENCE_SPAN``,
#: letter 147) it is SCALE-IMPLICIT: a domain that normalizes its readout to a different
#: scale (biology percent-of-control, span 200) produces genuinely decisive confidence
#: sequences whose width is ~167x wider in absolute units, so a raw 0.5 bound mis-rejects
#: them (letter 149: CI width 3.38 on the (0,200) scale > 0.5 -> INSUFFICIENT despite
#: e_product=1033 and a CS excluding zero). FIX: express w_min RELATIVE to the effective
#: certification metric span (``AggregationConfig.effective_w_min``). At the reference span
#: the factor is exactly 1.0 (IEEE ``span/span``) -> effective bound == the historical
#: absolute value bit-for-bit (chemistry byte-identical); at span 200 it scales up ~167x.
#: The OTHER eligibility gates (e-product vs 1/alpha, CS-excludes-zero, r_min) are already
#: scale-invariant, so w_min is the sole absolute-metric-scale gate in the aggregator.
W_MIN_REFERENCE_SPAN = 1.2


class AggregationError(ExposError):
    """Malformed aggregation input (e.g. a claim head naming empty arms, or a
    non-finite metric). user_facing=False: a producer bug, surfaced loudly rather
    than silently degraded to a fake verdict (errors.py "bug never silent")."""

    user_facing = False


# ---------------------------------------------------------------- e-value + CS math


def shafer_e_value(p_value: float) -> float:
    """Shafer's closed-form p-to-e calibrator ``e = p**(-1/2) - 1`` -- the
    admissible calibrator that turns a valid p-value into a valid e-value with no
    test rewrite (INDEX_M17_STATS §1.2/§Q1, PToECalibrator). p in (0, 1];
    e in [0, inf), monotone decreasing (p=1 -> e=0, small p -> large e)."""
    if not (0.0 < p_value <= 1.0):
        raise AggregationError(f"p_value={p_value} outside (0, 1]; cannot calibrate e")
    return float(p_value**-0.5 - 1.0)


def normal_mixture_cs_radius(information: float, rho: float, alpha: float) -> float:
    """Two-sided normal-mixture (Robbins 1970 / Howard-Ramdas 2021) confidence
    sequence radius, in effect units (INDEX_M17_STATS §1.1, the mixture
    supermartingale boundary ``uniform_boundaries.h``).

    ``information`` V = accumulated Fisher information = sum_r 1/se_r^2 (so 1/V is
    the pooled sampling variance of the effect estimate); ``rho`` > 0 is the
    mixture-tuning constant (the CS is tightest near V ~ rho); coverage is
    time-uniform for any rho > 0. The radius shrinks like sqrt(log V / V), the
    iterated-logarithm inflation of a fixed-sample interval that BUYS optional
    stopping. ``information <= 0`` (a single-point round with no variance estimate)
    yields an infinite radius -- honestly, no interval can be formed yet."""
    if information <= 0.0:
        return math.inf
    return math.sqrt(
        (information + rho)
        / (information * information)
        * 2.0
        * math.log(math.sqrt((information + rho) / rho) / alpha)
    )


#: Evidence-strength bands as (strict lower bound on accumulated e -> band), in
#: DESCENDING order. Cut points 3 / 10 / 30 are the Jeffreys/Rouder Bayes-factor
#: grades (REF-S §Convergence(c)); a Bayes factor is the e-value under the null,
#: so the e-value lane and the BF lane converged on the same table (INDEX 076
#: pt.3). ``e < 1`` = no discriminating evidence -> NONE.
_EVIDENCE_BANDS: tuple[tuple[float, EvidenceStrength], ...] = (
    (30.0, EvidenceStrength.VERY_STRONG),
    (10.0, EvidenceStrength.STRONG),
    (3.0, EvidenceStrength.MODERATE),
    (1.0, EvidenceStrength.WEAK),
    (0.0, EvidenceStrength.NONE),
)


def evidence_band_from_e(e_value: float) -> EvidenceStrength:
    """Map an accumulated e-value to its ordinal evidence-strength band via the
    fixed ``_EVIDENCE_BANDS`` cut points (K-A EvidenceStrength vocabulary)."""
    for lower, band in _EVIDENCE_BANDS:
        if e_value >= lower:
            return band
    return EvidenceStrength.NONE  # pragma: no cover - 0.0 floor always matches


# ---------------------------------------------------------------- config + inputs


class AggregationConfig(BaseModel):
    """Statistical configuration for one aggregation. Frozen. The DECISION
    thresholds (alpha/w_min/r_min/floor/rho) are pinned into the provenance
    snapshot so a third party replays the verdict from the event stream alone
    (K4); ``criterion_version`` labels this pinned set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    #: CS-width branch bound calibrated on the reference scale (``W_MIN_REFERENCE_SPAN``);
    #: the OPERATIVE gate is ``effective_w_min`` (this value rescaled to ``metric_range``).
    #: A CS wider than the effective bound => insufficient (precision insufficient).
    w_min: float = Field(default=0.5, gt=0.0)
    #: The effective certification metric scale ``(min, max)`` the CS-width gate is
    #: calibrated RELATIVE to (letter 149). Default = the chemistry reference span
    #: ``(0, W_MIN_REFERENCE_SPAN)``: span/reference == 1.0 exactly, so
    #: ``effective_w_min == w_min`` bit-for-bit (chemistry byte-identical). A caller whose
    #: domain normalizes the readout (biology percent-of-control -> ``(0, 200)``) passes
    #: that range, scaling the eligibility bound to the readout the aggregator sees. This
    #: is a DOMAIN FACT (the readout scale), not a hand-scaled w_min -- the magic factor
    #: span/W_MIN_REFERENCE_SPAN is derived structurally here, never hidden in a caller.
    metric_range: tuple[float, float] = (0.0, W_MIN_REFERENCE_SPAN)
    #: single-round-fluke guard -- fewer accumulated rounds than this => insufficient.
    r_min: int = Field(default=2, ge=1)
    #: practical-relevance floor on |pooled effect|; below it an aligned, eligible
    #: verdict degrades to qualified. None disables the qualified-by-effect tier.
    practical_effect_floor: float | None = Field(default=None, ge=0.0)
    #: normal-mixture CS tuning constant rho (any rho>0 keeps coverage).
    cs_mixture_rho: float = Field(default=1.0, gt=0.0)
    #: plate-order-balance bound (letter 075); above it the round is confound-
    #: suspect and refused. See ``PLATE_ORDER_BALANCE_MAX``.
    plate_order_balance_max: float = Field(default=PLATE_ORDER_BALANCE_MAX, gt=0.0)
    n_permutations: int = Field(default=9999, ge=1)
    seed: int = Field(default=0)
    criterion_version: str = CRITERION_VERSION
    #: the compiled-knowledge fingerprint this adjudication was computed against
    #: (K4 usage slot). K-C supplies the live value; default is the empty anchor.
    consumed_knowledge_fingerprint: str = ""
    run_fingerprint: str | None = None

    @property
    def e_threshold(self) -> float:
        """Eligibility threshold ``1/alpha`` (e >= this is necessary to adjudicate)."""
        return 1.0 / self.alpha

    @property
    def effective_w_min(self) -> float:
        """``w_min`` rescaled to the effective certification metric span (letter 149).

        The CS-width eligibility bound is an ABSOLUTE effect-unit quantity calibrated on
        the chemistry raw scale (``W_MIN_REFERENCE_SPAN``). Expressed relative to the run's
        ``metric_range`` span it becomes scale-aware: at the reference span the factor is
        exactly 1.0 (IEEE ``span/span``), so this returns ``w_min`` bit-for-bit (chemistry
        byte-identical); a normalized readout (biology ``(0, 200)``) scales it up ~167x, so
        a genuinely decisive percent-of-control CS is no longer mis-read as too wide. A
        non-positive span (a degenerate declared range) falls back to factor 1.0 -- mirrors
        the edge floor's ``metric_span > 0`` guard in ``qc.checks``."""
        lo, hi = self.metric_range
        span = float(hi) - float(lo)
        scale = span / W_MIN_REFERENCE_SPAN if span > 0.0 else 1.0
        return self.w_min * scale

    def decision_thresholds(self) -> dict[str, Any]:
        """The pinned threshold set recorded in the snapshot for K4 replay. ``w_min`` is
        pinned as the EFFECTIVE (scale-aware) bound so a third party replaying the verdict
        from the event stream alone reads the same value ``_decide_status`` gated on --
        no un-pinned scale factor (letter 149). At the reference span the effective bound
        == the raw 0.5 exactly, so the pinned set stays chemistry byte-identical."""
        return {
            "alpha": self.alpha,
            "e_threshold": self.e_threshold,
            "w_min": self.effective_w_min,
            "r_min": self.r_min,
            "practical_effect_floor": self.practical_effect_floor,
            "plate_order_balance_max": self.plate_order_balance_max,
        }


class ClaimHead(BaseModel):
    """The claim being adjudicated: its id + statement + stated DIRECTION and the
    two arms to contrast. Frozen. ``favorable_direction`` names which sign of
    (focal_mean - reference_mean) SUPPORTS the claim -- "higher" means focal is
    claimed larger (positive effect favourable), "lower" the reverse. This mirrors
    the offline compiler's ``_favorable`` so both lanes read direction identically.

    Arms are matched by an observation's group key (its ``cand_id`` or, for
    controls, ``control_id``): a TRUSTED observation joins the focal arm if its key
    is in ``focal_group`` and the reference arm if its key is in
    ``reference_group``. No hidden surface is consulted -- only these public keys."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    statement: str
    favorable_direction: Literal["higher", "lower"]
    focal_group: tuple[str, ...]
    reference_group: tuple[str, ...]


class RoundState(BaseModel):
    """Cross-round accumulation state for ONE claim. Frozen, JSON-serializable,
    deterministic -- the object handed back as ``prior_state`` next round.

    Accumulators:
      * ``rounds_observed`` -- rounds folded in so far (single-round-fluke guard).
      * ``e_product`` -- product of per-round e-values (the decision currency; the
        cross-round e-process, valid under ``filtration_assumption``).
      * ``per_round_e`` -- each round's e, retained verbatim (never collapsed).
      * ``info_sum`` / ``weighted_effect_sum`` -- inverse-variance meta accumulators
        for the pooled effect (the SEPARATE estimation currency): V = sum 1/se^2,
        S = sum effect/se^2; pooled effect = S/V, pooled se = 1/sqrt(V).
      * ``filtration_assumption`` -- the machine-readable optional-stopping
        assumption behind the e-product (letter 068)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    rounds_observed: int = 0
    e_product: float = 1.0
    per_round_e: tuple[float, ...] = ()
    info_sum: float = 0.0
    weighted_effect_sum: float = 0.0
    filtration_assumption: dict[str, Any] = Field(
        default_factory=lambda: dict(FILTRATION_ASSUMPTION)
    )

    def pooled_effect(self) -> float:
        if self.info_sum <= 0.0:
            return 0.0
        return self.weighted_effect_sum / self.info_sum

    def pooled_se(self) -> float:
        if self.info_sum <= 0.0:
            return math.inf
        return 1.0 / math.sqrt(self.info_sum)


#: The optional-stopping assumption the cross-round e-product rides on. Recorded
#: as data (letter 068): the challengeable object is this record, not a bare
#: assert. ``conditional_independence_across_rounds`` = the per-round permutation
#: e-values multiply legitimately only if rounds are conditionally independent
#: given the adjudicated history; ``basis`` cites that filtration.
FILTRATION_ASSUMPTION: dict[str, Any] = {
    "conditional_independence_across_rounds": True,
    "basis": ["adjudicated_history"],
}


# ---------------------------------------------------------------- decision core


def _favorable_sign(direction: str) -> float:
    """+1 if a POSITIVE effect supports the claim ("higher"), -1 if a negative
    effect does ("lower"). Mirrors the offline compiler ``_favorable``."""
    return 1.0 if direction == "higher" else -1.0


def _decide_status(
    *,
    e_product: float,
    ci_low: float | None,
    ci_high: float | None,
    rounds_observed: int,
    pooled_effect: float,
    favorable_direction: str,
    plate_order_balance: float | None,
    thresholds: dict[str, Any],
) -> ClaimDecisionStatus:
    """PURE verdict from the accumulated decision inputs -- the single authoritative
    rule shared by ``aggregate_round`` and the registered decision fn.

    Confound guard FIRST (letter 075): if the plate order is imbalanced against the
    contrast covariate, calibration drift can fake the direction signal, so the
    round is refused -> insufficient (never supported/rejected out of a confound).

    Eligibility to adjudicate (all four must hold): e-product over threshold, CS
    excludes zero, CS no wider than w_min, and at least r_min rounds observed. Any
    failure => insufficient (three-branch disjunction; the e-threshold is the
    fourth, orthogonal, gate). When eligible, POLARITY is the sign of the effect
    relative to the claim direction: aligned => supported (or QUALIFIED when the
    pooled effect is below the practical-relevance floor -- true support, not
    strong); anti-aligned => rejected. Contrary strong evidence is REJECTED, never
    qualified."""
    e_threshold = thresholds["e_threshold"]
    w_min = thresholds["w_min"]
    r_min = thresholds["r_min"]
    floor = thresholds["practical_effect_floor"]
    balance_max = thresholds["plate_order_balance_max"]

    # confound-suspect round: refuse rather than fake-adjudicate (letter 075).
    if plate_order_balance is not None and abs(plate_order_balance) > balance_max:
        return ClaimDecisionStatus.INSUFFICIENT

    # Missing CS bounds (a no-variance round pooled no information; the snapshot
    # stores None for an infinite bound) honestly read as an unbounded interval:
    # it contains zero, so the verdict below is insufficient, never a crash.
    if ci_low is None:
        ci_low = -math.inf
    if ci_high is None:
        ci_high = math.inf

    cs_contains_zero = ci_low <= 0.0 <= ci_high
    cs_width = ci_high - ci_low
    eligible = (
        e_product >= e_threshold
        and not cs_contains_zero
        and cs_width <= w_min
        and rounds_observed >= r_min
    )
    if not eligible:
        return ClaimDecisionStatus.INSUFFICIENT

    aligned = (pooled_effect * _favorable_sign(favorable_direction)) > 0.0
    if not aligned:
        return ClaimDecisionStatus.REJECTED
    if floor is not None and abs(pooled_effect) < floor:
        # QUALIFIED: direction supported and e over threshold, but the effect is
        # below the practical-relevance floor (the "+0.335 correlation" case).
        # Note: because eligibility forces e >= 1/alpha and the Shafer calibrator
        # then forces evidence band >= STRONG, the operative qualified trigger is
        # this sub-floor effect, not a weak/moderate band (which cannot co-occur
        # with eligibility) -- see module docstring.
        return ClaimDecisionStatus.QUALIFIED
    return ClaimDecisionStatus.SUPPORTED


@register_decision_fn(E_VALUE_CERTIFICATION_FN_ID, E_VALUE_CERTIFICATION_FN_VERSION)
def e_value_round_certification(
    *, statistic: dict[str, Any], power: dict[str, Any], criterion_version: str
) -> ClaimDecisionStatus:
    """Registered K-B decision fn (governance red line 1 -- membership authority).

    Pure and deterministic: it RECOMPUTES the verdict from the persisted snapshot
    alone (K4 self-sufficiency), reading the accumulated e-value, CS bounds, round
    count, pooled effect, direction and the pinned threshold set out of the
    statistic/power dicts. ``aggregate_round`` and this fn share ``_decide_status``
    so the delta's status always equals what a third party recomputes here.

    Call-signature note (K-C compatibility): the keyword-only
    ``(statistic, power, criterion_version)`` shape matches how
    ``planner.certification.RegisteredFnCertification`` invokes registered fns —
    but that policy hands EMPTY criterion dicts (its honesty boundary: K-C does no
    statistics). This fn therefore requires the K-B snapshot fields and fails
    LOUDLY on their absence: the correct wiring for the K-B fn is
    ``planner.certification.AggregatedCertification`` (the K-F element), which
    runs ``aggregate_round`` and stamps this fn id/version on the delta without
    routing the verdict through empty inputs."""
    required = (
        "decision_thresholds",
        "ci_low",
        "ci_high",
        "rounds_observed",
        "effect_estimate",
        "favorable_direction",
    )
    missing = [k for k in required if k not in statistic]
    if missing or "evidence_factor" not in power:
        raise AggregationError(
            f"{E_VALUE_CERTIFICATION_FN_ID} requires the K-B statistic snapshot "
            f"(missing statistic keys {missing}, power has evidence_factor="
            f"{'evidence_factor' in power}); wire the K-B aggregator via "
            "AggregatedCertification -- a bare RegisteredFnCertification hands "
            "empty criterion inputs and cannot carry an e-value verdict"
        )
    thresholds = statistic["decision_thresholds"]
    return _decide_status(
        e_product=power["evidence_factor"],
        ci_low=statistic["ci_low"],
        ci_high=statistic["ci_high"],
        rounds_observed=statistic["rounds_observed"],
        pooled_effect=statistic["effect_estimate"],
        favorable_direction=statistic["favorable_direction"],
        plate_order_balance=statistic.get("plate_order_balance"),
        thresholds=thresholds,
    )


# ---------------------------------------------------------------- observation prep


def _group_key(obs: ObservationObject) -> str | None:
    """The public arm key of an observation (its candidate id, or control id for a
    control). No hidden-surface field is read."""
    return obs.control_id if obs.is_control else obs.cand_id


def _content_fingerprint(obs: ObservationObject) -> str:
    """sha256 over the exact bytes the statistic consumed (obs id + metric +
    value) -- the K1 substitution-audit key pinned into the provenance usage
    slot."""
    payload = {
        "obs_id": obs.obs_id,
        "metric": obs.result.metric,
        "value": obs.result.value,
    }
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )


def _arm_observations(
    observations: list[ObservationObject], keys: tuple[str, ...]
) -> list[ObservationObject]:
    """TRUSTED observations whose public arm key is in ``keys`` and whose metric
    value is present, sorted by MEASUREMENT ORDER (``capture_index``, obs_id as a
    stable tiebreak) so the cross-arm pairing in ``aggregate_round`` is
    REPRODUCIBLE. Non-finite values fail loudly.

    Pairing-order ruling (K-F resume-red, red_to_blue/089↔090). The paired sign-flip
    contrast pairs ``focal[i]`` with ``reference[i]`` after this sort, so the sort
    key IS the pairing key. This was previously ``obs_id`` "for determinism" — but
    ``obs_id`` is a per-run RANDOM identifier, not content-derived, so it scrambled
    the focal↔reference pairing DIFFERENTLY every run. The paired-difference MEAN
    (the recorded ``effect``) is permutation-invariant and stayed bitwise stable
    (which is why the decision-face ``statistic.value`` / knowledge-fp chain replayed
    equal), but the paired-difference VARIANCE (``se`` -> ``info_sum`` /
    ``weighted_effect_sum``, the persisted cross-round decision-face state) is
    pairing-DEPENDENT, so it drifted run-to-run and broke both resume equality and
    gate-12 two-run reproduction. This was a GENUINE decision-face determinism bug in
    the statistic itself, NOT execution-face wet-value drift (the wet value SET per
    arm is deterministic; only its obs_id assignment was random) and NOT a
    checkpoint-vs-event-log lag at the resume seam (the uninterrupted whole run is
    itself nondeterministic run-to-run, so no resume path is involved). Sorting by
    ``capture_index`` also matches the letter-075 interleaved-plate design: pairing
    each focal well with the reference well measured ADJACENT to it makes the paired
    difference drift-robust (adjacent captures carry near-equal calibration drift).
    Assertion kept at full strength; state stays decision-face and now reproduces
    bitwise."""
    keyset = set(keys)
    out: list[ObservationObject] = []
    for obs in observations:
        if obs.trust is not TrustLevel.TRUSTED:
            continue
        if _group_key(obs) not in keyset:
            continue
        value = obs.result.value
        if value is None:
            continue
        if not math.isfinite(value):
            raise AggregationError(
                f"obs {obs.obs_id}: non-finite metric value {value!r}"
            )
        out.append(obs)
    out.sort(key=lambda o: (o.instrument_meta.capture_index, o.obs_id))
    return out


def _plate_order_balance(
    focal: list[ObservationObject], reference: list[ObservationObject]
) -> float | None:
    """Plate-order-balance diagnostic (letter 075): Pearson correlation between an
    observation's MEASUREMENT ORDER (``instrument_meta.capture_index``) and its
    contrast covariate (the binary arm indicator: focal=1, reference=0), over the
    round's TRUSTED observations. This is the exact confound channel of the K-D
    domain specialised to the two-arm contrast — if measurement order tracks arm
    assignment, monotone calibration drift fakes a between-arm difference.

    Returns None when the correlation is undefined (either arm empty, or no
    variance in measurement order — e.g. all capture indices equal — so no
    order-confound can exist)."""
    idx: list[float] = []
    ind: list[float] = []
    for obs in focal:
        idx.append(float(obs.instrument_meta.capture_index))
        ind.append(1.0)
    for obs in reference:
        idx.append(float(obs.instrument_meta.capture_index))
        ind.append(0.0)
    if len(idx) < 2:
        return None
    x = np.asarray(idx, dtype=float)
    y = np.asarray(ind, dtype=float)
    sx = float(x.std())
    sy = float(y.std())
    if sx == 0.0 or sy == 0.0:
        return None  # no order variance (or single arm) -> no order-confound
    return float(np.corrcoef(x, y)[0, 1])


def _group_summary(group: str, values: list[float]) -> GroupSummary:
    arr = np.asarray(values, dtype=float)
    n = int(arr.size)
    return GroupSummary(
        group=group,
        n=n,
        mean=float(arr.mean()) if n else None,
        var=float(arr.var(ddof=1)) if n > 1 else None,
    )


# ---------------------------------------------------------------- return bundle


class RoundAggregate(BaseModel):
    """The full result of one ``aggregate_round`` call: the persistable
    cross-round ``state`` (feed as next round's ``prior_state``), the display-only
    ``report`` (interpretability / "how much more evidence is needed"), and the
    per-round e-value / p-value for at-a-glance inspection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: RoundState
    report: dict[str, Any]
    round_e_value: float | None = None
    round_p_value: float | None = None


# ---------------------------------------------------------------- interpretability


def _information_for_radius(target: float, rho: float, alpha: float) -> float | None:
    """Back-solve the smallest information V such that the normal-mixture CS radius
    <= ``target`` (the statsmodels ``solve_power`` role: "how much information is
    still needed for the CS to exclude zero", REF-S §1 S-A). Monotone bracket +
    bisection; None if unreachable in the search window."""
    if target <= 0.0:
        return None
    lo, hi = 1e-9, 1.0
    for _ in range(200):  # expand until the radius drops under target
        if normal_mixture_cs_radius(hi, rho, alpha) <= target:
            break
        lo, hi = hi, hi * 2.0
    else:
        return None
    for _ in range(200):  # bisect
        mid = math.sqrt(lo * hi)
        if normal_mixture_cs_radius(mid, rho, alpha) <= target:
            hi = mid
        else:
            lo = mid
    return hi


def _interpretability_report(
    *,
    status: ClaimDecisionStatus,
    e_product: float,
    pooled_effect: float,
    pooled_se: float,
    info_sum: float,
    ci_low: float,
    ci_high: float,
    rounds_observed: int,
    config: AggregationConfig,
) -> dict[str, Any]:
    """The display-only navigation report. For insufficient rounds it back-solves
    the extra information / rounds needed for the CS to clear zero and for the
    e-product to reach the threshold. ``achieved_power`` is a normal-approximation
    display figure ONLY -- never a gate (K3)."""
    cs_contains_zero = ci_low <= 0.0 <= ci_high
    cs_width = ci_high - ci_low
    # achieved power: normal-approx two-sided power at the observed effect/se and
    # the eligibility alpha. Display-only (post-hoc power is monotone with p).
    if pooled_se not in (0.0, math.inf) and math.isfinite(pooled_se):
        z_alpha = 1.959963984540054  # Phi^{-1}(1 - 0.05/2); display constant
        z = abs(pooled_effect) / pooled_se - z_alpha
        achieved_power = float(0.5 * (1.0 + math.erf(z / math.sqrt(2.0))))
    else:
        achieved_power = None

    report: dict[str, Any] = {
        "status": status.value,
        "e_product": e_product,
        "e_threshold": config.e_threshold,
        "cs_low": ci_low,
        "cs_high": ci_high,
        "cs_width": cs_width,
        "cs_contains_zero": cs_contains_zero,
        "rounds_observed": rounds_observed,
        "achieved_power": achieved_power,  # display-only, never a gate (K3)
        "insufficient_branches": {
            "cs_contains_zero": cs_contains_zero,
            # scale-aware bound (letter 149); == config.w_min at the reference span.
            "cs_width_over_w_min": cs_width > config.effective_w_min,
            "rounds_below_r_min": rounds_observed < config.r_min,
        },
    }
    if status is ClaimDecisionStatus.INSUFFICIENT:
        # minimum-detectable-effect / information-still-needed back-solve.
        needed_info = None
        if abs(pooled_effect) > 0.0:
            needed_info = _information_for_radius(
                abs(pooled_effect), config.cs_mixture_rho, config.alpha
            )
        report["information_observed"] = info_sum
        report["information_needed_to_exclude_zero"] = needed_info
        if needed_info is not None and info_sum > 0.0 and rounds_observed > 0:
            per_round_info = info_sum / rounds_observed
            remaining = max(0.0, needed_info - info_sum)
            report["estimated_additional_rounds"] = (
                math.ceil(remaining / per_round_info) if per_round_info > 0 else None
            )
        # minimum-detectable-effect at the CURRENT information (solve_power role).
        report["minimum_detectable_effect"] = (
            normal_mixture_cs_radius(info_sum, config.cs_mixture_rho, config.alpha)
            if info_sum > 0.0
            else None
        )
    return report


# ---------------------------------------------------------------- aggregator


def aggregate_round(
    observations: list[ObservationObject],
    claim_head: ClaimHead,
    config: AggregationConfig,
    prior_state: RoundState | None = None,
) -> tuple[ClaimDelta, RoundAggregate]:
    """Aggregate one round's TRUSTED observations for ``claim_head`` into a
    ClaimDelta + updated cross-round state.

    PURE and deterministic (gate K5): no I/O, no clock, no randomness beyond the
    seeded permutation. TRUTH-BLIND: reads only TRUSTED observation values and the
    public arm keys -- never a hidden reference surface (see module docstring).

    Pipeline: split into focal/reference arms -> paired sign-flip permutation p
    (the reused ``expos.qc.stats`` primitive) -> Shafer e-value -> fold into
    the cross-round e-product (with the filtration assumption asserted + recorded)
    and the inverse-variance pooled effect -> normal-mixture confidence sequence ->
    ``_decide_status`` verdict -> ClaimDelta carrying the full StatisticSnapshot."""
    if not claim_head.focal_group or not claim_head.reference_group:
        raise AggregationError(
            f"claim {claim_head.claim_id}: focal/reference arms must be non-empty"
        )

    focal = _arm_observations(observations, claim_head.focal_group)
    reference = _arm_observations(observations, claim_head.reference_group)

    prior = prior_state or RoundState(claim_id=claim_head.claim_id)
    if prior.claim_id != claim_head.claim_id:
        raise AggregationError(
            f"prior_state.claim_id {prior.claim_id!r} != claim head "
            f"{claim_head.claim_id!r} (cross-round state is per-claim)"
        )

    # Filtration compatibility is the precondition of a valid e-product across
    # rounds (letter 068 / INDEX §Q1). Explicit assert AND machine-readable record.
    assert prior.filtration_assumption == FILTRATION_ASSUMPTION, (
        "cross-round e-product requires a compatible filtration; prior state's "
        "filtration_assumption does not match the aggregator's"
    )

    # Confound diagnostic (letter 075): does measurement order track the arm split?
    plate_order_balance = _plate_order_balance(focal, reference)
    confound_suspect = (
        plate_order_balance is not None
        and abs(plate_order_balance) > config.plate_order_balance_max
    )

    # deterministic pairing: each arm is sorted by measurement order (capture_index)
    # in _arm_observations, so focal[i]/reference[i] pair adjacently-measured wells;
    # pair up to the shorter arm. (Was obs_id-sorted -> a random, non-reproducible
    # pairing; see the _arm_observations pairing-order ruling.)
    n_pairs = min(len(focal), len(reference))
    diffs = np.asarray(
        [focal[i].result.value - reference[i].result.value for i in range(n_pairs)],
        dtype=float,
    )
    used_obs = {o.obs_id for o in focal[:n_pairs]} | {
        o.obs_id for o in reference[:n_pairs]
    }
    used_fp = {
        obs.obs_id: _content_fingerprint(obs)
        for obs in observations
        if obs.obs_id in used_obs
    }

    # per-round statistic: mean paired difference (effect), its SE, permutation p.
    if n_pairs >= 1:
        effect = float(diffs.mean())
    else:
        effect = 0.0
    if n_pairs >= 2:
        se = float(diffs.std(ddof=1) / math.sqrt(n_pairs))
    else:
        se = math.inf  # a single (or zero) pair yields no variance estimate

    if n_pairs >= 1 and not np.allclose(diffs, 0.0):
        perm = paired_permutation_test(
            diffs, n_permutations=config.n_permutations, seed=config.seed
        )
        round_p = float(perm["p_value"])
    else:
        # no pairs, or an exactly-zero contrast: no signal, p=1 (e=0).
        round_p = 1.0
    round_e = shafer_e_value(round_p)

    # ---- fold into cross-round accumulators -----------------------------------
    # A confound-suspect round is REFUSED: its (possibly drift-faked) evidence is
    # NOT folded into the e-product or the pooled effect (letter 075 — refuse, do
    # not regress out). The accumulators pass through untouched so a confound can
    # never leak into a later eligible verdict; the round still lands a traceable
    # insufficient annotation carrying the diagnostic.
    if confound_suspect or not (math.isfinite(se) and se > 0.0):
        info_sum = prior.info_sum
        weighted_effect_sum = prior.weighted_effect_sum
    else:
        info = 1.0 / (se * se)
        info_sum = prior.info_sum + info
        weighted_effect_sum = prior.weighted_effect_sum + effect * info

    if confound_suspect:
        state = RoundState(
            claim_id=claim_head.claim_id,
            rounds_observed=prior.rounds_observed,
            e_product=prior.e_product,
            per_round_e=prior.per_round_e,
            info_sum=prior.info_sum,
            weighted_effect_sum=prior.weighted_effect_sum,
            filtration_assumption=dict(FILTRATION_ASSUMPTION),
        )
    else:
        state = RoundState(
            claim_id=claim_head.claim_id,
            rounds_observed=prior.rounds_observed + 1,
            e_product=prior.e_product * round_e,
            per_round_e=prior.per_round_e + (round_e,),
            info_sum=info_sum,
            weighted_effect_sum=weighted_effect_sum,
            filtration_assumption=dict(FILTRATION_ASSUMPTION),
        )

    pooled_effect = state.pooled_effect()
    pooled_se = state.pooled_se()
    radius = normal_mixture_cs_radius(
        state.info_sum, config.cs_mixture_rho, config.alpha
    )
    ci_low = pooled_effect - radius
    ci_high = pooled_effect + radius

    thresholds = config.decision_thresholds()
    status = _decide_status(
        e_product=state.e_product,
        ci_low=ci_low,
        ci_high=ci_high,
        rounds_observed=state.rounds_observed,
        pooled_effect=pooled_effect,
        favorable_direction=claim_head.favorable_direction,
        plate_order_balance=plate_order_balance,
        thresholds=thresholds,
    )
    band = evidence_band_from_e(state.e_product)

    report = _interpretability_report(
        status=status,
        e_product=state.e_product,
        pooled_effect=pooled_effect,
        pooled_se=pooled_se,
        info_sum=state.info_sum,
        ci_low=ci_low,
        ci_high=ci_high,
        rounds_observed=state.rounds_observed,
        config=config,
    )
    report["plate_order_balance"] = plate_order_balance
    report["confound_suspect"] = confound_suspect

    snapshot = StatisticSnapshot(
        test_method=TEST_METHOD,
        statistic_name="mean_paired_difference",
        statistic_value=effect,
        tail="two-sided",
        p_value=round_p,
        effect_estimate=pooled_effect,
        effect_se=(pooled_se if math.isfinite(pooled_se) else None),
        ci_low=ci_low if math.isfinite(ci_low) else None,
        ci_high=ci_high if math.isfinite(ci_high) else None,
        achieved_power=report["achieved_power"],
        evidence_factor=state.e_product,  # continuous e-value behind the band
        independence_assumed=True,
        seed=config.seed,
        per_group=(
            _group_summary("focal", [o.result.value for o in focal]),
            _group_summary("reference", [o.result.value for o in reference]),
        ),
        rounds_observed=state.rounds_observed,
        favorable_direction=claim_head.favorable_direction,
        filtration_assumption=dict(state.filtration_assumption),
        decision_thresholds=thresholds,
        plate_order_balance=plate_order_balance,
        confound_suspect=confound_suspect,
    )

    new_content = (
        None
        if status is ClaimDecisionStatus.INSUFFICIENT
        else ClaimVersionContent(statement=claim_head.statement, status=status)
    )
    delta = ClaimDelta(
        target_claim_id=claim_head.claim_id,
        status=status,
        new_content=new_content,
        evidence_strength=band,
        provenance=ProvenanceSnapshot(
            usage=ProvenanceUsage(
                observations=tuple(
                    ObservationFingerprint(obs_id=oid, content_fingerprint=used_fp[oid])
                    for oid in sorted(used_fp)
                ),
                consumed_knowledge_fingerprint=config.consumed_knowledge_fingerprint,
            ),
            activity=ProvenanceActivity(
                decision_fn_id=E_VALUE_CERTIFICATION_FN_ID,
                decision_fn_version=E_VALUE_CERTIFICATION_FN_VERSION,
                criterion_version=config.criterion_version,
                run_fingerprint=config.run_fingerprint,
            ),
            statistic=snapshot,
        ),
    )

    aggregate = RoundAggregate(
        state=state,
        report=report,
        round_e_value=round_e,
        round_p_value=round_p,
    )
    return delta, aggregate
