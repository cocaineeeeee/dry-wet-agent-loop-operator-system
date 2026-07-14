"""M16 W5 -- solvent_screen domain end-to-end: dry + wet legs into ONE run store.

Proves the G4 semantics the W5 glue is responsible for:

  * The DRY leg (PySCFDryAdapter, W3) runs 2 real out-of-process PySCF jobs under
    a `compute` lease, ingests as `polarity_proxy` observations (channel = dry:
    raw_ref.kind="dry", pyscf instrument provenance + result sha).
  * The WET leg (WetDriver, W4) runs the full plate-reader flow under an
    `instrument` lease, ingests as `solvent_response` observations (channel = wet:
    raw_ref.kind="wet", plate-reader instrument id).
  * BOTH batches of observations land in the SAME RunStore with ONE events.jsonl,
    and QCPolicy/adjudicate works on each leg (route events + qc_report per leg).
  * The wet hidden truth is harvested on a SEPARATE (non-OS) scoring path via
    sim_reader.harvest_truth -> store.save_truth; driver readings carry no truth.

Determinism: reader noise_sd=0 (calibration drift + dropout artefacts stay
deterministic); PySCF result sha is the determinism anchor for the dry leg.
"""

from __future__ import annotations

import json
import socket
import threading

import pytest

from expos.adapters.dry.adapter import PySCFDryAdapter
from expos.adapters.dry.ingest import dry_raw_to_observations
from expos.adapters.wet import sim_reader
from expos.adapters.wet.screen import (
    DRY_METRIC,
    WET_METRIC,
    compile_wet,
    layout_from_protocol,
    run_wet_leg,
)
from expos.adapters.wet.sim_reader import harvest_truth
from expos.kernel.lifecycle import TrustPolicy
from expos.kernel.objects import (
    Budget,
    Candidate,
    Control,
    DesignProvenance,
    DesignSpace,
    ExecutionReq,
    ExperimentObject,
    LayoutAssignment,
    Objective,
    TrustLevel,
    VariableDef,
    WellAssignment,
)
from expos.kernel.store import RunStore
from expos.qc.checks import run_qc
from expos.qc.policy import QCPolicy
from expos.scheduler import LeaseManager, ResourceObject, SubprocessBackend

_SOLVENT_CHOICES = [
    "water", "methanol", "ethanol", "acetonitrile",
    "dmso", "acetone", "toluene", "hexane",
]


# ----------------------------------------------------------------- reader fixture

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _send(port: int, obj: dict, timeout: float = 2.0) -> dict:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps(obj) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                raise ConnectionError("reader closed without reply")
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode())


def _wait_port(port: int, timeout: float = 10.0) -> None:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"reader on port {port} did not come up")


@pytest.fixture
def reader():
    """In-process plate-reader; noise_sd=0 for a deterministic truth surface."""
    port = _free_port()
    srv = sim_reader.serve("127.0.0.1", port, seed=7, noise_sd=0.0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _wait_port(port)
    yield port
    srv.shutdown()
    srv.server_close()


# ----------------------------------------------------------------- exp builders

def _design_space() -> DesignSpace:
    return DesignSpace(
        name="solvent_screen",
        variables=[
            VariableDef(name="solvent", kind="categorical", choices=_SOLVENT_CHOICES),
            VariableDef(name="concentration", kind="continuous",
                        low=0.5, high=20.0, transform="log", unit="mM"),
            VariableDef(name="temperature", kind="continuous",
                        low=15.0, high=45.0, unit="C"),
            VariableDef(name="incubation_time", kind="continuous",
                        low=5.0, high=120.0, unit="min"),
        ],
    )


def _conditions(solvent: str) -> dict:
    """A full 4-dim candidate point (the 3 continuous dims are recorded conditions)."""
    return {"solvent": solvent, "concentration": 5.0,
            "temperature": 25.0, "incubation_time": 30.0}


def _dry_experiment() -> ExperimentObject:
    """Dry screening leg: metric = polarity_proxy (dipole). 2 solvents = 2 PySCF jobs."""
    cands = [
        Candidate(cand_id="cand_ethanol", params=_conditions("ethanol")),
        Candidate(cand_id="cand_hexane", params=_conditions("hexane")),
    ]
    layout = LayoutAssignment(
        rows=1, cols=2, seed=0,
        wells=[
            WellAssignment(well_id="A1", row=0, col=0, cand_id="cand_ethanol"),
            WellAssignment(well_id="A2", row=0, col=1, cand_id="cand_hexane"),
        ],
    )
    return ExperimentObject(
        exp_id="exp_dry",
        round_id=0,
        domain="solvent_screen",
        objective=Objective(name="polarity", metric=DRY_METRIC),
        design_space=_design_space(),
        active_vars=["solvent"],
        candidates=cands,
        layout=layout,
        budget=Budget(wells_total=96, rounds_total=2),
        execution_req=ExecutionReq(adapter="pyscf_dry"),
        provenance=DesignProvenance(generator="test"),
    )


def _wet_experiment_and_protocol(solvents: list[str]):
    """Wet leg: metric = solvent_response. Build candidates -> protocol -> matching
    layout, so deck positions and the kernel layout stay consistent."""
    cands = [Candidate(cand_id=f"cand_{s}", params=_conditions(s)) for s in solvents]
    controls = [
        Control(control_id="ctl0", kind="sentinel", params=_conditions("acetonitrile")),
        Control(control_id="ctl1", kind="sentinel", params=_conditions("acetonitrile")),
    ]
    exp_noptr = ExperimentObject(
        exp_id="exp_wet",
        round_id=0,
        domain="solvent_screen",
        objective=Objective(name="response", metric=WET_METRIC),
        design_space=_design_space(),
        active_vars=["solvent"],
        candidates=cands,
        controls=controls,
        budget=Budget(wells_total=96, rounds_total=2),
        execution_req=ExecutionReq(adapter="wet_sim_reader"),
        provenance=DesignProvenance(generator="test"),
    )
    otp = compile_wet(exp_noptr)
    layout = layout_from_protocol(otp)
    exp = exp_noptr.model_copy(update={"layout": layout})
    return exp, otp


def _qc_runner(metric_range):
    """Real three-tier QC, with history scoped to the SAME exp (so the two channels
    of the shared store never cross-contaminate each other's QC context)."""

    def runner(exp, obs_list, history):
        same = [o for o in (history or []) if o.exp_id == exp.exp_id]
        return run_qc(exp, obs_list, same or None, metric_range=metric_range,
                      moran_perm=99)

    return runner


# ============================================================ G4: both legs, one run

def test_dry_and_wet_land_in_one_run(reader, tmp_path):
    store = RunStore(tmp_path / "run")
    leases = LeaseManager(tmp_path)

    # ---- DRY leg: 2 real PySCF jobs under a `compute` lease --------------------
    exp_dry = _dry_experiment()
    before = exp_dry.model_dump()
    compute_res = ResourceObject("pyscf-compute", "compute")
    compute_lease = leases.acquire(compute_res.resource_id, ttl_s=120.0, tag="dry")
    assert compute_lease is not None
    try:
        adapter = PySCFDryAdapter(jobs_root=tmp_path / "dry_jobs", poll_interval_s=0.1)
        dry_result = adapter.run(exp_dry, backend=SubprocessBackend())
    finally:
        leases.release(compute_lease)

    assert exp_dry.model_dump() == before  # adapter never mutates the exp
    assert dry_result.n_jobs == 2 and len(dry_result.dry_raws) == 2
    assert dry_result.failures == {}

    store.save_experiment(exp_dry)
    dry_obs, provenance = dry_raw_to_observations(exp_dry, dry_result.dry_raws)
    QCPolicy(_qc_runner((0.0, 10.0)), TrustPolicy(0.6, 0.3)).judge(
        store, dry_obs, exp_dry)

    # dry channel provenance + adjudication
    for o in dry_obs:
        assert o.trust is not TrustLevel.PENDING           # adjudicated
        assert o.result.metric == DRY_METRIC
        assert o.raw_ref.kind == "dry"
        assert o.raw_ref.uri.endswith("result.json") and o.raw_ref.sha256
        assert o.instrument_meta.instrument_id.startswith("pyscf@")
        assert o.instrument_meta.engine == "pyscf"      # formal engine position
        prov = provenance[o.layout_meta.well_id]
        assert prov.engine == "pyscf" and prov.converged is True
    # method-error signal is real: ethanol (polar) has a larger dipole than hexane
    by_well = {o.layout_meta.well_id: o.result.value for o in dry_obs}
    assert by_well["A1"] > by_well["A2"]

    # ---- WET leg: full flow under an `instrument` lease -----------------------
    exp_wet, otp = _wet_experiment_and_protocol(
        ["ethanol", "acetonitrile", "acetone", "toluene"])
    instr_res = ResourceObject("plate-reader-0", "instrument")
    instr_lease = leases.acquire(instr_res.resource_id, ttl_s=120.0, tag="wet")
    assert instr_lease is not None
    # single-holder semantics: a second contender is denied while we hold it
    assert leases.acquire(instr_res.resource_id, ttl_s=120.0, tag="rival") is None
    try:
        wet_obs, wet_result = run_wet_leg(exp_wet, otp, port=reader)
    finally:
        leases.release(instr_lease)
    # released -> re-acquirable (lease is not leaked)
    reacq = leases.acquire(instr_res.resource_id, ttl_s=120.0, tag="after")
    assert reacq is not None
    leases.release(reacq)

    assert wet_result.outcome.value == "SUCCEEDED"
    store.save_experiment(exp_wet)
    QCPolicy(_qc_runner((0.0, 1.2)), TrustPolicy(0.6, 0.3)).judge(
        store, wet_obs, exp_wet)

    for o in wet_obs:
        assert o.trust is not TrustLevel.PENDING           # adjudicated
        assert o.result.metric == WET_METRIC
        assert o.raw_ref.kind == "wet"
        assert o.instrument_meta.instrument_id.startswith("plate_reader_sim@")
        assert o.instrument_meta.engine == "plate_reader_sim"   # formal engine
        # wet readings have no on-disk product: uri/sha honestly empty/None
        # (no fabricated uri), letter 060.
        assert o.raw_ref.uri == "" and o.raw_ref.sha256 is None

    # ---- ONE run store carries BOTH channels ----------------------------------
    all_obs = store.list_observations()
    kinds = {o.raw_ref.kind for o in all_obs}
    assert kinds == {"dry", "wet"}
    assert len(all_obs) == len(dry_obs) + len(wet_obs)
    # exactly one events.jsonl, with routing + qc_report for BOTH legs
    routing = store.read_events("routing")
    assert len(routing) == len(dry_obs) + len(wet_obs)
    assert len(store.read_events("qc_report")) == 2       # one per leg

    # ---- SCORING-side truth harvest (non-OS path) -> save_truth ---------------
    truth = harvest_truth(port=reader)
    assert truth and all("true_response" in t for t in truth)
    # driver readings never carried truth
    assert not any(hasattr(r, "true_response") for r in wet_result.readings)
    truth_path = store.save_truth(round_id=0, records=truth)
    assert truth_path.exists()
    saved = [json.loads(line) for line in
             truth_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(saved) == len(truth) and all("true_response" in t for t in saved)

    # custody chain is fully traced for every wet reading (chain of custody, G3)
    for rd in wet_result.readings:
        rec = wet_result.custody.trace(rd.sample_id)
        assert all(rec.segments_complete().values())


# ============================================================ QC adjudicates a wet null

def test_wet_dropout_is_visible_and_adjudicated(reader, tmp_path):
    """A dropout well surfaces as a visible null reading; QC/adjudicate still runs
    on it (never silently dropped), proving the wet failure path reaches trust."""
    store = RunStore(tmp_path / "run")
    exp_wet, otp = _wet_experiment_and_protocol(
        ["ethanol", "acetonitrile", "acetone", "toluene"])
    drop_well = otp.wells[1].well_id
    _send(reader, {"cmd": "inject", "dropout_wells": [drop_well]})

    wet_obs, wet_result = run_wet_leg(exp_wet, otp, port=reader)
    assert wet_result.outcome.value == "SUCCEEDED"        # dropout is not a hard abort

    store.save_experiment(exp_wet)
    QCPolicy(_qc_runner((0.0, 1.2)), TrustPolicy(0.6, 0.3)).judge(
        store, wet_obs, exp_wet)

    # every observation (including the null-valued dropout) got adjudicated
    assert all(o.trust is not TrustLevel.PENDING for o in wet_obs)
    dropped = [o for o in wet_obs if o.layout_meta.well_id == drop_well]
    assert len(dropped) == 1 and dropped[0].result.value is None
    # one routing event per well, one qc_report -- the null was not silently lost
    assert len(store.read_events("routing")) == len(wet_obs)
    assert len(store.read_events("qc_report")) == 1


# ============================================================ domain YAML structure

def test_domain_yaml_structural():
    """The YAML is structurally valid; the ONLY thing blocking load_domain is the
    unregistered dry adapter (session B's ADAPTER_REGISTRY diff, see handoff)."""
    from pathlib import Path

    import yaml

    from expos.domain import DomainConfig, load_domain

    root = Path(__file__).resolve().parents[1]
    path = root / "domains" / "solvent_screen.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    # structurally valid against the kernel schema
    cfg = DomainConfig.model_validate(raw)
    assert cfg.name == "solvent_screen"
    assert cfg.objective.metric == WET_METRIC
    assert len(cfg.design_space.variables) == 4
    assert cfg.design_space.var("solvent").kind == "categorical"
    assert len(cfg.design_space.var("solvent").choices) == 8
    assert cfg.budget.rounds_total == 2                    # G5: two rounds
    assert 0.0 < cfg.trust.quarantine_low < cfg.trust.suspect_high < 1.0

    # W9 landed: pyscf_dry is now a registered adapter -- load_domain succeeds
    cfg2 = load_domain(path)
    assert cfg2.adapter == "pyscf_dry"
