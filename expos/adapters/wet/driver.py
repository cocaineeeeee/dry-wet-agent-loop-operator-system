"""WetDriver -- client library for the plate-reader simulator (construct B, client).

Implements the ADAPTER_ACTIONS state machine as the execution-time sub-granularity
of a single wet goal. The six-state core is extended to SEVEN with an explicit
``AWAITING_RECOVERY`` state (INDEX_M16 Q2; Opentrons ``error_recovery_policy``)::

    (none) --submit--> ACCEPTED --run--> EXECUTING --> SUCCEEDED
                           |                 |  \\----> ABORTED   (device/timeout)
                           |                 |  \\--> CANCELING --> CANCELED
                           |                 \\--> AWAITING_RECOVERY  (recoverable,
                           |                          |   \\           policy != NeverRecover)
                           |                    recover|    \\abandon
                           |                           v     v
                           |                       EXECUTING  ABORTED

``AWAITING_RECOVERY`` is entered ONLY when the pluggable :class:`RecoveryPolicy`
returns ``AWAIT_HUMAN`` for a *defined, recoverable* error; it is resolved out of
band by the explicit :meth:`WetDriver.recover` / :meth:`WetDriver.abandon` API (the
real-device seam). The default policy is :class:`NeverRecover` -- every defined
error aborts, so the driver reproduces the prior six-state behaviour bit-for-bit
(the existing wet suite is the regression anchor). Undefined failures NEVER reach
the policy: they fail closed (施工令 Phase 4B / Opentrons anti-fragility).

A wet goal = "read this validated plate". The driver owns the seven G3 concerns on
the client side: it health-checks the device, holds the single instrument lease,
calibrates, measures with a bounded timeout+retry budget, classifies device
failures, and enforces the sample-identity custody chain on every reading it
ingests. Cancelled / failed wells become *visible* null readings (never silently
dropped) -- mirroring ADAPTER_ACTIONS 4.

Emits an event trail as plain dicts on ``self.events`` so the host runtime can
persist them to its events.jsonl.
"""

from __future__ import annotations

import json
import logging
import socket
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .labware import load_labware
from .ot_protocol import (
    OTProtocol,
    OT_BACKEND,
    ValidationError,
    compile_and_validate,
)
from .protocol_spec import CustodyChain, ProtocolSpec
from .recovery import (
    FailureDetail,
    NeverRecover,
    RecoveryAction,
    RecoveryPolicy,
    failure_modes_catalogue,
)


_LOG = logging.getLogger(__name__)


class GoalState(str, Enum):
    ACCEPTED = "ACCEPTED"
    EXECUTING = "EXECUTING"
    CANCELING = "CANCELING"
    AWAITING_RECOVERY = "AWAITING_RECOVERY"
    SUCCEEDED = "SUCCEEDED"
    ABORTED = "ABORTED"
    CANCELED = "CANCELED"


#: Terminal states -- the lease/socket are released exactly when one is reached.
_TERMINAL_STATES: frozenset[GoalState] = frozenset(
    {GoalState.SUCCEEDED, GoalState.ABORTED, GoalState.CANCELED}
)

#: Explicit seven-state transition table. Any transition NOT listed here is
#: illegal and rejected loudly by :meth:`WetDriver._transition` -- there is no
#: silent state corruption. ACCEPTED is the driver's entry state (set by
#: ``submit_goal``), so it has no inbound edge here.
_LEGAL_TRANSITIONS: dict[GoalState, frozenset[GoalState]] = {
    GoalState.ACCEPTED: frozenset({GoalState.EXECUTING}),
    GoalState.EXECUTING: frozenset({
        GoalState.SUCCEEDED,
        GoalState.ABORTED,
        GoalState.CANCELING,
        GoalState.AWAITING_RECOVERY,
    }),
    GoalState.CANCELING: frozenset({GoalState.CANCELED}),
    # AWAITING_RECOVERY has NO ->CANCELING/->CANCELED edge on purpose. A cancel
    # arriving while the run is paused is adjudicated as ABORTED, not CANCELED:
    # per 112 即裁二 (AWAITING_RECOVERY x cancel), a paused-state cancel is
    # DEFINED to be exactly ``abandon()`` -- the instrument could not be recovered,
    # so the run fails closed to ABORTED (every unmeasured well a visible null),
    # reusing the ->ABORTED edge below. See :meth:`WetDriver.request_cancel`.
    GoalState.AWAITING_RECOVERY: frozenset({
        GoalState.EXECUTING,   # recover()
        GoalState.ABORTED,     # abandon() -- AND paused-state cancel (112 即裁二)
    }),
    GoalState.SUCCEEDED: frozenset(),
    GoalState.ABORTED: frozenset(),
    GoalState.CANCELED: frozenset(),
}


class WetDriverError(Exception):
    pass


@dataclass
class RawReading:
    """OS-visible raw reading -- carries the custody key, NO truth fields."""

    sample_id: str
    well_id: str
    cand_id: str | None
    control_id: str | None
    value: float | None
    seq: int | None
    status: str            # ok | dropout | failed | canceled | assumed_false_positive
    raw_record_id: str


@dataclass
class WetExecutionResult:
    goal_id: str
    outcome: GoalState
    reason: str | None
    readings: list[RawReading]
    events: list[dict[str, Any]]
    custody: CustodyChain
    n_raw: int = 0
    n_failed: int = 0


@dataclass
class ValidationReport:
    """Result of the ``dry_run`` contract verb -- validate without the instrument."""

    ok: bool
    backend: str
    n_wells: int
    n_transfers: int
    warnings: list[str]
    error: str | None = None


# --- low-level socket client ---------------------------------------------------

class _ReaderClient:
    """Newline-delimited JSON over a TCP loopback socket, with reconnect."""

    def __init__(self, host: str, port: int, timeout_s: float) -> None:
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self._sock: socket.socket | None = None
        self._buf = b""

    def connect(self) -> None:
        self.close()
        s = socket.create_connection((self.host, self.port), timeout=self.timeout_s)
        s.settimeout(self.timeout_s)
        self._sock = s
        self._buf = b""

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
        self._buf = b""

    def request(self, obj: dict[str, Any]) -> dict[str, Any]:
        """Send one request, read one JSON line. Raises socket.timeout / OSError."""
        if self._sock is None:
            self.connect()
        assert self._sock is not None
        self._sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("reader closed connection without reply")
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return json.loads(line.decode("utf-8"))


# --- driver --------------------------------------------------------------------

class WetDriver:
    """Seven-state wet-execution driver over the plate-reader simulator.

    The simulate<->real-device boundary is a swappable :class:`RecoveryPolicy`
    argument, not a second driver. ``policy=NeverRecover()`` (default) == the
    trusted-simulation leg; ``WaitForRecovery`` / ``AssumeFalsePositive`` are the
    real-device legs.
    """

    #: instrument pipette usable range (µL); p300 single-channel gen2.
    PIPETTE_MIN_UL = 20.0
    PIPETTE_MAX_UL = 300.0
    #: simulated-time cost model (mirrors ot_protocol.execute_simulated).
    _DECK_S, _TIP_S, _ASPIRATE_S, _MOVE_S, _DISPENSE_S = 5.0, 4.0, 2.0, 3.0, 2.0

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        *,
        exp_id: str = "exp-0",
        round_id: int = 0,
        holder: str = "wet-driver",
        timeout_s: float = 2.0,
        max_retries: int = 3,
        lease_ttl_s: float = 60.0,
        policy: RecoveryPolicy | None = None,
        max_recovery_retries: int = 2,
    ) -> None:
        self.exp_id = exp_id
        self.round_id = round_id
        self.holder = holder
        self.max_retries = max_retries
        self.lease_ttl_s = lease_ttl_s
        self.goal_id = f"{exp_id}:{round_id}"
        #: pluggable recovery policy -- THE simulate<->real seam (default NeverRecover).
        self._policy: RecoveryPolicy = policy or NeverRecover()
        self.max_recovery_retries = max_recovery_retries
        self._client = _ReaderClient(host, port, timeout_s)
        self.state: GoalState = GoalState.ACCEPTED  # placeholder until submit
        self.events: list[dict[str, Any]] = []
        self._lease_id: str | None = None
        self._cancel_requested = False
        self._otp: OTProtocol | None = None
        # resumable-run cursor (persist across an AWAITING_RECOVERY suspension)
        self._readings: list[RawReading] = []
        self._well_index = 0
        self._recovery_retries = 0
        self._pending_failure: FailureDetail | None = None

    @property
    def policy(self) -> RecoveryPolicy:
        return self._policy

    # -- event helper ---------------------------------------------------------

    def _emit(self, kind: str, **fields: Any) -> None:
        self.events.append({
            "kind": kind, "goal_id": self.goal_id, "round_id": self.round_id,
            "ts": round(time.time(), 6), **fields,
        })

    def _transition(self, new: GoalState, reason: str | None = None) -> None:
        """Apply a state transition, rejecting any not in the legal table."""
        allowed = _LEGAL_TRANSITIONS.get(self.state, frozenset())
        if new not in allowed:
            raise WetDriverError(
                f"illegal wet-driver transition {self.state.value} -> {new.value} "
                f"(allowed from {self.state.value}: "
                f"{sorted(s.value for s in allowed)})"
            )
        self.state = new
        self._emit("action_state", state=new.value, reason=reason)

    # -- public: lifecycle ----------------------------------------------------

    def submit_goal(self, otp: OTProtocol) -> None:
        """Enter ACCEPTED with an immutable snapshot of the validated protocol."""
        if not otp.validated:
            raise WetDriverError("cannot submit an unvalidated protocol")
        if self._otp is not None:
            raise WetDriverError("goal already submitted on this driver")
        self._otp = otp
        # ACCEPTED is the entry state (no inbound edge in the table); set directly.
        self.state = GoalState.ACCEPTED
        self._emit("action_state", state=GoalState.ACCEPTED.value, reason=None)
        self._emit("action_goal", exp_id=self.exp_id, adapter="wet_sim_reader",
                   n_samples=len(otp.wells), backend=otp.backend,
                   policy=self._policy.name)

    def request_cancel(self) -> WetExecutionResult | None:
        """Human/timeout cancel request.

        Two regimes, made explicit (ABI 成文, no implicit edge -- 112 即裁二):

          * NOT paused (ACCEPTED / EXECUTING): the request is a flag honoured at
            the next well boundary inside the measurement loop, which drives
            EXECUTING -> CANCELING -> CANCELED. Returns ``None`` (the terminal
            result surfaces from the in-flight ``run``/``recover``).
          * paused in AWAITING_RECOVERY: there is no active loop to honour a flag,
            and the transition table deliberately has NO ->CANCELING edge out of
            the paused state. A paused-state cancel is therefore DEFINED to be
            exactly :meth:`abandon` -- the instrument could not be recovered, so
            the run fails closed to ABORTED with every unmeasured well a visible
            null and identical event留痕. Returns that terminal result.
        """
        self._cancel_requested = True
        self._emit("action_cancel_requested")
        if self.state is GoalState.AWAITING_RECOVERY:
            # 112 即裁二: 暂停态 cancel ≡ abandon(). Behaviour (ABORTED terminal,
            # visible-null unmeasured wells, event trail) is byte-for-byte abandon.
            return self.abandon("canceled_while_awaiting_recovery")
        return None

    def run(self, *, calibrate: bool = True) -> WetExecutionResult:
        """Drive ACCEPTED -> EXECUTING -> terminal (or AWAITING_RECOVERY).

        Guarantees: every planned well produces exactly one reading (ok / dropout
        / failed / canceled); unmeasured wells on an abort/cancel are emitted as
        visible null readings; the instrument lease is released on any TERMINAL
        outcome (and deliberately held across an AWAITING_RECOVERY suspension, so
        the reserved instrument stays reserved for the human recovery).
        """
        if self._otp is None or self.state != GoalState.ACCEPTED:
            raise WetDriverError(f"run() requires ACCEPTED state, got {self.state}")
        otp = self._otp
        custody = otp.custody
        self._readings = []
        self._well_index = 0
        self._recovery_retries = 0

        self._transition(GoalState.EXECUTING)
        try:
            # (1) health check ------------------------------------------------
            health = self._health_checked()
            if health.get("status") == "offline":
                return self._abort(custody, self._readings, otp,
                                   "device_offline_at_health")

            # (2) reservation -------------------------------------------------
            if not self._acquire_lease():
                return self._abort(custody, self._readings, otp,
                                   "reservation_denied")

            # (3) calibration -------------------------------------------------
            if calibrate:
                cal = self._client.request(
                    {"cmd": "calibrate", "lease_id": self._lease_id})
                self._emit("calibration", ok=cal.get("ok"),
                           detail=cal.get("error"))

            # (4) measurement loop (resumable) --------------------------------
            return self._measure_loop()
        finally:
            if self.state in _TERMINAL_STATES:
                self._release_lease()
                self._client.close()

    def recover(self) -> WetExecutionResult:
        """Resume a paused run (AWAITING_RECOVERY -> EXECUTING) and continue.

        The explicit real-device recovery API: the human/operator fixed the
        instrument, so the failed well is re-attempted and the run resumes. Returns
        a terminal result, or AWAITING_RECOVERY again if a further recoverable
        failure is hit.
        """
        if self.state != GoalState.AWAITING_RECOVERY or self._otp is None:
            raise WetDriverError(
                f"recover() requires AWAITING_RECOVERY state, got {self.state}")
        pending = self._pending_failure
        self._emit("recovery_resume",
                   well_id=pending.well_id if pending else None,
                   code=pending.code if pending else None)
        self._transition(GoalState.EXECUTING, "recovered")
        self._pending_failure = None
        self._recovery_retries = 0
        try:
            return self._measure_loop()
        finally:
            if self.state in _TERMINAL_STATES:
                self._release_lease()
                self._client.close()

    def abandon(self, reason: str = "abandoned_by_operator") -> WetExecutionResult:
        """Give up a paused run (AWAITING_RECOVERY -> ABORTED), fail-closed.

        The instrument could not be recovered; every unmeasured well surfaces as a
        visible null reading (never silently dropped) and the lease is released.
        """
        if self.state != GoalState.AWAITING_RECOVERY or self._otp is None:
            raise WetDriverError(
                f"abandon() requires AWAITING_RECOVERY state, got {self.state}")
        otp = self._otp
        custody = otp.custody
        self._fill_unmeasured(custody, self._readings, otp, self._well_index,
                              "failed")
        self._release_lease()
        self._transition(GoalState.ABORTED, reason)
        n_failed = sum(1 for r in self._readings if r.value is None)
        self._emit("action_result", outcome="ABORTED", reason=reason,
                   n_raw=len(self._readings), n_failed=n_failed)
        self._client.close()
        return self._result(GoalState.ABORTED, reason, self._readings, custody)

    # -- contract verbs (interface shape == real-machine driver contract) -----

    def health_check(self) -> dict[str, Any]:
        """Contract verb: query instrument health (public form of the run check)."""
        return self._health_checked()

    def calibrate(self) -> dict[str, Any]:
        """Contract verb: calibrate under the currently-held lease."""
        if self._lease_id is None:
            raise WetDriverError("calibrate() requires an acquired lease")
        cal = self._client.request({"cmd": "calibrate", "lease_id": self._lease_id})
        self._emit("calibration", ok=cal.get("ok"), detail=cal.get("error"))
        return cal

    def dry_run(self, spec: ProtocolSpec) -> ValidationReport:
        """Contract verb: validate a protocol WITHOUT touching the instrument.

        Reuses the opentrons ``simulate`` leg (``compile_and_validate``); a failing
        spec returns ``ok=False`` with the reason rather than raising, so callers
        can gate on the report.
        """
        try:
            otp = compile_and_validate(spec)
        except ValidationError as exc:
            return ValidationReport(ok=False, backend=OT_BACKEND, n_wells=0,
                                    n_transfers=0, warnings=[], error=str(exc))
        n_transfers = self._count_transfers(otp)
        return ValidationReport(ok=True, backend=otp.backend, n_wells=len(otp.wells),
                                n_transfers=n_transfers, warnings=list(otp.warnings),
                                error=None)

    def capabilities(self) -> dict[str, Any]:
        """Contract verb: machine-readable capability declaration.

        The "manifest" layer (INDEX_M16 Q3): labware support + channels + pipette
        range + supported metrics + the active recovery policy + the declared
        failure modes -- everything a planner needs to decide, without
        instantiating the instrument.
        """
        lw = load_labware()
        return {
            "adapter": "wet_sim_reader",
            "backend": OT_BACKEND,
            "channels": lw.well_channels,
            "labware": lw.capabilities(),
            "pipette": {"min_ul": self.PIPETTE_MIN_UL, "max_ul": self.PIPETTE_MAX_UL},
            "metrics": ["solvent_response"],
            "actions": ["health_check", "calibrate", "acquire", "release", "measure"],
            "recovery_policy": self._policy.name,
            "failure_modes": failure_modes_catalogue(),
        }

    def estimate_runtime(self, otp: OTProtocol) -> float:
        """Contract verb: modelled simulated wall-time (s) for the protocol."""
        duration = self._DECK_S
        for w in otp.wells:
            for v in (w.vol_low_ul, w.vol_high_ul):
                if v > 0:
                    duration += (self._TIP_S + self._ASPIRATE_S
                                 + self._MOVE_S + self._DISPENSE_S)
        return round(duration, 2)

    def estimate_cost(self, otp: OTProtocol) -> dict[str, Any]:
        """Contract verb: consumable + instrument-time cost (W5 model as a verb)."""
        n_transfers = self._count_transfers(otp)
        reagent_ul = sum(w.vol_low_ul + w.vol_high_ul for w in otp.wells)
        return {
            "n_transfers": n_transfers,
            "n_tips": n_transfers,               # one fresh tip per stock per well
            "reagent_ul": round(reagent_ul, 3),
            "duration_s": self.estimate_runtime(otp),
        }

    def failure_modes(self) -> list[dict[str, Any]]:
        """Contract verb: the declared defined-error catalogue (code + recoverable)."""
        return failure_modes_catalogue()

    @staticmethod
    def _count_transfers(otp: OTProtocol) -> int:
        return sum(1 for w in otp.wells
                   for v in (w.vol_low_ul, w.vol_high_ul) if v > 0)

    # -- measurement loop (shared by run / recover) ---------------------------

    def _measure_loop(self) -> WetExecutionResult:
        """Drive the per-well measurement from ``self._well_index`` to a result.

        Returns a terminal result, or an AWAITING_RECOVERY snapshot when the policy
        pauses the run. With the default NeverRecover policy the failure branch is
        byte-for-byte the prior behaviour (adjudicate -> ABORT).
        """
        assert self._otp is not None
        otp = self._otp
        custody = otp.custody
        readings = self._readings
        total = len(otp.wells)

        while self._well_index < total:
            i = self._well_index
            if self._cancel_requested:
                self._transition(GoalState.CANCELING, "cancel_requested")
                self._fill_unmeasured(custody, readings, otp, i, "canceled")
                self._release_lease()
                self._transition(GoalState.CANCELED, "canceled")
                self._emit("action_result", outcome="CANCELED",
                           reason="canceled", n_raw=len(readings))
                return self._result(GoalState.CANCELED, "canceled",
                                    readings, custody)

            plan = otp.wells[i]
            sample = {
                "sample_id": plan.sample_id, "well_id": plan.well_id,
                "polarity": plan.realised_polarity,
                "is_control": plan.control_id is not None,
            }
            ok, reply, failure = self._measure_one(sample)
            if not ok:
                assert failure is not None
                action = self._adjudicate(failure)
                if action is RecoveryAction.ABORT:
                    # device failure that survived retries + adjudication -> abort;
                    # this and all remaining wells become visible failures.
                    self._fill_unmeasured(custody, readings, otp, i, "failed")
                    self._release_lease()
                    return self._abort(custody, readings, otp, failure.reason,
                                       already_filled=True)
                if action is RecoveryAction.AWAIT_HUMAN:
                    self._well_index = i          # resume re-attempts this well
                    self._pending_failure = failure
                    self._transition(GoalState.AWAITING_RECOVERY, failure.reason)
                    self._emit("recovery_awaiting", code=failure.code,
                               well_id=failure.well_id, reason=failure.reason)
                    return self._partial_result(readings, custody, failure.reason)
                if action is RecoveryAction.RETRY_STEP:
                    self._recovery_retries += 1
                    self._emit("recovery_retry_step", code=failure.code,
                               well_id=failure.well_id,
                               attempt=self._recovery_retries)
                    if self._recovery_retries > self.max_recovery_retries:
                        self._fill_unmeasured(custody, readings, otp, i, "failed")
                        self._release_lease()
                        return self._abort(
                            custody, readings, otp,
                            f"{failure.reason}_recovery_exhausted",
                            already_filled=True)
                    continue                       # re-attempt same well index
                # ASSUME_FALSE_POSITIVE: adjudicate the flag a false alarm, continue
                self._ingest_assumed_false_positive(custody, plan, failure)
                self._recovery_retries = 0
                self._well_index = i + 1
                self._maybe_feedback(self._well_index, total)
                continue

            for r in reply.get("readings", []):
                reading = self._ingest_reading(otp, custody, plan, r)
                readings.append(reading)
            self._recovery_retries = 0
            self._well_index = i + 1
            self._maybe_feedback(self._well_index, total)

        # success -----------------------------------------------------------
        self._release_lease()
        self._transition(GoalState.SUCCEEDED)
        n_failed = sum(1 for r in readings if r.value is None)
        self._emit("action_result", outcome="SUCCEEDED", reason=None,
                   n_raw=len(readings), n_failed=n_failed)
        return self._result(GoalState.SUCCEEDED, None, readings, custody)

    def _maybe_feedback(self, completed: int, total: int) -> None:
        if completed % 4 == 0 or completed == total:
            self._emit("action_feedback", phase="measure",
                       fraction=round(completed / total, 3))

    def _adjudicate(self, failure: FailureDetail) -> RecoveryAction:
        """Map a classified failure to a recovery action (the policy seam).

        Fail-closed boundary: an UNDEFINED failure never reaches the policy -- it
        aborts unconditionally. A policy may not recover a non-recoverable failure
        (guardrail); such a response is downgraded to ABORT loudly.
        """
        if not failure.defined:
            self._emit("recovery_bypassed", code=failure.code,
                       well_id=failure.well_id,
                       reason="undefined_failure_fail_closed")
            return RecoveryAction.ABORT
        action = self._policy.decide(failure)
        if action is not RecoveryAction.ABORT and not failure.recoverable:
            self._emit("recovery_policy_violation", code=failure.code,
                       requested=action.value,
                       detail="policy requested recovery of a non-recoverable "
                              "failure -- downgraded to ABORT")
            return RecoveryAction.ABORT
        self._emit("recovery_decision", code=failure.code, policy=self._policy.name,
                   action=action.value, recoverable=failure.recoverable,
                   false_positive_prone=failure.false_positive_prone,
                   well_id=failure.well_id)
        return action

    # -- helpers --------------------------------------------------------------

    def _health_checked(self) -> dict[str, Any]:
        for attempt in range(self.max_retries):
            try:
                h = self._client.request({"cmd": "health"})
                self._emit("health", status=h.get("status"),
                           uptime_s=h.get("uptime_s"),
                           meas_since_cal=h.get("last_calibration", {})
                           .get("meas_since"))
                return h
            except (socket.timeout, OSError) as exc:
                self._emit("health_error", attempt=attempt, detail=str(exc))
                self._reconnect_backoff(attempt)
        return {"status": "offline"}

    def _acquire_lease(self) -> bool:
        try:
            r = self._client.request({
                "cmd": "acquire", "holder": self.holder, "ttl": self.lease_ttl_s})
        except (socket.timeout, OSError) as exc:
            self._emit("lease_error", detail=str(exc))
            return False
        if r.get("ok"):
            self._lease_id = r["lease_id"]
            self._emit("lease_acquired", lease_id=self._lease_id)
            return True
        self._emit("lease_denied", error=r.get("error"), detail=r.get("detail"))
        return False

    def _release_lease(self) -> None:
        if self._lease_id is None:
            return
        try:
            self._client.request(
                {"cmd": "release", "lease_id": self._lease_id})
            self._emit("lease_released", lease_id=self._lease_id)
        except (socket.timeout, OSError) as exc:
            _LOG.warning("lease release failed (%s) -- will TTL-expire server-side", exc)
        finally:
            self._lease_id = None

    def _measure_one(
        self, sample: dict[str, Any]
    ) -> tuple[bool, dict[str, Any], FailureDetail | None]:
        """Measure one well with bounded timeout + retry. Classifies failures.

        On failure returns a :class:`FailureDetail` (the ABI record the recovery
        policy adjudicates); on success returns ``(True, reply, None)``.
        """
        well_id = sample["well_id"]
        last_reason = "unknown"
        last_code = "unknown"
        for attempt in range(self.max_retries):
            try:
                reply = self._client.request({
                    "cmd": "measure", "lease_id": self._lease_id,
                    "samples": [sample]})
            except socket.timeout:
                last_reason = last_code = "timeout"
                self._emit("measure_retry", attempt=attempt, cause="timeout",
                           well_id=well_id)
                self._reconnect_backoff(attempt)
                continue
            except (ConnectionError, OSError) as exc:
                last_reason = last_code = "device_unreachable"
                self._emit("measure_retry", attempt=attempt,
                           cause="device_unreachable", detail=str(exc),
                           well_id=well_id)
                self._reconnect_backoff(attempt)
                continue
            if reply.get("ok"):
                return True, reply, None
            # device-level error reply -> classify
            err = reply.get("error", "device_error")
            last_reason = err
            # for a generic device_error the discriminating code rides in `code`
            last_code = reply.get("code", err) if err == "device_error" else err
            if err in ("device_offline", "device_error"):
                self._emit("measure_retry", attempt=attempt, cause=err,
                           code=reply.get("code"), well_id=well_id)
                self._reconnect_backoff(attempt)
                continue
            # lease_invalid / missing_sample_id: not retryable -> classify now
            self._emit("measure_failed", cause=err, well_id=well_id)
            return False, reply, FailureDetail.classify(
                last_code, err, well_id=well_id)
        self._emit("measure_budget_exhausted", reason=last_reason,
                   well_id=well_id, budget=self.max_retries)
        return False, {}, FailureDetail.classify(
            last_code, f"{last_reason}_budget_exhausted", well_id=well_id)

    def _ingest_reading(
        self, otp: OTProtocol, custody: CustodyChain,
        plan: Any, r: dict[str, Any],
    ) -> RawReading:
        """Enforce the sample-identity custody chain, then build a raw record."""
        sid = r.get("sample_id")
        # custody: forged / missing sample_id is rejected (not ingested as data)
        if not sid or not custody.known(sid):
            self._emit("custody_violation", got_sample_id=sid,
                       well_id=r.get("well_id"),
                       detail="reading rejected: sample_id unknown/missing")
            raw_id = f"raw-{self.exp_id}-{self.round_id}-{plan.well_id}-REJECTED"
            return RawReading(
                sample_id=sid or "", well_id=plan.well_id,
                cand_id=plan.cand_id, control_id=plan.control_id,
                value=None, seq=r.get("seq"), status="rejected",
                raw_record_id=raw_id)
        # audit: a known record with NO stamped actor never passed the audited
        # issue() path -- unattested/forged provenance, rejected like an unknown id.
        if not custody.attested(sid):
            self._emit("custody_violation", got_sample_id=sid,
                       well_id=r.get("well_id"),
                       detail="reading rejected: custody record has no actor "
                              "(unattested/forged provenance)")
            raw_id = f"raw-{self.exp_id}-{self.round_id}-{plan.well_id}-REJECTED"
            return RawReading(
                sample_id=sid, well_id=plan.well_id,
                cand_id=plan.cand_id, control_id=plan.control_id,
                value=None, seq=r.get("seq"), status="rejected",
                raw_record_id=raw_id)
        if sid != plan.sample_id:
            # reading for a different sample than the well we asked about
            self._emit("custody_violation", got_sample_id=sid,
                       expected=plan.sample_id, well_id=plan.well_id,
                       detail="sample_id mismatch vs deck binding")
        raw_id = f"raw-{self.exp_id}-{self.round_id}-{plan.well_id}"
        value = r.get("value")
        status = r.get("status", "ok")
        custody.record_measurement(sid, value, r.get("seq"))
        custody.record_raw(sid, raw_id)
        return RawReading(
            sample_id=sid, well_id=plan.well_id, cand_id=plan.cand_id,
            control_id=plan.control_id, value=value, seq=r.get("seq"),
            status=status, raw_record_id=raw_id)

    def _ingest_assumed_false_positive(
        self, custody: CustodyChain, plan: Any, failure: FailureDetail,
    ) -> None:
        """Continue past a false-positive sensor flag: emit a VISIBLE flagged null.

        The well is not silently skipped; it lands a null reading tagged
        ``assumed_false_positive`` (the QC false-positive adjudication isomorph),
        with its custody chain fully traced.
        """
        raw_id = f"raw-{self.exp_id}-{self.round_id}-{plan.well_id}"
        custody.record_measurement(plan.sample_id, None, None)
        custody.record_raw(plan.sample_id, raw_id)
        self._readings.append(RawReading(
            sample_id=plan.sample_id, well_id=plan.well_id, cand_id=plan.cand_id,
            control_id=plan.control_id, value=None, seq=None,
            status="assumed_false_positive", raw_record_id=raw_id))
        self._emit("recovery_false_positive", well_id=plan.well_id,
                   code=failure.code)

    def _fill_unmeasured(
        self, custody: CustodyChain, readings: list[RawReading],
        otp: OTProtocol, from_index: int, status: str,
    ) -> None:
        """Emit visible null readings for wells not yet measured (never drop)."""
        measured = {r.well_id for r in readings}
        for plan in otp.wells[from_index:]:
            if plan.well_id in measured:
                continue
            raw_id = f"raw-{self.exp_id}-{self.round_id}-{plan.well_id}"
            custody.record_measurement(plan.sample_id, None, None)
            custody.record_raw(plan.sample_id, raw_id)
            readings.append(RawReading(
                sample_id=plan.sample_id, well_id=plan.well_id,
                cand_id=plan.cand_id, control_id=plan.control_id,
                value=None, seq=None, status=status, raw_record_id=raw_id))
            self._emit("well_failed", well_id=plan.well_id, status=status)

    def _reconnect_backoff(self, attempt: int) -> None:
        self._client.close()
        time.sleep(min(0.05 * (attempt + 1), 0.2))
        try:
            self._client.connect()
        except OSError as exc:
            _LOG.warning("reconnect attempt failed (%s) -- next request retries", exc)

    def _abort(
        self, custody: CustodyChain, readings: list[RawReading],
        otp: OTProtocol, reason: str, *, already_filled: bool = False,
    ) -> WetExecutionResult:
        if not already_filled:
            self._fill_unmeasured(custody, readings, otp, 0, "failed")
        self._transition(GoalState.ABORTED, reason)
        n_failed = sum(1 for r in readings if r.value is None)
        self._emit("action_result", outcome="ABORTED", reason=reason,
                   n_raw=len(readings), n_failed=n_failed)
        return self._result(GoalState.ABORTED, reason, readings, custody)

    def _partial_result(
        self, readings: list[RawReading], custody: CustodyChain, reason: str,
    ) -> WetExecutionResult:
        """Snapshot returned while paused in AWAITING_RECOVERY (non-terminal)."""
        return WetExecutionResult(
            goal_id=self.goal_id, outcome=GoalState.AWAITING_RECOVERY, reason=reason,
            readings=list(readings), events=list(self.events), custody=custody,
            n_raw=len(readings),
            n_failed=sum(1 for r in readings if r.value is None))

    def _result(
        self, outcome: GoalState, reason: str | None,
        readings: list[RawReading], custody: CustodyChain,
    ) -> WetExecutionResult:
        return WetExecutionResult(
            goal_id=self.goal_id, outcome=outcome, reason=reason,
            readings=readings, events=list(self.events), custody=custody,
            n_raw=len(readings),
            n_failed=sum(1 for r in readings if r.value is None))
