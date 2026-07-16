"""M29 ``device_ir`` -- v0.1 SKELETON device-neutral instruction IR.

The lowering target between a typed :class:`~protocols.objects.Protocol` and a concrete
physical backend. A ``DeviceOp`` is a single opcode + params tuple that any conforming
backend knows how to execute; ``lower()`` compiles a protocol into an ordered op list.
Keeping this layer explicit means the protocol never names a device and the backend
never parses protocol intent -- the seam a real multi-instrument deployment needs.
"""

from device_ir.ir import (
    DeviceOp,
    Opcode,
    Unit,
    IR_VERSION,
    DECK_SENTINEL,
    lower,
    group_units,
    ir_fingerprint,
)

__all__ = [
    "DeviceOp", "Opcode", "Unit", "IR_VERSION", "DECK_SENTINEL",
    "lower", "group_units", "ir_fingerprint",
]
