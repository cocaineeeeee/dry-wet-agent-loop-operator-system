"""M28 ``hypotheses`` package — literature-grounded, machine-readable hypothesis
generation + a Robin-ADAPTED pairwise prioritiser (v0.1).

This package is a LEAF: it depends only on stdlib. It does NOT import the kernel, the
claim ledger, agents or analysis backends — a hypothesis is a *proposal*, never a claim,
so hypothesis code must not be able to touch the certification substrate at all (charter
moat: agents/LLM propose; only trusted observations certify via the ledger).

Contents:
  * :mod:`hypotheses.objects`  — typed hypothesis / assay-predicate / analysis-plan objects.
  * :mod:`hypotheses.ranking`  — deterministic Bradley-Terry pairwise strength (ADAPTED
    from Robin's ``choix.ilsr_pairwise`` usage) used ONLY as a proposer-internal
    prioritisation score — an acquisition-layer heuristic, never evidence.
  * :mod:`hypotheses.proposer` — a deterministic proposer that turns a
    :class:`DiscoveryContext` into >=2 COMPETING machine-readable hypotheses, plus an
    optional LLM-backed proposer seam (default stays deterministic/template).
"""

from hypotheses.objects import (
    AnalysisPlan,
    AssayPredicate,
    CompetingHypothesisSet,
    DiscoveryContext,
    Hypothesis,
)
from hypotheses.proposer import (
    DeterministicHypothesisProposer,
    HypothesisProposerBackend,
)
from hypotheses.ranking import PairwiseComparison, bradley_terry_strengths

__all__ = [
    "AnalysisPlan",
    "AssayPredicate",
    "CompetingHypothesisSet",
    "DiscoveryContext",
    "Hypothesis",
    "DeterministicHypothesisProposer",
    "HypothesisProposerBackend",
    "PairwiseComparison",
    "bradley_terry_strengths",
]
