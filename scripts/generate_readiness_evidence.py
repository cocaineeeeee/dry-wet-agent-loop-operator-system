#!/usr/bin/env python
"""M23 Phase 5 A-段 取证跑 — Real-Wet Readiness 证据集生成器.

按 mailbox/red_to_blue/126 §2 输入契约产 evidence set, 落 runs/readiness_evidence/.
生成器(B 侧报告)只吃这些文件 + 仓内纯函数; 本脚本零手填结果性数字 —— manifest 的
``expected_outcome`` 是设计声明(允许), 一切结果性数字由 B 生成器从文件派生.

用法::

    python scripts/generate_readiness_evidence.py gen                 # 全场景(幂等)
    python scripts/generate_readiness_evidence.py gen --only exact_success
    python scripts/generate_readiness_evidence.py gen --sbatch-job-ids 12345
    python scripts/generate_readiness_evidence.py verify              # 逐场景契约完整性
    python scripts/generate_readiness_evidence.py verify --only crash_I3

取证层级(manifest.evidence_level):
  * full_loop     -- run_mcl_loop + physical_backend 全环(events/checkpoint/physical ledger)
  * crash_resume  -- run_mcl_loop + interrupt_hook 崩溃 + resume(续写同 run)
  * orchestration -- 编排级直驱 PhysicalDispatch/orchestration facade 产 ledger(全环走不通的模式)
  * differential  -- 双 ledger 过差分门, diff_report.json 原样序列化
  * loud_failure  -- 子进程捕获响亮失败原文入 stderr.txt + exit_status.txt(非零)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from expos.adapters.wet.action_ledger import (  # noqa: E402
    ActionLedger,
    PlannedAction,
    SensedEvidence,
    SensedOutcome,
    VolumeLedger,
)
from expos.adapters.wet.differential_gate import (  # noqa: E402
    ToleranceEnvelope,
    run_differential_gate,
)
from expos.adapters.wet.fake_physical import (  # noqa: E402
    Behaviour,
    BehaviourSpec,
    FakePhysicalBackend,
    OBS_CHANNEL_SCHEMA,
    PhysicalDispatch,
    Scenario,
)
from expos.adapters.wet.orchestration import cancel_action, recover_action  # noqa: E402
from expos.adapters.wet.recovery import WaitForRecovery  # noqa: E402

_ROOT = _REPO / "runs" / "readiness_evidence"
_DOMAINS_DIR = _ROOT / "_domains"
_SOLVENT_YAML = _REPO / "domains" / "solvent_screen.yaml"
_ENVELOPE_REL = "expos/adapters/wet/tolerances_vendor_placeholder.json"

_TRUTH = "nonpolar_high"
_SEED = 7
_SOURCE_WELL = "RSV"
_SOURCE_CAP = 1e9
_DEST_WELLS = ["B2", "C2", "D2", "E2"]
_DIFF_VOLUME = 100.0  # band <=150uL: pct 2%%=2.0uL, floor 1.5uL -> allowance 2.0uL


# --------------------------------------------------------------- certification substrate

def _agg_cert():
    """The polar-head certification substrate (same shape as the Phase 4 tests) so a
    flipped (nonpolar_high) truth face progresses insufficient -> rejected and the wet
    leg actually issues + adjudicates."""
    from expos.planner.certification import AggregatedCertification
    from expos.qc.certification_stats import AggregationConfig, ClaimHead

    return AggregatedCertification(
        [
            ClaimHead(
                claim_id="c_polar_responds_higher",
                statement="polar solvents give a higher plate-reader response",
                favorable_direction="higher",
                focal_group=("cand_ethanol",),
                reference_group=("cand_acetonitrile",),
            )
        ],
        config=AggregationConfig(run_fingerprint="phase5_readiness"),
    )


def _no_units_domain() -> Path:
    """A solvent_screen domain with ``metric_units`` stripped so the physical wet wrap's
    unit-ingest check is a NO-OP (the reader channel does not stamp a unit yet). Every
    full-loop physical scenario uses this; the UNIT_MISMATCH scenario deliberately uses
    the units-declared domain to trip the loud reject."""
    _DOMAINS_DIR.mkdir(parents=True, exist_ok=True)
    out = _DOMAINS_DIR / "solvent_screen_no_units.yaml"
    raw = yaml.safe_load(_SOLVENT_YAML.read_text(encoding="utf-8"))
    raw.pop("metric_units", None)
    out.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return out


# --------------------------------------------------------------- small helpers

def _fresh_dir(scenario_id: str) -> Path:
    d = _ROOT / scenario_id
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _mk_action(aid: str, dest: str, vol: float = _DIFF_VOLUME) -> PlannedAction:
    return PlannedAction(
        action_id=aid, round_id=0, spec_fingerprint="spec-readiness",
        source_well=_SOURCE_WELL, destination_well=dest,
        requested_volume_ul=vol, backend_id="fake-0",
        expected_pre_state={}, expected_post_state={},
    )


def _volume_ledger() -> VolumeLedger:
    return VolumeLedger(
        capacities={_SOURCE_WELL: _SOURCE_CAP}, initial={_SOURCE_WELL: _SOURCE_CAP})


def _ledger_at(run_dir: Path, subdir: str = "physical") -> ActionLedger:
    return ActionLedger(run_dir / subdir, volume=_volume_ledger())


def _write_manifest(run_dir: Path, meta: dict[str, Any]) -> None:
    (run_dir / "scenario_manifest.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------- full-loop physical builders

def _run_full_loop(run_dir: Path, backend, *, domain: Path, rounds: int) -> None:
    from expos.mcl import run_mcl_loop

    run_mcl_loop(
        domain, rounds=rounds, seed=_SEED, out_dir=run_dir / "run",
        certification=_agg_cert(), truth_profile=_TRUTH, physical_backend=backend,
    )


class _MismatchFirstWell:
    """CONFIRM every transfer except the FIRST distinct destination well, which MISMATCHES
    (defined, recoverable code) -- the partial-execution face (per-well sensed truth)."""

    def __init__(self) -> None:
        self._bad_well: str | None = None

    def sense(self, action: PlannedAction, *, attempt: int) -> SensedEvidence:
        if self._bad_well is None:
            self._bad_well = action.destination_well
        eid = f"ev-{action.action_id}-a{attempt}"
        if action.destination_well == self._bad_well:
            return SensedEvidence(eid, attempt, SensedOutcome.MISMATCH,
                                  code="E_DEVICE", detail="partial: first well mismatch")
        return SensedEvidence(eid, attempt, SensedOutcome.CONFIRMED,
                              observed_volume_ul=action.requested_volume_ul)


def _scenario(name: str, default: Behaviour, **kw) -> Scenario:
    return Scenario(
        name=name, actions=[],
        default_behaviour=BehaviourSpec(attempt=1, behaviour=default, **kw))


def build_exact_success(run_dir: Path) -> None:
    _run_full_loop(run_dir, FakePhysicalBackend(_scenario("exact", Behaviour.CONFIRM_EXACT)),
                   domain=_no_units_domain(), rounds=1)


def build_sensed_mismatch(run_dir: Path) -> None:
    be = FakePhysicalBackend(
        _scenario("mismatch", Behaviour.MISMATCH_DEFINED, code="E_DEVICE"))
    _run_full_loop(run_dir, be, domain=_no_units_domain(), rounds=1)


def build_partial_execution(run_dir: Path) -> None:
    _run_full_loop(run_dir, _MismatchFirstWell(), domain=_no_units_domain(), rounds=1)


def build_timeout_before_confirm(run_dir: Path) -> None:
    be = FakePhysicalBackend(
        _scenario("timeout", Behaviour.TIMEOUT, timeout_at_tick=3))
    _run_full_loop(run_dir, be, domain=_no_units_domain(), rounds=1)


# --------------------------------------------------------------- orchestration-level builders

def build_duplicate_reply(run_dir: Path) -> None:
    """Idempotency gate: the same command echoed twice -> the second dispatch is an
    idempotent replay (io NOT re-run, one PENDING per action_id), the physical action
    executes exactly once and commits."""
    ledger = _ledger_at(run_dir)
    be = FakePhysicalBackend(_scenario("dup", Behaviour.CONFIRM_EXACT))
    for i, dest in enumerate(_DEST_WELLS[:3]):
        a = _mk_action(f"dup-{i}", dest)
        ledger.dispatch(a, lambda: True)          # -> PENDING (first send)
        ledger.dispatch(a, lambda: True)          # duplicate reply -> idempotent replay skip
        ev = be.sense(a, attempt=1)
        ledger.confirm(a.action_id, ev)           # sensed CONFIRMED -> COMMITTED


def build_disconnect_resume(run_dir: Path) -> None:
    """Orchestration interrupted mid wet-leg, resumed from the ledger via the trichotomy:
    COMMITTED skip / PENDING re-sense (never re-dispatch) / PLANNED (never-dispatched)
    re-dispatch. A NEW ledger over the same dir replays the torn state from disk."""
    a = _mk_action("disc-A", _DEST_WELLS[0])   # will be COMMITTED pre-disconnect
    b = _mk_action("disc-B", _DEST_WELLS[1])   # will be left PENDING (unobserved) at the tear
    c = _mk_action("disc-C", _DEST_WELLS[2])   # never dispatched at all pre-disconnect

    # -- pre-disconnect torn state -------------------------------------------------
    led1 = _ledger_at(run_dir)
    be1 = FakePhysicalBackend(_scenario("disc", Behaviour.CONFIRM_EXACT))
    led1.dispatch(a, lambda: True)
    led1.confirm(a.action_id, be1.sense(a, attempt=1))   # A -> COMMITTED
    led1.dispatch(b, lambda: True)                       # B -> PENDING (no confirm == disconnect)

    # -- resume: a fresh ledger replays A COMMITTED / B PENDING from the hash-chained file
    led2 = _ledger_at(run_dir)
    disp = PhysicalDispatch(led2, FakePhysicalBackend(_scenario("disc2", Behaviour.CONFIRM_EXACT)))
    disp.resume([a, b, c])   # A skip / B re-sense->COMMITTED / C re-dispatch->COMMITTED


def build_cancel_during_awaiting_recovery(run_dir: Path) -> None:
    """Operator cancel while AWAITING_RECOVERY == ABORTED (the seven-mode cancel face).
    A recoverable MISMATCH under WaitForRecovery parks the action in AWAITING_RECOVERY."""
    ledger = ActionLedger(run_dir / "physical", volume=_volume_ledger(),
                          policy=WaitForRecovery())
    be = FakePhysicalBackend(_scenario("cancel", Behaviour.MISMATCH_DEFINED, code="E_DEVICE"))
    disp = PhysicalDispatch(ledger, be)
    a = _mk_action("cancel-A", _DEST_WELLS[0])
    disp.dispatch_one(a)                       # MISMATCH(recoverable) -> AWAITING_RECOVERY
    cancel_action(a.action_id, ledger, reason="operator_cancel")   # -> ABORTED


# --------------------------------------------------------------- human-intervention builders

def _await_recovery_backend(dest: str) -> FakePhysicalBackend:
    """A backend that MISMATCHES attempt 1 (recoverable) and CONFIRMS attempt 2 on a
    specific destination well -- drives the AWAITING_RECOVERY -> recover(attempt++) arc."""
    sc = Scenario(name="human", actions=[], behaviours={
        dest: [
            BehaviourSpec(attempt=1, behaviour=Behaviour.MISMATCH_DEFINED, code="E_DEVICE"),
            BehaviourSpec(attempt=2, behaviour=Behaviour.CONFIRM_EXACT),
        ]
    })
    return FakePhysicalBackend(sc)


def build_human_recover(run_dir: Path) -> None:
    """sensed_mismatch -> AWAITING_RECOVERY -> recover_action(attempt++) re-sense CONFIRMED
    -> COMMITTED (the human fixed the instrument)."""
    dest = _DEST_WELLS[0]
    ledger = ActionLedger(run_dir / "physical", volume=_volume_ledger(),
                          policy=WaitForRecovery())
    be = _await_recovery_backend(dest)
    disp = PhysicalDispatch(ledger, be)
    a = _mk_action("recover-A", dest)
    disp.dispatch_one(a)                          # attempt1 MISMATCH -> AWAITING_RECOVERY
    recover_action(a, be, ledger)                 # attempt++ -> re-sense CONFIRM -> COMMITTED


def build_human_cancel(run_dir: Path) -> None:
    """sensed_mismatch -> AWAITING_RECOVERY -> cancel_action == ABORTED (the operator gave
    up). Distinct human-arc twin of the recover path."""
    dest = _DEST_WELLS[0]
    ledger = ActionLedger(run_dir / "physical", volume=_volume_ledger(),
                          policy=WaitForRecovery())
    be = _await_recovery_backend(dest)
    disp = PhysicalDispatch(ledger, be)
    a = _mk_action("hcancel-A", dest)
    disp.dispatch_one(a)                          # attempt1 MISMATCH -> AWAITING_RECOVERY
    cancel_action(a.action_id, ledger, reason="human_abandon")   # -> ABORTED


# --------------------------------------------------------------- differential builders

def _emit_diff_ledger(run_dir: Path, subdir: str, behaviour: Behaviour, **kw) -> Path:
    ledger = ActionLedger(run_dir / subdir, volume=_volume_ledger())
    sc = _scenario(subdir, behaviour, **kw)
    be = FakePhysicalBackend(sc)
    disp = PhysicalDispatch(ledger, be)
    disp.run([_mk_action(f"diff-{i}", d) for i, d in enumerate(_DEST_WELLS[:3])])
    return run_dir / subdir / "action_ledger.jsonl"


def _write_diff_report(run_dir: Path, sim_path: Path, real_path: Path) -> bool:
    report = run_differential_gate(
        sim_path, real_path,
        tolerance=ToleranceEnvelope.load(_REPO / _ENVELOPE_REL),
        sim_obs_schema=dict(OBS_CHANNEL_SCHEMA), real_obs_schema=dict(OBS_CHANNEL_SCHEMA),
    )
    (run_dir / "diff_report.json").write_text(
        json.dumps(report.as_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report.passed


def build_diff_positive(run_dir: Path) -> None:
    """nominal双 ledger: sim exact, real within-band (delta 1.5uL < 2.0uL allowance) -> PASS."""
    sim = _emit_diff_ledger(run_dir, "sim", Behaviour.CONFIRM_EXACT)
    real = _emit_diff_ledger(run_dir, "real", Behaviour.CONFIRM_WITHIN_TOL, delta_ul=1.5)
    _write_diff_report(run_dir, sim, real)


def build_diff_negative(run_dir: Path) -> None:
    """real drifts 5%% (observed 105uL, deviation 5.0uL > 2.0uL allowance) -> gate REJECT."""
    sim = _emit_diff_ledger(run_dir, "sim", Behaviour.CONFIRM_EXACT)
    real = _emit_diff_ledger(run_dir, "real", Behaviour.CONFIRM_DRIFT, drift_pct=5.0)
    _write_diff_report(run_dir, sim, real)


# --------------------------------------------------------------- loud-failure builder

_LOUD_FAILURE_CODE = """\
import sys
from pathlib import Path
sys.path.insert(0, {repo!r})
import expos.mcl as mcl
from expos.domain import load_domain
from expos.kernel.objects import LayoutMeta, MeasuredResult, ObservationObject

# The physical-path unit-ingest guard(the EXACT function _physical_wet_wrap calls on the
# COMMITTED observation set) fed a WRONG-unit observation: solvent_screen declares
# solvent_response=arbitrary_unit, but a mis-labelled instrument/driver reports the value
# stamped in DEBYE. check_unit_consistency(T2 Mars-Climate-Orbiter) refuses LOUDLY -- a
# debye value is never silently treated as an a.u. one(no coerce, no guess, no default).
cfg = load_domain({domain!r})
bad = ObservationObject(
    exp_id="exp-loud", round_id=0, cand_id="cand_x",
    result=MeasuredResult(metric="solvent_response", value=1.0, unit="debye"),
    layout_meta=LayoutMeta(well_id="B2", row=1, col=1), qc=None,
)
mcl._ingest_units(cfg, [bad], "solvent_response")
print("UNEXPECTED: unit mismatch did not raise", file=sys.stderr)
sys.exit(0)
"""


def build_unit_mismatch(run_dir: Path) -> None:
    """loud_failure: feed the physical-path unit-ingest guard a wrong-unit observation in a
    SUBPROCESS and capture the raised DomainError原文 into stderr.txt + a non-zero
    exit_status.txt (the report引原文)."""
    code = _LOUD_FAILURE_CODE.format(repo=str(_REPO), domain=str(_SOLVENT_YAML))
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=str(_REPO))
    (run_dir / "stderr.txt").write_text(proc.stderr, encoding="utf-8")
    (run_dir / "exit_status.txt").write_text(f"{proc.returncode}\n", encoding="utf-8")
    if proc.returncode == 0:
        raise RuntimeError(
            "unit_mismatch scenario expected a non-zero loud failure but exited 0")


# --------------------------------------------------------------- crash/resume builders

def _crash_hook(target_point: str, target_round: int) -> Callable[[str, int], None]:
    from expos.mcl import _SimulatedCrash

    def hook(point: str, round_id: int) -> None:
        if point == target_point and round_id == target_round:
            raise _SimulatedCrash(f"injected crash at {point} round {round_id}")
    return hook


def _build_crash(run_dir: Path, killpoint: str, crash_round: int) -> None:
    """Interrupt at a pinned killpoint(I1..I6) with a crash hook(hard crash: no run_stop),
    then RESUME the SAME run dir(resume_style=same_run). Mirrors tests/test_phase4_interruption.
    NO physical_backend -- the kernel crash/recovery seam is the proven green path."""
    from expos.mcl import _SimulatedCrash, run_mcl_loop

    out = run_dir / "run"
    try:
        run_mcl_loop(_SOLVENT_YAML, rounds=2, seed=_SEED, out_dir=out,
                     certification=_agg_cert(), truth_profile=_TRUTH,
                     interrupt_hook=_crash_hook(killpoint, crash_round))
    except _SimulatedCrash:
        pass
    else:
        raise RuntimeError(f"{killpoint}: injected crash did not fire")

    # resume: event-log-as-truth, continues from the last completed round into the same dir.
    run_mcl_loop(_SOLVENT_YAML, rounds=2, seed=_SEED, out_dir=out,
                 certification=_agg_cert(), truth_profile=_TRUTH, resume=True)


def _make_crash_builder(killpoint: str, crash_round: int):
    def builder(run_dir: Path) -> None:
        _build_crash(run_dir, killpoint, crash_round)
    return builder


# =============================================================== scenario registry

# (killpoint, crash_round) pins per tests/test_phase4_interruption._KILLPOINTS.
_CRASH_ROUNDS = {"I1": 1, "I2": 1, "I3": 1, "I4": 1, "I5": 1, "I6": 0}


def _scenarios() -> list[dict[str, Any]]:
    reg: list[dict[str, Any]] = [
        dict(scenario_id="exact_success", mode="exact_success", killpoint=None,
             expected_outcome="success", evidence_level="full_loop", rounds=1,
             domain="solvent_screen(no_units)", resume_style=None,
             description="七模式: 每格 CONFIRM_EXACT, observed~=requested, "
                         "PENDING->COMMITTED 恰一次; 全环 run_mcl_loop+physical_backend.",
             builder=build_exact_success),
        dict(scenario_id="sensed_mismatch", mode="sensed_mismatch", killpoint=None,
             expected_outcome="insufficient", evidence_level="full_loop", rounds=1,
             domain="solvent_screen(no_units)", resume_style=None,
             description="七模式: driver OK 回复但 read-back MISMATCH, OK 永不 commit; "
                         "NeverRecover 下每格 ROLLED_BACK, 无 committed 证据.",
             builder=build_sensed_mismatch),
        dict(scenario_id="partial_execution", mode="partial_execution", killpoint=None,
             expected_outcome="success", evidence_level="full_loop", rounds=1,
             domain="solvent_screen(no_units)", resume_style=None,
             description="七模式: 首个 distinct well MISMATCH(ROLLED_BACK), 其余 CONFIRMED; "
                         "per-WELL sensed truth, 部分 committed 证据存活.",
             builder=build_partial_execution),
        dict(scenario_id="timeout_before_confirm", mode="timeout_before_confirm",
             killpoint=None, expected_outcome="insufficient", evidence_level="full_loop",
             rounds=1, domain="solvent_screen(no_units)", resume_style=None,
             description="七模式: UNOBSERVED 直到 LOGICAL timeout budget(virtual tick, "
                         "无 real sleep), 然后 TRANSPORT timeout MISMATCH; 无静默重试.",
             builder=build_timeout_before_confirm),
        dict(scenario_id="duplicate_reply", mode="duplicate_reply", killpoint=None,
             expected_outcome="success", evidence_level="orchestration", rounds=1,
             domain="orchestration(plate96)", resume_style=None,
             description="七模式(编排级直驱): 同命令回复两次, idempotency gate 吃掉第二次 "
                         "(io 不重跑, 每 action_id 恰一次 PENDING), 物理动作恰执行一次.",
             builder=build_duplicate_reply),
        dict(scenario_id="disconnect_resume", mode="disconnect_resume", killpoint=None,
             expected_outcome="success", evidence_level="orchestration",
             resume_style="same_ledger", rounds=1, domain="orchestration(plate96)",
             description="七模式(编排级直驱): 编排中断后从 ledger resume, resume 三分法 "
                         "(COMMITTED skip / PENDING re-sense 不 re-dispatch / PLANNED re-dispatch); "
                         "新 ledger 从 hash-chain 文件重放撕裂态.",
             builder=build_disconnect_resume),
        dict(scenario_id="cancel_during_awaiting_recovery",
             mode="cancel_during_awaiting_recovery", killpoint=None,
             expected_outcome="aborted", evidence_level="orchestration", rounds=1,
             domain="orchestration(plate96)", resume_style=None,
             description="七模式(编排级直驱): WaitForRecovery 下可恢复 MISMATCH 进 "
                         "AWAITING_RECOVERY, operator cancel == ABORTED(无 commit 无需回滚).",
             builder=build_cancel_during_awaiting_recovery),
        # human intervention arcs
        dict(scenario_id="human_recover", mode="human_recover", killpoint=None,
             expected_outcome="recovered", evidence_level="orchestration", rounds=1,
             domain="orchestration(plate96)", resume_style=None,
             description="sensed_mismatch 进 AWAITING_RECOVERY 后 recover_action(attempt++) "
                         "re-sense CONFIRMED -> COMMITTED(人工修复成功).",
             builder=build_human_recover),
        dict(scenario_id="human_cancel", mode="human_cancel", killpoint=None,
             expected_outcome="aborted", evidence_level="orchestration", rounds=1,
             domain="orchestration(plate96)", resume_style=None,
             description="sensed_mismatch 进 AWAITING_RECOVERY 后 cancel_action == ABORTED"
                         "(人工放弃), recover 弧线的对偶.",
             builder=build_human_cancel),
        # loud failure
        dict(scenario_id="unit_mismatch", mode="unit_mismatch", killpoint=None,
             expected_outcome="loud_failure", evidence_level="loud_failure", rounds=1,
             domain="solvent_screen(units_declared)", resume_style=None,
             description="loud_failure(子进程捕获): 单位声明域走物理路径, 观测丢单位 -> "
                         "check_unit_consistency 响亮拒(T4 Mars-Climate-Orbiter); "
                         "stderr.txt 原文 + exit_status.txt 非零.",
             builder=build_unit_mismatch),
        # differential gate
        dict(scenario_id="diff_positive", mode="diff_positive", killpoint=None,
             expected_outcome="success", evidence_level="differential", rounds=1,
             domain="differential_gate", resume_style=None,
             description="差分正样本: sim exact 与 real within-band(delta 1.5uL < 2.0uL "
                         "allowance) 双 ledger 过差分门 PASS; diff_report.json 原样序列化.",
             builder=build_diff_positive),
        dict(scenario_id="diff_negative", mode="diff_negative", killpoint=None,
             expected_outcome="gate_reject", evidence_level="differential", rounds=1,
             domain="differential_gate", resume_style=None,
             description="差分负样本: real 漂移 5%%(observed 105uL, deviation 5.0uL > 2.0uL "
                         "allowance)超容差被拒; diff_report.json 原样序列化.",
             builder=build_diff_negative),
    ]
    # six-killpoint crash matrix (crash + resume same run).
    for kp, cr in _CRASH_ROUNDS.items():
        reg.append(dict(
            scenario_id=f"crash_{kp}", mode=f"crash_{kp}", killpoint=kp,
            expected_outcome="success", evidence_level="crash_resume",
            resume_style="same_run", rounds=2, domain="solvent_screen",
            description=(
                f"崩溃矩阵: 在 pinned killpoint {kp}(round {cr}) 注入 hard crash"
                "(无 run_stop), 续写同 run resume; 决策面 == 未中断基线, 无双发, "
                "wet 每轮恰发一次."),
            builder=_make_crash_builder(kp, cr)))
    return reg


# =============================================================== driver / verify

#: Evidence-pointer tables (letter 135 contract amendment): where each evidence
#: level actually keeps its run directory / physical action ledger, relative to
#: the scenario root. ``None`` = the evidence type is not involved by design.
_RUN_PATHS: dict[str, str | None] = {
    "full_loop": "run",
    "orchestration": None,
    "loud_failure": None,
    "differential": None,
    "crash_resume": "run",
}
_LEDGER_PATHS: dict[str, str | None] = {
    "full_loop": "run/physical/action_ledger.jsonl",
    "orchestration": "physical/action_ledger.jsonl",
    "loud_failure": None,
    "differential": None,
    "crash_resume": None,
}


def _manifest_meta(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "scenario_id": spec["scenario_id"],
        "mode": spec["mode"],
        "killpoint": spec["killpoint"],
        "expected_outcome": spec["expected_outcome"],
        "description": spec["description"],
        "seed": _SEED,
        "rounds": spec["rounds"],
        "domain": spec["domain"],
        "evidence_level": spec["evidence_level"],
        "resume_style": spec["resume_style"],
        # Contract amendment (letter 135): self-describing evidence pointers so the
        # report generator follows the manifest instead of guessing layouts.
        # null = that evidence type is NOT INVOLVED in this scenario (render
        # "not involved", never BROKEN/MISSING).
        "run_path": _RUN_PATHS[spec["evidence_level"]],
        "ledger_path": _LEDGER_PATHS[spec["evidence_level"]],
    }


def generate(only: str | None, sbatch_job_ids: list[str]) -> None:
    _ROOT.mkdir(parents=True, exist_ok=True)
    specs = _scenarios()
    if only:
        specs = [s for s in specs if s["scenario_id"] == only]
        if not specs:
            raise SystemExit(f"unknown scenario {only!r}")
    for spec in specs:
        sid = spec["scenario_id"]
        print(f"[gen] {sid} ({spec['evidence_level']}) ...", flush=True)
        run_dir = _fresh_dir(sid)
        spec["builder"](run_dir)
        _write_manifest(run_dir, _manifest_meta(spec))
        print(f"[gen] {sid} done", flush=True)
    # (re)write the top-level index over whatever scenario dirs now exist on disk.
    _write_index(sbatch_job_ids)


def _write_index(sbatch_job_ids: list[str]) -> None:
    all_ids = [s["scenario_id"] for s in _scenarios()]
    present = [sid for sid in all_ids if (_ROOT / sid / "scenario_manifest.json").exists()]
    index = {
        "scenarios": present,
        "envelope_config": _ENVELOPE_REL,
        "generated_by": "scripts/generate_readiness_evidence.py",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sbatch_job_ids": sbatch_job_ids,
    }
    (_ROOT / "evidence_index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[index] {len(present)}/{len(all_ids)} scenarios -> evidence_index.json",
          flush=True)


def _verify_scenario(spec: dict[str, Any]) -> list[str]:
    """Per-contract completeness check for one scenario. Returns a list of problems
    (empty == OK). Structural only -- NO result numbers are asserted here (those are B's
    generator's job)."""
    sid = spec["scenario_id"]
    d = _ROOT / sid
    problems: list[str] = []
    if not d.is_dir():
        return [f"{sid}: scenario dir missing"]

    man_path = d / "scenario_manifest.json"
    if not man_path.is_file():
        problems.append(f"{sid}: scenario_manifest.json missing")
    else:
        man = json.loads(man_path.read_text(encoding="utf-8"))
        required = {"scenario_id", "mode", "killpoint", "expected_outcome",
                    "description", "seed", "rounds", "domain"}
        missing = required - set(man)
        if missing:
            problems.append(f"{sid}: manifest missing keys {sorted(missing)}")
        allowed_outcome = {"success", "loud_failure", "insufficient", "gate_reject",
                           "recovered", "aborted"}
        if man.get("expected_outcome") not in allowed_outcome:
            problems.append(f"{sid}: expected_outcome {man.get('expected_outcome')!r} "
                            "not in contract enum")

    level = spec["evidence_level"]
    if level == "full_loop":
        problems += _need(d, sid, ["run/events.jsonl", "run/checkpoint.json",
                                   "run/physical/action_ledger.jsonl"])
    elif level == "crash_resume":
        problems += _need(d, sid, ["run/events.jsonl", "run/checkpoint.json"])
        problems += _verify_chain(d / "run", sid)
    elif level == "orchestration":
        problems += _need(d, sid, ["physical/action_ledger.jsonl"])
    elif level == "differential":
        problems += _need(d, sid, ["diff_report.json", "sim/action_ledger.jsonl",
                                   "real/action_ledger.jsonl"])
        problems += _verify_diff(d, sid, spec["expected_outcome"])
    elif level == "loud_failure":
        problems += _need(d, sid, ["stderr.txt", "exit_status.txt"])
        problems += _verify_loud(d, sid)
    return problems


def _need(d: Path, sid: str, rel_paths: list[str]) -> list[str]:
    return [f"{sid}: {rel} missing" for rel in rel_paths if not (d / rel).is_file()]


def _verify_diff(d: Path, sid: str, expected_outcome: str) -> list[str]:
    report = json.loads((d / "diff_report.json").read_text(encoding="utf-8"))
    want_pass = expected_outcome == "success"
    if report.get("passed") is not want_pass:
        return [f"{sid}: diff_report passed={report.get('passed')} but expected "
                f"passed={want_pass} (outcome {expected_outcome})"]
    return []


def _verify_loud(d: Path, sid: str) -> list[str]:
    status = (d / "exit_status.txt").read_text(encoding="utf-8").strip()
    problems: list[str] = []
    if status in ("", "0"):
        problems.append(f"{sid}: exit_status {status!r} is not a non-zero loud failure")
    stderr = (d / "stderr.txt").read_text(encoding="utf-8")
    if "Error" not in stderr and "error" not in stderr:
        problems.append(f"{sid}: stderr.txt carries no captured error text")
    return problems


def _verify_chain(run_dir: Path, sid: str) -> list[str]:
    """Run scripts/verify_run_chain.py over a crash-resume run: CHAIN COMPLETE expected."""
    import os

    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [str(_REPO), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep)}
    proc = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "verify_run_chain.py"),
         str(run_dir), "--json"],
        capture_output=True, text=True, cwd=str(_REPO), env=env)
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
        return [f"{sid}: verify_run_chain non-zero exit {proc.returncode}: {tail}"]
    return []


def verify(only: str | None) -> int:
    specs = _scenarios()
    if only:
        specs = [s for s in specs if s["scenario_id"] == only]
        if not specs:
            raise SystemExit(f"unknown scenario {only!r}")
    all_problems: list[str] = []
    for spec in specs:
        problems = _verify_scenario(spec)
        status = "OK" if not problems else "FAIL"
        print(f"[verify] {spec['scenario_id']:32s} {spec['evidence_level']:13s} {status}")
        all_problems += problems
    if not only:
        idx = _ROOT / "evidence_index.json"
        if not idx.is_file():
            all_problems.append("evidence_index.json missing")
        else:
            doc = json.loads(idx.read_text(encoding="utf-8"))
            for key in ("scenarios", "envelope_config", "generated_by", "sbatch_job_ids"):
                if key not in doc:
                    all_problems.append(f"evidence_index.json missing key {key!r}")
            if not (_REPO / doc.get("envelope_config", "")).is_file():
                all_problems.append("evidence_index.envelope_config path does not resolve")
    print()
    if all_problems:
        print(f"[verify] {len(all_problems)} PROBLEM(S):")
        for p in all_problems:
            print(f"  - {p}")
        return 1
    print("[verify] ALL SCENARIOS COMPLETE (contract-structural)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen", help="generate the evidence set (idempotent)")
    g.add_argument("--only", help="generate a single scenario by id")
    g.add_argument("--sbatch-job-ids", nargs="*", default=[],
                   help="record the submitting sbatch job id(s) in evidence_index.json")

    v = sub.add_parser("verify", help="check per-scenario contract completeness")
    v.add_argument("--only", help="verify a single scenario by id")

    i = sub.add_parser("index", help="(re)write evidence_index.json only")
    i.add_argument("--sbatch-job-ids", nargs="*", default=[])

    args = parser.parse_args(argv)
    if args.cmd == "gen":
        generate(args.only, args.sbatch_job_ids)
        return 0
    if args.cmd == "verify":
        return verify(args.only)
    if args.cmd == "index":
        _write_index(args.sbatch_job_ids)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
