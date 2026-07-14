"""Job lifecycle four-path taxonomy (G2), driven through the real
expos.scheduler backend: success / external-kill / timeout / non-convergence.
"""

from __future__ import annotations

import os
import signal
import time

import pytest

from expos.scheduler import JobState, SubprocessBackend

from expos.adapters.dry.adapter import PySCFDryAdapter
from expos.adapters.dry.spec import JobSpec

_TERMINAL = {JobState.SUCCEEDED, JobState.FAILED, JobState.TIMEOUT}


@pytest.fixture
def adapter(tmp_path):
    return PySCFDryAdapter(jobs_root=tmp_path / "jobs", poll_interval_s=0.1)


def _poll_until_terminal(adapter, run, wall_s=90.0):
    deadline = time.time() + wall_s
    while True:
        st = adapter.poll(run)
        if st in _TERMINAL:
            return st
        assert time.time() < deadline, "job did not reach a terminal state in time"
        time.sleep(0.1)


def _spec(**kw) -> JobSpec:
    base = dict(job_id="exp:0:A1", well_id="A1", cand_id="cand_1", solvent="water")
    base.update(kw)
    return JobSpec(**base)


def _to_states(run):
    return [e.to_state for e in run.events]


# ---------------------------------------------------------------- success

def test_success_path(adapter):
    backend = SubprocessBackend()
    run = adapter.submit(_spec(), backend)
    st = _poll_until_terminal(adapter, run)
    assert st is JobState.SUCCEEDED

    dry_raw, failure = adapter.collect(run)
    assert failure is None
    assert dry_raw is not None
    assert dry_raw.converged is True
    assert dry_raw.value > 1.0  # water dipole ~1.7 D
    assert "total_energy_hartree" in dry_raw.secondary
    assert dry_raw.engine == "pyscf"
    assert dry_raw.raw_uri.endswith("result.json")
    assert os.path.exists(dry_raw.raw_uri)
    # lifecycle recorded event-by-event: accepted -> spawned -> terminal
    assert _to_states(run) == ["PENDING", "RUNNING", "SUCCEEDED"]


# ---------------------------------------------------------------- external kill

def test_kill_midjob_is_failed_signal(adapter):
    backend = SubprocessBackend()
    # sleep_s opens a killable window (imports ~1s, then a long sleep).
    run = adapter.submit(_spec(sleep_s=30.0, timeout_s=120.0), backend)
    assert adapter.poll(run) is JobState.RUNNING
    time.sleep(2.0)  # let the worker get past imports into the sleep

    pid = run.handle.describe()["pid"]
    os.kill(pid, signal.SIGKILL)

    st = _poll_until_terminal(adapter, run)
    assert st is JobState.FAILED
    _, failure = adapter.collect(run)
    assert failure.reason == "signal"
    assert failure.signal == signal.SIGKILL
    assert failure.retryable is True          # infra/transient -> retryable
    assert _to_states(run)[-1] == "FAILED"


# ---------------------------------------------------------------- timeout

def test_timeout_terminates_and_is_timeout_state(adapter):
    backend = SubprocessBackend()
    run = adapter.submit(_spec(sleep_s=60.0, timeout_s=2.0), backend)
    st = _poll_until_terminal(adapter, run)
    assert st is JobState.TIMEOUT
    _, failure = adapter.collect(run)
    assert failure.reason == "timeout"
    assert failure.retryable is True
    # the process must really be gone (backend killed the group/process)
    pid = run.handle.describe()["pid"]
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


# ---------------------------------------------------------------- convergence

def test_scf_nonconvergence_is_failed_convergence(adapter):
    backend = SubprocessBackend()
    # Constructed non-convergence: 1 SCF cycle with DIIS off and a tight tol.
    run = adapter.submit(
        _spec(max_cycle=1, disable_diis=True, conv_tol=1e-12), backend
    )
    st = _poll_until_terminal(adapter, run)
    assert st is JobState.FAILED
    _, failure = adapter.collect(run)
    assert failure.reason == "convergence"
    assert failure.exit_code == 10
    assert failure.retryable is True          # retry with different SCF settings
    assert "converge" in failure.detail.lower()


# ------------------------------------------------ engine crash containment

def test_b3lyp_engine_crash_is_contained(adapter):
    """A segfaulting engine (libxc DFT crashes on this host) must be contained
    as a terminal job outcome, NOT crash the driving loop. This is the core
    value of out-of-process execution (G2: 'kill mid-job -> not a naked crash').
    If libxc were healthy the job would SUCCEED; either way the loop survives."""
    backend = SubprocessBackend()
    run = adapter.submit(_spec(method="B3LYP", timeout_s=120.0), backend)
    st = _poll_until_terminal(adapter, run)
    assert st in _TERMINAL                      # loop survived, job is terminal
    dry_raw, failure = adapter.collect(run)
    if st is JobState.FAILED:
        # a segfault surfaces as a signal failure, cleanly classified
        assert failure.reason in ("signal", "worker_error")
    else:
        assert dry_raw is not None and dry_raw.method == "B3LYP"


# ------------------------------------------------ missing-artifact = loud fail

def test_succeeded_but_missing_artifact_is_loud_failure(adapter):
    """A job that reports SUCCEEDED but whose declared product ('result.json')
    is absent must surface as a LOUD ``missing_artifact`` failure, never a silent
    fallback (letter 060, G-1). We construct the case by removing the collected
    product after a genuine success: poll() still says SUCCEEDED, but the
    declared artifact is now missing."""
    backend = SubprocessBackend()
    run = adapter.submit(_spec(), backend)
    st = _poll_until_terminal(adapter, run)
    assert st is JobState.SUCCEEDED

    # remove the declared product -> collect() must report it missing, loudly
    (run.workdir / "result.json").unlink()

    dry_raw, failure = adapter.collect(run)
    assert dry_raw is None
    assert failure is not None
    assert failure.reason == "missing_artifact"
    assert failure.retryable is False          # a vanished product is not transient
    assert "result.json" in failure.detail


# ---------------------------------------------------------------- cancel

def test_cancel_is_terminal_and_classified(adapter):
    backend = SubprocessBackend()
    run = adapter.submit(_spec(sleep_s=30.0, timeout_s=120.0), backend)
    assert adapter.poll(run) is JobState.RUNNING
    time.sleep(2.0)
    run.handle.cancel()
    st = _poll_until_terminal(adapter, run)
    assert st is JobState.FAILED           # cancel surfaces as a signal failure
    _, failure = adapter.collect(run)
    assert failure.reason == "signal"
    assert failure.retryable is True
