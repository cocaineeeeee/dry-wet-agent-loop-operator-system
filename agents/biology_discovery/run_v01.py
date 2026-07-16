"""M28 v0.1 END-TO-END runnable (charter DoD #2 + #8).

Domain-local autonomous-discovery loop over the REAL kernel claim ledger:

    literature-grounded context
      -> HypothesisAgent poses >=2 COMPETING machine-readable hypotheses (+ Robin-adapted
         prioritisation that a literature prior tilts toward the WRONG direction)
      -> each hypothesis carries an assayable claim + an analysis plan
      -> AnalysisAgent turns assay datasets into EvidenceObservations (statistics only)
      -> ContradictionAgent routes the competing evidence through the ledger, where the
         kernel gate certifies one direction (SUPPORTED) and REJECTS the other, resolving
         an earlier wrong lead as a SUPERSEDE under the strength-monotonicity gate
      -> a WEAK (technical-replicate-only) counter-analysis is DENIED by the strength gate
         (weak may not retract strong) -> annotation, head untouched
      -> an untrusted analysis collapses to INSUFFICIENT (non-mutating, K3)
      -> ReplicationAgent enforces the independent-biological-replicate bar and, from the
         CHANGED knowledge, selects a DIFFERENT follow-up than before certification.

THE MOAT: every ledger mutation goes through ``ledger_bridge`` -> ``apply_claim_deltas``
(the kernel gate). Agents and the (optional) LLM only ever produce hypotheses and evidence.
Trusted observations here are RETROSPECTIVE/SIMULATION stand-ins (is_wet_observation=False),
honestly labelled; a real wet/sim-reader observation entering ``mcl`` is the seam to B
(docs/bio_seams/M28.md). NOT wet-lab validated.

Usage:  python -m agents.biology_discovery.run_v01
"""

from __future__ import annotations

import json
from dataclasses import replace

from expos.kernel.claims import Ledger

from analysis_backends.deterministic import DeterministicAnalysisBackend, make_assay_dataset
from hypotheses.objects import DiscoveryContext, make_claim_id

from agents.biology_discovery import ledger_bridge
from agents.biology_discovery.agents import (
    AnalysisAgent,
    ContradictionAgent,
    HypothesisAgent,
    ReplicationAgent,
)


def _ledger_summary(ledger: Ledger) -> dict:
    statuses = {cid: s.value for cid, s in ledger.effective_statuses().items()}
    heads = {}
    for cid in sorted({r.claim_id for r in ledger.claims}):
        h = ledger.head(cid)
        if h is not None:
            heads[cid] = {
                "version": h.version,
                "status": h.status.value,
                "band": h.evidence_strength.value,
                "supersedes": h.supersedes,
            }
    annotations = [
        {
            "claim_id": r.claim_id,
            "version": r.version,
            "status": r.status.value,
            "deny_reason": r.deny_reason,
        }
        for r in ledger.claims
        if r.is_annotation
    ]
    return {
        "effective_statuses": statuses,
        "heads": heads,
        "annotations": annotations,
        "knowledge_fingerprint": ledger_bridge.knowledge_fingerprint(ledger),
    }


def _outcomes(outcomes) -> list[dict]:
    return [
        {
            "claim_id": o.target_claim_id,
            "final_status": o.final_status.value,
            "mutated": o.mutated_effective_status,
            "deny_reason": o.deny_reason,
            "landed_version": o.landed_record_version,
        }
        for o in outcomes
    ]


def run() -> dict:
    hyp_agent = HypothesisAgent()
    ana = AnalysisAgent(DeterministicAnalysisBackend())
    contra = ContradictionAgent()
    rep = ReplicationAgent(min_reps=2)

    # --- literature-grounded context: literature PRIOR says "decrease" (it is WRONG) -----
    context = DiscoveryContext(
        perturbation="KO_GENE_A",
        axis="axis_07",
        question="Does knockout of GENE_A increase or decrease axis_07?",
        prior_direction=-1,  # literature prior favors DECREASE (prior only, not evidence)
        citations=("doi:10.0000/sim.review.2026", "doi:10.0000/sim.screen.2025"),
        validation_level="simulation",
    )
    hset = hyp_agent.pose(context)
    h_up = hset.by_direction(+1)
    h_down = hset.by_direction(-1)
    assayable = hyp_agent.assayable_claims(hset)
    contradiction_pairs = contra.find_pairs(hset)

    ledger = Ledger()
    phases: dict = {}

    # === Phase B: an early, weaker retrospective lead installs a MODERATE head on the
    # WRONG direction (decreases). This is the false lead the strong evidence will overturn.
    seed_ds = make_assay_dataset(
        "KO_GENE_A", "axis_07", true_effect=-2.6, n_biological=5, noise=3.0, seed=7
    )
    seed_obs = ana.analyse(h_down, seed_ds)  # matches H_down direction, moderate |z|
    seed_obs = replace(seed_obs, trusted=True)  # adjudicated observation (sim boundary)
    ledger, seed_out, _ = contra.adjudicate(ledger, [(h_down, seed_obs)])
    phases["B_seed_moderate_wrong_lead"] = {
        "observation": seed_obs.note,
        "outcomes": _outcomes(seed_out),
        "ledger": _ledger_summary(ledger),
    }
    ledger_before_main = ledger

    # === Phase C: strong trusted evidence — the TRUE effect is an INCREASE. The competing
    # hypotheses are adjudicated on the ledger: up -> SUPPORTED, down -> REJECTED (supersede).
    main_ds = make_assay_dataset(
        "KO_GENE_A", "axis_07", true_effect=3.1, n_biological=3, noise=0.35, seed=27
    )
    obs_up = ana.analyse(h_up, main_ds)
    obs_up = replace(obs_up, trusted=True)
    obs_down = ana.analyse(h_down, main_ds)
    obs_down = replace(obs_down, trusted=True)
    preview = {
        h_up.hypothesis_id: ana.preview(h_up, obs_up).__dict__,
        h_down.hypothesis_id: ana.preview(h_down, obs_down).__dict__,
    }
    ledger, main_out, main_deltas = contra.adjudicate(ledger, [(h_up, obs_up), (h_down, obs_down)])
    phases["C_main_strong_true_increase"] = {
        "preview_non_certifying": preview,
        "outcomes": _outcomes(main_out),
        "ledger": _ledger_summary(ledger),
    }

    # === Phase D: a spurious WEAK counter-analysis (decisive |z| but TECHNICAL replicates
    # only) tries to flip the up-claim to rejected. The strength gate DENIES it (weak may
    # not retract strong): it lands only as an annotation; the head is untouched.
    weak_ds = make_assay_dataset(
        "KO_GENE_A", "axis_07", true_effect=-3.0, n_biological=1, n_technical=3,
        noise=0.05, seed=99,
    )
    obs_weak = ana.analyse(h_up, weak_ds)
    obs_weak = replace(obs_weak, trusted=True)
    ledger, weak_out, _ = contra.adjudicate(ledger, [(h_up, obs_weak)])
    phases["D_weak_denied_by_strength_gate"] = {
        "observation": obs_weak.note,
        "replication_verdict": rep.assess(obs_weak).__dict__,
        "outcomes": _outcomes(weak_out),
        "ledger": _ledger_summary(ledger),
    }

    # === Phase E: an UNTRUSTED analysis on a new axis collapses to INSUFFICIENT (K3:
    # absence of trusted evidence is not support; non-mutating annotation only).
    ctx2 = DiscoveryContext(
        perturbation="KO_GENE_A", axis="axis_09",
        question="Does KO of GENE_A move axis_09?", prior_direction=0,
    )
    hset2 = hyp_agent.pose(ctx2)
    h2_up = hset2.by_direction(+1)
    insuff_ds = make_assay_dataset(
        "KO_GENE_A", "axis_09", true_effect=3.0, n_biological=3, noise=0.3, seed=5,
        is_wet_observation=False,
    )
    obs_insuff = ana.analyse(h2_up, insuff_ds)  # trusted stays False -> untrusted
    ledger, insuff_out, _ = contra.adjudicate(ledger, [(h2_up, obs_insuff)])
    phases["E_untrusted_insufficient"] = {
        "observation": obs_insuff.note,
        "outcomes": _outcomes(insuff_out),
        "ledger": _ledger_summary(ledger),
    }

    # === Phase F: replication + follow-up selection driven by the CHANGED knowledge.
    rep_main = rep.assess(obs_up)
    # "before knowledge" baseline = the episode's empty start ledger (no certified
    # direction) -> run_assay; "after knowledge" = the certified+replicated ledger ->
    # probe_new_axis. The changed knowledge changes the next decision (DoD #7).
    follow_pre = rep.next_follow_up(context, Ledger())
    follow_post = rep.next_follow_up(context, ledger, replication=rep_main)

    up_id = make_claim_id("KO_GENE_A", "axis_07", +1)
    down_id = make_claim_id("KO_GENE_A", "axis_07", -1)
    report = {
        "milestone": "M28 autonomous biological discovery v0.1",
        "validation_level": "simulation / retrospective (NOT wet-lab validated)",
        "moat": (
            "agents/LLM produce hypotheses + evidence ONLY; every ledger mutation goes "
            "through ledger_bridge -> apply_claim_deltas (the kernel gate). Contradiction "
            "= ledger supersede under the strength gate, never an agent's spoken verdict."
        ),
        "context": {
            "perturbation": context.perturbation,
            "axis": context.axis,
            "question": context.question,
            "literature_prior_direction": context.prior_direction,
            "citations": list(context.citations),
            "note": "prior is a belief, NOT evidence; trusted evidence overturns it below.",
        },
        "competing_hypotheses_assayable": assayable,
        "proposer_prioritised_first": hset.ranked()[0].hypothesis_id,
        "contradiction_pairs": contradiction_pairs,
        "phases": phases,
        "discriminative_separation": {
            "claim_up": ledger.effective_statuses().get(up_id).value
            if ledger.effective_statuses().get(up_id) else None,
            "claim_down": ledger.effective_statuses().get(down_id).value
            if ledger.effective_statuses().get(down_id) else None,
            "separated": (
                ledger.effective_statuses().get(up_id)
                != ledger.effective_statuses().get(down_id)
            ),
            "evidence_overturned_literature_prior": (
                context.prior_direction == -1
                and ledger.effective_statuses().get(up_id) is not None
                and ledger.effective_statuses().get(up_id).value == "supported"
            ),
        },
        "knowledge_change": {
            "before_certification": ledger_bridge.knowledge_fingerprint(ledger_before_main),
            "after_certification": ledger_bridge.knowledge_fingerprint(ledger),
            "changed": ledger_bridge.knowledge_fingerprint(ledger_before_main)
            != ledger_bridge.knowledge_fingerprint(ledger),
        },
        "replication": {
            "main_evidence": rep_main.__dict__,
            "weak_evidence": rep.assess(obs_weak).__dict__,
        },
        "follow_up_selection": {
            "pre_knowledge": follow_pre.__dict__,
            "post_knowledge": follow_post.__dict__,
            "changed": follow_pre.action != follow_post.action,
        },
    }
    return report


def main() -> dict:
    report = run()
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
