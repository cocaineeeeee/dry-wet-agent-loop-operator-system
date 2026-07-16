"""Typed hypothesis objects for M28 (v0.1).

A :class:`DiscoveryContext` is the literature-grounded *starting point* of a discovery
episode: a target perturbation, an observable axis, an optional PRIOR direction suggested
by the literature (a prior belief, NOT evidence — this distinction is load-bearing), and
citations. From it the proposer generates >=2 COMPETING :class:`Hypothesis` objects.

Every hypothesis is machine-readable and carries an :class:`AssayPredicate` — a falsifiable
predicted-sign statement with an effect threshold and a required number of INDEPENDENT
BIOLOGICAL replicates — plus an :class:`AnalysisPlan` describing how it would be tested.

Red line encoded structurally: nothing here can certify anything. A hypothesis carries a
``prior_rank`` (a proposer-internal prioritisation score) but that score is explicitly
NOT evidence — only a trusted observation, routed through the claim ledger, ever moves a
verdict (charter §4). These objects are frozen: a proposed hypothesis is a fact about what
was proposed, not a mutable scratchpad.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:12]}"


@dataclass(frozen=True)
class DiscoveryContext:
    """The literature-grounded starting point of one discovery episode.

    ``prior_direction`` is a PRIOR belief (e.g. suggested by a review) used only to seed
    the proposer's prioritisation — it is never evidence and never certifies anything. It
    exists precisely so the demo can show trusted evidence OVERTURNING a literature prior
    (charter: "wet evidence must be able to overturn model ranking")."""

    perturbation: str
    axis: str
    question: str
    prior_direction: int = 0  # +1 / -1 literature prior, 0 = agnostic (NOT evidence)
    citations: tuple[str, ...] = ()
    validation_level: str = "simulation"  # honest label carried through the chain

    @property
    def context_id(self) -> str:
        return _stable_id("ctx", self.perturbation, self.axis, self.question)


@dataclass(frozen=True)
class AssayPredicate:
    """The falsifiable, machine-readable core of a hypothesis: a predicted sign on an
    observable, with the decisiveness bar (effect threshold + required INDEPENDENT
    biological replicates). Technical replicates never satisfy the replicate bar — that
    rule lives in the replication agent, but the *unit* is named here as 'biological'."""

    observable: str
    comparison: str  # e.g. "knockout vs wildtype"
    predicted_sign: int  # +1 predicts increase, -1 predicts decrease
    effect_threshold: float
    min_biological_replicates: int = 2


@dataclass(frozen=True)
class AnalysisPlan:
    """How a hypothesis would be tested: which observable/statistic and the thresholds a
    decisive verdict requires. The analysis backend consumes this to compute a statistic;
    it does NOT decide a claim (that is the ledger's job)."""

    observable: str
    statistic: str  # e.g. "standardized-mean-difference-z"
    decisive_abs_z: float = 2.0
    effect_threshold: float = 0.0


@dataclass(frozen=True)
class Hypothesis:
    """One directional causal hypothesis competing to explain an axis.

    ``prior_rank`` is a proposer-internal prioritisation score (see
    :mod:`hypotheses.ranking`). It is acquisition-layer side-info, NEVER evidence — the
    ledger ignores it entirely."""

    hypothesis_id: str
    perturbation: str
    axis: str
    direction: int  # +1 increases axis, -1 decreases axis
    statement: str
    assay: AssayPredicate
    plan: AnalysisPlan
    rationale: str = ""
    prior_rank: float = 0.0  # proposer prioritisation only — NOT evidence
    #: the ledger claim_id this hypothesis, IF certified, would adjudicate. Distinct per
    #: direction so two competing directions are two competing ledger claims.
    claim_id: str = ""


@dataclass(frozen=True)
class CompetingHypothesisSet:
    """>=2 mutually-exclusive hypotheses over the same (perturbation, axis) plus the
    context that spawned them. ``competing`` is guaranteed at construction to hold at
    least two hypotheses of differing direction (the discriminative minimum)."""

    context: DiscoveryContext
    hypotheses: tuple[Hypothesis, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if len(self.hypotheses) < 2:
            raise ValueError("a competing set needs >=2 hypotheses (discriminative minimum)")
        if len({h.direction for h in self.hypotheses}) < 2:
            raise ValueError("competing hypotheses must differ in direction")

    def by_direction(self, direction: int) -> Hypothesis:
        for h in self.hypotheses:
            if h.direction == direction:
                return h
        raise KeyError(f"no hypothesis with direction {direction}")

    def ranked(self) -> tuple[Hypothesis, ...]:
        """Hypotheses ordered by descending proposer prioritisation (prior_rank), ties
        broken by hypothesis_id for determinism. PRIORITISATION ONLY — not a verdict."""
        return tuple(sorted(self.hypotheses, key=lambda h: (-h.prior_rank, h.hypothesis_id)))


def make_claim_id(perturbation: str, axis: str, direction: int) -> str:
    """The stable ledger claim_id scheme for a directional biological claim. Two competing
    directions map to two distinct claim ids (each a candidate the ledger can certify or
    reject independently)."""
    sign = "increases" if direction > 0 else "decreases"
    return f"m28::{perturbation}::{axis}::{sign}"
