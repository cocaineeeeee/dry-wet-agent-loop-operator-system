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

from datasets.replay.real_perturbseq_benchmark import (
    RealBenchmarkTable,
    real_baseline_gate,
)
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
from domains.perturbation.run_v01 import real_benchmark_crosscheck, run_regime
from expos.adapters.domain_provider import (
    INPUT_KIND_CELL_STATE_PERTURBATION,
    DomainProvider,
)
from expos.adapters.models import (
    CellStatePerturbationAdapter,
    EnsembleBackend,
    KNNResponseBackend,
    LinearResponseBackend,
    MeanBaselineBackend,
    PathwayInformedBackend,
    solve_y_axb,
)
from expos.adapters.models.virtual_cell import BioModelBackend, PerturbationBatch
from expos.domain import load_domain


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


# --------------------------------------------------------------- five-backend grid


def _fit_five(regime="informative"):
    ds = make_replay_dataset(seed=27, n_pert=60, regime=regime, n_ood=6)
    batch = ds.subset(
        [p.pert_id for p in ds.perturbations if not p.pert_id.startswith("OOD_")]
    ).to_batch()
    backends = [
        MeanBaselineBackend().fit(batch),
        LinearResponseBackend().fit(batch),
        KNNResponseBackend().fit(batch),
        PathwayInformedBackend().fit(batch),
        EnsembleBackend().fit(batch),
    ]
    return ds, batch, backends


def test_five_backends_predict_a_distribution():
    """The competition grid is now the full bio_refs §3 set: 2 baselines + 3 candidates,
    each emitting a genuine per-axis distribution."""
    ds, batch, backends = _fit_five()
    names = [b.name for b in backends]
    assert names == [
        "mean_baseline", "linear_response", "knn_response",
        "pathway_informed", "ensemble",
    ]
    for b in backends:
        preds = b.predict(batch)
        assert len(preds) == batch.n
        assert preds[0].mean.shape == (ds.dim,) and preds[0].std.shape == (ds.dim,)
        assert np.all(preds[0].std > 0.0)
    # pathway + ensemble are CANDIDATES (must clear the gate), not baselines.
    flags = {b.name: b.is_baseline for b in backends}
    assert flags["pathway_informed"] is False and flags["ensemble"] is False


def test_pathway_fingerprint_reflects_pathway_prior():
    """Which pathway annotation was used enters the fingerprint (charter: impl versions ->
    provenance) -- two different priors give different fingerprints."""
    _, batch, _ = _fit_five()
    G = batch.deltas.shape[1]
    p_a = PathwayInformedBackend(pathway_of=np.zeros(G, dtype=int)).fit(batch)
    p_b = PathwayInformedBackend(pathway_of=np.arange(G) % 4).fit(batch)
    assert p_a.fingerprint() != p_b.fingerprint()


def test_ensemble_uncertainty_carries_epistemic_disagreement():
    """The ensemble std combines within-model width with member DISAGREEMENT (epistemic):
    its per-axis std is never below the tightest member and exceeds the pure-aleatoric
    floor where members disagree."""
    _, batch, backends = _fit_five()
    members = backends[:3]  # mean, linear, knn
    ens = EnsembleBackend(members=[type(m)() for m in members]).fit(batch)
    x = batch.embeddings[0]
    ep = ens.predict(PerturbationBatch(pert_ids=("q",), embeddings=x[None, :]))[0]
    member_stds = np.stack([m._predict_one("q", x).std for m in ens._members])
    aleatoric = np.sqrt((member_stds**2).mean(axis=0))
    # never tighter than the pure aleatoric floor, and strictly wider on some axes.
    assert np.all(ep.std + 1e-9 >= aleatoric)
    assert np.any(ep.std > aleatoric + 1e-6)


def test_full_grid_gate_is_discriminative_across_candidates():
    """With three candidate backends, the gate ADMITS at least one on the informative
    regime and admits NONE on the scrambled regime (every candidate -> negative claim)."""
    info = run_regime("informative")
    scr = run_regime("scrambled")
    assert len(info["admitted_backends"]) >= 1
    assert "knn_response" in info["admitted_backends"]
    assert scr["admitted_backends"] == []
    # every scrambled candidate carries a first-class negative claim.
    for g in scr["baseline_gate"]:
        assert g["admitted"] is False
        assert g["negative_claim"] is not None
        assert g["negative_claim"]["kind"] == "baseline_gate_negative"


# --------------------------------------------------------------- deepened calibration


def test_calibration_curve_and_proper_scores_are_populated():
    """Calibration is a CURVE (coverage at several sigma) plus proper scores (ECE, NLL,
    sharpness) -- not a single 1-sigma point."""
    _, batch, backends = _fit_five()
    ds = make_replay_dataset(seed=27, n_pert=60, regime="informative", n_ood=0)
    held = ds.to_batch()
    for b in backends:
        s = score_backend(b, held)
        assert len(s.coverage_curve) == 4
        assert all(0.0 <= c <= 1.0 for c in s.coverage_curve)
        assert s.calibration_ece >= 0.0
        assert s.sharpness > 0.0
        assert np.isfinite(s.gaussian_nll)


# --------------------------------------------------------------- REAL Perturb-seq benchmark


def test_real_benchmark_is_marked_non_wet():
    """The real published Perturb-seq benchmark is retrospective benchmark/calibration
    ONLY -- is_wet_observation is hard-False for every dataset (charter §4)."""
    t = RealBenchmarkTable.load()
    assert set(t.datasets()) >= {"adamson", "replogle_k562_essential", "replogle_rpe1_essential"}
    for ds in t.datasets():
        prov = t.provenance(ds)
        assert prov.is_wet_observation is False
        assert prov.validation_level == "retrospective"
        assert prov.role == "benchmark_calibration"
        assert "Ahlmann-Eltze" in prov.source and "Zenodo" in prov.source
    # scope is a real cell-line context boundary (folds into the fingerprint).
    assert "K562" in t.provenance("replogle_k562_essential").scope
    assert "RPE1" in t.provenance("replogle_rpe1_essential").scope


def test_real_benchmark_fingerprint_folds_scope():
    t = RealBenchmarkTable.load()
    fps = {ds: t.fingerprint(ds) for ds in t.datasets()}
    assert len(set(fps.values())) == len(fps)  # distinct datasets -> distinct fingerprints


def test_real_baseline_gate_confirms_no_consistent_winner():
    """The SAME baseline-gate logic on the REAL published numbers reproduces the paper's
    headline: no method clears the mean-baseline gate on ALL datasets, and the flagship
    foundation model (scgpt) clears on NONE. This is the external, real-data grounding of
    the synthetic gate (bio_refs §1)."""
    t = RealBenchmarkTable.load()
    cleared_on = {}  # method -> set of datasets it clears
    for ds in t.datasets():
        for v in real_baseline_gate(t, ds):
            if v.admitted:
                cleared_on.setdefault(v.method, set()).add(ds)
    n_ds = len(t.datasets())
    # no method clears on every dataset (no consistent winner).
    assert all(len(dss) < n_ds for dss in cleared_on.values())
    # scgpt (flagship foundation model) never clears.
    assert "scgpt" not in cleared_on


def test_real_benchmark_crosscheck_report_is_honest():
    rep = real_benchmark_crosscheck()
    assert rep["replogle_rpe1_essential"]["is_wet_observation"] is False
    assert rep["replogle_rpe1_essential"]["any_method_cleared"] is False
    assert "no method clears" in rep["conclusion"]


# --------------------------------------------------------------- yaml loadability (M27 handoff a)


def test_perturbation_yaml_passes_config_validation():
    """The perturbation_screen.yaml acceptance_faces status is a LEGAL enum (declared),
    so DomainConfig validation no longer rejects it on the illegal-enum gate. Full
    load_domain() still stops at the adapter gate until B registers the dry leg in
    ADAPTER_REGISTRY (the documented staging seam) -- that rejection is NOT the enum one."""
    from pathlib import Path

    import yaml

    from expos.domain import DomainConfig, DomainError

    raw = yaml.safe_load(
        Path("domains/perturbation/perturbation_screen.yaml").read_text(encoding="utf-8")
    )
    cfg = DomainConfig.model_validate(raw)
    assert cfg.name == "perturbation_screen"
    assert cfg.adapter == "cell_state_perturbation"
    # every acceptance face carries a legal status ({declared, landed}); none is `staged`.
    assert cfg.acceptance_faces is not None
    assert {f.status for f in cfg.acceptance_faces} <= {"declared", "landed"}
    assert all(f.status != "staged" for f in cfg.acceptance_faces)  # the fixed illegal value

    # load_domain now fails (if at all) at the adapter gate, NOT the enum gate.
    try:
        load_domain("domains/perturbation/perturbation_screen.yaml")
    except DomainError as e:
        assert "staged" not in str(e)
        assert "status" not in str(e)  # not an acceptance-face-status validation error


# --------------------------------------------------------------- dry competition leg (M27 handoff b)


def _dataset(regime, n_pert=60, n_ood=6):
    return make_replay_dataset(seed=27, n_pert=n_pert, regime=regime, n_ood=n_ood)


def test_adapter_capability_matches_central_input_kind():
    """The dry leg's capability equals the central input_kind constant B landed, so B's
    dry-dispatch registration keys straight onto it (charter: neutral capability, no domain
    literal in the kernel)."""
    ad = CellStatePerturbationAdapter()
    assert ad.name == "cell_state_perturbation"
    assert ad.ACCEPTS_INPUT_KINDS == (INPUT_KIND_CELL_STATE_PERTURBATION,)


def test_dry_leg_emits_negative_claim_when_complex_does_not_beat_baseline():
    """THE handoff-(b) discriminative case: on the SCRAMBLED regime the complex backend
    cannot beat the baseline, so the dry competition leg emits a FIRST-CLASS negative claim
    for it (baseline-gate hard gate; negative is knowledge, not failure)."""
    ad = CellStatePerturbationAdapter()
    result = ad.compete_from_dataset(_dataset("scrambled"), round_index=0)

    # no expensive proposer is admitted on scrambled data...
    assert result.admitted == []
    # ...and every candidate (incl. the complex knn) yields a first-class negative claim.
    assert result.has_negative is True
    knn_neg = [n for n in result.negative_claims if n["claim_id"].endswith("knn_response_not_over_baseline")]
    assert len(knn_neg) == 1
    nc = knn_neg[0]
    assert nc["status"] == "rejected"
    assert nc["kind"] == "baseline_gate_negative"
    assert nc["round_index"] == 0
    assert "did not significantly" in nc["statement"]


def test_dry_leg_admits_complex_when_it_genuinely_beats_baseline():
    """The gate is NOT an always-negative rubber stamp: on the INFORMATIVE regime the
    complex knn backend clears and is admitted (and carries no negative claim)."""
    ad = CellStatePerturbationAdapter()
    result = ad.compete_from_dataset(_dataset("informative"), round_index=0)
    assert "knn_response" in result.admitted
    assert all(
        "knn_response_not_over_baseline" not in n["claim_id"] for n in result.negative_claims
    )


def test_dry_leg_drives_competition_each_round_with_provenance():
    """The leg drives score_backend + baseline_gate per round and stamps each round's
    verdicts + backend weight-hash fingerprints (charter obligation #1)."""
    ad = CellStatePerturbationAdapter()
    ds = _dataset("informative")
    r0 = ad.compete_from_dataset(ds, round_index=0)
    # a baseline is present in the roster (the gate has something to gate against), and
    # every non-baseline proposer got a verdict.
    assert {"mean_baseline", "linear_response"} <= set(r0.scores)
    non_baseline = {v.backend for v in r0.verdicts}
    assert non_baseline == {"knn_response", "pathway_informed", "ensemble"}
    # provenance: each backend carries a name@version#sha256 fingerprint.
    assert all("#sha256:" in fp for fp in r0.backend_fingerprints.values())
    # re-fitting on different data (different regime) flips the complex backend fingerprint.
    r_scr = ad.compete_from_dataset(_dataset("scrambled"), round_index=0)
    assert r0.backend_fingerprints["knn_response"] != r_scr.backend_fingerprints["knn_response"]


def test_dry_leg_never_certifies_a_claim():
    """The competition leg emits only DRY evidence: every claim it produces is a REJECTED
    baseline-gate negative -- it never emits a `supported` biological claim (only a trusted
    observation certifies, charter §4)."""
    ad = CellStatePerturbationAdapter()
    for regime in ("informative", "scrambled"):
        result = ad.compete_from_dataset(_dataset(regime))
        for nc in result.negative_claims:
            assert nc["status"] == "rejected"
            assert nc["kind"] == "baseline_gate_negative"


# ================================================================= M27 mcl seam (B 160 §3)
#
# The two A-side items B's second batch is blocked on:
#   (1) the NEUTRAL round-batch hook the batch_compete dry leg is driven from, with the
#       replay data access SEALED inside the provider (mcl must never touch biology data);
#   (2) the domain-side CertificationPolicy that lands the baseline-gate's negative claims
#       in the real ledger through the EXISTING _certify_round (zero mcl change).

import ast
import inspect
from pathlib import Path

from expos.kernel.claims import (
    DECISION_FN_REGISTRY,
    ClaimDecisionStatus,
    ClaimRecord,
    EvidenceStrength,
    Ledger,
    ProvenanceActivity,
    ProvenanceSnapshot,
    ProvenanceUsage,
    apply_claim_deltas,
)
from expos.planner.certification import CertificationError, NullCertification

from domains.perturbation.gate_certification import (
    M27_CRITERION_VERSION,
    M27_DECISION_FN_ID,
    M27_DECISION_FN_VERSION,
    PerturbationGateCertification,
    ProposerSelector,
    gate_claim_id,
)

_KFP = "sha256:00knowledge00"


def _policy(regime="informative", **kw):
    return PerturbationGateCertification(
        PerturbationScreenProvider(replay_regime=regime), **kw
    )


# ---- (1) the neutral round-batch hook -------------------------------------------------


def test_round_batches_hook_is_neutral_and_carries_reference_deltas():
    """The hook B drives batch_compete from: `provider.round_batches(round_id)` ->
    (train_batch, held_batch) with the REFERENCE DELTAS on the held batch. The signature is
    neutral (a round id + an optional seed -- no domain object crosses it), so mcl calls it
    exactly as it calls M25's propose_candidates."""
    prov = PerturbationScreenProvider()
    params = inspect.signature(prov.round_batches).parameters
    assert list(params) == ["round_id", "seed"]
    assert all(p.default is not inspect.Parameter.empty for p in params.values())

    train, held = prov.round_batches(0)
    assert held.deltas is not None and held.deltas.shape[0] == len(held.pert_ids)
    assert train.deltas is not None and len(train.pert_ids) > 0
    assert held.ood_mask is not None and held.ood_mask.any()
    # the pair drives the UNCHANGED dry leg straight off the hook's return
    result = CellStatePerturbationAdapter().compete_round(train, held, round_index=0)
    assert {v.backend for v in result.verdicts} == {
        "knn_response", "pathway_informed", "ensemble"
    }


def test_round_batches_is_deterministic_train_grows_and_held_is_invariant():
    """Pure + deterministic in (round_id, seed) -- gate K5, so a resumed round rebuilds the
    same split bitwise. The held-out split is round-INVARIANT (cross-round scores stay
    comparable, no round leaks a held row into training) and the train set GROWS."""
    prov = PerturbationScreenProvider()
    (t0, h0), (t0b, h0b) = prov.round_batches(0), prov.round_batches(0)
    assert t0.pert_ids == t0b.pert_ids and h0.pert_ids == h0b.pert_ids
    assert np.array_equal(h0.deltas, h0b.deltas)

    t1, h1 = prov.round_batches(1)
    t2, _ = prov.round_batches(2)
    assert h1.pert_ids == h0.pert_ids  # held-out is round-invariant
    assert set(t0.pert_ids) < set(t1.pert_ids) <= set(t2.pert_ids)  # train grows
    assert not (set(t2.pert_ids) & set(h0.pert_ids))  # never leaks the held split
    # OOD is ALWAYS held out and NEVER trains (the abstention face's honesty premise).
    assert not any(p.startswith("OOD_") for p in t2.pert_ids)
    assert any(p.startswith("OOD_") for p in h0.pert_ids)


def test_round_batch_data_access_is_sealed_inside_the_provider():
    """B's constraint (red_to_blue/160 §3.2): mcl must not reach into datasets/replay/* --
    that would put biology in the domain-NEUTRAL core (EXP014 red, charter §4). The hook is
    the seal: the replay import lives inside the provider, and the neutral core imports
    neither the dataset package nor the domain package.

    Asserted on the IMPORT GRAPH (ast), not on the file's text: mentioning either path in a
    comment/docstring -- e.g. to explain why mcl does NOT touch it -- is fine and must not
    trip this."""
    mcl = Path(__file__).resolve().parents[1] / "expos" / "mcl.py"
    imported: set[str] = set()
    for node in ast.walk(ast.parse(mcl.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(node.module)
    roots = {m.partition(".")[0] for m in imported}
    assert "datasets" not in roots  # the replay data package
    assert "domains" not in roots  # every Team domain package (biology lives there)
    # ...while the provider (the domain layer, where biology is allowed to live) owns it.
    assert "make_replay_dataset" in inspect.getsource(
        PerturbationScreenProvider._replay_dataset
    )


def test_round_batches_source_is_retrospective_and_never_a_wet_observation():
    """Charter §4 iron rule at the one hook that touches data: what round_batches yields is
    benchmark/calibration material, never this run's wet observation."""
    prov = PerturbationScreenProvider()
    provenance = prov.round_batches_provenance()
    assert provenance["is_wet_observation"] is False
    assert provenance["validation_level"] == "retrospective"
    assert provenance["role"] == "benchmark_calibration"
    assert provenance["scope"]  # a negative result is only meaningful inside its scope


def test_round_batches_fingerprint_flips_with_the_replay_source():
    """SEAM 4: the batch-source token B folds into config_fingerprint (the M25
    operator_fingerprint precedent) -- a regime/scope change flips run identity instead of
    silently re-scoring the gate on different data."""
    a = PerturbationScreenProvider().round_batches_fingerprint()
    b = PerturbationScreenProvider(replay_regime="scrambled").round_batches_fingerprint()
    c = PerturbationScreenProvider(holdout_frac=0.4).round_batches_fingerprint()
    assert a.startswith("batches:sha256:")
    assert a != b and a != c
    assert PerturbationScreenProvider().round_batches_fingerprint() == a  # stable


# ---- (2) the domain-side CertificationPolicy ------------------------------------------


def test_gate_certification_conforms_to_the_certification_policy_protocol():
    """It is a drop-in seventh element: same decide() signature as NullCertification, so
    B injects it with ZERO edits to mcl / planner / kernel."""
    ours = inspect.signature(PerturbationGateCertification.decide)
    theirs = inspect.signature(NullCertification.decide)
    assert list(ours.parameters) == list(theirs.parameters)
    assert PerturbationGateCertification.name != NullCertification.name
    # the decision fn is registered in the SHARED registry the kernel gate checks
    reg = DECISION_FN_REGISTRY[M27_DECISION_FN_ID]
    assert reg.version == M27_DECISION_FN_VERSION


def test_gate_certification_lands_negative_claims_through_the_kernel_gate():
    """THE M27 ask: the baseline-gate's negative claims (a complex backend that did not
    clear the gate) land in the REAL ledger as first-class REJECTED knowledge, via the
    existing decide -> apply_claim_deltas path. The admitted proposer's mirror lands
    SUPPORTED, so the gate is proven discriminative on the ledger itself."""
    policy = _policy("informative")
    deltas, state = policy.decide([], Ledger(), None, 0, _KFP)
    ledger, report = apply_claim_deltas(Ledger(), deltas)  # the kernel gate is the mutator
    status = {c.claim_id: c.status for c in ledger.claims}

    assert status[gate_claim_id("knn_response")] is ClaimDecisionStatus.SUPPORTED
    for loser in ("pathway_informed", "ensemble"):
        assert status[gate_claim_id(loser)] is ClaimDecisionStatus.REJECTED
    assert all(o.deny_reason is None for o in report.outcomes)  # governance-legal deltas
    assert state is None  # stateless criterion: correlated re-scores never accumulate

    # every landed head is honestly scoped + labelled non-wet
    for rec in ledger.claims:
        assert "is_wet_observation=False" in rec.statement
        assert "scope:" in rec.statement
    # provenance closes the K4 chain against the run's REAL knowledge fingerprint
    assert all(
        d.provenance.usage.consumed_knowledge_fingerprint == _KFP for d in deltas
    )
    # the evidence entity is unmistakably the retrospective evaluation, not a wet obs
    obs = deltas[0].provenance.usage.observations[0]
    assert obs.obs_id.startswith("m27-retrospective-eval:r0:")


def test_gate_verdict_replays_from_the_event_stream():
    """K4: a third party recomputes the verdict from the emitted snapshot alone, through the
    registry-resolved fn -- the online status and the replayed status are one function."""
    deltas, _ = _policy("informative").decide([], Ledger(), None, 0, _KFP)
    fn = DECISION_FN_REGISTRY[M27_DECISION_FN_ID].fn
    for delta in deltas:
        stat = delta.provenance.statistic.model_dump(mode="json")
        replayed = fn(
            statistic=stat,
            power={"trusted": True, "n_eval": stat["per_group"][0]["n"]},
            criterion_version=M27_CRITERION_VERSION,
        )
        assert replayed is delta.status
        # the frozen criterion rides with the verdict (pinned for replay)
        assert stat["decision_thresholds"]["min_improvement"] == 0.05
        assert stat["decision_thresholds"]["baseline"] in ("mean_baseline", "linear_response")


def test_indecisive_gate_evidence_is_insufficient_and_mutates_nothing():
    """K3: on the scrambled regime nothing decisively beats (or loses to) the baseline, so
    the honest verdict is INSUFFICIENT -- band `none`, no head mutated -- yet the deltas are
    still emitted, so `not enough evidence` is recorded rather than silently dropped."""
    deltas, _ = _policy("scrambled").decide([], Ledger(), None, 0, _KFP)
    assert deltas
    assert all(d.status is ClaimDecisionStatus.INSUFFICIENT for d in deltas)
    assert all(d.evidence_strength is EvidenceStrength.NONE for d in deltas)
    assert all(d.new_content is None for d in deltas)  # insufficient proposes no head
    ledger, report = apply_claim_deltas(Ledger(), deltas)
    assert not any(o.mutated_effective_status for o in report.outcomes)


def test_gate_certification_is_a_proposer_never_a_mutator():
    """THE MOAT. decide() proposes; the kernel gate inside mcl._certify_round is the sole
    mutator. And a gate claim can never touch BIOLOGY: the policy only ever targets its own
    m27_gate_* model claims -- the provider's biological seed claims are untouchable from
    the dry side (only a trusted observation certifies those, charter §4)."""
    seed_ids = [c.claim_id for c in PerturbationScreenProvider().seed_claims()]
    ledger = Ledger(
        claims=tuple(
            ClaimRecord(
                claim_id=cid,
                version=1,
                status=ClaimDecisionStatus.INSUFFICIENT,
                statement="seed",
                provenance=ProvenanceSnapshot(
                    usage=ProvenanceUsage(consumed_knowledge_fingerprint=_KFP),
                    activity=ProvenanceActivity(
                        decision_fn_id=M27_DECISION_FN_ID,
                        decision_fn_version=M27_DECISION_FN_VERSION,
                        criterion_version=M27_CRITERION_VERSION,
                    ),
                ),
            )
            for cid in seed_ids
        )
    )
    before = ledger.canonical_json()
    deltas, _ = _policy("informative").decide([], ledger, None, 0, _KFP)

    assert ledger.canonical_json() == before  # decide mutated nothing
    assert deltas and not ({d.target_claim_id for d in deltas} & set(seed_ids))
    # ... and the backends themselves hold no ledger handle at all.
    assert not hasattr(MeanBaselineBackend(), "ledger")
    assert "ledger" not in inspect.getsource(CellStatePerturbationAdapter)


def test_gate_certification_ignores_this_runs_wet_observations():
    """No laundering in either direction (charter §4): a benchmark-split claim about a MODEL
    is neither supported nor refuted by a wet reading of this run, so the wet stream cannot
    move it -- the deltas are identical with or without adjudicated observations."""
    policy = _policy("informative")
    bare, _ = policy.decide([], Ledger(), None, 0, _KFP)
    with_obs, _ = policy.decide(
        [object(), object()], Ledger(), None, 0, _KFP  # a stream it must not read
    )
    assert [d.provenance.fingerprint() for d in bare] == [
        d.provenance.fingerprint() for d in with_obs
    ]


def test_gate_claims_bind_late_to_the_round_never_to_prebuilt_ids():
    """B's 158 general rule: a binding may only happen once the thing it binds to exists.
    Nothing is bound at construction -- the policy takes no claim id and no roster. The
    round's REAL competitors (which the roster + the data decide) mint the claim ids inside
    decide(): swap the roster and the SAME policy object certifies whatever actually ran."""
    assert "claim_id" not in inspect.signature(PerturbationGateCertification.__init__).parameters

    duo = CellStatePerturbationAdapter(
        backend_factory=lambda: [MeanBaselineBackend(), KNNResponseBackend()]
    )
    policy = PerturbationGateCertification(PerturbationScreenProvider(), duo)
    deltas, _ = policy.decide([], Ledger(), None, 0, _KFP)
    assert [d.target_claim_id for d in deltas] == [gate_claim_id("knn_response")]

    # a different roster -> different real competitors -> different claim ids, same policy
    # SHAPE (id-free construction), nothing prebuilt.
    full = PerturbationGateCertification(PerturbationScreenProvider())
    ids = {d.target_claim_id for d in full.decide([], Ledger(), None, 0, _KFP)[0]}
    assert ids == {
        gate_claim_id(b) for b in ("knn_response", "pathway_informed", "ensemble")
    }


def test_selector_names_proposers_by_semantics_not_by_id():
    """The M28 ArmSelector twin: which competitors get certified is named by SEMANTICS
    (backend name / the gate's own admission role), resolved against THIS round's real
    verdicts -- never by a claim id."""
    negatives_only = _policy("informative", selector=ProposerSelector(admission="not_admitted"))
    deltas, _ = negatives_only.decide([], Ledger(), None, 0, _KFP)
    assert {d.target_claim_id for d in deltas} == {
        gate_claim_id("pathway_informed"), gate_claim_id("ensemble")
    }
    assert all(d.status is ClaimDecisionStatus.REJECTED for d in deltas)

    by_name = _policy("informative", selector=ProposerSelector(names=("knn_response",)))
    assert [d.target_claim_id for d in by_name.decide([], Ledger(), None, 0, _KFP)[0]] == [
        gate_claim_id("knn_response")
    ]
    with pytest.raises(CertificationError):
        ProposerSelector(admission="maybe")


def test_gate_certification_fails_loud_at_wiring_not_mid_round():
    """Birth-time governance: a provider without the hook (chemistry / M24-B / M26 / M28)
    is refused AT CONSTRUCTION with a message naming the hook -- never an AttributeError
    mid-round, never a silent no-op."""
    class _NoHookProvider:
        pass

    with pytest.raises(CertificationError, match="round_batches"):
        PerturbationGateCertification(_NoHookProvider())
    with pytest.raises(CertificationError):
        PerturbationGateCertification()  # no provider and no staged result


def test_staged_round_results_avoid_recompeting_the_round():
    """B's dry leg already holds the round's CompetitionRoundResult; handing it over means
    the certified verdicts ARE the round's verdicts (one competition, not two)."""
    prov = PerturbationScreenProvider()
    train, held = prov.round_batches(3)
    result = CellStatePerturbationAdapter().compete_round(train, held, round_index=3)
    staged = PerturbationGateCertification(prov, results_by_round={3: result})
    assert staged.round_result(3) is result
    deltas, _ = staged.decide([], Ledger(), None, 3, _KFP)
    assert {d.target_claim_id for d in deltas} == {
        gate_claim_id(v.backend) for v in result.verdicts
    }
