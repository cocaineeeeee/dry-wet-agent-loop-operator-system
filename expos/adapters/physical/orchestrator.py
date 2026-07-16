"""Protocol executor: drive lowered device_ir units through the M23 transaction ledger.

This is the M29 v0.1 domain-local execution engine. It closes the loop

    typed Protocol -> constraint check -> device_ir units -> **simulated dispatch** ->
    sensed-state gate -> commit / rollback / await-recovery -> observation

by reusing the M23 real-wet transaction facet WITHOUT modifying it: every device_ir
:class:`~device_ir.ir.Unit` maps 1:1 to one
:class:`~expos.adapters.wet.action_ledger.ActionLedger` action. A liquid transfer moves
real volume (double-entry); a mix / incubate / measure is a zero-volume self-action -- but
EVERY unit reaches COMMITTED only through the SAME sensed-state gate the ledger enforces
(``confirm`` with a :class:`SensedEvidence`), so the loop advances on observed state, not
blind sequence.

The five execution faces the charter demands are all expressed through the ledger's public
API + the backends' fault plan:

  * **normal**          -- all sensed CONFIRMED -> every unit COMMITTED;
  * **wrong well**      -- a sensed MISMATCH -> RecoveryPolicy adjudicates -> ROLLED_BACK
                           (NeverRecover) or AWAITING_RECOVERY (WaitForRecovery);
  * **timeout**         -- a sensed UNOBSERVED -> the unit stays PENDING (Unknown, NOT a
                           failure); a later CONFIRMED re-sense commits it (closed loop);
  * **duplicate reply** -- re-dispatching a known action_id with the same fingerprint is an
                           idempotent replay -- NOTHING is re-sent (the resume red line);
  * **partial execution / resume** -- a fresh executor over the same run dir replays the
                           hash-chained ledger, refuses to re-dispatch COMMITTED units, and
                           re-senses the PENDING ones.

HONESTY (BIOLOGY_PROGRAM_2026 §5): this is **protocol-to-simulated-physical** against a
fake backend. It is NOT a physical autonomous laboratory; real hardware / real wet-lab
validation is pending. The commit/rollback/resume machinery is real; the physics is faked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from expos.adapters.wet.action_ledger import (
    ActionLedger,
    ActionState,
    PlannedAction,
    SensedOutcome,
    VolumeLedger,
)
from expos.adapters.wet.labware import Labware, load_labware
from expos.adapters.wet.recovery import RecoveryPolicy
from device_ir.ir import DECK_SENTINEL, Opcode, Unit, UNIT_MEASURE, group_units
from expos.adapters.physical.fake_backends import FakePlateReader, _FakeBackend, pick_backend


@dataclass
class UnitResult:
    """The outcome of executing one device_ir unit through the ledger."""

    unit_id: str
    kind: str
    action_id: str
    final_state: ActionState
    outcome: SensedOutcome
    committed: bool
    reading: float | None = None
    detail: str = ""


@dataclass
class RunLog:
    """The result of executing a protocol's units (one round)."""

    protocol_id: str
    round_id: int
    units: list[UnitResult] = field(default_factory=list)
    readings: dict[str, float] = field(default_factory=dict)  # unit_id -> reading

    @property
    def all_committed(self) -> bool:
        return all(u.committed for u in self.units)

    def by_state(self, state: ActionState) -> list[UnitResult]:
        return [u for u in self.units if u.final_state is state]


#: opcode used to route a unit to a backend (a transfer routes on DISPENSE; the rest on
#: their single op's opcode).
def _routing_opcode(unit: Unit) -> Opcode:
    if unit.kind == UNIT_MEASURE:
        return Opcode.MEASURE
    return unit.ops[-1].opcode  # transfer -> DISPENSE; mix/incubate -> their op


class ProtocolExecutor:
    """Drive lowered units through a real :class:`ActionLedger` over a run directory."""

    def __init__(
        self,
        run_dir: str | Path,
        backends: list[_FakeBackend],
        *,
        protocol_id: str = "protocol",
        round_id: int = 0,
        spec_fingerprint: str = "",
        policy: RecoveryPolicy | None = None,
        reservoirs: dict[str, float] | None = None,
        labware: Labware | None = None,
    ) -> None:
        lw = labware or load_labware()
        # Seed off-plate source volumes (reservoirs) + the synthetic deck sentinel so a
        # zero-volume self-action (mix/incubate/measure) passes the ledger prechecks.
        caps: dict[str, float] = {DECK_SENTINEL: 1e12}
        init: dict[str, float] = {DECK_SENTINEL: 0.0}
        for name, vol in (reservoirs or {}).items():
            init[name] = vol
            caps[name] = max(vol * 10.0, 1e6)  # reservoirs never overflow in v0.1
        volume = VolumeLedger(capacities=caps, initial=init, plate=lw)
        self.ledger = ActionLedger(run_dir, volume=volume, policy=policy, plate=lw)
        self.backends = backends
        self.protocol_id = protocol_id
        self.round_id = round_id
        self.spec_fingerprint = spec_fingerprint

    def _planned_action(self, unit: Unit) -> PlannedAction:
        backend = pick_backend(self.backends, _routing_opcode(unit))
        backend_id = backend.backend_id if backend else "<none>"
        return PlannedAction(
            action_id=unit.unit_id, round_id=self.round_id,
            spec_fingerprint=self.spec_fingerprint,
            source_well=unit.source or DECK_SENTINEL,
            destination_well=unit.destination or DECK_SENTINEL,
            requested_volume_ul=float(unit.volume_ul),
            backend_id=backend_id,
        )

    def execute_unit(self, unit: Unit) -> UnitResult:
        """Dispatch one unit: PENDING (before I/O) -> sensed gate -> terminal/pending.

        Reuses the ledger's idempotency + resume guarantees: a known action_id is an
        idempotent replay (no re-send); a COMMITTED unit on resume is skipped; a PENDING
        one is re-sensed.
        """
        backend = pick_backend(self.backends, _routing_opcode(unit))
        if backend is None:
            # loud: no capable backend for this unit's opcode.
            return UnitResult(unit.unit_id, unit.kind, unit.unit_id,
                              ActionState.ABORTED, SensedOutcome.MISMATCH, committed=False,
                              detail=f"no backend for {_routing_opcode(unit).value}")

        existing = self.ledger._records.get(unit.unit_id)
        action = self._planned_action(unit)

        # Resume: a COMMITTED unit is skipped (no re-send); a PENDING one is re-sensed only.
        if existing is not None and existing.state is ActionState.COMMITTED:
            return self._result(unit, existing.state, SensedOutcome.CONFIRMED, backend,
                                detail="resume: already committed (skipped)")
        if existing is None or existing.state is ActionState.PLANNED:
            self.ledger.dispatch(action, io_call=lambda: backend.io_call(unit.ops[-1]))

        rec = self.ledger.record(unit.unit_id)
        if rec.state is ActionState.PENDING:
            evidence = backend.sense(action, attempt=rec.attempt)
            self.ledger.confirm(unit.unit_id, evidence)
            rec = self.ledger.record(unit.unit_id)
            outcome = evidence.outcome
        else:  # AWAITING_RECOVERY etc. -- surface the current state
            outcome = SensedOutcome.UNOBSERVED
        return self._result(unit, rec.state, outcome, backend)

    def _result(
        self, unit: Unit, state: ActionState, outcome: SensedOutcome,
        backend: _FakeBackend, *, detail: str = "",
    ) -> UnitResult:
        reading: float | None = None
        if (unit.kind == UNIT_MEASURE and state is ActionState.COMMITTED
                and isinstance(backend, FakePlateReader)):
            reading = backend.read(unit.ops[-1])
        return UnitResult(
            unit_id=unit.unit_id, kind=unit.kind, action_id=unit.unit_id,
            final_state=state, outcome=outcome,
            committed=state is ActionState.COMMITTED, reading=reading, detail=detail)

    def run(self, units: list[Unit]) -> RunLog:
        """Execute every unit in order, returning the full run log."""
        log = RunLog(protocol_id=self.protocol_id, round_id=self.round_id)
        for unit in units:
            res = self.execute_unit(unit)
            log.units.append(res)
            if res.reading is not None:
                log.readings[res.unit_id] = res.reading
        return log


def run_ops(
    ops,
    backends: list[_FakeBackend],
    run_dir: str | Path,
    *,
    protocol_id: str = "protocol",
    round_id: int = 0,
    spec_fingerprint: str = "",
    policy: RecoveryPolicy | None = None,
    reservoirs: dict[str, float] | None = None,
) -> RunLog:
    """Convenience: group a lowered op list into units and execute them transactionally."""
    executor = ProtocolExecutor(
        run_dir, backends, protocol_id=protocol_id, round_id=round_id,
        spec_fingerprint=spec_fingerprint, policy=policy, reservoirs=reservoirs)
    return executor.run(group_units(ops))
