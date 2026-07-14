"""M23 Real-Wet Readiness Contract — Phase 1+2 transaction facet (A domain).

Discriminative-first (the W8 pattern): every red line has a test that turns red if
the guard is removed (KILL note inline). This suite exercises the physical-action
transaction facet (``action_ledger``) that lives BENEATH the WetDriver lifecycle:
each physical action (a liquid transfer) must be recoverable, re-readable,
committable, and non-replayable before real hardware is wired in.

  §1  five pre-dispatch rejections (Phase 2 volume ledger): negative volume /
      source-insufficient / destination-overflow / duplicate idempotency key /
      expected-pre-state mismatch.
  §2  ordering: PENDING is persisted BEFORE the hardware I/O runs.
  §3  OK != COMMITTED: a driver OK reply never commits alone (stays PENDING).
  §4  sensed-state gate: CONFIRMED -> COMMITTED; MISMATCH -> AWAITING_RECOVERY
      (WaitForRecovery) / ROLLED_BACK (NeverRecover); UNOBSERVED -> stays PENDING.
  §5  observedGeneration: a stale read-back (for_attempt != current) never commits.
  §6  cancel in AWAITING_RECOVERY == ABORTED (112 即裁二 delegation).
  §7  resume: a fresh ledger over the same dir refuses to re-dispatch a known
      action_id (no silent re-send).
  §8  requested / observed stored separately (request never overwritten).
  §9  append-only: a rewritten historical line is detected by the hash chain.
"""

from __future__ import annotations

import json

import pytest

from expos.adapters.wet.action_ledger import (
    ActionLedger,
    ActionPrecheckError,
    ActionState,
    ActionStateError,
    IdempotencyError,
    LedgerCorruptError,
    LedgerTamperError,
    PlannedAction,
    SensedEvidence,
    SensedOutcome,
    VolumeLedger,
)
from expos.adapters.wet.recovery import NeverRecover, WaitForRecovery


# ---- helpers ----------------------------------------------------------------

# A reservoir source "RSV" seeded with plenty of volume; destinations are plate
# wells (capacity 360 uL from the external labware plate96.json).
def _volume(*, source_uL: float = 15000.0, seed: dict | None = None) -> VolumeLedger:
    initial = {"RSV": source_uL}
    if seed:
        initial.update(seed)
    # RSV is off-plate: give it an explicit (large) capacity so it is a known well.
    return VolumeLedger(capacities={"RSV": 1e9}, initial=initial)


def _action(
    action_id: str = "act-1",
    *,
    source: str = "RSV",
    dest: str = "B2",
    volume: float = 150.0,
    pre_state: dict | None = None,
    round_id: int = 0,
) -> PlannedAction:
    return PlannedAction(
        action_id=action_id, round_id=round_id, spec_fingerprint="spec-abc",
        source_well=source, destination_well=dest, requested_volume_ul=volume,
        backend_id="sim-reader-0", expected_pre_state=pre_state or {},
        expected_post_state={},
    )


def _confirm_evidence(attempt: int = 1, observed: float = 150.0) -> SensedEvidence:
    return SensedEvidence(evidence_id="ev-ok", for_attempt=attempt,
                          outcome=SensedOutcome.CONFIRMED, observed_volume_ul=observed)


def _mismatch_evidence(attempt: int = 1, code: str = "E_DEVICE") -> SensedEvidence:
    return SensedEvidence(evidence_id="ev-bad", for_attempt=attempt,
                          outcome=SensedOutcome.MISMATCH, code=code)


# ======================================================================
# §1 — the five pre-dispatch rejections (Phase 2)
# ======================================================================

def test_reject_negative_volume(tmp_path):
    led = ActionLedger(tmp_path, volume=_volume())
    with pytest.raises(ActionPrecheckError) as ei:
        led.dispatch(_action(volume=-5.0))
    assert ei.value.code == "negative_volume"


def test_reject_source_insufficient(tmp_path):
    """KILL: drop the source-remaining check and this over-draw would dispatch."""
    led = ActionLedger(tmp_path, volume=_volume(source_uL=100.0))
    with pytest.raises(ActionPrecheckError) as ei:
        led.dispatch(_action(volume=150.0))          # source holds only 100
    assert ei.value.code == "source_insufficient"


def test_reject_destination_overflow(tmp_path):
    """Destination capacity is sourced from the external labware (plate96.json,
    360 uL). KILL: drop the overflow check and a well would be over-filled."""
    led = ActionLedger(tmp_path, volume=_volume(seed={"B2": 350.0}))
    with pytest.raises(ActionPrecheckError) as ei:
        led.dispatch(_action(dest="B2", volume=50.0))  # 350 + 50 = 400 > 360
    assert ei.value.code == "destination_overflow"


def test_idempotency_key_reuse_different_params_rejected(tmp_path):
    """The fifth rejection, Stripe form (REF-T point 2): same action_id (idempotency
    key) with DIFFERENT params -> loud IdempotencyError. KILL: drop the fingerprint
    comparison and a divergent re-use would silently overwrite/mis-dispatch."""
    led = ActionLedger(tmp_path, volume=_volume())
    led.dispatch(_action("key1", volume=150.0))
    with pytest.raises(IdempotencyError) as ei:
        led.dispatch(_action("key1", volume=120.0))     # same key, different volume
    assert ei.value.code == "idempotency_key_reuse"


def test_idempotent_replay_same_params_skips_and_does_not_resend(tmp_path):
    """Same action_id + same params is an idempotent replay: the existing record is
    returned and the I/O is NOT re-sent (no silent re-send). KILL: re-run io_call on a
    known key and the re-send counter climbs."""
    led = ActionLedger(tmp_path, volume=_volume())
    sends = {"n": 0}

    def io() -> bool:
        sends["n"] += 1
        return True

    first = led.dispatch(_action("key2", volume=150.0), io)
    again = led.dispatch(_action("key2", volume=150.0), io)   # identical replay
    assert sends["n"] == 1                                    # I/O issued exactly once
    assert again is first and again.state is ActionState.PENDING


def test_reject_expected_pre_state_mismatch(tmp_path):
    """The optimistic-concurrency guard: a plan whose expected pre-state disagrees
    with the ledger's current state is rejected (stale/reordered plan). KILL: drop
    the pre-state comparison and a stale plan would dispatch onto a changed world."""
    led = ActionLedger(tmp_path, volume=_volume(seed={"B2": 20.0}))
    # plan assumed B2 empty, but the ledger holds 20 uL
    with pytest.raises(ActionPrecheckError) as ei:
        led.dispatch(_action(dest="B2", volume=50.0, pre_state={"B2": 0.0}))
    assert ei.value.code == "pre_state_mismatch"


# ======================================================================
# §2 — PENDING is persisted BEFORE the hardware I/O
# ======================================================================

def test_pending_persisted_before_io(tmp_path):
    """Ordering evidence: when io_call runs, the PENDING line is ALREADY on disk.
    KILL: move the PENDING append to after io_call and this assertion fails."""
    led = ActionLedger(tmp_path, volume=_volume())
    seen: dict = {}

    def io_call() -> bool:
        # read the on-disk ledger at the exact moment the I/O is issued
        lines = [json.loads(ln) for ln in (tmp_path / "action_ledger.jsonl")
                 .read_text().splitlines() if ln.strip()]
        seen["states"] = [(r["action_id"], r["to"]) for r in lines]
        return True

    led.dispatch(_action("io-order"), io_call)
    assert ("io-order", "PENDING") in seen["states"]           # pending was on disk
    assert ("io-order", "COMMITTED") not in seen["states"]     # and not yet committed


# ======================================================================
# §3 — OK reply never commits alone
# ======================================================================

def test_ok_reply_never_commits_alone(tmp_path):
    """A driver OK reply (io_call True) leaves the action PENDING, never COMMITTED —
    COMMITTED is gated ONLY by a sensed-state confirmation. KILL: commit on io_ok and
    this flips to COMMITTED."""
    led = ActionLedger(tmp_path, volume=_volume())
    rec = led.dispatch(_action("ok-not-commit"), lambda: True)
    assert rec.state is ActionState.PENDING
    assert rec.observed_volume_ul is None


# ======================================================================
# §4 — the sensed-state commit gate
# ======================================================================

def test_sensed_confirmation_commits(tmp_path):
    """CONFIRMED sensed evidence drives PENDING -> COMMITTED and applies the observed
    volume to the volume ledger."""
    vol = _volume()
    led = ActionLedger(tmp_path, volume=vol)
    led.dispatch(_action("commit-me", volume=150.0), lambda: True)
    rec = led.confirm("commit-me", _confirm_evidence(attempt=1, observed=150.0))
    assert rec.state is ActionState.COMMITTED
    assert rec.disposition == "committed_by_sensed_state"
    assert vol.current("B2") == 150.0                          # observed applied
    assert vol.current("RSV") == 15000.0 - 150.0               # source drawn down


def test_sensed_mismatch_awaits_recovery_under_wait_policy(tmp_path):
    """A MISMATCH under WaitForRecovery -> AWAITING_RECOVERY (recoverable defined
    error routes to AWAIT_HUMAN). KILL: skip the policy and it would roll back."""
    led = ActionLedger(tmp_path, volume=_volume(), policy=WaitForRecovery())
    led.dispatch(_action("await-me"), lambda: True)
    rec = led.confirm("await-me", _mismatch_evidence(attempt=1, code="E_DEVICE"))
    assert rec.state is ActionState.AWAITING_RECOVERY


def test_sensed_mismatch_rolls_back_under_never_recover(tmp_path):
    """Under the default NeverRecover, a MISMATCH -> ROLLED_BACK (defined error
    aborts; nothing was committed so there is nothing to physically revert)."""
    vol = _volume()
    led = ActionLedger(tmp_path, volume=vol, policy=NeverRecover())
    led.dispatch(_action("rollback-me", volume=150.0), lambda: True)
    rec = led.confirm("rollback-me", _mismatch_evidence(attempt=1, code="E_DEVICE"))
    assert rec.state is ActionState.ROLLED_BACK
    assert vol.current("B2") == 0.0                            # never committed


def test_unobserved_stays_pending_not_awaiting(tmp_path):
    """Three-state observation (INDEX_M23_RECONCILE point 4): an UNOBSERVED read-back
    is Unknown, NOT a failure — the action stays PENDING and does NOT trip recovery.
    KILL: treat 'not observed' as a failure and it would enter AWAITING_RECOVERY."""
    led = ActionLedger(tmp_path, volume=_volume(), policy=WaitForRecovery())
    led.dispatch(_action("unobs"), lambda: True)
    rec = led.confirm("unobs", SensedEvidence(
        evidence_id="ev-none", for_attempt=1, outcome=SensedOutcome.UNOBSERVED))
    assert rec.state is ActionState.PENDING


# ======================================================================
# §5 — observedGeneration: stale read-back never commits
# ======================================================================

def test_stale_readback_never_commits(tmp_path):
    """observedGeneration gate (INDEX_M23_RECONCILE point 1): a CONFIRMED read-back
    stamped for a DIFFERENT attempt (for_attempt=0 while the action is on attempt 1)
    is stale — it must NOT commit; the action stays PENDING (Unknown). A fresh,
    correctly-stamped confirmation then commits. KILL: drop the for_attempt check and
    the stale reading would falsely COMMIT (the real-wet buffered-residue race)."""
    led = ActionLedger(tmp_path, volume=_volume())
    led.dispatch(_action("stale"), lambda: True)               # attempt -> 1
    rec = led.confirm("stale", _confirm_evidence(attempt=0, observed=150.0))
    assert rec.state is ActionState.PENDING                    # stale: NOT committed
    assert rec.observed_volume_ul is None
    # a correctly-stamped confirmation now commits
    rec = led.confirm("stale", _confirm_evidence(attempt=1, observed=150.0))
    assert rec.state is ActionState.COMMITTED


# ======================================================================
# §6 — cancel in AWAITING_RECOVERY == ABORTED (112 即裁二)
# ======================================================================

def test_cancel_in_awaiting_recovery_is_aborted(tmp_path):
    """A cancel arriving while the action is paused in AWAITING_RECOVERY terminates it
    ABORTED — reusing the driver's paused-cancel == abandon precedent. KILL: route the
    cancel anywhere but ABORTED and this flips."""
    led = ActionLedger(tmp_path, volume=_volume(), policy=WaitForRecovery())
    led.dispatch(_action("cancel-me"), lambda: True)
    led.confirm("cancel-me", _mismatch_evidence(attempt=1, code="E_DEVICE"))
    assert led.record("cancel-me").state is ActionState.AWAITING_RECOVERY
    rec = led.cancel("cancel-me")
    assert rec.state is ActionState.ABORTED
    assert "canceled_while_awaiting_recovery" in (rec.disposition or "")


# ======================================================================
# §7 — resume: no silent re-send of a known action
# ======================================================================

def test_resume_replays_and_does_not_resend_known_action(tmp_path):
    """A fresh ledger over the same run dir replays the (hash-verified) log; a resume
    that re-dispatches the SAME known action (same params) is an idempotent skip that
    re-sends NOTHING -- the red line "resume 不得静默重发已 PENDING/COMMITTED 动作".
    KILL: drop the replay/fingerprint guard and the resumed ledger re-issues the I/O."""
    led = ActionLedger(tmp_path, volume=_volume())
    led.dispatch(_action("resume-key"), lambda: True)
    # simulate a resume: brand-new ledger object over the same directory
    led2 = ActionLedger(tmp_path, volume=_volume())
    assert led2.record("resume-key").state is ActionState.PENDING   # replayed from log
    sends = {"n": 0}
    rec = led2.dispatch(_action("resume-key"), lambda: sends.__setitem__("n", 1) or True)
    assert sends["n"] == 0                                          # nothing re-sent
    assert rec.state is ActionState.PENDING
    # a resumed re-dispatch with DIFFERENT params is still a loud IdempotencyError
    with pytest.raises(IdempotencyError):
        led2.dispatch(_action("resume-key", volume=99.0), lambda: True)


def test_truncated_ledger_refuses_resume(tmp_path):
    """A ledger whose tail line is truncated (crash mid-write) is untrusted: resume
    fails closed with LedgerCorruptError rather than silently rebuilding an empty
    ledger (INDEX_M23_OTENGINE fail-closed). KILL: tolerate the torn tail and a
    half-written physical-action history would be silently accepted."""
    led = ActionLedger(tmp_path, volume=_volume())
    led.dispatch(_action("t1"), lambda: True)
    path = tmp_path / "action_ledger.jsonl"
    data = path.read_text()
    path.write_text(data + '{"seq": 99, "prev_sha": "x", "kind": "phys')  # torn tail
    with pytest.raises(LedgerCorruptError):
        ActionLedger(tmp_path, volume=_volume())


# ======================================================================
# §8 — requested / observed stored separately
# ======================================================================

def test_requested_and_observed_stored_separately(tmp_path):
    """The read-back's observed volume differs from the request; BOTH are retained —
    the observed value NEVER overwrites the original requested value (write-strict,
    k8s spec/status split). KILL: write observed onto requested and the request is
    lost."""
    led = ActionLedger(tmp_path, volume=_volume())
    led.dispatch(_action("split", volume=150.0), lambda: True)
    rec = led.confirm("split", _confirm_evidence(attempt=1, observed=147.3))
    assert rec.requested_volume_ul == 150.0        # request preserved
    assert rec.observed_volume_ul == 147.3         # observed stored distinctly
    assert rec.requested_volume_ul != rec.observed_volume_ul


# ======================================================================
# §9 — append-only: rewritten historical line is detected
# ======================================================================

def test_ledger_is_append_only_tamper_detected(tmp_path):
    """Every ledger line carries a hash chain; rewriting a historical line breaks its
    content hash (and every subsequent prev_sha). verify() detects it loudly. KILL:
    drop the hash chain and a silently-edited history would pass verification."""
    led = ActionLedger(tmp_path, volume=_volume())
    led.dispatch(_action("t1"), lambda: True)
    led.dispatch(_action("t2"), lambda: True)
    led.verify()                                   # clean chain verifies

    path = tmp_path / "action_ledger.jsonl"
    lines = path.read_text().splitlines()
    # tamper a historical line: bump a requested volume in the FIRST record
    rec0 = json.loads(lines[0])
    rec0["requested_volume_ul"] = 999.0            # line_sha left stale on purpose
    lines[0] = json.dumps(rec0)
    path.write_text("\n".join(lines) + "\n")

    with pytest.raises(LedgerTamperError):
        ActionLedger(tmp_path, volume=_volume()).verify()


# ======================================================================
# §10 — facet invariants P1 / P2 / P5 (REF-T)
# ======================================================================

def test_illegal_transition_rejected_loudly(tmp_path):
    """P1 state closure: an edge not in the legal table is rejected loudly. A COMMITTED
    action cannot roll back (COMMITTED is terminal). KILL: drop the legality check in
    _transition and this silent corruption goes unnoticed."""
    led = ActionLedger(tmp_path, volume=_volume())
    led.dispatch(_action("p1"), lambda: True)
    led.confirm("p1", _confirm_evidence(attempt=1, observed=150.0))
    rec = led.record("p1")
    assert rec.state is ActionState.COMMITTED
    with pytest.raises(ActionStateError, match="illegal action transition"):
        led._transition(rec, ActionState.ROLLED_BACK)


def test_committed_is_terminal(tmp_path):
    """P2 mutual exclusion ¬(COMMITTED ∧ VOIDED): COMMITTED has no out-edges, so an
    action can never be both committed and rolled-back/aborted."""
    from expos.adapters.wet.action_ledger import _LEGAL_TRANSITIONS
    assert _LEGAL_TRANSITIONS[ActionState.COMMITTED] == frozenset()
    assert _LEGAL_TRANSITIONS[ActionState.ROLLED_BACK] == frozenset()
    assert _LEGAL_TRANSITIONS[ActionState.ABORTED] == frozenset()


def test_volume_ledger_conserves(tmp_path):
    """P5 conservation: a committed transfer's double-entry legs sum to zero -- the
    running balance is conserved (net moved across all legs == 0). KILL: post an
    unbalanced leg and _post raises / net_moved != 0."""
    vol = _volume()
    led = ActionLedger(tmp_path, volume=vol)
    led.dispatch(_action("c1", source="RSV", dest="B2", volume=150.0), lambda: True)
    led.confirm("c1", _confirm_evidence(attempt=1, observed=150.0))
    # source lost exactly what destination gained; the whole ledger nets to zero
    assert abs(vol.net_moved()) < 1e-9
    assert vol.current("RSV") + vol.current("B2") == 15000.0
