"""M28 biology_discovery -- v0.1 SKELETON minimal multi-agent discovery organ.

Four deterministic agent stubs (Hypothesis / Analysis / Contradiction / Replication)
that propose >=2 competing hypotheses, analyse them against evidence, detect
contradictions, and gate decisive verdicts on replication. Agents propose+analyse
freely; only trusted evidence yields a decisive supported/rejected verdict (charter red
line). Reuses ``expos.kernel.claims`` verdict vocabulary READ-ONLY.

Run the smoke:  ``python -m agents.biology_discovery``
"""

from agents.biology_discovery.objects import Hypothesis, Evidence
from agents.biology_discovery.agents import (
    HypothesisAgent,
    AnalysisAgent,
    ContradictionAgent,
    ReplicationAgent,
    AnalysisVerdict,
)

__all__ = [
    "Hypothesis", "Evidence", "HypothesisAgent", "AnalysisAgent",
    "ContradictionAgent", "ReplicationAgent", "AnalysisVerdict",
]
