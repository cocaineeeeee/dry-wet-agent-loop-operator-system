"""W8 real-machine seam — RecoveryPolicy + seven-state machine + driver contract.

Discriminative-first (the W8 pattern): every guard has a test that turns red if the
guard is removed (kill-note inline). The regression anchor is that the DEFAULT
policy (NeverRecover) reproduces the six-state behaviour bit-for-bit — that is
covered by the whole existing test_w8_wet_stack suite; here §2 adds one explicit
same-behaviour assertion, and the rest exercises the new surface:

  §1  seven-state transition matrix — every illegal edge is rejected loudly.
  §2  NeverRecover regression anchor — a defined error still aborts, unchanged.
  §3  WaitForRecovery — enter AWAITING_RECOVERY -> explicit recover() -> success;
      -> explicit abandon() -> ABORTED; the lease stays HELD while paused.
  §4  AssumeFalsePositive — only a false-positive-prone code continues; any other
      defined error aborts.
  §5  undefined failure — bypasses the policy entirely (fail-closed), even under a
      recovering policy.
  §6  RETRY_STEP action — a policy returning RETRY_STEP re-drives the step, then
      fail-closes when the bounded recovery budget is exhausted.
  §7  driver contract verbs — capabilities / dry_run / estimate_* / failure_modes.
  §8  labware externalisation — a valid external def loads; a malformed def is
      rejected loudly; the default def reproduces the hard-coded well order.
"""

from __future__ import annotations

import dataclasses
import json
import socket
import threading
import time

import pytest

from expos.adapters.wet import sim_reader
from expos.adapters.wet.driver import (
    GoalState,
    ValidationReport,
    WetDriver,
    WetDriverError,
    _LEGAL_TRANSITIONS,
)
from expos.adapters.wet.labware import (
    DEFAULT_PLATE_PATH,
    LabwareError,
    load_labware,
    load_labware_doc,
)
from expos.adapters.wet.ot_protocol import compile_and_validate
from expos.adapters.wet.protocol_spec import all_wells, make_gradient_spec
from expos.adapters.wet.recovery import (
    AssumeFalsePositive,
    FailureDetail,
    NeverRecover,
    RecoveryAction,
    WaitForRecovery,
)


# ---- fixtures ---------------------------------------------------------------

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"reader on port {port} did not come up")


def send(port: int, obj: dict, timeout: float = 2.0) -> dict:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps(obj) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                raise ConnectionError("closed without reply")
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode())


@pytest.fixture
def reader():
    port = _free_port()
    srv = sim_reader.serve("127.0.0.1", port, seed=7, noise_sd=0.0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _wait_port(port)
    yield port
    srv.shutdown()
    srv.server_close()


# ======================================================================
# §1 — seven-state transition matrix
# ======================================================================

_ALL_STATES = list(GoalState)


def test_transition_table_is_exactly_seven_states():
    """The state machine is SEVEN states: the six original + AWAITING_RECOVERY."""
    assert len(_ALL_STATES) == 7
    assert GoalState.AWAITING_RECOVERY in _ALL_STATES


def test_every_illegal_transition_is_rejected_loudly():
    """Matrix discriminator: for every (from,to) pair NOT in the legal table,
    _transition raises WetDriverError. KILL: drop the legality check in
    _transition and this whole matrix goes green-to-red."""
    drv = WetDriver()
    for src in _ALL_STATES:
        legal = _LEGAL_TRANSITIONS[src]
        for dst in _ALL_STATES:
            drv.state = src
            if dst in legal:
                drv._transition(dst)          # must NOT raise
                assert drv.state is dst
            else:
                with pytest.raises(WetDriverError, match="illegal wet-driver"):
                    drv._transition(dst)


def test_legal_edges_are_exactly_the_documented_seven_state_graph():
    """Pin the intended graph so an accidental edit to the table is caught."""
    expected = {
        GoalState.ACCEPTED: {GoalState.EXECUTING},
        GoalState.EXECUTING: {
            GoalState.SUCCEEDED, GoalState.ABORTED,
            GoalState.CANCELING, GoalState.AWAITING_RECOVERY,
        },
        GoalState.CANCELING: {GoalState.CANCELED},
        GoalState.AWAITING_RECOVERY: {GoalState.EXECUTING, GoalState.ABORTED},
        GoalState.SUCCEEDED: set(),
        GoalState.ABORTED: set(),
        GoalState.CANCELED: set(),
    }
    assert {k: set(v) for k, v in _LEGAL_TRANSITIONS.items()} == expected


def test_recover_and_abandon_require_awaiting_recovery():
    """recover()/abandon() outside AWAITING_RECOVERY are illegal (loud)."""
    drv = WetDriver()
    otp = compile_and_validate(make_gradient_spec(n_samples=2, n_controls=0))
    drv.submit_goal(otp)
    assert drv.state is GoalState.ACCEPTED
    with pytest.raises(WetDriverError, match="AWAITING_RECOVERY"):
        drv.recover()
    with pytest.raises(WetDriverError, match="AWAITING_RECOVERY"):
        drv.abandon()


# ======================================================================
# §2 — NeverRecover regression anchor
# ======================================================================

def test_neverrecover_is_the_default_policy():
    assert isinstance(WetDriver().policy, NeverRecover)


def test_neverrecover_defined_error_aborts_unchanged(reader):
    """A persistent DEFINED (recoverable) error under the default NeverRecover
    aborts and never enters AWAITING_RECOVERY — the six-state behaviour anchor.
    KILL: if NeverRecover.decide returned anything but ABORT, the run would pause
    or continue and this flips."""
    otp = compile_and_validate(make_gradient_spec(n_samples=3, n_controls=0))
    send(reader, {"cmd": "inject",
                  "error_wells": {otp.wells[1].well_id: "E_DEVICE"}})
    drv = WetDriver(port=reader, max_retries=1)          # default NeverRecover
    drv.submit_goal(otp)
    res = drv.run(calibrate=False)
    assert res.outcome is GoalState.ABORTED
    assert drv.state is GoalState.ABORTED
    assert res.n_failed == len(otp.wells) - 1            # well 0 read, rest failed
    states = [e["state"] for e in res.events if e["kind"] == "action_state"]
    assert GoalState.AWAITING_RECOVERY.value not in states


# ======================================================================
# §3 — WaitForRecovery: pause -> recover / abandon
# ======================================================================

def test_wait_for_recovery_pauses_then_recovers_to_success(reader):
    """Recoverable defined error under WaitForRecovery -> AWAITING_RECOVERY; after
    the fault clears, explicit recover() resumes and the run SUCCEEDS. KILL: remove
    the AWAIT_HUMAN branch and the run would abort instead of pausing."""
    otp = compile_and_validate(make_gradient_spec(n_samples=4, n_controls=0))
    bad = otp.wells[2].well_id
    send(reader, {"cmd": "inject", "error_wells": {bad: "E_DEVICE"}})
    drv = WetDriver(port=reader, policy=WaitForRecovery(), max_retries=1)
    drv.submit_goal(otp)

    paused = drv.run(calibrate=False)
    assert paused.outcome is GoalState.AWAITING_RECOVERY
    assert drv.state is GoalState.AWAITING_RECOVERY
    assert any(e["kind"] == "recovery_awaiting" for e in paused.events)
    # lease is HELD while paused: a competing acquire is refused
    assert not send(reader, {"cmd": "acquire", "holder": "intruder"})["ok"]

    send(reader, {"cmd": "inject", "clear": True})        # human fixes the device
    done = drv.recover()
    assert done.outcome is GoalState.SUCCEEDED
    assert done.n_raw == 4 and done.n_failed == 0
    # the once-failed well now carries a real reading
    assert [r for r in done.readings if r.well_id == bad][0].value is not None


def test_wait_for_recovery_abandon_aborts_with_visible_failures(reader):
    """Explicit abandon() from AWAITING_RECOVERY -> ABORTED, and every unmeasured
    well surfaces as a visible null (never dropped)."""
    otp = compile_and_validate(make_gradient_spec(n_samples=4, n_controls=0))
    send(reader, {"cmd": "inject",
                  "error_wells": {otp.wells[1].well_id: "E_DEVICE"}})
    drv = WetDriver(port=reader, policy=WaitForRecovery(), max_retries=1)
    drv.submit_goal(otp)
    drv.run(calibrate=False)
    assert drv.state is GoalState.AWAITING_RECOVERY

    res = drv.abandon()
    assert res.outcome is GoalState.ABORTED
    assert res.n_raw == 4                                  # all wells accounted for
    assert res.n_failed == 3                               # well 0 read, 1..3 failed
    # lease released on the terminal outcome
    assert send(reader, {"cmd": "acquire", "holder": "next"})["ok"]


def test_wait_for_recovery_non_recoverable_aborts(reader):
    """A DEFINED but NON-recoverable error (missing_sample_id class) never pauses —
    WaitForRecovery.decide returns ABORT for it."""
    fd = FailureDetail.classify("lease_invalid", "lease_invalid")
    assert fd.defined and not fd.recoverable
    assert WaitForRecovery().decide(fd) is RecoveryAction.ABORT


# ======================================================================
# §4 — AssumeFalsePositive: only the flagged code continues
# ======================================================================

def test_assume_false_positive_continues_past_flagged_code(reader):
    """A false-positive-prone code (E_SENSOR) under AssumeFalsePositive is
    adjudicated a false alarm: the run continues, the flagged well lands a VISIBLE
    'assumed_false_positive' null, and the rest read normally."""
    otp = compile_and_validate(make_gradient_spec(n_samples=4, n_controls=0))
    flagged = otp.wells[1].well_id
    send(reader, {"cmd": "inject", "error_wells": {flagged: "E_SENSOR"}})
    drv = WetDriver(port=reader, policy=AssumeFalsePositive(), max_retries=1)
    drv.submit_goal(otp)
    res = drv.run(calibrate=False)
    assert res.outcome is GoalState.SUCCEEDED
    fp = [r for r in res.readings if r.well_id == flagged][0]
    assert fp.status == "assumed_false_positive" and fp.value is None
    # its custody chain is still fully traced (not a silent skip)
    assert all(res.custody.trace(fp.sample_id).segments_complete().values())
    assert any(e["kind"] == "recovery_false_positive" for e in res.events)


def test_assume_false_positive_aborts_on_unflagged_code(reader):
    """An error NOT marked false-positive-prone (E_DEVICE) still ABORTS under
    AssumeFalsePositive. KILL: if the policy ignored the flag and continued on any
    error, this would succeed."""
    otp = compile_and_validate(make_gradient_spec(n_samples=4, n_controls=0))
    send(reader, {"cmd": "inject",
                  "error_wells": {otp.wells[1].well_id: "E_DEVICE"}})
    drv = WetDriver(port=reader, policy=AssumeFalsePositive(), max_retries=1)
    drv.submit_goal(otp)
    res = drv.run(calibrate=False)
    assert res.outcome is GoalState.ABORTED


# ======================================================================
# §5 — undefined failure bypasses the policy (fail-closed)
# ======================================================================

def test_undefined_failure_bypasses_policy_even_under_recovery(reader):
    """An UNDEFINED failure code aborts unconditionally even under WaitForRecovery
    — it never reaches decide(). KILL: route undefined failures through the policy
    and a recovering policy would pause instead of failing closed."""
    otp = compile_and_validate(make_gradient_spec(n_samples=3, n_controls=0))
    send(reader, {"cmd": "inject",
                  "error_wells": {otp.wells[1].well_id: "E_TOTALLY_UNKNOWN"}})
    drv = WetDriver(port=reader, policy=WaitForRecovery(), max_retries=1)
    drv.submit_goal(otp)
    res = drv.run(calibrate=False)
    assert res.outcome is GoalState.ABORTED
    assert drv.state is GoalState.ABORTED
    assert any(e["kind"] == "recovery_bypassed"
               and e["reason"] == "undefined_failure_fail_closed"
               for e in res.events)


def test_failuredetail_classify_marks_defined_vs_undefined():
    """Unit twin of the fail-closed boundary: known code -> defined; unknown ->
    undefined + not recoverable + not false-positive-prone."""
    known = FailureDetail.classify("E_SENSOR", "device_error_budget_exhausted")
    assert known.defined and known.recoverable and known.false_positive_prone
    unknown = FailureDetail.classify("E_NOPE", "device_error")
    assert not unknown.defined
    assert not unknown.recoverable and not unknown.false_positive_prone


# ======================================================================
# §6 — RETRY_STEP action is wired and bounded
# ======================================================================

class _AlwaysRetryStep:
    """Test-only policy: always ask the driver to re-drive the failed step."""

    name = "always_retry_step"

    def decide(self, failure: FailureDetail) -> RecoveryAction:
        return RecoveryAction.RETRY_STEP


def test_retry_step_redrives_then_fail_closes_when_budget_exhausted(reader):
    """A policy returning RETRY_STEP re-drives the well; a persistent fault
    exhausts the bounded recovery budget and fail-closes to ABORTED (no infinite
    loop)."""
    otp = compile_and_validate(make_gradient_spec(n_samples=3, n_controls=0))
    send(reader, {"cmd": "inject",
                  "error_wells": {otp.wells[1].well_id: "E_DEVICE"}})
    drv = WetDriver(port=reader, policy=_AlwaysRetryStep(),
                    max_retries=1, max_recovery_retries=2)
    drv.submit_goal(otp)
    res = drv.run(calibrate=False)
    assert res.outcome is GoalState.ABORTED
    retries = [e for e in res.events if e["kind"] == "recovery_retry_step"]
    assert len(retries) == drv.max_recovery_retries + 1   # tries then gives up
    assert "recovery_exhausted" in (res.reason or "")


# ======================================================================
# §7 — driver contract verbs
# ======================================================================

def test_capabilities_is_machine_readable(reader):
    caps = WetDriver(port=reader).capabilities()
    assert caps["adapter"] == "wet_sim_reader"
    assert caps["channels"] == 1
    assert caps["labware"]["well_count"] == 96
    assert caps["pipette"] == {"min_ul": 20.0, "max_ul": 300.0}
    assert caps["recovery_policy"] == "never_recover"
    codes = {m["code"] for m in caps["failure_modes"]}
    assert {"E_DEVICE", "E_SENSOR", "timeout", "lease_invalid"} <= codes


def test_dry_run_validates_without_the_instrument():
    drv = WetDriver()                                     # no server needed
    good = drv.dry_run(make_gradient_spec(n_samples=4, n_controls=1))
    assert isinstance(good, ValidationReport)
    assert good.ok and good.n_wells == 5 and good.n_transfers > 0
    # an infeasible spec reports ok=False rather than raising
    from expos.adapters.wet.protocol_spec import ProtocolSpec, SolventSample
    bad = drv.dry_run(ProtocolSpec(samples=[SolventSample("x", target_polarity=0.99)]))
    assert not bad.ok and bad.error


def test_estimate_runtime_and_cost_match_the_ledger_model():
    """estimate_runtime/estimate_cost reproduce the ot_protocol duration model, so
    the promotion cost gate and the driver verb agree."""
    from expos.adapters.wet.ot_protocol import execute_simulated
    otp = compile_and_validate(make_gradient_spec(n_samples=4, n_controls=1))
    drv = WetDriver()
    led = execute_simulated(otp)
    assert drv.estimate_runtime(otp) == led["duration_s"]
    cost = drv.estimate_cost(otp)
    assert cost["n_transfers"] == led["n_transfers"] == cost["n_tips"]
    assert cost["duration_s"] == led["duration_s"]
    assert cost["reagent_ul"] > 0


def test_failure_modes_declares_recoverable_and_false_positive_bits():
    modes = {m["code"]: m for m in WetDriver().failure_modes()}
    assert modes["E_SENSOR"]["false_positive_prone"] is True
    assert modes["E_DEVICE"]["false_positive_prone"] is False
    assert modes["E_DEVICE"]["recoverable"] is True
    assert modes["lease_invalid"]["recoverable"] is False


def test_health_check_and_calibrate_verbs(reader):
    drv = WetDriver(port=reader)
    h = drv.health_check()
    assert h["status"] == "healthy"
    # calibrate needs a lease; without one it fails loud
    with pytest.raises(WetDriverError, match="lease"):
        drv.calibrate()


# ======================================================================
# §8 — labware externalisation
# ======================================================================

def test_default_labware_reproduces_hardcoded_well_order():
    """The external plate96.json 'ordering' == the previous hard-coded column-major
    A1,B1,..,H1,A2,.. order — default behaviour is bit-for-bit unchanged."""
    lw = load_labware(DEFAULT_PLATE_PATH)
    assert lw.all_wells() == all_wells()
    assert len(lw.all_wells()) == 96
    assert lw.is_edge("A1") and not lw.is_edge("B2")
    assert lw.capacity_of("A1") == 360.0


def test_missing_labware_file_is_rejected_loudly(tmp_path):
    with pytest.raises(LabwareError, match="not found"):
        load_labware(tmp_path / "no_such_plate.json")


def test_malformed_labware_definition_is_rejected_loudly():
    """A def whose 'ordering' disagrees with 'wells', or is missing a required key,
    or has a bad geometry cell, is rejected — never a silent fallback to a default
    plate. KILL: drop the _validate checks and these bad defs would load."""
    base_well = {"shape": "circular", "totalLiquidVolume": 100.0,
                 "depth": 5.0, "x": 1.0, "y": 1.0, "z": 0.0}
    good = {
        "schemaVersion": 1, "namespace": "t",
        "metadata": {"displayName": "t"},
        "parameters": {"loadName": "t2", "wellChannels": 1, "wellCapacityUl": 90.0},
        "dimensions": {"xDimension": 1.0, "yDimension": 1.0, "zDimension": 1.0},
        "ordering": [["A1", "B1"]],
        "wells": {"A1": dict(base_well), "B1": dict(base_well)},
    }
    assert load_labware_doc(good).all_wells() == ["A1", "B1"]   # sanity: good loads

    # (a) ordering references a well with no geometry
    bad_orphan = json.loads(json.dumps(good))
    bad_orphan["ordering"] = [["A1", "B1", "C1"]]
    with pytest.raises(LabwareError, match="disagree"):
        load_labware_doc(bad_orphan)

    # (b) missing required top-level key
    bad_missing = json.loads(json.dumps(good))
    del bad_missing["dimensions"]
    with pytest.raises(LabwareError, match="missing required key"):
        load_labware_doc(bad_missing)

    # (c) a well missing a geometry field
    bad_geom = json.loads(json.dumps(good))
    del bad_geom["wells"]["A1"]["depth"]
    with pytest.raises(LabwareError, match="geometry field"):
        load_labware_doc(bad_geom)

    # (d) ragged ordering columns
    bad_ragged = json.loads(json.dumps(good))
    bad_ragged["ordering"] = [["A1"], ["B1", "C1"]]
    with pytest.raises(LabwareError, match="ragged|disagree"):
        load_labware_doc(bad_ragged)


def test_sim_reader_serves_labware_capabilities(reader):
    """The reader (device side) also consumes the external labware, exposing it via
    a 'capabilities' command — the client no longer hard-codes the plate shape."""
    caps = send(reader, {"cmd": "capabilities"})
    assert caps["ok"] and caps["channels"] == 1
    assert caps["labware"]["well_count"] == 96
    assert caps["metric"] == "solvent_response"


# ======================================================================
# §9 — paused-state cancel ≡ abandon() (112 即裁二)
# ======================================================================

# Fields whose value is a property of the shared stateful reader server (wall clock
# / lease counter), NOT of the driver's cancel-vs-abandon logic -- excluded so two
# independent drives on the one server compare on the fields the driver controls.
_SERVER_FIELDS = frozenset({"ts", "uptime_s", "meas_since_cal", "lease_id"})


def _strip_server(events: list[dict]) -> list[dict]:
    return [{k: v for k, v in e.items() if k not in _SERVER_FIELDS} for e in events]


def _drive_to_awaiting(reader: int) -> WetDriver:
    """Drive a fresh WaitForRecovery driver to a deterministic AWAITING_RECOVERY
    pause. The fault is injected on well 0, so the pause happens BEFORE any real
    reading is taken -- every well is still unmeasured, making the terminal
    abandon/cancel readings server-state-independent. Sequential-safe: each call
    re-injects and the lease is free between calls (prior driver released it)."""
    otp = compile_and_validate(make_gradient_spec(n_samples=4, n_controls=0))
    send(reader, {"cmd": "inject",
                  "error_wells": {otp.wells[0].well_id: "E_DEVICE"}})
    drv = WetDriver(port=reader, policy=WaitForRecovery(), max_retries=1)
    drv.submit_goal(otp)
    drv.run(calibrate=False)
    assert drv.state is GoalState.AWAITING_RECOVERY
    return drv


def test_paused_cancel_is_abandon_equivalent(reader):
    """112 即裁二: a cancel arriving in AWAITING_RECOVERY is DEFINED to be exactly
    abandon() -- same ABORTED terminal, same visible-null readings, and the same
    event trail (field-for-field, ts aside). KILL: route the paused cancel into the
    normal ->CANCELING flag path and the outcome would be CANCELED, not ABORTED,
    and the two event streams would diverge."""
    # Leg A: explicit abandon() with the cancel reason.
    drv_a = _drive_to_awaiting(reader)
    res_abandon = drv_a.abandon("canceled_while_awaiting_recovery")
    # Leg B: request_cancel() from the paused state (the API under test).
    drv_b = _drive_to_awaiting(reader)
    res_cancel = drv_b.request_cancel()

    # (1) request_cancel returns the terminal result and the state machine aborts.
    assert res_cancel is not None
    assert res_cancel.outcome is GoalState.ABORTED
    assert drv_b.state is GoalState.ABORTED
    assert res_abandon.outcome is GoalState.ABORTED

    # (2) readings逐字段等: paused at well 0, so all four wells are visible-null
    # 'failed' readings -- identical field-for-field (no server-state divergence).
    assert res_cancel.n_raw == res_abandon.n_raw == 4
    assert res_cancel.n_failed == res_abandon.n_failed == 4
    assert all(r.value is None and r.status == "failed" for r in res_cancel.readings)
    assert [dataclasses.astuple(r) for r in res_cancel.readings] == \
           [dataclasses.astuple(r) for r in res_abandon.readings]

    # (3) event trail同形: with the server-only fields (ts/lease/uptime) and the
    # leading action_cancel_requested marker (the sole extra event a cancel emits)
    # removed, the two event streams are field-for-field equal -- cancel really did
    # run abandon(), including the well_failed / lease_released / action_state=ABORTED
    # / action_result sequence, with NO CANCELING/CANCELED state anywhere.
    ev_cancel = [e for e in _strip_server(res_cancel.events)
                 if e["kind"] != "action_cancel_requested"]
    ev_abandon = _strip_server(res_abandon.events)
    assert ev_cancel == ev_abandon
    assert not any(e.get("state") in ("CANCELING", "CANCELED")
                   for e in res_cancel.events)
    # the ABORTED result event carries the cancel reason verbatim (成文, not implicit).
    final = res_cancel.events[-1]
    assert final["kind"] == "action_result" and final["outcome"] == "ABORTED"
    assert final["reason"] == "canceled_while_awaiting_recovery"


def test_non_paused_cancel_still_flags_and_returns_none(reader):
    """Regression guard: request_cancel() OUTSIDE AWAITING_RECOVERY is unchanged --
    it only sets the flag, returns None, and the in-flight run honours it as a
    ->CANCELED (never the paused abandon path). KILL: fire the abandon branch on
    any state and this ACCEPTED-time cancel would abort instead of cancelling."""
    otp = compile_and_validate(make_gradient_spec(n_samples=3, n_controls=0))
    drv = WetDriver(port=reader, max_retries=1)
    drv.submit_goal(otp)
    assert drv.state is GoalState.ACCEPTED
    ret = drv.request_cancel()                 # cancel before the first well boundary
    assert ret is None                          # non-paused cancel returns nothing
    res = drv.run(calibrate=False)
    assert res.outcome is GoalState.CANCELED    # honoured as CANCELED, not ABORTED
    assert all(r.status == "canceled" for r in res.readings)
