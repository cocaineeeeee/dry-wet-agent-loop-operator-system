"""Hypothesis proposers (v0.1): deterministic default + optional LLM seam.

A proposer turns a :class:`DiscoveryContext` into a :class:`CompetingHypothesisSet` of
>=2 mutually-exclusive, machine-readable hypotheses (increase vs decrease of the axis),
each with an :class:`AssayPredicate` and an :class:`AnalysisPlan`, plus a proposer-internal
prioritisation (:mod:`hypotheses.ranking`, Robin-adapted).

CHARTER MOAT: a proposer PROPOSES; it never certifies. The default is deterministic and
template-based so v0.1 is bit-for-bit reproducible; an LLM proposer may be injected via the
:class:`HypothesisProposerBackend` protocol, but even then its output is only a set of
hypotheses (proposals), which the ledger later adjudicates against trusted observations.
The LLM is NEVER allowed to write a narrative claim (that is the moat, docs/bio_refs/04 §5).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hypotheses.objects import (
    AnalysisPlan,
    AssayPredicate,
    CompetingHypothesisSet,
    DiscoveryContext,
    Hypothesis,
    make_claim_id,
)
from hypotheses.ranking import PairwiseComparison, bradley_terry_strengths


@runtime_checkable
class HypothesisProposerBackend(Protocol):
    """The proposer seam. A conforming backend (deterministic template OR an LLM-backed
    one — see ``expos/agent/backends.py`` for the injection precedent) returns a
    :class:`CompetingHypothesisSet`. It returns PROPOSALS only; it has no ledger handle and
    cannot certify. Default implementations must be deterministic (charter DoD #10)."""

    def propose(self, context: DiscoveryContext) -> CompetingHypothesisSet: ...


class DeterministicHypothesisProposer:
    """The default, deterministic proposer (no LLM, offline, reproducible).

    For a (perturbation, axis) it emits the two competing directional hypotheses
    (increase / decrease). Prioritisation: a literature ``prior_direction`` casts weighted
    pairwise "wins" for the matching direction; a parsimony tie-breaker slightly favours
    'increase' only to break exact ties deterministically. The resulting Bradley-Terry
    strengths populate each hypothesis' ``prior_rank`` — PRIORITISATION ONLY, not evidence."""

    #: how many phantom pairwise wins a stated literature prior is worth (proposer knob).
    prior_weight: float = 3.0

    def propose(self, context: DiscoveryContext) -> CompetingHypothesisSet:
        pert, axis = context.perturbation, context.axis
        directions = (+1, -1)  # increase, decrease

        # --- proposer-internal pairwise prioritisation (Robin-adapted, NOT evidence) ---
        # index 0 = increase, 1 = decrease.
        comparisons: list[PairwiseComparison] = []
        if context.prior_direction > 0:
            comparisons.append(PairwiseComparison(0, 1, weight=self.prior_weight))
        elif context.prior_direction < 0:
            comparisons.append(PairwiseComparison(1, 0, weight=self.prior_weight))
        # deterministic parsimony tie-break: a faint edge to 'increase'.
        comparisons.append(PairwiseComparison(0, 1, weight=0.01))
        strengths = bradley_terry_strengths(2, comparisons)
        rank_by_dir = {+1: strengths[0], -1: strengths[1]}

        hyps: list[Hypothesis] = []
        for d in directions:
            verb = "increases" if d > 0 else "decreases"
            assay = AssayPredicate(
                observable=f"{axis}_readout",
                comparison="knockout vs wildtype",
                predicted_sign=d,
                effect_threshold=2.0,
                min_biological_replicates=2,
            )
            plan = AnalysisPlan(
                observable=f"{axis}_readout",
                statistic="standardized-mean-difference-z",
                decisive_abs_z=2.0,
                effect_threshold=2.0,
            )
            claim_id = make_claim_id(pert, axis, d)
            hyps.append(
                Hypothesis(
                    hypothesis_id=f"{pert}:{axis}:{'up' if d > 0 else 'down'}",
                    perturbation=pert,
                    axis=axis,
                    direction=d,
                    statement=f"{pert} {verb} {axis}",
                    assay=assay,
                    plan=plan,
                    rationale=(
                        f"Directional causal hypothesis over {axis}; literature prior "
                        f"direction={context.prior_direction} (prior only, not evidence)."
                    ),
                    prior_rank=rank_by_dir[d],
                    claim_id=claim_id,
                )
            )
        return CompetingHypothesisSet(context=context, hypotheses=tuple(hyps))
