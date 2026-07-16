"""Domain-local end-to-end loop for the M25 generative-construct organ.

Closes the FULL biology loop DOMAIN-LOCALLY, reusing the EXISTING (domain-neutral)
claim + knowledge lifecycle -- no kernel/ledger specialization, nothing edited in
central files:

    seed -> generate children (5 operators) -> dry proxy preview
         -> diversity-aware acquisition (pick a batch)
         -> SIMULATED wet phenotype (trusted observation, wet_truth)
         -> certify a claim via the EXISTING kernel.claims ledger
         -> compile knowledge via the EXISTING kernel.knowledge compiler
         -> changed knowledge alters the NEXT round's acquisition decision

The full mcl/store event wiring (round-end emit points, socket sim_reader) is the
integration owner's seam (docs/bio_seams/M25.md); this module proves the organ can
drive obs->claim->knowledge->next-design locally, on the same lifecycle objects mcl
would use. HONEST LIMITS: the wet phenotype is a SIMULATION (wet_truth), the
statistic is a simple mean-difference band (a real permutation test + evidence
factor is K-B's job), and observation "trust" is asserted by construction here
rather than by a QC gate.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

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
    ledger_to_claim_dicts,
    register_decision_fn,
)
from expos.kernel.knowledge import compile_knowledge
from expos.kernel.objects import HypothesisObject, HypothesisStatus
from domains.generative_construct.acquisition import select
from domains.generative_construct.objects import ConstructDesign
from domains.generative_construct.wet_truth import POSITIVE_FACE, measure_pool

# ---------------------------------------------------------------------------
# The M25 claim and its registered decision function
# ---------------------------------------------------------------------------

#: The logical claim this organ adjudicates each round. Note it is DISTINCT from
#: the M24 seeded "higher-design-wins" surface claim: M25 asks whether the DRY
#: PROXY's own top pick is the wet winner -- i.e. whether the model ranking
#: survives contact with the (simulated) phenotype.
M25_CLAIM_ID = "m25.dry_top_design_is_wet_winner"
M25_CLAIM_STATEMENT = (
    "the design the dry expression proxy ranks first is also the highest-expressing "
    "design in (simulated) wet among the assayed batch"
)

M25_DECISION_FN_ID = "m25_dry_ranking_verdict"
M25_DECISION_FN_VERSION = "1"
M25_CRITERION_VERSION = "1"

# effect thresholds (in simulated-fluorescence units) for the crude verdict bands
_Z_INSUFFICIENT = 0.05  # |dry-top wet - best-other wet| below this -> insufficient
_BAND_STRONG = 0.20


@register_decision_fn(M25_DECISION_FN_ID, M25_DECISION_FN_VERSION)
def m25_dry_ranking_verdict(
    *, statistic: dict[str, object], power: dict[str, object], criterion_version: str
) -> ClaimDecisionStatus:
    """Reference M25 verdict: does the dry-top design win in wet?

    ``statistic['effect_estimate']`` = wet_mean(dry_top) - max wet_mean(others).
    Positive & decisive -> SUPPORTED (dry ranking predicts wet); negative &
    decisive -> REJECTED (a dry-disfavored design out-expresses the dry pick --
    the model ranking is overturned by the phenotype); small -> INSUFFICIENT
    (absence of a decisive gap is not support -- gate K3). Pure/deterministic."""
    effect = float(statistic.get("effect_estimate") or 0.0)
    if abs(effect) < _Z_INSUFFICIENT:
        return ClaimDecisionStatus.INSUFFICIENT
    return ClaimDecisionStatus.SUPPORTED if effect > 0 else ClaimDecisionStatus.REJECTED


def _evidence_band(effect: float) -> EvidenceStrength:
    if abs(effect) < _Z_INSUFFICIENT:
        return EvidenceStrength.NONE
    if abs(effect) < _BAND_STRONG:
        return EvidenceStrength.MODERATE
    return EvidenceStrength.STRONG


def _fingerprint(obj: object) -> str:
    return "sha256:" + hashlib.sha256(repr(obj).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# One round: acquire -> simulate wet -> certify -> knowledge -> next decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoundResult:
    round_id: int
    face: str
    strategy: str
    selected: tuple[str, ...]
    dry_top: str
    wet_top: str
    effect: float
    status: str
    effective_status: str
    knowledge_fingerprint: str
    next_strategy: str


def run_round(
    pool: list[ConstructDesign],
    *,
    round_id: int,
    ledger: Ledger,
    face: str = POSITIVE_FACE,
    strategy: str = "greedy",
    k: int = 3,
    replicates: int = 4,
    seed: int = 0,
    consumed_knowledge_fp: str = "sha256:genesis",
) -> tuple[Ledger, RoundResult]:
    """Run one design->assay->certify->knowledge round on ``pool``.

    Returns the updated ledger and a machine-readable ``RoundResult`` (the seed of
    the machine report). The returned ``next_strategy`` is how the CHANGED
    knowledge steers the next round's acquisition (DoD item 7)."""
    # 1. acquisition: pick a batch (observation-independent, diversity-aware)
    picks = select(pool, k, strategy=strategy)
    selected_ids = [p.design_id for p in picks]
    selected = {d.design_id: d for d in pool if d.design_id in selected_ids}
    selected_components = {did: d.components for did, d in selected.items()}

    # 2. SIMULATED wet phenotype for the batch (trusted observations)
    readings = measure_pool(
        selected_components, face=face, seed=seed, replicates=replicates
    )
    wet_mean = {
        did: sum(r.value for r in rs) / len(rs) for did, rs in readings.items()
    }

    # 3. the statistic: is the dry-top pick the wet winner?
    dry_top = max(selected_ids, key=lambda d: (selected[d].proxy or 0.0, d))
    wet_top = max(selected_ids, key=lambda d: (wet_mean[d], d))
    best_other = max(
        (wet_mean[d] for d in selected_ids if d != dry_top), default=wet_mean[dry_top]
    )
    effect = wet_mean[dry_top] - best_other

    status = m25_dry_ranking_verdict(
        statistic={"effect_estimate": effect},
        power={},
        criterion_version=M25_CRITERION_VERSION,
    )
    band = _evidence_band(effect)

    # 4. build the ClaimDelta on the EXISTING lifecycle + apply to the ledger
    obs_fps = tuple(
        ObservationFingerprint(
            obs_id=f"r{round_id}:{r.design_id}:rep{r.replicate}",
            content_fingerprint=_fingerprint(round(r.value, 6)),
        )
        for did in sorted(readings)
        for r in readings[did]
    )
    provenance = ProvenanceSnapshot(
        usage=ProvenanceUsage(
            observations=obs_fps, consumed_knowledge_fingerprint=consumed_knowledge_fp
        ),
        activity=ProvenanceActivity(
            decision_fn_id=M25_DECISION_FN_ID,
            decision_fn_version=M25_DECISION_FN_VERSION,
            criterion_version=M25_CRITERION_VERSION,
            run_fingerprint=f"m25-e2e-round{round_id}",
        ),
        statistic=StatisticSnapshot(
            test_method="mean_difference_band",
            statistic_name="wet(dry_top) - max wet(others)",
            statistic_value=effect,
            effect_estimate=effect,
            favorable_direction="higher",
            seed=seed,
        ),
    )
    new_content = (
        None
        if status is ClaimDecisionStatus.INSUFFICIENT
        else ClaimVersionContent(statement=M25_CLAIM_STATEMENT, status=status)
    )
    delta = ClaimDelta(
        target_claim_id=M25_CLAIM_ID,
        status=status,
        new_content=new_content,
        evidence_strength=band,
        provenance=provenance,
    )
    ledger, _report = apply_claim_deltas(ledger, [delta])

    # 5. compile knowledge on the EXISTING compiler (claim -> hypothesis status)
    hyp = HypothesisObject(
        hypothesis_id="hyp.m25.trust_dry_proxy",
        statement="the dry expression proxy is a reliable ranker of wet expression",
        status=HypothesisStatus.OPEN,
        evidence_refs=[M25_CLAIM_ID],
    )
    view = compile_knowledge(ledger_to_claim_dicts(ledger), [hyp])
    effective = view.hypotheses[0].effective_status

    # 6. changed knowledge -> next-round acquisition decision (DoD item 7)
    #    proxy trusted (SUPPORTED) -> exploit (greedy);
    #    proxy overturned (REJECTED) -> distrust proxy, explore (value_diversity);
    #    insufficient -> hold current strategy.
    if effective is HypothesisStatus.SUPPORTED:
        next_strategy = "greedy"
    elif effective is HypothesisStatus.REJECTED:
        next_strategy = "value_diversity"
    else:
        next_strategy = strategy

    return ledger, RoundResult(
        round_id=round_id,
        face=face,
        strategy=strategy,
        selected=tuple(selected_ids),
        dry_top=dry_top,
        wet_top=wet_top,
        effect=effect,
        status=status.value,
        effective_status=effective.value,
        knowledge_fingerprint=view.knowledge_fingerprint,
        next_strategy=next_strategy,
    )
