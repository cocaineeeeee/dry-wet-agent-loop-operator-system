"""PySCF dry adapter prototype (M16 W3).

A real-job dry compute adapter: consumes an ExperimentObject, dispatches one
PySCF single-point job per candidate/control as an *out-of-process* job, polls
the job lifecycle, collects a RawResult, and classifies failures.

Job lifecycle is delegated to the authoritative W1 kernel (``expos.scheduler``):
its ``JobBackend`` / ``JobHandle`` (Subprocess / Ssh / Sbatch) provide
submit/poll/collect/cancel and the 5-state ``JobState`` machine. This package
adds only the PySCF-specific layer on top:

- ``spec``      -- JobSpec (input card) + ComputeResult schema
- ``solvents``  -- preset solvent geometries (Z-matrices)
- ``compute``   -- pure PySCF single point (dipole + HF/DFT energy)
- ``worker``    -- ``python -m expos.adapters.dry.worker`` out-of-process entry point
- ``outcomes``  -- failure taxonomy (JobFailure) + LifecycleEvent records
- ``adapter``   -- PySCFDryAdapter: exp -> specs -> jobs -> DryRawResult
- ``ingest``    -- DryRawResult -> ObservationObject (trust=PENDING) shaping

Truth-semantics note: the dry compute result is an OBSERVATION, not truth (PySCF
carries method error). This adapter only produces RawResult material; the domain
simulation face owns the truth sidecar. This module never writes events itself —
it returns LifecycleEvent records for the loop to persist.
"""

from __future__ import annotations

from expos.adapters.dry.adapter import AdapterRunResult, JobRun, PySCFDryAdapter
from expos.adapters.dry.ingest import DryRawResult, InstrumentProvenance, dry_raw_to_observations
from expos.adapters.dry.outcomes import JobFailure, LifecycleEvent, classify_failure
from expos.adapters.dry.reuse import (
    ENGINE_ID,
    ReuseDecision,
    current_engine_version,
    evaluate_reuse,
    reuse_key,
)
from expos.adapters.dry.solvents import SOLVENTS, solvent_names
from expos.adapters.dry.spec import ComputeResult, JobSpec

__all__ = [
    "PySCFDryAdapter",
    "JobRun",
    "AdapterRunResult",
    "JobSpec",
    "ComputeResult",
    "JobFailure",
    "LifecycleEvent",
    "classify_failure",
    "DryRawResult",
    "InstrumentProvenance",
    "dry_raw_to_observations",
    "SOLVENTS",
    "solvent_names",
    "ENGINE_ID",
    "ReuseDecision",
    "current_engine_version",
    "evaluate_reuse",
    "reuse_key",
]
