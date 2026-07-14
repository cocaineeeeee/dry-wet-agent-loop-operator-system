"""The four M28 discovery agent STUBS (v0.1 skeleton).

Hypothesis / Analysis / Contradiction / Replication. Deliberately deterministic pure
functions (no LLM in this pass -- the LLM proposer/critic is a later seam; see
docs/bio_seams/M28.md). They reuse the kernel claim vocabulary READ-ONLY
(``expos.kernel.claims.ClaimDecisionStatus``) so the discovery layer speaks the same
verdict language as the certification kernel WITHOUT re-implementing or specialising it.

Charter red line (M28 DoD #5): agents propose and analyse freely, but only *trusted*
evidence can yield a decisive (supported/rejected) verdict. Untrusted/insufficient
evidence collapses to INSUFFICIENT -- non-mutating.
"""

from __future__ import annotations

from dataclasses import dataclass

# READ-ONLY reuse of the kernel verdict vocabulary (no specialisation of the kernel).
from expos.kernel.claims import ClaimDecisionStatus
from agents.biology_discovery.objects import Hypothesis, Evidence

_Z_DECISIVE = 2.0  # |effect/se| threshold to call an effect real (v0.1 constant)


class HypothesisAgent:
    """Proposes >=2 COMPETING directional hypotheses for one (perturbation, axis)."""

    def propose(self, perturbation: str, axis: str) -> list[Hypothesis]:
        return [
            Hypothesis(
                hypothesis_id=f"{perturbation}:{axis}:up",
                perturbation=perturbation, axis=axis, direction=+1,
                statement=f"{perturbation} INCREASES {axis}",
            ),
            Hypothesis(
                hypothesis_id=f"{perturbation}:{axis}:down",
                perturbation=perturbation, axis=axis, direction=-1,
                statement=f"{perturbation} DECREASES {axis}",
            ),
        ]


@dataclass(frozen=True)
class AnalysisVerdict:
    hypothesis_id: str
    status: ClaimDecisionStatus
    z: float
    rationale: str


class AnalysisAgent:
    """Scores one hypothesis against its matching evidence -> a PROPOSED verdict.

    A verdict is only decisive when the evidence is trusted AND significant (|z|>=2):
    the hypothesised direction matching the observed sign -> SUPPORTED, opposing ->
    REJECTED. Untrusted or under-powered evidence -> INSUFFICIENT (non-mutating)."""

    def analyse(self, h: Hypothesis, ev: Evidence | None) -> AnalysisVerdict:
        if ev is None or ev.se <= 0:
            return AnalysisVerdict(h.hypothesis_id, ClaimDecisionStatus.INSUFFICIENT,
                                   0.0, "no usable evidence")
        z = ev.effect / ev.se
        if not ev.trusted or abs(z) < _Z_DECISIVE:
            return AnalysisVerdict(
                h.hypothesis_id, ClaimDecisionStatus.INSUFFICIENT, z,
                "untrusted" if not ev.trusted else f"under-powered |z|={abs(z):.2f}",
            )
        matches = (h.direction > 0) == (ev.effect > 0)
        status = ClaimDecisionStatus.SUPPORTED if matches else ClaimDecisionStatus.REJECTED
        return AnalysisVerdict(h.hypothesis_id, status, z,
                               f"trusted decisive |z|={abs(z):.2f}, "
                               f"{'match' if matches else 'oppose'}")


class ContradictionAgent:
    """Finds mutually exclusive hypothesis pairs (same target/axis, opposite direction)."""

    def contradictions(self, hyps: list[Hypothesis]) -> list[tuple[str, str]]:
        pairs = []
        for i, a in enumerate(hyps):
            for b in hyps[i + 1:]:
                if (a.perturbation, a.axis) == (b.perturbation, b.axis) and \
                        a.direction == -b.direction:
                    pairs.append((a.hypothesis_id, b.hypothesis_id))
        return pairs


class ReplicationAgent:
    """Requires >=``min_reps`` independent trusted, same-sign evidence points before a
    decisive verdict is allowed to stand (else demotes to INSUFFICIENT)."""

    def __init__(self, min_reps: int = 2) -> None:
        self.min_reps = min_reps

    def confirm(self, evidences: list[Evidence]) -> bool:
        trusted = [e for e in evidences if e.trusted]
        if len(trusted) < self.min_reps:
            return False
        signs = {e.effect > 0 for e in trusted}
        return len(signs) == 1  # all agree in sign
