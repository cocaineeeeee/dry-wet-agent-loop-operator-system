"""Physical-action transaction facet + runtime volume ledger (M23 Phase 1+2).

The real-wet readiness contract: every *physical action* (a liquid transfer from a
source well to a destination well) must be **recoverable, re-readable, committable,
and non-replayable** before any real instrument is wired in. This module is that
semantic ground layer -- a narrow transaction facet that lives BENEATH the
:class:`~expos.adapters.wet.driver.WetDriver` seven-state lifecycle (which owns a
*goal* = "read this validated plate"); it does not replace it. The driver's state
machine stays exactly as it is; this is the finer transaction grain for the
individual transfers a real backend will one day dispatch.

Action transaction state machine (钦定六态)::

    PLANNED --dispatch--> PENDING --sensed-confirm--> COMMITTED
                             |  \\--sensed-mismatch--> ROLLED_BACK   (policy ABORT / fail-closed)
                             |   \\-------------------> AWAITING_RECOVERY (policy AWAIT_HUMAN)
                             |                              |  \\--recover(sensed-confirm)--> COMMITTED
                             |                              |   \\--abandon----------------> ROLLED_BACK
                             |                              \\----cancel-------------------> ABORTED

Red lines this facet enforces (each pinned by a discriminative test in
tests/test_realwet_transactions.py):

  * ``dispatch`` persists **PENDING BEFORE the hardware I/O** (crash-visible pending).
  * a driver OK reply **never alone** produces COMMITTED -- COMMITTED is gated ONLY by
    a sensed-state confirmation (INDEX_M21 §4; mailbox 112 即裁三 well-level saga
    pending->committed gate == sensed-state read-back). Without confirmation the
    action stays PENDING.
  * a sensed mismatch is adjudicated by the SAME pluggable
    :class:`~expos.adapters.wet.recovery.RecoveryPolicy` the driver uses -> either
    AWAITING_RECOVERY (AWAIT_HUMAN) or ROLLED_BACK (ABORT / undefined fail-closed).
  * a cancel arriving in AWAITING_RECOVERY reuses the driver's paused-cancel ==
    abandon precedent (112 即裁二), terminating the action ABORTED.
  * the ledger is a run-dir append-only jsonl with a per-line **hash chain**; a
    resumed ledger replays it and **loudly refuses to re-dispatch** an action whose
    ``action_id`` (the idempotency key) is already recorded -- no silent re-send.

Runtime volume ledger (Phase 2): a deterministic file-state (no DB) per-well volume
tracker with FIVE pre-dispatch rejections (负体积 / 超源井余量 / 目标井溢出 /
同幂等键重复转移 / expected-pre-state 失配). Destination well capacity comes from the
external labware definition (plate96.json). After a sensed confirmation the
**observed** volume is committed and stored in a field DISTINCT from the original
**requested** volume -- the request is never overwritten (write-strict, no hidden
truth into the record).

COMMITTED is deliberately STRICTER than the target device engine's own success
semantics: Opentrons marks a command SUCCEEDED when ``execute()`` returns without
raising -- it trusts the command's self-reported ``state_update`` with NO independent
read-back (INDEX_M23_OTENGINE §2). OT's SUCCEEDED only reaches this facet's PENDING;
COMMITTED adds the sensed-state seal OT does not have. That extra gate is the whole
point of the real-wet contract, not an accident.

Facet invariants (INDEX_M23_OTENGINE / REF-T P1-P6; each with its acceptance anchor):

  * **P1 state closure**   -- every transition is in :data:`_LEGAL_TRANSITIONS`; any
    other edge is rejected loudly (``test_illegal_transition_rejected_loudly``).
  * **P2 mutual exclusion** -- an action is never both COMMITTED and ROLLED_BACK/ABORTED
    (single ``state`` field + terminal states have no out-edges; the state machine makes
    ¬(COMMITTED∧VOIDED) structural -- ``test_committed_is_terminal``).
  * **P3 pending -> unique terminal** -- a PENDING action resolves to EXACTLY one of
    COMMITTED / ROLLED_BACK / ABORTED (the commit / rollback / cancel tests).
  * **P4 observed after the gate** -- ``observed_volume_ul`` is written ONLY on the
    COMMITTED path (never at dispatch/intent) -- ``test_ok_reply_never_commits_alone`` +
    ``test_requested_and_observed_stored_separately``.
  * **P5 conservation**    -- a transfer's double-entry legs sum to zero (source -v,
    dest +v); the running balance is conserved (``test_volume_ledger_conserves``).
  * **P6 append-only monotonic** -- the ledger is a hash-chained, seq-monotonic,
    append-only log; a rewritten/truncated history is detected
    (``test_ledger_is_append_only_tamper_detected`` + ``test_truncated_ledger_refuses_resume``).

The event face: every state transition is an append-only event of kind
``physical_action_transition`` carrying the full action record snapshot + from/to
state. This module does NOT touch the kernel store registry (B domain owns event-kind
registration); the payload shape is documented for B to register.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from .labware import Labware, load_labware
from .recovery import (
    FailureDetail,
    NeverRecover,
    RecoveryAction,
    RecoveryPolicy,
)

_LEDGER_FILE = "action_ledger.jsonl"
_EVENT_KIND = "physical_action_transition"


# --- state machine -------------------------------------------------------------

class ActionState(str, Enum):
    PLANNED = "PLANNED"
    PENDING = "PENDING"
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"
    AWAITING_RECOVERY = "AWAITING_RECOVERY"
    ABORTED = "ABORTED"


#: Terminal states -- no further transition is legal.
_TERMINAL_STATES: frozenset[ActionState] = frozenset(
    {ActionState.COMMITTED, ActionState.ROLLED_BACK, ActionState.ABORTED}
)

#: Explicit legal transition table -- any edge NOT listed is rejected loudly by
#: :meth:`ActionLedger._transition` (no silent state corruption; mirrors the
#: driver's seven-state table discipline).
_LEGAL_TRANSITIONS: dict[ActionState, frozenset[ActionState]] = {
    ActionState.PLANNED: frozenset({ActionState.PENDING}),
    ActionState.PENDING: frozenset({
        ActionState.COMMITTED,
        ActionState.ROLLED_BACK,
        ActionState.AWAITING_RECOVERY,
    }),
    ActionState.AWAITING_RECOVERY: frozenset({
        ActionState.PENDING,        # recover(attempt=n+1): re-dispatch a distinct
                                    # auditable attempt instance (122 即裁二); the new
                                    # attempt then re-senses -> COMMITTED, or fails back
                                    # to AWAITING_RECOVERY (the existing PENDING edge).
        ActionState.COMMITTED,      # recover-in-place: human fixed it, re-sensed confirm
                                    # on the SAME attempt (weaker audit; kept legal).
        ActionState.ROLLED_BACK,    # abandon(): give up cleanly
        ActionState.ABORTED,        # cancel while paused (112 即裁二)
    }),
    ActionState.COMMITTED: frozenset(),
    ActionState.ROLLED_BACK: frozenset(),
    ActionState.ABORTED: frozenset(),
}


# --- errors --------------------------------------------------------------------

class ActionLedgerError(Exception):
    """Base for every loud refusal in this facet (never silent)."""


class ActionPrecheckError(ActionLedgerError):
    """A pre-dispatch rejection (one of the five). ``code`` is the machine-readable
    discriminator so callers / tests gate on the exact reason."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")


class IdempotencyError(ActionPrecheckError):
    """Idempotency-key reuse with DIFFERENT parameters (INDEX_M23_OTENGINE §5 / REF-T
    point 2, Stripe semantics): the same deterministic key (round_id, exp_id, well_idx)
    arrived carrying a divergent ``params_fingerprint``. Same key + same fingerprint is
    an idempotent replay (return the existing result, re-send nothing); same key +
    different fingerprint is a real bug and is refused loudly."""

    def __init__(self, message: str) -> None:
        super().__init__("idempotency_key_reuse", message)


class ActionStateError(ActionLedgerError):
    """An illegal transition, or an operation on an action in the wrong state."""


class LedgerIntegrityError(ActionLedgerError):
    """Base for "the on-disk ledger cannot be trusted" -- resume/dispatch fail closed
    rather than optimistically rebuilding an empty ledger (INDEX_M23_OTENGINE §7,
    Opentrons ``BadStateSummary``: a persisted state read-back may be corrupt; do not
    assume it is intact). A corrupt physical-action ledger demands human attention."""


class LedgerTamperError(LedgerIntegrityError):
    """The append-only hash chain does not verify -- a historical line was rewritten,
    reordered, or deleted. Loud by construction (append-only evidence)."""


class LedgerCorruptError(LedgerIntegrityError):
    """A ledger line is unparseable / truncated (e.g. a crash mid-write left half a
    line). Unlike the kernel event store, this facet does NOT tolerate a torn tail: a
    physical-action ledger of unknown integrity is treated as untrusted and refuses to
    resume, requiring manual reconciliation (INDEX_M23_OTENGINE §7 fail-closed)."""


# --- sensed-state seam (Phase 3 reuse) -----------------------------------------

class SensedOutcome(str, Enum):
    """Three-state observation bit (INDEX_M23_RECONCILE §1.4 / point 4; k8s
    Condition.Status True/False/**Unknown**). ``UNOBSERVED`` (Unknown) MUST be
    distinct from ``MISMATCH`` (observed failure): "not yet observed" is not
    "observed to have failed" -- conflating them would let a not-yet-read action
    wrongly trip the RecoveryPolicy."""

    CONFIRMED = "CONFIRMED"      # observed True: the action physically happened
    MISMATCH = "MISMATCH"        # observed False: read-back contradicts the request
    UNOBSERVED = "UNOBSERVED"    # Unknown: no conclusive read-back (NOT a failure)


@dataclass(frozen=True)
class SensedEvidence:
    """The result of "ask the machine what actually happened" (INDEX_M21 §4), the
    PENDING->COMMITTED gate.

    ``for_attempt`` is the observedGeneration stamp (INDEX_M23_RECONCILE §1.3 / point
    1): which attempt this read-back observes. On a real instrument a read taken right
    after action *A* may return the buffered residue of the previous action *A'*; the
    stamp lets the commit gate reject a stale read (``for_attempt`` != the action's
    current attempt) instead of mistaking *A'*'s old success for *A*'s COMMITTED -- the
    hardest-to-reproduce real-wet race, designed in now while it is free.

      * ``CONFIRMED``  -- ``observed_volume_ul`` is the actually-delivered volume (may
        differ from the request; stored separately, never overwriting it).
      * ``MISMATCH``   -- ``code`` classifies the failure for the RecoveryPolicy (a
        :data:`~expos.adapters.wet.recovery.FAILURE_MODES` code, or undefined ->
        fail-closed).
      * ``UNOBSERVED`` -- inconclusive; the action stays PENDING (a driver OK reply is
        not, on its own, a confirmation).
    """

    evidence_id: str
    for_attempt: int
    outcome: SensedOutcome = SensedOutcome.UNOBSERVED
    observed_volume_ul: float | None = None
    code: str = ""
    detail: str = ""


@runtime_checkable
class SensedState(Protocol):
    """The narrow read-back verb a real/fake physical backend implements (Phase 3).

    Given the planned action and the attempt number, return the sensed evidence that
    gates PENDING->COMMITTED -- the implementation MUST stamp ``for_attempt=attempt``
    (observedGeneration discipline). Phase 1 wires evidence explicitly through
    :meth:`ActionLedger.confirm`; Phase 3's fake physical backend implements this
    protocol to PRODUCE that evidence from a simulated read-back / receipt.
    """

    def sense(self, action: "PlannedAction", *, attempt: int) -> SensedEvidence:
        ...


# --- action records ------------------------------------------------------------

@dataclass(frozen=True)
class PlannedAction:
    """The immutable plan for one physical action (a single liquid transfer).

    Carries the 钦定 identity + intent fields. ``expected_pre_state`` /
    ``expected_post_state`` are ``{well_id: volume_ul}`` snapshots the planner assumed
    -- the optimistic-concurrency check: if the ledger's current state disagrees with
    ``expected_pre_state`` the dispatch is rejected (a stale / reordered plan)."""

    action_id: str
    round_id: int
    spec_fingerprint: str
    source_well: str
    destination_well: str
    requested_volume_ul: float
    backend_id: str
    expected_pre_state: dict[str, float] = field(default_factory=dict)
    expected_post_state: dict[str, float] = field(default_factory=dict)


@dataclass
class ActionRecord:
    """The mutable ledger row -- the 钦定 全字段单. ``requested`` and ``observed`` are
    kept in DISTINCT fields (the request is never overwritten by the read-back)."""

    action_id: str
    round_id: int
    spec_fingerprint: str
    source_well: str
    destination_well: str
    requested_volume_ul: float
    backend_id: str
    expected_pre_state: dict[str, float]
    expected_post_state: dict[str, float]
    state: ActionState
    #: full-intent-parameter fingerprint riding alongside the idempotency key
    #: (``action_id``): a same-key replay with a DIFFERENT fingerprint is refused
    #: loudly (REF-T point 2 / Stripe). Attempt-independent -- retries reuse the key.
    params_fingerprint: str = ""
    attempt: int = 0
    sensed_evidence_id: str | None = None
    # observed_* is the k8s-status analogue (INDEX_M23_RECONCILE §1.2 / point 3):
    # written ONLY by the sensed read-back path (:meth:`ActionLedger.confirm`); the
    # dispatch/intent path treats it read-only and NEVER overwrites the request.
    observed_volume_ul: float | None = None
    disposition: str | None = None            # final disposition string

    def snapshot(self) -> dict[str, Any]:
        """Full record snapshot -- the ``physical_action_transition`` event payload
        (B registers this shape). Every transition carries the whole record so the
        ledger can be reconstructed from the last event per action_id alone."""
        return {
            "action_id": self.action_id,
            "round_id": self.round_id,
            "spec_fingerprint": self.spec_fingerprint,
            "source_well": self.source_well,
            "destination_well": self.destination_well,
            "requested_volume_ul": self.requested_volume_ul,
            "backend_id": self.backend_id,
            "expected_pre_state": dict(self.expected_pre_state),
            "expected_post_state": dict(self.expected_post_state),
            "state": self.state.value,
            "params_fingerprint": self.params_fingerprint,
            "attempt": self.attempt,
            "sensed_evidence_id": self.sensed_evidence_id,
            "observed_volume_ul": self.observed_volume_ul,
            "disposition": self.disposition,
        }

    @classmethod
    def from_snapshot(cls, snap: dict[str, Any]) -> "ActionRecord":
        return cls(
            action_id=snap["action_id"],
            round_id=snap["round_id"],
            spec_fingerprint=snap["spec_fingerprint"],
            source_well=snap["source_well"],
            destination_well=snap["destination_well"],
            requested_volume_ul=snap["requested_volume_ul"],
            backend_id=snap["backend_id"],
            expected_pre_state=dict(snap.get("expected_pre_state") or {}),
            expected_post_state=dict(snap.get("expected_post_state") or {}),
            state=ActionState(snap["state"]),
            params_fingerprint=snap.get("params_fingerprint", ""),
            attempt=snap.get("attempt", 0),
            sensed_evidence_id=snap.get("sensed_evidence_id"),
            observed_volume_ul=snap.get("observed_volume_ul"),
            disposition=snap.get("disposition"),
        )


# --- runtime volume ledger (Phase 2) -------------------------------------------

class VolumeLedger:
    """Runtime per-well volume tracker in a **double-entry (slimmed) form** (REF-T
    point 1; the protection layer PLR's VolumeTracker suggests, INDEX_M21 §5).

    A liquid transfer is a *balanced transaction*: the two legs (source -v, dest +v)
    sum to zero -- a conservation self-check (P5). Balances are DERIVED by folding an
    append-only in-memory entry stream (``_entries``); the entries also persist as the
    balanced legs riding on the :class:`ActionLedger`'s hash-chained COMMITTED events
    (source_well, destination_well, observed_volume_ul), so the ledger stays the single
    append-only source of truth -- no line is ever rewritten. Evaporation / dead-volume
    are :meth:`record_loss` legs (explicit, NEVER silent); a well void is a
    :meth:`void` compensating entry (reverses the accounting effect, not the physical
    liquid).

    Destination capacities default to the external labware geometry (plate96.json
    ``totalLiquidVolume``); source wells (reservoirs, off-plate) are seeded via
    ``initial`` / ``capacities`` overrides."""

    _EPS = 1e-6
    #: sentinel accounting well for explicit, never-silent losses (evaporation / dead
    #: volume) -- a loss leg debits the source and credits this loss account so the
    #: transaction still balances (conservation holds even for losses).
    LOSS_ACCOUNT = "__loss__"

    def __init__(
        self,
        capacities: dict[str, float] | None = None,
        initial: dict[str, float] | None = None,
        *,
        plate: Labware | None = None,
    ) -> None:
        lw = plate or load_labware()
        caps: dict[str, float] = {w: lw.capacity_of(w) for w in lw.all_wells()}
        if capacities:
            caps.update(capacities)
        self._capacity = caps
        self._volume: dict[str, float] = dict(initial or {})
        #: append-only double-entry leg stream (derived-balance / conservation source).
        self._entries: list[dict[str, Any]] = []

    def current(self, well_id: str) -> float:
        return self._volume.get(well_id, 0.0)

    def capacity(self, well_id: str) -> float:
        if well_id not in self._capacity:
            raise ActionPrecheckError(
                "unknown_well",
                f"destination well {well_id!r} has no declared capacity "
                "(not in labware, not seeded)",
            )
        return self._capacity[well_id]

    def _post(self, legs: list[tuple[str, float]], *, kind: str, ref: str) -> None:
        """Post a balanced set of legs (they MUST sum to zero -- conservation, P5)."""
        total = sum(delta for _, delta in legs)
        if abs(total) > self._EPS:
            raise ActionLedgerError(
                f"volume conservation violated: legs for {ref!r} sum to {total} != 0"
            )
        for well, delta in legs:
            self._volume[well] = self.current(well) + delta
            self._entries.append({"kind": kind, "well": well, "delta": delta,
                                  "ref": ref})

    def apply(self, source_well: str, destination_well: str, volume_ul: float) -> None:
        """Commit a transfer as a balanced double-entry (source -v, dest +v)."""
        self._post([(source_well, -volume_ul), (destination_well, volume_ul)],
                   kind="transfer", ref=f"{source_well}->{destination_well}")

    def record_loss(self, source_well: str, volume_ul: float, *, reason: str) -> None:
        """Explicit loss leg (evaporation / dead volume) -- NEVER silent (REF-T point
        1). Debits the source, credits the loss account, so the entry still balances."""
        self._post([(source_well, -volume_ul), (self.LOSS_ACCOUNT, volume_ul)],
                   kind="loss", ref=f"loss:{reason}:{source_well}")

    def void(self, source_well: str, destination_well: str, volume_ul: float,
             *, ref: str) -> None:
        """Compensating entry that reverses a committed transfer's ACCOUNTING effect
        (not the physical liquid) -- the ledger's finalizer-style clean-up (never a
        deletion / rewrite of the original entry)."""
        self._post([(destination_well, -volume_ul), (source_well, volume_ul)],
                   kind="void", ref=f"void:{ref}")

    def net_moved(self) -> float:
        """Sum of ALL leg deltas -- must be 0 for a conserved ledger (P5 witness)."""
        return sum(e["delta"] for e in self._entries)


# --- the ledger orchestrator ---------------------------------------------------

class ActionLedger:
    """Append-only, resume-safe physical-action transaction ledger.

    One ledger owns one run directory's ``action_ledger.jsonl``. Construct it, then
    :meth:`dispatch` (phase 1: precheck + persist PENDING before I/O) and
    :meth:`confirm` (phase 2: the sensed-state gate to COMMITTED / ROLLED_BACK /
    AWAITING_RECOVERY). A fresh ledger over an existing directory :meth:`_replay`\\ s
    the log (verifying the hash chain) so it refuses to re-dispatch known actions."""

    def __init__(
        self,
        run_dir: str | Path,
        *,
        volume: VolumeLedger | None = None,
        policy: RecoveryPolicy | None = None,
        plate: Labware | None = None,
    ) -> None:
        self._dir = Path(run_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / _LEDGER_FILE
        self._volume = volume if volume is not None else VolumeLedger(plate=plate)
        self._policy: RecoveryPolicy = policy or NeverRecover()
        self._records: dict[str, ActionRecord] = {}
        #: in-memory mirror of the appended event records (host runtime may persist).
        self.events: list[dict[str, Any]] = []
        self._seq = 0
        self._last_sha = ""
        if self._path.exists():
            self._replay()

    @property
    def policy(self) -> RecoveryPolicy:
        return self._policy

    @property
    def volume(self) -> VolumeLedger:
        return self._volume

    def record(self, action_id: str) -> ActionRecord:
        rec = self._records.get(action_id)
        if rec is None:
            raise ActionStateError(f"unknown action_id {action_id!r}")
        return rec

    def records(self) -> list[ActionRecord]:
        return list(self._records.values())

    # -- hash-chained append-only persistence ---------------------------------

    @staticmethod
    def _canonical(body: dict[str, Any]) -> str:
        return json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)

    @classmethod
    def _sha(cls, body: dict[str, Any]) -> str:
        return hashlib.sha256(cls._canonical(body).encode("utf-8")).hexdigest()

    def _append(self, kind_payload: dict[str, Any]) -> dict[str, Any]:
        """Append one hash-chained event line (append-only). ``line_sha`` covers every
        field except itself; ``prev_sha`` links to the previous line -- so rewriting,
        reordering, deleting, or truncating a historical line breaks verification."""
        body = {"seq": self._seq, "prev_sha": self._last_sha,
                "ts": round(time.time(), 6), "kind": _EVENT_KIND, **kind_payload}
        line_sha = self._sha(body)
        record = {**body, "line_sha": line_sha}
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            f.flush()
        self._seq += 1
        self._last_sha = line_sha
        self.events.append(record)
        return record

    def verify(self) -> None:
        """Walk the on-disk ledger and verify the hash chain; raise
        :class:`LedgerTamperError` on the first broken link. A historical line whose
        content was altered no longer hashes to its stored ``line_sha`` (and breaks
        every subsequent ``prev_sha``); a deleted/reordered line breaks ``seq`` /
        ``prev_sha`` continuity."""
        if not self._path.exists():
            return
        prev = ""
        expect_seq = 0
        with self._path.open("r", encoding="utf-8") as f:
            for lineno, raw in enumerate(f, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError as exc:
                    # NO torn-tail tolerance here (unlike the kernel store): an
                    # unparseable/truncated physical-action ledger is untrusted and
                    # fails closed -- refuse to resume, require manual reconciliation.
                    raise LedgerCorruptError(
                        f"line {lineno}: unparseable/truncated ({exc}) -- physical-action "
                        "ledger integrity unknown, refusing to resume (INDEX_M23_OTENGINE "
                        "fail-closed)"
                    ) from exc
                stored = rec.pop("line_sha", None)
                if stored is None:
                    raise LedgerTamperError(f"line {lineno}: missing line_sha")
                recomputed = self._sha(rec)
                if recomputed != stored:
                    raise LedgerTamperError(
                        f"line {lineno} (seq {rec.get('seq')}): content hash mismatch "
                        f"-- historical line was rewritten (append-only violated)"
                    )
                if rec.get("prev_sha", "") != prev:
                    raise LedgerTamperError(
                        f"line {lineno}: prev_sha chain broken "
                        "(line deleted/reordered/inserted)"
                    )
                if rec.get("seq") != expect_seq:
                    raise LedgerTamperError(
                        f"line {lineno}: seq {rec.get('seq')} != expected {expect_seq}"
                    )
                prev = stored
                expect_seq += 1

    def _replay(self) -> None:
        """Rebuild in-memory state from the (verified) log on resume. The latest event
        per ``action_id`` is the authoritative current state (each event is a full
        snapshot); committed observed volumes are re-applied to the volume ledger so
        continued prechecks see the true post-commit world."""
        self.verify()
        with self._path.open("r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                rec = json.loads(raw)
                self._seq = rec["seq"] + 1
                self._last_sha = rec["line_sha"]
                snap = {k: rec[k] for k in rec
                        if k not in ("seq", "prev_sha", "ts", "kind", "line_sha",
                                     "from", "to")}
                self._records[snap["action_id"]] = ActionRecord.from_snapshot(snap)
        for rec in self._records.values():
            if rec.state is ActionState.COMMITTED and rec.observed_volume_ul is not None:
                self._volume.apply(
                    rec.source_well, rec.destination_well, rec.observed_volume_ul)

    # -- transitions ----------------------------------------------------------

    def _transition(
        self, rec: ActionRecord, new: ActionState, *, disposition: str | None = None
    ) -> None:
        allowed = _LEGAL_TRANSITIONS.get(rec.state, frozenset())
        if new not in allowed:
            raise ActionStateError(
                f"illegal action transition {rec.state.value} -> {new.value} "
                f"for action {rec.action_id!r} (allowed: "
                f"{sorted(s.value for s in allowed)})"
            )
        old = rec.state
        rec.state = new
        if disposition is not None:
            rec.disposition = disposition
        if new in _TERMINAL_STATES and rec.disposition is None:
            rec.disposition = new.value.lower()
        snap = rec.snapshot()
        snap["from"] = old.value
        snap["to"] = new.value
        self._append(snap)

    # -- phase 1: dispatch (precheck + persist PENDING before I/O) -------------

    @staticmethod
    def params_fingerprint(action: PlannedAction) -> str:
        """The full-intent-parameter fingerprint (REF-T point 2): hashes every
        parameter that defines what the action DOES -- deliberately including the
        destination well and volume, whose omission is the acknowledged Opentrons
        idempotency-key bug (INDEX_M23_OTENGINE §5: OT hashes only commandType, so
        "measure A1" and "measure B2" collide). Attempt-INDEPENDENT (a retry of the
        same intent keeps the same fingerprint)."""
        return ActionLedger._sha({
            "round_id": action.round_id,
            "spec_fingerprint": action.spec_fingerprint,
            "source_well": action.source_well,
            "destination_well": action.destination_well,
            "requested_volume_ul": action.requested_volume_ul,
        })

    @staticmethod
    def derive_action_id(round_id: int, exp_id: str, well_idx: int) -> str:
        """Deterministically derive the idempotency KEY from (round_id, exp_id,
        well_idx) -- resume MUST reproduce the identical key, so it excludes any random
        uuid / wall-clock component (REF-T point 2). Retries reuse this key throughout.

        NOTE (reserved): calibration / deck-setup actions, when they exist, should be
        DELIBERATELY excluded from idempotency tracking (each run legitimately redoes
        them) -- they will get a per-run-unique key, not this deterministic one."""
        return f"act-r{round_id}-{exp_id}-w{well_idx}"

    def _precheck(self, action: PlannedAction) -> None:
        """The FOUR volume-boundary pre-dispatch rejections (the fifth -- idempotency
        key reuse -- is handled in :meth:`dispatch` because it branches skip-vs-reject
        rather than always raising). Each raises :class:`ActionPrecheckError` with a
        distinct ``code``."""
        # (1) 负体积
        if action.requested_volume_ul < 0:
            raise ActionPrecheckError(
                "negative_volume",
                f"requested volume {action.requested_volume_ul} uL is negative",
            )
        # (5) expected pre-state 与台账记录不符 (optimistic-concurrency guard)
        for well_id, expected in action.expected_pre_state.items():
            actual = self._volume.current(well_id)
            if abs(actual - expected) > VolumeLedger._EPS:
                raise ActionPrecheckError(
                    "pre_state_mismatch",
                    f"expected pre-state for well {well_id!r} = {expected} uL but the "
                    f"ledger holds {actual} uL (stale/reordered plan)",
                )
        # (2) 超源井余量
        remaining = self._volume.current(action.source_well)
        if action.requested_volume_ul > remaining + VolumeLedger._EPS:
            raise ActionPrecheckError(
                "source_insufficient",
                f"source well {action.source_well!r} holds {remaining} uL but "
                f"{action.requested_volume_ul} uL was requested",
            )
        # (3) 目标井溢出 (capacity from the external labware definition)
        cap = self._volume.capacity(action.destination_well)
        projected = self._volume.current(action.destination_well) + action.requested_volume_ul
        if projected > cap + VolumeLedger._EPS:
            raise ActionPrecheckError(
                "destination_overflow",
                f"destination well {action.destination_well!r} would hold {projected} uL "
                f"> capacity {cap} uL",
            )

    def dispatch(
        self, action: PlannedAction, io_call: Callable[[], bool] | None = None
    ) -> ActionRecord:
        """Phase 1: run the five prechecks, register PLANNED, **persist PENDING BEFORE**
        running ``io_call`` (the hardware I/O), then leave the action PENDING.

        NEVER commits: a driver OK reply is recorded on the record, but COMMITTED is
        reached only through :meth:`confirm` with a sensed-state confirmation.
        ``io_call`` returns the driver's OK/not-OK (recorded, NOT treated as physical
        truth).

        Idempotency (REF-T point 2 / Stripe, the fifth precheck): a repeat of a known
        ``action_id`` with the SAME ``params_fingerprint`` is an idempotent replay --
        the existing record is returned and ``io_call`` is NOT run (no silent re-send on
        resume, the red line); a repeat with a DIFFERENT fingerprint raises
        :class:`IdempotencyError`."""
        fp = self.params_fingerprint(action)
        existing = self._records.get(action.action_id)
        if existing is not None:
            if existing.params_fingerprint == fp:
                # idempotent replay: recognise the already-dispatched action, re-send
                # NOTHING. Recorded (not silent) as a note on the append-only log.
                self._append({**existing.snapshot(), "from": existing.state.value,
                              "to": existing.state.value,
                              "note": "idempotent_replay_skipped"})
                return existing
            raise IdempotencyError(
                f"action_id {action.action_id!r} reused with a different "
                f"params_fingerprint ({existing.params_fingerprint} != {fp}) -- same "
                "idempotency key, divergent parameters (a real bug, refusing)"
            )
        self._precheck(action)
        rec = ActionRecord(
            action_id=action.action_id, round_id=action.round_id,
            spec_fingerprint=action.spec_fingerprint,
            source_well=action.source_well, destination_well=action.destination_well,
            requested_volume_ul=action.requested_volume_ul, backend_id=action.backend_id,
            expected_pre_state=dict(action.expected_pre_state),
            expected_post_state=dict(action.expected_post_state),
            state=ActionState.PLANNED, params_fingerprint=fp,
        )
        self._records[action.action_id] = rec
        self._append({**rec.snapshot(), "from": None, "to": ActionState.PLANNED.value})
        # This dispatch issues attempt #1; the sensed evidence that commits it must be
        # stamped for_attempt==1 (observedGeneration gate). Set BEFORE PENDING is
        # persisted so the attempt number is on the crash-visible pending line.
        rec.attempt = 1
        # PENDING is persisted (hash-chained line flushed to disk) BEFORE any I/O.
        self._transition(rec, ActionState.PENDING)
        if io_call is not None:
            ok = bool(io_call())
            self._append({**rec.snapshot(), "from": ActionState.PENDING.value,
                          "to": ActionState.PENDING.value, "io_ok": ok,
                          "note": "driver_reply_recorded_not_committed"})
        return rec

    # -- phase 2: the sensed-state commit gate --------------------------------

    def confirm(self, action_id: str, evidence: SensedEvidence) -> ActionRecord:
        """Phase 2: the sensed-state gate for a PENDING (or recovering
        AWAITING_RECOVERY) action.

        observedGeneration gate FIRST (INDEX_M23_RECONCILE §1.3 / point 1): evidence
        whose ``for_attempt`` != the action's current attempt is a stale read-back --
        treated as UNOBSERVED (Unknown), never a confirmation nor a failure -- so the
        action stays PENDING. Then, for a stamp-matched observation:

          * ``CONFIRMED``  -> COMMITTED; the **observed** volume is committed to the
            volume ledger and stored alongside (never overwriting) the request.
          * ``MISMATCH``   -> adjudicate the classified failure with the RecoveryPolicy:
            AWAIT_HUMAN -> AWAITING_RECOVERY, else (ABORT / undefined fail-closed) ->
            ROLLED_BACK. Nothing was committed, so there is nothing to physically revert.
          * ``UNOBSERVED`` -> inconclusive read-back; the action stays PENDING (a driver
            OK alone is not a confirmation).
        """
        rec = self.record(action_id)
        if rec.state not in (ActionState.PENDING, ActionState.AWAITING_RECOVERY):
            raise ActionStateError(
                f"confirm() requires PENDING/AWAITING_RECOVERY, "
                f"action {action_id!r} is {rec.state.value}"
            )
        # observedGeneration: reject a read-back stamped for a different attempt as
        # stale -- it does NOT count as this attempt's observation (stays Unknown).
        if evidence.for_attempt != rec.attempt:
            self._append({**rec.snapshot(), "from": rec.state.value,
                          "to": rec.state.value, "note": "sensed_stale",
                          "evidence_id": evidence.evidence_id,
                          "evidence_for_attempt": evidence.for_attempt,
                          "current_attempt": rec.attempt})
            return rec

        if evidence.outcome is SensedOutcome.CONFIRMED:
            rec.sensed_evidence_id = evidence.evidence_id or rec.sensed_evidence_id
            rec.observed_volume_ul = evidence.observed_volume_ul
            if evidence.observed_volume_ul is not None:
                self._volume.apply(rec.source_well, rec.destination_well,
                                   evidence.observed_volume_ul)
            self._transition(rec, ActionState.COMMITTED,
                             disposition="committed_by_sensed_state")
            return rec

        if evidence.outcome is SensedOutcome.MISMATCH:
            rec.sensed_evidence_id = evidence.evidence_id or rec.sensed_evidence_id
            failure = FailureDetail.classify(
                evidence.code, evidence.detail or "sensed_state_mismatch",
                well_id=rec.destination_well)
            if not failure.defined:
                # fail-closed: an undefined failure never reaches the policy.
                self._append({**rec.snapshot(), "from": rec.state.value,
                              "to": rec.state.value, "note": "recovery_bypassed",
                              "reason": "undefined_failure_fail_closed",
                              "code": failure.code})
                self._transition(rec, ActionState.ROLLED_BACK,
                                 disposition="rolled_back_fail_closed")
                return rec
            verdict = self._policy.decide(failure)
            if verdict is not RecoveryAction.ABORT and not failure.recoverable:
                # guardrail: a policy may not recover a non-recoverable failure.
                verdict = RecoveryAction.ABORT
            self._append({**rec.snapshot(), "from": rec.state.value,
                          "to": rec.state.value, "note": "recovery_decision",
                          "policy": self._policy.name, "action": verdict.value,
                          "code": failure.code, "recoverable": failure.recoverable})
            if verdict is RecoveryAction.AWAIT_HUMAN:
                self._transition(rec, ActionState.AWAITING_RECOVERY,
                                 disposition=None)
            else:
                self._transition(rec, ActionState.ROLLED_BACK,
                                 disposition=f"rolled_back_{failure.code}")
            return rec

        # UNOBSERVED (Unknown): inconclusive read-back -> stays PENDING (NOT a failure).
        self._append({**rec.snapshot(), "from": rec.state.value,
                      "to": rec.state.value, "note": "sensed_unobserved",
                      "evidence_id": evidence.evidence_id})
        return rec

    def abandon(self, action_id: str, reason: str = "abandoned") -> ActionRecord:
        """Give up a paused action (AWAITING_RECOVERY -> ROLLED_BACK), clean fail-closed
        (nothing was committed)."""
        rec = self.record(action_id)
        if rec.state is not ActionState.AWAITING_RECOVERY:
            raise ActionStateError(
                f"abandon() requires AWAITING_RECOVERY, action {action_id!r} is "
                f"{rec.state.value}")
        self._transition(rec, ActionState.ROLLED_BACK, disposition=f"abandoned_{reason}")
        return rec

    def cancel(self, action_id: str, reason: str = "canceled") -> ActionRecord:
        """A cancel arriving while the action is AWAITING_RECOVERY terminates it ABORTED
        -- reusing the driver's paused-cancel == abandon precedent (112 即裁二): the
        instrument could not be recovered, the action fails closed."""
        rec = self.record(action_id)
        if rec.state is not ActionState.AWAITING_RECOVERY:
            raise ActionStateError(
                f"cancel() of a physical action is defined only in AWAITING_RECOVERY "
                f"(112 即裁二); action {action_id!r} is {rec.state.value}")
        self._transition(rec, ActionState.ABORTED,
                         disposition=f"canceled_while_awaiting_recovery_{reason}")
        return rec

    def recover(self, action_id: str) -> ActionRecord:
        """Operator-initiated recovery of a paused action (122 即裁二): re-dispatch as a
        DISTINCT auditable attempt instead of folding "the n-th failure" into a self-loop.

        AWAITING_RECOVERY --recover(attempt=n+1)--> PENDING: the attempt counter is bumped
        (the idempotency KEY ``action_id`` is unchanged; ``for_attempt`` distinguishes the
        attempts, so each try is independently auditable and the observedGeneration gate
        rejects a read-back stamped for the OLD attempt). The caller then re-senses with
        evidence stamped ``for_attempt == the new attempt``: a CONFIRMED read-back commits,
        a fresh MISMATCH routes back to AWAITING_RECOVERY via the existing PENDING edge.
        The attempt CEILING is a RecoveryPolicy concern (A2 ordered-policy table), NOT a
        transition-table edge -- this method only advances one attempt."""
        rec = self.record(action_id)
        if rec.state is not ActionState.AWAITING_RECOVERY:
            raise ActionStateError(
                f"recover() requires AWAITING_RECOVERY, action {action_id!r} is "
                f"{rec.state.value}")
        rec.attempt += 1
        # PENDING for the NEW attempt; the sensed evidence that commits it must be
        # stamped for_attempt == rec.attempt (the bumped value).
        self._transition(rec, ActionState.PENDING)
        return rec

    def continue_planned(
        self, action_id: str, io_call: Callable[[], bool] | None = None
    ) -> ActionRecord:
        """Resume-path completion of a PLANNED action whose I/O was NEVER issued (122 即裁
        三, the PLANNED third of the resume trichotomy).

        A PLANNED-on-disk state is the narrow crash window BETWEEN the PLANNED append and
        the PENDING append inside :meth:`dispatch` (PENDING-before-I/O invariant), so the
        hardware I/O provably never ran. Completing it to PENDING + issuing the I/O is the
        correct NON-duplicating resume action -- it does not re-send anything, because
        nothing was ever sent. (COMMITTED resumes SKIP; PENDING resumes RE-SENSE, never
        re-dispatch -- both handled by the orchestrator, not here.)"""
        rec = self.record(action_id)
        if rec.state is not ActionState.PLANNED:
            raise ActionStateError(
                f"continue_planned() requires PLANNED, action {action_id!r} is "
                f"{rec.state.value}")
        if rec.attempt == 0:
            rec.attempt = 1
        self._transition(rec, ActionState.PENDING)
        if io_call is not None:
            ok = bool(io_call())
            self._append({**rec.snapshot(), "from": ActionState.PENDING.value,
                          "to": ActionState.PENDING.value, "io_ok": ok,
                          "note": "driver_reply_recorded_not_committed"})
        return rec
