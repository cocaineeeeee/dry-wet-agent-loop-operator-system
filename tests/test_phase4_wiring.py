"""M23 Phase 4-B — the single-batch mcl wiring of the physical-dispatch orchestration.

Under test is session B's mcl seam that consumes session A's Phase 4-A orchestration facade
(``expos.adapters.wet.orchestration``) + the Phase 1/2 transaction ledger. Discriminative-first
(W8 pattern): each guard has a test that turns red if the guard is removed (KILL note inline).

Coverage map (deliverables A-G):
  * default-path byte-identity — no ``physical_backend`` => ZERO physical events, decision face
    and certification bitwise identical to a second unwired run (the solvent regression anchor).
  * physical-path smoke — a nominal (CONFIRM_EXACT) backend runs one full loop: actions traverse
    PLANNED->PENDING->COMMITTED, ``physical_action_transition`` events land in the run log,
    observations are produced ONLY from COMMITTED wells, QC/Trust adjudicates, certification
    consumes.
  * unit ingest — ``_ingest_units`` is loud on a dimension mismatch (T2), a no-op when the domain
    declares no unit (legacy identical).
  * QC/Trust routing gate — a committed-but-unadjudicated observation cannot reach certification:
    the QC judge is UNCONDITIONALLY in series before ``_certify_round`` (source-order pin), and
    the judge itself refuses an un-QC'd observation loudly (PolicyError).
  * partial-completion — a mismatched well's transfer never commits => that well yields NO
    observation (the structural gate, live at the mcl output narrowing).
  * resume — the mcl resume seam is idempotent (a re-run over the persisted ledger re-sends
    nothing and re-derives the same committed set); the orchestration resume trichotomy is pinned
    per state.
  * fence semantics — the physical ledger is a SEPARATE append-only file; a truncated ledger is
    refused loudly on resume (see the FENCE-SEMANTICS REVIEW below).
  * AST guards — the orchestration/action_ledger/fake_physical modules import no harness/eval/mcl
    module; mcl's physical path imports NO concrete backend (fake_physical) — it is injected.

FENCE-SEMANTICS REVIEW (deliverable D)
--------------------------------------
The new physical ledger honors the run's standing "the event log is truth, the checkpoint is a
lagging cursor" discipline, extended cleanly to TWO append-only logs. The physical-action
transaction ledger (``<run>/physical/action_ledger.jsonl``) is a SEPARATE hash-chained file from
the kernel event log (``events.jsonl``); it is the append-only TRUTH for physical state, while the
mirrored ``physical_action_transition`` events in the kernel log are a lagging projection of it
(``_mirror_physical_events`` copies only THIS process's new transitions, preserving append order,
so a resumed round never double-mirrors). On resume, both logs are cross-checked: the kernel log's
own forked-resume anchor guards the decision face, and constructing the ``ActionLedger`` over the
existing physical dir replays AND verifies its hash chain first — a truncated or rewritten physical
ledger fails CLOSED (LedgerCorruptError / LedgerTamperError), never silently rebuilds an empty or
diverged ledger. The checkpoint remains a pure cursor: it carries no physical state, so it can only
lag the two logs, never lead them. resume_round then applies the trichotomy (COMMITTED skip /
PENDING re-sense, never re-dispatch / PLANNED re-dispatch) idempotently keyed on the deterministic
``action_id``, so a crash-resumed wet leg re-sends no already-issued transfer.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import expos.mcl as mcl
from expos.adapters.wet.action_ledger import (
    ActionLedger,
    ActionRecord,
    ActionState,
    LedgerIntegrityError,
    PlannedAction,
    VolumeLedger,
)
from expos.adapters.wet.fake_physical import (
    Behaviour,
    BehaviourSpec,
    FakePhysicalBackend,
    PhysicalDispatch,
    ResumeDisposition,
    Scenario,
)
from expos.adapters.wet.orchestration import dispatch_round
from expos.adapters.wet.recovery import WaitForRecovery
from expos.domain import DomainError, load_domain
from expos.kernel.objects import (
    LayoutMeta,
    MeasuredResult,
    ObservationObject,
)
from expos.kernel.store import DECISION_FACE_KINDS_V1, RunStore
from expos.mcl import run_mcl_loop
from expos.planner.certification import AggregatedCertification
from expos.qc.certification_stats import AggregationConfig, ClaimHead
from expos.qc.policy import PolicyError, QCPolicy

_REPO = Path(__file__).resolve().parents[1]
_SOLVENT_YAML = _REPO / "domains" / "solvent_screen.yaml"

_POLAR = "c_polar_responds_higher"
_FOCAL = "cand_ethanol"
_REFERENCE = "cand_acetonitrile"
_TRUTH = "nonpolar_high"


# --------------------------------------------------------------- fixtures / helpers


def _agg_cert() -> AggregatedCertification:
    return AggregatedCertification(
        [
            ClaimHead(
                claim_id=_POLAR,
                statement="polar solvents give a higher plate-reader response",
                favorable_direction="higher",
                focal_group=(_FOCAL,),
                reference_group=(_REFERENCE,),
            )
        ],
        config=AggregationConfig(run_fingerprint="phase4b_wiring"),
    )


def _domain_without_units(tmp_path: Path) -> Path:
    """A solvent_screen domain with its ``metric_units`` block stripped, so the physical
    wet wrap's unit-ingest check is a NO-OP (the reader channel does not yet stamp a unit;
    see the module report). Everything else is byte-for-byte solvent_screen."""
    raw = yaml.safe_load(_SOLVENT_YAML.read_text(encoding="utf-8"))
    raw.pop("metric_units", None)
    out = tmp_path / "solvent_no_units.yaml"
    out.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return out


def _phys_events(run_dir: Path) -> list[dict]:
    return RunStore(run_dir, create=False).read_events("physical_action_transition")


def _decision_face(run_dir: Path) -> list[tuple]:
    """A stable projection of the decision face (drop non-deterministic seq/ts)."""
    face: list[tuple] = []
    for ev in RunStore(run_dir, create=False).read_events():
        k, p = ev["kind"], ev["payload"]
        if k not in DECISION_FACE_KINDS_V1:
            continue
        if k == "knowledge_updated":
            face.append((k, p.get("round_id"), p.get("fingerprint")))
        elif k == "promotion_decision":
            face.append((k, p.get("round_id"),
                         tuple(x["cand_id"] for x in p.get("promoted", []))))
        elif k == "claim_decision":
            face.append((k, p.get("round_id"), p.get("decision_status")))
    return face


def _obs(well_id: str = "A1", unit: str = "", metric: str = "solvent_response",
         *, cand_id: str = "cand_x", qc=None) -> ObservationObject:
    return ObservationObject(
        exp_id="exp-test", round_id=0, cand_id=cand_id,
        result=MeasuredResult(metric=metric, value=1.0, unit=unit),
        layout_meta=LayoutMeta(well_id=well_id, row=0, col=0),
        qc=qc,
    )


class _MismatchFirstWell:
    """A test-only :class:`SensedState` backend: CONFIRM every transfer except the FIRST distinct
    destination well it senses, which it MISMATCHES (defined, recoverable code). Deterministic,
    needs no knowledge of the deck well ids. Confirms the physical path accepts ANY injected
    SensedState (mcl never imports a concrete backend)."""

    def __init__(self) -> None:
        from expos.adapters.wet.action_ledger import SensedEvidence, SensedOutcome
        self._SensedEvidence = SensedEvidence
        self._SensedOutcome = SensedOutcome
        self._bad_well: str | None = None

    def sense(self, action: PlannedAction, *, attempt: int):
        if self._bad_well is None:
            self._bad_well = action.destination_well
        eid = f"ev-{action.action_id}-a{attempt}"
        if action.destination_well == self._bad_well:
            return self._SensedEvidence(eid, attempt, self._SensedOutcome.MISMATCH,
                                        code="E_DEVICE", detail="test mismatch")
        return self._SensedEvidence(eid, attempt, self._SensedOutcome.CONFIRMED,
                                    action.requested_volume_ul)


# =============================================================== A. default-path byte-identity


def test_default_path_no_physical_events_and_identical_decision_face(tmp_path):
    """No ``physical_backend`` => the wet leg is byte-identical to pre-Phase-4: ZERO
    physical_action_transition events, and the decision face + certification state match a second
    unwired run bitwise. KILL: run the physical wrap unconditionally (drop the None guard) and the
    first assertion (zero physical events) turns red immediately."""
    a, b = tmp_path / "a", tmp_path / "b"
    run_mcl_loop(_SOLVENT_YAML, rounds=2, seed=7, out_dir=a,
                 certification=_agg_cert(), truth_profile=_TRUTH)
    run_mcl_loop(_SOLVENT_YAML, rounds=2, seed=7, out_dir=b,
                 certification=_agg_cert(), truth_profile=_TRUTH)

    assert _phys_events(a) == []                      # no physical path engaged
    assert _decision_face(a) == _decision_face(b)     # deterministic decision face
    ck_a = RunStore(a, create=False).read_checkpoint()["certification_state"]
    ck_b = RunStore(b, create=False).read_checkpoint()["certification_state"]
    assert ck_a == ck_b


# =============================================================== F. physical-path smoke (nominal)


def test_physical_path_smoke_all_committed(tmp_path):
    """A nominal (CONFIRM_EXACT) backend runs one full physical round: every transfer traverses
    PLANNED->PENDING->COMMITTED (events on the run log), observations are produced from COMMITTED
    wells, QC/Trust adjudicates (qc_report present), certification consumes, the run completes.
    KILL: never mirror the transitions (drop _mirror_physical_events) and the PENDING/COMMITTED
    assertions turn red; route non-committed wells into wet_obs and the well-count check breaks."""
    dom = _domain_without_units(tmp_path)
    run = tmp_path / "run"
    backend = FakePhysicalBackend(Scenario(name="nominal", actions=[]))
    summary = run_mcl_loop(dom, rounds=1, seed=7, out_dir=run,
                           certification=_agg_cert(), truth_profile=_TRUTH,
                           physical_backend=backend)
    assert summary is not None

    evs = _phys_events(run)
    assert evs, "physical transitions must be mirrored into the run log"
    tos = [e["payload"]["to"] for e in evs]
    assert "PENDING" in tos and "COMMITTED" in tos
    # every mirrored payload carries the registered required keys.
    for e in evs:
        p = e["payload"]
        assert {"action_id", "round_id", "to"} <= set(p)

    # PENDING precedes COMMITTED for each action (append-only ordering preserved, §3).
    for e in evs:
        p = e["payload"]
        if p.get("to") == "COMMITTED":
            aid = p["action_id"]
            pend = next(i for i, x in enumerate(evs)
                        if x["payload"]["action_id"] == aid
                        and x["payload"].get("to") == "PENDING")
            comm = next(i for i, x in enumerate(evs)
                        if x["payload"]["action_id"] == aid
                        and x["payload"].get("to") == "COMMITTED")
            assert pend < comm

    # adjudication happened (QC judged the committed observations) + wet leg issued once.
    store = RunStore(run, create=False)
    assert store.read_events("qc_report")
    assert len(store.read_events("wet_leg_issued")) == 1
    # the physical ledger is a separate append-only file at the spec's path.
    assert (run / "physical" / "action_ledger.jsonl").is_file()


# =============================================================== B. unit ingest one-liner


def test_unit_ingest_mismatch_is_loud(tmp_path):
    """T2 live: a committed observation carrying a unit that disagrees with the domain's declared
    metric unit is refused loudly (never silently coerced). KILL: drop the check_unit_consistency
    call in _ingest_units and a debye-labelled solvent_response value would flow on silently."""
    cfg = load_domain(_SOLVENT_YAML)   # declares solvent_response: arbitrary_unit
    bad = _obs(unit="debye")
    with pytest.raises(DomainError):
        mcl._ingest_units(cfg, [bad], "solvent_response")


def test_unit_ingest_match_passes(tmp_path):
    """A matching unit passes (strict equality)."""
    cfg = load_domain(_SOLVENT_YAML)
    mcl._ingest_units(cfg, [_obs(unit="arbitrary_unit")], "solvent_response")  # no raise


def test_unit_ingest_noop_when_no_units_declared(tmp_path):
    """No-op when the domain declares no unit for the metric — a legacy unit-free observation
    (unit='') passes untouched (byte-identical to pre-Phase-0). KILL: make the check fire on a
    None declaration and every legacy run would break."""
    cfg = load_domain(_SOLVENT_YAML).model_copy(update={"metric_units": None})
    mcl._ingest_units(cfg, [_obs(unit="")], "solvent_response")  # no raise


# =============================================================== C. QC/Trust routing gate guard


def test_committed_observation_still_traverses_qc_before_certification_in_series():
    """STRUCTURAL guard: in _run_round the wet observations flow through QCPolicy.judge BEFORE
    _certify_round, and the physical commit gate (_physical_wet_wrap) runs BEFORE that judge — the
    commit gate and the QC gate compose in SERIES (a CommittedResult is necessary but not
    sufficient, §5). KILL: reorder so certification consumes wet_obs before the judge call and the
    source-order assertion turns red."""
    src = inspect.getsource(mcl._run_round)
    i_wrap = src.index("_physical_wet_wrap")
    i_judge = src.index("QCPolicy(_qc_runner(cfg.metric_range")
    # the MAIN-path certification is the last _certify_round call (earlier ones are the
    # resume-consume / no-candidate early returns, which never reach the wet leg).
    i_certify = src.rindex("_certify_round(")
    # gate -> QC judge -> certification, strictly in that order in the source.
    assert i_wrap < i_judge < i_certify


def test_unadjudicated_observation_bypass_fails_loudly(tmp_path):
    """A committed observation that arrives at the judge WITHOUT a QC report cannot pass silently:
    QCPolicy.judge refuses it loudly (PolicyError) — so a committed-but-unadjudicated observation
    can never reach certification. KILL: degrade the qc-None branch of judge to a silent default
    trust and an un-QC'd observation would slip through to certification."""
    store = RunStore(tmp_path / "run")

    class _Exp:  # minimal stand-in; judge reads only round_id after the loop (never reached)
        round_id = 0

    # a runner producing NO report for the observation => obs.qc stays None => loud refusal.
    policy = QCPolicy(qc_runner=lambda exp, obs_list, history: {})
    with pytest.raises(PolicyError):
        policy.judge(store, [_obs(qc=None)], _Exp())


# =============================================================== F. partial completion (gate live)


def test_partial_completion_uncommitted_well_yields_no_observation(tmp_path):
    """A backend that MISMATCHES one well => that transfer never commits (default NeverRecover ->
    ROLLED_BACK) => the well yields NO observation (the structural commit-before-observation gate,
    realized at the mcl output narrowing). The surviving wells commit and are adjudicated. KILL:
    stop narrowing wet_obs to committed wells and the dropped well's observation reappears (well
    counts equal), collapsing the gate."""
    dom = _domain_without_units(tmp_path)
    run = tmp_path / "run"
    run_mcl_loop(dom, rounds=1, seed=7, out_dir=run,
                 certification=_agg_cert(), truth_profile=_TRUTH,
                 physical_backend=_MismatchFirstWell())

    evs = _phys_events(run)
    committed = {e["payload"]["action_id"] for e in evs
                 if e["payload"].get("to") == "COMMITTED"}
    # at least one action never reached COMMITTED (the mismatched well).
    all_actions = {e["payload"]["action_id"] for e in evs}
    assert committed and committed != all_actions, (committed, all_actions)

    # the persisted wet observations are a SUBSET of the deck: the mismatched well is absent.
    store = RunStore(run, create=False)
    wet_wells = {o.layout_meta.well_id for o in store.list_observations(round_id=0)
                 if o.raw_ref.kind == "wet"}
    n_wells = store.read_events("wet_leg_issued")[0]["payload"]["n_wells"]
    assert 0 < len(wet_wells) < n_wells      # at least one well produced no observation


# =============================================================== F/E. resume seam + trichotomy


def test_mcl_resume_seam_idempotent_over_persisted_ledger(tmp_path):
    """The mcl resume seam (_physical_wet_wrap) is idempotent over the run-wide persisted ledger:
    a second pass sees THIS round's action_ids already on the ledger, takes the resume_round branch,
    re-sends NOTHING, and re-derives the identical committed well set. Driven directly with a
    lightweight otp/wet_exp stub (the wrap reads only otp.wells / wet_exp.exp_id / obs well ids).
    KILL: always dispatch_round (never resume) and a fresh backend's re-sense of a committed action
    on the second pass would be non-empty (the resume trichotomy COMMITTED-skip lost)."""
    dom = load_domain(_domain_without_units(tmp_path))   # metric_units None => unit check no-op
    run = tmp_path / "run"
    store = RunStore(run)

    wells = ["B2", "C2", "D2"]
    otp = SimpleNamespace(wells=[SimpleNamespace(well_id=w, total_ul=150.0) for w in wells])
    wet_exp = SimpleNamespace(exp_id="exp-r0")
    wet_obs = [_obs(well_id=w, cand_id=f"cand_{w}") for w in wells]

    be1 = FakePhysicalBackend(Scenario(name="nominal", actions=[]))
    first = mcl._physical_wet_wrap(dom, store, 0, wet_exp, otp, be1, wet_obs)
    assert {o.layout_meta.well_id for o in first} == set(wells)   # all committed

    be2 = FakePhysicalBackend(Scenario(name="nominal", actions=[]))
    second = mcl._physical_wet_wrap(dom, store, 0, wet_exp, otp, be2, wet_obs)
    assert be2.sensed_log == []                       # committed actions NOT re-sensed on resume
    assert {o.layout_meta.well_id for o in second} == {o.layout_meta.well_id for o in first}


def test_resume_trichotomy_classification_per_state():
    """Deterministic three-branch (per orchestration's resume_round via PhysicalDispatch): a
    replayed record's resume disposition is COMMITTED->skip / PENDING->re-sense / PLANNED->
    re-dispatch (+ AWAITING_RECOVERY->await-operator, terminals->skip). KILL: collapse any branch
    of classify_resume and its row turns red."""
    def _rec(state: ActionState) -> ActionRecord:
        return ActionRecord(
            action_id="a", round_id=0, spec_fingerprint="s", source_well="RSV",
            destination_well="A1", requested_volume_ul=1.0, backend_id="fake-0",
            expected_pre_state={}, expected_post_state={}, state=state)

    c = PhysicalDispatch.classify_resume
    assert c(_rec(ActionState.COMMITTED)) is ResumeDisposition.SKIP_COMMITTED
    assert c(_rec(ActionState.PENDING)) is ResumeDisposition.RESENSE_PENDING
    assert c(_rec(ActionState.PLANNED)) is ResumeDisposition.REDISPATCH_PLANNED
    assert c(_rec(ActionState.AWAITING_RECOVERY)) is ResumeDisposition.AWAIT_OPERATOR
    assert c(_rec(ActionState.ROLLED_BACK)) is ResumeDisposition.SKIP_TERMINAL
    assert c(_rec(ActionState.ABORTED)) is ResumeDisposition.SKIP_TERMINAL


def test_out_of_band_recovery_path_reaches_commit(tmp_path):
    """The recovery path is reachable: a mismatch parks the action AWAITING_RECOVERY (WaitForRecovery
    policy, dispatch_round does NOT auto-retry); recover_action bumps the attempt and re-senses, and
    a now-fixed instrument commits on attempt 2. KILL: auto-retry inside dispatch_round and the
    parked assertion is already COMMITTED before recovery is invoked."""
    from expos.adapters.wet.orchestration import recover_action
    sc = Scenario(name="rec", actions=[{"action_id": "fix", "dest": "B2", "volume": 150.0}],
                  behaviours={"B2": [BehaviourSpec(1, Behaviour.MISMATCH_DEFINED, code="E_DEVICE"),
                                     BehaviourSpec(2, Behaviour.CONFIRM_EXACT)]})
    led = ActionLedger(tmp_path / "rec", volume=VolumeLedger(
        capacities={"RSV": 1e9}, initial={"RSV": 1e9}), policy=WaitForRecovery())
    be = FakePhysicalBackend(sc)
    res = dispatch_round(sc.planned(), be, led)
    assert res.non_committed[0].state == ActionState.AWAITING_RECOVERY.value
    rec = recover_action(sc.planned()[0], be, led)
    assert rec.state is ActionState.COMMITTED and rec.attempt == 2


# =============================================================== D. fence semantics (truncation)


def test_truncated_physical_ledger_refuses_resume(tmp_path):
    """The physical ledger is a SEPARATE append-only, hash-chained file; a truncated / torn ledger
    is refused loudly on resume (fail-closed, never a silent empty rebuild) — the truth log leads,
    the checkpoint cursor only lags. KILL: tolerate a torn tail on the physical ledger (as the
    kernel store does) and a resume would silently continue over an unknown-integrity ledger."""
    ledger_dir = tmp_path / "physical"
    sc = Scenario(name="nominal", actions=[{"action_id": "a1", "dest": "B2", "volume": 150.0}])
    led = ActionLedger(ledger_dir, volume=VolumeLedger(
        capacities={"RSV": 1e9}, initial={"RSV": 1e9}))
    dispatch_round(sc.planned(), FakePhysicalBackend(sc), led)

    path = ledger_dir / "action_ledger.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2
    # truncate the LAST line mid-way (a crash mid-write) -> unparseable tail.
    lines[-1] = lines[-1][: len(lines[-1]) // 2]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(LedgerIntegrityError):
        ActionLedger(ledger_dir, volume=VolumeLedger(
            capacities={"RSV": 1e9}, initial={"RSV": 1e9}))


# =============================================================== E. AST guards (harness / backend)


_ADAPTER_MODULES = ["orchestration", "action_ledger", "fake_physical"]


def _imported_names(py: Path) -> list[str]:
    tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
    return names


def test_physical_modules_import_no_harness_or_eval_or_mcl():
    """Symmetric to the truth-blind guard (Phase 4 completion criterion #3): the physical
    orchestration / ledger / backend modules import NEITHER expos.eval.harness_record NOR any
    expos.eval / expos.mcl module — the decision/dispatch path and the evaluation harness stay
    structurally separate. KILL: import harness_record into orchestration for 'convenience' and
    this test lists it as an offender."""
    base = _REPO / "expos" / "adapters" / "wet"
    offenders: list[str] = []
    for mod in _ADAPTER_MODULES:
        for name in _imported_names(base / f"{mod}.py"):
            if ("harness" in name or name.startswith("expos.eval")
                    or name == "expos.mcl" or name.startswith("expos.mcl.")):
                offenders.append(f"{mod}.py: {name}")
    assert offenders == [], offenders


def test_mcl_physical_path_imports_no_concrete_backend():
    """mcl consumes the orchestration FACADE + ledger value types, but imports NO concrete physical
    backend (fake_physical) — the backend is dependency-injected (``physical_backend``), so
    'simulation is the upper bound' stays a code-sharing guarantee, not an mcl dependency. KILL:
    import FakePhysicalBackend into mcl and this test flags the fake_physical import."""
    offenders = [n for n in _imported_names(_REPO / "expos" / "mcl.py")
                 if "fake_physical" in n]
    assert offenders == [], offenders
