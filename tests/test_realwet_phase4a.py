"""M23 Real-Wet Readiness Contract -- Phase 4-A (A domain): the physical-dispatch
ORCHESTRATION facade (``expos.adapters.wet.orchestration``).

Discriminative-first (W8 pattern): every guard has a test that turns red if the guard is
removed (KILL note inline). Under test is the Phase 4-A convergence face mcl consumes:

  * :func:`dispatch_round` -- the round front door (positive: all CONFIRMED ->
    committed_results complete).
  * the STRUCTURAL commit-before-observation gate -- a non-COMMITTED action is listed in
    ``non_committed`` and carries NO observed-value field (mcl cannot read its "observation").
  * :func:`resume_round` -- the crash-resume face, idempotent at round granularity.
  * the append-only event-emission orderings the facade preserves (PENDING event before
    I/O; COMMITTED event before the committed_results it feeds).
  * the out-of-band recovery port (:func:`recover_action` / :func:`cancel_action`).
"""

from __future__ import annotations

from dataclasses import fields

from expos.adapters.wet.action_ledger import (
    ActionLedger,
    ActionState,
    VolumeLedger,
)
from expos.adapters.wet.fake_physical import (
    Behaviour,
    BehaviourSpec,
    FakePhysicalBackend,
    Scenario,
)
from expos.adapters.wet.orchestration import (
    DispatchRoundResult,
    NonCommittedAction,
    cancel_action,
    dispatch_round,
    recover_action,
    resume_round,
)
from expos.adapters.wet.recovery import WaitForRecovery

# ---- helpers (mirroring test_realwet_phase3) --------------------------------

def _vol(source_uL: float = 15000.0) -> VolumeLedger:
    return VolumeLedger(capacities={"RSV": 1e9}, initial={"RSV": source_uL})


def _scenario(actions, behaviours=None) -> Scenario:
    return Scenario(name="sc", actions=actions, behaviours=behaviours or {})


def _one(action_id="a1", dest="B2", volume=150.0):
    return [{"action_id": action_id, "dest": dest, "volume": volume}]


# ======================================================================
# dispatch_round -- positive facade: all CONFIRMED -> committed_results complete
# ======================================================================

def test_dispatch_round_all_confirmed_committed_results_complete(tmp_path):
    """All three transfers CONFIRM -> every one lands in committed_results with its observed
    volume + evidence id; non_committed is empty; committed_by_well maps each plate well.
    Red line: the happy-path face returns the full observed set mcl needs. KILL: drop the
    COMMITTED branch in _from_records and committed_results would be empty."""
    sc = _scenario([{"action_id": "a-B2", "dest": "B2", "volume": 150.0},
                    {"action_id": "a-C2", "dest": "C2", "volume": 150.0},
                    {"action_id": "a-D2", "dest": "D2", "volume": 150.0}])
    led = ActionLedger(tmp_path, volume=_vol())
    res = dispatch_round(sc.planned(), FakePhysicalBackend(sc), led)

    assert isinstance(res, DispatchRoundResult)
    assert res.all_committed is True
    assert res.non_committed == []
    by_well = res.committed_by_well()
    assert set(by_well) == {"B2", "C2", "D2"}
    for well in ("B2", "C2", "D2"):
        cr = by_well[well]
        assert cr.observed_volume_ul == 150.0
        assert cr.requested_volume_ul == 150.0
        assert cr.sensed_evidence_id is not None          # evidence id carried through


# ======================================================================
# the STRUCTURAL commit-before-observation gate
# ======================================================================

def test_dispatch_round_mismatch_gated_out_no_observation_field(tmp_path):
    """A round with one CONFIRMED + one MISMATCH: the confirmed well is in committed_results;
    the mismatched action is in non_committed (AWAITING_RECOVERY) and is STRUCTURALLY absent
    from any observed channel -- NonCommittedAction has no observed-value field at all, so mcl
    cannot read its "observation". Red line: commit-before-observation is structural, not a
    convention. KILL: add an observed field to NonCommittedAction (or route the mismatch into
    committed_results) and this test fails on both the field-absence and the membership."""
    sc = _scenario([{"action_id": "ok", "dest": "B2", "volume": 150.0},
                    {"action_id": "bad", "dest": "C2", "volume": 150.0}],
                   behaviours={"C2": [BehaviourSpec(1, Behaviour.MISMATCH_DEFINED,
                                                    code="E_DEVICE")]})
    led = ActionLedger(tmp_path, volume=_vol(), policy=WaitForRecovery())
    res = dispatch_round(sc.planned(), FakePhysicalBackend(sc), led)

    assert res.committed_by_action().keys() == {"ok"}                 # only the confirmed one
    assert "C2" not in res.committed_by_well()                        # no observed for the bad well
    nc = {n.action_id: n for n in res.non_committed}
    assert nc.keys() == {"bad"}
    assert nc["bad"].state == ActionState.AWAITING_RECOVERY.value
    # STRUCTURAL gate: the non-committed record type carries no observed-value field.
    field_names = {f.name for f in fields(NonCommittedAction)}
    assert "observed_volume_ul" not in field_names
    assert not hasattr(nc["bad"], "observed_volume_ul")


# ======================================================================
# resume_round -- idempotent at round granularity
# ======================================================================

def test_resume_round_idempotent_committed_skip(tmp_path):
    """After a round fully commits, a crash-resume over a FRESH ledger + FRESH backend SKIPS
    the committed actions (neither re-sensed nor re-dispatched) and re-derives the identical
    committed_results; a second resume is a no-op with the same result. Red line: round-level
    resume is idempotent -- no duplicate physical send, stable observed set. KILL: re-sense a
    committed action on resume and the fresh backend's sensed_log would be non-empty."""
    sc = _scenario(_one("done"))
    led = ActionLedger(tmp_path, volume=_vol())
    first = dispatch_round(sc.planned(), FakePhysicalBackend(sc), led)
    assert first.all_committed

    be2 = FakePhysicalBackend(sc)
    led2 = ActionLedger(tmp_path, volume=_vol())
    resumed = resume_round(sc.planned(), be2, led2)
    assert be2.sensed_log == []                                       # NOT re-sensed
    assert resumed.committed_by_action().keys() == {"done"}
    assert resumed.committed_by_action()["done"].observed_volume_ul == 150.0

    # second resume over the same dir with another fresh backend: still idempotent
    be3 = FakePhysicalBackend(sc)
    again = resume_round(sc.planned(), be3, ActionLedger(tmp_path, volume=_vol()))
    assert be3.sensed_log == []
    assert again.committed_by_action()["done"].observed_volume_ul == 150.0


# ======================================================================
# append-only event-emission orderings the facade preserves
# ======================================================================

def test_pending_event_precedes_io_at_facade(tmp_path):
    """Re-prove at the facade layer: the ``-> PENDING`` transition event is appended BEFORE
    the hardware-I/O reply note (PENDING-before-I/O -- a crash-visible pending, never a
    silent lost send). Red line inherited from the ledger, re-asserted through
    dispatch_round. KILL: run io_call before persisting PENDING and this ordering inverts."""
    sc = _scenario(_one("a1"))
    led = ActionLedger(tmp_path, volume=_vol())
    dispatch_round(sc.planned(), FakePhysicalBackend(sc), led)

    ev = led.events
    pending_idx = next(i for i, e in enumerate(ev)
                       if e.get("from") == "PLANNED" and e.get("to") == "PENDING")
    io_idx = next(i for i, e in enumerate(ev)
                  if str(e.get("note", "")).startswith("driver_reply"))
    assert pending_idx < io_idx                                      # PENDING persisted first
    assert ev[io_idx]["io_ok"] is True                               # the I/O reply WAS recorded


def test_committed_event_precedes_committed_results(tmp_path):
    """Every CommittedResult is derived from an already-appended ``-> COMMITTED`` event: the
    event is the append-only source, the result the derivative (so no result can exist before
    its event is on the log). Red line: committed_results never precede their COMMITTED event.
    KILL: build committed_results from the sensed evidence directly (bypassing the ledger
    commit) and there would be no matching COMMITTED event to find."""
    sc = _scenario([{"action_id": "a-B2", "dest": "B2", "volume": 150.0},
                    {"action_id": "a-C2", "dest": "C2", "volume": 150.0}])
    led = ActionLedger(tmp_path, volume=_vol())
    res = dispatch_round(sc.planned(), FakePhysicalBackend(sc), led)

    committed_events = {e["action_id"] for e in led.events if e.get("to") == "COMMITTED"}
    for cr in res.committed_results:
        assert cr.action_id in committed_events                      # event exists (was appended)


# ======================================================================
# the out-of-band recovery port (recover_action / cancel_action)
# ======================================================================

def test_out_of_band_recover_and_cancel_port(tmp_path):
    """A mismatch parks the action in AWAITING_RECOVERY (dispatch_round does NOT auto-retry).
    The out-of-band port resolves it: recover_action bumps the attempt and re-senses (here the
    instrument is now fixed -> COMMITTED on attempt 2); a separate parked action is terminated
    by cancel_action -> ABORTED. Red line: recovery is out of band, not folded into the round.
    KILL: auto-recover inside dispatch_round and the parked assertion below would already be
    COMMITTED before recover_action is called."""
    # (a) recover path: attempt 1 MISMATCH, attempt 2 CONFIRM
    sc = _scenario(_one("fix"), behaviours={"B2": [
        BehaviourSpec(1, Behaviour.MISMATCH_DEFINED, code="E_DEVICE"),
        BehaviourSpec(2, Behaviour.CONFIRM_EXACT)]})
    led = ActionLedger(tmp_path / "rec", volume=_vol(), policy=WaitForRecovery())
    be = FakePhysicalBackend(sc)
    res = dispatch_round(sc.planned(), be, led)
    assert res.non_committed[0].state == ActionState.AWAITING_RECOVERY.value  # NOT auto-retried
    rec = recover_action(sc.planned()[0], be, led)
    assert rec.state is ActionState.COMMITTED
    assert rec.attempt == 2

    # (b) cancel path: a parked action is aborted out of band
    sc2 = _scenario(_one("kill"), behaviours={"B2": [
        BehaviourSpec(1, Behaviour.MISMATCH_DEFINED, code="E_DEVICE")]})
    led2 = ActionLedger(tmp_path / "can", volume=_vol(), policy=WaitForRecovery())
    dispatch_round(sc2.planned(), FakePhysicalBackend(sc2), led2)
    aborted = cancel_action("kill", led2)
    assert aborted.state is ActionState.ABORTED
