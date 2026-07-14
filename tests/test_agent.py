"""M8 agent 层骨架验收（BUILD_PLAN M8，ARCHITECTURE §10）：
ProposalQueue 校验 / TemplateBackend 确定性与产物形状 / explain_verdict 容错 / 守门（公理 7）。"""

import pytest

from expos.agent import backends as backends_mod
from expos.agent import views as views_mod
from expos.agent.backends import AgentBackend, TemplateBackend
from expos.agent.views import AgentError, ProposalQueue
from expos.kernel.objects import (
    ActionType,
    Actor,
    Budget,
    DecisionKind,
    DecisionRecord,
    DesignProvenance,
    DesignSpace,
    ExecutionReq,
    ExperimentObject,
    FailureAttribution,
    FailureHypothesis,
    LayoutMeta,
    MeasuredResult,
    Objective,
    ObservationObject,
    QCCheck,
    QCReport,
    RecommendedAction,
    Routing,
    TrustLevel,
    VariableDef,
)
from expos.kernel.store import ReadOnlyRunView

_EXPORTED_AT = "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------- 构造器

def make_obs(
    obs_id: str,
    *,
    trust: TrustLevel = TrustLevel.SUSPECT,
    with_qc: bool = True,
    with_action: bool = True,
    suspicion: float = 0.7,
    round_id: int = 0,
    value: float | None = 0.5,
    failure_attr: FailureAttribution | None = None,
) -> ObservationObject:
    qc = None
    if with_qc:
        qc = QCReport(
            checks=[
                QCCheck(name="edge_effect", level="structural", passed=False, score=suspicion),
            ]
        )
    next_action = None
    if with_action:
        next_action = RecommendedAction(
            action=ActionType.DISAMBIGUATION_REPEAT,
            params={"placement_hint": "center_only"},
            reason=f"{obs_id} 边缘蒸发嫌疑，建议中心复现消歧",
        )
    return ObservationObject(
        obs_id=obs_id,
        exp_id="exp_x",
        round_id=round_id,
        cand_id="cand_" + obs_id,
        result=MeasuredResult(metric="quality_index", value=value),
        layout_meta=LayoutMeta(well_id="C4", row=2, col=3),
        qc=qc,
        trust=trust,
        routing=Routing.TO_FAILURE_MODEL if trust == TrustLevel.SUSPECT else Routing.TO_RESPONSE_MODEL,
        next_action=next_action,
        failure_attr=failure_attr,
    )


def make_exp(
    exp_id: str,
    *,
    domain: str,
    variables: list[VariableDef],
    round_id: int = 0,
) -> ExperimentObject:
    return ExperimentObject(
        exp_id=exp_id,
        round_id=round_id,
        domain=domain,
        objective=Objective(name="obj", metric="quality_index"),
        design_space=DesignSpace(name="ds", variables=variables),
        budget=Budget(wells_total=96, rounds_total=5),
        execution_req=ExecutionReq(adapter="sim"),
        provenance=DesignProvenance(generator="seed"),
    )


def make_view(observations=(), experiments=()) -> ReadOnlyRunView:
    return ReadOnlyRunView(
        run_root="/tmp/run",
        exported_at=_EXPORTED_AT,
        observations=tuple(observations),
        experiments=tuple(experiments),
    )


# ---------------------------------------------------------------- ProposalQueue 校验

def test_proposal_queue_rejects_non_agent_actor():
    q = ProposalQueue()
    rec = DecisionRecord(round_id=0, actor=Actor.PLANNER, kind=DecisionKind.ACTION_PROPOSAL)
    with pytest.raises(AgentError):
        q.put(rec)
    assert len(q) == 0


def test_proposal_queue_rejects_non_proposal_kind():
    q = ProposalQueue()
    rec = DecisionRecord(round_id=0, actor=Actor.AGENT, kind=DecisionKind.QC_EXPLANATION)
    with pytest.raises(AgentError):
        q.put(rec)
    assert len(q) == 0


def test_proposal_queue_drain_clears():
    q = ProposalQueue()
    rec = DecisionRecord(round_id=0, actor=Actor.AGENT, kind=DecisionKind.ACTION_PROPOSAL)
    q.put(rec)
    assert len(q) == 1
    drained = q.drain()
    assert drained == [rec]
    assert len(q) == 0
    assert q.drain() == []  # 再次 drain 得空


# ---------------------------------------------------------------- TemplateBackend

def test_template_backend_satisfies_protocol():
    assert isinstance(TemplateBackend(), AgentBackend)


def test_template_suggest_is_deterministic():
    view = make_view([make_obs("o1"), make_obs("o2")])
    b = TemplateBackend()
    b.ingest(view)
    s1 = b.suggest(view, round_id=1)
    s2 = b.suggest(view, round_id=1)
    assert s1 == s2  # 逐字段相等（decision_id/created_at 亦确定性）
    assert len(s1) == 2


def test_template_suggest_products_are_agent_action_proposals():
    view = make_view([make_obs("o1"), make_obs("o2")])
    proposals = TemplateBackend().suggest(view, round_id=3)
    assert proposals, "应对 SUSPECT+next_action 观测产出提案"
    for p in proposals:
        assert p.actor == Actor.AGENT
        assert p.kind == DecisionKind.ACTION_PROPOSAL
        assert p.accepted is None  # 未裁定
        assert "action" in p.content and "target" in p.content and "reason" in p.content


def test_template_suggest_respects_batch_size_and_skips_non_suspect():
    obs = [make_obs(f"o{i}") for i in range(5)]
    obs.append(make_obs("trusted", trust=TrustLevel.TRUSTED))  # 非 SUSPECT，跳过
    obs.append(make_obs("noact", with_action=False))            # 无 next_action，跳过
    view = make_view(obs)
    proposals = TemplateBackend().suggest(view, round_id=0, batch_size=2)
    assert len(proposals) == 2
    assert all(o_id not in p.refs for p in proposals for o_id in ("trusted", "noact"))


def test_explain_verdict_cites_qc_names_and_scores():
    view = make_view([make_obs("o1", suspicion=0.73)])
    text = TemplateBackend().explain_verdict(view, "o1")
    assert isinstance(text, str) and text
    assert "edge_effect" in text and "SUSPECT" in text


def test_explain_verdict_without_qc_returns_text_not_raises():
    view = make_view([make_obs("noqc", trust=TrustLevel.PENDING, with_qc=False, with_action=False)])
    text = TemplateBackend().explain_verdict(view, "noqc")
    assert isinstance(text, str) and text  # 说明性文本，不抛


def test_explain_verdict_unknown_obs_returns_text():
    view = make_view([make_obs("o1")])
    assert isinstance(TemplateBackend().explain_verdict(view, "missing"), str)


def test_suggest_products_feed_proposal_queue():
    view = make_view([make_obs("o1"), make_obs("o2")])
    q = ProposalQueue()
    for p in TemplateBackend().suggest(view, round_id=1):
        q.put(p)  # agent+action_proposal 全部合法入队
    assert len(q) == 2


# ---------------------------------------------------------------- 职责 1：translate_goal

def test_translate_goal_matches_domain_direction():
    rec = TemplateBackend().translate_goal(
        "最大化 perovskite 的效率", ["perovskite", "polymer"]
    )
    assert rec.actor == Actor.AGENT
    assert rec.kind == DecisionKind.GOAL_TRANSLATION
    assert rec.content["domain"] == "perovskite"
    assert rec.content["direction"] == "maximize"
    assert "needs_clarification" not in rec.content


def test_translate_goal_extracts_rounds_budget():
    rec = TemplateBackend().translate_goal(
        "用 5 轮 minimize the defect density of polymer", ["polymer"]
    )
    assert rec.content["domain"] == "polymer"
    assert rec.content["direction"] == "minimize"
    assert rec.content["rounds"] == 5
    # 确定性：同输入逐字段相等
    rec2 = TemplateBackend().translate_goal(
        "用 5 轮 minimize the defect density of polymer", ["polymer"]
    )
    assert rec == rec2


def test_translate_goal_unmatched_domain_flags_clarification():
    rec = TemplateBackend().translate_goal("随便做点有趣的实验", ["perovskite"])
    assert rec.content["domain"] is None
    assert rec.content["needs_clarification"] is True
    assert rec.kind == DecisionKind.GOAL_TRANSLATION  # 仍给提案，不抛错


# ---------------------------------------------------------------- 职责 2：propose_priors

def test_propose_priors_kind_actor_and_only_log_vars():
    exp = make_exp(
        "exp1",
        domain="perovskite",
        variables=[
            VariableDef(name="additive", low=1e-3, high=1.0, transform="log"),
            VariableDef(name="temp", low=20.0, high=80.0, transform="linear"),
        ],
    )
    view = make_view(experiments=[exp])
    priors = TemplateBackend().propose_priors(view, round_id=0)
    assert len(priors) == 1  # 仅 log 变量出提案
    p = priors[0]
    assert p.actor == Actor.AGENT
    assert p.kind == DecisionKind.PRIOR_PROPOSAL
    assert p.content["variable"] == "additive"
    assert p.content["suggestion"] == "log_fine_scan"


def test_propose_priors_deterministic_and_capped_per_domain():
    exp = make_exp(
        "exp1",
        domain="d1",
        variables=[
            VariableDef(name=f"v{i}", low=1e-3, high=1.0, transform="log")
            for i in range(3)
        ],
    )
    view = make_view(experiments=[exp])
    b = TemplateBackend()
    p1 = b.propose_priors(view, round_id=1)
    p2 = b.propose_priors(view, round_id=1)
    assert p1 == p2                # 逐字段相等
    assert len(p1) == 2            # 每域最多 2 条


# ---------------------------------------------------------------- 职责 4：narrate_round

def test_narrate_round_numbers_match_view():
    fa = FailureAttribution(
        hypotheses=[FailureHypothesis(cause="edge_evaporation", score=0.9)],
        top_cause="edge_evaporation",
        confidence=0.9,
    )
    obs = [
        make_obs("t1", trust=TrustLevel.TRUSTED, with_action=False, value=0.8),
        make_obs("t2", trust=TrustLevel.TRUSTED, with_action=False, value=0.5),
        make_obs("s1", trust=TrustLevel.SUSPECT, failure_attr=fa, value=0.3),
    ]
    view = make_view(observations=obs)
    rec = TemplateBackend().narrate_round(view, round_id=0, n_submitted=1)
    assert rec.actor == Actor.AGENT
    assert rec.kind == DecisionKind.ROUND_RATIONALE
    assert rec.accepted is None  # 非提案类，但字段一致
    c = rec.content
    assert c["n_trusted"] == 2
    assert c["n_suspect"] == 1
    assert c["best_trusted_value"] == 0.8
    assert c["top_cause"] == "edge_evaporation" and c["top_cause_count"] == 1
    assert c["n_queued_actions"] == 1  # 仅 SUSPECT s1 带 next_action（"identified" 数）
    assert c["n_submitted"] == 1  # 调用方传入的真实提交数（NARR3 P2 fix）
    assert "edge_evaporation" in c["narrative"] and "0.8" in c["narrative"]
    assert "identified 1 suggested action" in c["narrative"]
    assert "submitted 1" in c["narrative"]


def test_narrate_round_no_numbers_states_so():
    obs = [make_obs("s1", trust=TrustLevel.SUSPECT, with_action=False)]
    view = make_view(observations=obs)
    c = TemplateBackend().narrate_round(view, round_id=0).content
    assert c["n_trusted"] == 0
    assert c["best_trusted_value"] is None
    assert "暂无可信观测值可报" in c["narrative"]


def test_narrate_round_submitted_can_be_lower_than_identified():
    """[NARR3 P2 fix] narrate_round must not imply identified == submitted: when the
    caller's real submission count (batch_size-capped) is below the count of observations
    carrying a next_action, the narrative must report both numbers distinctly rather than
    describing the identified count as already "enqueued"."""
    obs = [
        make_obs(f"s{i}", trust=TrustLevel.SUSPECT, round_id=0)
        for i in range(5)  # all 5 carry next_action ("identified"); caller only submitted 3
    ]
    view = make_view(observations=obs)
    c = TemplateBackend().narrate_round(view, round_id=0, n_submitted=3).content
    assert c["n_queued_actions"] == 5
    assert c["n_submitted"] == 3
    assert "identified 5 suggested action" in c["narrative"]
    assert "submitted 3" in c["narrative"]


# ---------------------------------------------------------------- EV3: TRUSTED + empty checks


def test_explain_verdict_trusted_empty_checks_is_self_consistent():
    """[EV3 fix, P3] TRUSTED (or SUSPECT/FAILED) with empty qc.checks is a decided
    verdict with no evidence recorded — the explanation must not claim the observation
    is "kept pending" (that phrasing is reserved for trust=PENDING); it must instead
    describe the (unusual) decided-but-evidence-empty state without contradiction."""
    obs = make_obs("t_no_checks", trust=TrustLevel.TRUSTED, with_qc=False, with_action=False)
    view = make_view(observations=[obs])
    text = TemplateBackend().explain_verdict(view, "t_no_checks")
    assert "TRUSTED" in text
    assert "pending" not in text.lower()  # no longer claims an undecided state
    assert "no QC check evidence recorded" in text


def test_explain_verdict_cites_attribution_and_refuter():
    fa = FailureAttribution(
        hypotheses=[
            FailureHypothesis(
                cause="edge_evaporation",
                score=0.9,
                evidence={"refuter": {"passed": True, "mode": "placebo+subsample"}},
            )
        ],
        top_cause="edge_evaporation",
        confidence=0.88,
    )
    view = make_view(observations=[make_obs("o1", failure_attr=fa)])
    text = TemplateBackend().explain_verdict(view, "o1")
    assert "edge_evaporation" in text
    assert "反驳器" in text and "0.88" in text


# ---------------------------------------------------------------- 守门（公理 7）

WRITE_WORDS = ("save", "append", "write", "delete", "update", "remove")

FORBIDDEN_IMPORTS = (
    "RunStore",
    "lifecycle",
    "adapters",
    "planner",
    "models",
    "submit_proposal",
    "validate_proposal",
    "adjudicate",
    "reclassify",
    "route_observation",
)


@pytest.mark.parametrize("mod", [views_mod, backends_mod])
def test_new_module_has_no_write_public_api(mod):
    offenders = [
        n for n in dir(mod)
        if not n.startswith("_") and any(w in n.lower() for w in WRITE_WORDS)
    ]
    assert offenders == [], f"{mod.__name__} 暴露了写型公有 API: {offenders}"


@pytest.mark.parametrize("mod", [views_mod, backends_mod])
def test_new_module_imports_no_forbidden_targets(mod):
    with open(mod.__file__, encoding="utf-8") as f:
        import_lines = [
            ln for ln in f
            if ln.lstrip().startswith(("import ", "from "))
        ]
    src = "".join(import_lines)
    hits = [tok for tok in FORBIDDEN_IMPORTS if tok in src]
    assert hits == [], f"{mod.__name__} import 了禁区符号: {hits}"


def test_agent_package_still_writeless_and_empty_all():
    import expos.agent as agent_pkg

    offenders = [
        n for n in dir(agent_pkg)
        if not n.startswith("_") and any(w in n.lower() for w in WRITE_WORDS)
    ]
    assert offenders == [] and agent_pkg.__all__ == []
