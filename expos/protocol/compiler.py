"""compile(spec, domain_cfg) -> CompiledProtocol: two deterministic build targets.

``compile`` is a pure, deterministic function: the same ``(spec, domain_cfg)``
yields byte-identical plans and a byte-identical ``protocol_fingerprint``. It
produces two build targets from one declarative :class:`~expos.protocol.spec.
ProtocolSpec`:

    (a) DryJobPlan      -> W3 PySCF adapter. A cmd template + a per-candidate
        input-file manifest (each a real ``expos.adapters.dry.spec.JobSpec``
        card written as ``spec.json``) + the expected product filenames. The
        cards validate against the actual W3 JobSpec schema, so the plan is
        genuinely consumable by ``python -m expos.adapters.dry.worker``.

    (b) WetProtocolPlan -> W4 ot_protocol. Labware + per-sample bindings.
        ``to_wet_protocol_spec()`` materialises a real
        ``expos.adapters.wet.protocol_spec.ProtocolSpec`` that the opentrons
        stack (``compile_and_validate``) gate-keeps -- i.e. the wet plan
        round-trips through A-side's authoritative schema.

Fingerprint anchor (VNext (2)):

    protocol_fingerprint = sha256( canonical_json(spec) || compiler-source-sha )

The compiler source sha makes the anchor sensitive to a change in the compiler
itself (a different compilation is a different protocol identity), and
``canonical_json(spec)`` makes it sensitive to any spec/param change. Note the
anchor deliberately does NOT fold in ``domain_cfg``: the spec + compiler pin the
protocol IDENTITY (what to do); domain_cfg is the environment binding (where/how
to run) and does not change what protocol this is.

Wiring note: this module only PRODUCES the fingerprint on the compile exit. The
loop/experiment build side is not wired here -- W3 (dry) and W4 (wet) stamp
``DesignProvenance.protocol_fingerprint`` when they consume these plans. Wired by
W3/W4.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from expos.protocol.spec import (
    ALLOWED_OPS,
    CandidateBinding,
    ProtocolModel,
    ProtocolSpec,
    canonical_json,
)

if TYPE_CHECKING:  # avoid importing the wet package at module import time
    from expos.adapters.wet.protocol_spec import ProtocolSpec as WetSpec


class CompileError(Exception):
    """Raised when a spec cannot be compiled for the given domain config."""


# ---------------------------------------------------------------- fingerprint

def compiler_source_sha() -> str:
    """sha256 of this compiler's own source -- the compiler-version component of
    the fingerprint. Changing the compiler changes the protocol identity it
    emits."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def protocol_fingerprint(spec: ProtocolSpec, *, source_sha: str | None = None) -> str:
    """sha256( canonical_json(spec) || compiler-source-sha ).

    ``source_sha`` is injectable purely so a test can prove the compiler-source
    term is really part of the hash (a fake sha must yield a different digest);
    production callers leave it None to use the live source.
    """
    sha = source_sha if source_sha is not None else compiler_source_sha()
    payload = canonical_json(spec) + sha
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- domain config

class StockCfg(ProtocolModel):
    name: str
    reservoir_well: str
    polarity: float


class DomainCompileConfig(ProtocolModel):
    """Environment binding for compilation (NOT part of the fingerprint).

    Defaults target the ``solvent_screen`` domain and are aligned with A-side
    ``expos.adapters.wet.protocol_spec`` defaults so a compiled wet plan
    round-trips through the real opentrons validator.
    """

    domain: str = "solvent_screen"
    # -- wet leg --
    total_volume_ul: float = 200.0
    stock_low: StockCfg = Field(
        default_factory=lambda: StockCfg(
            name="stockA_lowpol", reservoir_well="A1", polarity=0.10
        )
    )
    stock_high: StockCfg = Field(
        default_factory=lambda: StockCfg(
            name="stockB_highpol", reservoir_well="A2", polarity=0.90
        )
    )
    plate_labware: str = "corning_96_wellplate_360ul_flat"
    reservoir_labware: str = "nest_12_reservoir_15ml"
    tiprack_labware: str = "opentrons_96_tiprack_300ul"
    pipette: str = "p300_single_gen2"
    mount: str = "right"
    plate_slot: int = 1
    reservoir_slot: int = 2
    tiprack_slot: int = 3
    # -- dry leg --
    dry_worker_module: str = "expos.adapters.dry.worker"
    dry_basis: str = "sto-3g"
    dry_method: str = "HF"
    dry_metric: str = "polarity_proxy"


def default_solvent_screen_config() -> DomainCompileConfig:
    return DomainCompileConfig()


# ---------------------------------------------------------------- dry target

class DryJobCard(ProtocolModel):
    """One dry job: its input-card (a JobSpec-shaped ``spec.json`` payload)."""

    job_id: str
    well_id: str
    cand_id: str | None
    control_id: str | None
    #: JobSpec-shaped card. Written verbatim as ``spec.json`` in the job workdir
    #: and consumed by ``python -m expos.adapters.dry.worker``. Validates against
    #: ``expos.adapters.dry.spec.JobSpec`` (extra=forbid) -- keys are a subset.
    input_card: dict[str, Any]


class DryJobPlan(ProtocolModel):
    """Build target (a): everything W3 needs to dispatch the dry leg."""

    domain: str
    #: argv template; ``{workdir}`` is substituted per job by the runtime (which
    #: materialises ``input_filename`` into that workdir). Wired by W3.
    cmd_template: list[str]
    input_filename: str = "spec.json"
    jobs: list[DryJobCard]
    #: Product filenames the worker emits (success / failure). Wired by W3.
    expected_products: list[str] = Field(
        default_factory=lambda: ["result.json", "error.json"]
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- wet target

class WetSampleBinding(ProtocolModel):
    cand_id: str
    target_polarity: float
    is_control: bool = False
    control_id: str | None = None


class WetProtocolPlan(ProtocolModel):
    """Build target (b): labware + per-sample bindings for W4 ot_protocol.

    ``to_wet_protocol_spec`` materialises the A-side authoritative schema so the
    real opentrons stack can gate-keep the compiled protocol.
    """

    domain: str
    total_volume_ul: float
    stock_low: StockCfg
    stock_high: StockCfg
    plate_labware: str
    reservoir_labware: str
    tiprack_labware: str
    pipette: str
    mount: str
    plate_slot: int
    reservoir_slot: int
    tiprack_slot: int
    samples: list[WetSampleBinding]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_wet_protocol_spec(self) -> "WetSpec":
        """Build the real A-side ``ProtocolSpec`` (custody + opentrons schema).

        Imported lazily so the protocol package stays importable even where the
        wet adapter is absent, and so the fingerprint stays a pure function of
        the plan data (never of the wet import).
        """
        from expos.adapters.wet.protocol_spec import (
            ProtocolSpec as WetProtocolSpec,
            SolventSample,
            Stock,
        )

        samples = [
            SolventSample(
                cand_id=s.cand_id,
                target_polarity=s.target_polarity,
                is_control=s.is_control,
                control_id=s.control_id,
            )
            for s in self.samples
        ]
        return WetProtocolSpec(
            samples=samples,
            stock_low=Stock(
                self.stock_low.name,
                self.stock_low.reservoir_well,
                self.stock_low.polarity,
            ),
            stock_high=Stock(
                self.stock_high.name,
                self.stock_high.reservoir_well,
                self.stock_high.polarity,
            ),
            total_volume_ul=self.total_volume_ul,
            plate_labware=self.plate_labware,
            reservoir_labware=self.reservoir_labware,
            tiprack_labware=self.tiprack_labware,
            pipette=self.pipette,
            mount=self.mount,
            plate_slot=self.plate_slot,
            reservoir_slot=self.reservoir_slot,
            tiprack_slot=self.tiprack_slot,
        )


# ---------------------------------------------------------------- compiled

class CompiledProtocol(ProtocolModel):
    """The compile output: the fingerprint anchor + whichever targets apply."""

    spec_name: str
    version: str
    protocol_fingerprint: str
    dry_plan: DryJobPlan | None = None
    wet_plan: WetProtocolPlan | None = None


# ---------------------------------------------------------------- compile

def _well_ids(n: int) -> list[str]:
    """A1..H12 column-major order, shared by both legs so a candidate keeps one
    well across dry bookkeeping and the physical plate. Reuses the A-side helper
    to stay in lock-step with the real plate ordering."""
    from expos.adapters.wet.protocol_spec import all_wells

    wells = all_wells()
    if n > len(wells):
        raise CompileError(
            f"{n} candidates exceed 96-well plate capacity ({len(wells)})"
        )
    return wells[:n]


def _require_polarity(cb: CandidateBinding) -> float:
    p = cb.params.get("target_polarity")
    if p is None:
        raise CompileError(
            f"candidate {cb.cand_id!r} is missing 'target_polarity' "
            "(required by the wet_assay step)"
        )
    return float(p)


def _compile_dry(
    spec: ProtocolSpec, cfg: DomainCompileConfig, wells: list[str]
) -> DryJobPlan:
    step = spec.step_for("dry_compute")
    if step is None:  # pragma: no cover - guarded by caller
        raise CompileError("internal: _compile_dry called without a dry_compute step")
    basis = step.params.get("basis", cfg.dry_basis)
    method = step.params.get("method", cfg.dry_method)
    metric = step.params.get("metric", cfg.dry_metric)
    jobs: list[DryJobCard] = []
    for cb, well in zip(spec.inputs.candidates, wells):
        card: dict[str, Any] = {
            "job_id": f"{spec.name}-{spec.version}-{cb.cand_id}",
            "well_id": well,
            "cand_id": None if cb.is_control else cb.cand_id,
            "control_id": cb.control_id if cb.is_control else None,
            "solvent": cb.params.get("solvent"),
            "geometry": cb.params.get("geometry"),
            "basis": basis,
            "method": method,
            "metric": metric,
        }
        jobs.append(
            DryJobCard(
                job_id=card["job_id"],
                well_id=well,
                cand_id=card["cand_id"],
                control_id=card["control_id"],
                input_card=card,
            )
        )
    return DryJobPlan(
        domain=cfg.domain,
        cmd_template=["python3", "-m", cfg.dry_worker_module, "{workdir}"],
        jobs=jobs,
        metadata={"step_target": step.target, "basis": basis, "method": method},
    )


def _compile_wet(
    spec: ProtocolSpec, cfg: DomainCompileConfig
) -> WetProtocolPlan:
    step = spec.step_for("wet_assay")
    if step is None:  # pragma: no cover - guarded by caller
        raise CompileError("internal: _compile_wet called without a wet_assay step")
    total_volume = float(step.params.get("total_volume_ul", cfg.total_volume_ul))
    samples = [
        WetSampleBinding(
            cand_id=cb.cand_id,
            target_polarity=_require_polarity(cb),
            is_control=cb.is_control,
            control_id=cb.control_id,
        )
        for cb in spec.inputs.candidates
    ]
    return WetProtocolPlan(
        domain=cfg.domain,
        total_volume_ul=total_volume,
        stock_low=cfg.stock_low,
        stock_high=cfg.stock_high,
        plate_labware=cfg.plate_labware,
        reservoir_labware=cfg.reservoir_labware,
        tiprack_labware=cfg.tiprack_labware,
        pipette=cfg.pipette,
        mount=cfg.mount,
        plate_slot=cfg.plate_slot,
        reservoir_slot=cfg.reservoir_slot,
        tiprack_slot=cfg.tiprack_slot,
        samples=samples,
        metadata={"step_target": step.target},
    )


def compile(  # noqa: A001 - deliberate public verb name (mirrors compile_and_validate)
    spec: ProtocolSpec, domain_cfg: DomainCompileConfig | None = None
) -> CompiledProtocol:
    """Compile ``spec`` into its two build targets + the fingerprint anchor.

    Deterministic: same ``(spec, domain_cfg)`` -> identical output. A dry plan is
    emitted iff the spec has a ``dry_compute`` step; a wet plan iff it has a
    ``wet_assay`` step. The fingerprint is always produced (the compile exit's
    load-bearing contract).
    """
    cfg = domain_cfg or default_solvent_screen_config()

    # Defensive: the ops set is closed at M16. An op outside ALLOWED_OPS should
    # already be rejected by ProtocolStep, but a compiler that grew a new op
    # without a compile branch would silently drop it -- fail loud instead.
    for op in spec.ops():
        if op not in ALLOWED_OPS:
            raise CompileError(f"compiler has no build branch for op {op!r}")

    wells = _well_ids(len(spec.inputs.candidates))
    dry_plan = _compile_dry(spec, cfg, wells) if spec.step_for("dry_compute") else None
    wet_plan = _compile_wet(spec, cfg) if spec.step_for("wet_assay") else None

    return CompiledProtocol(
        spec_name=spec.name,
        version=spec.version,
        protocol_fingerprint=protocol_fingerprint(spec),
        dry_plan=dry_plan,
        wet_plan=wet_plan,
    )
