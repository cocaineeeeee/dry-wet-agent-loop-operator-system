"""Pytest fixtures: in-process reader for fast tests, subprocess reader for kill."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time

import pytest
from pathlib import Path as _P

ROOT = _P(__file__).resolve().parents[1]

from expos.adapters.wet import sim_reader  # noqa: E402


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def send(port: int, obj: dict, timeout: float = 2.0, host: str = "127.0.0.1") -> dict:
    """One-shot raw JSON request (used for admin inject / truth_dump in tests)."""
    with socket.create_connection((host, port), timeout=timeout) as s:
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
    """In-process reader server; yields its port. Fast; no external process."""
    port = _free_port()
    srv = sim_reader.serve("127.0.0.1", port, seed=7, noise_sd=0.0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _wait_port(port)
    yield port
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def reader_proc():
    """Reader as a real subprocess (for the process-kill test). Yields (proc, port)."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "expos.adapters.wet.sim_reader", "--port", str(port),
         "--seed", "7", "--noise", "0.0"],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    _wait_port(port, proc=proc)
    yield proc, port
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)


def _wait_port(port: int, proc=None, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError("reader process died during startup")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"reader on port {port} did not come up")


# ---- tests (merged from sandbox test_wet.py) ----
"""W4 seven-concern test suite: normal + fault-injection for each of the seven
instrument concerns (G3), plus end-to-end six-state lifecycle and custody chain.

Run: ./venv/bin/python -m pytest tests/ -v
"""




from expos.adapters.wet.driver import WetDriver, GoalState
from expos.adapters.wet.ot_protocol import (
    compile_and_validate, execute_simulated, ValidationError,
)
from expos.adapters.wet.protocol_spec import (
    ProtocolSpec, SolventSample, make_gradient_spec,
)


# ============================================================ 4. protocol validation
# (opentrons real stack; runs before any reader interaction)

def test_compile_valid_gradient():
    spec = make_gradient_spec(n_samples=8, n_controls=2)
    otp = compile_and_validate(spec)
    assert otp.validated
    assert len(otp.wells) == 10
    # every well got a distinct deck position and a bound custody record
    assert len({w.well_id for w in otp.wells}) == 10
    for w in otp.wells:
        rec = otp.custody.trace(w.sample_id)
        assert rec.segments_complete()["protocol"]
        assert rec.segments_complete()["deck"]


def test_execute_simulated_ledger():
    spec = make_gradient_spec(n_samples=4, n_controls=0)
    otp = compile_and_validate(spec)
    led = execute_simulated(otp)
    assert led["n_transfers"] > 0
    assert led["duration_s"] > 0
    for w in otp.wells:
        cell = led["wells"][w.well_id]
        assert abs(cell["total_ul"] - spec.total_volume_ul) < 1e-6


def test_reject_polarity_out_of_stock_span():
    # target polarity above the high stock -> unmixable, must be rejected
    spec = ProtocolSpec(samples=[SolventSample("bad", target_polarity=0.99)])
    with pytest.raises(ValidationError, match="outside|range|span"):
        compile_and_validate(spec)


def test_reject_over_pipette_range():
    # tiny total volume -> a mix leg falls below the pipette minimum (20 uL)
    spec = ProtocolSpec(
        samples=[SolventSample("c0", target_polarity=0.5)],
        total_volume_ul=10.0,
    )
    with pytest.raises(ValidationError, match="pipette range"):
        compile_and_validate(spec)


def test_reject_well_overflow():
    spec = ProtocolSpec(
        samples=[SolventSample("c0", target_polarity=0.5)],
        total_volume_ul=400.0, well_capacity_ul=340.0,
    )
    with pytest.raises(ValidationError, match="well capacity|pipette range"):
        compile_and_validate(spec)


def test_reject_deck_slot_conflict():
    spec = make_gradient_spec(n_samples=2, n_controls=0)
    spec.reservoir_slot = spec.plate_slot  # collide reservoir onto plate slot
    with pytest.raises(ValidationError):
        compile_and_validate(spec)


def test_reject_missing_labware():
    spec = make_gradient_spec(n_samples=2, n_controls=0)
    spec.plate_labware = "no_such_labware_xyz"
    with pytest.raises(ValidationError):
        compile_and_validate(spec)


# ============================================================ 1. health check

def test_health_normal(reader):
    h = send(reader, {"cmd": "health"})
    assert h["ok"] and h["status"] == "healthy"
    assert h["uptime_s"] >= 0
    assert "last_calibration" in h


def test_health_degraded_and_offline(reader):
    send(reader, {"cmd": "inject", "status": "degraded"})
    assert send(reader, {"cmd": "health"})["status"] == "degraded"
    send(reader, {"cmd": "inject", "status": "offline"})
    assert send(reader, {"cmd": "health"})["status"] == "offline"


# ============================================================ 3. resource reservation

def test_single_lease_double_acquire_rejected(reader):
    a = send(reader, {"cmd": "acquire", "holder": "A", "ttl": 30})
    assert a["ok"]
    b = send(reader, {"cmd": "acquire", "holder": "B", "ttl": 30})
    assert not b["ok"] and b["error"] == "resource_busy"
    rel = send(reader, {"cmd": "release", "lease_id": a["lease_id"]})
    assert rel["ok"]
    c = send(reader, {"cmd": "acquire", "holder": "B", "ttl": 30})
    assert c["ok"]  # free after release


def test_lease_ttl_auto_expiry(reader):
    a = send(reader, {"cmd": "acquire", "holder": "A", "ttl": 0.3})
    assert a["ok"]
    assert not send(reader, {"cmd": "acquire", "holder": "B"})["ok"]
    time.sleep(0.4)
    # expired lease auto-reaped -> new acquire succeeds
    assert send(reader, {"cmd": "acquire", "holder": "B"})["ok"]


# ============================================================ 2. calibration

def test_calibration_drift_detectable_and_reset(reader):
    a = send(reader, {"cmd": "acquire", "holder": "A", "ttl": 30})
    lease = a["lease_id"]
    send(reader, {"cmd": "calibrate", "lease_id": lease})
    # measure the same control polarity many times WITHOUT recalibrating
    ctrl = {"sample_id": "SMP-CTL-ctl0", "well_id": "A1", "polarity": 0.55}
    first = send(reader, {"cmd": "measure", "lease_id": lease,
                          "samples": [ctrl]})["readings"][0]["value"]
    for _ in range(30):
        send(reader, {"cmd": "measure", "lease_id": lease, "samples": [ctrl]})
    drifted = send(reader, {"cmd": "measure", "lease_id": lease,
                            "samples": [ctrl]})["readings"][0]["value"]
    # systematic downward bias accrued (gain<1, though offset rises) -> detectable
    assert drifted < first - 0.05, (first, drifted)
    # truth sidecar confirms drift; client never saw gain/offset
    dump = send(reader, {"cmd": "truth_dump"})
    assert dump["gain"] < 1.0 and dump["offset"] > 0.0
    assert all("gain" not in r for r in [first if isinstance(first, dict) else {}])
    # recalibrate resets the model
    send(reader, {"cmd": "calibrate", "lease_id": lease})
    reset = send(reader, {"cmd": "truth_dump"})
    assert abs(reset["gain"] - 1.0) < 1e-9 and abs(reset["offset"]) < 1e-9


# ============================================================ 6. sample identity

def test_reader_rejects_missing_sample_id(reader):
    a = send(reader, {"cmd": "acquire", "holder": "A"})
    lease = a["lease_id"]
    r = send(reader, {"cmd": "measure", "lease_id": lease,
                      "samples": [{"well_id": "A1", "polarity": 0.5}]})
    assert not r["ok"] and r["error"] == "missing_sample_id"


def test_driver_rejects_forged_sample_id(reader):
    # Build a protocol, but the reader will be told to relabel via injection is
    # not available; instead we assert the driver's custody guard directly by
    # feeding a reading with an unknown sample_id through _ingest_reading.
    spec = make_gradient_spec(n_samples=2, n_controls=0)
    otp = compile_and_validate(spec)
    drv = WetDriver(port=reader, exp_id="e1", round_id=0)
    plan = otp.wells[0]
    forged = {"sample_id": "SMP-CND-GHOST", "well_id": plan.well_id,
              "value": 0.9, "seq": 1, "status": "ok"}
    reading = drv._ingest_reading(otp, otp.custody, plan, forged)
    assert reading.status == "rejected" and reading.value is None
    assert any(e["kind"] == "custody_violation" for e in drv.events)


# ==================================================== 6b. custody who/when audit dims

def test_custody_trace_carries_who_and_when(reader):
    """The four-segment custody trace records WHO transferred custody and WHEN:
    trace(sample_id) exposes an append-only who/when log -- protocol_compiler for
    the protocol+deck segments, wet_driver for the measurement+raw segments -- with
    a parseable ISO-8601 UTC timestamp on every transfer (senaite-inspired audit)."""
    from datetime import datetime

    spec = make_gradient_spec(n_samples=3, n_controls=1)
    otp = compile_and_validate(spec)
    # after compile, the protocol+deck segments are already stamped
    rec0 = otp.custody.trace(otp.wells[0].sample_id)
    assert [e["action"] for e in rec0.custody_log] == ["protocol", "deck"]
    assert all(e["actor"] == "protocol_compiler" for e in rec0.custody_log)

    drv = WetDriver(port=reader, exp_id="e_audit", round_id=0)
    drv.submit_goal(otp)
    res = drv.run()
    assert res.outcome == GoalState.SUCCEEDED
    for rd in res.readings:
        rec = res.custody.trace(rd.sample_id)  # one command -> full who/when trace
        actions = [e["action"] for e in rec.custody_log]
        assert actions == ["protocol", "deck", "measurement", "raw"]
        actors = {e["action"]: e["actor"] for e in rec.custody_log}
        assert actors["protocol"] == actors["deck"] == "protocol_compiler"
        assert actors["measurement"] == actors["raw"] == "wet_driver"
        # scalar audit dims reflect the LATEST transfer (raw, by wet_driver)
        assert rec.actor == "wet_driver" and rec.at_utc == rec.custody_log[-1]["at_utc"]
        # every timestamp is a real ISO-8601 UTC instant, monotonic non-decreasing
        stamps = [datetime.fromisoformat(e["at_utc"]) for e in rec.custody_log]
        assert all(s.tzinfo is not None for s in stamps)
        assert stamps == sorted(stamps)


def test_driver_rejects_unattested_custody_record(reader):
    """Discriminator (sibling of the forged-sample_id guard): a custody record that
    exists but was NEVER stamped by any construct (no actor -> forged/unaudited
    provenance) is rejected on ingest, exactly like an unknown sample_id. Deleting
    the attestation guard would let un-audited readings through -> this must fail."""
    from expos.adapters.wet.protocol_spec import CustodyChain, CustodyRecord

    spec = make_gradient_spec(n_samples=2, n_controls=0)
    otp = compile_and_validate(spec)
    plan = otp.wells[0]

    # forge a custody chain whose record was inserted WITHOUT the audited issue()
    # path: sample_id is "known" but no actor was ever stamped (empty custody_log).
    forged_chain = CustodyChain()
    forged_chain._records[plan.sample_id] = CustodyRecord(sample_id=plan.sample_id)
    assert forged_chain.known(plan.sample_id)          # id is present ...
    assert not forged_chain.attested(plan.sample_id)   # ... but unattested

    drv = WetDriver(port=reader, exp_id="e_forge", round_id=0)
    reading = drv._ingest_reading(
        otp, forged_chain,
        plan, {"sample_id": plan.sample_id, "well_id": plan.well_id,
               "value": 0.9, "seq": 1, "status": "ok"},
    )
    assert reading.status == "rejected" and reading.value is None
    viol = [e for e in drv.events if e["kind"] == "custody_violation"]
    assert viol and "no actor" in viol[-1]["detail"]


# ============================================================ end-to-end SUCCEEDED
# exercises health + reservation + calibration + measurement + custody together

def test_end_to_end_success_and_custody_chain(reader):
    spec = make_gradient_spec(n_samples=6, n_controls=2)
    otp = compile_and_validate(spec)
    drv = WetDriver(port=reader, exp_id="e2", round_id=1)
    drv.submit_goal(otp)
    assert drv.state == GoalState.ACCEPTED
    res = drv.run()
    assert res.outcome == GoalState.SUCCEEDED
    assert res.n_raw == 8 and res.n_failed == 0
    # every reading carries a known custody key
    for rd in res.readings:
        assert otp.custody.known(rd.sample_id)
        rec = res.custody.trace(rd.sample_id)
        segs = rec.segments_complete()
        assert all(segs.values()), (rd.sample_id, segs)  # all 4 segments traced
    # six-state trail present
    kinds = [e["kind"] for e in res.events]
    assert "action_goal" in kinds and "action_result" in kinds
    states = [e["state"] for e in res.events if e["kind"] == "action_state"]
    assert states[:2] == ["ACCEPTED", "EXECUTING"] and states[-1] == "SUCCEEDED"


# ============================================================ 5. timeout + retry

def test_timeout_budget_exhausted_aborts(reader):
    send(reader, {"cmd": "inject", "slow_ms": 5000})  # far beyond driver timeout
    spec = make_gradient_spec(n_samples=2, n_controls=0)
    otp = compile_and_validate(spec)
    drv = WetDriver(port=reader, exp_id="e3", round_id=0,
                    timeout_s=0.3, max_retries=3)
    drv.submit_goal(otp)
    res = drv.run(calibrate=False)
    assert res.outcome == GoalState.ABORTED
    assert "timeout" in (res.reason or "")
    # all wells emitted as visible failures (never silently dropped)
    assert res.n_failed == len(otp.wells)
    assert any(e["kind"] == "measure_budget_exhausted" for e in res.events)


def test_hang_no_response_aborts(reader):
    send(reader, {"cmd": "inject", "hang": True})
    spec = make_gradient_spec(n_samples=1, n_controls=0)
    otp = compile_and_validate(spec)
    drv = WetDriver(port=reader, exp_id="e3b", round_id=0,
                    timeout_s=0.3, max_retries=2)
    drv.submit_goal(otp)
    res = drv.run(calibrate=False)
    assert res.outcome == GoalState.ABORTED and res.n_failed == 1


# ============================================================ 6. device failure

def test_error_code_injection_retries_then_aborts(reader):
    # more errors than the retry budget -> abort with device_error classification
    send(reader, {"cmd": "inject", "error_next": 10})
    spec = make_gradient_spec(n_samples=1, n_controls=0)
    otp = compile_and_validate(spec)
    drv = WetDriver(port=reader, exp_id="e4", round_id=0,
                    timeout_s=1.0, max_retries=3)
    drv.submit_goal(otp)
    res = drv.run(calibrate=False)
    assert res.outcome == GoalState.ABORTED
    assert "device_error" in (res.reason or "")


def test_partial_dropout_visible_null(reader):
    spec = make_gradient_spec(n_samples=4, n_controls=0)
    otp = compile_and_validate(spec)
    drop_well = otp.wells[1].well_id
    send(reader, {"cmd": "inject", "dropout_wells": [drop_well]})
    drv = WetDriver(port=reader, exp_id="e5", round_id=0)
    drv.submit_goal(otp)
    res = drv.run()
    assert res.outcome == GoalState.SUCCEEDED  # dropout is not a hard failure
    dropped = [r for r in res.readings if r.well_id == drop_well][0]
    assert dropped.value is None and dropped.status == "dropout"
    # its custody chain is still fully traced (dropout != silent loss)
    assert all(res.custody.trace(dropped.sample_id).segments_complete().values())


def test_process_kill_reconnect_aborts(reader_proc):
    proc, port = reader_proc
    spec = make_gradient_spec(n_samples=3, n_controls=0)
    otp = compile_and_validate(spec)
    drv = WetDriver(port=port, exp_id="e6", round_id=0,
                    timeout_s=0.5, max_retries=3)
    drv.submit_goal(otp)
    # kill the device mid-flight: health+lease succeed, then measures hit refused
    h = send(port, {"cmd": "health"})
    assert h["status"] == "healthy"
    proc.kill()
    proc.wait(timeout=5)
    res = drv.run(calibrate=False)
    assert res.outcome == GoalState.ABORTED
    assert res.n_failed == len(otp.wells)
    assert any(e["kind"] in ("measure_retry", "lease_error", "health_error")
               for e in res.events)


# ============================================================ cancel -> CANCELED

def test_bridge_raw_dicts_and_truth_harvest(reader):
    from expos.adapters.wet.bridge import to_raw_dicts
    from expos.adapters.wet.sim_reader import harvest_truth
    spec = make_gradient_spec(n_samples=3, n_controls=1)
    otp = compile_and_validate(spec)
    drv = WetDriver(port=reader, exp_id="e8", round_id=0)
    drv.submit_goal(otp)
    res = drv.run()
    raws = to_raw_dicts(res, metric="solvent_response")
    assert len(raws) == 4
    for rd in raws:
        assert rd["metric"] == "solvent_response"
        assert (rd["cand_id"] is None) != (rd["control_id"] is None)  # exactly one
        assert "sample_id" in rd  # custody key rides along
    # truth harvest is a separate (non-OS) channel; driver readings carry no truth
    truth = harvest_truth(port=reader)
    assert truth and all("true_response" in t for t in truth)
    assert not any(hasattr(r, "true_response") for r in res.readings)


def test_cancel_yields_visible_failures(reader):
    spec = make_gradient_spec(n_samples=6, n_controls=0)
    otp = compile_and_validate(spec)
    drv = WetDriver(port=reader, exp_id="e7", round_id=0)
    drv.submit_goal(otp)
    drv.request_cancel()  # cancel before the first well boundary
    res = drv.run()
    assert res.outcome == GoalState.CANCELED
    assert res.n_failed == len(otp.wells)  # all wells visible-null, none dropped
    assert all(r.status == "canceled" for r in res.readings)
