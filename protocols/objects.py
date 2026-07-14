"""Typed device-neutral protocol objects for M29 (v0.1 skeleton).

A Protocol is an ordered list of high-level, instrument-agnostic steps. Steps carry
*intent* only (what to do to which well), never device coordinates or firmware calls --
those are the device IR's job (``device_ir``). This mirrors the M23 protocol_spec but is
the minimal typed surface the v0.1 lowering needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class ProtocolStep:
    """Base marker for a typed protocol step."""


@dataclass(frozen=True)
class Transfer(ProtocolStep):
    """Move ``volume_ul`` of liquid from ``source`` well to ``destination`` well."""

    source: str
    destination: str
    volume_ul: float


@dataclass(frozen=True)
class Incubate(ProtocolStep):
    """Hold the plate at ``temperature_c`` for ``minutes``."""

    temperature_c: float
    minutes: float


@dataclass(frozen=True)
class ReadPlate(ProtocolStep):
    """Read ``channel`` (e.g. fluorescence) for a list of wells."""

    channel: str
    wells: tuple[str, ...]


@dataclass(frozen=True)
class Protocol:
    """An ordered, named, device-neutral protocol."""

    protocol_id: str
    steps: tuple[ProtocolStep, ...] = field(default_factory=tuple)
