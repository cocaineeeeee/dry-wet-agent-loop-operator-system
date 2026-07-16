"""Protocol constraint checker for M29 (v0.1).

The compile-time gate between a typed :class:`~protocols.objects.Protocol` and the device
IR: it walks the ordered steps and rejects a protocol that a real deck could not run --
LOUDLY (an unrunnable protocol never reaches the lowering / dispatch stage). This mirrors
the "写严读容 + LOUD reject" discipline of the M23 ``ot_protocol`` device-validity gate and
the ProtoPilot "device-level validity gate" abstraction (docs/bio_refs/04 §3; the ADOPT is
*layer-wise verifiability* -- a deterministic checker, NOT an LLM self-assessment).

Three constraint families, each pinned by a discriminative test:

  * **labware / capacity** -- a destination well must exist in the labware, and the
    cumulative volume dispensed into it must not exceed its declared capacity
    (``totalLiquidVolume`` from ``plate96.json``);
  * **volume** -- every transfer volume is positive and within the pipette's usable range;
  * **ordering** -- a well must be *filled* (receive >=1 transfer) before it is mixed or
    read; the plate must hold liquid before it is incubated.

The checker is DETERMINISTIC and side-effect free. Volumes are reconciled by folding the
ordered steps into a running per-well volume map; the final map is returned for provenance
(the compiled protocol's expected deck state).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from expos.adapters.wet.labware import Labware, load_labware
from protocols.objects import (
    Incubate,
    Mix,
    Protocol,
    ReadPlate,
    Transfer,
)

#: Default usable pipette range (µL) -- a dual p20/p300 deck: 1 µL floor, 300 µL ceiling.
#: Cell-free reactions use small volumes, so the floor is 1.0 (not the p300-only 20.0 the
#: M23 wet driver models). Overridable per check.
DEFAULT_PIPETTE_MIN_UL = 1.0
DEFAULT_PIPETTE_MAX_UL = 300.0


class ConstraintError(Exception):
    """A LOUD protocol-constraint rejection. ``code`` is the machine-readable
    discriminator so callers / tests gate on the exact reason."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")


@dataclass(frozen=True)
class Violation:
    code: str
    step_index: int
    detail: str


@dataclass
class ConstraintReport:
    """Result of checking a protocol. ``ok`` iff there are no violations."""

    ok: bool
    violations: list[Violation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: final per-well volume the protocol would leave on the deck (provenance).
    well_volumes: dict[str, float] = field(default_factory=dict)
    filled_wells: frozenset[str] = frozenset()

    def raise_if_bad(self) -> "ConstraintReport":
        """LOUD gate: raise :class:`ConstraintError` on the FIRST violation."""
        if self.violations:
            v = self.violations[0]
            raise ConstraintError(v.code, f"step {v.step_index}: {v.detail}")
        return self


def check_protocol(
    protocol: Protocol,
    *,
    labware: Labware | None = None,
    pipette_min_ul: float = DEFAULT_PIPETTE_MIN_UL,
    pipette_max_ul: float = DEFAULT_PIPETTE_MAX_UL,
    reservoirs: frozenset[str] | None = None,
) -> ConstraintReport:
    """Walk ``protocol`` and collect every constraint violation (does not raise).

    ``reservoirs`` (if given) is the set of legal off-plate source labels; a transfer from
    an unknown source is rejected. When omitted, source labels are accepted as-is (any
    ``reservoir_*`` name), so a caller that has not declared its deck reservoirs is not
    blocked -- capacity/volume/ordering are still fully enforced.
    """
    lw = labware or load_labware()
    known_wells = set(lw.all_wells())
    violations: list[Violation] = []
    warnings: list[str] = []
    volumes: dict[str, float] = {}
    filled: set[str] = set()
    any_fill = False
    incubated = False

    for i, step in enumerate(protocol.steps):
        if isinstance(step, Transfer):
            # -- volume constraints --
            if step.volume_ul <= 0:
                violations.append(Violation(
                    "volume_nonpositive", i,
                    f"transfer volume {step.volume_ul} µL is not positive"))
            elif not (pipette_min_ul <= step.volume_ul <= pipette_max_ul):
                violations.append(Violation(
                    "volume_out_of_pipette_range", i,
                    f"transfer volume {step.volume_ul} µL outside usable pipette range "
                    f"[{pipette_min_ul}, {pipette_max_ul}] µL"))
            # -- source constraint --
            if reservoirs is not None and step.source not in reservoirs:
                violations.append(Violation(
                    "unknown_source", i,
                    f"source {step.source!r} is not a declared reservoir "
                    f"{sorted(reservoirs)}"))
            # -- labware / capacity constraints (destination) --
            if step.destination not in known_wells:
                violations.append(Violation(
                    "unknown_destination_well", i,
                    f"destination {step.destination!r} is not in labware "
                    f"{lw.load_name!r}"))
            else:
                cap = lw.capacity_of(step.destination)
                projected = volumes.get(step.destination, 0.0) + max(step.volume_ul, 0.0)
                if projected > cap + 1e-6:
                    violations.append(Violation(
                        "destination_overflow", i,
                        f"destination {step.destination!r} would hold {projected} µL "
                        f"> capacity {cap} µL"))
                volumes[step.destination] = projected
                filled.add(step.destination)
                any_fill = True

        elif isinstance(step, Mix):
            if step.well not in filled:
                violations.append(Violation(
                    "mix_before_fill", i,
                    f"mix on well {step.well!r} which has received no liquid yet"))
            if step.cycles <= 0:
                violations.append(Violation(
                    "mix_nonpositive_cycles", i,
                    f"mix cycles {step.cycles} must be positive"))

        elif isinstance(step, Incubate):
            if not any_fill:
                violations.append(Violation(
                    "incubate_empty_plate", i,
                    "incubate with no prior transfer -- the plate is empty"))
            if step.minutes <= 0:
                violations.append(Violation(
                    "incubate_nonpositive_time", i,
                    f"incubate minutes {step.minutes} must be positive"))
            incubated = True

        elif isinstance(step, ReadPlate):
            if not step.wells:
                violations.append(Violation(
                    "read_no_wells", i, "read step names no wells"))
            for w in step.wells:
                if w not in filled:
                    violations.append(Violation(
                        "read_before_fill", i,
                        f"read of well {w!r} which has received no liquid "
                        "(nothing to read)"))
            if not incubated:
                # soft: an expression read before incubation yields no signal, but it is
                # not a physical impossibility -- warn, do not reject.
                warnings.append(
                    f"step {i}: read before any incubation (expression may be absent)")

        else:  # pragma: no cover - guarded by the closed step union
            violations.append(Violation(
                "unknown_step", i, f"unknown protocol step {step!r}"))

    return ConstraintReport(
        ok=not violations,
        violations=violations,
        warnings=warnings,
        well_volumes=volumes,
        filled_wells=frozenset(filled),
    )
