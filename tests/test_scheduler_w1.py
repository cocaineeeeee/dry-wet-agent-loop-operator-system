"""W1 acceptance tests — minimal lease manager + job-handle abstraction.

Kill-oriented discipline: every guard is paired with a test that goes red if
the guard is deleted. Notably the lease mutual-exclusion test relies on
``O_CREAT | O_EXCL``; a companion out-of-band mutation check (documented in the
W1 completion report) rewrites acquire() to a plain create+truncate and shows
this file's mutex test then observes multiple simultaneous winners.

Leases are coordination-plane state under ``<root>/_scheduler/`` — these tests
assert they never touch a run's events.jsonl (there is no such write at all).

Ssh/Sbatch backends are NOT really submitted here: their command assembly is
pure and unit-tested directly; a real localhost-ssh / real-sbatch run is guarded
by a reachability probe and skipped when unavailable on this host.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from expos.scheduler.jobs import (
    JobError,
    JobResult,
    JobState,
    JobStatus,
    SbatchBackend,
    SubprocessBackend,
    build_sbatch_script,
    build_ssh_artifact_script,
    build_ssh_launch_script,
    build_ssh_poll_script,
    build_ssh_cancel_script,
    parse_sacct,
    parse_squeue_state,
    parse_ssh_artifacts,
    parse_ssh_poll,
    sacct_argv,
    sbatch_submit_argv,
    scancel_argv,
    squeue_argv,
    SLURM_BIN,
    SshBackend,
)
from expos.scheduler.leases import (
    LeaseError,
    LeaseManager,
    ResourceObject,
)


# ======================================================================
# ResourceObject validation guards
# ======================================================================
def test_resource_object_valid():
    r = ResourceObject("spec-01", "instrument")
    assert r.capacity == 1 and r.kind == "instrument"


def test_resource_object_rejects_bad_kind():
    with pytest.raises(ValueError):
        ResourceObject("x", "gpu")  # not in {instrument, compute}


def test_resource_object_rejects_capacity_over_one():
    # Guard: M16 is single-holder; capacity>1 must fail loud, not silently
    # pretend to support concurrency. Delete the check => this test goes red.
    with pytest.raises(ValueError):
        ResourceObject("x", "compute", capacity=2)


def test_resource_id_rejects_path_separators():
    with pytest.raises(ValueError):
        ResourceObject("../escape", "compute")


# ======================================================================
# Lease: basic acquire / release / renew
# ======================================================================
def test_acquire_then_release_roundtrip(tmp_path):
    mgr = LeaseManager(tmp_path)
    lease = mgr.acquire("dev-a", ttl_s=60, tag="worker-1")
    assert lease is not None
    assert lease.resource_id == "dev-a"
    assert lease.holder_pid == os.getpid()
    # Lease lands under _scheduler/leases and NOT in any events.jsonl.
    lease_file = tmp_path / "_scheduler" / "leases" / "dev-a.lease"
    assert lease_file.exists()
    assert not list(tmp_path.rglob("events.jsonl"))
    mgr.release(lease)
    assert not lease_file.exists()


def test_acquire_contended_by_live_holder_returns_none(tmp_path):
    mgr = LeaseManager(tmp_path)
    first = mgr.acquire("dev-a", ttl_s=60, tag="w1")
    assert first is not None
    # Same-process second acquire: holder pid alive + ttl fresh => contention.
    second = mgr.acquire("dev-a", ttl_s=60, tag="w2")
    assert second is None


def test_release_is_idempotent(tmp_path):
    mgr = LeaseManager(tmp_path)
    lease = mgr.acquire("dev-a", ttl_s=60, tag="w1")
    mgr.release(lease)
    # Second release must not raise (file already gone).
    mgr.release(lease)


def test_release_does_not_delete_a_supplanting_holder(tmp_path):
    mgr = LeaseManager(tmp_path)
    lease = mgr.acquire("dev-a", ttl_s=60, tag="w1")
    # Simulate reclamation + re-grant to someone else by overwriting the file.
    lease_file = tmp_path / "_scheduler" / "leases" / "dev-a.lease"
    lease_file.write_text(json.dumps({
        "holder_pid": os.getpid(), "holder_tag": "other",
        "acquired_utc": datetime.now(timezone.utc).isoformat(), "ttl_s": 60,
    }), encoding="utf-8")
    mgr.release(lease)  # our stale lease != on-disk lease
    assert lease_file.exists()  # must not have clobbered the new holder


def test_renew_extends_acquisition_time(tmp_path):
    mgr = LeaseManager(tmp_path)
    lease = mgr.acquire("dev-a", ttl_s=1, tag="w1")
    time.sleep(0.01)
    renewed = mgr.renew(lease, ttl_s=120)
    assert renewed.ttl_s == 120
    assert renewed.acquired_utc >= lease.acquired_utc
    on_disk = json.loads((tmp_path / "_scheduler" / "leases" / "dev-a.lease")
                         .read_text(encoding="utf-8"))
    assert on_disk["ttl_s"] == 120
    assert on_disk["acquired_utc"] == renewed.acquired_utc


def test_renew_after_loss_raises(tmp_path):
    mgr = LeaseManager(tmp_path)
    lease = mgr.acquire("dev-a", ttl_s=60, tag="w1")
    mgr.release(lease)
    with pytest.raises(LeaseError):
        mgr.renew(lease, ttl_s=60)  # no longer held => loud failure


# ======================================================================
# Lease: stale reclamation — the TWO paths (pid death & ttl expiry)
# ======================================================================
def _write_lease(tmp_path: Path, resource_id: str, pid: int,
                 acquired: datetime, ttl_s: float) -> Path:
    d = tmp_path / "_scheduler" / "leases"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{resource_id}.lease"
    p.write_text(json.dumps({
        "holder_pid": pid, "holder_tag": "ghost",
        "acquired_utc": acquired.isoformat(), "ttl_s": ttl_s,
    }), encoding="utf-8")
    return p


def _dead_pid() -> int:
    """Spawn a trivial child, reap it, return its now-dead pid."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def test_stale_reclaim_via_dead_holder_pid(tmp_path, caplog):
    # Path 1: holder pid is gone. Fresh ttl, yet reclaimable because the
    # process is provably dead. Delete the pid-liveness check => acquire would
    # see a live-looking, unexpired lease and return None => red.
    dead = _dead_pid()
    _write_lease(tmp_path, "dev-a", dead,
                 datetime.now(timezone.utc), ttl_s=9999)
    mgr = LeaseManager(tmp_path)
    lease = mgr.acquire("dev-a", ttl_s=60, tag="reclaimer")
    assert lease is not None
    assert lease.holder_pid == os.getpid()


def test_ttl_axis_live_holder_not_grabbable_then_reclaimable(tmp_path):
    # Path 2: holder pid is ALIVE (our own pid) so the pid axis is neutralized
    # and only TTL governs. Within ttl => not grabbable; expired => reclaimable.
    now = datetime.now(timezone.utc)
    mgr = LeaseManager(tmp_path)

    # (a) fresh: live holder + unexpired => contention, not grabbable.
    _write_lease(tmp_path, "dev-a", os.getpid(), now, ttl_s=1000)
    assert mgr.acquire("dev-a", ttl_s=60, tag="x") is None

    # (b) expired: live holder but ancient acquisition => reclaimable.
    _write_lease(tmp_path, "dev-a", os.getpid(),
                 now - timedelta(seconds=1000), ttl_s=10)
    lease = mgr.acquire("dev-a", ttl_s=60, tag="x")
    assert lease is not None


def test_corrupt_lease_file_is_reclaimable(tmp_path):
    d = tmp_path / "_scheduler" / "leases"
    d.mkdir(parents=True, exist_ok=True)
    (d / "dev-a.lease").write_text("{ this is not json", encoding="utf-8")
    mgr = LeaseManager(tmp_path)
    lease = mgr.acquire("dev-a", ttl_s=60, tag="x")
    assert lease is not None


def test_sweep_reaps_stale_keeps_live(tmp_path):
    now = datetime.now(timezone.utc)
    # stale by dead pid
    _write_lease(tmp_path, "dead", _dead_pid(), now, ttl_s=9999)
    # stale by ttl
    _write_lease(tmp_path, "old", os.getpid(), now - timedelta(seconds=1000),
                 ttl_s=1)
    # live + fresh
    _write_lease(tmp_path, "live", os.getpid(), now, ttl_s=9999)
    mgr = LeaseManager(tmp_path)
    reclaimed = set(mgr.sweep())
    assert reclaimed == {"dead", "old"}
    leases = tmp_path / "_scheduler" / "leases"
    assert not (leases / "dead.lease").exists()
    assert not (leases / "old.lease").exists()
    assert (leases / "live.lease").exists()


# ======================================================================
# Lease: R4-E anti-double-start — concurrent acquire, exactly one winner
# ======================================================================
def _acquire_worker(root: str, resource_id: str, barrier, q):
    mgr = LeaseManager(root)
    barrier.wait()  # all children slam acquire at once
    lease = mgr.acquire(resource_id, ttl_s=120, tag=f"pid{os.getpid()}")
    q.put(1 if lease is not None else 0)


def test_concurrent_acquire_exactly_one_winner(tmp_path):
    # R4-E structural fix: N processes race for one resource; O_CREAT|O_EXCL
    # guarantees exactly one create-winner. This is the anti-double-start
    # primitive. (Kill check: rewriting acquire to plain create+truncate makes
    # this assert see N winners — see the W1 report's mutation record.)
    n = 16
    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(n)
    q = ctx.Queue()
    procs = [
        ctx.Process(target=_acquire_worker,
                    args=(str(tmp_path), "shared-dev", barrier, q))
        for _ in range(n)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
    results = [q.get() for _ in range(n)]
    assert sum(results) == 1, f"expected exactly one winner, got {sum(results)}"


# ======================================================================
# JobHandle: SubprocessBackend full lifecycle (echo / sleep / false)
# ======================================================================
def _poll_until_terminal(handle, timeout_s=10.0) -> JobStatus:
    deadline = time.monotonic() + timeout_s
    status = handle.poll()
    while status.state is JobState.RUNNING or status.state is JobState.PENDING:
        if time.monotonic() > deadline:
            raise AssertionError(f"job never terminated (last={status.state})")
        time.sleep(0.02)
        status = handle.poll()
    return status


def test_subprocess_success_and_collect(tmp_path):
    be = SubprocessBackend()
    h = be.submit(["echo", "hello-expos"])
    status = _poll_until_terminal(h)
    assert status.state is JobState.SUCCEEDED
    assert status.returncode == 0
    result = h.collect()
    assert isinstance(result, JobResult)
    assert "hello-expos" in result.stdout
    assert result.artifacts  # stdout/stderr paths listed


def test_subprocess_running_then_succeeds():
    be = SubprocessBackend()
    h = be.submit(["sleep", "1"])
    assert h.poll().state is JobState.RUNNING
    status = _poll_until_terminal(h, timeout_s=5.0)
    assert status.state is JobState.SUCCEEDED


def test_subprocess_failure_carries_returncode():
    be = SubprocessBackend()
    h = be.submit(["sh", "-c", "exit 3"])
    status = _poll_until_terminal(h)
    assert status.state is JobState.FAILED
    assert status.returncode == 3


def test_subprocess_timeout_is_distinct_state():
    # Guard: TIMEOUT must be its own state (retryability discriminator), not
    # folded into FAILED. Delete the deadline check => this sleep would run to
    # completion as SUCCEEDED => red.
    be = SubprocessBackend()
    h = be.submit(["sleep", "10"], timeout_s=0.4)
    status = _poll_until_terminal(h, timeout_s=8.0)
    assert status.state is JobState.TIMEOUT


def test_subprocess_collect_before_success_raises():
    # Guard: collect() before SUCCEEDED must fail loud, not return partial.
    be = SubprocessBackend()
    h = be.submit(["sleep", "5"])
    assert h.poll().state is JobState.RUNNING
    with pytest.raises(JobError):
        h.collect()
    h.cancel()


def test_subprocess_cancel_terminates():
    be = SubprocessBackend()
    h = be.submit(["sleep", "30"])
    assert h.poll().state is JobState.RUNNING
    h.cancel()
    status = _poll_until_terminal(h, timeout_s=5.0)
    assert status.state in (JobState.FAILED, JobState.TIMEOUT)


def test_subprocess_describe_shape():
    be = SubprocessBackend()
    h = be.submit(["echo", "x"])
    d = h.describe()
    assert d["backend"] == "subprocess" and "pid" in d and d["cmd"] == ["echo", "x"]
    _poll_until_terminal(h)


# ======================================================================
# JobHandle: SSH backend — pure command assembly + guarded real run
# ======================================================================
def test_ssh_launch_script_embeds_cmd_and_cwd():
    s = build_ssh_launch_script(["python3", "run.py", "--n", "5"],
                                cwd="/work/dir", job_id="abc123")
    assert "run.py" in s and "/work/dir" in s
    # setsid (not nohup) — the job must lead a fresh session/process group so
    # cancel can group-kill the whole tree (G-3). Delete setsid => group kill
    # only reaches the leader => the G-3 grandchild test would leak.
    assert "setsid" in s and "abc123" in s and "/pid" in s


def test_ssh_launch_script_timeout_wraps_with_timeout_cmd():
    s = build_ssh_launch_script(["sleep", "100"], cwd=None,
                                job_id="j1", timeout_s=30)
    assert "timeout 30" in s


def test_ssh_poll_and_cancel_scripts_reference_pid():
    poll = build_ssh_poll_script("j1")
    assert "kill -0" in poll and "RUNNING" in poll and "DONE" in poll
    cancel = build_ssh_cancel_script("j1")
    # Negative-pid form => signal the whole process group, not just the leader
    # (G-3). The `-- -"$p"` argument is the group-kill tell.
    assert "kill" in cancel and '-- -"$p"' in cancel


@pytest.mark.parametrize("token,expected_state,expected_rc", [
    ("RUNNING", JobState.RUNNING, None),
    ("DONE 0", JobState.SUCCEEDED, 0),
    ("DONE 3", JobState.FAILED, 3),
    ("DONE 124", JobState.TIMEOUT, 124),   # timeout(1) exit code
    ("GONE", JobState.FAILED, None),
])
def test_parse_ssh_poll(token, expected_state, expected_rc):
    st = parse_ssh_poll(token)
    assert st.state is expected_state and st.returncode == expected_rc


def test_parse_ssh_poll_rejects_garbage():
    with pytest.raises(JobError):
        parse_ssh_poll("WAT")


def _ssh_reachable(host: str) -> bool:
    if shutil.which("ssh") is None:
        return False
    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", host, "true"],
            capture_output=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


@pytest.mark.skipif(not _ssh_reachable("localhost"),
                    reason="localhost ssh not reachable (BatchMode); "
                           "ssh backend real-run skipped on this host")
def test_ssh_backend_real_localhost_roundtrip():
    be = SshBackend("localhost")
    h = be.submit(["echo", "ssh-expos-ok"])
    status = _poll_until_terminal(h, timeout_s=15.0)
    assert status.state is JobState.SUCCEEDED
    assert "ssh-expos-ok" in h.collect().stdout


# ======================================================================
# JobHandle: Sbatch backend — pure command assembly + guarded real run
# ======================================================================
def test_sbatch_script_embeds_directives_and_cmd():
    s = build_sbatch_script(["python3", "calc.py"], cwd="/scratch/x",
                            job_name="expos_test", log_path="/tmp/out.log",
                            timeout_s=120)
    assert "#SBATCH --job-name=expos_test" in s
    assert "#SBATCH --output=/tmp/out.log" in s
    assert "#SBATCH --time=2" in s  # 120s -> 2 minutes
    assert "/scratch/x" in s and "calc.py" in s


def test_sbatch_argv_builders_use_slurm_bin():
    assert sbatch_submit_argv("/x/job.sh") == [f"{SLURM_BIN}/sbatch",
                                               "--parsable", "/x/job.sh"]
    assert squeue_argv("42")[:3] == [f"{SLURM_BIN}/squeue", "-j", "42"]
    assert sacct_argv("42")[:3] == [f"{SLURM_BIN}/sacct", "-j", "42"]
    assert scancel_argv("42") == [f"{SLURM_BIN}/scancel", "42"]


@pytest.mark.parametrize("state,expected", [
    ("PENDING", JobState.PENDING),
    ("RUNNING", JobState.RUNNING),
    ("COMPLETED", JobState.SUCCEEDED),
    ("TIMEOUT", JobState.TIMEOUT),
    ("FAILED", JobState.FAILED),
    ("CANCELLED", JobState.FAILED),
])
def test_parse_squeue_state(state, expected):
    assert parse_squeue_state(state).state is expected


def test_parse_squeue_state_rejects_garbage():
    with pytest.raises(JobError):
        parse_squeue_state("BOGUS")


@pytest.mark.parametrize("row,expected_state,expected_rc", [
    ("COMPLETED|0:0", JobState.SUCCEEDED, 0),
    ("FAILED|3:0", JobState.FAILED, 3),
    ("TIMEOUT|0:15", JobState.TIMEOUT, 0),
    ("CANCELLED by 1001|0:0", JobState.FAILED, 0),
    ("RUNNING|0:0", JobState.RUNNING, None),
])
def test_parse_sacct(row, expected_state, expected_rc):
    st = parse_sacct(row)
    assert st.state is expected_state
    if expected_rc is not None:
        assert st.returncode == expected_rc


def _slurm_partition_ready() -> bool:
    sinfo = f"{SLURM_BIN}/sinfo"
    if not Path(sinfo).exists():
        return False
    try:
        r = subprocess.run([sinfo, "-h", "-o", "%a %t"],
                           capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "up" and parts[1] in ("idle", "mix"):
            return True
    return False


@pytest.mark.skipif(not _slurm_partition_ready(),
                    reason="no up+idle/mix Slurm partition available; "
                           "sbatch backend real-run skipped on this host")
def test_sbatch_backend_real_submit(tmp_path):
    be = SbatchBackend(log_dir=tmp_path)
    h = be.submit(["echo", "sbatch-expos-ok"])
    status = _poll_until_terminal(h, timeout_s=120.0)
    assert status.state is JobState.SUCCEEDED


# ================================================================ letter 047: cold-start storm anchor

_STORM_WORKER = r"""
import sys, json, os, time
sys.path.insert(0, {root!r})
from expos.scheduler import LeaseManager
mgr = LeaseManager({sched_root!r})
start_flag, done_flag = {start_flag!r}, {done_flag!r}
while not os.path.exists(start_flag):      # start barrier: true simultaneity
    time.sleep(0.001)
lease = mgr.acquire("instrument:reader", ttl_s=30.0, tag="storm-" + str(os.getpid()))
print(json.dumps({{"won": lease is not None}}), flush=True)
while not os.path.exists(done_flag):       # HOLD the lease until the round ends:
    time.sleep(0.01)                       # an exiting winner is legitimately
                                           # reclaimable via pid-death semantics
                                           # and would fake extra "winners".
"""


def test_cold_start_storm_exactly_one_winner(tmp_path):
    """Regression anchor for the publish-before-payload TOCTOU (letter 047):
    16 processes cold-start-race one lease behind a start barrier, 3 rounds --
    exactly one winner each. Winners HOLD the lease until the round closes,
    because pid-death reclaim of an exited winner is correct lease semantics,
    not a mutex breach (first draft of this test tripped on exactly that).
    Pre-fix create-then-write publishing let EEXIST racers read an empty file,
    judge it corrupt-stale, reclaim and double-win; tmp+os.link publish removes
    the window. Kill-verified: reverting the publish order turns this red."""
    import subprocess, sys, json, time
    root = str(Path(__file__).resolve().parents[1])
    for round_no in range(3):
        sched_root = str(tmp_path / f"storm{round_no}")
        start_flag = str(tmp_path / f"go{round_no}")
        done_flag = str(tmp_path / f"done{round_no}")
        script = _STORM_WORKER.format(root=root, sched_root=sched_root,
                                      start_flag=start_flag, done_flag=done_flag)
        procs = [subprocess.Popen([sys.executable, "-c", script],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for _ in range(16)]
        time.sleep(1.0)                       # let all workers reach the barrier
        Path(start_flag).touch()
        outs = []
        deadline = time.time() + 60
        for pr in procs:                      # read result lines while they hold
            line = pr.stdout.readline()
            outs.append(json.loads(line))
            assert time.time() < deadline
        Path(done_flag).touch()
        for pr in procs:
            pr.wait(timeout=30)
        wins = sum(o["won"] for o in outs)
        assert wins == 1, f"round {round_no}: {wins} winners (mutex breached)"


# ================================================================ letter 051: W3 three-gap closure

# ---------- G-1: collect() recovers declared cwd artifacts ----------
def test_subprocess_collect_recovers_declared_artifact(tmp_path):
    # G-1: a job that writes a FILE product in its cwd must be recoverable via
    # collect(), not only through captured stdout. Declare the product with
    # expected_artifacts; collect() resolves it to a real path.
    be = SubprocessBackend()
    h = be.submit(["sh", "-c", "echo hi > result.json"], cwd=str(tmp_path),
                  expected_artifacts=["result.json"])
    status = _poll_until_terminal(h)
    assert status.state is JobState.SUCCEEDED
    result = h.collect()
    product = str(tmp_path / "result.json")
    assert product in result.artifacts
    assert result.missing_artifacts == []


def test_subprocess_collect_reports_missing_artifact(tmp_path):
    # G-1: a declared product that the job did NOT write is reported in
    # missing_artifacts — a legible provenance gap, never a silent drop.
    # Kill check: dropping the missing-pattern branch => this asserts [] => red.
    be = SubprocessBackend()
    h = be.submit(["sh", "-c", "echo hi > result.json"], cwd=str(tmp_path),
                  expected_artifacts=["result.json", "energies_*.csv"])
    _poll_until_terminal(h)
    result = h.collect()
    assert str(tmp_path / "result.json") in result.artifacts
    assert result.missing_artifacts == ["energies_*.csv"]


def test_subprocess_collect_no_declared_artifacts_is_backward_compatible():
    # Additivity: with no expected_artifacts (the pre-051 call), collect() is
    # unchanged — capture logs present, missing list empty.
    be = SubprocessBackend()
    h = be.submit(["echo", "hello-expos"])
    _poll_until_terminal(h)
    result = h.collect()
    assert result.artifacts and result.missing_artifacts == []


def test_ssh_artifact_script_and_parser_roundtrip():
    # G-1 (ssh path, pure): the remote resolver emits F <path> / M <pattern>,
    # and the parser splits them back into (found, missing).
    s = build_ssh_artifact_script("/work/dir", ["result.json", "*.csv"])
    assert "/work/dir" in s and "result.json" in s and "*.csv" in s
    assert "echo F" in s and "echo M" in s
    found, missing = parse_ssh_artifacts(
        "F /work/dir/result.json\nM *.csv\n\n"
    )
    assert found == ["/work/dir/result.json"] and missing == ["*.csv"]


# ---------- G-2: backend-agnostic failure_detail() channel ----------
def test_subprocess_failure_detail_reads_error_json(tmp_path):
    # G-2: a failed job's self-reported error.json is legible via
    # failure_detail() in a terminal state, WITHOUT collect()'s SUCCEEDED gate.
    # Field names align with the W3 worker: {reason, detail}.
    be = SubprocessBackend()
    script = (
        "printf '%s' '{\"reason\": \"convergence\", \"detail\": \"SCF stalled\"}' "
        "> error.json; exit 10"
    )
    h = be.submit(["sh", "-c", script], cwd=str(tmp_path))
    status = _poll_until_terminal(h)
    assert status.state is JobState.FAILED and status.returncode == 10
    # collect() still refuses (not SUCCEEDED) — failure_detail is the way in.
    with pytest.raises(JobError):
        h.collect()
    detail = h.failure_detail()
    assert detail == {"reason": "convergence", "detail": "SCF stalled"}


def test_subprocess_failure_detail_absent_returns_none(tmp_path):
    # No error.json written => None, never a raise.
    be = SubprocessBackend()
    h = be.submit(["sh", "-c", "exit 3"], cwd=str(tmp_path))
    _poll_until_terminal(h)
    assert h.failure_detail() is None


def test_subprocess_describe_attaches_failure_detail_on_failure(tmp_path):
    # G-2: describe() folds the failure record in on FAILED/TIMEOUT so a single
    # diagnostic call surfaces the cause.
    be = SubprocessBackend()
    script = (
        "printf '%s' '{\"reason\": \"worker_error\", \"detail\": \"boom\"}' "
        "> error.json; exit 20"
    )
    h = be.submit(["sh", "-c", script], cwd=str(tmp_path))
    _poll_until_terminal(h)
    d = h.describe()
    assert d["failure_detail"] == {"reason": "worker_error", "detail": "boom"}


def test_subprocess_describe_omits_failure_detail_on_success():
    # A succeeding job carries no failure_detail key.
    be = SubprocessBackend()
    h = be.submit(["echo", "ok"])
    _poll_until_terminal(h)
    assert "failure_detail" not in h.describe()


# ---------- G-3: process-group kill reaps grandchildren ----------
def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_subprocess_cancel_kills_whole_process_group(tmp_path):
    # G-3: start_new_session=True + os.killpg must reap a GRANDCHILD the job
    # forked, not just the direct child. The job spawns a long `sleep`, records
    # its pid, then blocks; after cancel() the grandchild must be dead too.
    #
    # Kill-verification (performed once, documented here): replacing the
    # _kill_group(SIGTERM) call in cancel() with the old self._proc.terminate()
    # leaves the orphaned `sleep` grandchild alive (reparented to init) and this
    # assert goes red — proving the group-kill is load-bearing, not decorative.
    gpid_file = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, time\n"
        "gc = subprocess.Popen(['sleep', '300'])\n"
        f"open({str(gpid_file)!r}, 'w').write(str(gc.pid))\n"
        "time.sleep(300)\n"
    )
    be = SubprocessBackend()
    h = be.submit([sys.executable, "-c", script])
    # Wait until the grandchild pid has been recorded.
    deadline = time.monotonic() + 10.0
    while not gpid_file.exists() or not gpid_file.read_text().strip():
        assert time.monotonic() < deadline, "grandchild never spawned"
        time.sleep(0.02)
    gpid = int(gpid_file.read_text().strip())
    assert _pid_alive(gpid), "grandchild should be alive before cancel"

    h.cancel()

    # The whole group (child + grandchild) must die.
    deadline = time.monotonic() + 5.0
    while _pid_alive(gpid):
        if time.monotonic() > deadline:
            # Clean up the leak before failing so the test host stays tidy.
            try:
                os.kill(gpid, 9)
            except ProcessLookupError:
                pass
            raise AssertionError("grandchild survived cancel() — group not killed")
        time.sleep(0.02)
