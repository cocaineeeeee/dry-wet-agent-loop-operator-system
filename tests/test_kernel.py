"""M1 内核验收测试（BUILD_PLAN M1）：
序列化往返 / 事件追加 / 裁决表 / 改判写日志 / DecisionRecord 落盘与配对 / 守门。"""

import json
from pathlib import Path

import pytest

from expos.kernel.lifecycle import (
    ADJUDICATOR_ACTORS,
    LifecycleError,
    TrustPolicy,
    accepted_proposals,
    adjudicate,
    advance_status,
    reclassify,
    route_observation,
    submit_proposal,
    unresolved_proposals,
    validate_proposal,
)
from expos.kernel.objects import (
    ActionType,
    FailureAttribution,
    FailureHypothesis,
    RecommendedAction,
)
from expos.kernel.objects import (
    Actor,
    Budget,
    Candidate,
    Constraint,
    Control,
    DecisionKind,
    DecisionRecord,
    DesignProvenance,
    DesignSpace,
    ExecutionReq,
    ExperimentObject,
    ExpStatus,
    LayoutAssignment,
    LayoutMeta,
    MeasuredResult,
    Objective,
    ObservationObject,
    QCCheck,
    QCReport,
    ReplicatePlan,
    Routing,
    TrustLevel,
    VariableDef,
    WellAssignment,
)
from expos.kernel.store import ReadOnlyRunView, RunStore, StoreError


# ---------------------------------------------------------------- 构造器

def make_experiment(round_id: int = 0) -> ExperimentObject:
    space = DesignSpace(
        name="crystal",
        variables=[
            VariableDef(name="supersaturation", low=1.05, high=1.6, unit="S"),
            VariableDef(name="additive_frac", low=1e-4, high=1e-2, transform="log"),
            VariableDef(name="cool_rate", low=0.1, high=2.0, unit="K/h"),
            VariableDef(name="seeded", kind="categorical", choices=[0, 1]),
        ],
    )
    cand = Candidate(params={"supersaturation": 1.2, "additive_frac": 1e-3, "cool_rate": 0.5, "seeded": 1}, source="sobol")
    ctrl = Control(kind="sentinel", params={"supersaturation": 1.1, "additive_frac": 1e-3, "cool_rate": 1.0, "seeded": 0}, expected_band=(0.3, 0.5))
    layout = LayoutAssignment(
        rows=6,
        cols=8,
        seed=7,
        wells=[
            WellAssignment(well_id="A1", row=0, col=0, control_id=ctrl.control_id, is_edge=True, block_id="Q0"),
            WellAssignment(well_id="C4", row=2, col=3, cand_id=cand.cand_id, is_edge=False, block_id="Q1"),
        ],
    )
    return ExperimentObject(
        round_id=round_id,
        domain="crystal",
        objective=Objective(name="single_crystal_quality", metric="quality_index"),
        design_space=space,
        active_vars=[v.name for v in space.variables],
        restrictions=[Constraint(name="safe_supersat", kind="range", params={"var": "supersaturation", "max": 1.6})],
        candidates=[cand],
        controls=[ctrl],
        replicate_plan=ReplicatePlan(n_replicates=2),
        layout=layout,
        budget=Budget(wells_total=384, rounds_total=8),
        execution_req=ExecutionReq(adapter="sim_crystal", n_solution_batches=2),
        provenance=DesignProvenance(generator="sobol", rationale="round0 space filling"),
    )


def make_observation(exp: ExperimentObject, suspicion: float = 0.0, hard_fail: bool = False) -> ObservationObject:
    checks = [QCCheck(name="value_range", level="hard", passed=not hard_fail, score=1.0 if hard_fail else 0.0)]
    if suspicion > 0:
        checks.append(QCCheck(name="edge_effect", level="structural", passed=False, score=suspicion))
    return ObservationObject(
        exp_id=exp.exp_id,
        round_id=exp.round_id,
        cand_id=exp.candidates[0].cand_id,
        result=MeasuredResult(metric="quality_index", value=0.72, uncertainty=0.03, secondary={"grain_count": 5}),
        layout_meta=LayoutMeta(well_id="C4", row=2, col=3, is_edge=False, block_id="Q1"),
        qc=QCReport(checks=checks),
    )


# ---------------------------------------------------------------- 序列化往返

def test_experiment_roundtrip():
    exp = make_experiment()
    exp2 = ExperimentObject.model_validate_json(exp.model_dump_json())
    assert exp2 == exp
    assert exp2.design_space.var("additive_frac").transform == "log"


def test_observation_roundtrip():
    exp = make_experiment()
    obs = make_observation(exp, suspicion=0.7)
    obs2 = ObservationObject.model_validate_json(obs.model_dump_json())
    assert obs2 == obs
    assert obs2.qc.checks[1].score == pytest.approx(0.7)


def test_decision_record_roundtrip():
    rec = DecisionRecord(round_id=1, actor=Actor.AGENT, kind=DecisionKind.ACTION_PROPOSAL, content={"action": "REMEASURE"})
    rec2 = DecisionRecord.model_validate_json(rec.model_dump_json())
    assert rec2 == rec


def test_schema_consistency_guards():
    exp = make_experiment()
    with pytest.raises(ValueError):  # cand 与 control 二选一
        WellAssignment(well_id="A1", row=0, col=0)
    with pytest.raises(ValueError):  # is_control 不一致
        ObservationObject(
            exp_id=exp.exp_id, round_id=0, control_id="ctrl_x", is_control=False,
            result=MeasuredResult(metric="q"), layout_meta=LayoutMeta(well_id="A1", row=0, col=0),
        )
    with pytest.raises(ValueError):  # log 变量 low>0
        VariableDef(name="bad", low=0.0, high=1.0, transform="log")


def test_designspace_rejects_duplicate_var_names():
    # J-3：变量重名产生幻影维度（var() 只取首个、维数错位）——加载期响亮拒绝
    with pytest.raises(ValueError):
        DesignSpace(
            name="dup",
            variables=[
                VariableDef(name="x", low=0.0, high=1.0),
                VariableDef(name="x", low=0.0, high=2.0),
            ],
        )


def test_qc_score_suspicion_trust_confidence_bounded_0_1():
    # Q-3：三处嫌疑/置信 Field 加 [0,1] 约束——越界生产者在构造期即被暴露
    with pytest.raises(ValueError):
        QCCheck(name="c", level="structural", passed=False, score=7.0)
    with pytest.raises(ValueError):
        QCCheck(name="c", level="structural", passed=True, score=-0.1)
    with pytest.raises(ValueError):
        QCReport(suspicion=1.5)
    exp = make_experiment()
    with pytest.raises(ValueError):
        obs = make_observation(exp)
        obs.trust_confidence = 7.0  # validate_assignment=True → 立即拒


# ---------------------------------------------------------------- 存储与事件日志

def test_store_roundtrip_and_reopen(tmp_path):
    store = RunStore(tmp_path / "run")
    exp = make_experiment()
    obs = make_observation(exp)
    store.save_experiment(exp)
    store.save_observation(obs)
    # 新句柄重开（模拟断点续跑）
    store2 = RunStore(tmp_path / "run", create=False)
    assert store2.load_experiment(exp.exp_id) == exp
    assert store2.load_observation(obs.obs_id) == obs
    assert [e.exp_id for e in store2.list_experiments()] == [exp.exp_id]


def test_event_append_only_and_seq(tmp_path):
    store = RunStore(tmp_path / "run")
    e0 = store.append_event("status_transition", {"from": "DESIGNED", "to": "EXECUTED"})
    e1 = store.append_event("routing", {"obs_id": "obs_x"})
    e2 = store.append_event("routing", {"obs_id": "obs_y"})
    assert (e0["seq"], e1["seq"], e2["seq"]) == (0, 1, 2)
    lines = (tmp_path / "run" / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0]) == e0  # 早期事件从未被改写
    assert [e["seq"] for e in store.read_events("routing")] == [1, 2]
    # 重开后 seq 续接，不回绕、不覆盖
    store2 = RunStore(tmp_path / "run", create=False)
    e3 = store2.append_event("checkpoint", {"round_id": 0})
    assert e3["seq"] == 3


def test_checkpoint_atomic_roundtrip(tmp_path):
    store = RunStore(tmp_path / "run")
    assert store.read_checkpoint() is None
    store.write_checkpoint({"round_id": 2, "status": "ROUTED", "wells_used": 96})
    ckpt = RunStore(tmp_path / "run", create=False).read_checkpoint()
    assert ckpt["round_id"] == 2 and ckpt["wells_used"] == 96 and "written_at" in ckpt
    assert not (tmp_path / "run" / "checkpoint.json.tmp").exists()


# ---------------------------------------------------------------- 状态机

def test_status_transitions(tmp_path):
    store = RunStore(tmp_path / "run")
    exp = make_experiment()
    store.save_experiment(exp)
    for status in (ExpStatus.EXECUTED, ExpStatus.QC_DONE, ExpStatus.ROUTED, ExpStatus.CLOSED):
        exp = advance_status(store, exp, status)
    assert [e["payload"]["to"] for e in store.read_events("status_transition")] == [
        "EXECUTED", "QC_DONE", "ROUTED", "CLOSED",
    ]
    with pytest.raises(LifecycleError):
        advance_status(store, exp, ExpStatus.EXECUTED)  # CLOSED 是终态


def test_illegal_skip_transition(tmp_path):
    store = RunStore(tmp_path / "run")
    exp = make_experiment()
    with pytest.raises(LifecycleError):
        advance_status(store, exp, ExpStatus.ROUTED)  # DESIGNED 不能跳到 ROUTED


# ---------------------------------------------------------------- 裁决表（§7.4）

@pytest.mark.parametrize(
    "hard_fail,suspicion,want_trust,want_routing",
    [
        (True, 0.0, TrustLevel.FAILED, Routing.TO_FAILURE_MODEL),
        (False, 0.75, TrustLevel.SUSPECT, Routing.TO_FAILURE_MODEL),
        (False, 0.60, TrustLevel.SUSPECT, Routing.TO_FAILURE_MODEL),  # 阈值含等号
        (False, 0.45, TrustLevel.SUSPECT, Routing.QUARANTINE),
        (False, 0.30, TrustLevel.SUSPECT, Routing.QUARANTINE),
        (False, 0.10, TrustLevel.TRUSTED, Routing.TO_RESPONSE_MODEL),
        (False, 0.0, TrustLevel.TRUSTED, Routing.TO_RESPONSE_MODEL),
    ],
)
def test_adjudication_table(hard_fail, suspicion, want_trust, want_routing):
    exp = make_experiment()
    obs = make_observation(exp, suspicion=suspicion, hard_fail=hard_fail)
    trust, routing, conf = adjudicate(obs.qc, TrustPolicy())
    assert (trust, routing) == (want_trust, want_routing)
    assert 0.0 <= conf <= 1.0


def test_route_observation_persists_and_logs(tmp_path):
    store = RunStore(tmp_path / "run")
    exp = make_experiment()
    obs = make_observation(exp, suspicion=0.7)
    routed = route_observation(store, obs)
    assert routed.trust == TrustLevel.SUSPECT and routed.routing == Routing.TO_FAILURE_MODEL
    assert store.load_observation(obs.obs_id).trust == TrustLevel.SUSPECT
    ev = store.read_events("routing")
    assert len(ev) == 1 and ev[0]["payload"]["obs_id"] == obs.obs_id


def test_route_requires_qc(tmp_path):
    store = RunStore(tmp_path / "run")
    exp = make_experiment()
    obs = make_observation(exp)
    obs.qc = None
    with pytest.raises(LifecycleError):
        route_observation(store, obs)  # 公理 2：无 QC 证据不得路由


def test_adjudicate_empty_qc_report_refuses_trusted():
    # Q-4：空 QCReport（无检查证据）不得裁为 TRUSTED conf=1.0（"无证据即满分信任"）
    with pytest.raises(LifecycleError):
        adjudicate(QCReport())
    # 显式 suspicion 仍可裁（有证据来源），不受空 checks 拦截误伤
    trust, routing, _ = adjudicate(QCReport(suspicion=0.7))
    assert trust == TrustLevel.SUSPECT


def test_route_observation_refuses_non_pending(tmp_path):
    # Q-2：已裁决观测重路由会静默回滚——route_observation 只首判 PENDING 观测
    store = RunStore(tmp_path / "run")
    exp = make_experiment()
    obs = route_observation(store, make_observation(exp, suspicion=0.7))  # → SUSPECT
    assert obs.trust != TrustLevel.PENDING
    with pytest.raises(LifecycleError):
        route_observation(store, obs)  # 非 PENDING，须走 reclassify


def test_reclassify_sets_trust_confidence_and_records_from(tmp_path):
    # Q-5：FAILED(conf=1.0) 改判后不更新则语义残留；改判置 1.0 + payload 记 from_confidence
    store = RunStore(tmp_path / "run")
    exp = make_experiment()
    obs = route_observation(store, make_observation(exp, suspicion=0.45))  # QUARANTINE, conf<1
    assert obs.trust_confidence < 1.0
    updated = reclassify(
        store, obs.obs_id, TrustLevel.TRUSTED, Routing.TO_RESPONSE_MODEL,
        actor=Actor.HUMAN, reason="哨兵正常，翻案",
    )
    assert updated.trust_confidence == 1.0  # 人工裁决=确定
    rc = store.read_events("reclassification")[0]["payload"]
    assert rc["from_confidence"] == pytest.approx(obs.trust_confidence)


# ---------------------------------------------------------------- 改判 / 翻案

def test_reclassify_appends_history_never_overwrites(tmp_path):
    store = RunStore(tmp_path / "run")
    exp = make_experiment()
    obs = route_observation(store, make_observation(exp, suspicion=0.45))  # → QUARANTINE
    n_events_before = len(store.read_events())
    updated = reclassify(
        store, obs.obs_id, TrustLevel.TRUSTED, Routing.TO_RESPONSE_MODEL,
        actor=Actor.HUMAN, reason="后续证据：同批次哨兵正常，翻案",
    )
    assert updated.trust == TrustLevel.TRUSTED
    # 历史保留：原 routing 事件仍在，新增 reclassification + reclassification_conflict
    # （SUSPECT→TRUSTED 属高危翻案，额外落强留痕事件）+ OVERRIDE 决策事件 = +3。
    events = store.read_events()
    assert len(events) == n_events_before + 3
    rc = store.read_events("reclassification")[0]["payload"]
    assert rc["from_trust"] == "SUSPECT" and rc["to_trust"] == "TRUSTED"
    # 高危翻案强留痕：单独可检索的 reclassification_conflict 事件。
    conflict = store.read_events("reclassification_conflict")
    assert len(conflict) == 1 and conflict[0]["payload"]["to_trust"] == "TRUSTED"
    overrides = store.list_decisions(kind=DecisionKind.OVERRIDE)
    assert len(overrides) == 1 and obs.obs_id in overrides[0].refs
    assert overrides[0].actor == Actor.HUMAN


# ---------------------------------------------------------------- DecisionRecord 配对（§4.5 审计不变量）

def test_proposal_acceptance_rejection_pairing(tmp_path):
    store = RunStore(tmp_path / "run")
    p1 = submit_proposal(store, DecisionRecord(
        round_id=1, actor=Actor.AGENT, kind=DecisionKind.ACTION_PROPOSAL,
        content={"action": "DISAMBIGUATION_REPEAT", "cand_id": "cand_x", "placement_hint": "center_only"},
    ))
    p2 = submit_proposal(store, DecisionRecord(
        round_id=1, actor=Actor.AGENT, kind=DecisionKind.PRIOR_PROPOSAL, content={"note": "additive log 细扫"},
    ))
    # 未裁定：两条都 unresolved，accepted 为空 → 对下一轮设计不可见
    assert {d.decision_id for d in unresolved_proposals(store)} == {p1.decision_id, p2.decision_id}
    assert accepted_proposals(store) == []
    # 裁定：p1 接受、p2 拒绝
    a1 = validate_proposal(store, p1, accepted=True, actor=Actor.PLANNER, reason="预算内且布局可行")
    validate_proposal(store, p2, accepted=False, actor=Actor.PLANNER, reason="证据不足")
    assert unresolved_proposals(store) == []
    accepted = accepted_proposals(store)
    assert [d.decision_id for d in accepted] == [p1.decision_id]
    assert a1.refs == [p1.decision_id] and a1.accepted is True and a1.validator == "planner"


def test_non_proposal_kinds_rejected_by_pairing_api(tmp_path):
    store = RunStore(tmp_path / "run")
    note = DecisionRecord(round_id=0, actor=Actor.AGENT, kind=DecisionKind.QC_EXPLANATION, content={"text": "..."})
    with pytest.raises(LifecycleError):
        submit_proposal(store, note)  # 解释类不是提案
    store.append_decision(note)  # 但可作为普通决策载荷落盘
    with pytest.raises(LifecycleError):
        validate_proposal(store, note, accepted=True, actor=Actor.PLANNER)


def test_decision_records_land_in_event_log(tmp_path):
    store = RunStore(tmp_path / "run")
    store.append_decision(DecisionRecord(round_id=0, actor=Actor.AGENT, kind=DecisionKind.ROUND_RATIONALE, content={"text": "r0"}))
    events = store.read_events("decision")
    assert len(events) == 1
    assert events[0]["payload"]["kind"] == "round_rationale"
    assert store.list_decisions(actor=Actor.AGENT)[0].content == {"text": "r0"}


# ---------------------------------------------------------------- 审查补测：裁决权与冲突（公理 7 的日志层强制）

def test_agent_cannot_adjudicate_proposals(tmp_path):
    store = RunStore(tmp_path / "run")
    p = submit_proposal(store, DecisionRecord(
        round_id=1, actor=Actor.AGENT, kind=DecisionKind.ACTION_PROPOSAL, content={"action": "REMEASURE"},
    ))
    assert Actor.AGENT not in ADJUDICATOR_ACTORS
    with pytest.raises(LifecycleError):
        validate_proposal(store, p, accepted=True, actor=Actor.AGENT)
    # 即使伪造 acceptance 记录绕过 API 直接落日志，机器检查也不采信
    store.append_decision(DecisionRecord(
        round_id=1, actor=Actor.AGENT, kind=DecisionKind.ACCEPTANCE,
        refs=[p.decision_id], accepted=True, validator="agent",
    ))
    assert accepted_proposals(store) == []
    assert {d.decision_id for d in unresolved_proposals(store)} == {p.decision_id}


def test_agent_cannot_reclassify(tmp_path):
    store = RunStore(tmp_path / "run")
    exp = make_experiment()
    obs = route_observation(store, make_observation(exp, suspicion=0.7))  # SUSPECT
    with pytest.raises(LifecycleError):
        reclassify(store, obs.obs_id, TrustLevel.TRUSTED, Routing.TO_RESPONSE_MODEL,
                   actor=Actor.AGENT, reason="agent 试图翻案")
    assert store.load_observation(obs.obs_id).trust == TrustLevel.SUSPECT  # 未被改动


def test_resolution_conflict_requires_human_override(tmp_path):
    store = RunStore(tmp_path / "run")
    p = submit_proposal(store, DecisionRecord(
        round_id=1, actor=Actor.AGENT, kind=DecisionKind.ACTION_PROPOSAL, content={"action": "REMEASURE"},
    ))
    validate_proposal(store, p, accepted=False, actor=Actor.PLANNER, reason="预算不足")
    with pytest.raises(LifecycleError):  # planner 不能静默翻盘
        validate_proposal(store, p, accepted=True, actor=Actor.PLANNER)
    assert accepted_proposals(store) == []
    # human 可 override，翻盘留 conflict 事件
    validate_proposal(store, p, accepted=True, actor=Actor.HUMAN, reason="人工复核后接受")
    assert [d.decision_id for d in accepted_proposals(store)] == [p.decision_id]
    conflicts = store.read_events("resolution_conflict")
    assert len(conflicts) == 1 and conflicts[0]["payload"]["proposal_id"] == p.decision_id


# ---------------------------------------------------------------- Q-8：提案配对三边缘洞（加固）

def test_ghost_adjudication_rejected(tmp_path):
    """Q-8 幽灵裁定：裁定一个从未 submit 入库的提案 → 响亮拒绝，绝不凭空产出裁定。"""
    store = RunStore(tmp_path / "run")
    ghost = DecisionRecord(
        round_id=1, actor=Actor.AGENT, kind=DecisionKind.ACTION_PROPOSAL,
        content={"action": "REMEASURE"},
    )  # 未经 submit_proposal 入库
    with pytest.raises(LifecycleError):
        validate_proposal(store, ghost, accepted=True, actor=Actor.PLANNER, reason="裁个幽灵")
    # 日志里既无该提案也无其裁定
    assert unresolved_proposals(store) == []
    assert accepted_proposals(store) == []


def test_duplicate_decision_id_double_submit_rejected(tmp_path):
    """Q-8 重复提交双计：同 decision_id 二次落盘 → StoreError（提案本应每次新铸 id）。"""
    store = RunStore(tmp_path / "run")
    p = submit_proposal(store, DecisionRecord(
        decision_id="dec_dup", round_id=1, actor=Actor.AGENT,
        kind=DecisionKind.ACTION_PROPOSAL, content={"action": "REMEASURE"},
    ))
    with pytest.raises(StoreError):
        submit_proposal(store, p)  # 同 id 二次提交
    # 只登记一次，不双计
    assert [d.decision_id for d in unresolved_proposals(store)] == ["dec_dup"]


def test_duplicate_decision_id_rebuilt_across_handles(tmp_path):
    """Q-8 去重集 resume 重建：新句柄从盘惰性重建 seen 集，跨句柄仍拒重复。"""
    root = tmp_path / "run"
    store = RunStore(root)
    submit_proposal(store, DecisionRecord(
        decision_id="dec_x", round_id=0, actor=Actor.AGENT,
        kind=DecisionKind.ACTION_PROPOSAL, content={"action": "REMEASURE"},
    ))
    store2 = RunStore(root, create=False)
    with pytest.raises(StoreError):
        store2.append_decision(DecisionRecord(
            decision_id="dec_x", round_id=0, actor=Actor.AGENT,
            kind=DecisionKind.ACTION_PROPOSAL, content={"action": "REMEASURE"},
        ))


def test_multi_ref_adjudication_rejected(tmp_path):
    """Q-8 多 refs 一票裁俩：伪造一条 adjudicator ACCEPTANCE 同时 refs 两个提案 →
    _resolutions 遍历时响亮拒（一裁一案），绝不让一票静默裁定两个提案。"""
    store = RunStore(tmp_path / "run")
    p1 = submit_proposal(store, DecisionRecord(
        round_id=1, actor=Actor.AGENT, kind=DecisionKind.ACTION_PROPOSAL,
        content={"action": "REMEASURE"},
    ))
    p2 = submit_proposal(store, DecisionRecord(
        round_id=1, actor=Actor.AGENT, kind=DecisionKind.PRIOR_PROPOSAL, content={"n": 1},
    ))
    # 直写一条 planner 的多 refs ACCEPTANCE（validate_proposal 永不产多 refs）
    store.append_decision(DecisionRecord(
        round_id=1, actor=Actor.PLANNER, kind=DecisionKind.ACCEPTANCE,
        refs=[p1.decision_id, p2.decision_id], accepted=True, validator="planner",
    ))
    with pytest.raises(StoreError):
        unresolved_proposals(store)  # 经 _resolutions 时炸
    with pytest.raises(StoreError):
        accepted_proposals(store)


def test_adjudicate_explicit_suspicion_overrides_checks():
    qc = QCReport(checks=[QCCheck(name="edge_effect", level="structural", passed=False, score=0.1)],
                  suspicion=0.9)  # 显式汇总分优先于 checks 派生
    trust, routing, _ = adjudicate(qc)
    assert (trust, routing) == (TrustLevel.SUSPECT, Routing.TO_FAILURE_MODEL)


def test_full_observation_roundtrip_with_verdict_fields():
    exp = make_experiment()
    obs = make_observation(exp, suspicion=0.7)
    obs.trust = TrustLevel.SUSPECT
    obs.routing = Routing.TO_FAILURE_MODEL
    obs.failure_attr = FailureAttribution(
        hypotheses=[FailureHypothesis(cause="edge_evaporation", score=0.8,
                                      remedy=ActionType.DISAMBIGUATION_REPEAT)],
        top_cause="edge_evaporation", confidence=0.8,
    )
    obs.next_action = RecommendedAction(action=ActionType.DISAMBIGUATION_REPEAT,
                                        params={"placement_hint": "center_only"}, reason="边缘嫌疑")
    obs2 = ObservationObject.model_validate_json(obs.model_dump_json())
    assert obs2 == obs and obs2.routing == Routing.TO_FAILURE_MODEL
    assert obs2.failure_attr.hypotheses[0].remedy == ActionType.DISAMBIGUATION_REPEAT


def test_store_trust_filter_and_atomic_object_writes(tmp_path):
    store = RunStore(tmp_path / "run")
    exp = make_experiment()
    store.save_experiment(exp)
    route_observation(store, make_observation(exp, suspicion=0.7))   # SUSPECT
    route_observation(store, make_observation(exp, suspicion=0.0))   # TRUSTED
    assert len(store.list_observations(trust=TrustLevel.SUSPECT)) == 1
    assert len(store.list_observations(trust=TrustLevel.TRUSTED)) == 1
    assert len(store.list_observations(round_id=0)) == 2
    # 原子写：目录中不残留 .tmp
    leftovers = list((tmp_path / "run").rglob("*.tmp"))
    assert leftovers == []


# ---------------------------------------------------------------- 守门（公理 7）

WRITE_WORDS = ("save", "append", "write", "delete", "update", "remove")


def test_agent_package_has_no_write_api():
    import expos.agent as agent_pkg

    offenders = [
        n for n in dir(agent_pkg)
        if not n.startswith("_") and any(w in n.lower() for w in WRITE_WORDS)
    ]
    assert offenders == [], f"agent 包暴露了写型 API: {offenders}"
    assert agent_pkg.__all__ == []


def test_save_truth_idempotent_per_round(tmp_path):
    """断点重做同一轮时 truth 不得重复追加（覆盖写语义）。"""
    store = RunStore(tmp_path / "run")
    store.save_truth(0, [{"well_id": "A1", "true_value": 0.3}])
    p = store.save_truth(0, [{"well_id": "A1", "true_value": 0.3},
                             {"well_id": "A2", "true_value": 0.4}])
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2  # 第二次写入覆盖而非追加成 3 行


def test_readonly_view_is_frozen_truthless_and_writeless(tmp_path):
    store = RunStore(tmp_path / "run")
    exp = make_experiment()
    store.save_experiment(exp)
    obs = route_observation(store, make_observation(exp, suspicion=0.7))
    store.save_truth(0, [{"well_id": "C4", "true_value": 0.41}])  # 真值 sidecar 落盘
    store.write_checkpoint({"round_id": 0})
    view = store.export_view()

    # frozen：属性赋值必须抛错
    with pytest.raises(Exception):
        view.checkpoint = {}
    # 无 truth 暴露
    assert "truth" not in ReadOnlyRunView.model_fields
    assert not any("truth" in n.lower() for n in dir(view) if not n.startswith("_"))
    # 无写型方法（排除 pydantic BaseModel 自带成员，如弃用的 update_forward_refs）
    from pydantic import BaseModel

    offenders = [
        n for n in dir(view)
        if not n.startswith("_")
        and n not in dir(BaseModel)
        and any(w in n.lower() for w in WRITE_WORDS)
    ]
    assert offenders == [], f"ReadOnlyRunView 暴露了写型 API: {offenders}"
    # 内容与存储一致，且过滤器可用
    assert view.experiments[0].exp_id == exp.exp_id
    assert view.observations_by_trust(TrustLevel.SUSPECT)[0].obs_id == obs.obs_id
    assert view.checkpoint["round_id"] == 0


# ---------------------------------------------------------------- 域配置语义校验（J-3）

def _base_domain_dict() -> dict:
    """从真实 crystal.yaml 取一份合法基线，供各错误变体逐一注入。"""
    import yaml

    text = (Path(__file__).resolve().parent.parent / "domains" / "crystal.yaml").read_text(
        encoding="utf-8"
    )
    return yaml.safe_load(text)


def _load_mutated(tmp_path, mutate) -> None:
    import yaml

    from expos.domain import load_domain

    cfg = _base_domain_dict()
    mutate(cfg)
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    load_domain(p)


def test_domain_yaml_baseline_still_loads(tmp_path):
    # 未变异的真实基线必须仍能加载（守卫不误伤合法配置）
    _load_mutated(tmp_path, lambda c: None)


@pytest.mark.parametrize(
    "mutate",
    [
        # J-3：trust 阈值倒置（suspect_high < quarantine_low）直接改写裁决方向
        lambda c: c.__setitem__("trust", {"suspect_high": 0.3, "quarantine_low": 0.6}),
        # 阈值越界（≥1 / ≤0）
        lambda c: c.__setitem__("trust", {"suspect_high": 1.2, "quarantine_low": 0.3}),
        lambda c: c.__setitem__("trust", {"suspect_high": 0.6, "quarantine_low": 0.0}),
        # DesignSpace 变量重名 → 幻影维度
        lambda c: c["design_space"]["variables"].append(
            {"name": "cool_rate", "kind": "continuous", "low": 0.1, "high": 2.0}
        ),
        # DesignSpace low ≥ high
        lambda c: c["design_space"]["variables"].__setitem__(
            0, {"name": "supersaturation", "kind": "continuous", "low": 1.6, "high": 1.05}
        ),
        # metric_range 上下界倒置
        lambda c: c.__setitem__("metric_range", [1.2, 0.0]),
        # budget 非正
        lambda c: c["budget"].__setitem__("wells_total", 0),
        lambda c: c["budget"].__setitem__("rounds_total", -1),
        # replicates 非正
        lambda c: c.__setitem__("replicates", 0),
    ],
)
def test_domain_yaml_semantic_errors_fail_loudly(tmp_path, mutate):
    from expos.domain import DomainError

    with pytest.raises(DomainError):
        _load_mutated(tmp_path, mutate)
