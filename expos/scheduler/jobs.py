"""Job handle abstraction over three interchangeable execution backends.

A JobHandle models the lifecycle of one external job with a uniform verb set:

    submit(cmd, cwd, env, timeout_s, expected_artifacts) -> JobHandle
    handle.poll()            -> JobStatus (PENDING/RUNNING/SUCCEEDED/FAILED/TIMEOUT)
    handle.collect()         -> JobResult (stdout + declared artifacts; SUCCEEDED-only)
    handle.cancel()          -> None
    handle.failure_detail()  -> dict|None (backend-agnostic failure channel)
    handle.describe()        -> dict

TIMEOUT is a first-class terminal state, deliberately *distinct* from FAILED:
it is the retryability-class discriminator (a timed-out job is a candidate for
retry; a FAILED(rc) job carries a real exit code to classify). ``collect`` is
the artifact-collection verb — calling it before the job reached SUCCEEDED
raises loudly rather than returning partial/garbage output.

Three cross-backend affordances requested by W3 (the dry PySCF adapter) that
originally had to be worked around adapter-side:

- **Artifact collection (G-1).** ``submit(..., expected_artifacts=[glob, ...])``
  declares the *file* products a job writes in its ``cwd``. ``collect()`` then
  resolves those globs and reports both what was found and what is missing, so
  a job that writes ``result.json`` recovers it portably rather than only
  through captured stdout. Explicit declaration beats snapshot-diffing magic.
- **Failure detail (G-2).** ``failure_detail()`` is a terminal-state-safe
  reader for a job's self-reported ``<cwd>/error.json`` ({reason, detail, ...}),
  written voluntarily by the worker. It returns ``None`` when absent and never
  raises, so a failed job's cause is legible without ``collect()``'s
  SUCCEEDED-only guard getting in the way. ``describe()`` folds it in on
  FAILED/TIMEOUT.
- **Process-group isolation (G-3).** The subprocess backend starts each job in
  a new session (``start_new_session=True``) and ``cancel``/timeout signal the
  whole process group via ``os.killpg`` — grandchildren of a forking job are
  reaped, not leaked. Ssh mirrors this with ``setsid`` + ``kill -- -PGID``;
  Slurm's ``scancel`` already has whole-step semantics.

Backend selection is explicit (constructed by the caller); there is no
auto-probing. Explicit beats magic:

    SubprocessBackend()        local Popen
    SshBackend(host)           direct ssh: nohup + pid file + `kill -0` poll
    SbatchBackend()            Slurm: sbatch --parsable / squeue / sacct / scancel

The ssh/sbatch command-assembly helpers are pure module functions so they can
be unit-tested without ever contacting a remote host or a scheduler.
"""

from __future__ import annotations

import abc
import json
import os
import shlex
import signal
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# Slurm lives outside the default PATH on this cluster.
SLURM_BIN = "/opt/slurm/bin"


class JobState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


_TERMINAL_STATES = (JobState.SUCCEEDED, JobState.FAILED, JobState.TIMEOUT)


class JobError(Exception):
    """Raised on job-handle contract violations (e.g. collect() before the
    job reached SUCCEEDED, or a backend probe that cannot be interpreted)."""


@dataclass(frozen=True)
class JobStatus:
    state: JobState
    returncode: int | None = None


@dataclass(frozen=True)
class JobResult:
    """Output of a successful job: captured stdout, resolved artifact paths, and
    the list of declared ``expected_artifacts`` globs that matched nothing.

    ``artifacts`` always includes the backend's own capture logs (stdout/stderr)
    and then any files matched from ``expected_artifacts`` (G-1). A non-empty
    ``missing_artifacts`` means the job succeeded but did not produce a declared
    product — a legible provenance signal rather than a silent gap."""

    stdout: str
    artifacts: list[str] = field(default_factory=list)
    returncode: int | None = None
    missing_artifacts: list[str] = field(default_factory=list)


def _collect_local_artifacts(
    base_dir: str | os.PathLike[str] | None, patterns: list[str] | None
) -> tuple[list[str], list[str]]:
    """Resolve ``patterns`` (globs relative to ``base_dir``) on the local/shared
    filesystem. Returns ``(found_abspaths, missing_patterns)``. Pure w.r.t. the
    filesystem it reads; no writes. A pattern that matches nothing is reported
    as missing rather than silently dropped."""
    if not patterns or base_dir is None:
        return [], []
    base = Path(base_dir)
    found: list[str] = []
    missing: list[str] = []
    for pat in patterns:
        matches = sorted(str(m) for m in base.glob(pat) if m.exists())
        if matches:
            found.extend(matches)
        else:
            missing.append(pat)
    return found, missing


def _read_error_json(
    base_dir: str | os.PathLike[str] | None,
) -> dict | None:
    """Best-effort read of ``<base_dir>/error.json`` (G-2). Returns the parsed
    object, or ``None`` if the file is absent, unreadable, malformed, or not a
    JSON object. Never raises — any terminal state may call it."""
    if base_dir is None:
        return None
    p = Path(base_dir) / "error.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _attach_failure_detail(handle: "JobHandle", d: dict) -> None:
    """Fold a job's failure record into its ``describe()`` output, but only in
    the FAILED/TIMEOUT terminal states. Best-effort: any error polling the
    backend or reading the record is swallowed so ``describe()`` never raises."""
    try:
        state = handle.poll().state
        if state in (JobState.FAILED, JobState.TIMEOUT):
            detail = handle.failure_detail()
            if detail is not None:
                d["failure_detail"] = detail
    except Exception:  # noqa: BLE001 — describe() must stay side-effect-safe
        return  # diagnostic-only: on any backend error, omit the detail key


class JobHandle(abc.ABC):
    """Uniform handle over one submitted job."""

    @abc.abstractmethod
    def poll(self) -> JobStatus:
        ...

    @abc.abstractmethod
    def collect(self) -> JobResult:
        ...

    @abc.abstractmethod
    def cancel(self) -> None:
        ...

    @abc.abstractmethod
    def failure_detail(self) -> dict | None:
        """Return the job's self-reported failure record (``<cwd>/error.json``:
        ``{reason, detail, ...}``), or ``None`` if none was written. Valid in
        any terminal state; never raises. Backend-agnostic — the worker writes
        the file voluntarily and each backend reads it wherever ``cwd`` lives."""
        ...

    @abc.abstractmethod
    def describe(self) -> dict:
        ...


class JobBackend(abc.ABC):
    """Factory for JobHandles. One backend == one execution substrate."""

    @abc.abstractmethod
    def submit(
        self,
        cmd: list[str],
        cwd: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float | None = None,
        expected_artifacts: list[str] | None = None,
    ) -> JobHandle:
        ...


# ======================================================================
# Subprocess backend
# ======================================================================
class _SubprocessJobHandle(JobHandle):
    def __init__(
        self,
        proc: subprocess.Popen,
        cmd: list[str],
        job_dir: Path,
        stdout_path: Path,
        stderr_path: Path,
        timeout_s: float | None,
        cwd: str | os.PathLike[str] | None = None,
        expected_artifacts: list[str] | None = None,
    ) -> None:
        self._proc = proc
        self._cmd = cmd
        self._job_dir = job_dir
        self._stdout_path = stdout_path
        self._stderr_path = stderr_path
        self._deadline = None if timeout_s is None else time.monotonic() + timeout_s
        self._timed_out = False
        self._cwd = cwd
        self._expected_artifacts = list(expected_artifacts or [])

    def _artifact_base(self) -> Path:
        # Job products land in the job's cwd; without one, fall back to the
        # per-job temp dir (still where our capture logs live).
        return Path(self._cwd) if self._cwd is not None else self._job_dir

    def _kill_group(self, sig: int) -> None:
        """Signal the whole process group. ``start_new_session=True`` at submit
        makes the child a session/group leader, so its pgid equals its pid and
        ``killpg`` reaches every grandchild it forked. Falls back to a direct
        signal if the group is already gone (race with natural exit)."""
        try:
            os.killpg(self._proc.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                self._proc.send_signal(sig)
            except (ProcessLookupError, OSError):
                return  # process already reaped between the two signals — done

    def poll(self) -> JobStatus:
        if self._timed_out:
            return JobStatus(JobState.TIMEOUT, self._proc.returncode)
        rc = self._proc.poll()
        if rc is None:
            if self._deadline is not None and time.monotonic() > self._deadline:
                self._kill_group(signal.SIGKILL)
                # Bounded reap so the state settles; the rc is the signal code.
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired as exc:
                    raise JobError(
                        f"timed-out job pid={self._proc.pid} did not die after kill"
                    ) from exc
                self._timed_out = True
                return JobStatus(JobState.TIMEOUT, self._proc.returncode)
            return JobStatus(JobState.RUNNING)
        if rc == 0:
            return JobStatus(JobState.SUCCEEDED, 0)
        return JobStatus(JobState.FAILED, rc)

    def collect(self) -> JobResult:
        status = self.poll()
        if status.state is not JobState.SUCCEEDED:
            raise JobError(
                f"collect() requires SUCCEEDED, job is {status.state.value} "
                f"(rc={status.returncode})"
            )
        stdout = self._stdout_path.read_text(encoding="utf-8", errors="replace")
        found, missing = _collect_local_artifacts(
            self._artifact_base(), self._expected_artifacts
        )
        return JobResult(
            stdout=stdout,
            artifacts=[str(self._stdout_path), str(self._stderr_path), *found],
            returncode=0,
            missing_artifacts=missing,
        )

    def failure_detail(self) -> dict | None:
        return _read_error_json(self._artifact_base())

    def cancel(self) -> None:
        if self._proc.poll() is None:
            self._kill_group(signal.SIGTERM)
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._kill_group(signal.SIGKILL)

    def describe(self) -> dict:
        d = {
            "backend": "subprocess",
            "pid": self._proc.pid,
            "cmd": list(self._cmd),
            "job_dir": str(self._job_dir),
            "stdout": str(self._stdout_path),
            "stderr": str(self._stderr_path),
        }
        _attach_failure_detail(self, d)
        return d


class SubprocessBackend(JobBackend):
    """Runs jobs as local child processes via ``subprocess.Popen``.

    stdout/stderr are streamed to files in a per-job temp dir so that
    ``collect()`` can read them back without PIPE deadlock risk."""

    def submit(
        self,
        cmd: list[str],
        cwd: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float | None = None,
        expected_artifacts: list[str] | None = None,
    ) -> JobHandle:
        job_dir = Path(tempfile.mkdtemp(prefix="expos_job_"))
        stdout_path = job_dir / "stdout.log"
        stderr_path = job_dir / "stderr.log"
        out_f = stdout_path.open("wb")
        err_f = stderr_path.open("wb")
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            stdout=out_f,
            stderr=err_f,
            # New session => the child is its own process-group leader, so
            # cancel()/timeout can killpg the whole tree (G-3): a job that forks
            # grandchildren no longer leaks them on termination.
            start_new_session=True,
        )
        # The child inherits its own dup'd fds; the parent closes its copies so
        # collect() sees a complete file once the child exits.
        out_f.close()
        err_f.close()
        return _SubprocessJobHandle(
            proc, cmd, job_dir, stdout_path, stderr_path, timeout_s,
            cwd=cwd, expected_artifacts=expected_artifacts,
        )


# ======================================================================
# SSH backend — pure command assembly + thin execution wrapper
# ======================================================================
def ssh_job_dir(job_id: str) -> str:
    """Remote scratch dir holding this job's pid/rc/log files."""
    return f"$HOME/.expos_jobs/{job_id}"


def build_ssh_launch_script(
    cmd: list[str],
    cwd: str | None,
    job_id: str,
    timeout_s: float | None = None,
) -> str:
    """Assemble the remote shell script that starts ``cmd`` detached and records
    its pid and, on completion, its return code. Pure — safe to unit-test.

    Layout under ssh_job_dir(job_id): ``pid``, ``rc``, ``log``. ``timeout(1)``
    enforces the wall clock and reports 124 on expiry (mapped to TIMEOUT).
    ``setsid`` puts the job in a fresh session/process group (pgid == recorded
    pid), so cancel can ``kill -- -PGID`` the whole tree (G-3) and the job is
    immune to SIGHUP on ssh disconnect (subsuming nohup)."""
    d = ssh_job_dir(job_id)
    inner = shlex.join(cmd)
    if timeout_s is not None:
        inner = f"timeout {int(timeout_s)} sh -c {shlex.quote(inner)}"
    cd = f"cd {shlex.quote(cwd)} && " if cwd else ""
    runner = f"{cd}{inner}; echo $? > {d}/rc"
    return (
        f"mkdir -p {d} && "
        f"setsid sh -c {shlex.quote(runner)} > {d}/log 2>&1 & "
        f"echo $! > {d}/pid; cat {d}/pid"
    )


def build_ssh_poll_script(job_id: str) -> str:
    """Emit one token: RUNNING | DONE <rc> | GONE (process died, no rc file)."""
    d = ssh_job_dir(job_id)
    return (
        f"p=$(cat {d}/pid 2>/dev/null); "
        f"if [ -n \"$p\" ] && kill -0 \"$p\" 2>/dev/null; then echo RUNNING; "
        f"elif [ -f {d}/rc ]; then echo DONE $(cat {d}/rc); "
        f"else echo GONE; fi"
    )


def build_ssh_cancel_script(job_id: str) -> str:
    # The recorded pid is the session/process-group leader (setsid at launch),
    # so the negative-pid form signals the ENTIRE group — grandchildren the job
    # forked die too, not just the leader (G-3).
    d = ssh_job_dir(job_id)
    return (
        f"p=$(cat {d}/pid 2>/dev/null); "
        f"[ -n \"$p\" ] && kill -TERM -- -\"$p\" 2>/dev/null; "
        f"[ -n \"$p\" ] && kill -KILL -- -\"$p\" 2>/dev/null; true"
    )


def build_ssh_artifact_script(cwd: str | None, patterns: list[str]) -> str:
    """Emit one line per declared artifact glob (resolved in the remote shell,
    relative to ``cwd``): ``F <abspath>`` for each existing match, ``M <pattern>``
    when a glob matches nothing. Pure — safe to unit-test."""
    cd = f"cd {shlex.quote(cwd)} && " if cwd else ""
    parts = []
    for pat in patterns:
        # An unmatched glob stays literal in the shell => the [ -e ] test fails
        # => reported as missing. A matched glob yields real paths => absolute.
        parts.append(
            f'for f in {pat}; do if [ -e "$f" ]; then '
            f'echo F "$(pwd)/$f"; else echo M {shlex.quote(pat)}; fi; done'
        )
    return cd + "; ".join(parts)


def parse_ssh_artifacts(text: str) -> tuple[list[str], list[str]]:
    """Parse ``build_ssh_artifact_script`` output into ``(found, missing)``."""
    found: list[str] = []
    missing: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        tag, _, rest = line.partition(" ")
        if tag == "F":
            found.append(rest)
        elif tag == "M":
            missing.append(rest)
    return found, missing


def parse_ssh_poll(token: str) -> JobStatus:
    """Map a poll token to a JobStatus. rc 124 (timeout(1)) => TIMEOUT."""
    parts = token.split()
    if not parts:
        raise JobError("empty ssh poll token")
    head = parts[0]
    if head == "RUNNING":
        return JobStatus(JobState.RUNNING)
    if head == "GONE":
        # Process vanished without leaving an rc (killed / node reboot).
        return JobStatus(JobState.FAILED, None)
    if head == "DONE":
        rc = int(parts[1]) if len(parts) > 1 else None
        if rc == 0:
            return JobStatus(JobState.SUCCEEDED, 0)
        if rc == 124:
            return JobStatus(JobState.TIMEOUT, rc)
        return JobStatus(JobState.FAILED, rc)
    raise JobError(f"unrecognized ssh poll token: {token!r}")


class _SshJobHandle(JobHandle):
    def __init__(
        self,
        host: str,
        job_id: str,
        cmd: list[str],
        pid: str | None,
        cwd: str | None = None,
        expected_artifacts: list[str] | None = None,
    ) -> None:
        self._host = host
        self._job_id = job_id
        self._cmd = cmd
        self._pid = pid
        self._cwd = cwd
        self._expected_artifacts = list(expected_artifacts or [])

    def _ssh(self, script: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["ssh", self._host, script],
            capture_output=True,
            text=True,
            check=False,
        )

    def poll(self) -> JobStatus:
        res = self._ssh(build_ssh_poll_script(self._job_id))
        if res.returncode != 0:
            raise JobError(
                f"ssh poll of {self._host}:{self._job_id} failed "
                f"(rc={res.returncode}): {res.stderr.strip()}"
            )
        return parse_ssh_poll(res.stdout.strip())

    def collect(self) -> JobResult:
        status = self.poll()
        if status.state is not JobState.SUCCEEDED:
            raise JobError(
                f"collect() requires SUCCEEDED, job is {status.state.value}"
            )
        log_path = f"{ssh_job_dir(self._job_id)}/log"
        res = self._ssh(f"cat {log_path}")
        if res.returncode != 0:
            raise JobError(
                f"ssh collect of {self._host}:{self._job_id} failed: "
                f"{res.stderr.strip()}"
            )
        found: list[str] = []
        missing: list[str] = []
        if self._expected_artifacts:
            # One extra round-trip only when products were declared (G-1); the
            # remote filesystem is not visible to a local glob.
            ares = self._ssh(
                build_ssh_artifact_script(self._cwd, self._expected_artifacts)
            )
            if ares.returncode == 0:
                found, missing = parse_ssh_artifacts(ares.stdout)
            else:
                missing = list(self._expected_artifacts)
        return JobResult(
            stdout=res.stdout,
            artifacts=[log_path, *found],
            returncode=0,
            missing_artifacts=missing,
        )

    def failure_detail(self) -> dict | None:
        if self._cwd is None:
            return None
        # error.json lives in the remote cwd; read it over the channel (G-2).
        res = self._ssh(f"cat {shlex.quote(self._cwd)}/error.json 2>/dev/null")
        if res.returncode != 0 or not res.stdout.strip():
            return None
        try:
            data = json.loads(res.stdout)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def cancel(self) -> None:
        self._ssh(build_ssh_cancel_script(self._job_id))

    def describe(self) -> dict:
        d = {
            "backend": "ssh",
            "host": self._host,
            "job_id": self._job_id,
            "remote_pid": self._pid,
            "cmd": list(self._cmd),
        }
        _attach_failure_detail(self, d)
        return d


class SshBackend(JobBackend):
    """Runs jobs over a direct (pre-authorized) ssh channel using setsid + a pid
    file + ``kill -0`` polling. No agent/daemon on the far side."""

    def __init__(self, host: str) -> None:
        self.host = host

    def submit(
        self,
        cmd: list[str],
        cwd: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float | None = None,
        expected_artifacts: list[str] | None = None,
    ) -> JobHandle:
        job_id = uuid.uuid4().hex
        cwd_str = str(cwd) if cwd is not None else None
        # The remote shell inherits nothing; env is applied via an `env` prefix.
        effective_cmd = cmd
        if env:
            effective_cmd = ["env", *[f"{k}={v}" for k, v in env.items()], *cmd]
        script = build_ssh_launch_script(
            effective_cmd, cwd_str, job_id, timeout_s
        )
        res = subprocess.run(
            ["ssh", self.host, script],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise JobError(
                f"ssh submit to {self.host} failed (rc={res.returncode}): "
                f"{res.stderr.strip()}"
            )
        pid = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else None
        return _SshJobHandle(
            self.host, job_id, cmd, pid,
            cwd=cwd_str, expected_artifacts=expected_artifacts,
        )


# ======================================================================
# Sbatch (Slurm) backend — pure command assembly + thin execution wrapper
# ======================================================================
def build_sbatch_script(
    cmd: list[str],
    cwd: str | None,
    job_name: str,
    log_path: str,
    timeout_s: float | None = None,
) -> str:
    """Assemble the batch script text handed to ``sbatch``. Pure."""
    lines = ["#!/bin/sh", f"#SBATCH --job-name={job_name}", f"#SBATCH --output={log_path}"]
    if timeout_s is not None:
        # Slurm wants HH:MM:SS; ceil to whole minutes (minimum 1).
        minutes = max(1, (int(timeout_s) + 59) // 60)
        lines.append(f"#SBATCH --time={minutes}")
    if cwd:
        lines.append(f"cd {shlex.quote(cwd)}")
    lines.append(shlex.join(cmd))
    return "\n".join(lines) + "\n"


def sbatch_submit_argv(script_path: str) -> list[str]:
    return [f"{SLURM_BIN}/sbatch", "--parsable", script_path]


def squeue_argv(job_id: str) -> list[str]:
    return [f"{SLURM_BIN}/squeue", "-j", job_id, "-h", "-o", "%T"]


def sacct_argv(job_id: str) -> list[str]:
    return [f"{SLURM_BIN}/sacct", "-j", job_id, "-n", "-P",
            "--format=State,ExitCode"]


def scancel_argv(job_id: str) -> list[str]:
    return [f"{SLURM_BIN}/scancel", job_id]


def parse_squeue_state(state: str) -> JobStatus:
    """Map a live squeue %T state to a JobStatus (job still in the queue)."""
    s = state.strip().upper()
    if s in ("PENDING", "CONFIGURING", "REQUEUED", "SUSPENDED"):
        return JobStatus(JobState.PENDING)
    if s in ("RUNNING", "COMPLETING"):
        return JobStatus(JobState.RUNNING)
    if s == "TIMEOUT":
        return JobStatus(JobState.TIMEOUT)
    if s in ("COMPLETED",):
        return JobStatus(JobState.SUCCEEDED, 0)
    if s in ("FAILED", "CANCELLED", "NODE_FAIL", "BOOT_FAIL", "OUT_OF_MEMORY",
             "DEADLINE", "PREEMPTED"):
        return JobStatus(JobState.FAILED)
    raise JobError(f"unrecognized squeue state: {state!r}")


def parse_sacct(line: str) -> JobStatus:
    """Map an sacct ``State|ExitCode`` accounting row to a JobStatus.

    Used once the job has left the queue; ExitCode is ``code:signal``."""
    fields = line.strip().split("|")
    state = fields[0].strip().upper() if fields else ""
    rc: int | None = None
    if len(fields) > 1 and fields[1].strip():
        code = fields[1].split(":")[0].strip()
        if code.isdigit():
            rc = int(code)
    # sacct decorates cancellations as "CANCELLED by <uid>"
    state = state.split()[0] if state else ""
    if state == "COMPLETED":
        return JobStatus(JobState.SUCCEEDED, rc if rc is not None else 0)
    if state == "TIMEOUT":
        return JobStatus(JobState.TIMEOUT, rc)
    if state in ("PENDING", "REQUEUED"):
        return JobStatus(JobState.PENDING)
    if state in ("RUNNING", "COMPLETING"):
        return JobStatus(JobState.RUNNING)
    if state in ("FAILED", "CANCELLED", "NODE_FAIL", "BOOT_FAIL", "OUT_OF_MEMORY",
                 "DEADLINE", "PREEMPTED"):
        return JobStatus(JobState.FAILED, rc)
    raise JobError(f"unrecognized sacct state: {line!r}")


class _SbatchJobHandle(JobHandle):
    def __init__(self, job_id: str, cmd: list[str], log_path: str,
                 script_path: str, cwd: str | None = None,
                 expected_artifacts: list[str] | None = None) -> None:
        self._job_id = job_id
        self._cmd = cmd
        self._log_path = log_path
        self._script_path = script_path
        self._cwd = cwd
        self._expected_artifacts = list(expected_artifacts or [])

    def _artifact_base(self) -> str:
        # Slurm jobs run against a shared filesystem, so products in the job's
        # cwd are readable locally; without a cwd, fall back to the log dir.
        return self._cwd if self._cwd is not None else str(Path(self._log_path).parent)

    def poll(self) -> JobStatus:
        q = subprocess.run(squeue_argv(self._job_id), capture_output=True,
                           text=True, check=False)
        live = q.stdout.strip()
        if live:
            return parse_squeue_state(live.splitlines()[0])
        # Left the queue — consult accounting for the terminal verdict.
        a = subprocess.run(sacct_argv(self._job_id), capture_output=True,
                           text=True, check=False)
        rows = [r for r in a.stdout.splitlines() if r.strip()]
        if not rows:
            raise JobError(
                f"job {self._job_id} absent from both squeue and sacct — "
                "cannot determine terminal state"
            )
        return parse_sacct(rows[0])

    def collect(self) -> JobResult:
        status = self.poll()
        if status.state is not JobState.SUCCEEDED:
            raise JobError(
                f"collect() requires SUCCEEDED, job is {status.state.value}"
            )
        stdout = ""
        p = Path(self._log_path)
        if p.exists():
            stdout = p.read_text(encoding="utf-8", errors="replace")
        found, missing = _collect_local_artifacts(
            self._artifact_base(), self._expected_artifacts
        )
        return JobResult(
            stdout=stdout,
            artifacts=[self._log_path, *found],
            returncode=0,
            missing_artifacts=missing,
        )

    def failure_detail(self) -> dict | None:
        return _read_error_json(self._artifact_base())

    def cancel(self) -> None:
        # scancel already terminates the whole job step (Slurm cgroup / task
        # group), so process-group isolation (G-3) is native here — no killpg
        # dance needed as on the subprocess/ssh backends.
        subprocess.run(scancel_argv(self._job_id), capture_output=True,
                       text=True, check=False)

    def describe(self) -> dict:
        d = {
            "backend": "sbatch",
            "job_id": self._job_id,
            "cmd": list(self._cmd),
            "log": self._log_path,
            "script": self._script_path,
        }
        _attach_failure_detail(self, d)
        return d


class SbatchBackend(JobBackend):
    """Submits jobs to Slurm via ``sbatch --parsable`` and tracks them with
    squeue/sacct; cancellation via scancel."""

    def __init__(self, log_dir: str | os.PathLike[str] | None = None) -> None:
        self.log_dir = Path(log_dir) if log_dir is not None else Path(tempfile.gettempdir())

    def submit(
        self,
        cmd: list[str],
        cwd: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float | None = None,
        expected_artifacts: list[str] | None = None,
    ) -> JobHandle:
        job_name = f"expos_{uuid.uuid4().hex[:8]}"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = str(self.log_dir / f"{job_name}.out")
        cwd_str = str(cwd) if cwd is not None else None
        effective_cmd = cmd
        if env:
            effective_cmd = ["env", *[f"{k}={v}" for k, v in env.items()], *cmd]
        script = build_sbatch_script(
            effective_cmd, cwd_str, job_name, log_path, timeout_s,
        )
        script_path = str(self.log_dir / f"{job_name}.sh")
        Path(script_path).write_text(script, encoding="utf-8")
        res = subprocess.run(
            sbatch_submit_argv(script_path),
            capture_output=True, text=True, check=False,
        )
        if res.returncode != 0:
            raise JobError(
                f"sbatch submit failed (rc={res.returncode}): {res.stderr.strip()}"
            )
        # --parsable prints "jobid" or "jobid;cluster".
        job_id = res.stdout.strip().split(";")[0]
        return _SbatchJobHandle(
            job_id, cmd, log_path, script_path,
            cwd=cwd_str, expected_artifacts=expected_artifacts,
        )
