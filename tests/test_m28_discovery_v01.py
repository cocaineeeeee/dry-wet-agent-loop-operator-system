"""M28 autonomous biological discovery v0.1 — acceptance tests.

Covers the charter's discriminative contract:
  * >=2 COMPETING machine-readable hypotheses;
  * agents produce EVIDENCE ONLY and never mutate the ledger (the moat);
  * a contradiction is resolved as a ledger SUPERSEDE under the strength gate, and weak
    (technical-only) evidence CANNOT retract strong;
  * supported + rejected/insufficient three-way separation via the REAL kernel ledger;
  * determinism (same episode -> bit-for-bit identical ledger).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from expos.kernel.claims import (
    ClaimDecisionStatus,
    ClaimDelta,
    EvidenceStrength,
    Ledger,
)

import analysis_backends
import hypotheses
from analysis_backends.deterministic import DeterministicAnalysisBackend, make_assay_dataset
from analysis_backends.objects import EvidenceObservation
from hypotheses.objects import DiscoveryContext, make_claim_id
from agents.biology_discovery import ledger_bridge
from agents.biology_discovery.agents import (
    AnalysisAgent,
    ContradictionAgent,
    HypothesisAgent,
    ReplicationAgent,
)
from agents.biology_discovery.run_v01 import run


# ----------------------------------------------------------------- fixtures


def _context() -> DiscoveryContext:
    return DiscoveryContext(
        perturbation="KO_TEST",
        axis="axis_x",
        question="Does KO_TEST move axis_x?",
        prior_direction=-1,  # wrong prior
    )


def _trusted_increase_obs(hyp) -> EvidenceObservation:
    from dataclasses import replace

    ds = make_assay_dataset("KO_TEST", "axis_x", true_effect=3.0, n_biological=3, seed=1)
    obs = DeterministicAnalysisBackend().analyse(hyp, ds)
    return replace(obs, trusted=True)


# ----------------------------------------------------------------- >=2 competing


def test_at_least_two_competing_machine_readable_hypotheses():
    hset = HypothesisAgent().pose(_context())
    assert len(hset.hypotheses) >= 2
    directions = {h.direction for h in hset.hypotheses}
    assert directions == {+1, -1}, "must be mutually-exclusive directions"
    # machine-readable assay + distinct ledger claim ids per direction
    claim_ids = {h.claim_id for h in hset.hypotheses}
    assert len(claim_ids) == 2
    for h in hset.hypotheses:
        assert h.assay.predicted_sign == h.direction
        assert h.assay.min_biological_replicates >= 2
    pairs = ContradictionAgent.find_pairs(hset)
    assert len(pairs) == 1, "the two directions must be flagged as contradictory"


# ----------------------------------------------------------------- moat: agents don't mutate


def test_leaf_packages_cannot_import_the_kernel_ledger():
    """Structural moat: the hypotheses + analysis_backends packages must not IMPORT the
    claim ledger at all — they are incapable of certifying by construction. (AST-based so a
    docstring mentioning the kernel in prose is not a false positive.)"""
    import ast

    for pkg in (analysis_backends, hypotheses):
        pkg_dir = Path(pkg.__file__).parent
        for src_file in pkg_dir.glob("*.py"):
            tree = ast.parse(src_file.read_text())
            for node in ast.walk(tree):
                mods: list[str] = []
                if isinstance(node, ast.Import):
                    mods = [n.name for n in node.names]
                elif isinstance(node, ast.ImportFrom):
                    mods = [node.module or ""]
                for m in mods:
                    assert not m.startswith("expos.kernel"), (
                        f"{src_file} imports {m} — moat violation (analysis/hypothesis "
                        "code must produce proposals/evidence only, never touch the kernel)"
                    )


def test_analysis_agent_produces_evidence_not_claims_and_does_not_mutate():
    hset = HypothesisAgent().pose(_context())
    h_up = hset.by_direction(+1)
    ana = AnalysisAgent()
    ledger = Ledger()
    before = ledger.canonical_json()
    obs = ana.analyse(h_up, make_assay_dataset("KO_TEST", "axis_x", true_effect=3.0, seed=1))
    # analysis output is evidence, never a claim/delta/ledger
    assert isinstance(obs, EvidenceObservation)
    assert not isinstance(obs, (ClaimDelta, Ledger))
    # analysing does not touch the ledger
    assert ledger.canonical_json() == before


def test_build_delta_is_a_proposal_that_mutates_nothing():
    hset = HypothesisAgent().pose(_context())
    h_up = hset.by_direction(+1)
    obs = _trusted_increase_obs(h_up)
    ledger = Ledger()
    before = ledger.canonical_json()
    delta = ledger_bridge.build_delta(h_up, obs, ledger)
    assert isinstance(delta, ClaimDelta)
    # building a delta is a PROPOSAL; it does not mutate the ledger
    assert ledger.canonical_json() == before
    # only apply (via certify_round) mutates
    new_ledger, _, _ = ledger_bridge.certify_round(ledger, [(h_up, obs)])
    assert new_ledger.canonical_json() != before


# ----------------------------------------------------------------- supersede + strength gate


def test_contradiction_resolved_as_supersede_and_weak_cannot_retract_strong():
    from dataclasses import replace

    ctx = _context()
    hset = HypothesisAgent().pose(ctx)
    h_up, h_down = hset.by_direction(+1), hset.by_direction(-1)
    contra = ContradictionAgent()
    ledger = Ledger()

    # seed a MODERATE wrong-direction head (decreases, supported)
    seed_ds = make_assay_dataset("KO_TEST", "axis_x", true_effect=-2.6, n_biological=5,
                                 noise=3.0, seed=7)
    seed_obs = replace(DeterministicAnalysisBackend().analyse(h_down, seed_ds), trusted=True)
    ledger, _, _ = contra.adjudicate(ledger, [(h_down, seed_obs)])
    down_id = make_claim_id("KO_TEST", "axis_x", -1)
    assert ledger.effective_statuses()[down_id] is ClaimDecisionStatus.SUPPORTED
    assert ledger.head(down_id).evidence_strength is EvidenceStrength.MODERATE

    # strong true-increase evidence: up supported, down rejected via SUPERSEDE
    obs_up = _trusted_increase_obs(h_up)
    obs_down = _trusted_increase_obs(h_down)
    ledger, _, _ = contra.adjudicate(ledger, [(h_up, obs_up), (h_down, obs_down)])
    up_id = make_claim_id("KO_TEST", "axis_x", +1)
    assert ledger.effective_statuses()[up_id] is ClaimDecisionStatus.SUPPORTED
    assert ledger.effective_statuses()[down_id] is ClaimDecisionStatus.REJECTED
    down_head = ledger.head(down_id)
    assert down_head.supersedes == 1, "rejected head must supersede the moderate head (bidir chain)"
    assert ledger.superseded_by(down_id, 1) == down_head.version

    # weak (technical-only) counter-evidence CANNOT retract the strong up head
    weak_ds = make_assay_dataset("KO_TEST", "axis_x", true_effect=-3.0, n_biological=1,
                                 n_technical=3, noise=0.05, seed=99)
    obs_weak = replace(DeterministicAnalysisBackend().analyse(h_up, weak_ds), trusted=True)
    ledger, outcomes, _ = contra.adjudicate(ledger, [(h_up, obs_weak)])
    weak_outcome = outcomes[0]
    assert weak_outcome.deny_reason == "weak_cannot_retract_strong"
    assert weak_outcome.mutated_effective_status is False
    # the up head is untouched: still SUPPORTED
    assert ledger.effective_statuses()[up_id] is ClaimDecisionStatus.SUPPORTED


# ----------------------------------------------------------------- three-way separation


def test_supported_rejected_insufficient_separation():
    from dataclasses import replace

    ctx = _context()
    hset = HypothesisAgent().pose(ctx)
    h_up, h_down = hset.by_direction(+1), hset.by_direction(-1)
    contra = ContradictionAgent()
    ledger = Ledger()

    obs_up = _trusted_increase_obs(h_up)
    obs_down = _trusted_increase_obs(h_down)
    ledger, _, _ = contra.adjudicate(ledger, [(h_up, obs_up), (h_down, obs_down)])

    # an UNTRUSTED observation -> insufficient, non-mutating (K3)
    untrusted_ds = make_assay_dataset("KO_TEST", "axis_y", true_effect=3.0, seed=2)
    from hypotheses.objects import Hypothesis, make_claim_id as mk

    h_y = replace(h_up, axis="axis_y", claim_id=mk("KO_TEST", "axis_y", +1))
    obs_unt = DeterministicAnalysisBackend().analyse(h_y, untrusted_ds)  # trusted stays False
    assert isinstance(h_y, Hypothesis)
    ledger, outcomes, _ = contra.adjudicate(ledger, [(h_y, obs_unt)])

    statuses = ledger.effective_statuses()
    up_id = make_claim_id("KO_TEST", "axis_x", +1)
    down_id = make_claim_id("KO_TEST", "axis_x", -1)
    y_id = mk("KO_TEST", "axis_y", +1)
    assert statuses[up_id] is ClaimDecisionStatus.SUPPORTED
    assert statuses[down_id] is ClaimDecisionStatus.REJECTED
    # insufficient never becomes a head -> absent from effective statuses
    assert y_id not in statuses
    assert outcomes[0].final_status is ClaimDecisionStatus.INSUFFICIENT
    assert outcomes[0].mutated_effective_status is False


# ----------------------------------------------------------------- determinism + e2e


def test_run_is_deterministic_and_discriminative():
    r1 = run()
    r2 = run()
    # bit-for-bit identical knowledge fingerprints across independent runs
    assert r1["knowledge_change"] == r2["knowledge_change"]
    sep = r1["discriminative_separation"]
    assert sep["claim_up"] == "supported"
    assert sep["claim_down"] == "rejected"
    assert sep["separated"] is True
    assert sep["evidence_overturned_literature_prior"] is True
    # changed knowledge changes the follow-up action
    assert r1["follow_up_selection"]["changed"] is True


def test_replication_rejects_technical_masquerade():
    from dataclasses import replace

    hset = HypothesisAgent().pose(_context())
    h_up = hset.by_direction(+1)
    rep = ReplicationAgent(min_reps=2)
    # 3 independent biological replicates -> independent
    good = _trusted_increase_obs(h_up)
    assert rep.assess(good).independent is True
    # 1 biological + 3 technical -> NOT independent (technical never masquerades)
    tech_ds = make_assay_dataset("KO_TEST", "axis_x", true_effect=3.0, n_biological=1,
                                 n_technical=3, seed=3)
    tech = replace(DeterministicAnalysisBackend().analyse(h_up, tech_ds), trusted=True)
    v = rep.assess(tech)
    assert v.independent is False
    assert v.n_technical == 3


# --------------------------------------------------- mcl bridge seam (DiscoveryCertification)
# The M28 -> mcl entry point: a CertificationPolicy-conforming adapter B drops into the
# existing ``mcl._certify_round`` (which calls ``.decide(...) -> (deltas, state)`` then owns
# the single ``apply_claim_deltas`` mutation). These tests exercise the bridge interface B
# connects to, without importing mcl (structural conformance + the kernel apply path).


from types import SimpleNamespace

from expos.kernel.claims import apply_claim_deltas
from expos.kernel.objects import TrustLevel
from expos.planner.certification import CertificationPolicy
from agents.biology_discovery.certification import DiscoveryCertification, DiscoveryVerdict


def _fake_adjudicated(obs_id: str, trust: TrustLevel):
    """A minimal ObservationObject stand-in carrying only what the trust cross-check reads
    (``obs_id`` + ``trust``) — the adapter uses getattr, so no full kernel object needed."""
    return SimpleNamespace(obs_id=obs_id, trust=trust)


def _staged_three(ctx=None):
    """Stage a competing pair (up/down, both trusted, true increase) + an untrusted verdict
    on a second axis — the three-way discriminative fixture, shared by the bridge tests."""
    from dataclasses import replace

    ctx = ctx or _context()
    hset = HypothesisAgent().pose(ctx)
    h_up, h_down = hset.by_direction(+1), hset.by_direction(-1)
    obs_up = _trusted_increase_obs(h_up)
    obs_down = _trusted_increase_obs(h_down)

    h_y = replace(h_up, axis="axis_y", claim_id=make_claim_id("KO_TEST", "axis_y", +1))
    obs_unt = DeterministicAnalysisBackend().analyse(
        h_y, make_assay_dataset("KO_TEST", "axis_y", true_effect=3.0, seed=2)
    )  # trusted stays False
    verdicts = [
        DiscoveryVerdict(h_up, obs_up),
        DiscoveryVerdict(h_down, obs_down),
        DiscoveryVerdict(h_y, obs_unt),
    ]
    return h_up, h_down, h_y, verdicts


def test_discovery_certification_conforms_to_certification_policy_protocol():
    cert = DiscoveryCertification([])
    # runtime_checkable structural conformance to the seventh planner-injection element
    assert isinstance(cert, CertificationPolicy)
    assert cert.name == "discovery_certification"
    assert callable(cert.decide)


def test_discovery_certification_decide_returns_deltas_and_mutates_nothing():
    _, _, _, verdicts = _staged_three()
    cert = DiscoveryCertification(verdicts)
    ledger = Ledger()
    before = ledger.canonical_json()
    deltas, state = cert.decide([], ledger, None, 0, "sha256:kfp")
    # decide is the PROPOSAL half: it returns ClaimDeltas + passes state through, mutating
    # nothing (mcl._certify_round owns the apply — the kernel gate stays the sole mutator).
    assert all(isinstance(d, ClaimDelta) for d in deltas)
    assert len(deltas) == 3
    assert state is None
    assert ledger.canonical_json() == before


def test_discovery_certification_bridge_three_state_separation_via_kernel_gate():
    """>=2 competing hypotheses adjudicated through the bridge -> real kernel apply:
    up SUPPORTED, down REJECTED, untrusted INSUFFICIENT (non-mutating)."""
    _, _, _, verdicts = _staged_three()
    cert = DiscoveryCertification(verdicts)
    deltas, _ = cert.decide([], Ledger(), None, 0, "sha256:kfp")
    # land them exactly as mcl._certify_round would
    ledger, report = apply_claim_deltas(Ledger(), deltas)

    statuses = ledger.effective_statuses()
    up_id = make_claim_id("KO_TEST", "axis_x", +1)
    down_id = make_claim_id("KO_TEST", "axis_x", -1)
    y_id = make_claim_id("KO_TEST", "axis_y", +1)
    assert statuses[up_id] is ClaimDecisionStatus.SUPPORTED
    assert statuses[down_id] is ClaimDecisionStatus.REJECTED
    assert y_id not in statuses  # insufficient never becomes a head (K3)
    y_outcome = next(o for o in report.outcomes if o.target_claim_id == y_id)
    assert y_outcome.final_status is ClaimDecisionStatus.INSUFFICIENT
    assert y_outcome.mutated_effective_status is False


def test_discovery_certification_threads_run_knowledge_fingerprint():
    """Seam #6: the delta provenance chains to the RUN's compiled-knowledge fingerprint that
    mcl passes into decide(), not the domain-local projection (K4 chain closes on B's ledger)."""
    _, _, _, verdicts = _staged_three()
    cert = DiscoveryCertification(verdicts)
    run_kfp = "sha256:run-compiled-knowledge-fingerprint"
    deltas, _ = cert.decide([], Ledger(), None, 3, run_kfp)
    for d in deltas:
        assert d.provenance.usage.consumed_knowledge_fingerprint == run_kfp


def test_discovery_certification_trust_gate_downgrades_qc_rejected_observation():
    """The certified stream can only REMOVE trust: an observation the QC stream saw and did
    NOT mark TRUSTED is forced untrusted -> INSUFFICIENT; a TRUSTED one stays SUPPORTED."""
    h_up, _, _, verdicts = _staged_three()
    up_verdict = verdicts[0]  # trusted, true increase
    join_id = up_verdict.observation.observation_id

    # (a) QC marked it SUSPECT -> the gate downgrades -> INSUFFICIENT (non-mutating)
    cert = DiscoveryCertification([up_verdict])
    deltas, _ = cert.decide(
        [_fake_adjudicated(join_id, TrustLevel.SUSPECT)], Ledger(), None, 0, "sha256:k"
    )
    assert deltas[0].status is ClaimDecisionStatus.INSUFFICIENT

    # (b) QC marked it TRUSTED -> stands -> SUPPORTED
    deltas_ok, _ = cert.decide(
        [_fake_adjudicated(join_id, TrustLevel.TRUSTED)], Ledger(), None, 0, "sha256:k"
    )
    assert deltas_ok[0].status is ClaimDecisionStatus.SUPPORTED

    # (c) absent from the stream -> honor B's flag (seam #1) -> SUPPORTED
    deltas_absent, _ = cert.decide([], Ledger(), None, 0, "sha256:k")
    assert deltas_absent[0].status is ClaimDecisionStatus.SUPPORTED


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
