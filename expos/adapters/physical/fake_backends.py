"""Fake liquid handler + fake plate reader for M29 (v0.1, SIMULATION only).

These fakes are the physical-backend seam the M23 real-wet transaction contract was built
for. Each backend:

  * advertises a set of :class:`~device_ir.ir.Opcode` capabilities (a real instrument that
    lacks a capability is rejected LOUDLY, never silently no-op'd);
  * runs an op's hardware I/O leg (``io_call`` -- the driver OK reply, RECORDED but never
    treated as physical truth);
  * implements the M23 :class:`~expos.adapters.wet.action_ledger.SensedState` protocol
    (``sense``): the read-back that GATES the ledger's PENDING->COMMITTED transition. A
    driver OK alone never commits -- only a sensed confirmation does (INDEX_M21 §4).

BioProVLA independently converges on this same design from the vision side (docs/bio_refs/
04 §4): "sensed-state verification is the execution gate, not an after-the-fact log". The
fake plate reader's read-back (``sense`` for a MEASURE unit) flows through the *same* commit
gate, so the loop advances on observed state, not blind sequence.

A per-action **fault plan** lets a caller/test inject the five execution faces (confirm /
mismatch=wrong-well / unobserved=timeout) at any action_id; the default is CONFIRMED.

SIMULATION only: readings are deterministic synthetic values, NOT a truth channel; no real
firmware, no real hardware.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

# READ-ONLY reuse of the M23 transaction vocabulary + sensed-state contract.
from expos.adapters.wet.action_ledger import (
    ActionState,
    PlannedAction,
    SensedEvidence,
    SensedOutcome,
)
from device_ir.ir import DeviceOp, Opcode


@dataclass(frozen=True)
class DispatchReceipt:
    """The result of executing one device op / unit on a fake backend."""

    op_id: str
    opcode: Opcode
    backend_id: str
    outcome: SensedOutcome
    final_state: ActionState
    reading: float | None = None   # populated for MEASURE units
    detail: str = ""


@dataclass
class Fault:
    """An injected sensed outcome for one action_id (a face driver).

    ``outcome`` = what the read-back will report; ``code`` classifies a MISMATCH for the
    RecoveryPolicy (a defined :data:`~expos.adapters.wet.recovery.FAILURE_MODES` code, e.g.
    ``E_DEVICE`` for a recoverable mispositioning; an unknown code fails closed)."""

    outcome: SensedOutcome
    code: str = ""
    detail: str = ""


class _FakeBackend:
    backend_id = "<fake>"
    capabilities: frozenset[Opcode] = frozenset()

    def __init__(self) -> None:
        #: action_id -> injected Fault. Default (absent) == CONFIRMED.
        self.faults: dict[str, Fault] = {}
        #: op_ids whose io_call actually ran (duplicate-reply face inspects this).
        self.io_calls: list[str] = []

    def can(self, opcode: Opcode) -> bool:
        return opcode in self.capabilities

    def inject(self, action_id: str, fault: Fault) -> "_FakeBackend":
        self.faults[action_id] = fault
        return self

    # -- driver I/O leg (recorded, NOT physical truth) ------------------------

    def io_call(self, op: DeviceOp) -> bool:
        """Run the op's hardware I/O and return the driver's OK/not-OK reply.

        A timeout-faced action returns not-OK here, but that ALONE never rolls the action
        back -- the ledger waits for the (inconclusive) sensed read-back. Confirmation is
        the ONLY commit signal."""
        self.io_calls.append(op.op_id)
        fault = self.faults.get(op.unit_id)
        return not (fault is not None and fault.outcome is SensedOutcome.UNOBSERVED)

    # -- sensed-state gate (M23 SensedState protocol) -------------------------

    def sense(self, action: PlannedAction, *, attempt: int) -> SensedEvidence:
        """Read-back that gates PENDING->COMMITTED. Stamps ``for_attempt=attempt``.

        Default: CONFIRMED with the observed volume == the requested volume. A fault plan
        entry overrides this to a MISMATCH (wrong-well) or UNOBSERVED (timeout)."""
        ev_id = f"ev-{action.action_id}-a{attempt}"
        fault = self.faults.get(action.action_id)
        if fault is None or fault.outcome is SensedOutcome.CONFIRMED:
            return SensedEvidence(
                evidence_id=ev_id, for_attempt=attempt,
                outcome=SensedOutcome.CONFIRMED,
                observed_volume_ul=action.requested_volume_ul,
                detail="fake sensed confirmation")
        if fault.outcome is SensedOutcome.MISMATCH:
            return SensedEvidence(
                evidence_id=ev_id, for_attempt=attempt, outcome=SensedOutcome.MISMATCH,
                code=fault.code or "E_DEVICE",
                detail=fault.detail or "sensed read-back contradicts request "
                       "(wrong well / wrong volume)")
        # UNOBSERVED: no conclusive read-back -> action stays PENDING (Unknown != failure).
        return SensedEvidence(
            evidence_id=ev_id, for_attempt=attempt, outcome=SensedOutcome.UNOBSERVED,
            detail=fault.detail or "no conclusive read-back (timeout)")

    def execute(self, op: DeviceOp) -> DispatchReceipt:  # pragma: no cover - legacy smoke
        """Back-compat single-op execute (the original skeleton surface). Returns a
        confirmed/committed receipt unless the backend lacks the capability. The
        transactional path is :mod:`expos.adapters.physical.orchestrator`."""
        if not self.can(op.opcode):
            return DispatchReceipt(op.op_id, op.opcode, self.backend_id,
                                   SensedOutcome.MISMATCH, ActionState.ABORTED,
                                   detail=f"{self.backend_id} lacks {op.opcode.value}")
        return DispatchReceipt(op.op_id, op.opcode, self.backend_id,
                               SensedOutcome.CONFIRMED, ActionState.COMMITTED,
                               detail="fake dispatch ok")


class FakeLiquidHandler(_FakeBackend):
    """Executes ASPIRATE / DISPENSE / MIX / INCUBATE. Deterministic confirmed fake."""

    backend_id = "fake_liquid_handler"
    capabilities = frozenset(
        {Opcode.ASPIRATE, Opcode.DISPENSE, Opcode.MIX, Opcode.INCUBATE})


class FakePlateReader(_FakeBackend):
    """Executes MEASURE. Its ``sense`` read-back IS the confirmation that a well was read;
    ``read`` returns a deterministic synthetic reading (NOT a truth channel)."""

    backend_id = "fake_plate_reader"
    capabilities = frozenset({Opcode.MEASURE})

    def read(self, op: DeviceOp) -> float:
        """A deterministic synthetic reading for a MEASURE op (well count -> a stable
        pseudo-fluorescence). SIMULATION -- not a real optical measurement."""
        wells = op.params.get("wells", []) or []
        channel = str(op.params.get("channel", ""))
        h = hashlib.sha256(f"{channel}|{','.join(map(str, wells))}".encode()).hexdigest()
        # map to a small deterministic float in [len(wells), len(wells)+1)
        frac = int(h[:8], 16) / 0xFFFFFFFF
        return round(float(len(wells)) + frac, 6)


def pick_backend(backends: list[_FakeBackend], opcode: Opcode) -> _FakeBackend | None:
    """The first backend advertising ``opcode`` (capability routing)."""
    return next((b for b in backends if b.can(opcode)), None)


def dispatch(ops: list[DeviceOp], backends: list[_FakeBackend]) -> list[DispatchReceipt]:
    """Legacy op-by-op dispatch (the original skeleton surface, kept for the smoke).

    Routes each op to the first capable backend and executes it. An op with no capable
    backend yields an ABORTED/MISMATCH receipt (loud, not silent). The full transactional
    (commit/rollback/resume) path lives in :mod:`expos.adapters.physical.orchestrator`.
    """
    receipts: list[DispatchReceipt] = []
    for op in ops:
        backend = pick_backend(backends, op.opcode)
        if backend is None:
            receipts.append(DispatchReceipt(
                op.op_id, op.opcode, "<none>", SensedOutcome.MISMATCH,
                ActionState.ABORTED, detail=f"no backend for {op.opcode.value}"))
            continue
        rec = backend.execute(op)
        if op.opcode is Opcode.MEASURE and isinstance(backend, FakePlateReader):
            rec = DispatchReceipt(rec.op_id, rec.opcode, rec.backend_id, rec.outcome,
                                  rec.final_state, reading=backend.read(op),
                                  detail=rec.detail)
        receipts.append(rec)
    return receipts
