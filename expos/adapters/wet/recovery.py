"""RecoveryPolicy: the simulate<->real-device seam as a pluggable policy object.

Design (INDEX_M16 Q2 + REF-W headline; Opentrons ``error_recovery_policy.py``):
the difference between a trusted simulation and a real instrument is NOT two
drivers -- it is one driver contract plus a swappable :class:`RecoveryPolicy` and
one explicit ``AWAITING_RECOVERY`` state. ``simulate`` semantics == the default
:class:`NeverRecover` (a defined error aborts the run); a real machine injects a
policy that can pause for a human or continue past a false-positive sensor flag.

Fail-closed boundary (施工令 Phase 4B, Opentrons anti-fragility): the policy sees
ONLY *defined* failures -- failure codes enumerated in :data:`FAILURE_MODES`. An
*undefined* failure (an unrecognised code) never reaches the policy; it aborts the
run unconditionally. Recoverability is a property of the failure, not the policy:
a policy may choose to recover a recoverable failure, but can never invent
recoverability for one that is not.

The three shipped policies:
  * :class:`NeverRecover`  -- default; every defined error -> ABORT. Reproduces the
    six-state behaviour bit-for-bit (the regression anchor).
  * :class:`WaitForRecovery` -- recoverable defined error -> AWAIT_HUMAN (enter
    ``AWAITING_RECOVERY``, resolved by the driver's explicit ``recover()`` /
    ``abandon()`` API; the real-device leg). Non-recoverable defined error -> ABORT.
  * :class:`AssumeFalsePositive` -- ONLY a defined error flagged
    ``false_positive_prone`` -> ASSUME_FALSE_POSITIVE (continue, treating the flag
    as a false alarm). This is the direct isomorph of the expos QC false-positive
    adjudication (``ErrorRecoveryType.ASSUME_FALSE_POSITIVE_AND_CONTINUE``): a
    sensor that sometimes cries wolf must not be blindly retried nor abort the run,
    it is adjudicated. Any error NOT so flagged -> ABORT.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class RecoveryAction(str, Enum):
    """The verdict a :class:`RecoveryPolicy` returns for one defined failure."""

    ABORT = "ABORT"                                # fail-closed: terminate ABORTED
    RETRY_STEP = "RETRY_STEP"                      # re-attempt the failed step
    AWAIT_HUMAN = "AWAIT_HUMAN"                    # pause into AWAITING_RECOVERY
    ASSUME_FALSE_POSITIVE = "ASSUME_FALSE_POSITIVE"  # continue: flag was a false alarm


@dataclass(frozen=True)
class FailureMode:
    """One row of the driver's declared failure-mode catalogue (an ABI record).

    ``recoverable`` is the bit REF-I reserved on the FailureDetail ABI: whether a
    real instrument could, in principle, be brought back from this failure.
    ``false_positive_prone`` marks a flag that a sensor is known to raise
    spuriously (the QC false-positive isomorph).
    """

    code: str
    recoverable: bool
    false_positive_prone: bool
    category: str
    detail: str


#: Enumerated DEFINED failure modes. A failure whose code is absent here is
#: UNDEFINED and fail-closed (never adjudicated by a policy). Keyed by the code the
#: driver classifies from the device reply / transport error.
FAILURE_MODES: dict[str, FailureMode] = {
    # -- transport (driver-classified, recoverable: re-seat / reconnect) --
    "timeout": FailureMode(
        "timeout", True, False, "transport",
        "device did not respond within the client timeout budget"),
    "device_unreachable": FailureMode(
        "device_unreachable", True, False, "transport",
        "socket connection to the device was refused / reset"),
    # -- device errors (reply-classified) --
    "device_offline": FailureMode(
        "device_offline", True, False, "device",
        "device reports itself offline"),
    "E_DEVICE": FailureMode(
        "E_DEVICE", True, False, "device",
        "generic recoverable device error code"),
    "E_SENSOR": FailureMode(
        "E_SENSOR", True, True, "sensor",
        "sensor flag known to false-alarm (false-positive-prone; QC isomorph)"),
    # -- reservation --
    "resource_busy": FailureMode(
        "resource_busy", True, False, "lease",
        "instrument lease held by another holder"),
    "no_lease": FailureMode(
        "no_lease", False, False, "lease",
        "no active lease for this operation"),
    "lease_invalid": FailureMode(
        "lease_invalid", False, False, "lease",
        "lease id does not match the active lease"),
    # -- protocol / custody (never recoverable: a bad request, not a fault) --
    "missing_sample_id": FailureMode(
        "missing_sample_id", False, False, "custody",
        "reader refused an unlabeled sample (no sample_id)"),
    "no_samples": FailureMode(
        "no_samples", False, False, "protocol",
        "measure request carried no samples"),
}


@dataclass(frozen=True)
class FailureDetail:
    """A classified failure handed to the recovery policy (the FailureDetail ABI).

    ``defined`` is True iff ``code`` is a known :data:`FAILURE_MODES` entry; only
    defined failures are ever adjudicated. ``recoverable`` /
    ``false_positive_prone`` are copied from the catalogue for a defined failure
    (both False for an undefined one -- an unknown fault is treated as neither
    recoverable nor a false alarm).
    """

    code: str
    reason: str                 # the driver's terminal reason string (with suffix)
    defined: bool
    recoverable: bool
    false_positive_prone: bool
    category: str
    well_id: str | None = None
    detail: str = ""

    @classmethod
    def classify(
        cls, code: str, reason: str, *, well_id: str | None = None
    ) -> FailureDetail:
        """Build a FailureDetail by looking ``code`` up in :data:`FAILURE_MODES`."""
        mode = FAILURE_MODES.get(code)
        if mode is None:
            return cls(
                code=code, reason=reason, defined=False, recoverable=False,
                false_positive_prone=False, category="undefined",
                well_id=well_id,
                detail=f"undefined failure code {code!r} -- fail-closed",
            )
        return cls(
            code=code, reason=reason, defined=True, recoverable=mode.recoverable,
            false_positive_prone=mode.false_positive_prone, category=mode.category,
            well_id=well_id, detail=mode.detail,
        )


@runtime_checkable
class RecoveryPolicy(Protocol):
    """The simulate<->real seam. ``decide`` maps a DEFINED failure to an action.

    Implementations must never be consulted for an undefined failure (the driver
    fail-closes those upstream), and must never return an action that recovers a
    failure whose ``recoverable`` bit is False.
    """

    name: str

    def decide(self, failure: FailureDetail) -> RecoveryAction:
        ...


@dataclass(frozen=True)
class NeverRecover:
    """Default (``simulate`` semantics): every defined error aborts the run.

    This makes the seven-state driver behave exactly like the prior six-state one
    -- ``AWAITING_RECOVERY`` is never entered -- so the existing wet suite is the
    regression anchor.
    """

    name: str = "never_recover"

    def decide(self, failure: FailureDetail) -> RecoveryAction:
        return RecoveryAction.ABORT


@dataclass(frozen=True)
class WaitForRecovery:
    """Real-device leg: a recoverable defined error pauses into AWAITING_RECOVERY.

    The pause is resolved OUT OF BAND through the driver's explicit ``recover()``
    (resume, the human fixed it) or ``abandon()`` (give up -> ABORTED) API -- the
    seam a real instrument uses. A non-recoverable defined error still aborts.
    """

    name: str = "wait_for_recovery"

    def decide(self, failure: FailureDetail) -> RecoveryAction:
        if failure.recoverable:
            return RecoveryAction.AWAIT_HUMAN
        return RecoveryAction.ABORT


@dataclass(frozen=True)
class AssumeFalsePositive:
    """Continue past a false-positive-prone sensor flag; abort everything else.

    Isomorph of the expos QC false-positive adjudication (see module docstring and
    Opentrons ``ASSUME_FALSE_POSITIVE_AND_CONTINUE``): the flag is adjudicated a
    false alarm and the run proceeds. Applies ONLY to errors the catalogue marks
    ``false_positive_prone``; any other defined error -> ABORT (never a blanket
    "ignore all errors").
    """

    name: str = "assume_false_positive"

    def decide(self, failure: FailureDetail) -> RecoveryAction:
        if failure.false_positive_prone:
            return RecoveryAction.ASSUME_FALSE_POSITIVE
        return RecoveryAction.ABORT


def failure_modes_catalogue() -> list[dict[str, Any]]:
    """Machine-readable failure-mode list for the ``failure_modes()`` contract verb."""
    return [
        {
            "code": m.code,
            "recoverable": m.recoverable,
            "false_positive_prone": m.false_positive_prone,
            "category": m.category,
            "detail": m.detail,
        }
        for m in FAILURE_MODES.values()
    ]
