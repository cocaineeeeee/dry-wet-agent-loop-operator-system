"""Domain-local smoke for M28 biology_discovery: >=2 competing hypotheses -> separation.

Deterministic. The HypothesisAgent proposes two contradictory hypotheses (KO increases
vs decreases an axis); the ContradictionAgent confirms they are mutually exclusive; a set
of trusted, replicated, same-sign evidence points show a decisive INCREASE; the
AnalysisAgent then separates them into SUPPORTED (up) vs REJECTED (down). An UNTRUSTED
variant of the same evidence collapses both to INSUFFICIENT (non-mutating), demonstrating
the "only trusted evidence is decisive" red line. Reuses claim vocabulary read-only.
"""

from __future__ import annotations

from expos.kernel.claims import ClaimDecisionStatus
from agents.biology_discovery.objects import Evidence
from agents.biology_discovery.agents import (
    HypothesisAgent, AnalysisAgent, ContradictionAgent, ReplicationAgent,
)


def smoke() -> int:
    hyp_agent = HypothesisAgent()
    ana = AnalysisAgent()
    contra = ContradictionAgent()
    rep = ReplicationAgent(min_reps=2)

    pert, axis = "KO_GENE_A", "axis_07"
    hyps = hyp_agent.propose(pert, axis)
    assert len(hyps) >= 2, "need >=2 competing hypotheses"

    pairs = contra.contradictions(hyps)
    assert pairs, "contradiction not detected between competing hypotheses"

    # Trusted, replicated evidence: consistent INCREASE (effect > 0, |z| >> 2).
    trusted_ev = [
        Evidence(pert, axis, effect=3.1, se=0.5, trusted=True),
        Evidence(pert, axis, effect=2.8, se=0.5, trusted=True),
    ]
    assert rep.confirm(trusted_ev), "replication should confirm consistent trusted evidence"
    agg = Evidence(pert, axis, effect=2.95, se=0.35, trusted=True)  # pooled

    verdicts = {h.hypothesis_id: ana.analyse(h, agg) for h in hyps}
    up = verdicts[f"{pert}:{axis}:up"]
    down = verdicts[f"{pert}:{axis}:down"]
    assert up.status is ClaimDecisionStatus.SUPPORTED, up
    assert down.status is ClaimDecisionStatus.REJECTED, down
    assert up.status != down.status, "hypotheses not separated"

    # Untrusted variant -> both non-decisive (INSUFFICIENT).
    untrusted = Evidence(pert, axis, effect=2.95, se=0.35, trusted=False)
    for h in hyps:
        assert ana.analyse(h, untrusted).status is ClaimDecisionStatus.INSUFFICIENT

    print(f"[M28 smoke] proposed {len(hyps)} competing hypotheses; {len(pairs)} contradiction pair(s)")
    print(f"[M28 smoke] trusted+replicated evidence -> "
          f"up={up.status.value} ({up.rationale}) | down={down.status.value} ({down.rationale})")
    print("[M28 smoke] untrusted same evidence -> both INSUFFICIENT (non-mutating)")
    print("[M28 smoke] PASS (competing hypotheses separated into supported/rejected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(smoke())
