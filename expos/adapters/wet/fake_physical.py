"""Fake physical wet backend (M23 Phase 3, A domain) -- seven behaviour modes + the
minimal dispatch face that runs a :class:`~expos.adapters.wet.action_ledger.PlannedAction`
sequence through the ledger's ``dispatch -> I/O -> sense -> confirm`` orchestration.

This is the real dispatch wiring point Phase 1's report foretold: Phase 1 wired sensed
evidence in EXPLICITLY through :meth:`ActionLedger.confirm`; Phase 3's fake backend is the
first concrete :class:`~expos.adapters.wet.action_ledger.SensedState` implementation that
PRODUCES that evidence from a simulated read-back, and the orchestrator drives it end to
end.

**"Simulation is the upper bound" is guaranteed by shared code, not trust** (INDEX_REF_F
F-1; pyvisa-sim ``SimVisaLibrary(VisaLibraryBase)`` + PLR ``opentrons_simulator`` -- two
independent industry precedents). The fake backend's ONLY divergence point from a real
backend is :meth:`FakePhysicalBackend.sense` -- "where does the read value come from"
(a simulated degradation model here, a real instrument socket later). Everything
downstream -- the ``FailureDetail`` four-branch classification, the six-state transaction
machine, the ``RecoveryPolicy`` adjudication, the volume ledger's conservation -- is the
SAME ``ActionLedger`` code both backends share, so a fake backend CANNOT produce a result
a real backend could not (it can only be as good or worse). The fake never reimplements
any adjudication; it only supplies evidence.

Determinism is virtual, never wall-clock (INDEX_REF_F F-4; renode integer-tick virtual
time). The ``slow`` / ``timeout`` modes advance a :class:`VirtualClock` LOGICAL tick
counter -- **there is no ``time.sleep``** -- so the differential gate stays reproducible.

Data/code split (INDEX_REF_F §Convergence(a), 122 即裁一): a scenario is DATA (a dict /
yaml: which plate, each well's initial volume, which well at which attempt triggers which
behaviour, the timeout tick budget); the DEGRADATION FUNCTIONS -- how each behaviour
degrades the read value into structured evidence -- are CODE (this module). Error
classification is NOT yaml-ised: the scenario only names a behaviour + params; the
``FailureDetail`` four branches, transaction states, and volume rejections are the
existing code path.

The seven user-钦定 behaviour modes (each binds a red line it exercises):

  1. exact-success        -- CONFIRMED, observed ~= requested; PENDING->COMMITTED once.
  2. sensed-mismatch      -- driver OK reply but read-back MISMATCH; OK never commits.
  3. partial-execution    -- k of n wells CONFIRMED, n-k MISMATCH; per-WELL sensed truth.
  4. timeout-before-confirm - UNOBSERVED until a LOGICAL timeout budget, then TRANSPORT
                              timeout; no silent retry, no real sleep.
  5. duplicate-reply      -- the same command echoed twice; the idempotency gate eats the
                              second (physical action executed exactly once).
  6. disconnect-resume    -- orchestration interrupted, resumed from the ledger; the
                              resume trichotomy (COMMITTED skip / PENDING re-sense /
                              PLANNED re-dispatch) each with a discriminative test.
  7. cancel-in-recovery   -- operator cancel while AWAITING_RECOVERY == ABORTED.

Plus the attempt++ recovery (122 即裁二): recover() bumps the attempt, re-sense; a fresh
failure routes back to AWAITING_RECOVERY.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .action_ledger import (
    ActionLedger,
    ActionRecord,
    ActionState,
    PlannedAction,
    SensedEvidence,
    SensedOutcome,
)

# The observation-channel schema the backend DECLARES it emits (the SensedEvidence face).
# The differential gate checks a real backend's declared channels are compatible with the
# sim's -- a real backend emitting a field the sim never declared is fail-closed.
OBS_CHANNEL_SCHEMA: dict[str, str] = {
    "evidence_id": "str",
    "for_attempt": "int",
    "outcome": "enum[CONFIRMED,MISMATCH,UNOBSERVED]",
    "observed_volume_ul": "float|null",
    "code": "str",
    "detail": "str",
}


# --- virtual clock (no wall-clock; renode integer-tick discipline, F-4) ---------

class VirtualClock:
    """A logical integer-tick counter. ``advance`` bumps the tick; there is NO
    ``time.sleep`` anywhere -- ``slow`` / ``timeout`` are pure logical-time events so the
    differential gate is bit-reproducible (INDEX_REF_F F-4)."""

    def __init__(self) -> None:
        self._tick = 0

    def now(self) -> int:
        return self._tick

    def advance(self, ticks: int = 1) -> int:
        self._tick += ticks
        return self._tick


# --- code-side degradation behaviours -------------------------------------------

class Behaviour(str, Enum):
    """The CODE-side degradation catalogue (how a read value is degraded into evidence).
    The scenario DATA only names one of these + params; the mapping to FailureDetail /
    transaction state / volume rejection is the shared ``ActionLedger`` code."""

    CONFIRM_EXACT = "confirm_exact"          # observed == requested (nominal)
    CONFIRM_WITHIN_TOL = "confirm_within_tol"  # observed = requested + small in-band delta
    CONFIRM_DRIFT = "confirm_drift"          # observed = requested*(1+drift); may exceed tol
    MISMATCH_DEFINED = "mismatch_defined"    # MISMATCH with a registered FAILURE_MODES code
    MISMATCH_UNDEFINED = "mismatch_undefined"  # MISMATCH with an unregistered code (fail-closed)
    TIMEOUT = "timeout"                      # UNOBSERVED until budget, then MISMATCH timeout
    UNOBSERVED = "unobserved"                # inconclusive read-back forever (stays PENDING)


@dataclass(frozen=True)
class BehaviourSpec:
    """One (attempt -> behaviour + params) row of a well's scenario. DATA-driven."""

    attempt: int
    behaviour: Behaviour
    code: str = ""
    delta_ul: float = 0.0          # signed absolute offset for CONFIRM_WITHIN_TOL
    drift_pct: float = 0.0         # percent offset for CONFIRM_DRIFT
    timeout_at_tick: int = 3       # TIMEOUT fires once clock.now() >= this


def _evidence(
    action: PlannedAction, attempt: int, spec: BehaviourSpec, clock: VirtualClock
) -> SensedEvidence:
    """Pure degradation function: (planned action, attempt, behaviour spec, virtual
    clock) -> sensed evidence, ALWAYS stamped ``for_attempt=attempt`` (observedGeneration
    discipline). This is the whole of the fake backend's divergence from a real backend."""
    eid = f"ev-{action.action_id}-a{attempt}-t{clock.now()}"
    req = action.requested_volume_ul
    b = spec.behaviour
    if b is Behaviour.CONFIRM_EXACT:
        return SensedEvidence(eid, attempt, SensedOutcome.CONFIRMED, req)
    if b is Behaviour.CONFIRM_WITHIN_TOL:
        return SensedEvidence(eid, attempt, SensedOutcome.CONFIRMED, req + spec.delta_ul)
    if b is Behaviour.CONFIRM_DRIFT:
        return SensedEvidence(
            eid, attempt, SensedOutcome.CONFIRMED, req * (1.0 + spec.drift_pct / 100.0))
    if b is Behaviour.MISMATCH_DEFINED:
        return SensedEvidence(eid, attempt, SensedOutcome.MISMATCH,
                              code=spec.code or "E_DEVICE",
                              detail="fake backend: sensed read-back contradicts request")
    if b is Behaviour.MISMATCH_UNDEFINED:
        return SensedEvidence(eid, attempt, SensedOutcome.MISMATCH,
                              code=spec.code or "E_GREMLIN_UNREGISTERED",
                              detail="fake backend: undefined failure (fail-closed)")
    if b is Behaviour.TIMEOUT:
        if clock.now() >= spec.timeout_at_tick:
            return SensedEvidence(eid, attempt, SensedOutcome.MISMATCH, code="timeout",
                                  detail="fake backend: logical timeout budget exhausted")
        return SensedEvidence(eid, attempt, SensedOutcome.UNOBSERVED,
                              detail="fake backend: no conclusive read-back yet")
    # UNOBSERVED
    return SensedEvidence(eid, attempt, SensedOutcome.UNOBSERVED,
                          detail="fake backend: inconclusive read-back")


# --- scenario (DATA) ------------------------------------------------------------

@dataclass
class Scenario:
    """A dict/yaml-shaped scenario: initial state + per-well/per-attempt behaviour
    triggers (DATA). No degradation logic lives here (that is the ``Behaviour`` code)."""

    name: str
    actions: list[dict[str, Any]]                       # PlannedAction field dicts
    behaviours: dict[str, list[BehaviourSpec]] = field(default_factory=dict)
    initial: dict[str, float] = field(default_factory=dict)
    source_capacity: dict[str, float] = field(default_factory=dict)
    default_behaviour: BehaviourSpec = field(
        default_factory=lambda: BehaviourSpec(attempt=1, behaviour=Behaviour.CONFIRM_EXACT))

    def planned(self) -> list[PlannedAction]:
        out = []
        for a in self.actions:
            out.append(PlannedAction(
                action_id=a["action_id"], round_id=a.get("round_id", 0),
                spec_fingerprint=a.get("spec_fingerprint", "spec-fake"),
                source_well=a.get("source", "RSV"), destination_well=a["dest"],
                requested_volume_ul=a["volume"], backend_id=a.get("backend_id", "fake-0"),
                expected_pre_state=a.get("pre_state", {}) or {},
                expected_post_state={}))
        return out

    def behaviour_for(self, dest_well: str, attempt: int) -> BehaviourSpec:
        for spec in self.behaviours.get(dest_well, ()):
            if spec.attempt == attempt:
                return spec
        return self.default_behaviour

    @classmethod
    def from_dict(cls, doc: dict[str, Any]) -> "Scenario":
        behaviours: dict[str, list[BehaviourSpec]] = {}
        for well, rows in (doc.get("behaviours") or {}).items():
            behaviours[well] = [
                BehaviourSpec(
                    attempt=r["attempt"], behaviour=Behaviour(r["behaviour"]),
                    code=r.get("code", ""), delta_ul=r.get("delta_ul", 0.0),
                    drift_pct=r.get("drift_pct", 0.0),
                    timeout_at_tick=r.get("timeout_at_tick", 3))
                for r in rows]
        return cls(
            name=doc.get("name", "scenario"), actions=list(doc["actions"]),
            behaviours=behaviours, initial=dict(doc.get("initial") or {}),
            source_capacity=dict(doc.get("source_capacity") or {}))


# --- the fake backend (the ONLY SensedState implementation in Phase 3) ----------

class FakePhysicalBackend:
    """Implements the :class:`SensedState` protocol -- the narrow read-back verb. Given a
    planned action + attempt, it produces the sensed evidence gating PENDING->COMMITTED,
    driven by the scenario's DATA and the code-side degradation functions. It owns a
    :class:`VirtualClock` (advanced on every ``sense`` -- no wall clock).

    It declares its observation-channel schema (:data:`OBS_CHANNEL_SCHEMA`) for the
    differential gate's schema-compatibility facet."""

    declared_obs_schema: dict[str, str] = dict(OBS_CHANNEL_SCHEMA)

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.clock = VirtualClock()
        #: audit of every read-back this backend produced (for tests / provenance).
        self.sensed_log: list[SensedEvidence] = []

    def sense(self, action: PlannedAction, *, attempt: int) -> SensedEvidence:
        self.clock.advance()
        spec = self.scenario.behaviour_for(action.destination_well, attempt)
        ev = _evidence(action, attempt, spec, self.clock)
        self.sensed_log.append(ev)
        return ev


# --- the minimal dispatch face (the orchestration / wiring point) ---------------

class ResumeDisposition(str, Enum):
    """The resume trichotomy verdict per action (122 即裁三)."""

    SKIP_COMMITTED = "skip_committed"      # terminal success: nothing to do
    RESENSE_PENDING = "resense_pending"    # in-flight: re-READ, never re-dispatch
    REDISPATCH_PLANNED = "redispatch_planned"  # I/O never issued: safe to (re)dispatch
    SKIP_TERMINAL = "skip_terminal"        # ROLLED_BACK / ABORTED: done, no action
    AWAIT_OPERATOR = "await_operator"      # AWAITING_RECOVERY: out-of-band, no auto action


class PhysicalDispatch:
    """The minimal dispatch face: runs a PlannedAction sequence through the ledger's
    ``dispatch -> I/O -> sense -> confirm`` orchestration, consuming a
    :class:`FakePhysicalBackend`.

    The driver's ``io_call`` reply is recorded but NEVER treated as physical truth (it
    always returns OK here); only the sensed read-back gates COMMITTED -- so a driver OK
    reply alone can never commit (the sensed-mismatch red line). There is NO silent retry
    and NO real sleep (timeout is a logical-tick event)."""

    def __init__(
        self, ledger: ActionLedger, backend: FakePhysicalBackend, *, max_polls: int = 64
    ) -> None:
        self.ledger = ledger
        self.backend = backend
        self.max_polls = max_polls

    # -- one action, dispatch through resolution ------------------------------

    def _driver_reply(self) -> bool:
        # The fake driver ACKs the send (OK). This is deliberately NOT the physical
        # truth -- the sensed read-back is. Exercises "driver OK reply != physical truth".
        return True

    def _sense_to_resolution(self, planned: PlannedAction) -> ActionRecord:
        """Poll the sensed read-back (advancing the virtual clock) until the action leaves
        PENDING or the logical poll budget is exhausted. An action that is never
        conclusively observed stays PENDING (no silent commit, no silent abort)."""
        rec = self.ledger.record(planned.action_id)
        for _ in range(self.max_polls):
            if rec.state is not ActionState.PENDING:
                return rec
            ev = self.backend.sense(planned, attempt=rec.attempt)
            rec = self.ledger.confirm(planned.action_id, ev)
        return rec

    def dispatch_one(self, planned: PlannedAction) -> ActionRecord:
        self.ledger.dispatch(planned, self._driver_reply)
        return self._sense_to_resolution(planned)

    def run(self, planned_actions: list[PlannedAction]) -> list[ActionRecord]:
        """Drive a whole sequence (partial-execution: each well resolves independently by
        its own sensed read-back -- per-WELL truth, not a blanket driver reply)."""
        return [self.dispatch_one(p) for p in planned_actions]

    # -- operator recovery (attempt++, 122 即裁二) ----------------------------

    def recover_one(self, planned: PlannedAction) -> ActionRecord:
        """Operator recovery: bump the attempt (AWAITING_RECOVERY -> PENDING) and re-sense.
        A fresh CONFIRMED commits; a fresh MISMATCH routes back to AWAITING_RECOVERY."""
        self.ledger.recover(planned.action_id)
        return self._sense_to_resolution(planned)

    # -- resume trichotomy (122 即裁三) ---------------------------------------

    @staticmethod
    def classify_resume(rec: ActionRecord) -> ResumeDisposition:
        """Pure classification of a replayed record's resume disposition."""
        if rec.state is ActionState.COMMITTED:
            return ResumeDisposition.SKIP_COMMITTED
        if rec.state is ActionState.PENDING:
            return ResumeDisposition.RESENSE_PENDING
        if rec.state is ActionState.PLANNED:
            return ResumeDisposition.REDISPATCH_PLANNED
        if rec.state is ActionState.AWAITING_RECOVERY:
            return ResumeDisposition.AWAIT_OPERATOR
        return ResumeDisposition.SKIP_TERMINAL

    def resume(
        self, planned_actions: list[PlannedAction]
    ) -> dict[str, ResumeDisposition]:
        """Resume from the replayed ledger, applying the trichotomy per action:

          * COMMITTED  -> SKIP (already done; NOT re-sensed, NOT re-dispatched).
          * PENDING    -> RE-SENSE (re-read to re-determine; the physical action was
            already issued, so it is NEVER re-dispatched -- idempotency also guarantees
            this, but the intent is to LEARN the true outcome via a fresh read-back).
          * PLANNED    -> RE-DISPATCH (I/O provably never issued; complete it safely).
          * AWAITING_RECOVERY -> AWAIT_OPERATOR (out of band; recover/abandon/cancel).
          * ROLLED_BACK / ABORTED -> SKIP (terminal).
        """
        verdicts: dict[str, ResumeDisposition] = {}
        for planned in planned_actions:
            try:
                rec = self.ledger.record(planned.action_id)
            except Exception:
                # never dispatched at all -> a fresh dispatch (not a resume of a known one)
                self.dispatch_one(planned)
                verdicts[planned.action_id] = ResumeDisposition.REDISPATCH_PLANNED
                continue
            verdict = self.classify_resume(rec)
            verdicts[planned.action_id] = verdict
            if verdict is ResumeDisposition.RESENSE_PENDING:
                self._sense_to_resolution(planned)
            elif verdict is ResumeDisposition.REDISPATCH_PLANNED:
                self.ledger.continue_planned(planned.action_id, self._driver_reply)
                self._sense_to_resolution(planned)
            # SKIP_COMMITTED / SKIP_TERMINAL / AWAIT_OPERATOR: no automatic action.
        return verdicts


def load_scenario(path: str | Path) -> Scenario:
    """Load a scenario from a JSON file (yaml-shaped dict). DATA-driven per 122 即裁一."""
    import json
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return Scenario.from_dict(doc)
