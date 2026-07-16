"""Breadth-first Biology Program v0.1 seam-wiring e2e (integration owner B).

One focused e2e-smoke per organ that the mcl/shared seams reach in this pass. Leads with the
LANDED organ (M26 genetic_circuit: a full dry+wet e2e through ``run_mcl_loop``) and the shared
vocabulary both heavy organs converge on; the lighter organs (M25/M27/M28/M29) are covered by
the confirmation-level assertions the seam actually reaches in v0.1 (the rest is recorded as an
honest handoff in the batch report -- wiring as far as the seam allows is honest v0.1 status).

HARD gates re-pinned here alongside the dedicated anchors (test_m24_mcl_wiring / test_k_c_wiring
/ test_w9_mcl): the new capabilities gate OFF for chemistry + M24-B (byte-identical), and no
biology literal leaks into the neutral core (EXP014, scripts/expos_lint.py).
"""

from __future__ import annotations

from pathlib import Path

from expos.adapters.domain_provider import (
    INPUT_KIND_CELL_STATE_PERTURBATION,
    INPUT_KIND_CIRCUIT_TOPOLOGY,
    INPUT_KIND_MOLECULAR_GEOMETRY,
    INPUT_KIND_SEQUENCE_CONSTRUCT,
    ComputeTarget,
)
from expos.adapters.dry.circuit_adapter import CircuitTopologyAdapter
from expos.domain import load_domain
from expos.kernel.store import RunStore
from expos.mcl import (
    _candidate_params,
    _domain_bindings,
    _LINEAGE_PARAMS_KEY,
    _make_dry_leg_plan,
    _params_kind_for,
    run_mcl_loop,
)

_REPO = Path(__file__).resolve().parents[1]
_GC = _REPO / "domains" / "genetic_circuit" / "genetic_circuit.yaml"
_CATALYST = _REPO / "domains" / "catalyst_screen.yaml"
_BIO = _REPO / "domains" / "cell_free_expression_screen.yaml"


# ==================================================== central shared vocabulary (M26/M27 SEAM 1)


def test_new_input_kinds_are_central_constants():
    """The two breadth-first input kinds live in the shared vocabulary (both heavy organs asked
    B to converge their local literals here). Byte-compatible with the domain-local literals."""
    assert INPUT_KIND_CIRCUIT_TOPOLOGY == "circuit_topology"
    assert INPUT_KIND_CELL_STATE_PERTURBATION == "cell_state_perturbation"


# ==================================================== M26 genetic_circuit (LANDED dry+wet e2e)


def test_m26_dry_dispatch_selects_circuit_sync():
    """circuit_topology capability -> the SYNCHRONOUS CircuitTopologyAdapter dry leg (the
    dynamic-phenotype analogue of the sequence sync leg), selected by CAPABILITY."""
    cfg = load_domain(_GC)
    bindings = _domain_bindings(cfg)
    assert bindings.dry_capability == INPUT_KIND_CIRCUIT_TOPOLOGY
    assert bindings.params_kind == "circuit"
    plan = _make_dry_leg_plan(cfg, bindings, Path("/tmp"))
    assert isinstance(plan.adapter, CircuitTopologyAdapter)
    assert plan.kind == "sync_execute"
    assert plan.metric == CircuitTopologyAdapter.default_metric  # dynamic_proxy


def test_m26_circuit_params_wrap_typed_graph():
    """A circuit level's Candidate params carry the serialised typed graph under the
    ``circuit_topology`` key the dry adapter reads, plus the screening-var level key."""
    cfg = load_domain(_GC)
    bindings = _domain_bindings(cfg)
    level = bindings.candidate_pool[0]
    params = _candidate_params(level, bindings)
    assert isinstance(params["circuit_topology"], dict)  # the graph payload
    assert params[bindings.variable] == level


def test_m26_e2e_runs_dry_and_wet(tmp_path):
    """The FULL M26 v0.1 e2e closes through run_mcl_loop: the typed circuit graph is verified +
    simulated (dry), promoted, and its DYNAMIC phenotype is read in-process (wet time-series
    reader) into trusted observations -- obs -> claim lifecycle."""
    summary = run_mcl_loop(
        _GC, rounds=2, seed=7, out_dir=tmp_path / "gc", truth_profile="dynamic_high"
    )
    assert summary["rounds_completed"] == 2
    assert summary["n_dry"] > 0   # verify->simulate->derive dry observations
    assert summary["n_wet"] > 0   # dynamic time-series wet observations
    assert summary["n_trusted"] > 0


def test_m26_e2e_dynamic_phenotype_is_discriminative(tmp_path):
    """The dynamic analogue of the M24 supported/flat split: the mean wet DYNAMIC phenotype is
    high on the positive face (dynamic_high) and collapses to baseline on the null face
    (dynamic_flat) -- so a correct aggregator certifies the seed on high and stays insufficient
    on flat. Both legs derive the phenotype identically (derive_phenotype), the single seam."""
    means = {}
    for face in ("dynamic_high", "dynamic_flat"):
        out = tmp_path / face
        run_mcl_loop(_GC, rounds=1, seed=7, out_dir=out, truth_profile=face)
        wet = [
            o.result.value for o in RunStore(out).list_observations()
            if o.raw_ref.kind == "wet" and o.result.value is not None
        ]
        means[face] = sum(wet) / len(wet)
    assert means["dynamic_high"] > means["dynamic_flat"] + 0.3


# ==================================================== M25 generative_construct (candidate path)


def test_m25_generated_construct_candidate_runs_the_sequence_path():
    """M25 SEAM 1 confirmation: a PROVIDER-GENERATED construct child (parent->variant, not a
    preset catalogue entry) is a ``sequence_construct`` payload -- the exact kind M24-B already
    routes -- so the mcl candidate path is GENERATOR-AGNOSTIC: it accepts a generated child
    verbatim and lifts its parent lineage into the store-only field (M24 item #4). Modelled
    self-contained (a generated child's payload) rather than reaching into the M25 domain
    internals, which A's completion agents are actively evolving in parallel.

    Recorded-incomplete (report handoff): folding a generative source's children INTO the
    round candidate pool needs a ``provider.propose_candidates`` hook + a loadable M25 domain;
    the operator source-hash into config_fingerprint is the M25 SEAM #6 refinement."""
    child_id = "j23103__promoter_swap__j23100"
    target = ComputeTarget(
        target_id=child_id,
        input_kind=INPUT_KIND_SEQUENCE_CONSTRUCT,
        payload={"sequence": "ATGCGT", "promoter": "j23100", "rbs": "B0034",
                 "cds": "gfp", "parent_construct": "j23103",
                 "sequence_version": "promoter_swap"},
        payload_schema_version="sequence_construct/1",
        adapter_capability=INPUT_KIND_SEQUENCE_CONSTRUCT,
    )
    bindings = _domain_bindings(load_domain(_BIO))  # real bio sequence bindings...
    bindings = bindings.__class__(
        **{**bindings.__dict__, "variable": "construct",
           "candidate_pool": (child_id,),
           "coords": {child_id: 1.0},
           "compute_targets": {child_id: target}}  # ...pointed at the generated child
    )
    params = _candidate_params(child_id, bindings)
    assert params["sequence"] == "ATGCGT"        # dry-adapter input forwarded verbatim
    assert params["construct"] == child_id       # screening-var key carries the level
    # generated-design lineage rides the dedicated store-only field (parent fingerprint),
    # NOT the replicate-provenance parent_obs_id
    assert params[_LINEAGE_PARAMS_KEY]["parent_construct"] == "j23103"


# ==================================================== gating: chemistry + M24-B byte-identical


def test_chemistry_gates_off_the_new_capabilities():
    """The chemistry anchor: catalyst_screen selects the UNCHANGED async PySCF dry leg and none
    of the breadth-first capability branches engage (params_kind is neither circuit nor
    sequence)."""
    cfg = load_domain(_CATALYST)
    bindings = _domain_bindings(cfg)
    assert bindings.dry_capability == INPUT_KIND_MOLECULAR_GEOMETRY
    assert _make_dry_leg_plan(cfg, bindings, Path("/tmp")).kind == "async_job"
    assert bindings.params_kind == "descriptor"


def test_m24b_bio_still_selects_sequence_not_circuit():
    """M24-B byte-identical anchor: the cell-free bio domain still routes the sequence sync leg,
    untouched by the new circuit branch."""
    cfg = load_domain(_BIO)
    bindings = _domain_bindings(cfg)
    assert bindings.dry_capability == INPUT_KIND_SEQUENCE_CONSTRUCT
    assert _params_kind_for(bindings.dry_capability) == "sequence"


def test_params_kind_resolver_keys_on_capability():
    """The params-build strategy keys on the neutral capability constant, never a domain name."""
    assert _params_kind_for(INPUT_KIND_CIRCUIT_TOPOLOGY) == "circuit"
    assert _params_kind_for(INPUT_KIND_SEQUENCE_CONSTRUCT) == "sequence"
    assert _params_kind_for(INPUT_KIND_MOLECULAR_GEOMETRY) == "descriptor"


# NOTE: M28 biology_discovery is covered by a report HANDOFF, not a test here -- its
# red-line (untrusted evidence non-mutating) is enforced agent-side in a domain API that A's
# completion agents are actively evolving; reaching into it from an integration test would be
# fragile. The mcl bridge (a trusted discovery verdict -> the existing certification ->
# ClaimDelta path) is the recorded-incomplete seam.


# ==================================================== M29 protocols/physical (M23 seam reuse)


def test_m29_fake_backend_drives_the_transaction_ledger(tmp_path):
    """M29 SEAM 1/2 FINDING: the M29 fake backends already implement the M23
    ``SensedState.sense`` protocol, so ``FakeLiquidHandler`` is injectable through mcl's
    EXISTING ``physical_backend`` seam with no new wiring -- deck transfers route through the
    physical-action transaction ledger and observations are narrowed to COMMITTED wells. (The
    remaining M29 seams -- protocol->device_ir->ExperimentObject compiler + MEASURE->observable
    binding -- are recorded as handoffs.)"""
    from expos.adapters.physical.fake_backends import FakeLiquidHandler

    out = tmp_path / "m29"
    summary = run_mcl_loop(
        _BIO, rounds=1, seed=7, out_dir=out, truth_profile="expression_high",
        physical_backend=FakeLiquidHandler(),
    )
    n_trans = len(RunStore(out).read_events("physical_action_transition"))
    assert n_trans > 0             # the fake drove the M23 transaction ledger
    assert summary["n_wet"] > 0    # committed wells survived the commit-before-observation gate
