"""Serializable job spec + compute result schemas.

JobSpec is the unit of work handed to a backend. It is fully JSON-serializable
so the same spec can be dispatched by the subprocess backend today and by the
ssh/sbatch backends later without shape changes (the spec crosses the process
boundary as ``spec.json`` in the job workdir).

ComputeResult is what the worker writes to ``result.json`` on success and what
``collect()`` deserializes. It carries the OS-visible measurement plus engine
provenance (engine version / basis / method / convergence metadata).
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Basis sets allowed for the "minutes-per-candidate" budget. Guards against a
#: caller requesting a huge basis that would blow the <2 min/job budget.
ALLOWED_BASIS = frozenset({"sto-3g", "3-21g", "6-31g", "6-31g*", "6-31g(d)"})
#: Supported single-point methods.
ALLOWED_METHOD = frozenset({"HF", "B3LYP"})
#: Default metric name for the solvent_screen dry face.
DEFAULT_METRIC = "polarity_proxy"


class JobSpec(BaseModel):
    """A single PySCF single-point job (one candidate/control)."""

    model_config = ConfigDict(extra="forbid")

    # ---- identity (maps the job back to the ExperimentObject layout) ----
    job_id: str
    well_id: str
    cand_id: str | None = None
    control_id: str | None = None

    # ---- what to compute ----
    #: Preset solvent name (see dry.solvents.SOLVENTS). Ignored if `geometry`
    #: is given.
    solvent: str | None = None
    #: Explicit PySCF geometry (Z-matrix or Cartesian) overriding `solvent`.
    geometry: str | None = None
    charge: int = 0
    spin: int = 0  # 2S; number of unpaired electrons
    basis: str = "sto-3g"
    method: str = "HF"
    max_cycle: int = 50
    conv_tol: float = 1e-9
    metric: str = DEFAULT_METRIC

    # ---- execution knobs ----
    #: Wall-clock timeout hint (seconds); the backend enforces the real kill.
    timeout_s: float = 110.0
    #: Test/debug hook: worker sleeps this long before computing, to open a
    #: killable/timeoutable window for lifecycle tests (simulates a long job).
    #: Not used by real screening runs.
    sleep_s: float = 0.0
    #: Test/debug hook: disable DIIS so a low `max_cycle` reliably fails to
    #: converge (used to construct the SCF-nonconvergence case).
    disable_diis: bool = False

    @field_validator("basis")
    @classmethod
    def _norm_basis(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("method")
    @classmethod
    def _norm_method(cls, v: str) -> str:
        return v.strip().upper()

    def compute_fingerprint(self) -> dict:
        """Canonical dict of *compute-relevant* fields (identity/exec knobs
        excluded) — the basis for spec_sha and deterministic workdir naming."""
        return {
            "solvent": self.solvent,
            "geometry": (self.geometry or "").strip() or None,
            "charge": self.charge,
            "spin": self.spin,
            "basis": self.basis,
            "method": self.method,
            "max_cycle": self.max_cycle,
            "conv_tol": self.conv_tol,
        }

    def spec_sha(self) -> str:
        blob = json.dumps(self.compute_fingerprint(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()


class ComputeResult(BaseModel):
    """Worker output (``result.json``) — the OS-visible measurement + engine
    provenance. Contains NO truth field (method error is not disclosed here)."""

    model_config = ConfigDict(extra="forbid")

    metric: str
    value: float  # polarity proxy score
    unit: str = "Debye"
    secondary: dict[str, float] = Field(default_factory=dict)

    # ---- engine provenance (instrument_meta material) ----
    engine: str = "pyscf"
    engine_version: str
    basis: str
    method: str
    charge: int
    spin: int
    converged: bool
    scf_cycles: int
    n_electrons: int
    total_energy_hartree: float
    dipole_debye: float

    spec_sha: str
    result_sha: str  # sha over rounded key output fields (determinism anchor)

    @staticmethod
    def result_fingerprint(
        total_energy_hartree: float, dipole_debye: float, homo_lumo_gap_ev: float
    ) -> str:
        """Deterministic sha over rounded key output fields. Rounding absorbs
        sub-ULP float jitter so the same input yields the same sha."""
        payload = {
            "E": round(float(total_energy_hartree), 8),
            "dipole": round(float(dipole_debye), 6),
            "gap": round(float(homo_lumo_gap_ev), 6),
        }
        blob = json.dumps(payload, sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()
