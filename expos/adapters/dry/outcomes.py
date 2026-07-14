"""Failure taxonomy + lifecycle records layered on top of expos.scheduler.

expos.scheduler.JobHandle gives us a uniform poll()/collect()/cancel() over
three backends and a 5-state JobState machine, but it does not (a) classify
*why* a job failed beyond the raw returncode, nor (b) emit a lifecycle event
stream. Both are this adapter's job:

- ``classify_failure`` maps a terminal (non-SUCCEEDED) JobStatus + the job's
  self-reported failure record onto a structured JobFailure with a
  retryable/fatal discriminator. The failure record arrives via the
  backend-agnostic ``JobHandle.failure_detail()`` channel (letter 060), so the
  taxonomy no longer reaches into the local workdir and works identically over
  subprocess / ssh / sbatch.
- ``LifecycleEvent`` records one observed state transition; the adapter returns
  the list so the loop can persist them as events (this module writes nothing).

Failure taxonomy (G2):
  timeout         -> JobState.TIMEOUT (scheduler killed the process)   retryable
  signal          -> FAILED, returncode < 0 (killed / cancelled)       retryable
  convergence     -> FAILED, worker exit 10 (SCF did not converge)     retryable
  worker_error    -> FAILED, worker exit 20 / other nonzero            FATAL
  missing_artifact-> SUCCEEDED but a declared product is absent         FATAL
  invalid_input   -> raised pre-submit by the adapter (no job spawned)  FATAL
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from expos.scheduler import JobState, JobStatus

# Worker exit-code protocol (see dry.worker).
EXIT_CONVERGENCE = 10
EXIT_WORKER_ERROR = 20

#: reason -> retryable? (True = transient/retry, False = fatal/do-not-retry)
RETRYABLE_BY_REASON = {
    "timeout": True,
    "signal": True,
    "convergence": True,
    "worker_error": False,
    "missing_artifact": False,
    "invalid_input": False,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobFailure(BaseModel):
    """Structured failure classification with a retryable/fatal discriminator."""

    model_config = ConfigDict(extra="forbid")

    reason: str  # timeout | signal | convergence | worker_error | invalid_input
    detail: str = ""
    retryable: bool
    signal: int | None = None
    exit_code: int | None = None


class LifecycleEvent(BaseModel):
    """One observed job state transition. The integration layer maps these onto
    the expos event vocabulary (action_goal / action_result); see INTEGRATION.md."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    ts: str = Field(default_factory=_utc_now)
    backend: str
    from_state: str | None
    to_state: str
    reason: str | None = None
    meta: dict = Field(default_factory=dict)


def _detail_str(failure_detail: dict | None) -> str:
    """Extract the worker's ``detail`` string from a backend-agnostic failure
    record (``JobHandle.failure_detail()`` output), or "" if none/malformed."""
    if not isinstance(failure_detail, dict):
        return ""
    detail = failure_detail.get("detail", "")
    return detail if isinstance(detail, str) else str(detail)


def classify_failure(
    status: JobStatus, failure_detail: dict | None = None
) -> JobFailure:
    """Classify a terminal, non-SUCCEEDED JobStatus into a JobFailure.

    ``failure_detail`` is the job's self-reported record, obtained from the
    backend-agnostic ``JobHandle.failure_detail()`` channel (never a direct
    workdir read), so classification is identical across execution backends."""
    rc = status.returncode

    if status.state is JobState.TIMEOUT:
        return JobFailure(
            reason="timeout",
            detail=f"wall-clock deadline exceeded; process killed (rc={rc})",
            retryable=RETRYABLE_BY_REASON["timeout"],
            signal=(-rc if (rc is not None and rc < 0) else None),
            exit_code=rc,
        )

    if status.state is not JobState.FAILED:
        # Defensive: SUCCEEDED/PENDING/RUNNING are not failures.
        raise ValueError(f"classify_failure called on non-failure state {status.state}")

    if rc is not None and rc < 0:
        return JobFailure(
            reason="signal",
            detail=f"process killed by signal {-rc}",
            retryable=RETRYABLE_BY_REASON["signal"],
            signal=-rc,
            exit_code=rc,
        )

    detail = _detail_str(failure_detail)
    if rc == EXIT_CONVERGENCE:
        return JobFailure(
            reason="convergence",
            detail=detail or "SCF did not converge",
            retryable=RETRYABLE_BY_REASON["convergence"],
            exit_code=rc,
        )

    return JobFailure(
        reason="worker_error",
        detail=detail or f"worker exited with code {rc}",
        retryable=RETRYABLE_BY_REASON["worker_error"],
        exit_code=rc,
    )
