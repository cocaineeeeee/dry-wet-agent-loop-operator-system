"""Fake liquid handler + fake plate reader for M29 (v0.1 skeleton, SIMULATION only).

Each backend advertises a set of ``Opcode`` capabilities and executes matching ops,
producing a :class:`DispatchReceipt` that carries a sensed outcome. The state/outcome
vocabulary is imported READ-ONLY from the M23 action-ledger -- the fakes never mutate a
ledger here; they only PRODUCE the sensed bit the ledger's PENDING->COMMITTED gate would
consume, so full transactional wiring is a drop-in seam (docs/bio_seams/M29.md).
"""

from __future__ import annotations

from dataclasses import dataclass

# READ-ONLY reuse of the M23 transaction vocabulary (no ledger mutation here).
from expos.adapters.wet.action_ledger import ActionState, SensedOutcome
from device_ir.ir import DeviceOp, Opcode


@dataclass(frozen=True)
class DispatchReceipt:
    """The result of executing one device op on a fake backend."""

    op_id: str
    opcode: Opcode
    backend_id: str
    outcome: SensedOutcome
    final_state: ActionState
    reading: float | None = None  # populated for MEASURE ops
    detail: str = ""


class _FakeBackend:
    backend_id = "<fake>"
    capabilities: frozenset[Opcode] = frozenset()

    def can(self, opcode: Opcode) -> bool:
        return opcode in self.capabilities

    def execute(self, op: DeviceOp) -> DispatchReceipt:  # pragma: no cover - overridden
        raise NotImplementedError


class FakeLiquidHandler(_FakeBackend):
    """Executes ASPIRATE / DISPENSE / INCUBATE. Deterministic 'confirmed' fake."""

    backend_id = "fake_liquid_handler"
    capabilities = frozenset({Opcode.ASPIRATE, Opcode.DISPENSE, Opcode.INCUBATE})

    def execute(self, op: DeviceOp) -> DispatchReceipt:
        if not self.can(op.opcode):
            return DispatchReceipt(op.op_id, op.opcode, self.backend_id,
                                   SensedOutcome.MISMATCH, ActionState.ABORTED,
                                   detail=f"{self.backend_id} lacks {op.opcode.value}")
        return DispatchReceipt(op.op_id, op.opcode, self.backend_id,
                               SensedOutcome.CONFIRMED, ActionState.COMMITTED,
                               detail="fake dispatch ok")


class FakePlateReader(_FakeBackend):
    """Executes MEASURE. Returns a deterministic synthetic reading (NOT a truth channel)."""

    backend_id = "fake_plate_reader"
    capabilities = frozenset({Opcode.MEASURE})

    def execute(self, op: DeviceOp) -> DispatchReceipt:
        if not self.can(op.opcode):
            return DispatchReceipt(op.op_id, op.opcode, self.backend_id,
                                   SensedOutcome.MISMATCH, ActionState.ABORTED,
                                   detail=f"{self.backend_id} lacks {op.opcode.value}")
        wells = op.params.get("wells", [])
        reading = float(len(wells))  # deterministic synthetic placeholder reading
        return DispatchReceipt(op.op_id, op.opcode, self.backend_id,
                               SensedOutcome.CONFIRMED, ActionState.COMMITTED,
                               reading=reading, detail="fake read ok")


def dispatch(ops: list[DeviceOp], backends: list[_FakeBackend]) -> list[DispatchReceipt]:
    """Route each op to the first backend that advertises its opcode; execute in order.

    An op with no capable backend yields an ABORTED/MISMATCH receipt (loud, not silent).
    """
    receipts: list[DispatchReceipt] = []
    for op in ops:
        backend = next((b for b in backends if b.can(op.opcode)), None)
        if backend is None:
            receipts.append(DispatchReceipt(
                op.op_id, op.opcode, "<none>", SensedOutcome.MISMATCH,
                ActionState.ABORTED, detail=f"no backend for {op.opcode.value}"))
            continue
        receipts.append(backend.execute(op))
    return receipts
