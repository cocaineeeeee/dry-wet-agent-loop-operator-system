"""M26 v0.1 genetic-circuit domain discriminative suite (Team M26).

The FIRST expos domain whose phenotype is DYNAMIC (a time-series), proving expos handles
more than a scalar phenotype. Everything is exercised DOMAIN-LOCALLY (the e2e mcl run awaits
B's time-series observable schema + circuit_topology dispatch + dynamic-face registration
seams -- docs/bio_seams/M26.md). Validation level: simulation.

Covered:
  1. typed circuit graph identity: topology digest vs parameter digest (two-layer), payload
     round-trip, functional revision (kinetics / part swap) changes the right digest;
  2. verify gate: legal circuits pass all 5 levels; illegal topologies rejected at the right
     level (dangling ref -> execution; missing feedback -> function motif);
  3. simulation determinism + stochastic-proxy seed reproducibility;
  4. time-series derivation: steady state / response amplitude / switching time;
  5. high / flipped / flat DYNAMIC faces (only-mu-differs sign flip; flat = no dynamic);
  6. dry adapter execute face: value + secondary, NO truth_records, no exp mutation, and it
     refuses to simulate an illegal topology (verify gate enforced inside the dry leg);
  7. dry proxy monotone in the design coordinate (cassette dose ladder + toggle) -> Dry->Wet
     promotion signal is real;
  8. provider birth-time governance + validate_yaml + kernel-neutrality of the payload.
"""

from __future__ import annotations

import numpy as np
import pytest
import yaml

from domains.genetic_circuit import library as lib
from domains.genetic_circuit.graph import CircuitGraph, circuit_from_payload
from domains.genetic_circuit.provider import (
    INPUT_KIND_CIRCUIT_TOPOLOGY,
    GeneticCircuitProvider,
)
from domains.genetic_circuit.verify import LEVELS, verify
from expos.adapters.domain_provider import (
    DomainProviderError,
    adapter_accepts_capability,
)
from expos.adapters.dry.circuit_adapter import CircuitTopologyAdapter, circuit_params
from expos.adapters.dry.circuit_dynamics import (
    response_amplitude,
    steady_state,
    switching_time,
)
from expos.adapters.dry.circuit_simulation import simulate
from expos.adapters.wet.timeseries_reader import (
    DYNAMIC_TRUTH_PROFILES,
    DynamicTruthSurface,
    read_dynamic,
)
from expos.domain import DomainConfig
from expos.kernel.objects import (
    Budget,
    Candidate,
    DesignProvenance,
    DesignSpace,
    ExecutionReq,
    ExperimentObject,
    LayoutAssignment,
    Objective,
    VariableDef,
    WellAssignment,
)

_YAML_PATH = "domains/genetic_circuit/genetic_circuit.yaml"
_COORDS = [0.1, 0.4, 0.7, 1.0]


# ============================================================ 1. typed graph identity


def test_topology_vs_parameter_identity_two_layers():
    """Two-layer identity (docs/bio_refs/02 §4.D): two toggles with identical WIRING but
    different kinetics share a topology digest yet differ in parameter digest; a toggle and
    a cassette differ in topology digest. Same params -> bit-identical digests."""
    strong = lib.toggle_switch(1.0)
    weak = lib.toggle_switch(0.4)
    cassette = lib.expression_cassette(1.0)

    assert strong.topology_digest() == weak.topology_digest()          # same wiring
    assert strong.parameter_digest() != weak.parameter_digest()        # different kinetics
    assert strong.topology_digest() != cassette.topology_digest()      # different topology
    # deterministic: recomputing is bit-identical.
    assert lib.toggle_switch(1.0).parameter_digest() == strong.parameter_digest()


def test_payload_roundtrip_and_functional_revision():
    """A graph round-trips through its payload (the candidate.params carrier), and functional
    revision changes the correct digest layer: with_kinetics -> parameter only; swap_part ->
    topology (DoD #7 next-round redesign primitives)."""
    g = lib.toggle_switch(1.0)
    back = circuit_from_payload(g.to_payload())
    assert back.parameter_digest() == g.parameter_digest()
    assert back.topology_digest() == g.topology_digest()

    revised = g.with_kinetics("tu_tetr", beta=99.0)
    assert revised.topology_digest() == g.topology_digest()            # same wiring
    assert revised.parameter_digest() != g.parameter_digest()          # new kinetics

    cas = lib.expression_cassette(1.0, promoter=lib._PROMOTER_STRONG)
    # add a weaker promoter part then swap it in -> topology changes.
    cas2 = CircuitGraph(
        circuit_id=cas.circuit_id,
        parts=cas.parts + (lib._PROMOTER_WEAK,),
        units=cas.units, interactions=cas.interactions, behaviour=cas.behaviour,
    ).swap_part("tu_reporter", "promoter", lib._PROMOTER_WEAK.part_id)
    assert cas2.topology_digest() != cas.topology_digest()


# ============================================================ 2. verify gate


def test_verify_passes_legal_circuits_all_five_levels():
    """Both preset families pass all five verify levels (execution->validity->structure->
    semantics->function motif), highest_passed == function."""
    for g in (lib.expression_cassette(1.0), lib.toggle_switch(1.0)):
        rep = verify(g)
        assert rep.ok, rep.as_dict()
        assert rep.highest_passed == "function"
        assert tuple(r.level for r in rep.results) == LEVELS
        assert all(r.ran and r.passed for r in rep.results)


def test_verify_rejects_illegal_topologies_at_correct_level():
    """The gate stops illegal topologies BEFORE simulation, at the honest level: a dangling
    regulator reference fails EXECUTION (level 1, later levels not run); a toggle missing its
    feedback edge fails FUNCTION (motif absent) though its structure is valid."""
    dangling = verify(lib.dangling_regulator_circuit())
    assert not dangling.ok
    assert dangling.failed_level == "execution"
    # short-circuit: higher levels marked not-run.
    assert [r.ran for r in dangling.results] == [True, False, False, False, False]

    broken = verify(lib.broken_toggle_missing_feedback())
    assert not broken.ok
    assert broken.failed_level == "function"           # structure/semantics passed
    assert broken.highest_passed == "semantics"


# ============================================================ 3. simulation determinism


def test_simulation_deterministic_and_stochastic_seed_reproducible():
    """The ODE integration is deterministic; the stochastic proxy is reproducible for a fixed
    seed and differs from the deterministic run (intrinsic noise present)."""
    g = lib.expression_cassette(1.0)
    a = simulate(g, t_end=10.0, dt=0.02)
    b = simulate(g, t_end=10.0, dt=0.02)
    assert np.array_equal(a.series["GFP"], b.series["GFP"])            # deterministic

    s1 = simulate(g, t_end=10.0, dt=0.02, stochastic=True, seed=7)
    s2 = simulate(g, t_end=10.0, dt=0.02, stochastic=True, seed=7)
    s3 = simulate(g, t_end=10.0, dt=0.02, stochastic=True, seed=8)
    assert np.array_equal(s1.series["GFP"], s2.series["GFP"])          # same seed reproduces
    assert not np.array_equal(s1.series["GFP"], s3.series["GFP"])      # different seed differs
    assert not np.array_equal(s1.series["GFP"], a.series["GFP"])       # noise perturbs the ODE


# ============================================================ 4. time-series derivation


def test_time_series_derivation_steady_amplitude_switching():
    """The three dynamic-phenotype derivations behave: a rising reporter has steady_state near
    its plateau, positive response amplitude, and a switching (half-rise) time strictly inside
    the window; a flat trace has zero amplitude and never switches."""
    g = lib.expression_cassette(1.0)
    ts = simulate(g, t_end=20.0, dt=0.02)
    y = ts.series["GFP"]
    ss = steady_state(ts.t, y)
    assert ss == pytest.approx(50.5, abs=1.0)                          # basal+beta = 0.5+50
    assert response_amplitude(ts.t, y) > 40.0
    sw = switching_time(ts.t, y)
    assert 0.0 < sw < ts.t[-1]

    flat = np.full_like(ts.t, 0.05)
    assert response_amplitude(ts.t, flat) == pytest.approx(0.0)
    assert switching_time(ts.t, flat) == pytest.approx(ts.t[-1])       # never switches


# ============================================================ 5. dynamic faces (high/flipped/flat)


def test_dynamic_high_face_positive_sign():
    """dynamic_high (mu=0.85): the settled DYNAMIC phenotype rises strictly with the design
    coordinate -- stronger-design circuits reach a higher settled phenotype (the expected/
    positive dynamic). Derived from an actual time-series trace, not a scalar."""
    face = DynamicTruthSurface.from_profile("dynamic_high")
    phenos = [read_dynamic(c, profile="dynamic_high", noise_sd=0.0)[0].steady_state for c in _COORDS]
    assert all(b > a for a, b in zip(phenos, phenos[1:]))              # strictly increasing
    assert np.corrcoef(_COORDS, phenos)[0, 1] > 0.9
    # only-mu-differs law: amplitude/sigma/baseline are the shared defaults.
    assert (face.amplitude, face.sigma, face.baseline) == (1.0, 0.15, 0.05)


def test_dynamic_flipped_face_negative_sign():
    """dynamic_flipped (mu=0.20): the relation is INVERTED -- the settled phenotype FALLS with
    the design coordinate (weaker-design circuits reach the higher phenotype), contradicting
    the seed -- while amplitude/sigma/baseline stay identical (only mu differs)."""
    low = DynamicTruthSurface.from_profile("dynamic_flipped")
    high = DynamicTruthSurface.from_profile("dynamic_high")
    phenos = [read_dynamic(c, profile="dynamic_flipped", noise_sd=0.0)[0].steady_state for c in _COORDS]
    assert all(b < a for a, b in zip(phenos, phenos[1:]))              # strictly decreasing
    assert np.corrcoef(_COORDS, phenos)[0, 1] < -0.9
    assert (low.amplitude, low.sigma, low.baseline) == (high.amplitude, high.sigma, high.baseline)
    assert low.mu == 0.20 and high.mu == 0.85


def test_dynamic_flat_null_face_no_dynamic():
    """dynamic_flat is the NULL face: NO dynamic at all -- every coordinate yields the same
    flat baseline trace (zero response amplitude, no switching), so no direction can be read
    (a correct aggregator returns insufficient). Amplitude is zeroed."""
    flat = DynamicTruthSurface.from_profile("dynamic_flat")
    assert flat.amplitude == 0.0
    phenos, amps, switches = [], [], []
    for c in _COORDS:
        ph, _ = read_dynamic(c, profile="dynamic_flat", noise_sd=0.0)
        phenos.append(ph.steady_state)
        amps.append(ph.response_amplitude)
        switches.append(ph.switching_time)
    assert len(set(round(p, 12) for p in phenos)) == 1                 # coord-independent
    assert all(a == pytest.approx(0.0) for a in amps)                  # no rise
    assert all(s == pytest.approx(switches[0]) for s in switches)      # never switches
    # unknown face fails loudly (never silently falls back to a signal face).
    with pytest.raises(ValueError):
        DynamicTruthSurface.from_profile("no_such_face")
    assert set(DYNAMIC_TRUTH_PROFILES) == {"dynamic_high", "dynamic_flipped", "dynamic_flat"}


# ============================================================ 6. dry adapter execute face


def _exp_with_layout(cid: str, params: dict) -> ExperimentObject:
    return ExperimentObject(
        exp_id="m26",
        round_id=0,
        domain="genetic_circuit",
        objective=Objective(name="dynamic", metric="dynamic_proxy"),
        design_space=DesignSpace(
            name="genetic_circuit",
            variables=[VariableDef(name="circuit", kind="categorical", choices=[cid])],
        ),
        active_vars=["circuit"],
        candidates=[Candidate(cand_id=f"cand_{cid}", params=params)],
        layout=LayoutAssignment(
            rows=1, cols=1, seed=0,
            wells=[WellAssignment(well_id="A1", row=0, col=0, cand_id=f"cand_{cid}")],
        ),
        budget=Budget(wells_total=1, rounds_total=1),
        execution_req=ExecutionReq(adapter="circuit_topology"),
        provenance=DesignProvenance(generator="test"),
    )


def test_dry_adapter_execute_emits_value_secondary_no_truth_no_mutation():
    """The execute face emits one RawResult per well with the load-bearing dynamic value +
    the four dynamic secondary channels, NO truth_records (a dry leg never produces a truth
    sidecar), and does not mutate the exp. Capability probe accepts the circuit_topology kind."""
    g = lib.toggle_switch(1.0)
    params = circuit_params(g.to_payload(), coord=1.0)
    exp = _exp_with_layout(g.circuit_id, params)
    before = exp.model_dump()

    adapter = CircuitTopologyAdapter()
    assert adapter_accepts_capability(adapter, INPUT_KIND_CIRCUIT_TOPOLOGY)
    res = adapter.execute(exp, np.random.default_rng(0))
    assert res.truth_records is None
    assert len(res.raw_results) == 1
    raw = res.raw_results[0]
    assert raw.metric == "dynamic_proxy"
    assert raw.value == pytest.approx(adapter.compute(params).value)
    assert set(raw.secondary) == {
        "steady_state", "response_amplitude", "switching_time", "separation",
    }
    assert exp.model_dump() == before


def test_dry_adapter_refuses_illegal_topology_before_simulation():
    """The propose->dry verify gate is enforced INSIDE the dry leg: an illegal topology raises
    at compute time (rejected before any simulation is run), never returning a fabricated
    dynamic value."""
    from expos.adapters.base import AdapterError

    bad = circuit_params(lib.broken_toggle_missing_feedback().to_payload())
    with pytest.raises(AdapterError, match="failed verify"):
        CircuitTopologyAdapter().compute(bad)


# ============================================================ 7. dry proxy monotone (Dry->Wet)


def test_dry_proxy_monotone_in_design_coordinate():
    """The dry ODE proxy is an honest-biased proxy: the cassette dose ladder's steady-state
    and the toggle's bistable separation both rise strictly with the design coordinate, so the
    Dry->Wet promotion ranking signal is real (correlates with the public design coord)."""
    adapter = CircuitTopologyAdapter()
    # cassette ladder (strongest first -> reverse for ascending coord).
    ladder = list(reversed(lib.cassette_ladder()))
    coords = [coord for _cid, coord, _g in ladder]
    ss = [adapter.compute(circuit_params(g.to_payload(), coord=coord)).value
          for _cid, coord, g in ladder]
    assert all(b > a for a, b in zip(ss, ss[1:]))
    assert np.corrcoef(coords, ss)[0, 1] > 0.95

    tcoords = [0.1, 0.4, 0.7, 1.0]
    seps = [adapter.compute(circuit_params(lib.toggle_switch(c).to_payload(), coord=c)).value
            for c in tcoords]
    assert all(b > a for a, b in zip(seps, seps[1:]))                  # deeper bistability
    assert min(seps) > 0.0                                             # latch is separated


# ============================================================ 8. provider governance / neutrality


def test_provider_birth_time_governance_and_validate_yaml():
    """The provider passes the birth-time completeness + cross-hook gate (compute_targets keys
    == wet_coords keys, the three dynamic faces non-empty, dynamic_flat null declared, seed
    claims well-formed), and validate_yaml accepts the real yaml."""
    p = GeneticCircuitProvider.check_complete()
    assert set(p.compute_targets()) == set(p.wet_coords())
    assert set(p.truth_profiles()) == {"dynamic_high", "dynamic_flipped", "dynamic_flat"}
    assert p.null_profiles() == frozenset({"dynamic_flat"})
    seeds = {(c.claim_id, c.status, c.direction) for c in p.seed_claims()}
    assert seeds == {
        ("gc_strongdesign_dynamic_higher", "supported", "higher"),
        ("gc_weakdesign_dynamic_higher", "rejected", "lower"),
    }
    cfg = DomainConfig.model_validate(yaml.safe_load(open(_YAML_PATH, encoding="utf-8")))
    p.validate_yaml(cfg)  # no raise


def test_compute_targets_are_circuit_topology_and_yaml_mismatch_rejected():
    """Contract: every compute target is a circuit_topology carrying the typed graph payload
    (topology + parameter digests, NO fabricated geometry/sequence), the dry adapter accepts
    its capability, and a yaml whose circuit choices don't match the provider is rejected."""
    from types import SimpleNamespace

    p = GeneticCircuitProvider()
    adapter = CircuitTopologyAdapter()
    for cid, ct in p.compute_targets().items():
        assert ct.input_kind == INPUT_KIND_CIRCUIT_TOPOLOGY
        assert "circuit_topology" not in ct.payload  # payload IS the graph, not nested
        assert "parts" in ct.payload and "units" in ct.payload
        assert ct.payload["topology_digest"].startswith("topo:sha256:")
        assert "zmatrix" not in ct.payload and "sequence" not in ct.payload
        assert adapter_accepts_capability(adapter, ct.adapter_capability)

    fake_cfg = SimpleNamespace(design_space=SimpleNamespace(
        variables=[SimpleNamespace(name="circuit", choices=["cassette_pTet_J23100", "nope"])]))
    with pytest.raises(DomainProviderError, match="choices must equal"):
        p.validate_yaml(fake_cfg)
