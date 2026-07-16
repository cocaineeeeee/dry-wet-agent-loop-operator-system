"""Typed device-neutral protocol objects for M29 (v0.1 domain-local complete).

A :class:`Protocol` is an ordered list of high-level, instrument-agnostic steps. Steps
carry *intent* only (what to do to which well), never device coordinates or firmware
calls -- those are the device IR's job (``device_ir``). This mirrors the M23
``protocol_spec`` but is the minimal typed surface the v0.1 lowering + constraint checker
need.

The v0.1 step set covers the cell-free expression workflow the charter names
(加样 / 混合 / 孵育 / 读板):

  * :class:`Transfer` -- add/aspirate+dispense a volume from a source (reservoir) into a
    destination well (加样);
  * :class:`Mix`      -- resuspend/homogenise a destination well in place (混合);
  * :class:`Incubate` -- hold the whole plate at a temperature for a duration (孵育);
  * :class:`ReadPlate`-- read an optical channel for a set of wells (读板).

SAFETY (BIOLOGY_PROGRAM_2026 §6.1): the only biology modelled here is a **safe cell-free
protein-expression** assay (lysate + a reporter template -> fluorescence). No pathogen /
toxin / dual-use content. SIMULATION only -- these objects describe intent; nothing here
touches real hardware.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class ProtocolStep:
    """Base marker for a typed protocol step (the closed step union below)."""


@dataclass(frozen=True)
class Transfer(ProtocolStep):
    """Move ``volume_ul`` of liquid from ``source`` well/reservoir to ``destination``.

    ``reagent`` is a free-form label (e.g. ``"lysate"`` / ``"dna_template"``) used only
    for provenance and the machine report; it never affects lowering.
    """

    source: str
    destination: str
    volume_ul: float
    reagent: str = ""


@dataclass(frozen=True)
class Mix(ProtocolStep):
    """Homogenise ``well`` in place: ``cycles`` aspirate/dispense strokes of ``volume_ul``.

    A mix moves no *net* liquid (it aspirates and re-dispenses the same well), so it is a
    self-action in the transaction/volume ledger -- but it still requires the well to hold
    liquid first (an ordering constraint the checker enforces).
    """

    well: str
    volume_ul: float
    cycles: int = 3


@dataclass(frozen=True)
class Incubate(ProtocolStep):
    """Hold the plate at ``temperature_c`` for ``minutes`` (a plate-wide operation)."""

    temperature_c: float
    minutes: float


@dataclass(frozen=True)
class ReadPlate(ProtocolStep):
    """Read ``channel`` (e.g. ``"fluorescence_gfp"``) for an ordered list of wells."""

    channel: str
    wells: tuple[str, ...]


@dataclass(frozen=True)
class Protocol:
    """An ordered, named, device-neutral protocol."""

    protocol_id: str
    steps: tuple[ProtocolStep, ...] = field(default_factory=tuple)

    def transfers(self) -> tuple[Transfer, ...]:
        return tuple(s for s in self.steps if isinstance(s, Transfer))

    def destinations(self) -> tuple[str, ...]:
        """Every distinct destination well touched by a Transfer, in first-seen order."""
        seen: list[str] = []
        for s in self.steps:
            if isinstance(s, Transfer) and s.destination not in seen:
                seen.append(s.destination)
        return tuple(seen)


# --- charter workflow factory --------------------------------------------------

def cell_free_expression_protocol(
    protocol_id: str = "cfe_expression_v01",
    *,
    reaction_wells: tuple[str, ...] = ("B2", "B3", "B4"),
    control_wells: tuple[str, ...] = ("B5",),
    dna_source: str = "reservoir_dna",
    lysate_source: str = "reservoir_lysate",
    buffer_source: str = "reservoir_buffer",
    lysate_ul: float = 12.0,
    dna_ul: float = 3.0,
    incubate_c: float = 30.0,
    incubate_min: float = 120.0,
    channel: str = "fluorescence_gfp",
) -> Protocol:
    """Build a typed, safe cell-free protein-expression protocol.

    For each reaction well: add cell-free lysate, add the reporter DNA template, then mix.
    A no-template control well receives buffer instead of DNA (an intra-plate negative).
    The plate is then incubated and every well is read on the reporter channel.

    Deterministic: the same arguments yield the same ordered step tuple (hence the same
    ``device_ir`` fingerprint downstream). This is SIMULATION -- no cells, no hardware.
    """
    steps: list[ProtocolStep] = []
    all_wells = tuple(reaction_wells) + tuple(control_wells)
    for well in all_wells:
        steps.append(Transfer(lysate_source, well, lysate_ul, reagent="lysate"))
    for well in reaction_wells:
        steps.append(Transfer(dna_source, well, dna_ul, reagent="dna_template"))
    for well in control_wells:
        steps.append(Transfer(buffer_source, well, dna_ul, reagent="no_template_buffer"))
    for well in all_wells:
        steps.append(Mix(well, volume_ul=min(lysate_ul, 10.0), cycles=3))
    steps.append(Incubate(temperature_c=incubate_c, minutes=incubate_min))
    steps.append(ReadPlate(channel=channel, wells=all_wells))
    return Protocol(protocol_id=protocol_id, steps=tuple(steps))
