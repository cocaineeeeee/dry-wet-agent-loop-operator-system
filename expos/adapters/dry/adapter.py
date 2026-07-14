"""PySCFDryAdapter — ExperimentObject in, dry RawResults + lifecycle out.

Built ON TOP of expos.scheduler (the authoritative W1 job-handle kernel): the
adapter owns the PySCF-specific layer only — input-card generation, workdir
isolation, result parsing, failure classification, pre-submit validation, and
RawResult shaping — and delegates process lifecycle to a scheduler backend
(Subprocess today; Ssh/Sbatch are the same JobBackend interface).

Flow:
  build_specs(exp)   -> one JobSpec per layout well (candidate/control)
  validate_spec(spec)-> pre-submit validation; loud-fails BEFORE any job spawns
  run(exp, backend)  -> submit all, poll to terminal, collect, classify

Truth-semantics note: the adapter emits only RawResult material. The dry compute
is an observation (it carries method error); the domain simulation face owns the
truth sidecar. This adapter never produces truth_records, and does NOT mutate
the ExperimentObject (read-only, like the sim adapters).
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from expos.scheduler import (
    JobBackend,
    JobHandle,
    JobState,
    SubprocessBackend,
)
from expos.scheduler.jobs import JobResult

from expos.adapters.domain_provider import INPUT_KIND_MOLECULAR_GEOMETRY
from expos.adapters.dry.compute import ComputeError, build_mol
from expos.adapters.dry.ingest import DryRawResult
from expos.adapters.dry.outcomes import (
    RETRYABLE_BY_REASON,
    JobFailure,
    LifecycleEvent,
    classify_failure,
)
from expos.adapters.dry.solvents import SOLVENTS
from expos.adapters.dry.spec import ALLOWED_BASIS, ALLOWED_METHOD, ComputeResult, JobSpec

_TERMINAL = {JobState.SUCCEEDED, JobState.FAILED, JobState.TIMEOUT}

#: File products the worker declares on success. Passed to the scheduler as
#: ``expected_artifacts`` so ``collect()`` reports a missing product LOUDLY
#: (JobResult.missing_artifacts) instead of the adapter silently snapshot-diffing
#: the cwd or scraping a stdout marker line (letter 060, G-1).
_EXPECTED_ARTIFACTS = ["result.json"]

#: Worker module, derived from this package so it stays correct whether the
#: package is imported as `dry` (standalone) or `expos.adapters.dry` (in-tree).
_WORKER_MODULE = f"{__package__}.worker"


def _package_root() -> str:
    """Directory that makes the `dry` package importable (child PYTHONPATH)."""
    # parents[1] of dry/adapter.py == the dir containing `dry/` (standalone) or
    # the expos repo root's ancestor chain (in-tree, harmless duplicate).
    return str(Path(__file__).resolve().parents[1])


def _expos_root() -> str | None:
    """Directory that makes `expos` importable, propagated to the child so the
    worker can import the package (dry/__init__ pulls in expos). Deployment-
    agnostic: derived from the already-imported expos module, not hardcoded."""
    try:
        import expos

        return str(Path(expos.__file__).resolve().parents[1])
    except Exception:  # noqa: BLE001
        return None


@dataclass
class JobRun:
    """One in-flight/terminal job: the scheduler handle + PySCF-side context."""

    spec: JobSpec
    handle: JobHandle
    workdir: Path
    backend_name: str
    events: list[LifecycleEvent] = field(default_factory=list)
    _terminal_recorded: bool = False

    def _record(self, from_state: str | None, to_state: str,
                reason: str | None = None, **meta) -> None:
        self.events.append(
            LifecycleEvent(
                job_id=self.spec.job_id,
                backend=self.backend_name,
                from_state=from_state,
                to_state=to_state,
                reason=reason,
                meta=meta,
            )
        )


class AdapterRunResult(BaseModel):
    """Outcome of running one ExperimentObject through the dry adapter."""

    model_config = ConfigDict(extra="forbid")

    dry_raws: list[DryRawResult] = Field(default_factory=list)      # successes
    failures: dict[str, JobFailure] = Field(default_factory=dict)   # job_id -> failure
    events: list[LifecycleEvent] = Field(default_factory=list)      # lifecycle log
    n_jobs: int = 0


class PySCFDryAdapter:
    name = "pyscf_dry"

    #: Contract v3 capability declaration (adapters/domain_provider.py convention):
    #: this adapter executes ``molecular_geometry`` ComputeTargets only (it builds a
    #: PySCF ``Mole`` from a Z-matrix). B's mcl dry-leg dispatch reads this (via
    #: ``domain_provider.adapter_accepts_capability``) to pick an adapter per target;
    #: a geometry-free target (``sequence_*``) routes to a different adapter.
    ACCEPTS_INPUT_KINDS: tuple[str, ...] = (INPUT_KIND_MOLECULAR_GEOMETRY,)

    @classmethod
    def accepts_capability(cls, capability: str) -> bool:
        """True iff this adapter can execute a ComputeTarget requiring ``capability``
        (i.e. ``capability in ACCEPTS_INPUT_KINDS``)."""
        return capability in cls.ACCEPTS_INPUT_KINDS

    def __init__(
        self,
        *,
        jobs_root: str | Path = "./_dry_jobs",
        basis: str = "sto-3g",
        method: str = "HF",
        timeout_s: float = 110.0,
        poll_interval_s: float = 0.25,
        python: str | None = None,
    ):
        # Resolve to an absolute path: the worker subprocess runs with cwd=workdir,
        # so a relative jobs_root makes the argv path unresolvable from inside the
        # child (silent instant dry_failed — bitten by the sbatch co-run passing
        # OUT=runs/... relative; local tests always used absolute tmp paths).
        self.jobs_root = Path(jobs_root).resolve()
        self.basis = basis.strip().lower()
        self.method = method.strip().upper()
        self.timeout_s = float(timeout_s)
        self.poll_interval_s = float(poll_interval_s)
        self.python = python or sys.executable

    # ---- spec construction --------------------------------------------------

    def build_specs(self, exp) -> list[JobSpec]:
        """One JobSpec per layout well. Reads (never mutates) the exp."""
        from expos.adapters.base import AdapterError

        if exp.layout is None:
            raise AdapterError(f"{self.name}: exp {exp.exp_id} has no layout")

        params_by_id: dict[str, dict] = {}
        for c in exp.candidates:
            params_by_id[c.cand_id] = c.params
        for c in exp.controls:
            params_by_id[c.control_id] = c.params

        metric = exp.objective.metric
        specs: list[JobSpec] = []
        for w in exp.layout.wells:
            entry_id = w.cand_id if w.cand_id is not None else w.control_id
            if entry_id not in params_by_id:
                raise AdapterError(
                    f"{self.name}: layout references unknown entry {entry_id!r}"
                )
            p = params_by_id[entry_id]
            specs.append(
                JobSpec(
                    job_id=f"{exp.exp_id}:{exp.round_id}:{w.well_id}",
                    well_id=w.well_id,
                    cand_id=w.cand_id,
                    control_id=w.control_id,
                    solvent=p.get("solvent"),
                    geometry=p.get("geometry"),
                    charge=int(p.get("charge", 0)),
                    spin=int(p.get("spin", 0)),
                    basis=str(p.get("basis", self.basis)),
                    method=str(p.get("method", self.method)),
                    max_cycle=int(p.get("max_cycle", 50)),
                    conv_tol=float(p.get("conv_tol", 1e-9)),
                    metric=metric,
                    timeout_s=float(p.get("timeout_s", self.timeout_s)),
                    sleep_s=float(p.get("sleep_s", 0.0)),
                    disable_diis=bool(p.get("disable_diis", False)),
                )
            )
        return specs

    # ---- pre-submit validation ---------------------------------------------

    def validate_spec(self, spec: JobSpec) -> None:
        """Reject invalid specs BEFORE submitting (no job wasted). Raises
        AdapterError(invalid_input). Builds the Mole (cheap, no SCF) to catch
        bad geometry / charge / spin parity early."""
        from expos.adapters.base import AdapterError

        if not spec.solvent and not (spec.geometry and spec.geometry.strip()):
            raise AdapterError(
                f"invalid_input: job {spec.job_id} has neither a `solvent` preset "
                f"nor an explicit `geometry`"
            )
        if spec.solvent and spec.solvent not in SOLVENTS and not spec.geometry:
            raise AdapterError(
                f"invalid_input: job {spec.job_id} unknown solvent {spec.solvent!r}; "
                f"presets: {sorted(SOLVENTS)}"
            )
        if spec.basis not in ALLOWED_BASIS:
            raise AdapterError(
                f"invalid_input: job {spec.job_id} basis {spec.basis!r} not allowed "
                f"(budget guard); allowed: {sorted(ALLOWED_BASIS)}"
            )
        if spec.method not in ALLOWED_METHOD:
            raise AdapterError(
                f"invalid_input: job {spec.job_id} method {spec.method!r} not "
                f"supported; allowed: {sorted(ALLOWED_METHOD)}"
            )
        if spec.max_cycle < 1:
            raise AdapterError(
                f"invalid_input: job {spec.job_id} max_cycle must be >= 1"
            )
        # Build the molecule to validate geometry + charge/spin parity. PySCF
        # rejects an impossible (charge, spin) parity at build time.
        try:
            build_mol(spec)
        except (ComputeError, Exception) as exc:  # noqa: BLE001
            raise AdapterError(
                f"invalid_input: job {spec.job_id} molecule build failed: {exc}"
            ) from exc

    # ---- single-job driving (reusable by loop/tests) ------------------------

    def _child_env(self) -> dict[str, str]:
        # SubprocessBackend passes env straight to Popen (which REPLACES the
        # environment), so we must ship a full copy plus our additions.
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        roots = [_package_root()]
        expos_root = _expos_root()
        if expos_root:
            roots.append(expos_root)
        existing = env.get("PYTHONPATH", "")
        if existing:
            roots.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(roots)
        return env

    def _prepare_workdir(self, spec: JobSpec) -> Path:
        safe_id = spec.job_id.replace(":", "-").replace("/", "-")
        workdir = self.jobs_root / f"{safe_id}__{spec.spec_sha()[:12]}"
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "spec.json").write_text(spec.model_dump_json(), encoding="utf-8")
        return workdir

    def submit(self, spec: JobSpec, backend: JobBackend) -> JobRun:
        """Validate + submit one job. Records accepted + spawned lifecycle
        events. The workdir holds spec.json and (later) result.json/error.json."""
        self.validate_spec(spec)
        workdir = self._prepare_workdir(spec)
        cmd = [self.python, "-m", _WORKER_MODULE, str(workdir)]
        handle = backend.submit(
            cmd,
            cwd=str(workdir),
            env=self._child_env(),
            timeout_s=spec.timeout_s,
            expected_artifacts=list(_EXPECTED_ARTIFACTS),
        )
        run = JobRun(
            spec=spec,
            handle=handle,
            workdir=workdir,
            backend_name=type(backend).__name__,
        )
        run._record(None, JobState.PENDING.value, reason="accepted",
                    spec_sha=spec.spec_sha())
        run._record(JobState.PENDING.value, JobState.RUNNING.value, reason="spawned")
        return run

    def poll(self, run: JobRun) -> JobState:
        """Poll one job; record the terminal transition exactly once."""
        status = run.handle.poll()
        if status.state in _TERMINAL and not run._terminal_recorded:
            run._terminal_recorded = True
            run._record(JobState.RUNNING.value, status.state.value,
                        reason=status.state.value.lower(), returncode=status.returncode)
        return status.state

    def collect(self, run: JobRun) -> tuple[DryRawResult | None, JobFailure | None]:
        """Return (dry_raw, None) on success, (None, failure) otherwise.

        On SUCCEEDED, the declared ``result.json`` product is recovered through
        the scheduler's artifact channel; a job that reports success but did NOT
        write its declared product is a loud ``missing_artifact`` failure, never
        a silent fallback (letter 060, G-1). On any non-SUCCEEDED terminal state
        the failure taxonomy consumes the backend-agnostic failure_detail record."""
        status = run.handle.poll()
        if status.state is JobState.SUCCEEDED:
            job_result = run.handle.collect()
            if "result.json" in job_result.missing_artifacts:
                return None, JobFailure(
                    reason="missing_artifact",
                    detail=(
                        f"job {run.spec.job_id} reported SUCCEEDED but its declared "
                        f"artifact 'result.json' is absent "
                        f"(missing={job_result.missing_artifacts}); a missing product "
                        f"is a loud failure, never a silent gap"
                    ),
                    retryable=RETRYABLE_BY_REASON["missing_artifact"],
                )
            compute = self._load_result(run, job_result)
            dry_raw = DryRawResult.from_compute(
                well_id=run.spec.well_id,
                cand_id=run.spec.cand_id,
                control_id=run.spec.control_id,
                raw_uri=str(run.workdir / "result.json"),
                compute=compute,
            )
            return dry_raw, None
        return None, classify_failure(status, run.handle.failure_detail())

    def _load_result(self, run: JobRun, job_result: JobResult) -> ComputeResult:
        """Deserialize the collected ``result.json`` product. ``collect()`` has
        already verified it is not in ``missing_artifacts``, so it is present;
        read it from the resolved artifact path (shared filesystem) with the
        job workdir as the belt-and-suspenders fallback."""
        for path_str in job_result.artifacts:
            if path_str.endswith("result.json") and Path(path_str).exists():
                return ComputeResult.model_validate_json(
                    Path(path_str).read_text(encoding="utf-8")
                )
        local = run.workdir / "result.json"
        if local.exists():
            return ComputeResult.model_validate_json(
                local.read_text(encoding="utf-8")
            )
        from expos.adapters.base import AdapterError

        raise AdapterError(
            f"job {run.spec.job_id}: result.json was collected (not missing) but is "
            f"unreadable from artifacts {job_result.artifacts} or {run.workdir}"
        )

    # ---- orchestration ------------------------------------------------------

    def run(self, exp, backend: JobBackend | None = None) -> AdapterRunResult:
        """End-to-end: validate all, submit all, poll to terminal, collect."""
        if backend is None:
            backend = SubprocessBackend()

        specs = self.build_specs(exp)
        # Validate ALL specs before spawning ANY job (one bad spec fails the
        # batch pre-submit; invalid input never wastes a job).
        for spec in specs:
            self.validate_spec(spec)

        runs = [self.submit(spec, backend) for spec in specs]

        # Safety wall: no job may keep the loop alive past its own timeout.
        overall_deadline = time.time() + max(s.timeout_s for s in specs) + 30.0

        pending = list(runs)
        while pending:
            pending = [r for r in pending if self.poll(r) not in _TERMINAL]
            if not pending:
                break
            if time.time() > overall_deadline:
                for r in pending:
                    r.handle.cancel()
                    self.poll(r)
                break
            time.sleep(self.poll_interval_s)

        result = AdapterRunResult(n_jobs=len(runs))
        for r in runs:
            result.events.extend(r.events)
            dry_raw, failure = self.collect(r)
            if dry_raw is not None:
                result.dry_raws.append(dry_raw)
            elif failure is not None:
                result.failures[r.spec.job_id] = failure
        return result
