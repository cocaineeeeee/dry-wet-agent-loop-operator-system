"""M23 Real-Wet Readiness Contract — Phase 3 (A domain): fake physical backend (seven
behaviour modes) + the sim-real differential gate.

Discriminative-first (the W8 pattern): every red line has a test that turns red if the
guard is removed (KILL note inline). Two deliverables under test:

  * ``fake_physical`` — the seven user-钦定 behaviour modes driven through the ledger's
    dispatch->I/O->sense->confirm orchestration, plus the attempt++ recovery (122 即裁二)
    and the resume trichotomy (122 即裁三). Time is VIRTUAL (no real sleep).
  * ``differential_gate`` — the sim-real numerical differential gate (AHEAD OF PRECEDENT:
    no such explicit gate exists in pyvisa-sim / renode / PLR). Three acceptance modes:
    EXACT (semantic identity), TOLERANCE-BOUNDED (observed within the declared envelope),
    FAIL-CLOSED (missing/extra action is red).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from expos.adapters.wet.action_ledger import (
    ActionLedger,
    ActionState,
    VolumeLedger,
)
from expos.adapters.wet.differential_gate import (
    ToleranceEnvelope,
    run_differential_gate,
)
from expos.adapters.wet.fake_physical import (
    OBS_CHANNEL_SCHEMA,
    Behaviour,
    BehaviourSpec,
    FakePhysicalBackend,
    PhysicalDispatch,
    ResumeDisposition,
    Scenario,
)
from expos.adapters.wet.recovery import NeverRecover, WaitForRecovery


# ---- helpers ----------------------------------------------------------------

def _vol(source_uL: float = 15000.0) -> VolumeLedger:
    # RSV is an off-plate reservoir; destinations are plate wells (cap 360 uL).
    return VolumeLedger(capacities={"RSV": 1e9}, initial={"RSV": source_uL})


def _scenario(actions, behaviours=None, name="sc") -> Scenario:
    return Scenario(name=name, actions=actions, behaviours=behaviours or {})


def _drive(tmp_path, scenario, *, policy=None, max_polls=64):
    led = ActionLedger(tmp_path, volume=_vol(), policy=policy or WaitForRecovery())
    backend = FakePhysicalBackend(scenario)
    disp = PhysicalDispatch(led, backend, max_polls=max_polls)
    return led, backend, disp


def _one(action_id="a1", dest="B2", volume=150.0):
    return [{"action_id": action_id, "dest": dest, "volume": volume}]


def _ledger_path(tmp_path) -> str:
    return str(Path(tmp_path) / "action_ledger.jsonl")


# ======================================================================
# Mode 1 — exact success
# ======================================================================

def test_mode1_exact_success_commits_exactly_once(tmp_path):
    """Nominal: CONFIRMED read-back drives PENDING -> COMMITTED, observed == requested,
    volume applied EXACTLY once. Red line: exactly-once commit + observed stored distinct
    from requested. KILL: apply the observed volume twice and the reservoir over-draws."""
    sc = _scenario(_one(volume=150.0))
    led, be, disp = _drive(tmp_path, sc)
    rec = disp.run(sc.planned())[0]
    assert rec.state is ActionState.COMMITTED
    assert rec.observed_volume_ul == 150.0
    assert rec.requested_volume_ul == 150.0
    assert led.volume.current("B2") == 150.0                    # applied exactly once
    assert led.volume.current("RSV") == 15000.0 - 150.0


# ======================================================================
# Mode 2 — sensed mismatch (driver OK reply is NOT physical truth)
# ======================================================================

def test_mode2_sensed_mismatch_ok_reply_never_commits(tmp_path):
    """The fake driver ACKs the send (io_ok True), but the sensed read-back is MISMATCH:
    the OK reply ALONE never commits. Under WaitForRecovery a recoverable defined error
    routes to AWAITING_RECOVERY. Red line: 'driver 回复不当物理真相'. KILL: commit on
    io_ok and this flips to COMMITTED."""
    sc = _scenario(_one(), behaviours={"B2": [
        BehaviourSpec(1, Behaviour.MISMATCH_DEFINED, code="E_DEVICE")]})
    led, be, disp = _drive(tmp_path, sc, policy=WaitForRecovery())
    rec = disp.run(sc.planned())[0]
    assert rec.state is ActionState.AWAITING_RECOVERY
    assert rec.observed_volume_ul is None                       # nothing committed
    # the driver DID reply OK -- proof the OK reply was recorded yet ignored as truth
    io_notes = [e for e in led.events if e.get("note", "").startswith("driver_reply")]
    assert io_notes and io_notes[0]["io_ok"] is True


def test_mode2_sensed_mismatch_under_never_recover_rolls_back(tmp_path):
    """Same mismatch under the default NeverRecover -> ROLLED_BACK (defined error aborts;
    nothing was committed so nothing to physically revert)."""
    sc = _scenario(_one(), behaviours={"B2": [
        BehaviourSpec(1, Behaviour.MISMATCH_DEFINED, code="E_DEVICE")]})
    led, be, disp = _drive(tmp_path, sc, policy=NeverRecover())
    rec = disp.run(sc.planned())[0]
    assert rec.state is ActionState.ROLLED_BACK
    assert led.volume.current("B2") == 0.0


def test_mode2b_undefined_error_fails_closed(tmp_path):
    """An UNDEFINED (unregistered) failure code never reaches the policy: fail-closed to
    ROLLED_BACK unconditionally (REF-I anti-fragility). KILL: route an unknown code through
    the policy and a WaitForRecovery would wrongly pause it."""
    sc = _scenario(_one(), behaviours={"B2": [
        BehaviourSpec(1, Behaviour.MISMATCH_UNDEFINED, code="E_GREMLIN")]})
    led, be, disp = _drive(tmp_path, sc, policy=WaitForRecovery())
    rec = disp.run(sc.planned())[0]
    assert rec.state is ActionState.ROLLED_BACK
    assert rec.disposition == "rolled_back_fail_closed"


# ======================================================================
# Mode 3 — partial execution (k of n wells): per-WELL sensed truth
# ======================================================================

def test_mode3_partial_execution_per_well_sensed_truth(tmp_path):
    """A batch of 3 transfers: the driver ACKs ALL three (blanket OK), but the per-well
    sensed read-back decides each independently -- B2/D2 CONFIRMED -> COMMITTED, C2
    MISMATCH -> AWAITING_RECOVERY. Red line: success/failure is per-well sensed truth, NOT
    a blanket driver reply. KILL: decide the batch by the (uniform OK) driver reply and C2
    would wrongly commit."""
    sc = _scenario(
        [{"action_id": "a-B2", "dest": "B2", "volume": 150.0},
         {"action_id": "a-C2", "dest": "C2", "volume": 150.0},
         {"action_id": "a-D2", "dest": "D2", "volume": 150.0}],
        behaviours={"C2": [BehaviourSpec(1, Behaviour.MISMATCH_DEFINED, code="E_DEVICE")]})
    led, be, disp = _drive(tmp_path, sc, policy=WaitForRecovery())
    recs = {r.action_id: r for r in disp.run(sc.planned())}
    assert recs["a-B2"].state is ActionState.COMMITTED
    assert recs["a-D2"].state is ActionState.COMMITTED
    assert recs["a-C2"].state is ActionState.AWAITING_RECOVERY     # failed well isolated
    assert led.volume.current("C2") == 0.0                         # not committed
    assert led.volume.current("B2") == 150.0 and led.volume.current("D2") == 150.0


# ======================================================================
# Mode 4 — timeout before confirmation: VIRTUAL time, no real sleep
# ======================================================================

def test_mode4_timeout_is_virtual_no_real_sleep(tmp_path, monkeypatch):
    """The read-back is UNOBSERVED (Unknown, NOT a failure) until a LOGICAL tick budget is
    exhausted, then a TRANSPORT timeout -> AWAITING_RECOVERY (WaitForRecovery). Red lines:
    (a) NO real sleep -- timeout is a virtual-tick event (renode determinism, F-4); (b) no
    silent retry. KILL: implement slow/timeout with time.sleep and the patched sleep fires.
    """
    def _boom(*a, **k):
        raise AssertionError("real time.sleep was called -- timeout must be VIRTUAL")
    monkeypatch.setattr(time, "sleep", _boom)

    sc = _scenario(_one(), behaviours={"B2": [
        BehaviourSpec(1, Behaviour.TIMEOUT, timeout_at_tick=4)]})
    led, be, disp = _drive(tmp_path, sc, policy=WaitForRecovery())
    rec = disp.run(sc.planned())[0]
    assert rec.state is ActionState.AWAITING_RECOVERY             # timed out, paused
    assert be.clock.now() == 4                                    # deterministic virtual tick
    # UNOBSERVED never tripped recovery before the budget (no premature failure)
    assert rec.observed_volume_ul is None


def test_mode4b_never_observed_stays_pending_no_silent_commit(tmp_path):
    """A read-back that is NEVER conclusive (UNOBSERVED forever) leaves the action PENDING
    after the poll budget -- NO silent commit and NO silent abort. Red line: 'not observed'
    is Unknown, never invented as success or failure."""
    sc = _scenario(_one(), behaviours={"B2": [BehaviourSpec(1, Behaviour.UNOBSERVED)]})
    led, be, disp = _drive(tmp_path, sc, policy=WaitForRecovery(), max_polls=5)
    rec = disp.run(sc.planned())[0]
    assert rec.state is ActionState.PENDING
    assert rec.observed_volume_ul is None


# ======================================================================
# Mode 5 — duplicate reply: the idempotency gate eats the second
# ======================================================================

def test_mode5_duplicate_reply_idempotency_eats_second(tmp_path):
    """The physical backend echoes the same command twice (at-least-once delivery). The
    idempotency gate (same action_id + same params) absorbs the second dispatch: the I/O
    is issued EXACTLY once and the physical action executes once. Red line: 'resume/重复
    回复 不静默重发物理动作'. KILL: drop the fingerprint idempotency skip and the re-send
    counter climbs."""
    sc = _scenario(_one())
    led = ActionLedger(tmp_path, volume=_vol())
    sends = {"n": 0}

    def io() -> bool:
        sends["n"] += 1
        return True

    planned = sc.planned()[0]
    led.dispatch(planned, io)
    led.dispatch(planned, io)                                    # duplicate reply
    assert sends["n"] == 1                                       # issued exactly once
    # the second was recorded (not silent) as an idempotent-replay note
    assert any(e.get("note") == "idempotent_replay_skipped" for e in led.events)


# ======================================================================
# Mode 6 — disconnect / reconnect resume (== the resume trichotomy, 122 即裁三)
# ======================================================================

def test_mode6_resume_committed_skips(tmp_path):
    """COMMITTED third of the trichotomy: a resumed ledger SKIPS an already-COMMITTED
    action -- it is neither re-sensed nor re-dispatched. KILL: re-sense a committed action
    and the fresh backend's sensed_log would be non-empty."""
    sc = _scenario(_one("done"))
    led, be, disp = _drive(tmp_path, sc)
    disp.run(sc.planned())
    assert led.record("done").state is ActionState.COMMITTED
    # resume with a fresh ledger + fresh backend over the same dir
    led2 = ActionLedger(tmp_path, volume=_vol())
    be2 = FakePhysicalBackend(sc)
    verd = PhysicalDispatch(led2, be2).resume(sc.planned())
    assert verd["done"] is ResumeDisposition.SKIP_COMMITTED
    assert be2.sensed_log == []                                  # NOT re-sensed


def test_mode6_resume_pending_resenses_not_redispatch(tmp_path):
    """PENDING third: an in-flight (PENDING) action is RE-SENSED on resume (re-read to
    learn its true outcome), NEVER re-dispatched -- the physical action is not re-issued.
    KILL: re-dispatch a PENDING action on resume and the I/O would be re-sent."""
    # first run leaves the action PENDING (never conclusively observed)
    sc = _scenario(_one("inflight"), behaviours={"B2": [
        BehaviourSpec(1, Behaviour.UNOBSERVED)]})
    led, be, disp = _drive(tmp_path, sc, max_polls=3)
    disp.run(sc.planned())
    assert led.record("inflight").state is ActionState.PENDING
    # resume: the backend now confirms -> re-sense resolves it to COMMITTED
    sc2 = _scenario(_one("inflight"), behaviours={"B2": [
        BehaviourSpec(1, Behaviour.CONFIRM_EXACT)]})
    led2 = ActionLedger(tmp_path, volume=_vol())
    sends = {"n": 0}
    disp2 = PhysicalDispatch(led2, FakePhysicalBackend(sc2))
    # patch the driver reply to prove NO physical re-dispatch happens on the PENDING path
    disp2._driver_reply = lambda: sends.__setitem__("n", sends["n"] + 1) or True
    verd = disp2.resume(sc2.planned())
    assert verd["inflight"] is ResumeDisposition.RESENSE_PENDING
    assert sends["n"] == 0                                       # nothing re-dispatched
    assert led2.record("inflight").state is ActionState.COMMITTED   # resolved by re-sense


def test_mode6_resume_planned_redispatches(tmp_path):
    """PLANNED third: a PLANNED-only on-disk state (crash between the PLANNED append and
    the PENDING append -- I/O provably never issued) is RE-DISPATCHED on resume. KILL:
    treat PLANNED like PENDING (skip) and an action whose I/O never ran would be silently
    abandoned."""
    sc = _scenario(_one("planned"))
    led = ActionLedger(tmp_path, volume=_vol())
    led.dispatch(sc.planned()[0], lambda: True)
    # truncate the ledger to just the first line (from None -> PLANNED): simulate a crash
    # right after the PLANNED append, before PENDING was persisted (clean suffix drop keeps
    # the hash chain valid).
    path = Path(tmp_path) / "action_ledger.jsonl"
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    assert json.loads(lines[0])["to"] == "PLANNED"
    path.write_text(lines[0] + "\n")

    led2 = ActionLedger(tmp_path, volume=_vol())
    assert led2.record("planned").state is ActionState.PLANNED   # replayed as PLANNED
    disp2 = PhysicalDispatch(led2, FakePhysicalBackend(sc))
    verd = disp2.resume(sc.planned())
    assert verd["planned"] is ResumeDisposition.REDISPATCH_PLANNED
    assert led2.record("planned").state is ActionState.COMMITTED  # completed safely


# ======================================================================
# Mode 7 — operator cancel while AWAITING_RECOVERY == ABORTED
# ======================================================================

def test_mode7_cancel_in_awaiting_recovery_is_aborted(tmp_path):
    """A mismatch pauses the action into AWAITING_RECOVERY; an operator cancel there
    terminates it ABORTED (112 即裁二 paused-cancel == abandon precedent). KILL: route the
    cancel anywhere but ABORTED and this flips."""
    sc = _scenario(_one("cancel-me"), behaviours={"B2": [
        BehaviourSpec(1, Behaviour.MISMATCH_DEFINED, code="E_DEVICE")]})
    led, be, disp = _drive(tmp_path, sc, policy=WaitForRecovery())
    disp.run(sc.planned())
    assert led.record("cancel-me").state is ActionState.AWAITING_RECOVERY
    rec = led.cancel("cancel-me")
    assert rec.state is ActionState.ABORTED
    assert "canceled_while_awaiting_recovery" in (rec.disposition or "")


# ======================================================================
# attempt++ re-dispatch (122 即裁二): recover bumps the attempt
# ======================================================================

def test_attempt_increment_redispatch_new_attempt_fails_back(tmp_path):
    """recover() re-dispatches as a DISTINCT auditable attempt (AWAITING_RECOVERY ->
    PENDING, attempt++); a fresh MISMATCH on the new attempt routes BACK to
    AWAITING_RECOVERY (the existing edge). The idempotency KEY (action_id) is unchanged;
    for_attempt distinguishes the two tries. KILL: fold the retry into a self-loop and the
    attempt counter would not advance (audit granularity lost)."""
    sc = _scenario(_one("retry"), behaviours={"B2": [
        BehaviourSpec(1, Behaviour.MISMATCH_DEFINED, code="E_DEVICE"),
        BehaviourSpec(2, Behaviour.MISMATCH_DEFINED, code="E_DEVICE")]})
    led, be, disp = _drive(tmp_path, sc, policy=WaitForRecovery())
    disp.run(sc.planned())
    assert led.record("retry").state is ActionState.AWAITING_RECOVERY
    assert led.record("retry").attempt == 1
    disp.recover_one(sc.planned()[0])                            # attempt -> 2, re-sense
    rec = led.record("retry")
    assert rec.state is ActionState.AWAITING_RECOVERY            # new attempt failed back
    assert rec.attempt == 2                                      # distinct auditable attempt
    assert rec.action_id == "retry"                             # idempotency key unchanged


def test_attempt_increment_recovers_when_new_attempt_confirms(tmp_path):
    """The recovery success path: attempt 1 MISMATCH pauses; recover() -> attempt 2; the
    instrument is now fixed so attempt 2 CONFIRMS -> COMMITTED, stamped for the NEW
    attempt (observedGeneration)."""
    sc = _scenario(_one("fixed"), behaviours={"B2": [
        BehaviourSpec(1, Behaviour.MISMATCH_DEFINED, code="E_DEVICE"),
        BehaviourSpec(2, Behaviour.CONFIRM_EXACT)]})
    led, be, disp = _drive(tmp_path, sc, policy=WaitForRecovery())
    disp.run(sc.planned())
    disp.recover_one(sc.planned()[0])
    rec = led.record("fixed")
    assert rec.state is ActionState.COMMITTED
    assert rec.attempt == 2 and rec.observed_volume_ul == 150.0


# ======================================================================
# Differential gate — EXACT acceptance mode (semantic identity)
# ======================================================================

def _build_ledger(tmp_path, actions, behaviours=None, policy=None):
    sc = _scenario(actions, behaviours=behaviours)
    led = ActionLedger(tmp_path, volume=_vol(), policy=policy or WaitForRecovery())
    PhysicalDispatch(led, FakePhysicalBackend(sc)).run(sc.planned())
    return _ledger_path(tmp_path)


def test_diff_exact_positive_identical_ledgers_pass(tmp_path):
    """EXACT mode positive: two ledgers with the same action sequence, labware/wells,
    requested volumes, terminal states, and in-tolerance observed volumes -> gate PASSES.
    Also exercises the observation-schema facet (compatible schemas)."""
    acts = [{"action_id": "a1", "dest": "B2", "volume": 150.0},
            {"action_id": "a2", "dest": "C2", "volume": 150.0}]
    sim = _build_ledger(tmp_path / "sim", acts)
    real = _build_ledger(tmp_path / "real", acts)
    rep = run_differential_gate(sim, real, sim_obs_schema=OBS_CHANNEL_SCHEMA,
                                real_obs_schema=OBS_CHANNEL_SCHEMA)
    assert rep.passed is True
    assert all(rep.facet_status.values())
    assert rep.tolerance_source == "vendor_spec_placeholder"


def test_diff_exact_negative_terminal_state_mismatch_red(tmp_path):
    """EXACT mode negative: identical plan but the real ledger's action ended in a
    different terminal state (AWAITING_RECOVERY vs COMMITTED) -> RED on the terminal-state
    facet. KILL: skip the terminal-state comparison and a diverged outcome passes."""
    acts = [{"action_id": "a1", "dest": "B2", "volume": 150.0}]
    sim = _build_ledger(tmp_path / "sim", acts)
    real = _build_ledger(tmp_path / "real", acts,
                         behaviours={"B2": [BehaviourSpec(1, Behaviour.MISMATCH_DEFINED,
                                                          code="E_DEVICE")]})
    rep = run_differential_gate(sim, real)
    assert rep.passed is False
    assert rep.facet_status["terminal_state"] is False
    assert any(f.kind == "terminal_state_mismatch" for f in rep.findings)


# ======================================================================
# Differential gate — TOLERANCE-BOUNDED acceptance mode
# ======================================================================

def test_diff_tolerance_bounded_positive_small_drift_passes(tmp_path):
    """TOLERANCE-BOUNDED positive: the real observed volume drifts 1% (1.5 uL on 150 uL),
    within the declared band allowance (max(2%*150, 1.5 uL floor) = 3.0 uL) -> gate PASSES.
    The plan is identical; only observed differs, within envelope."""
    acts = [{"action_id": "a1", "dest": "B2", "volume": 150.0}]
    sim = _build_ledger(tmp_path / "sim", acts)
    real = _build_ledger(tmp_path / "real", acts,
                         behaviours={"B2": [BehaviourSpec(1, Behaviour.CONFIRM_DRIFT,
                                                          drift_pct=1.0)]})
    rep = run_differential_gate(sim, real)
    assert rep.passed is True
    assert rep.facet_status["device_tolerance"] is True


def test_diff_tolerance_bounded_negative_volume_tampered_red(tmp_path):
    """TOLERANCE-BOUNDED negative: the real observed volume drifts 10% (15 uL on 150 uL),
    far beyond the 3.0 uL band allowance -> RED on the device-tolerance facet. This is the
    drift-detector the differential gate exists for (INDEX_REF_P3: drift caught the moment
    it leaves the declared envelope). KILL: drop the band check and a 10% drift passes."""
    acts = [{"action_id": "a1", "dest": "B2", "volume": 150.0}]
    sim = _build_ledger(tmp_path / "sim", acts)
    real = _build_ledger(tmp_path / "real", acts,
                         behaviours={"B2": [BehaviourSpec(1, Behaviour.CONFIRM_DRIFT,
                                                          drift_pct=10.0)]})
    rep = run_differential_gate(sim, real)
    assert rep.passed is False
    assert rep.facet_status["device_tolerance"] is False
    f = [f for f in rep.findings if f.kind == "volume_tolerance_exceeded"]
    assert f and f[0].action_id == "a1"


# ======================================================================
# Differential gate — FAIL-CLOSED acceptance mode (missing / extra action)
# ======================================================================

def test_diff_failclosed_missing_action_red(tmp_path):
    """FAIL-CLOSED: the real ledger is MISSING an action the sim declared -> RED (a real
    backend doing FEWER actions than declared is never 'close enough'). KILL: tolerate a
    missing action and an under-executed run passes."""
    sim = _build_ledger(tmp_path / "sim",
                        [{"action_id": "a1", "dest": "B2", "volume": 150.0},
                         {"action_id": "a2", "dest": "C2", "volume": 150.0}])
    real = _build_ledger(tmp_path / "real",
                        [{"action_id": "a1", "dest": "B2", "volume": 150.0}])
    rep = run_differential_gate(sim, real)
    assert rep.passed is False
    assert rep.facet_status["action_sequence_identity"] is False
    assert any(f.kind == "action_missing" and f.action_id == "a2" for f in rep.findings)


def test_diff_failclosed_extra_action_red(tmp_path):
    """FAIL-CLOSED: the real ledger has an EXTRA action the sim never declared -> RED ('多
    做了也算过' is forbidden -- doing MORE is not passing). KILL: tolerate an extra action
    and an over-executed run passes."""
    sim = _build_ledger(tmp_path / "sim",
                        [{"action_id": "a1", "dest": "B2", "volume": 150.0}])
    real = _build_ledger(tmp_path / "real",
                        [{"action_id": "a1", "dest": "B2", "volume": 150.0},
                         {"action_id": "a2", "dest": "C2", "volume": 150.0}])
    rep = run_differential_gate(sim, real)
    assert rep.passed is False
    assert rep.facet_status["action_sequence_identity"] is False
    assert any(f.kind == "action_extra" and f.action_id == "a2" for f in rep.findings)


def test_diff_failclosed_undeclared_observation_channel_red(tmp_path):
    """FAIL-CLOSED schema facet: a real backend that declares an observation channel the
    sim never declared -> RED (real produced a quantity sim did not declare). KILL: skip
    the schema check and an undeclared telemetry channel slips through."""
    acts = [{"action_id": "a1", "dest": "B2", "volume": 150.0}]
    sim = _build_ledger(tmp_path / "sim", acts)
    real = _build_ledger(tmp_path / "real", acts)
    rogue = dict(OBS_CHANNEL_SCHEMA)
    rogue["hidden_pressure_sensor"] = "float"                   # undeclared by the sim
    rep = run_differential_gate(sim, real, sim_obs_schema=OBS_CHANNEL_SCHEMA,
                                real_obs_schema=rogue)
    assert rep.passed is False
    assert rep.facet_status["observation_schema"] is False
    assert any(f.kind == "undeclared_channel" for f in rep.findings)


# ======================================================================
# Tolerance envelope — declared-data discipline (vendor_spec_placeholder)
# ======================================================================

def test_tolerance_envelope_is_volume_banded_and_placeholder_sourced():
    """The envelope is DECLARED data keyed by volume band (never a single scalar), sourced
    from vendor_spec_placeholder. Near the minimum volume the uL FLOOR dominates the
    percent bound (dual representation); above the largest band it is fail-closed (None)."""
    env = ToleranceEnvelope.load()
    assert env.meta["source"] == "vendor_spec_placeholder"
    # 1 uL: floor 0.15 uL dominates (15% of 1 uL == 0.15 uL); banded, not scalar
    assert env.systematic_allowance(1.0) == pytest.approx(0.15)
    # 150 uL: 2% * 150 = 3.0 uL wins over the 1.5 uL floor
    assert env.systematic_allowance(150.0) == pytest.approx(3.0)
    # above the largest declared band -> fail-closed
    assert env.systematic_allowance(5000.0) is None
    # multi-channel doubling (ISO 8655)
    assert env.systematic_allowance(150.0, channels=8) == pytest.approx(6.0)
