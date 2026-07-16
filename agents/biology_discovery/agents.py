"""The four M28 discovery agents, deepened to v0.1.

    HypothesisAgent    — literature-grounded context -> >=2 COMPETING machine-readable
                         hypotheses (each an assayable claim + analysis plan).
    AnalysisAgent      — hypothesis + assay dataset -> an EvidenceObservation (a statistic).
                         It produces EVIDENCE ONLY; it has NO ledger handle and CANNOT
                         construct a claim (it does not even import the kernel).
    ContradictionAgent — detects mutually-exclusive hypotheses and routes the competing
                         evidence through the ledger, where a contradiction is resolved as
                         a SUPERSEDE under the kernel's strength-monotonicity gate.
    ReplicationAgent   — enforces the independent-biological-replicate bar (technical
                         replicates never masquerade) and chooses the follow-up experiment
                         from the CHANGED knowledge.

CHARTER MOAT (docs/bio_refs/04 §5): agents PROPOSE and ANALYSE; only trusted observations,
routed through ``ledger_bridge`` -> ``apply_claim_deltas`` (the kernel gate), ever move a
claim. No agent and no LLM writes a narrative claim. ``AnalysisAgent`` and the proposer are
structurally incapable of mutating the ledger (they do not import it).
"""

from __future__ import annotations

from analysis_backends.base import AnalysisBackend, evidence_strength_band
from analysis_backends.deterministic import DeterministicAnalysisBackend
from analysis_backends.objects import AssayDataset, EvidenceObservation
from hypotheses.objects import CompetingHypothesisSet, DiscoveryContext, Hypothesis
from hypotheses.proposer import DeterministicHypothesisProposer, HypothesisProposerBackend

from agents.biology_discovery.objects import (
    AnalysisVerdictPreview,
    FollowUp,
    ReplicationVerdict,
)

# The bridge is the SINGLE crossing point to the ledger; it is imported only by the agents
# that adjudicate (Contradiction / the run harness), never by the analysis/proposer side.
from agents.biology_discovery import ledger_bridge


# ----------------------------------------------------------------- Hypothesis agent


class HypothesisAgent:
    """Poses >=2 competing, machine-readable hypotheses from a literature-grounded context.

    The proposer backend defaults to the deterministic template (reproducible v0.1); an LLM
    proposer may be injected (it still returns proposals only — the moat holds)."""

    def __init__(self, backend: HypothesisProposerBackend | None = None) -> None:
        self.backend: HypothesisProposerBackend = backend or DeterministicHypothesisProposer()

    def pose(self, context: DiscoveryContext) -> CompetingHypothesisSet:
        hset = self.backend.propose(context)
        if len(hset.hypotheses) < 2:
            raise ValueError("HypothesisAgent must pose >=2 competing hypotheses")
        return hset

    @staticmethod
    def assayable_claims(hset: CompetingHypothesisSet) -> list[dict]:
        """The machine-readable assayable form of each hypothesis (for the report / an
        assay-selection consumer). Pure projection, no side effects."""
        return [
            {
                "hypothesis_id": h.hypothesis_id,
                "claim_id": h.claim_id,
                "statement": h.statement,
                "observable": h.assay.observable,
                "comparison": h.assay.comparison,
                "predicted_sign": h.assay.predicted_sign,
                "effect_threshold": h.assay.effect_threshold,
                "min_biological_replicates": h.assay.min_biological_replicates,
                "prior_rank": round(h.prior_rank, 4),
            }
            for h in hset.ranked()
        ]


# ----------------------------------------------------------------- Analysis agent


class AnalysisAgent:
    """Runs the analysis backend to turn a hypothesis + assay dataset into an
    :class:`EvidenceObservation` — a statistic with honest provenance.

    RED LINE (structural): the analysis backend it delegates to lives in the
    ``analysis_backends`` package, which does NOT import ``expos.kernel.claims`` at all (a
    guard test asserts this). Its only output type is ``EvidenceObservation``; it never
    constructs a ClaimDelta and never calls the mutator — analysis produces evidence, the
    ledger certifies (charter §4)."""

    def __init__(self, backend: AnalysisBackend | None = None) -> None:
        self.backend: AnalysisBackend = backend or DeterministicAnalysisBackend()

    def analyse(self, hypothesis: Hypothesis, dataset: AssayDataset) -> EvidenceObservation:
        return self.backend.analyse(hypothesis, dataset)

    def preview(
        self, hypothesis: Hypothesis, observation: EvidenceObservation
    ) -> AnalysisVerdictPreview:
        """A NON-CERTIFYING preview of the expected ledger verdict, for reporting only. The
        real verdict is whatever ``apply_claim_deltas`` lands; this never mutates anything."""
        band = evidence_strength_band(
            trusted=observation.trusted,
            abs_z=abs(observation.z),
            n_biological_replicates=observation.n_biological_replicates,
            min_biological_replicates=hypothesis.assay.min_biological_replicates,
            decisive_abs_z=hypothesis.plan.decisive_abs_z,
        )
        if band == "none":
            expected, why = "insufficient", "untrusted or under-powered (non-mutating)"
        else:
            observed_sign = 1 if observation.effect > 0 else -1
            if observed_sign == hypothesis.direction:
                expected, why = "supported", f"trusted decisive |z|={abs(observation.z):.2f}, match"
            else:
                expected, why = "rejected", f"trusted decisive |z|={abs(observation.z):.2f}, oppose"
        return AnalysisVerdictPreview(
            hypothesis_id=hypothesis.hypothesis_id,
            claim_id=hypothesis.claim_id,
            expected_status=expected,
            z=observation.z,
            band=band,
            rationale=why,
        )


# ----------------------------------------------------------------- Contradiction agent


class ContradictionAgent:
    """Finds mutually-exclusive hypotheses and resolves them ON THE LEDGER.

    ``find_pairs`` reports the contradictory hypothesis-id pairs (same perturbation+axis,
    opposite direction). ``adjudicate`` routes the competing (hypothesis, evidence) pairs
    through the bridge so the kernel gate resolves the contradiction as a SUPERSEDE (append-
    only, bidirectional, strength-monotone). The contradiction is thus a ledger event, not
    an agent's spoken verdict — and a WEAK contradiction cannot retract a STRONG head (the
    kernel degrades it to an annotation), which is exactly the moat."""

    @staticmethod
    def find_pairs(hset: CompetingHypothesisSet) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        hyps = list(hset.hypotheses)
        for i, a in enumerate(hyps):
            for b in hyps[i + 1:]:
                if (a.perturbation, a.axis) == (b.perturbation, b.axis) and \
                        a.direction == -b.direction:
                    pairs.append((a.hypothesis_id, b.hypothesis_id))
        return pairs

    @staticmethod
    def adjudicate(ledger, items, *, run_fingerprint: str = "m28-domain-local"):
        """Land the round's (hypothesis, evidence) pairs through the ONLY mutator
        (``ledger_bridge.certify_round`` -> ``apply_claim_deltas``). Returns
        ``(new_ledger, outcomes, deltas)``. The agent proposes deltas; the kernel decides."""
        return ledger_bridge.certify_round(ledger, items, run_fingerprint=run_fingerprint)


# ----------------------------------------------------------------- Replication agent


class ReplicationAgent:
    """Enforces the independent-biological-replicate bar and chooses the follow-up.

    A decisive verdict may only STAND on ``>= min_reps`` INDEPENDENT biological replicates;
    technical replicates are counted but never satisfy the bar (charter §4, M24-B collapse
    mechanism). The follow-up decision is read from the CHANGED knowledge so the loop
    closes: knowledge update -> a different next experiment."""

    def __init__(self, min_reps: int = 2) -> None:
        self.min_reps = min_reps

    def assess(self, observation: EvidenceObservation) -> ReplicationVerdict:
        independent = observation.n_biological_replicates >= self.min_reps
        if independent:
            reason = (
                f"{observation.n_biological_replicates} independent biological replicates "
                f">= {self.min_reps}; decisive verdict may stand."
            )
        elif observation.n_technical_replicates > 0:
            reason = (
                f"only {observation.n_biological_replicates} biological replicate(s) with "
                f"{observation.n_technical_replicates} TECHNICAL replicate(s) — technical "
                f"replicates do not count as independent evidence; need independent "
                f"biological replication before a decisive verdict may stand."
            )
        else:
            reason = (
                f"only {observation.n_biological_replicates} biological replicate(s) < "
                f"{self.min_reps}; need independent biological replication."
            )
        return ReplicationVerdict(
            observation_id=observation.observation_id,
            n_biological=observation.n_biological_replicates,
            n_technical=observation.n_technical_replicates,
            independent=independent,
            reason=reason,
        )

    def next_follow_up(
        self,
        context: DiscoveryContext,
        ledger,
        *,
        replication: ReplicationVerdict | None = None,
    ) -> FollowUp:
        """Choose the next experiment FROM the ledger's current (possibly changed)
        knowledge. If the direction claim is now certified AND independently replicated,
        move on to a new axis; if certified but only technically replicated, demand an
        independent biological replicate; if still open, run the assay."""
        from hypotheses.objects import make_claim_id  # local: keep module import graph flat

        # READ-ONLY status peek (reading derived statuses never mutates the ledger — the
        # moat is about not MUTATING / not constructing ClaimDeltas, which this does not do).
        from expos.kernel.claims import ClaimDecisionStatus

        kfp = ledger_bridge.knowledge_fingerprint(ledger)
        statuses = ledger.effective_statuses()
        up_id = make_claim_id(context.perturbation, context.axis, +1)
        down_id = make_claim_id(context.perturbation, context.axis, -1)
        certified_dir = None
        if statuses.get(up_id) is ClaimDecisionStatus.SUPPORTED:
            certified_dir = "increase"
        elif statuses.get(down_id) is ClaimDecisionStatus.SUPPORTED:
            certified_dir = "decrease"

        if certified_dir is None:
            return FollowUp(
                action="run_assay",
                target=f"{context.perturbation}::{context.axis}",
                reason="direction not yet certified; execute the planned assay.",
                driven_by=kfp,
            )
        if replication is not None and not replication.independent:
            return FollowUp(
                action="seek_independent_biological_replication",
                target=f"{context.perturbation}::{context.axis}",
                reason=(
                    f"direction certified ({certified_dir}) but backing is not independently "
                    f"replicated: {replication.reason}"
                ),
                driven_by=kfp,
            )
        return FollowUp(
            action="probe_new_axis",
            target=f"{context.perturbation}::<next-axis>",
            reason=(
                f"direction on {context.axis} certified ({certified_dir}) and independently "
                f"replicated; the informative next probe is a DIFFERENT axis."
            ),
            driven_by=kfp,
        )
