"""Opentrons ``simulate`` protocol-execution leg (construct A of W4).

``compile_and_validate(spec)`` builds the liquid-gradient protocol and runs it
through the **real** opentrons protocol stack (labware defs, pipette range, deck
occupancy, tip logic). Any illegal protocol -- over-range volume, deck-slot
conflict, missing labware -- is loudly rejected at simulate time and surfaced as
:class:`ValidationError`. ``execute_simulated(otp)`` re-runs it and returns the
per-well liquid ledger (actual composition) plus a simulated duration.

If opentrons cannot be imported the module falls back to a same-interface
deck/volume/labware validator and reports the degradation via ``OT_BACKEND ==
"fallback"`` (and ``OTProtocol.backend``). The fallback still catches the three
headline failure classes the spec cares about; it just cannot vouch for the full
physical protocol stack, and says so honestly.

The volume-boundary and well-capacity pre-checks run in BOTH backends because the
opentrons ``transfer`` helper silently ignores non-positive volumes (verified),
so a spec-level guard is required regardless of backend.
"""

from __future__ import annotations

import io
import contextlib
from dataclasses import dataclass, field
from typing import Any

from .protocol_spec import ProtocolSpec, CustodyChain, is_edge


# --- backend detection ---------------------------------------------------------

try:  # real opentrons protocol stack
    from opentrons import simulate as _ot_simulate  # type: ignore

    OT_BACKEND = "opentrons"
except Exception:  # pragma: no cover - exercised only where opentrons absent
    _ot_simulate = None
    OT_BACKEND = "fallback"


class ValidationError(Exception):
    """Raised when a protocol spec fails compilation/validation, either backend."""


@dataclass
class WellPlan:
    """One planned well: sample + deck position + realised mix (custody seg 1-2)."""

    well_id: str
    sample_id: str
    cand_id: str | None
    control_id: str | None
    target_polarity: float
    vol_low_ul: float
    vol_high_ul: float
    is_edge: bool

    @property
    def total_ul(self) -> float:
        return self.vol_low_ul + self.vol_high_ul

    @property
    def realised_polarity(self) -> float:
        """The polarity actually achieved by the mix (== target when exact)."""
        # inverse of mix_volumes: recovered for the reader's ground-truth input.
        return self._realised

    _realised: float = 0.0


@dataclass
class OTProtocol:
    """A compiled, validated protocol + its custody chain (segments 1-2 bound)."""

    backend: str
    spec: ProtocolSpec
    wells: list[WellPlan]
    custody: CustodyChain
    validated: bool = True
    warnings: list[str] = field(default_factory=list)

    def sample_ids(self) -> list[str]:
        return [w.sample_id for w in self.wells]

    def reader_samples(self) -> list[dict[str, Any]]:
        """The measurement order handed to the plate reader (custody seg 2->3)."""
        return [
            {
                "sample_id": w.sample_id,
                "well_id": w.well_id,
                "polarity": w.realised_polarity,
                "is_control": w.control_id is not None,
            }
            for w in self.wells
        ]


# --- volume / capacity pre-checks (backend-independent) ------------------------

def _pre_check_volumes(spec: ProtocolSpec) -> list[WellPlan]:
    plans: list[WellPlan] = []
    pa, pb = spec.stock_low.polarity, spec.stock_high.polarity
    for i, s in enumerate(spec.samples):
        well_id = spec.well_for(i)
        vol_low, vol_high = spec.mix_volumes(s.target_polarity)
        # boundary 1: target polarity must be spannable by the two stocks
        if not (min(pa, pb) <= s.target_polarity <= max(pa, pb)):
            raise ValidationError(
                f"sample {s.cand_id}: target polarity {s.target_polarity} outside "
                f"stock span [{pa}, {pb}] -- cannot be mixed"
            )
        # boundary 2: no negative volumes (opentrons silently drops these)
        if vol_low < 0 or vol_high < 0:
            raise ValidationError(
                f"sample {s.cand_id}: negative mix volume "
                f"(low={vol_low:.2f}, high={vol_high:.2f} uL)"
            )
        # boundary 3: each non-zero aspiration must fit the pipette range
        for label, v in (("low", vol_low), ("high", vol_high)):
            if v > 0 and (v < spec.pipette_min_ul or v > spec.pipette_max_ul):
                raise ValidationError(
                    f"sample {s.cand_id}: {label}-stock volume {v:.2f} uL out of "
                    f"pipette range [{spec.pipette_min_ul}, {spec.pipette_max_ul}]"
                )
        # boundary 4: total must not overflow the destination well
        if vol_low + vol_high > spec.well_capacity_ul:
            raise ValidationError(
                f"sample {s.cand_id}: total volume {vol_low + vol_high:.2f} uL "
                f"exceeds well capacity {spec.well_capacity_ul} uL"
            )
        plan = WellPlan(
            well_id=well_id,
            sample_id=s.sample_id,
            cand_id=None if s.is_control else s.cand_id,
            control_id=s.control_id if s.is_control else None,
            target_polarity=s.target_polarity,
            vol_low_ul=vol_low,
            vol_high_ul=vol_high,
            is_edge=is_edge(well_id),
        )
        # realised polarity from the (exact) mix accounting
        frac_high = vol_high / (vol_low + vol_high) if (vol_low + vol_high) else 0.0
        plan._realised = pa + frac_high * (pb - pa)
        plans.append(plan)
    return plans


# --- opentrons backend ---------------------------------------------------------

def _build_ot_context(spec: ProtocolSpec):
    """Load labware + pipette into a fresh simulate context (deck/labware checks)."""
    ctx = _ot_simulate.get_protocol_api("2.15")
    try:
        plate = ctx.load_labware(spec.plate_labware, spec.plate_slot)
        reservoir = ctx.load_labware(spec.reservoir_labware, spec.reservoir_slot)
        tiprack = ctx.load_labware(spec.tiprack_labware, spec.tiprack_slot)
        pipette = ctx.load_instrument(
            spec.pipette, spec.mount, tip_racks=[tiprack]
        )
    except Exception as exc:  # labware missing / slot conflict / bad pipette
        raise ValidationError(
            f"opentrons load failed: {type(exc).__name__}: {exc}"
        ) from exc
    return ctx, plate, reservoir, pipette


def _run_ot(spec: ProtocolSpec, plans: list[WellPlan]) -> None:
    """Execute all transfers in a simulate context; raise ValidationError on any
    opentrons protocol-engine rejection (over-volume, tip logic, ...)."""
    ctx, plate, reservoir, pipette = _build_ot_context(spec)
    low_src = reservoir[spec.stock_low.reservoir_well]
    high_src = reservoir[spec.stock_high.reservoir_well]
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            for plan in plans:
                dest = plate[plan.well_id]
                # new tip per stock per well: honest, avoids cross-contamination
                if plan.vol_low_ul > 0:
                    pipette.transfer(plan.vol_low_ul, low_src, dest)
                if plan.vol_high_ul > 0:
                    pipette.transfer(plan.vol_high_ul, high_src, dest)
    except Exception as exc:
        raise ValidationError(
            f"opentrons simulate rejected protocol: {type(exc).__name__}: "
            f"{str(exc)[:200]}"
        ) from exc


# --- public API ----------------------------------------------------------------

def compile_and_validate(spec: ProtocolSpec) -> OTProtocol:
    """Compile ``spec`` into a validated :class:`OTProtocol` or raise.

    Order: spec-level volume/capacity guards first (catch non-positive volumes
    opentrons ignores), then the real opentrons protocol stack (labware / deck /
    pipette). In fallback mode only the spec-level guards + a structural
    labware/deck check run, and a warning records the reduced assurance.
    """
    if not spec.samples:
        raise ValidationError("protocol spec has no samples")

    plans = _pre_check_volumes(spec)
    warnings: list[str] = []

    if OT_BACKEND == "opentrons":
        _run_ot(spec, plans)
    else:
        warnings.append(
            "DEGRADED: opentrons unavailable; validated by fallback "
            "deck/volume/labware checker only (physical protocol stack NOT "
            "exercised)"
        )
        _fallback_structural_check(spec)

    custody = CustodyChain()
    for s, plan in zip(spec.samples, plans):
        custody.issue(s)
        custody.bind_deck(
            plan.sample_id, spec.plate_slot, plan.well_id,
            plan.target_polarity, plan.vol_low_ul, plan.vol_high_ul,
        )

    return OTProtocol(
        backend=OT_BACKEND, spec=spec, wells=plans, custody=custody,
        validated=True, warnings=warnings,
    )


def _fallback_structural_check(spec: ProtocolSpec) -> None:
    """Deck/labware sanity when opentrons is missing (same failure classes)."""
    # missing labware: a known-good allow-list of load-names we ship with.
    known = {
        "corning_96_wellplate_360ul_flat",
        "nest_12_reservoir_15ml",
        "opentrons_96_tiprack_300ul",
        "opentrons_24_tuberack_generic_2ml_screwcap",
    }
    for name in (spec.plate_labware, spec.reservoir_labware, spec.tiprack_labware):
        if name not in known:
            raise ValidationError(
                f"fallback: unknown labware load-name {name!r} "
                f"(known={sorted(known)})"
            )
    # deck-slot conflict
    slots = [spec.plate_slot, spec.reservoir_slot, spec.tiprack_slot]
    if len(set(slots)) != len(slots):
        raise ValidationError(
            f"fallback: deck-slot conflict among {slots} "
            f"(plate/reservoir/tiprack must occupy distinct slots)"
        )
    if not (1 <= min(slots) and max(slots) <= 11):
        raise ValidationError(f"fallback: deck slot out of range 1-11: {slots}")


def execute_simulated(otp: OTProtocol) -> dict[str, Any]:
    """Run the validated protocol and return the liquid ledger + simulated time.

    Returns::

        {
          "backend": "opentrons"|"fallback",
          "duration_s": float,                 # modelled simulated wall-time
          "wells": {well_id: {sample_id, cand_id, control_id,
                              vol_low_ul, vol_high_ul, total_ul,
                              realised_polarity}},
          "n_transfers": int,
        }

    The composition is deterministic accounting of the validated plan (which the
    opentrons stack already certified physically executable), so plan == actual.
    """
    if not otp.validated:
        raise ValidationError("cannot execute an unvalidated protocol")

    # Re-run through opentrons to prove executability is repeatable (idempotent).
    if otp.backend == "opentrons" and OT_BACKEND == "opentrons":
        _run_ot(otp.spec, otp.wells)

    ledger: dict[str, Any] = {}
    n_transfers = 0
    # simple, deterministic duration model (seconds): fixed overhead + per-aspirate
    aspirate_s, dispense_s, move_s, tip_s = 2.0, 2.0, 3.0, 4.0
    duration = 5.0  # deck setup overhead
    for w in otp.wells:
        ledger[w.well_id] = {
            "sample_id": w.sample_id,
            "cand_id": w.cand_id,
            "control_id": w.control_id,
            "vol_low_ul": round(w.vol_low_ul, 3),
            "vol_high_ul": round(w.vol_high_ul, 3),
            "total_ul": round(w.total_ul, 3),
            "realised_polarity": round(w.realised_polarity, 5),
        }
        for v in (w.vol_low_ul, w.vol_high_ul):
            if v > 0:
                n_transfers += 1
                duration += tip_s + aspirate_s + move_s + dispense_s
    return {
        "backend": otp.backend,
        "duration_s": round(duration, 2),
        "wells": ledger,
        "n_transfers": n_transfers,
    }
