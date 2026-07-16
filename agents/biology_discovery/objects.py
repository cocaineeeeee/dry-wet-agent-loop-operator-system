"""Agent-local orchestration objects for the M28 discovery organ (v0.1).

The load-bearing typed objects live in the leaf packages:
  * a hypothesis / assay-predicate / analysis-plan  -> :mod:`hypotheses.objects`;
  * an evidence observation / assay dataset          -> :mod:`analysis_backends.objects`;
  * a claim delta / ledger record                    -> ``expos.kernel.claims`` (kernel).

This module only adds the small orchestration records the four agents pass among
themselves: an analysis verdict PREVIEW (what the ledger is expected to decide, for
reporting — NOT the certification itself), a replication assessment, and a follow-up
decision. None of these certify anything; certification is the ledger's job alone.
"""

from __future__ import annotations

from dataclasses import dataclass

# Re-export the shared objects so ``agents.biology_discovery`` remains a convenient
# single import surface for the organ (they are DEFINED in the leaf packages).
from hypotheses.objects import (  # noqa: F401
    AnalysisPlan,
    AssayPredicate,
    CompetingHypothesisSet,
    DiscoveryContext,
    Hypothesis,
)
from analysis_backends.objects import (  # noqa: F401
    AssayDataset,
    EvidenceObservation,
    ReplicateMeasurement,
)


@dataclass(frozen=True)
class AnalysisVerdictPreview:
    """A NON-CERTIFYING preview of what the ledger is expected to decide for one
    (hypothesis, evidence) pair, used only for the machine report. The authoritative
    verdict is whatever ``apply_claim_deltas`` lands — this is a convenience echo, never a
    substitute for the ledger."""

    hypothesis_id: str
    claim_id: str
    expected_status: str  # supported | rejected | insufficient (preview only)
    z: float
    band: str
    rationale: str


@dataclass(frozen=True)
class ReplicationVerdict:
    """Whether an observation rests on enough INDEPENDENT biological replication to let a
    decisive verdict stand. Technical replicates are counted but never satisfy the bar."""

    observation_id: str
    n_biological: int
    n_technical: int
    independent: bool
    reason: str


@dataclass(frozen=True)
class FollowUp:
    """The next action the discovery loop should take, chosen from the CHANGED knowledge
    (closes the loop: knowledge update -> different next decision). ``driven_by`` records
    the knowledge fingerprint the decision was made against (auditable)."""

    action: str
    target: str
    reason: str
    driven_by: str
