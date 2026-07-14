"""M27 perturbation-biology / virtual-cell v0.1 discriminative suite.

The point is NOT a demo but the discriminative proofs the charter demands (honesty first,
this group is the easiest to over-hype):
  1. three backends fit + predict a response DISTRIBUTION (mean + std);
  2. the BASELINE-GATE is a real gate: it ADMITS the complex backend on an informative
     regime AND emits a first-class NEGATIVE claim on a scrambled regime (it is NOT an
     always-pass rubber stamp, and NOT an always-negative one either);
  3. OOD abstention is a first-class, rewarded action;
  4. a trusted (retrospective, non-wet) observation certifies a causal claim; a model
     prediction can only be recorded as a non-certifying proposal;
  5. retrospective replay is provenance-marked NON-wet (charter §4 iron rule);
  6. the domain provider passes birth-time governance;
  7. the linear baseline reproduces the reference solve_y_axb maths.
"""

from __future__ import annotations

import numpy as np
import pytest

from datasets.replay.synthetic_perturbseq import make_replay_dataset
from domains.perturbation.causal import (
    certify_causal_claims,
    update_claim_with_observation,
)
from domains.perturbation.competition import baseline_gate, score_backend
from domains.perturbation.objects import (
    DatasetProvenance,
    PerturbationCausalClaim,
)
from domains.perturbation.provider import PerturbationScreenProvider
from domains.perturbation.run_v01 import run_regime
from expos.adapters.domain_provider import DomainProvider
from expos.adapters.models import (
    KNNResponseBackend,
    LinearResponseBackend,
    MeanBaselineBackend,
    solve_y_axb,
)
from expos.adapters.models.virtual_cell import BioModelBackend, PerturbationBatch


# --------------------------------------------------------------- backends & distribution


def _fit_all(regime="informative"):
    ds = make_replay_dataset(seed=27, n_pert=60, regime=regime, n_ood=6)
    batch = ds.subset(
        [p.pert_id for p in ds.perturbations if not p.pert_id.startswith("OOD_")]
    ).to_batch()
    backends = [
        MeanBaselineBackend().fit(batch),
        LinearResponseBackend().fit(batch),
        KNNResponseBackend().fit(batch),
    ]
    return ds, batch, backends


def test_three_backends_predict_a_distribution():
    ds, batch, backends = _fit_all()
    assert [b.name for b in backends] == ["mean_baseline", "linear_response", "knn_response"]
    for b in backends:
        assert isinstance(b, BioModelBackend)
        preds = b.predict(batch)
        assert len(preds) == batch.n
        p0 = preds[0]
        assert p0.mean.shape == (ds.dim,) and p0.std.shape == (ds.dim,)
        assert np.all(p0.std > 0.0)  # a genuine distribution width, not a point


def test_backend_fingerprint_covers_weights():
    _, batch, backends = _fit_all()
    fps = {b.name: b.fingerprint() for b in backends}
    assert len(set(fps.values())) == 3  # distinct backends -> distinct fingerprints
    # re-fitting the same backend on different data flips the weight hash.
    other = make_replay_dataset(seed=99, n_pert=60, regime="informative", n_ood=6)
    ob = other.subset(
        [p.pert_id for p in other.perturbations if not p.pert_id.startswith("OOD_")]
    ).to_batch()
    assert LinearResponseBackend().fit(ob).fingerprint() != fps["linear_response"]


def test_baselines_are_flagged_and_complex_is_not():
    _, _, backends = _fit_all()
    flags = {b.name: b.is_baseline for b in backends}
    assert flags == {"mean_baseline": True, "linear_response": True, "knn_response": False}


# --------------------------------------------------------------- the baseline-gate (core)


def test_baseline_gate_is_discriminative_admit_and_negative():
    """The gate ADMITS the complex backend on the informative regime and emits a
    first-class NEGATIVE claim on the scrambled regime -- proving it is a real gate."""
    info = run_regime("informative")
    scr = run_regime("scrambled")

    info_gate = info["baseline_gate"][0]
    assert info_gate["backend"] == "knn_response"
    assert info_gate["admitted"] is True
    assert info_gate["negative_claim"] is None
    assert info_gate["l2_improvement"] > 0.0 and info_gate["ci_low"] > 0.0

    scr_gate = scr["baseline_gate"][0]
    assert scr_gate["admitted"] is False
    nc = scr_gate["negative_claim"]
    assert nc is not None
    assert nc["status"] == "rejected"
    assert nc["kind"] == "baseline_gate_negative"
    assert "did not significantly" in nc["statement"]


def test_gate_negative_when_complex_worse_than_baseline():
    """Direct unit: a complex backend whose held-out L2 is worse than the baselines is
    NOT admitted and yields a negative claim (charter: negative is first-class)."""
    _, batch, backends = _fit_all("scrambled")
    # Build a held-out scrambled batch (models cannot beat mean here).
    ds = make_replay_dataset(seed=27, n_pert=60, regime="scrambled", n_ood=0)
    held = ds.to_batch()
    scores = {b.name: score_backend(b, held) for b in backends}
    verdicts = baseline_gate(scores)
    knn = [v for v in verdicts if v.backend == "knn_response"][0]
    assert knn.admitted is False
    assert knn.negative_claim is not None


# --------------------------------------------------------------- OOD abstention


def test_ood_abstention_is_first_class_and_rewarded():
    info = run_regime("informative")
    # ood_score = ood_abstain_rate - id_abstain_rate; honest abstention on OOD > in-dist.
    for name, b in info["backends"].items():
        assert b["ood_score"] > 0.0, name


def test_backend_abstains_on_far_ood_point():
    _, batch, backends = _fit_all()
    knn = backends[2]
    far = batch.embeddings.mean(axis=0) + 50.0  # far outside the training support
    q = PerturbationBatch(pert_ids=("q",), embeddings=far[None, :])
    pred = knn.predict(q)[0]
    assert pred.abstained is True
    assert pred.in_distribution < 0.5


# --------------------------------------------------------------- causal claims


def test_trusted_observation_certifies_causal_claim():
    ds = make_replay_dataset(seed=27, n_pert=30, regime="informative", n_ood=0)
    backends_preds = None
    claims = certify_causal_claims(ds, effect_threshold=6.0)
    assert claims and all(isinstance(c, PerturbationCausalClaim) for c in claims)
    supported = [c for c in claims if c.status == "supported"]
    assert supported, "expected some strong hits to certify"
    c = supported[0]
    assert c.direction in ("up", "down")
    # evidence records the NON-wet provenance (never mistaken for wet certification).
    assert "is_wet_observation=False" in c.evidence[0]


def test_model_proposal_never_certifies():
    """A model prediction is recorded only as a non-certifying proposal; only a trusted
    observation may set status (charter §4)."""
    ds = make_replay_dataset(seed=27, n_pert=30, regime="informative", n_ood=0)
    batch = ds.to_batch()
    preds = LinearResponseBackend().fit(batch).predict(batch)
    claims = certify_causal_claims(ds, effect_threshold=6.0, proposals={"linear": preds})
    with_prop = [c for c in claims if c.proposals]
    assert with_prop, "proposals should be attached as dry evidence"
    # A claim's status is driven by the observation, and proposals are a SEPARATE field.
    c = with_prop[0]
    assert c.status in ("supported", "insufficient")
    assert any("linear predicted" in p for p in c.proposals)


def test_causal_claim_lifecycle_update():
    claim = PerturbationCausalClaim(claim_id="x", pert_id="KO_000", axis="axis_00")
    assert claim.status == "insufficient"
    update_claim_with_observation(claim, 9.0, n_replicates=3, effect_threshold=6.0)
    assert claim.status == "supported" and claim.direction == "up"
    # a weak/underpowered observation reverts it to insufficient.
    update_claim_with_observation(claim, 0.5, n_replicates=3, effect_threshold=6.0)
    assert claim.status == "insufficient" and claim.direction == "none"


# --------------------------------------------------------------- replay provenance


def test_replay_is_marked_non_wet():
    ds = make_replay_dataset(seed=27, n_pert=20, regime="informative")
    assert ds.provenance.is_wet_observation is False
    assert ds.provenance.validation_level == "retrospective"
    assert ds.provenance.role == "benchmark_calibration"
    assert "SYNTHETIC" in ds.provenance.scope


def test_provenance_refuses_wet_observation_flag():
    with pytest.raises(ValueError):
        DatasetProvenance(source="x", scope="y", is_wet_observation=True)


def test_dataset_fingerprint_folds_provenance_and_scope():
    a = make_replay_dataset(seed=27, regime="informative")
    b = make_replay_dataset(seed=27, regime="scrambled")
    # same seed, different embedding regime (=> different scope + data) -> different fp.
    assert a.fingerprint() != b.fingerprint()
    assert a.provenance.fingerprint() != b.provenance.fingerprint()


# --------------------------------------------------------------- provider governance


def test_provider_birth_time_governance():
    prov = PerturbationScreenProvider.check_complete()  # raises on any inconsistency
    assert isinstance(prov, DomainProvider)
    assert prov.domain_name == "perturbation_screen"
    assert set(prov.compute_targets()) == set(prov.wet_coords())
    assert "flat" in prov.truth_profiles()
    assert prov.null_profiles() == frozenset({"flat"})
    # compute_targets carry the perturbation input_kind (no fabricated geometry).
    for t in prov.compute_targets().values():
        assert t.input_kind == "cell_state_perturbation"


def test_provider_fingerprint_is_source_bound():
    fp = PerturbationScreenProvider().provider_fingerprint()
    assert fp.startswith("domains.perturbation.provider:PerturbationScreenProvider@sha256:")


# --------------------------------------------------------------- solve_y_axb reference


def test_solve_y_axb_recovers_a_linear_map():
    """The ported solve_y_axb (A=None branch) recovers a known linear response map on
    clean data (faithful to the reference implementation)."""
    rng = np.random.default_rng(0)
    d, G, m = 5, 12, 200
    B = rng.normal(size=(d, m))  # pert embedding (features x conditions)
    B = B - B.mean(axis=1, keepdims=True)  # column-mean-centered (solve_y_axb centers Y)
    Ktrue = rng.normal(size=(G, d))
    center = rng.normal(size=G)
    Y = center[:, None] + Ktrue @ B  # (G, m), noiseless
    sol = solve_y_axb(Y, A=None, B=B, B_ridge=1e-6)
    assert np.allclose(sol["center"], Y.mean(axis=1))
    # prediction on a fresh condition matches truth.
    b_new = rng.normal(size=d)
    pred = sol["center"] + sol["K"] @ b_new
    assert np.allclose(pred, center + Ktrue @ b_new, atol=1e-2)


def test_selection_changes_with_knowledge():
    """DoD #7: changed (certified) knowledge alters the next active-selection decision."""
    for regime in ("informative", "scrambled"):
        rep = run_regime(regime)
        assert rep["selection_changed"] is True, regime
