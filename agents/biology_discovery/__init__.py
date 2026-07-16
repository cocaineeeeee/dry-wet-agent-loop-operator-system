"""M28 biology_discovery — autonomous biological discovery organ (v0.1 complete).

Four deepened agents (Hypothesis / Analysis / Contradiction / Replication) run a domain-
local discovery loop over the REAL kernel claim ledger:

    literature-grounded context -> >=2 COMPETING machine-readable hypotheses (+ assayable
    claim + analysis plan) -> analysis produces EVIDENCE only -> the ledger certifies one
    direction SUPPORTED and REJECTS the other (a contradiction resolved as a SUPERSEDE
    under the strength-monotonicity gate) -> weak/technical-only counter-evidence is
    DENIED by the gate -> untrusted evidence collapses to INSUFFICIENT -> the CHANGED
    knowledge selects a different follow-up.

THE MOAT (docs/bio_refs/04 §5): agents and the (optional) LLM produce hypotheses and
evidence ONLY; every ledger mutation goes through ``ledger_bridge`` -> the kernel gate
``apply_claim_deltas``. No agent writes a narrative claim. The kernel stays biology-blind.

Run the e2e:  ``python -m agents.biology_discovery``  (== ``.run_v01``)
"""

from agents.biology_discovery.agents import (
    AnalysisAgent,
    ContradictionAgent,
    HypothesisAgent,
    ReplicationAgent,
)
from agents.biology_discovery.objects import (
    AnalysisVerdictPreview,
    AssayDataset,
    CompetingHypothesisSet,
    DiscoveryContext,
    EvidenceObservation,
    FollowUp,
    Hypothesis,
    ReplicationVerdict,
)
from agents.biology_discovery import ledger_bridge

__all__ = [
    "HypothesisAgent",
    "AnalysisAgent",
    "ContradictionAgent",
    "ReplicationAgent",
    "Hypothesis",
    "DiscoveryContext",
    "CompetingHypothesisSet",
    "EvidenceObservation",
    "AssayDataset",
    "AnalysisVerdictPreview",
    "ReplicationVerdict",
    "FollowUp",
    "ledger_bridge",
]
