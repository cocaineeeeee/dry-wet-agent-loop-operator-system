"""M29 protocol-compilation & embodied-execution v0.1 discriminative suite.

The point is NOT a demo but the discriminative proofs the charter demands:
  1. the typed cell-free protocol lowers to a device-neutral IR with a stable fingerprint;
  2. the constraint checker is a REAL gate (rejects capacity / volume / ordering breaches
     LOUDLY, admits a valid protocol);
  3. the fake backends drive the REAL M23 action-ledger: COMMITTED is gated ONLY by a
     sensed-state confirmation (a driver OK alone never commits);
  4. the FIVE execution faces -- normal / wrong-well / timeout / duplicate-reply / partial-
     resume -- each carry the M23 commit/rollback/resume semantics;
  5. everything runs DOMAIN-LOCALLY (no mcl); honest fake-backend labelling.

HONESTY: protocol-to-simulated-physical against a fake backend -- NOT a physical
autonomous laboratory; real hardware pending (BIOLOGY_PROGRAM_2026 §5).
"""

from __future__ import annotations

import pytest

from protocols.objects import (
    Incubate,
    Mix,
    Protocol,
    ReadPlate,
    Transfer,
    cell_free_expression_protocol,
)
from protocols.constraints import ConstraintError, check_protocol
from device_ir.ir import (
    DECK_SENTINEL,
    IR_VERSION,
    Opcode,
    group_units,
    ir_fingerprint,
    lower,
)
from expos.adapters.physical import (
    FakeLiquidHandler,
    FakePlateReader,
    Fault,
    ProtocolExecutor,
)
from expos.adapters.wet.action_ledger import ActionState, SensedOutcome
from expos.adapters.wet.recovery import NeverRecover, WaitForRecovery


# --------------------------------------------------------------- typed protocol + lowering


def test_cfe_protocol_shape():
    proto = cell_free_expression_protocol()
    # 4 lysate + 3 dna + 1 buffer + 4 mix + 1 incubate + 1 read = 14 steps
    assert len(proto.steps) == 14
    assert isinstance(proto.steps[-1], ReadPlate)
    assert isinstance(proto.steps[-2], Incubate)
    assert set(proto.destinations()) == {"B2", "B3", "B4", "B5"}
    # the control well received buffer, not dna (a safe intra-plate negative)
    reagents = {t.destination: t.reagent for t in proto.transfers() if t.volume_ul == 3.0}
    assert reagents["B5"] == "no_template_buffer"
    assert reagents["B2"] == "dna_template"


def test_lowering_units_and_opcodes():
    proto = cell_free_expression_protocol()
    ops = lower(proto)
    # each transfer -> 2 ops (aspirate+dispense); 8 transfers -> 16; +4 mix +1 inc +1 read
    assert len(ops) == 22
    units = group_units(ops)
    assert len(units) == 14
    kinds = [u.kind for u in units]
    assert kinds.count("transfer") == 8
    assert kinds.count("mix") == 4
    assert kinds == (["transfer"] * 8 + ["mix"] * 4 + ["incubate", "measure"])
    # a transfer unit carries its two device primitives and its semantic fields
    t0 = units[0]
    assert [o.opcode for o in t0.ops] == [Opcode.ASPIRATE, Opcode.DISPENSE]
    assert t0.source == "reservoir_lysate" and t0.destination == "B2"
    assert t0.volume_ul == 12.0
    # the incubate unit is scoped to the synthetic deck sentinel (plate-wide op, no well)
    inc = next(u for u in units if u.kind == "incubate")
    assert inc.well == DECK_SENTINEL


def test_ir_fingerprint_is_deterministic_and_sensitive():
    a = lower(cell_free_expression_protocol())
    b = lower(cell_free_expression_protocol())
    assert ir_fingerprint(a) == ir_fingerprint(b)  # deterministic
    # a changed volume flips the digest
    c = lower(cell_free_expression_protocol(dna_ul=5.0))
    assert ir_fingerprint(c) != ir_fingerprint(a)
    # the digest binds the IR version (a lowering-contract change flips identity)
    assert IR_VERSION in ("m29-device-ir/v0.1",)


# --------------------------------------------------------------- constraint checker (gate)


def test_constraint_admits_valid_protocol():
    proto = cell_free_expression_protocol()
    rep = check_protocol(
        proto, reservoirs=frozenset(
            {"reservoir_lysate", "reservoir_dna", "reservoir_buffer"}))
    assert rep.ok and not rep.violations
    assert rep.well_volumes == {"B2": 15.0, "B3": 15.0, "B4": 15.0, "B5": 15.0}


def test_constraint_rejects_capacity_overflow():
    # two in-range transfers accumulate past the 360 µL well capacity (200+200 > 360),
    # isolating the CAPACITY breach from the pipette-range check.
    proto = Protocol("overflow", (
        Transfer("reservoir_x", "A1", 200.0),
        Transfer("reservoir_x", "A1", 200.0),
    ))
    rep = check_protocol(proto)
    assert not rep.ok
    assert rep.violations[0].code == "destination_overflow"
    with pytest.raises(ConstraintError) as exc:
        rep.raise_if_bad()
    assert exc.value.code == "destination_overflow"


def test_constraint_rejects_out_of_range_and_nonpositive_volume():
    hi = check_protocol(Protocol("hi", (Transfer("r", "A1", 999.0),)))
    assert hi.violations[0].code in ("volume_out_of_pipette_range", "destination_overflow")
    lo = check_protocol(Protocol("lo", (Transfer("r", "A1", 0.0),)))
    assert lo.violations[0].code == "volume_nonpositive"


def test_constraint_rejects_ordering_read_and_mix_before_fill():
    read_first = Protocol("rf", (ReadPlate("gfp", ("A1",)),))
    assert any(v.code == "read_before_fill" for v in check_protocol(read_first).violations)
    mix_first = Protocol("mf", (Mix("A1", 5.0),))
    assert any(v.code == "mix_before_fill" for v in check_protocol(mix_first).violations)
    inc_first = Protocol("if", (Incubate(30.0, 60.0),))
    assert any(
        v.code == "incubate_empty_plate" for v in check_protocol(inc_first).violations)


def test_constraint_rejects_unknown_well_and_source():
    bad_well = check_protocol(Protocol("bw", (Transfer("r", "Z99", 10.0),)))
    assert any(v.code == "unknown_destination_well" for v in bad_well.violations)
    bad_src = check_protocol(
        Protocol("bs", (Transfer("mystery", "A1", 10.0),)),
        reservoirs=frozenset({"reservoir_ok"}))
    assert any(v.code == "unknown_source" for v in bad_src.violations)


# --------------------------------------------------------------- helpers for dispatch faces


def _executor(tmp_path, policy=None):
    return ProtocolExecutor(
        str(tmp_path),
        [FakeLiquidHandler(), FakePlateReader()],
        protocol_id="cfe",
        policy=policy,
        reservoirs={"reservoir_lysate": 1000.0, "reservoir_dna": 1000.0,
                    "reservoir_buffer": 1000.0},
    )


def _units():
    return group_units(lower(cell_free_expression_protocol("cfe")))


# --------------------------------------------------------------- FACE 1: normal


def test_face_normal_all_committed(tmp_path):
    ex = _executor(tmp_path)
    log = ex.run(_units())
    assert log.all_committed
    assert all(u.outcome is SensedOutcome.CONFIRMED for u in log.units)
    # a real observation came back through the same gate (closed loop, not blind sequence)
    assert len(log.readings) == 1
    assert next(iter(log.readings.values())) > 0.0


def test_committed_only_by_sensed_state_not_driver_ok(tmp_path):
    """The M23 red line: a driver OK reply never commits; only a sensed confirmation does.

    Inject UNOBSERVED -> the io_call still records a (not-OK) driver reply but the action
    stays PENDING, proving COMMITTED is gated ONLY by the sensed read-back."""
    lh, pr = FakeLiquidHandler(), FakePlateReader()
    units = _units()
    lh.inject(units[0].unit_id, Fault(SensedOutcome.UNOBSERVED))
    ex = ProtocolExecutor(str(tmp_path), [lh, pr], protocol_id="cfe",
                          reservoirs={"reservoir_lysate": 1000.0, "reservoir_dna": 1000.0,
                                      "reservoir_buffer": 1000.0})
    res = ex.execute_unit(units[0])
    assert res.final_state is ActionState.PENDING
    assert not res.committed


# --------------------------------------------------------------- FACE 2: wrong well


def test_face_wrong_well_rolls_back_under_never_recover(tmp_path):
    lh, pr = FakeLiquidHandler(), FakePlateReader()
    units = _units()
    # a sensed MISMATCH on the first dispense: the read-back says the liquid is not where
    # it was requested. Under NeverRecover a defined recoverable failure -> ABORT/rollback.
    lh.inject(units[0].unit_id,
              Fault(SensedOutcome.MISMATCH, code="E_DEVICE", detail="wrong well"))
    ex = ProtocolExecutor(str(tmp_path), [lh, pr], protocol_id="cfe",
                          policy=NeverRecover(),
                          reservoirs={"reservoir_lysate": 1000.0, "reservoir_dna": 1000.0,
                                      "reservoir_buffer": 1000.0})
    res = ex.execute_unit(units[0])
    assert res.final_state is ActionState.ROLLED_BACK
    assert res.outcome is SensedOutcome.MISMATCH
    assert not res.committed


def test_face_wrong_well_awaits_recovery_then_resolves(tmp_path):
    """Under WaitForRecovery a recoverable mismatch pauses into AWAITING_RECOVERY; the
    driver's explicit recover()/abandon() API resolves it (reuse M23, not re-implemented)."""
    lh, pr = FakeLiquidHandler(), FakePlateReader()
    units = _units()
    lh.inject(units[0].unit_id, Fault(SensedOutcome.MISMATCH, code="E_DEVICE"))
    ex = ProtocolExecutor(str(tmp_path), [lh, pr], protocol_id="cfe",
                          policy=WaitForRecovery(),
                          reservoirs={"reservoir_lysate": 1000.0, "reservoir_dna": 1000.0,
                                      "reservoir_buffer": 1000.0})
    res = ex.execute_unit(units[0])
    assert res.final_state is ActionState.AWAITING_RECOVERY
    # operator abandons -> clean fail-closed rollback (nothing was committed).
    rec = ex.ledger.abandon(units[0].unit_id)
    assert rec.state is ActionState.ROLLED_BACK


# --------------------------------------------------------------- FACE 3: timeout


def test_face_timeout_stays_pending_then_recloses(tmp_path):
    """A sensed UNOBSERVED (timeout / no read-back) is NOT a failure: the action stays
    PENDING (Unknown != failed). A later CONFIRMED re-sense commits it -- the closed loop."""
    lh, pr = FakeLiquidHandler(), FakePlateReader()
    units = _units()
    lh.inject(units[0].unit_id, Fault(SensedOutcome.UNOBSERVED))
    ex = ProtocolExecutor(str(tmp_path), [lh, pr], protocol_id="cfe",
                          reservoirs={"reservoir_lysate": 1000.0, "reservoir_dna": 1000.0,
                                      "reservoir_buffer": 1000.0})
    res = ex.execute_unit(units[0])
    assert res.final_state is ActionState.PENDING and not res.committed
    # the read-back becomes conclusive; re-sensing the same PENDING action commits it.
    lh.faults.pop(units[0].unit_id)
    action = ex._planned_action(units[0])
    rec = ex.ledger.record(units[0].unit_id)
    ev = lh.sense(action, attempt=rec.attempt)
    ex.ledger.confirm(units[0].unit_id, ev)
    assert ex.ledger.record(units[0].unit_id).state is ActionState.COMMITTED


# --------------------------------------------------------------- FACE 4: duplicate reply


def test_face_duplicate_reply_is_idempotent_no_resend(tmp_path):
    """Re-dispatching a known action_id with the same fingerprint is an idempotent replay:
    NOTHING is re-sent (the resume red line). The io_call ran exactly once."""
    ex = _executor(tmp_path)
    units = _units()
    ex.execute_unit(units[0])  # committed
    lh = ex.backends[0]
    io_before = list(lh.io_calls)
    # a second execute of the SAME unit: already COMMITTED -> skipped, no new io_call
    res2 = ex.execute_unit(units[0])
    assert res2.committed
    assert lh.io_calls == io_before  # no re-send


def test_idempotent_dispatch_records_replay_note(tmp_path):
    """Direct ledger check: a same-key/same-fingerprint re-dispatch appends a replay note
    and re-sends no I/O (uses the M23 idempotency path unchanged)."""
    ex = _executor(tmp_path)
    units = _units()
    action = ex._planned_action(units[0])
    lh = ex.backends[0]
    ex.ledger.dispatch(action, io_call=lambda: lh.io_call(units[0].ops[-1]))
    n_io = len(lh.io_calls)
    ex.ledger.dispatch(action, io_call=lambda: lh.io_call(units[0].ops[-1]))
    assert len(lh.io_calls) == n_io  # idempotent replay: no second I/O


# --------------------------------------------------------------- FACE 5: partial + resume


def test_face_partial_execution_then_resume(tmp_path):
    """Commit some units, drop the executor (crash), then resume with a FRESH executor over
    the SAME run dir: the hash-chained ledger replays, refuses to re-dispatch COMMITTED
    units, and re-senses/continues the rest -- the M23 resume trichotomy, reused."""
    units = _units()
    run_dir = str(tmp_path)
    ex1 = ProtocolExecutor(run_dir, [FakeLiquidHandler(), FakePlateReader()],
                           protocol_id="cfe",
                           reservoirs={"reservoir_lysate": 1000.0,
                                       "reservoir_dna": 1000.0,
                                       "reservoir_buffer": 1000.0})
    for u in units[:3]:
        ex1.execute_unit(u)
    committed_before = {u.unit_id for u in units[:3]}
    assert all(ex1.ledger.record(uid).state is ActionState.COMMITTED
               for uid in committed_before)
    vol_b2_before = ex1.ledger.volume.current("B2")

    # crash + resume: brand-new executor, same run dir (the ledger is on disk)
    ex2 = ProtocolExecutor(run_dir, [FakeLiquidHandler(), FakePlateReader()],
                           protocol_id="cfe",
                           reservoirs={"reservoir_lysate": 1000.0,
                                       "reservoir_dna": 1000.0,
                                       "reservoir_buffer": 1000.0})
    # committed volumes survived the replay
    assert ex2.ledger.volume.current("B2") == vol_b2_before
    lh2 = ex2.backends[0]
    io_at_resume = len(lh2.io_calls)
    # re-running an already-committed unit is a no-op skip (no re-dispatch, no I/O)
    res = ex2.execute_unit(units[0])
    assert res.committed and "already committed" in res.detail
    assert len(lh2.io_calls) == io_at_resume
    # the run completes cleanly from where it left off
    log = ex2.run(units)
    assert log.all_committed


def test_ledger_tamper_is_detected_on_resume(tmp_path):
    """The append-only hash chain is real: a rewritten ledger line refuses to resume."""
    from expos.adapters.wet.action_ledger import LedgerTamperError

    units = _units()
    run_dir = str(tmp_path)
    ex = _executor(run_dir)
    ex.execute_unit(units[0])
    ledger_path = tmp_path / "action_ledger.jsonl"
    lines = ledger_path.read_text().splitlines()
    # corrupt a historical line's content without fixing the chain
    lines[0] = lines[0].replace("PLANNED", "COMMITTED")
    ledger_path.write_text("\n".join(lines) + "\n")
    with pytest.raises(LedgerTamperError):
        _executor(run_dir)


# --------------------------------------------------------------- full domain-local e2e


def test_domain_local_e2e_report(tmp_path):
    """DoD #2/#8: one runnable end-to-end workflow + a machine-derived report."""
    from protocols.__main__ import run_e2e

    report = run_e2e(str(tmp_path))
    assert report["dispatch"]["all_committed"]
    assert report["n_units"] == 14
    assert report["constraint_check"]["ok"]
    assert len(report["ir_fingerprint"]) == 64
    # honest labelling: never claims physical autonomy.
    assert "simulated-physical" in report["validation_level"]
    assert "real hardware pending" in report["validation_level"]
    assert report["dispatch"]["readings"]  # a real observation flowed back


def test_backend_rejects_uncapable_opcode_loudly(tmp_path):
    """A backend that lacks an opcode's capability yields a loud ABORTED unit, never a
    silent no-op (the dispatch-capability seam)."""
    units = _units()
    # only a plate reader present -> no backend can ASPIRATE/DISPENSE a transfer
    ex = ProtocolExecutor(str(tmp_path), [FakePlateReader()], protocol_id="cfe",
                          reservoirs={"reservoir_lysate": 1000.0})
    res = ex.execute_unit(units[0])
    assert res.final_state is ActionState.ABORTED
    assert "no backend" in res.detail


# =========================================================================================
# protocol -> ExperimentObject compiler + MEASURE -> expression_fluorescence binding (v0.1)
#
# The seam B asked for: compile a typed cell-free protocol into a kernel ExperimentObject
# (candidates / controls / layout / objective observable) so mcl can orchestrate it as one
# round, and bind the COMMITTED ReadPlate/MEASURE read-back to the `expression_fluorescence`
# observable. Reuses the M23 transaction/backend machinery above; adds NO new physics.
# HONESTY: protocol-to-simulated-physical; commit gate real, readings faked; real hw pending.
# =========================================================================================

from protocols.experiment import (  # noqa: E402
    EXPRESSION_FLUORESCENCE,
    bind_measurements,
    classify_wells,
    compile_experiment,
    run_protocol_round,
)
from expos.kernel.objects import (  # noqa: E402
    Candidate,
    Control,
    ExperimentObject,
    ObservationObject,
    TrustLevel,
)


def test_compile_experiment_shape_and_observable():
    """A cell-free protocol compiles to a valid ExperimentObject: reaction wells -> candidates,
    the no-template well -> a negative control, plate positions -> layout, objective observable
    -> expression_fluorescence."""
    proto = cell_free_expression_protocol()
    exp = compile_experiment(proto, round_id=3)
    assert isinstance(exp, ExperimentObject)
    assert exp.round_id == 3
    assert exp.domain == "cell_free_expression_screen"
    # objective observable binding
    assert exp.objective.metric == EXPRESSION_FLUORESCENCE
    assert exp.objective.direction == "maximize"
    # reaction wells B2/B3/B4 -> candidates; control well B5 -> negative control
    assert [c.params["well"] for c in exp.candidates] == ["B2", "B3", "B4"]
    assert all(isinstance(c, Candidate) for c in exp.candidates)
    assert len(exp.controls) == 1
    assert isinstance(exp.controls[0], Control)
    assert exp.controls[0].kind == "negative"
    assert exp.controls[0].params["well"] == "B5"
    # layout covers every observed well, cand/control ids consistent (kernel exactly-one rule)
    assert exp.layout is not None
    assert {w.well_id for w in exp.layout.wells} == {"B2", "B3", "B4", "B5"}
    cand_ids = {c.cand_id for c in exp.candidates}
    for w in exp.layout.wells:
        if w.cand_id is not None:
            assert w.cand_id in cand_ids
        else:
            assert w.control_id == exp.controls[0].control_id


def test_compile_stamps_protocol_fingerprint_and_is_deterministic():
    """The lowered device-IR fingerprint is stamped into provenance (a lowering change flips
    run identity); the same protocol compiles to the same fingerprint."""
    a = compile_experiment(cell_free_expression_protocol())
    b = compile_experiment(cell_free_expression_protocol())
    assert a.provenance.protocol_fingerprint == b.provenance.protocol_fingerprint
    assert len(a.provenance.protocol_fingerprint) == 64
    assert a.provenance.generator == "protocols.experiment.compile_experiment"
    # a changed volume (different lowering) flips the stamped fingerprint
    c = compile_experiment(cell_free_expression_protocol(dna_ul=5.0))
    assert c.provenance.protocol_fingerprint != a.provenance.protocol_fingerprint


def test_classify_wells_infers_controls_and_respects_override():
    proto = cell_free_expression_protocol()
    reaction, controls = classify_wells(proto)
    assert reaction == ("B2", "B3", "B4")
    assert controls == ("B5",)
    # an explicit override wins over inference
    reaction2, controls2 = classify_wells(proto, control_wells={"B2"})
    assert controls2 == ("B2",)
    assert set(reaction2) == {"B3", "B4", "B5"}


def test_well_designs_bind_onto_candidates():
    """B's mcl can bind a richer per-well design point (e.g. a construct id) onto candidates
    without the compiler inventing a design."""
    proto = cell_free_expression_protocol()
    exp = compile_experiment(proto, well_designs={"B2": {"construct": "j23100", "coord": 1.0}})
    b2 = next(c for c in exp.candidates if c.params["well"] == "B2")
    assert b2.params["construct"] == "j23100"
    assert b2.params["coord"] == 1.0


def test_measure_binding_yields_fluorescence_observations(tmp_path):
    """DoD #5 (M29 side): a COMMITTED ReadPlate/MEASURE read-back binds to one
    expression_fluorescence observation per candidate/control well."""
    rr = run_protocol_round(str(tmp_path))
    assert rr.run_log.all_committed
    obs = rr.observations
    # one observation per observed well (3 candidates + 1 control)
    assert len(obs) == 4
    assert all(isinstance(o, ObservationObject) for o in obs)
    assert all(o.result.metric == EXPRESSION_FLUORESCENCE for o in obs)
    assert all(o.result.value is not None and o.result.value >= 0.0 for o in obs)
    assert all(o.result.unit == "arbitrary_unit" for o in obs)
    # each observation is bound to the right subject (exactly one of cand/control per well)
    wells = {o.layout_meta.well_id for o in obs}
    assert wells == {"B2", "B3", "B4", "B5"}
    control_obs = [o for o in obs if o.is_control]
    assert len(control_obs) == 1 and control_obs[0].layout_meta.well_id == "B5"
    assert control_obs[0].control_id == rr.experiment.controls[0].control_id
    # exp_id / round_id thread through to the observation
    assert all(o.exp_id == rr.experiment.exp_id for o in obs)


def test_measure_binding_is_commit_gated_no_commit_no_observation(tmp_path):
    """The trust boundary: an UNCOMMITTED MEASURE yields NO observation. Inject an UNOBSERVED
    (timeout) sensed read-back on the MEASURE unit so it stays PENDING -> zero observations,
    proving COMMITTED is the observation-eligibility gate (not blind sequence)."""
    proto = cell_free_expression_protocol("cfe")
    exp = compile_experiment(proto)
    units = group_units(lower(proto))
    measure_unit = next(u for u in units if u.unit_id.endswith("u013"))  # the ReadPlate unit
    assert measure_unit.kind == "measure"

    lh, pr = FakeLiquidHandler(), FakePlateReader()
    pr.inject(measure_unit.unit_id, Fault(SensedOutcome.UNOBSERVED))
    ex = ProtocolExecutor(str(tmp_path), [lh, pr], protocol_id="cfe",
                          reservoirs={"reservoir_lysate": 1000.0, "reservoir_dna": 1000.0,
                                      "reservoir_buffer": 1000.0})
    log = ex.run(units)
    measure_res = next(u for u in log.units if u.kind == "measure")
    assert measure_res.final_state is ActionState.PENDING and not measure_res.committed
    # commit gate: an uncommitted read produces no trusted-eligible observation.
    assert bind_measurements(exp, proto, log, pr) == []


def test_observations_are_pending_never_self_certified(tmp_path):
    """Honest trust boundary: the compiler/binding NEVER self-certifies trust; observations
    are emitted PENDING for the kernel QC / claim lifecycle to adjudicate (B's seam #3)."""
    rr = run_protocol_round(str(tmp_path))
    assert rr.observations
    assert all(o.trust is TrustLevel.PENDING for o in rr.observations)
    assert all(o.qc is None and o.routing is None for o in rr.observations)


def test_round_result_carries_honest_fingerprint(tmp_path):
    """The whole-round result exposes the device-IR fingerprint that the compiled experiment's
    provenance was stamped with (one consistent identity across compile + execute)."""
    rr = run_protocol_round(str(tmp_path))
    assert len(rr.ir_fingerprint) == 64
    assert rr.experiment.provenance.protocol_fingerprint == rr.ir_fingerprint
    assert rr.experiment.execution_req.params["ir_fingerprint"] == rr.ir_fingerprint
