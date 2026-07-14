"""M16 acceptance suite skeleton — the five gates of docs/M16_MIN_LOOP.md §0.

This is the discriminative acceptance layer for the minimal Dry-Wet-Agent loop
(MCL). It restates the acceptance口径 (verdict shape) for each gate ONE more
time, end-to-end, rather than re-implementing the underlying mechanisms (those
live in the W3/W4/W6 unit suites and are only *referenced* here).

Landed vs pending (the M16 burndown ledger):
  * Testable NOW: G1 knowledge-compile determinism + reverse-claim flip (W6),
    G2 dry job lifecycle + kill classification (W3), G3 wet seven-concern
    fault-injection matrix + custody chain (W4), G4 both legs under one QC path
    (W5).
  * Pending (skip stubs with an explicit reason — remove the skip when the work
    lands): G1 "agent proposal follows knowledge" (W9 agent consumption face),
    G4 dry->wet promotion-decision-into-events (W7), G5 the whole `--loop mcl`
    two-round entry (W9). Their assertion bodies are written out below so the
    stub doubles as the spec for the pending work.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import threading
import time

import pytest

from expos.adapters.dry.adapter import PySCFDryAdapter
from expos.adapters.dry.ingest import dry_raw_to_observations
from expos.adapters.dry.spec import JobSpec
from expos.adapters.wet import sim_reader
from expos.adapters.wet.driver import GoalState, WetDriver
from expos.adapters.wet.ot_protocol import ValidationError, compile_and_validate
from expos.adapters.wet.protocol_spec import (
    ProtocolSpec,
    SolventSample,
    make_gradient_spec,
)
from expos.adapters.wet.screen import (
    DRY_METRIC,
    WET_METRIC,
    compile_wet,
    layout_from_protocol,
    run_wet_leg,
)
from expos.kernel.knowledge import compile_knowledge
from expos.kernel.lifecycle import TrustPolicy
from expos.kernel.objects import (
    Budget,
    Candidate,
    Control,
    DesignProvenance,
    DesignSpace,
    ExecutionReq,
    ExperimentObject,
    HypothesisObject,
    HypothesisStatus,
    LayoutAssignment,
    Objective,
    TrustLevel,
    VariableDef,
    WellAssignment,
)
from expos.kernel.store import RunStore
from expos.qc.checks import run_qc
from expos.qc.policy import QCPolicy
from expos.scheduler import JobState, SubprocessBackend

_TERMINAL = {JobState.SUCCEEDED, JobState.FAILED, JobState.TIMEOUT}


# ======================================================================
# shared fixtures / helpers
# ======================================================================


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_port(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"reader on port {port} did not come up")


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


@pytest.fixture
def dry_adapter(tmp_path):
    return PySCFDryAdapter(jobs_root=tmp_path / "dry_jobs", poll_interval_s=0.1)


def _poll_until_terminal(adapter, run, wall_s=90.0):
    deadline = time.time() + wall_s
    while True:
        st = adapter.poll(run)
        if st in _TERMINAL:
            return st
        assert time.time() < deadline, "job did not reach a terminal state in time"
        time.sleep(0.1)


def _dry_spec(**kw) -> JobSpec:
    base = dict(job_id="acc:0:A1", well_id="A1", cand_id="cand_1", solvent="water")
    base.update(kw)
    return JobSpec(**base)


def _design_space() -> DesignSpace:
    return DesignSpace(
        name="solvent_screen",
        variables=[
            VariableDef(
                name="solvent",
                kind="categorical",
                choices=[
                    "water",
                    "ethanol",
                    "acetonitrile",
                    "acetone",
                    "toluene",
                    "hexane",
                    "methanol",
                    "dmso",
                ],
            ),
            VariableDef(
                name="concentration",
                kind="continuous",
                low=0.5,
                high=20.0,
                transform="log",
                unit="mM",
            ),
            VariableDef(
                name="temperature", kind="continuous", low=15.0, high=45.0, unit="C"
            ),
            VariableDef(
                name="incubation_time",
                kind="continuous",
                low=5.0,
                high=120.0,
                unit="min",
            ),
        ],
    )


def _conditions(solvent: str) -> dict:
    return {
        "solvent": solvent,
        "concentration": 5.0,
        "temperature": 25.0,
        "incubation_time": 30.0,
    }


def _dry_experiment() -> ExperimentObject:
    return ExperimentObject(
        exp_id="acc_dry",
        round_id=0,
        domain="solvent_screen",
        objective=Objective(name="polarity", metric=DRY_METRIC),
        design_space=_design_space(),
        active_vars=["solvent"],
        candidates=[
            Candidate(cand_id="cand_ethanol", params=_conditions("ethanol")),
            Candidate(cand_id="cand_hexane", params=_conditions("hexane")),
        ],
        layout=LayoutAssignment(
            rows=1,
            cols=2,
            seed=0,
            wells=[
                WellAssignment(well_id="A1", row=0, col=0, cand_id="cand_ethanol"),
                WellAssignment(well_id="A2", row=0, col=1, cand_id="cand_hexane"),
            ],
        ),
        budget=Budget(wells_total=96, rounds_total=2),
        execution_req=ExecutionReq(adapter="pyscf_dry"),
        provenance=DesignProvenance(generator="test"),
    )


def _wet_experiment_and_protocol(solvents: list[str]):
    cands = [Candidate(cand_id=f"cand_{s}", params=_conditions(s)) for s in solvents]
    controls = [
        Control(control_id="ctl0", kind="sentinel", params=_conditions("acetonitrile"))
    ]
    exp_noptr = ExperimentObject(
        exp_id="acc_wet",
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
    def runner(exp, obs_list, history):
        same = [o for o in (history or []) if o.exp_id == exp.exp_id]
        return run_qc(
            exp, obs_list, same or None, metric_range=metric_range, moran_perm=99
        )

    return runner


def _hyp(hid, statement, refs, status=HypothesisStatus.OPEN) -> HypothesisObject:
    return HypothesisObject(
        hypothesis_id=hid, statement=statement, evidence_refs=refs, status=status
    )


def _claim(cid, status) -> dict:
    # Ledger-shaped claim dict: compile_knowledge requires claim_id + status.
    return {"claim_id": cid, "status": status}


# ======================================================================
# G1 — Agent 闭环: knowledge is a compiled, discriminatively-consumed product
# ======================================================================


def test_g1_frozen_knowledge_is_bit_for_bit_identical():
    """Freeze the knowledge (same claims + hypotheses) -> the compiled
    fingerprint is bit-for-bit identical, and input ORDER cannot perturb it
    (the determinism substrate for 'round-2 proposals identical to round-1')."""
    claims = [_claim("c_polar", "supported"), _claim("c_nonpolar", "rejected")]
    hyps = [
        _hyp("hyp_polar", "polar solvents respond higher", ["c_polar"]),
        _hyp("hyp_nonpolar", "nonpolar solvents respond higher", ["c_nonpolar"]),
    ]

    v1 = compile_knowledge(claims, hyps)
    v2 = compile_knowledge(claims, hyps)
    v3 = compile_knowledge(list(reversed(claims)), list(reversed(hyps)))

    assert v1.knowledge_fingerprint == v2.knowledge_fingerprint
    assert v1.knowledge_fingerprint == v3.knowledge_fingerprint  # order-insensitive
    eff = {h.hypothesis_id: h.effective_status for h in v1.hypotheses}
    assert eff["hyp_polar"] is HypothesisStatus.SUPPORTED
    assert eff["hyp_nonpolar"] is HypothesisStatus.REJECTED


def test_g1_reverse_claim_flips_status_and_fingerprint():
    """Inject a REVERSED claim (supported -> rejected) on the referenced evidence
    -> the affected hypothesis's effective status flips SUPPORTED -> REJECTED and
    the knowledge fingerprint changes (no performative feedback — C2 lesson)."""
    hyps = [_hyp("hyp_polar", "polar solvents respond higher", ["c_polar"])]

    supported = compile_knowledge([_claim("c_polar", "supported")], hyps)
    rejected = compile_knowledge([_claim("c_polar", "rejected")], hyps)

    assert supported.knowledge_fingerprint != rejected.knowledge_fingerprint
    assert supported.hypotheses[0].effective_status is HypothesisStatus.SUPPORTED
    assert rejected.hypotheses[0].effective_status is HypothesisStatus.REJECTED


# -- whole-loop G1/G5 machinery (W9 landed: expos/mcl.py) -----------------
#
# One REAL two-round MCL run (PySCF + reader) is shared module-wide as the
# baseline; G1 does one more frozen run + one reversed-claim run. Comparison
# is restricted to the DECISION PLANE (knowledge fingerprint, proposal order,
# promoted set) -- execution-side fields (deny_reason under load, obs counts)
# are honestly non-deterministic (letter 069 boundary) and excluded.

_DOMAIN_YAML = "domains/solvent_screen.yaml"


def _decision_plane(out_dir):
    from expos.kernel.store import RunStore

    store = RunStore(out_dir, lock=False)
    fps = [e["payload"]["fingerprint"]
           for e in store.read_events("knowledge_updated")]
    proposals = [
        (d.round_id, tuple(d.content["candidates"]), tuple(d.content["basis"]))
        for d in store.list_decisions()
        if d.kind.value == "prior_proposal"
    ]
    promoted = [
        (e["payload"]["round_id"],
         tuple(p["cand_id"] for p in e["payload"]["promoted"]))
        for e in store.read_events("promotion_decision")
    ]
    return {"fps": fps, "proposals": sorted(proposals), "promoted": sorted(promoted)}


@pytest.fixture(scope="module")
def mcl_baseline(tmp_path_factory):
    from expos.mcl import run_mcl_loop

    out = tmp_path_factory.mktemp("mcl_baseline") / "run"
    summary = run_mcl_loop(_DOMAIN_YAML, rounds=2, seed=7, out_dir=out)
    return {"summary": summary, "out": out}


def test_g1_agent_proposal_follows_knowledge(mcl_baseline, tmp_path):
    """WHOLE-LOOP G1: freeze the knowledge -> the decision plane of a second
    run is bit-identical; reverse a claim -> the proposal ordering (and hence
    the promoted set) provably changes. Discriminative both ways."""
    from expos.mcl import _default_claims, run_mcl_loop

    frozen_out = tmp_path / "frozen"
    run_mcl_loop(_DOMAIN_YAML, rounds=2, seed=7, out_dir=frozen_out)
    base = _decision_plane(mcl_baseline["out"])
    frozen = _decision_plane(frozen_out)
    assert frozen == base, "frozen knowledge must yield a bit-identical decision plane"

    reversed_claims = [dict(c) for c in _default_claims()]
    reversed_claims[0]["status"] = "rejected"   # flip: polar-higher supported -> rejected
    reversed_claims[1]["status"] = "supported"  # its converse becomes supported
    flipped_out = tmp_path / "flipped"
    run_mcl_loop(_DOMAIN_YAML, rounds=2, seed=7, out_dir=flipped_out,
                 claims=reversed_claims)
    flipped = _decision_plane(flipped_out)
    assert flipped["fps"] != base["fps"], "reversed claim must change the fingerprint"
    assert flipped["proposals"] != base["proposals"], (
        "reversed claim must re-steer the proposal -- knowledge consumption is "
        "real, not performative"
    )


# ======================================================================
# G2 — Dry 真执行: full job lifecycle + kill classification, end-to-end
# ======================================================================


def test_g2_dry_job_lifecycle_events_complete(dry_adapter):
    """Acceptance口径 for the dry leg: a real out-of-process PySCF job records the
    full lifecycle (accepted -> spawned -> terminal) and recovers its declared
    product through the artifact channel (provenance on the formal positions)."""
    backend = SubprocessBackend()
    run = dry_adapter.submit(_dry_spec(), backend)
    st = _poll_until_terminal(dry_adapter, run)
    assert st is JobState.SUCCEEDED

    states = [e.to_state for e in run.events]
    assert states == ["PENDING", "RUNNING", "SUCCEEDED"]  # full lifecycle logged

    dry_raw, failure = dry_adapter.collect(run)
    assert failure is None and dry_raw is not None
    assert dry_raw.engine == "pyscf" and dry_raw.converged is True
    assert dry_raw.raw_uri.endswith("result.json") and os.path.exists(dry_raw.raw_uri)


def test_g2_dry_job_kill_is_classified_not_a_naked_crash(dry_adapter):
    """Kill a dry job mid-flight -> the failure taxonomy CATCHES it (a classified
    terminal outcome), the driving loop never sees a naked crash. This is the
    core value of out-of-process execution."""
    backend = SubprocessBackend()
    run = dry_adapter.submit(_dry_spec(sleep_s=30.0, timeout_s=120.0), backend)
    assert dry_adapter.poll(run) is JobState.RUNNING
    time.sleep(2.0)  # get past imports into sleep

    os.kill(run.handle.describe()["pid"], signal.SIGKILL)

    st = _poll_until_terminal(dry_adapter, run)
    assert st is JobState.FAILED
    dry_raw, failure = dry_adapter.collect(run)
    assert dry_raw is None and failure is not None
    assert failure.reason == "signal" and failure.retryable is True
    assert [e.to_state for e in run.events][-1] == "FAILED"


# ======================================================================
# G3 — Wet 真执行: seven-concern fault-injection matrix + custody chain
# ======================================================================
# One assertion per concern; the mechanisms live in test_w8_wet_stack — here we
# only re-assert that each of the seven G3 concerns leaves an event trace.


def _kinds(events) -> set:
    return {e["kind"] for e in events}


def test_g3_concern_protocol_validation(reader):
    """(1) protocol validation — the real opentrons stack rejects an infeasible
    spec before any reader interaction; a valid spec compiles + validates."""
    good = compile_and_validate(make_gradient_spec(n_samples=4, n_controls=1))
    assert good.validated and len(good.wells) == 5
    with pytest.raises(ValidationError):
        compile_and_validate(
            ProtocolSpec(samples=[SolventSample("bad", target_polarity=0.99)])
        )


def test_g3_concern_health_check(reader):
    """(2) driver health check — a normal run emits a health event."""
    spec = make_gradient_spec(n_samples=2, n_controls=0)
    otp = compile_and_validate(spec)
    drv = WetDriver(port=reader, exp_id="g3h", round_id=0)
    drv.submit_goal(otp)
    res = drv.run()
    assert res.outcome is GoalState.SUCCEEDED
    assert "health" in _kinds(res.events)


def test_g3_concern_resource_reservation(reader):
    """(3) resource reservation — the single-instrument lease is acquired then
    released (its acquire/release both leave a trace)."""
    spec = make_gradient_spec(n_samples=2, n_controls=0)
    otp = compile_and_validate(spec)
    drv = WetDriver(port=reader, exp_id="g3r", round_id=0)
    drv.submit_goal(otp)
    res = drv.run()
    kinds = _kinds(res.events)
    assert "lease_acquired" in kinds and "lease_released" in kinds


def test_g3_concern_calibration(reader):
    """(4) calibration — a run with calibration emits a calibration event."""
    spec = make_gradient_spec(n_samples=2, n_controls=0)
    otp = compile_and_validate(spec)
    drv = WetDriver(port=reader, exp_id="g3c", round_id=0)
    drv.submit_goal(otp)
    res = drv.run(calibrate=True)
    assert "calibration" in _kinds(res.events)


def test_g3_concern_timeout_and_retry(reader):
    """(5) timeout + retry — inject a slow device; the bounded retry budget is
    exhausted and the concern leaves measure_retry + budget-exhausted traces."""
    _send(reader, {"cmd": "inject", "slow_ms": 5000})
    otp = compile_and_validate(make_gradient_spec(n_samples=2, n_controls=0))
    drv = WetDriver(port=reader, exp_id="g3t", round_id=0, timeout_s=0.3, max_retries=3)
    drv.submit_goal(otp)
    res = drv.run(calibrate=False)
    assert res.outcome is GoalState.ABORTED and "timeout" in (res.reason or "")
    kinds = _kinds(res.events)
    assert "measure_retry" in kinds and "measure_budget_exhausted" in kinds


def test_g3_concern_device_failure(reader):
    """(6) device failure handling — inject more device errors than the retry
    budget; the run aborts with a device_error classification + retry traces."""
    _send(reader, {"cmd": "inject", "error_next": 10})
    otp = compile_and_validate(make_gradient_spec(n_samples=1, n_controls=0))
    drv = WetDriver(port=reader, exp_id="g3d", round_id=0, timeout_s=1.0, max_retries=3)
    drv.submit_goal(otp)
    res = drv.run(calibrate=False)
    assert res.outcome is GoalState.ABORTED and "device_error" in (res.reason or "")
    assert "measure_retry" in _kinds(res.events)


def test_g3_concern_sample_identity_custody(reader):
    """(7) sample identity — a forged sample_id is rejected with a custody
    violation, and a healthy reading's custody chain is four-segment traceable
    in ONE trace() call (chain of custody, G3)."""
    otp = compile_and_validate(make_gradient_spec(n_samples=2, n_controls=0))
    drv = WetDriver(port=reader, exp_id="g3s", round_id=0)

    # forged sample_id -> rejected + custody_violation event
    plan = otp.wells[0]
    forged = {
        "sample_id": "SMP-CND-GHOST",
        "well_id": plan.well_id,
        "value": 0.9,
        "seq": 1,
        "status": "ok",
    }
    reading = drv._ingest_reading(otp, otp.custody, plan, forged)
    assert reading.status == "rejected" and reading.value is None
    assert "custody_violation" in _kinds(drv.events)

    # a real end-to-end run -> every reading's custody chain is fully traced
    drv2 = WetDriver(port=reader, exp_id="g3s2", round_id=0)
    drv2.submit_goal(otp)
    res = drv2.run()
    assert res.outcome is GoalState.SUCCEEDED
    for rd in res.readings:
        rec = res.custody.trace(rd.sample_id)  # one command -> full trace
        segs = rec.segments_complete()
        assert set(segs) == {"protocol", "deck", "measurement", "raw"}
        assert all(segs.values()), (rd.sample_id, segs)


# ======================================================================
# G4 — 同一 Runtime 串联: both legs adjudicated by ONE QC path in ONE run
# ======================================================================


def test_g4_both_legs_under_one_qc_path(reader, dry_adapter, tmp_path):
    """Acceptance口径 for G4 (referenced from test_w8_domain_e2e): the dry leg and
    the wet leg land in ONE RunStore and are adjudicated by the SAME QC/trust
    path (one adjudicate route per observation, one qc_report per leg)."""
    store = RunStore(tmp_path / "run")

    # dry leg (2 real PySCF jobs)
    exp_dry = _dry_experiment()
    dry_result = dry_adapter.run(exp_dry, backend=SubprocessBackend())
    assert dry_result.n_jobs == 2 and len(dry_result.dry_raws) == 2
    store.save_experiment(exp_dry)
    dry_obs, _prov = dry_raw_to_observations(exp_dry, dry_result.dry_raws)
    QCPolicy(_qc_runner((0.0, 10.0)), TrustPolicy(0.6, 0.3)).judge(
        store, dry_obs, exp_dry
    )

    # wet leg (full plate-reader flow)
    exp_wet, otp = _wet_experiment_and_protocol(
        ["ethanol", "acetonitrile", "acetone", "toluene"]
    )
    wet_obs, wet_result = run_wet_leg(exp_wet, otp, port=reader)
    assert wet_result.outcome is GoalState.SUCCEEDED
    store.save_experiment(exp_wet)
    QCPolicy(_qc_runner((0.0, 1.2)), TrustPolicy(0.6, 0.3)).judge(
        store, wet_obs, exp_wet
    )

    # both legs adjudicated (no observation left PENDING) by the SAME path
    assert all(o.trust is not TrustLevel.PENDING for o in dry_obs + wet_obs)

    # one run store, both channels, one routing event per observation
    all_obs = store.list_observations()
    assert {o.raw_ref.kind for o in all_obs} == {"dry", "wet"}
    assert len(all_obs) == len(dry_obs) + len(wet_obs)
    assert len(store.read_events("routing")) == len(dry_obs) + len(wet_obs)
    assert len(store.read_events("qc_report")) == 2  # one per leg


def test_g4_dry_to_wet_promotion_is_recorded_evidence(tmp_path):
    """The Dry->Wet candidate promotion must be a RECORDED evidence decision
    (who was promoted, on what channel basis, who was denied and why) in the
    event log. Exercises the landed W7 API directly (decide + emit helper);
    the run_loop wiring point itself is asserted at G5 (W9)."""
    from expos.kernel.store import RunStore
    from expos.planner.promotion import (
        DryCandidateView,
        PromotionBudget,
        WetCostEstimate,
        decide,
        emit_promotion_decision,
    )

    cost = WetCostEstimate(n_transfers=1, duration_s=5.0)
    cands = [
        DryCandidateView(cand_id="cand_ok", converged=True, in_window=True,
                         acquisition=0.9, wet_cost=cost, failure_detail=None),
        DryCandidateView(cand_id="cand_bad", converged=False, in_window=True,
                         acquisition=0.8, wet_cost=cost, failure_detail=None),
    ]
    budget = PromotionBudget(top_k=1, max_transfers_total=10,
                             max_duration_s_total=60.0, risk_threshold=0.5)
    decision = decide(cands, None, "fp-g4-acceptance", budget)

    store = RunStore(tmp_path / "g4_promo", lock=True)
    emit_promotion_decision(store, round_id=0, decision=decision)

    promo = store.read_events("promotion_decision")
    assert promo, "promotion must be recorded, not implicit"
    payload = promo[0]["payload"]
    # decision provenance: which knowledge state it consumed (G1 hook)
    assert payload["knowledge_fingerprint"] == "fp-g4-acceptance"
    # promoted side carries per-candidate channel basis (no scalar collapse)
    promoted_ids = [p["cand_id"] for p in payload["promoted"]]
    assert promoted_ids == ["cand_ok"]
    assert "basis" in payload["promoted"][0]
    # denied side is loud: every cull carries a reason (no silent edge)
    denied = {d["cand_id"]: d for d in payload["denied"]}
    assert denied["cand_bad"]["deny_reason"] == "gate_convergence"
    # payload validation gate (batch-3 infrastructure) sees no violations
    store.read_events(validate=True)
    assert not store.last_payload_violations


# ======================================================================
# G5 — 整环: `--loop mcl` two rounds, zero manual intervention
# ======================================================================


def test_g5_loop_mcl_two_rounds_end_to_end(mcl_baseline):
    """The whole loop for two rounds with zero manual intervention (W9 landed):
    (a) round-2 proposal basis references claim ids already carried by round-1's
    knowledge_updated; (b) no human override was consumed; (c) run_stop is a
    clean success; (d) the event chain carries every M16 stage for both rounds."""
    from expos.kernel.store import RunStore

    summary, out = mcl_baseline["summary"], mcl_baseline["out"]
    assert summary["rounds_completed"] == 2
    assert summary["n_dry"] > 0 and summary["n_wet"] > 0
    store = RunStore(out, lock=False)

    # (a) round-2 proposal basis <- claim ids in round-1 knowledge_updated
    from expos.mcl import _default_claims

    ku = store.read_events("knowledge_updated")
    assert len(ku) == 2
    assert ku[0]["payload"]["fingerprint"] == ku[1]["payload"]["fingerprint"], (
        "frozen substrate: round-2 consumed the same knowledge state round-1 "
        "recorded"
    )
    props = [d for d in store.list_decisions()
             if d.kind.value == "prior_proposal" and d.round_id == 1]
    assert props, "round-2 proposal must exist"
    basis = set(props[0].content["basis"])
    ledger_ids = {c["claim_id"] for c in _default_claims()}
    assert basis and basis <= ledger_ids, (
        "round-2 proposal basis must reference claim ids from the ledger the "
        "round-1 knowledge_updated event recorded"
    )
    assert props[0].content["knowledge_fingerprint"] == ku[0]["payload"]["fingerprint"]

    # (b) zero manual intervention
    overrides = [e for e in store.read_events("reclassification")
                 if e["payload"].get("actor") == "human"]
    assert not overrides

    # (c) clean terminal state
    run_stops = store.read_events("run_stop")
    assert run_stops and run_stops[-1]["payload"]["exit_status"] == "success"

    # (d) full stage chain, both rounds, zero payload violations
    assert len(store.read_events("promotion_decision")) == 2
    assert len(store.read_events("qc_report")) >= 4  # two legs x two rounds
    store.read_events(validate=True)
    assert not store.last_payload_violations


# ======================================================================
# Axiom 7 as an ACCEPTANCE asset (INDEX_M16_AGENT borrowing #1):
# "validated != enacted" -- an agent actor invoking adjudication verbs must
# raise, structurally, not by prompt discipline. Six surveyed science-agent
# frameworks lack this separation; here it is a machine-checked gate.
# ======================================================================


def test_axiom7_agent_cannot_adjudicate(tmp_path):
    """Direct adjudication calls with actor=AGENT must raise LifecycleError:
    kill-assertion for the structural authority separation (axiom 7)."""
    from expos.kernel.lifecycle import (
        LifecycleError,
        reclassify,
        validate_proposal,
    )
    from expos.kernel.objects import (
        Actor,
        DecisionKind,
        DecisionRecord,
        Routing,
        TrustLevel,
    )
    from expos.kernel.store import RunStore

    store = RunStore(tmp_path / "axiom7", lock=True)
    proposal = DecisionRecord(
        round_id=0, actor=Actor.AGENT, kind=DecisionKind.ACTION_PROPOSAL,
        content={"action": "REMEASURE", "cand_id": "cand_x"},
    )
    store.append_decision(proposal)

    with pytest.raises(LifecycleError):
        validate_proposal(store, proposal, accepted=True, actor=Actor.AGENT,
                          reason="agent trying to accept its own proposal")

    with pytest.raises(LifecycleError):
        reclassify(store, obs_id="obs_nonexistent", new_trust=TrustLevel.TRUSTED,
                   new_routing=Routing.TO_RESPONSE_MODEL, actor=Actor.AGENT,
                   reason="agent trying to flip a verdict")
