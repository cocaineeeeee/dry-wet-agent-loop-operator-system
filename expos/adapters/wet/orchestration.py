"""M23 Phase 4-A: the pure physical-dispatch ORCHESTRATION facade.

This module is the single, backend-agnostic *front door* that ``mcl.py`` (session B)
consumes to run a round's physical transfers through the real-wet transaction contract.
It is a THIN convergence over Phase 3's :class:`~expos.adapters.wet.fake_physical.
PhysicalDispatch` (the ``dispatch -> I/O -> sense -> confirm`` loop, the ``attempt++``
recovery hook, and the resume trichotomy) -- it RE-EXPOSES those already-tested Phase 3
behaviours behind one stable API; it does NOT reimplement any adjudication, transition,
or volume logic. The face works with ANY :class:`~expos.adapters.wet.action_ledger.
SensedState` implementation (the fake backend today, a real instrument later), so the
wiring point mcl consumes never changes when the backend is swapped.

THE "COMMIT-BEFORE-OBSERVATION" GATE (Phase 4 red line, enforced STRUCTURALLY here)
---------------------------------------------------------------------------------
A wet observation may be produced ONLY from a COMMITTED physical action. This module
makes that a *structural* guarantee, not a convention: :class:`DispatchRoundResult`
exposes observed values ONLY through :attr:`DispatchRoundResult.committed_results`
(each a :class:`CommittedResult` carrying the well's observed volume + evidence id),
and every non-COMMITTED action is listed in :attr:`DispatchRoundResult.non_committed`
as a :class:`NonCommittedAction` that carries NO observed-value field at all. The
caller (mcl) therefore *cannot* read a non-committed action's "observation" -- there is
no attribute to read. The gate is the absence of the field, not a runtime check the
caller must remember to make.

EVENT-EMISSION ORDERING GUARANTEES (append-only, inherited from the ledger)
--------------------------------------------------------------------------
Every state transition is an append-only ``physical_action_transition`` event (already
registered by session B). The ledger the facade drives guarantees, and this facade
preserves, two orderings mcl relies on:

  * **PENDING event precedes the hardware I/O.** ``ActionLedger.dispatch`` persists (and
    flushes) the ``-> PENDING`` transition line BEFORE it runs ``io_call``; the I/O
    reply is a *later* append (a ``driver_reply_recorded_not_committed`` note). A crash
    between them leaves a crash-visible PENDING, never a silent lost send.
  * **COMMITTED event precedes committed_results.** The ``-> COMMITTED`` transition line
    is appended by ``ActionLedger.confirm`` DURING ``dispatch_round``; the returned
    :class:`DispatchRoundResult` is built AFTER the whole sequence resolves, reading
    already-appended records. So no ``CommittedResult`` can exist before its COMMITTED
    event is on the append-only log -- the event is always the source, the result the
    derivative.

OUT-OF-BAND RECOVERY PORT
-------------------------
An action that a mismatch parked in ``AWAITING_RECOVERY`` is resolved out of band, NOT
inside :func:`dispatch_round`: :func:`recover_action` (operator ``attempt++`` re-sense)
and :func:`cancel_action` (operator cancel -> ABORTED) are exposed alongside the round
face, and :func:`resume_round` is the crash-resume face (idempotent continuation via the
Phase 3 resume trichotomy). All four share one construction helper.

HARNESS-SEPARATION INVARIANT (Phase 4 completion criterion #3 -- DO NOT VIOLATE)
-------------------------------------------------------------------------------
This physical-orchestration module MUST NOT import ``expos.eval.harness_record`` (nor any
other evaluation-harness module). The evaluation harness and the decision/dispatch path
stay structurally separate -- symmetric to the existing truth-blind guard on the wet
observation channel. Session B adds an AST-level guard test asserting this module's import
set is harness-free; keep it that way. The facade is deliberately confined to
``adapters.wet`` (ledger + backend) and imports nothing from ``expos.eval`` / ``expos.mcl``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .action_ledger import (
    ActionLedger,
    ActionRecord,
    ActionState,
    PlannedAction,
    SensedState,
)
from .fake_physical import PhysicalDispatch, ResumeDisposition, VirtualClock

__all__ = [
    "CommittedResult",
    "NonCommittedAction",
    "DispatchRoundResult",
    "dispatch_round",
    "resume_round",
    "recover_action",
    "cancel_action",
    # Re-exported so callers wanting the per-action resume trichotomy verdict (rather than
    # the round-level partition) never reach past this facade into Phase 3.
    "ResumeDisposition",
]


# --- the two result shapes (the structural commit-before-observation gate) -------

@dataclass(frozen=True)
class CommittedResult:
    """One COMMITTED physical action's observed outcome -- the ONLY channel through which
    an observed volume leaves the orchestration. ``observed_volume_ul`` is the sensed
    read-back the commit gate accepted (distinct from ``requested_volume_ul``, never
    overwriting it). mcl builds a wet observation from THESE and only these."""

    action_id: str
    destination_well: str
    source_well: str
    observed_volume_ul: float | None
    requested_volume_ul: float
    sensed_evidence_id: str | None
    attempt: int


@dataclass(frozen=True)
class NonCommittedAction:
    """A physical action that did NOT reach COMMITTED (PENDING / ROLLED_BACK /
    AWAITING_RECOVERY / ABORTED / PLANNED). It carries the terminal-or-parked ``state``
    and a human ``reason`` -- and DELIBERATELY carries NO observed-value field. That
    absence is the commit-before-observation gate: the caller structurally cannot read an
    "observation" for an action that never committed."""

    action_id: str
    state: str
    reason: str
    attempt: int


@dataclass(frozen=True)
class DispatchRoundResult:
    """The round's outcome, partitioned by the commit gate: ``committed_results`` (with
    observed values) vs ``non_committed`` (without). Building an observation from anything
    but ``committed_results`` is structurally impossible -- the other list has no value."""

    committed_results: list[CommittedResult]
    non_committed: list[NonCommittedAction]

    @property
    def all_committed(self) -> bool:
        """True iff every action of the round reached COMMITTED (``non_committed`` empty)."""
        return not self.non_committed

    def committed_by_well(self) -> dict[str, CommittedResult]:
        """Destination-well -> its COMMITTED result. The mapping mcl indexes to attach an
        observed volume to a plate well (only committed wells appear)."""
        return {c.destination_well: c for c in self.committed_results}

    def committed_by_action(self) -> dict[str, CommittedResult]:
        """action_id -> its COMMITTED result."""
        return {c.action_id: c for c in self.committed_results}

    @classmethod
    def _from_records(cls, records: Iterable[ActionRecord]) -> DispatchRoundResult:
        """Partition resolved ledger records by the commit gate. Observed volume is read
        ONLY off a COMMITTED record; a non-committed record contributes no value field."""
        committed: list[CommittedResult] = []
        non_committed: list[NonCommittedAction] = []
        for rec in records:
            if rec.state is ActionState.COMMITTED:
                committed.append(CommittedResult(
                    action_id=rec.action_id,
                    destination_well=rec.destination_well,
                    source_well=rec.source_well,
                    observed_volume_ul=rec.observed_volume_ul,
                    requested_volume_ul=rec.requested_volume_ul,
                    sensed_evidence_id=rec.sensed_evidence_id,
                    attempt=rec.attempt,
                ))
            else:
                non_committed.append(NonCommittedAction(
                    action_id=rec.action_id,
                    state=rec.state.value,
                    reason=_non_commit_reason(rec.state),
                    attempt=rec.attempt,
                ))
        return cls(committed_results=committed, non_committed=non_committed)


_NON_COMMIT_REASON: dict[ActionState, str] = {
    ActionState.PLANNED: "planned_io_never_issued",
    ActionState.PENDING: "pending_no_conclusive_read_back",
    ActionState.AWAITING_RECOVERY: "awaiting_operator_recovery",
    ActionState.ROLLED_BACK: "rolled_back_no_commit",
    ActionState.ABORTED: "aborted_no_commit",
}


def _non_commit_reason(state: ActionState) -> str:
    return _NON_COMMIT_REASON.get(state, f"non_committed_{state.value.lower()}")


# --- construction helper (shared by every entry) ---------------------------------

def _driver(
    ledger: ActionLedger,
    sensed_backend: SensedState,
    clock: VirtualClock | None,
    max_polls: int,
) -> PhysicalDispatch:
    """Build the Phase 3 dispatcher over ``ledger`` + ``sensed_backend``. When ``clock``
    is supplied it is INJECTED into a backend that owns a virtual clock (dependency
    injection of virtual time -- never a wall clock), so the caller can observe logical
    ticks / a real backend can share a deterministic clock; when None the backend's own
    virtual clock governs (byte-identical to Phase 3)."""
    if clock is not None and hasattr(sensed_backend, "clock"):
        sensed_backend.clock = clock
    return PhysicalDispatch(ledger, sensed_backend, max_polls=max_polls)


# --- the round face (the mcl wiring point) ---------------------------------------

def dispatch_round(
    actions: Iterable[PlannedAction],
    sensed_backend: SensedState,
    ledger: ActionLedger,
    *,
    clock: VirtualClock | None = None,
    max_polls: int = 64,
) -> DispatchRoundResult:
    """Run one round's physical actions through ``dispatch -> I/O -> sense -> confirm`` and
    return the commit-partitioned :class:`DispatchRoundResult`.

    Pure orchestration: no adjudication logic lives here -- the ledger owns the state
    machine, the volume conservation, the recovery policy, and the append-only event log;
    the backend owns the sensed read-back. This function only sequences them and packages
    the outcome behind the commit-before-observation gate.

    Ordering (see module docstring): each action's ``-> PENDING`` event is appended and
    flushed BEFORE its hardware I/O, and every ``-> COMMITTED`` event is on the append-only
    log BEFORE the returned result is built (the result is derived from already-persisted
    records). A mismatch that parks an action in AWAITING_RECOVERY is NOT auto-retried
    here -- resolve it out of band via :func:`recover_action` / :func:`cancel_action`.
    """
    disp = _driver(ledger, sensed_backend, clock, max_polls)
    records = disp.run(list(actions))
    return DispatchRoundResult._from_records(records)


def resume_round(
    actions: Iterable[PlannedAction],
    sensed_backend: SensedState,
    ledger: ActionLedger,
    *,
    clock: VirtualClock | None = None,
    max_polls: int = 64,
) -> DispatchRoundResult:
    """Crash-resume face: replay ``ledger`` and continue ``actions`` via the Phase 3
    resume trichotomy (COMMITTED skip / PENDING re-sense, never re-dispatch / PLANNED
    safe re-dispatch), then return the commit-partitioned result for the whole round.

    Idempotent at round granularity: re-running over an already-resolved ledger re-sends
    NOTHING (the idempotency key guards duplicate dispatch; a COMMITTED action is skipped,
    not re-sensed) and yields the same ``committed_results``. This is the face mcl calls
    when a round is resumed mid wet-leg."""
    disp = _driver(ledger, sensed_backend, clock, max_polls)
    disp.resume(list(actions))
    records = [ledger.record(a.action_id) for a in actions]
    return DispatchRoundResult._from_records(records)


# --- the out-of-band recovery port -----------------------------------------------

def recover_action(
    action: PlannedAction,
    sensed_backend: SensedState,
    ledger: ActionLedger,
    *,
    clock: VirtualClock | None = None,
    max_polls: int = 64,
) -> ActionRecord:
    """Out-of-band operator recovery of an AWAITING_RECOVERY action (122 即裁二): bump the
    attempt (a DISTINCT auditable ``attempt++``) and re-sense. A fresh CONFIRMED commits;
    a fresh MISMATCH routes back to AWAITING_RECOVERY. Returns the resolved record -- the
    caller re-runs :func:`dispatch_round`-style partitioning if it needs a round view."""
    disp = _driver(ledger, sensed_backend, clock, max_polls)
    return disp.recover_one(action)


def cancel_action(
    action_id: str, ledger: ActionLedger, *, reason: str = "canceled"
) -> ActionRecord:
    """Out-of-band operator cancel of an AWAITING_RECOVERY action -> ABORTED (112 即裁二
    paused-cancel == abandon precedent). Nothing was committed, so nothing is physically
    reverted. Returns the terminal record."""
    return ledger.cancel(action_id, reason)
