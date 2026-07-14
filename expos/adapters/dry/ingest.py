"""RawResult -> ObservationObject shaping for the dry adapter.

Aligns to the expos ingestion contract: observations always enter PENDING
(qc=None, routing=None, failure_attr=None, next_action=None). Ingestion only
does shape/layout mapping and never adjudicates.

Provenance now travels on FIRST-CLASS kernel positions (letter 060 — the W1
scheduler/kernel side is in place):
  1. raw_ref.uri + raw_ref.sha256 carry the job workdir product uri + its sha
     (RawResult.uri/sha256 formal fields; raw_to_observations maps them onto
     ObservationObject.raw_ref). No longer a documented "seam".
  2. The producing engine lands on InstrumentMeta.engine (formal field, filled
     with the pyscf engine string). The `InstrumentProvenance` sidecar is kept
     only for the EXTRA engine detail that has no kernel home
     (converged / scf_cycles / n_electrons / version / basis / method); the
     MAIN chain reads provenance off the formal positions, not the sidecar.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from expos.adapters.dry.spec import ComputeResult


class DryRawResult(BaseModel):
    """OS-visible dry measurement + engine provenance (adapter output).

    Superset of the expos RawResult: it additionally carries the raw workdir
    product uri, the result sha, and engine metadata. `to_expos_raw()` projects
    it down onto the current expos RawResult schema (lossy) for the standard
    ingestion path; `dry_raw_to_observations()` keeps the provenance."""

    model_config = ConfigDict(extra="forbid")

    well_id: str
    cand_id: str | None = None
    control_id: str | None = None
    metric: str
    value: float
    unit: str = "Debye"
    secondary: dict[str, float] = Field(default_factory=dict)

    # provenance
    raw_uri: str
    result_sha: str
    engine: str
    engine_version: str
    basis: str
    method: str
    charge: int
    spin: int
    converged: bool
    scf_cycles: int
    n_electrons: int

    @classmethod
    def from_compute(
        cls,
        *,
        well_id: str,
        cand_id: str | None,
        control_id: str | None,
        raw_uri: str,
        compute: ComputeResult,
    ) -> "DryRawResult":
        return cls(
            well_id=well_id,
            cand_id=cand_id,
            control_id=control_id,
            metric=compute.metric,
            value=compute.value,
            unit=compute.unit,
            secondary=dict(compute.secondary),
            raw_uri=raw_uri,
            result_sha=compute.result_sha,
            engine=compute.engine,
            engine_version=compute.engine_version,
            basis=compute.basis,
            method=compute.method,
            charge=compute.charge,
            spin=compute.spin,
            converged=compute.converged,
            scf_cycles=compute.scf_cycles,
            n_electrons=compute.n_electrons,
        )

    def to_expos_raw(self, capture_index: int = 0):
        """Project onto the expos RawResult (extra='forbid') so the stock
        `raw_to_observations` accepts it. Provenance is NO LONGER lossy: the
        workdir product uri, its sha, and the engine ride the formal
        RawResult.uri / sha256 / engine positions (letter 060)."""
        from expos.adapters.base import RawResult

        return RawResult(
            well_id=self.well_id,
            cand_id=self.cand_id,
            control_id=self.control_id,
            metric=self.metric,
            value=self.value,
            unit=self.unit,
            secondary=dict(self.secondary),
            capture_index=capture_index,
            # provenance on the formal three-tuple positions
            uri=self.raw_uri,
            sha256=self.result_sha,
            engine=self.engine,
        )


class InstrumentProvenance(BaseModel):
    """Engine provenance sidecar for the EXTRA detail with no kernel home.

    The engine name lives on InstrumentMeta.engine (formal); the workdir uri +
    sha live on raw_ref (formal). This sidecar carries only what the kernel
    schema does not model: converged / scf_cycles / n_electrons / engine_version
    / basis / method. It stays consistent with the formal positions (engine ==
    InstrumentMeta.engine, raw_uri == raw_ref.uri, result_sha == raw_ref.sha256)."""

    model_config = ConfigDict(extra="forbid")

    well_id: str
    engine: str
    engine_version: str
    basis: str
    method: str
    charge: int
    spin: int
    converged: bool
    scf_cycles: int
    n_electrons: int
    raw_uri: str
    result_sha: str


def dry_raw_to_observations(exp, dry_raws: list[DryRawResult]):
    """Map DryRawResults onto the layout and produce ObservationObjects
    (trust=PENDING) plus a provenance sidecar keyed by well_id.

    Loud-fails (AdapterError) on: no layout, unknown well, metric mismatch,
    cand/control mismatch — same discipline as expos raw_to_observations.
    """
    from expos.adapters.base import AdapterError
    from expos.kernel.objects import (
        InstrumentMeta,
        LayoutMeta,
        MaterialMeta,
        MeasuredResult,
        ObservationObject,
        RawDataRef,
    )

    if exp.layout is None:
        raise AdapterError(f"exp {exp.exp_id} has no layout; cannot ingest dry results")

    by_well = {w.well_id: w for w in exp.layout.wells}
    metric = exp.objective.metric
    observations: list[ObservationObject] = []
    provenance: dict[str, InstrumentProvenance] = {}

    for idx, dr in enumerate(dry_raws):
        wa = by_well.get(dr.well_id)
        if wa is None:
            raise AdapterError(
                f"dry raw well_id {dr.well_id!r} not in exp {exp.exp_id} layout "
                f"(refusing to silently drop unknown well)"
            )
        if dr.metric != metric:
            raise AdapterError(
                f"well {dr.well_id}: raw.metric {dr.metric!r} != objective.metric "
                f"{metric!r} (unknown metric must fail loudly)"
            )
        if dr.cand_id != wa.cand_id or dr.control_id != wa.control_id:
            raise AdapterError(
                f"well {dr.well_id}: raw attribution "
                f"(cand={dr.cand_id}, control={dr.control_id}) != layout "
                f"(cand={wa.cand_id}, control={wa.control_id})"
            )

        obs = ObservationObject(
            exp_id=exp.exp_id,
            round_id=exp.round_id,
            cand_id=wa.cand_id,
            control_id=wa.control_id,
            is_control=wa.control_id is not None,
            result=MeasuredResult(
                metric=metric,
                value=dr.value,
                secondary=dict(dr.secondary),
                unit=dr.unit,
            ),
            # Provenance on the formal kernel positions (letter 060): raw_ref
            # carries the workdir product uri + its content sha256.
            raw_ref=RawDataRef(uri=dr.raw_uri, kind="dry", sha256=dr.result_sha),
            layout_meta=LayoutMeta(
                well_id=wa.well_id,
                row=wa.row,
                col=wa.col,
                is_edge=wa.is_edge,
                block_id=wa.block_id,
            ),
            material_meta=MaterialMeta(),
            # engine on the formal InstrumentMeta.engine position; instrument_id
            # keeps the human-readable engine@version/basis/method descriptor.
            instrument_meta=InstrumentMeta(
                instrument_id=f"{dr.engine}@{dr.engine_version}/{dr.basis}/{dr.method}",
                capture_index=idx,
                engine=dr.engine,
            ),
            qc=None,
            failure_attr=None,
            routing=None,
            next_action=None,
        )
        observations.append(obs)
        provenance[dr.well_id] = InstrumentProvenance(
            well_id=dr.well_id,
            engine=dr.engine,
            engine_version=dr.engine_version,
            basis=dr.basis,
            method=dr.method,
            charge=dr.charge,
            spin=dr.spin,
            converged=dr.converged,
            scf_cycles=dr.scf_cycles,
            n_electrons=dr.n_electrons,
            raw_uri=dr.raw_uri,
            result_sha=dr.result_sha,
        )

    return observations, provenance
