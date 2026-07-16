"""Device-neutral instruction IR + protocol lowering (M29 v0.1).

The lowering target between a typed :class:`~protocols.objects.Protocol` and a concrete
physical backend. A :class:`DeviceOp` is a single opcode + a flat JSON-able param dict + a
stable id; it names NO device. ``lower()`` compiles a protocol into an ordered op list and
tags each op with a **unit** -- the transactional grouping the dispatcher commits/rolls
back atomically (a Transfer's aspirate+dispense pair share one unit; a mix/incubate/read
is its own unit). ``group_units()`` recovers those units in order.

Determinism is load-bearing: op ids / unit ids are positional, so the same protocol lowers
to a byte-identical op list and a stable :func:`ir_fingerprint` (the provenance anchor that
flips run identity if the lowering or the opcode set changes).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum

from protocols.objects import Incubate, Mix, Protocol, ReadPlate, Transfer

#: Version tag of the opcode set + lowering contract. Bumping it (or changing the lowering)
#: changes :func:`ir_fingerprint`, so a compiled protocol's identity is lowering-sensitive.
IR_VERSION = "m29-device-ir/v0.1"


class Opcode(str, Enum):
    """The minimal device-neutral opcode set. Each maps to a capability a backend
    advertises; a backend lacking an opcode's capability rejects the op LOUDLY (the
    dispatch-capability seam)."""

    ASPIRATE = "ASPIRATE"
    DISPENSE = "DISPENSE"
    MIX = "MIX"
    INCUBATE = "INCUBATE"
    MEASURE = "MEASURE"


#: Which unit-kind each op belongs to (the transactional grouping the dispatcher uses).
UNIT_TRANSFER = "transfer"
UNIT_MIX = "mix"
UNIT_INCUBATE = "incubate"
UNIT_MEASURE = "measure"


@dataclass(frozen=True)
class DeviceOp:
    """One lowered instruction: opcode + flat param dict + a stable id, tagged with the
    transactional unit it belongs to."""

    op_id: str
    opcode: Opcode
    unit_id: str
    unit_kind: str
    params: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Unit:
    """A transactional group of ops the dispatcher commits/rolls back as one action.

    ``kind`` selects the semantics: a ``transfer`` unit moves ``volume_ul`` from ``source``
    to ``destination`` (real double-entry volume accounting); ``mix`` / ``measure`` are
    self-actions on ``well`` (no net volume); ``incubate`` is plate-wide (``well`` is the
    synthetic deck sentinel). Every unit maps 1:1 to one M23 action-ledger transaction.
    """

    unit_id: str
    kind: str
    ops: tuple[DeviceOp, ...]
    source: str | None = None
    destination: str | None = None
    well: str | None = None
    volume_ul: float = 0.0
    params: dict[str, object] = field(default_factory=dict)


#: Synthetic deck sentinel well an INCUBATE unit is scoped to (plate-wide op has no well).
DECK_SENTINEL = "__deck__"


def lower(protocol: Protocol) -> list[DeviceOp]:
    """Compile a typed protocol into an ordered device-neutral op list.

    A ``Transfer`` lowers to an ASPIRATE+DISPENSE pair (the liquid-handler primitive)
    sharing one transfer unit; ``Mix``/``Incubate``/``ReadPlate`` lower 1:1. Deterministic:
    op/unit ids are positional.
    """
    ops: list[DeviceOp] = []
    for i, step in enumerate(protocol.steps):
        unit_id = f"{protocol.protocol_id}.u{i:03d}"
        base = f"{protocol.protocol_id}.op{i:03d}"
        if isinstance(step, Transfer):
            shared = {"source": step.source, "destination": step.destination,
                      "volume_ul": step.volume_ul, "reagent": step.reagent}
            ops.append(DeviceOp(f"{base}a", Opcode.ASPIRATE, unit_id, UNIT_TRANSFER,
                                {"well": step.source, "volume_ul": step.volume_ul,
                                 "role": "aspirate", **shared}))
            ops.append(DeviceOp(f"{base}b", Opcode.DISPENSE, unit_id, UNIT_TRANSFER,
                                {"well": step.destination, "volume_ul": step.volume_ul,
                                 "role": "dispense", **shared}))
        elif isinstance(step, Mix):
            ops.append(DeviceOp(base, Opcode.MIX, unit_id, UNIT_MIX,
                                {"well": step.well, "volume_ul": step.volume_ul,
                                 "cycles": step.cycles}))
        elif isinstance(step, Incubate):
            ops.append(DeviceOp(base, Opcode.INCUBATE, unit_id, UNIT_INCUBATE,
                                {"temperature_c": step.temperature_c,
                                 "minutes": step.minutes, "well": DECK_SENTINEL}))
        elif isinstance(step, ReadPlate):
            ops.append(DeviceOp(base, Opcode.MEASURE, unit_id, UNIT_MEASURE,
                                {"channel": step.channel, "wells": list(step.wells)}))
        else:  # pragma: no cover - guarded by the typed step union
            raise TypeError(f"cannot lower unknown protocol step {step!r}")
    return ops


def group_units(ops: list[DeviceOp]) -> list[Unit]:
    """Recover the ordered transactional units from a lowered op list.

    Consecutive ops sharing a ``unit_id`` form one unit; the semantic fields (source /
    destination / well / volume) are read from the ops so the dispatcher never re-parses
    protocol intent.
    """
    units: list[Unit] = []
    seen: dict[str, int] = {}
    for op in ops:
        if op.unit_id not in seen:
            seen[op.unit_id] = len(units)
            units.append(Unit(unit_id=op.unit_id, kind=op.unit_kind, ops=(op,)))
        else:
            u = units[seen[op.unit_id]]
            units[seen[op.unit_id]] = Unit(
                unit_id=u.unit_id, kind=u.kind, ops=u.ops + (op,),
                source=u.source, destination=u.destination, well=u.well,
                volume_ul=u.volume_ul, params=u.params)
    # fill semantic fields per unit kind
    out: list[Unit] = []
    for u in units:
        p = u.ops[0].params
        if u.kind == UNIT_TRANSFER:
            out.append(Unit(u.unit_id, u.kind, u.ops,
                            source=str(p["source"]), destination=str(p["destination"]),
                            well=str(p["destination"]), volume_ul=float(p["volume_ul"]),
                            params={"reagent": p.get("reagent", "")}))
        elif u.kind == UNIT_MIX:
            out.append(Unit(u.unit_id, u.kind, u.ops, source=str(p["well"]),
                            destination=str(p["well"]), well=str(p["well"]),
                            volume_ul=0.0,
                            params={"volume_ul": p["volume_ul"], "cycles": p["cycles"]}))
        elif u.kind == UNIT_INCUBATE:
            out.append(Unit(u.unit_id, u.kind, u.ops, source=DECK_SENTINEL,
                            destination=DECK_SENTINEL, well=DECK_SENTINEL, volume_ul=0.0,
                            params={"temperature_c": p["temperature_c"],
                                    "minutes": p["minutes"]}))
        elif u.kind == UNIT_MEASURE:
            wells = list(p["wells"])
            scope = wells[0] if wells else DECK_SENTINEL
            out.append(Unit(u.unit_id, u.kind, u.ops, source=scope, destination=scope,
                            well=scope, volume_ul=0.0,
                            params={"channel": p["channel"], "wells": wells}))
        else:  # pragma: no cover
            out.append(u)
    return out


def ir_fingerprint(ops: list[DeviceOp]) -> str:
    """sha256( IR_VERSION || canonical-json(op stream) ) -- the lowering provenance anchor.

    Deterministic and order-sensitive: a changed op, param, opcode set, or ordering flips
    the digest, so a protocol's *compiled form* is auditable and a lowering change flips run
    identity (the M29 device_ir-fingerprint seam, now realised locally)."""
    payload = [
        {"op_id": o.op_id, "opcode": o.opcode.value, "unit_id": o.unit_id,
         "unit_kind": o.unit_kind, "params": o.params}
        for o in ops
    ]
    canonical = json.dumps({"ir_version": IR_VERSION, "ops": payload},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                           default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
