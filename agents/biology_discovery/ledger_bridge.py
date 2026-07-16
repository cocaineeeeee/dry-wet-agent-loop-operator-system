"""THE MOAT: the single crossing point from agent evidence to the claim ledger (v0.1).

Everything upstream (hypotheses, analysis backends, the four agents) produces PROPOSALS and
EVIDENCE and nothing else. This module is the ONLY place that:

  1. turns an :class:`EvidenceObservation` (data) into a :class:`ClaimDelta` (a *proposed*
     ledger mutation carrying full W3C-PROV five-tuple provenance), and
  2. hands that delta to the kernel gate ``apply_claim_deltas`` — the SOLE mutator of the
     ledger.

No agent, and no LLM, ever constructs a ClaimDelta or touches a ``Ledger`` directly. The
kernel gate (read-only-imported here) enforces the three governance red lines unchanged:
append-only bidirectional supersede, strength-monotonicity (weak may not retract strong),
and insufficient-never-mutates. A contradiction is therefore a *ledger event* (a supersede
under the strength gate), never an agent's spoken verdict — exactly the expos difference
from Robin's narrative evidence layer (docs/bio_refs/04 §5).

Kernel neutrality (charter §4): the decision function registered here operates on a generic
statistic dict (effect / se / z / predicted sign); the kernel never learns it is biology.
Biological semantics stay entirely in this (domain/agent) layer.
"""

from __future__ import annotations

import hashlib
import json

# READ-ONLY use of the kernel claim substrate. We call its public apply path and register a
# decision_fn through its public decorator — we never edit the kernel, and the kernel stays
# biology-agnostic. (register_decision_fn is the same public extension seam qc/
# certification_stats.py and scripts/claim_compiler.py already use.)
from expos.kernel.claims import (
    ClaimDecisionStatus,
    ClaimDelta,
    ClaimVersionContent,
    EvidenceStrength,
    Ledger,
    ObservationFingerprint,
    ProvenanceActivity,
    ProvenanceSnapshot,
    ProvenanceUsage,
    StatisticSnapshot,
    apply_claim_deltas,
    register_decision_fn,
)

from analysis_backends.base import evidence_strength_band
from analysis_backends.objects import EvidenceObservation
from hypotheses.objects import Hypothesis

# ---------------------------------------------------------------- decision fn

M28_DECISION_FN_ID = "m28_discovery_verdict"
M28_DECISION_FN_VERSION = "1"
M28_CRITERION_VERSION = "m28-smd-1"


@register_decision_fn(M28_DECISION_FN_ID, M28_DECISION_FN_VERSION)
def m28_discovery_verdict(
    *, statistic: dict, power: dict, criterion_version: str
) -> ClaimDecisionStatus:
    """Reference recomputation of the M28 verdict from a self-sufficient statistic dict
    (K4: a third party can replay the verdict from the event stream alone). Pure,
    deterministic, biology-agnostic — it sees only effect / se / z / predicted_sign.

    Verdict: under-powered or untrusted → INSUFFICIENT; otherwise the observed effect sign
    matching the hypothesis' predicted sign → SUPPORTED, opposing → REJECTED."""
    z = statistic.get("statistic_value")
    predicted_sign = statistic.get("predicted_sign", 0)
    decisive = statistic.get("decisive_abs_z", 2.0)
    trusted = bool(power.get("trusted", False))
    if z is None or not trusted or abs(z) < decisive:
        return ClaimDecisionStatus.INSUFFICIENT
    observed_sign = 1 if statistic.get("effect_estimate", 0.0) > 0 else -1
    if observed_sign == predicted_sign:
        return ClaimDecisionStatus.SUPPORTED
    return ClaimDecisionStatus.REJECTED


# ---------------------------------------------------------------- band mapping

_BAND_BY_NAME = {
    "none": EvidenceStrength.NONE,
    "weak": EvidenceStrength.WEAK,
    "moderate": EvidenceStrength.MODERATE,
    "strong": EvidenceStrength.STRONG,
    "very_strong": EvidenceStrength.VERY_STRONG,
}


def knowledge_fingerprint(ledger: Ledger) -> str:
    """A stable fingerprint over the ledger's DERIVED effective statuses — the compiled
    knowledge a round is adjudicated against (feeds ProvenanceUsage.consumed_knowledge_
    fingerprint, closing the K4 loop domain-locally)."""
    projection = {cid: s.value for cid, s in ledger.effective_statuses().items()}
    blob = json.dumps(projection, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_delta(
    hypothesis: Hypothesis,
    observation: EvidenceObservation,
    ledger: Ledger,
    *,
    run_fingerprint: str = "m28-domain-local",
    consumed_knowledge_fingerprint: str | None = None,
) -> ClaimDelta:
    """Turn ONE (hypothesis, trusted-or-not evidence observation) into a proposed
    :class:`ClaimDelta` with full provenance. This is a *proposal* — it mutates nothing;
    only ``certify_round`` → ``apply_claim_deltas`` can land it, subject to the kernel gate.

    Verdict + strength band are computed by the same deterministic rules the registered
    decision_fn recomputes, so the event stream is self-verifying (K4).

    ``consumed_knowledge_fingerprint`` — the K4 knowledge fingerprint this adjudication is
    computed AGAINST. Defaults to the domain-local ``knowledge_fingerprint(ledger)`` (the
    self-contained run). The mcl integration seam (``DiscoveryCertification``) passes the
    run's REAL compiled-knowledge fingerprint here so the provenance chain closes against
    B's ledger, never the domain-local projection (bio_seams/M28.md seam #6)."""
    band_name = evidence_strength_band(
        trusted=observation.trusted,
        abs_z=abs(observation.z),
        n_biological_replicates=observation.n_biological_replicates,
        min_biological_replicates=hypothesis.assay.min_biological_replicates,
        decisive_abs_z=hypothesis.plan.decisive_abs_z,
    )
    strength = _BAND_BY_NAME[band_name]

    if band_name == "none":
        status = ClaimDecisionStatus.INSUFFICIENT
    else:
        observed_sign = 1 if observation.effect > 0 else -1
        status = (
            ClaimDecisionStatus.SUPPORTED
            if observed_sign == hypothesis.direction
            else ClaimDecisionStatus.REJECTED
        )

    statistic = StatisticSnapshot(
        test_method="standardized-mean-difference",
        statistic_name="z",
        statistic_value=observation.z,
        effect_estimate=observation.effect,
        effect_se=observation.se,
        rounds_observed=observation.n_biological_replicates,
        favorable_direction="higher" if hypothesis.direction > 0 else "lower",
        independence_assumed=(observation.n_technical_replicates == 0
                              or observation.n_biological_replicates
                              >= hypothesis.assay.min_biological_replicates),
        decision_thresholds={
            "decisive_abs_z": hypothesis.plan.decisive_abs_z,
            "min_biological_replicates": hypothesis.assay.min_biological_replicates,
        },
    )
    # StatisticSnapshot forbids extra fields, so the decisiveness thresholds ride in the
    # ``decision_thresholds`` map above and the predicted sign is recoverable from
    # ``favorable_direction``. The registered decision_fn is a REFERENCE recomputation (the
    # online gate checks registry membership + version, never runs it), so it reads those
    # from the statistic dict a caller reconstructs from the emitted event stream.
    provenance = ProvenanceSnapshot(
        usage=ProvenanceUsage(
            observations=(
                ObservationFingerprint(
                    obs_id=observation.observation_id,
                    content_fingerprint=observation.content_fingerprint(),
                ),
            ),
            consumed_knowledge_fingerprint=(
                consumed_knowledge_fingerprint
                if consumed_knowledge_fingerprint is not None
                else knowledge_fingerprint(ledger)
            ),
        ),
        activity=ProvenanceActivity(
            decision_fn_id=M28_DECISION_FN_ID,
            decision_fn_version=M28_DECISION_FN_VERSION,
            criterion_version=M28_CRITERION_VERSION,
            run_fingerprint=run_fingerprint,
        ),
        statistic=statistic,
    )

    new_content = (
        None
        if status is ClaimDecisionStatus.INSUFFICIENT
        else ClaimVersionContent(statement=hypothesis.statement, status=status)
    )
    return ClaimDelta(
        target_claim_id=hypothesis.claim_id,
        status=status,
        new_content=new_content,
        evidence_strength=strength,
        provenance=provenance,
    )


def build_round_deltas(
    ledger: Ledger,
    items: list[tuple[Hypothesis, EvidenceObservation]],
    *,
    run_fingerprint: str = "m28-domain-local",
    consumed_knowledge_fingerprint: str | None = None,
) -> list[ClaimDelta]:
    """Build (but DO NOT land) the ClaimDeltas for a round's (hypothesis, evidence) pairs.

    This is the PURE verdict→ClaimDelta half of the bridge — no ledger mutation. It is the
    exact seam the mcl ``CertificationPolicy.decide`` contract wants (``decide`` returns
    deltas; ``mcl._certify_round`` owns the ``apply_claim_deltas`` call). ``DiscoveryCert-
    ification`` calls this from its ``decide``; ``certify_round`` calls it then lands them
    for the domain-local runnable."""
    return [
        build_delta(
            h,
            o,
            ledger,
            run_fingerprint=run_fingerprint,
            consumed_knowledge_fingerprint=consumed_knowledge_fingerprint,
        )
        for h, o in items
    ]


def certify_round(
    ledger: Ledger,
    items: list[tuple[Hypothesis, EvidenceObservation]],
    *,
    run_fingerprint: str = "m28-domain-local",
    consumed_knowledge_fingerprint: str | None = None,
) -> tuple[Ledger, list, list[ClaimDelta]]:
    """Build deltas for a round's (hypothesis, evidence) pairs and land them through the
    kernel gate. Returns ``(new_ledger, apply_report_outcomes, deltas)``.

    The ONLY ledger mutation in all of M28 happens on the ``apply_claim_deltas`` line
    below. Everything else in this package is proposal/evidence."""
    deltas = build_round_deltas(
        ledger,
        items,
        run_fingerprint=run_fingerprint,
        consumed_knowledge_fingerprint=consumed_knowledge_fingerprint,
    )
    new_ledger, report = apply_claim_deltas(ledger, deltas)
    return new_ledger, list(report.outcomes), deltas
