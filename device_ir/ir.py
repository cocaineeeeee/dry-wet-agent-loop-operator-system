"""Device-neutral instruction IR + protocol lowering (M29 v0.1 skeleton)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from protocols.objects import Protocol, Transfer, Incubate, ReadPlate


class Opcode(str, Enum):
    """The minimal device-neutral opcode set. Each maps to a capability a backend
    advertises; a backend that lacks an opcode's capability rejects the op (loud, not
    silent) -- the dispatch-capability seam listed in docs/bio_seams/M29.md."""

    ASPIRATE = "ASPIRATE"
    DISPENSE = "DISPENSE"
    INCUBATE = "INCUBATE"
    MEASURE = "MEASURE"


@dataclass(frozen=True)
class DeviceOp:
    """One lowered instruction: opcode + a flat, JSON-able param dict + a stable id."""

    op_id: str
    opcode: Opcode
    params: dict[str, object] = field(default_factory=dict)


def lower(protocol: Protocol) -> list[DeviceOp]:
    """Compile a typed protocol into an ordered device-neutral op list.

    A ``Transfer`` lowers to an ASPIRATE+DISPENSE pair (the liquid-handler primitive);
    ``Incubate`` and ``ReadPlate`` lower 1:1. Deterministic: op_ids are positional.
    """
    ops: list[DeviceOp] = []
    for i, step in enumerate(protocol.steps):
        base = f"{protocol.protocol_id}.op{i:03d}"
        if isinstance(step, Transfer):
            ops.append(DeviceOp(f"{base}a", Opcode.ASPIRATE,
                                {"well": step.source, "volume_ul": step.volume_ul}))
            ops.append(DeviceOp(f"{base}b", Opcode.DISPENSE,
                                {"well": step.destination, "volume_ul": step.volume_ul}))
        elif isinstance(step, Incubate):
            ops.append(DeviceOp(base, Opcode.INCUBATE,
                                {"temperature_c": step.temperature_c, "minutes": step.minutes}))
        elif isinstance(step, ReadPlate):
            ops.append(DeviceOp(base, Opcode.MEASURE,
                                {"channel": step.channel, "wells": list(step.wells)}))
        else:  # pragma: no cover - guarded by the typed step union
            raise TypeError(f"cannot lower unknown protocol step {step!r}")
    return ops
