"""M7 规划器仲裁验收测试（docs/ARCHITECTURE.md §9；REFERENCE_MAP §11.5/§13.8/§13.2）。

覆盖：collect 去重/排序/agent+endogenous 混合；arbitrate 预算封顶与溢出；
discounted_scores 归一化 + min-filter（p=0.9 钳 0.5）；exploration_quota；
actions_to_candidates 的 hint/查表失败响亮；确定性；依赖隔离红线。
"""

import numpy as np
import pytest

from expos.errors import ExposError
from expos.kernel.objects import (
    ActionType,
    Actor,
    DecisionKind,
    DecisionRecord,
    FailureAttribution,
    LayoutMeta,
    MeasuredResult,
    ObservationObject,
    RecommendedAction,
    TrustLevel,
)
from expos.planner.arbiter import (
    ActionItem,
    ArbiterError,
    actions_to_candidates,
    arbitrate,
    collect_actions,
    discounted_scores,
    exploration_quota,
    well_cost,
)


# ---------------------------------------------------------------- 构造工具

def _obs(
    obs_id,
    cand_id="cand_1",
    trust=TrustLevel.SUSPECT,
    action=ActionType.REMEASURE,
    confidence=0.8,
    trust_conf=0.5,
    params=None,
):
    fa = FailureAttribution(top_cause="glare", confidence=confidence) if confidence else None
    na = (
        RecommendedAction(action=action, params=params or {}, reason="r")
        if action is not None
        else None
    )
    return ObservationObject(
        obs_id=obs_id,
        exp_id="exp_1",
        round_id=2,
        cand_id=cand_id,
        result=MeasuredResult(metric="quality", value=1.0),
        layout_meta=LayoutMeta(well_id=f"w_{obs_id}", row=0, col=0),
        trust=trust,
        trust_confidence=trust_conf,
        failure_attr=fa,
        next_action=na,
    )


def _agent_prop(decision_id, action="DISAMBIGUATION_REPEAT", target="cand_2", obs_id="obs_2",
                priority=None, params=None):
    content = {
        "action": action,
        "target": target,
        "obs_id": obs_id,
        "params": params or {},
        "reason": "agent 建议",
    }
    if priority is not None:
        content["priority"] = priority
    return DecisionRecord(
        decision_id=decision_id,
        round_id=2,
        actor=Actor.AGENT,
        kind=DecisionKind.ACTION_PROPOSAL,
        refs=[obs_id],
        content=content,
    )


# ---------------------------------------------------------------- collect_actions

def test_collect_endogenous_only():
    items = collect_actions([_obs("obs_1")], [])
    assert len(items) == 1
    it = items[0]
    assert it.source == "endogenous"
    assert it.action == ActionType.REMEASURE
    assert it.semantics == "detour"  # REMEASURE=detour（§13.2）
    assert it.supersedes == ("obs_1",)  # detour 顶替旧判
    assert it.target_cand_id == "cand_1"
    assert it.priority == 0.8  # failure_attr.confidence 优先


def test_collect_skips_trusted_and_none_action():
    obs_trusted = _obs("obs_t", trust=TrustLevel.TRUSTED)
    obs_none = _obs("obs_n", action=ActionType.NONE)
    obs_no_action = _obs("obs_x", action=None, confidence=0.0)
    assert collect_actions([obs_trusted, obs_none, obs_no_action], []) == []


def test_collect_priority_desc_sorted():
    a = _obs("obs_a", cand_id="cand_a", confidence=0.3)
    b = _obs("obs_b", cand_id="cand_b", confidence=0.9)
    c = _obs("obs_c", cand_id="cand_c", confidence=0.6)
    items = collect_actions([a, b, c], [])
    assert [i.priority for i in items] == [0.9, 0.6, 0.3]


def test_collect_dedupe_keeps_higher_priority():
    # 同 cand 同 action → 保 priority 高者
    lo = _obs("obs_lo", cand_id="cand_dup", confidence=0.2)
    hi = _obs("obs_hi", cand_id="cand_dup", confidence=0.95)
    items = collect_actions([lo, hi], [])
    assert len(items) == 1
    assert items[0].priority == 0.95
    assert items[0].target_obs_id == "obs_hi"


def test_collect_mixed_agent_and_endogenous():
    endo = _obs("obs_1", cand_id="cand_1", confidence=0.7)
    prop = _agent_prop("dec_1", priority=0.9)
    items = collect_actions([endo], [prop])
    assert len(items) == 2
    assert {i.source for i in items} == {"endogenous", "agent"}
    # 按 priority 降序：agent(0.9) 在前
    assert items[0].source == "agent"
    ag = items[0]
    assert ag.action == ActionType.DISAMBIGUATION_REPEAT
    assert ag.semantics == "detour"
    assert ag.target_cand_id == "cand_2"
    assert ag.target_obs_id == "obs_2"
    assert ag.supersedes == ("obs_2",)


def test_collect_agent_default_priority():
    items = collect_actions([], [_agent_prop("dec_1")])
    assert items[0].priority == 0.5  # content 无 priority → 缺省 0.5


def test_collect_agent_bad_content_skips_not_raises():
    """J-2：已 accept 的坏提案（缺 action）在消费侧降级 reject-after-the-fact——
    跳过 + on_skip 留痕，绝不裸抛（否则 append-only 重放每轮打停闭环）。"""
    bad = DecisionRecord(
        decision_id="dec_bad", round_id=2, actor=Actor.AGENT,
        kind=DecisionKind.ACTION_PROPOSAL, content={"target": "cand_x"},  # 缺 action
    )
    skipped = []
    items = collect_actions([], [bad], on_skip=lambda did, why: skipped.append((did, why)))
    assert items == []
    assert skipped and skipped[0][0] == "dec_bad" and "action" in skipped[0][1]


def test_collect_agent_illegal_actiontype_skips_not_raises():
    bad = _agent_prop("dec_bad", action="FLY_TO_MOON")
    skipped = []
    items = collect_actions([], [bad], on_skip=lambda did, why: skipped.append((did, why)))
    assert items == []
    assert skipped and "ActionType" in skipped[0][1]


def test_collect_agent_poison_params_skips_not_raises():
    """J-2 毒丸本体：params 非 dict（"oops"）曾令 `dict("oops")` 裸抛 ValueError。
    现经共用校验降级跳过，不打停闭环。"""
    bad = DecisionRecord(
        decision_id="dec_poison", round_id=2, actor=Actor.AGENT,
        kind=DecisionKind.ACTION_PROPOSAL,
        content={"action": "REMEASURE", "target": "cand_x", "params": "oops"},
    )
    skipped = []
    items = collect_actions([], [bad], on_skip=lambda did, why: skipped.append((did, why)))
    assert items == []
    assert skipped and "params" in skipped[0][1]


def test_collect_agent_bad_content_no_sink_silent_skip():
    """无 on_skip（纯上下文）时坏提案仍安静跳过，不抛。"""
    bad = _agent_prop("dec_bad", action="FLY_TO_MOON")
    assert collect_actions([], [bad]) == []


def test_collect_ignores_non_action_proposal():
    other = DecisionRecord(
        decision_id="dec_g", round_id=2, actor=Actor.AGENT,
        kind=DecisionKind.PRIOR_PROPOSAL, content={"action": "REMEASURE"},
    )
    assert collect_actions([], [other]) == []


def test_collect_deterministic():
    obss = [_obs(f"obs_{i}", cand_id=f"cand_{i}", confidence=0.5) for i in range(4)]
    props = [_agent_prop(f"dec_{i}", target=f"cand_a{i}", obs_id=f"obs_a{i}", priority=0.5)
             for i in range(3)]
    r1 = collect_actions(obss, props)
    r2 = collect_actions(obss, props)
    assert [i.item_uid for i in r1] == [i.item_uid for i in r2]


# ---------------------------------------------------------------- J-4 priority 无界+NaN

def test_agent_priority_clamped_to_unit_interval():
    """J-4：agent priority=1e9 钳到 1.0（不再压倒内生补救的排序）。"""
    items = collect_actions([], [_agent_prop("dec_big", priority=1e9)])
    assert items[0].priority == 1.0


def test_agent_priority_negative_clamped_to_zero():
    items = collect_actions([], [_agent_prop("dec_neg", priority=-5.0)])
    assert items[0].priority == 0.0


def test_agent_nan_priority_rejected_via_skip():
    """J-4：NaN/inf priority 破坏全序——校验层拒（降级跳过，不进队列）。"""
    for bad_p in (float("nan"), float("inf"), float("-inf")):
        skipped = []
        items = collect_actions(
            [], [_agent_prop("dec_nan", priority=bad_p)],
            on_skip=lambda did, why: skipped.append(why),
        )
        assert items == [] and skipped and "priority" in skipped[0]


def test_endogenous_beats_agent_on_same_target_action():
    """J-4（红队建议）：同 (action,target) 去重时内生项恒优先于 agent 项，
    即便 agent priority 更高——防 agent 替换内生消歧几何。"""
    endo = _obs("obs_e", cand_id="cand_dup", action=ActionType.REMEASURE, confidence=0.2)
    prop = _agent_prop("dec_a", action="REMEASURE", target="cand_dup", priority=0.99)
    items = collect_actions([endo], [prop])
    assert len(items) == 1
    assert items[0].source == "endogenous"
    assert items[0].priority == 0.2


# ---------------------------------------------------------------- validate_proposal_content

def test_validate_proposal_content_accepts_legit():
    from expos.planner.arbiter import validate_proposal_content
    ok = {"action": "REMEASURE", "target": "cand_1",
          "params": {"n_wells": 3, "placement_hint": "center_only"}, "priority": 0.7}
    assert validate_proposal_content(ok) is None


def test_validate_proposal_content_rejects_all_bad_surfaces():
    from expos.planner.arbiter import validate_proposal_content
    assert validate_proposal_content("not a dict") is not None
    assert validate_proposal_content({}) is not None                       # 缺 action
    assert validate_proposal_content({"action": "FLY"}) is not None         # 非法 ActionType
    assert validate_proposal_content({"action": "REMEASURE", "params": "x"}) is not None
    assert validate_proposal_content(
        {"action": "ADD_CONTROLS", "params": {"n_controls": 0}}) is not None  # 非正整数
    assert validate_proposal_content(
        {"action": "NEW_CANDIDATES", "params": {"n": -1}}) is not None
    assert validate_proposal_content(
        {"action": "REMEASURE", "params": {"n_wells": 2.5}}) is not None      # 非整
    assert validate_proposal_content(
        {"action": "REMEASURE", "params": {"placement_hint": "bogus"}}) is not None
    assert validate_proposal_content(
        {"action": "REMEASURE", "priority": float("nan")}) is not None


# ---------------------------------------------------------------- well_cost / arbitrate

def _item(uid, action=ActionType.REMEASURE, priority=0.5, placement_hint=None, params=None):
    from expos.planner.arbiter import _semantics_of
    return ActionItem(
        item_uid=uid, action=action, semantics=_semantics_of(action),
        target_cand_id="cand_x", target_obs_id="obs_x", params=params or {},
        placement_hint=placement_hint, supersedes=(), source="endogenous", priority=priority,
    )


def test_well_cost_variants():
    assert well_cost(_item("a", ActionType.REMEASURE), replicates=3) == 3
    assert well_cost(_item("b", ActionType.DISAMBIGUATION_REPEAT,
                            placement_hint="edge_center_pair"), replicates=3) == 2
    assert well_cost(_item("c", ActionType.ADD_CONTROLS, params={"n_controls": 4}), 3) == 4
    assert well_cost(_item("d", ActionType.NEW_CANDIDATES, params={"n": 5}), 3) == 5
    assert well_cost(_item("e", ActionType.REMEASURE, params={"n_wells": 7}), 3) == 7


def test_arbitrate_budget_cap_and_overflow():
    # 每个复测 3 孔，预算 7 → 入选 2 个（6 孔），第 3 个溢出
    acts = [_item(f"i{i}", priority=1.0 - i * 0.1) for i in range(3)]
    admitted, overflow = arbitrate(acts, n_wells_for_actions=7, replicates=3)
    assert len(admitted) == 2
    assert len(overflow) == 1
    assert overflow[0].item_uid == "i2"


def test_arbitrate_exact_fit():
    acts = [_item(f"i{i}") for i in range(2)]
    admitted, overflow = arbitrate(acts, n_wells_for_actions=6, replicates=3)
    assert len(admitted) == 2 and overflow == []


def test_arbitrate_zero_budget():
    admitted, overflow = arbitrate([_item("i0")], 0, replicates=3)
    assert admitted == [] and len(overflow) == 1


def test_arbitrate_skips_oversized_but_admits_later_small():
    # 贪心：大动作放不下溢出，后续小动作仍可入选
    big = _item("big", ActionType.NEW_CANDIDATES, priority=1.0, params={"n": 10})
    small = _item("small", ActionType.REMEASURE, priority=0.9, params={"n_wells": 2})
    admitted, overflow = arbitrate([big, small], n_wells_for_actions=4, replicates=3)
    assert [i.item_uid for i in admitted] == ["small"]
    assert [i.item_uid for i in overflow] == ["big"]


def test_arbitrate_invalid_inputs_loud():
    with pytest.raises(ArbiterError):
        arbitrate([], -1, replicates=3)
    with pytest.raises(ArbiterError):
        arbitrate([], 5, replicates=0)


# ---------------------------------------------------------------- discounted_scores

def test_discounted_scores_normalizes_then_multiplies():
    scores = np.array([0.0, 5.0, 10.0])
    out = discounted_scores(scores, p_artifact_opt=0.0)
    # min-max 归一化 → [0, 0.5, 1]，p=0 → 因子 max(1,0.5)=1
    assert np.allclose(out, [0.0, 0.5, 1.0])


def test_discounted_scores_min_filter_clamps_at_half():
    scores = np.array([0.0, 10.0])
    out = discounted_scores(scores, p_artifact_opt=0.9)
    # 归一化 [0,1]，p=0.9 → max(0.1,0.5)=0.5
    assert np.allclose(out, [0.0, 0.5])


def test_discounted_scores_moderate_p_not_clamped():
    scores = np.array([0.0, 10.0])
    out = discounted_scores(scores, p_artifact_opt=0.3)
    # p=0.3 → max(0.7,0.5)=0.7，未触发钳位
    assert np.allclose(out, [0.0, 0.7])


def test_discounted_scores_degenerate_all_equal():
    out = discounted_scores(np.array([4.0, 4.0, 4.0]), p_artifact_opt=0.0)
    # 全等 → 归一化全 1（不被清零）
    assert np.allclose(out, [1.0, 1.0, 1.0])


def test_discounted_scores_per_well_array_p():
    scores = np.array([0.0, 10.0])
    out = discounted_scores(scores, p_artifact_opt=np.array([0.9, 0.0]))
    # 逐孔折扣：[0*0.5, 1*1.0]
    assert np.allclose(out, [0.0, 1.0])


def test_discounted_scores_empty():
    assert discounted_scores(np.array([]), 0.5).size == 0


# ---------------------------------------------------------------- J-6 discounted_scores 响亮

def test_discounted_scores_nan_scores_loud():
    """J-6：一个 NaN 会让归一化产 NaN、下游 argsort 静默乱序——入口响亮拒。"""
    with pytest.raises(ArbiterError):
        discounted_scores(np.array([1.0, np.nan, 3.0]), 0.2)


def test_discounted_scores_inf_scores_loud():
    with pytest.raises(ArbiterError):
        discounted_scores(np.array([1.0, np.inf]), 0.2)


def test_discounted_scores_p_out_of_range_loud():
    """J-6：p<0 会令折扣因子>1 反转为放大；p>1 同样越界——响亮拒。"""
    with pytest.raises(ArbiterError):
        discounted_scores(np.array([0.0, 1.0]), -0.1)
    with pytest.raises(ArbiterError):
        discounted_scores(np.array([0.0, 1.0]), 1.5)


def test_discounted_scores_p_nan_loud():
    with pytest.raises(ArbiterError):
        discounted_scores(np.array([0.0, 1.0]), float("nan"))
    with pytest.raises(ArbiterError):
        discounted_scores(np.array([0.0, 1.0]), np.array([0.2, np.nan]))


# ---------------------------------------------------------------- exploration_quota

def test_exploration_quota_default():
    assert exploration_quota(48) == 6  # round(48*0.12=5.76)=6


def test_exploration_quota_custom_frac():
    assert exploration_quota(40, frac=0.15) == 6
    assert exploration_quota(10, frac=0.1) == 1


def test_exploration_quota_bounds_and_invalid():
    assert exploration_quota(0) == 0
    with pytest.raises(ArbiterError):
        exploration_quota(-1)
    with pytest.raises(ArbiterError):
        exploration_quota(10, frac=1.5)


# ---------------------------------------------------------------- actions_to_candidates

def test_actions_to_candidates_lookup_and_hint():
    it = _item("i0", ActionType.DISAMBIGUATION_REPEAT, placement_hint="edge_center_pair")
    lookup = {"cand_x": {"supersat": 1.2, "additive": 0.01}}
    cands = actions_to_candidates([it], lookup)
    assert len(cands) == 1
    c = cands[0]
    assert c.params == {"supersat": 1.2, "additive": 0.01}
    assert c.placement_hint == "edge_center_pair"
    assert c.source == "arbiter:endogenous"
    assert c.parent_obs_id == "obs_x"


def test_actions_to_candidates_skips_non_candidate_actions():
    ctrl = _item("i_ctrl", ActionType.ADD_CONTROLS, params={"n_controls": 2})
    newc = _item("i_new", ActionType.NEW_CANDIDATES, params={"n": 3})
    assert actions_to_candidates([ctrl, newc], {}) == []


def test_actions_to_candidates_missing_lookup_loud():
    it = _item("i0", ActionType.REMEASURE)
    with pytest.raises(ArbiterError):
        actions_to_candidates([it], {})  # cand_x 不在查表 → 响亮


def test_actions_to_candidates_missing_target_cand_loud():
    it = ActionItem(
        item_uid="i0", action=ActionType.REMEASURE, semantics="detour",
        target_cand_id=None, target_obs_id="obs_x", params={}, placement_hint=None,
        supersedes=(), source="agent", priority=0.5,
    )
    with pytest.raises(ArbiterError):
        actions_to_candidates([it], {"cand_x": {}})


# ---------------------------------------------------------------- 依赖隔离红线

def test_arbiter_no_forbidden_imports():
    import ast

    import expos.planner.arbiter as mod
    src = open(mod.__file__, encoding="utf-8").read()
    # 只查真实 import 语句（docstring 里的名词不算），红线：不 import loop/adapters/models/agent
    forbidden = ("expos.loop", "expos.adapters", "expos.models", "expos.agent")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not any(node.module.startswith(f) for f in forbidden), node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(alias.name.startswith(f) for f in forbidden), alias.name
    # 不读 truth sidecar（公理 6）：代码里不出现 truth 路径引用
    assert "truth/" not in src and "truth_records" not in src


def test_arbiter_error_is_exposerror():
    assert issubclass(ArbiterError, ExposError)


# ---------------------------------------------------------------- 策略接线判别（压测 R1-2a/b）
# 只断言名字是上一轮压测的靶子——这一节直接断言机制"生效"：折扣因子确实 <1、
# 风险图确实随失败历史起伏。

from types import SimpleNamespace

from expos.kernel.objects import MaterialMeta
from expos.planner import policy as _pol
from expos.qc.failure_model import FailureModel


def _fm_obs(trust, *, is_edge=False, block_id="Q0", solution_batch="R0-B0", round_id=0):
    """FailureModel 计数用观测（provenance 桶键 + trust 裁决即可）。"""
    return ObservationObject(
        exp_id="exp_fm", round_id=round_id, cand_id="cand_fm",
        result=MeasuredResult(metric="m", value=0.5),
        layout_meta=LayoutMeta(well_id="A1", row=0, col=0,
                               is_edge=is_edge, block_id=block_id),
        material_meta=MaterialMeta(solution_batch=solution_batch),
        trust=trust,
    )


class _StubModel:
    """只提供 _generate 所需面（n_train/kappa/score_pool）的最小模型替身。"""

    n_train = 99
    kappa = 0.0

    def score_pool(self, pool):
        return np.linspace(0.0, 1.0, pool.shape[0])


def _stub_ctx():
    return _pol.PlanContext(
        cfg=SimpleNamespace(design_space=None, restrictions=[]),
        store=None, model=_StubModel(), round_id=0, seed=0, n_cands=6, kappa=1.5,
    )


def test_risk_discount_generator_scores_differ_from_pure_ucb(monkeypatch):
    """判别（R1-2a）：p_global>0 时 risk_discount 生成器的池评分 = 归一化 UCB × 折扣
    因子 max(1−p̄, 0.5) < 1——修复前 .get("global_rate", 0.0) 令因子恒 1、与纯 UCB 逐位相同。"""
    # 失败历史 2/4 → p̄ = 0.5
    fm = FailureModel().rebuild([
        _fm_obs(TrustLevel.SUSPECT), _fm_obs(TrustLevel.FAILED),
        _fm_obs(TrustLevel.TRUSTED), _fm_obs(TrustLevel.TRUSTED),
    ])
    assert fm.summary()["p_global"] == pytest.approx(0.5)
    captured = {}

    def _capture_propose(space, n, seed, score_fn, restrictions):
        pool = np.zeros((6, 3))
        captured["scores"] = score_fn(pool)
        return []

    monkeypatch.setattr(_pol, "propose_candidates", _capture_propose)
    ctx = _stub_ctx()
    _pol.TrustAwarePlanner()._generate(
        ctx, "response_gp+ucb+risk_discount", 4, fm, gen_seed=0
    )
    raw = _StubModel().score_pool(np.zeros((6, 3)))
    got = captured["scores"]
    assert np.allclose(got, discounted_scores(raw, 0.5))       # 折扣因子 0.5 生效
    assert not np.allclose(got, discounted_scores(raw, 0.0))   # ≠ 纯 UCB（修复前逐位相同）


def test_risk_discount_missing_contract_key_fails_loudly():
    """判别（R1-2a 契约面）：summary() 缺 'p_global' → 响亮 PlannerError，不许默认值兜底。"""

    class _BadFM:
        def summary(self):
            return {"global_rate": 0.5}   # 旧键名：正是当年静默失配的形状

    with pytest.raises(_pol.PlannerError):
        _pol.TrustAwarePlanner()._generate(
            _stub_ctx(), "response_gp+ucb+risk_discount", 4, _BadFM(), gen_seed=0
        )


def test_adjudicate_gate_rejects_poison_pill(tmp_path):
    """J-2 闸门侧：毒丸提案（params 非 dict）被 `_adjudicate_proposals` reject 在门外，
    永不进 accepted 集——共用 validate_proposal_content 补齐了旧闸门只查 action/target
    的解析面缺口。"""
    from expos.kernel.lifecycle import accepted_proposals, submit_proposal
    from expos.kernel.store import RunStore

    store = RunStore(tmp_path / "run")
    poison = DecisionRecord(
        decision_id="dec_poison", round_id=0, actor=Actor.AGENT,
        kind=DecisionKind.ACTION_PROPOSAL, refs=["obs_x"],
        content={"action": "REMEASURE", "target": "cand_x", "params": "oops"},
    )
    submit_proposal(store, poison)
    _pol.TrustAwarePlanner()._adjudicate_proposals(store)
    assert accepted_proposals(store) == []  # 未进 accepted
    rejections = [d for d in store.list_decisions()
                  if d.kind == DecisionKind.REJECTION and "dec_poison" in d.refs]
    assert rejections and "params" in rejections[0].content["reason"]


def _plate_cfg():
    return SimpleNamespace(plate=SimpleNamespace(rows=6, cols=8))


def test_plate_risk_map_edge_history_raises_edge_wells():
    """判别（R1-2b）：上一轮边缘高失败史 → 下一轮 _plate_risk_map 边缘孔 > 中心孔且
    非常数——修复前 solution_batch=None 恒 miss 真实批次桶、全板同值。走生产真实路径：
    本轮新批标签的精确桶为空 → 失败模型层级回退到批次边际。"""
    obs = []
    for block in ("Q0", "Q1", "Q2", "Q3"):
        for _ in range(4):
            obs.append(_fm_obs(TrustLevel.FAILED, is_edge=True, block_id=block,
                               solution_batch="R0-B0", round_id=0))
        obs.append(_fm_obs(TrustLevel.TRUSTED, is_edge=True, block_id=block,
                           solution_batch="R0-B1", round_id=0))
        for _ in range(5):
            obs.append(_fm_obs(TrustLevel.TRUSTED, is_edge=False, block_id=block,
                               solution_batch="R0-B0", round_id=0))
    fm = FailureModel().rebuild(obs)
    rm = _pol._plate_risk_map(_plate_cfg(), fm, round_id=1)  # 同 round_band r0-1
    assert len(set(rm.values())) > 1, "风险图仍是常数（修复前的靶子）"
    from expos.design.layout import well_id_of
    edge_vals = [rm[well_id_of(r, c)] for r in range(6) for c in range(8)
                 if r in (0, 5) or c in (0, 7)]
    center_vals = [rm[well_id_of(r, c)] for r in range(1, 5) for c in range(1, 7)]
    assert min(edge_vals) > max(center_vals), "边缘失败史未抬高边缘孔风险"


def test_plate_risk_map_batch_checkerboard_bucket_hit():
    """判别（R1-2b 批次维）：某批高失败史 → 该批棋盘奇偶格风险更高——证明每孔批次
    标签与执行面公式 f"R{round}-B{(row+col)%2}" 对齐、真实批次桶被命中。"""
    obs = []
    for _ in range(8):
        obs.append(_fm_obs(TrustLevel.FAILED, is_edge=False, block_id="Q0",
                           solution_batch="R5-B1", round_id=5))
    for _ in range(8):
        obs.append(_fm_obs(TrustLevel.TRUSTED, is_edge=False, block_id="Q0",
                           solution_batch="R5-B0", round_id=5))
    fm = FailureModel().rebuild(obs)
    rm = _pol._plate_risk_map(_plate_cfg(), fm, round_id=5)
    from expos.design.layout import well_id_of
    # 同 block（Q0 象限）、同非边缘、相邻奇偶格：奇格批 R5-B1（高失败）> 偶格批 R5-B0
    assert rm[well_id_of(2, 3)] > rm[well_id_of(2, 2)]
    assert rm[well_id_of(1, 2)] > rm[well_id_of(1, 1)]


def test_plate_risk_map_batch_signal_survives_across_rounds():
    """FM3 判别（cross-round）：批次污染史来自**过去轮**，规划**后续轮**风险图仍须让
    污染批棋盘格 > 干净批。修复前 solution_batch=R{round}-B{k} 铸新标签查询恒 miss 历史
    桶、落批次边际把 B0/B1 抹平到同值——本断言恒假（红）；②去轮次化 + 跨轮同批边际回退
    后，学到的批次信号进图（绿）。"""
    from expos.design.layout import well_id_of
    obs = []
    # round 0（band r0-1）：批 B1 污染（非边缘高失败），批 B0 干净；同 block Q0。
    for _ in range(8):
        obs.append(_fm_obs(TrustLevel.FAILED, is_edge=False, block_id="Q0",
                           solution_batch="R0-B1", round_id=0))
    for _ in range(8):
        obs.append(_fm_obs(TrustLevel.TRUSTED, is_edge=False, block_id="Q0",
                           solution_batch="R0-B0", round_id=0))
    fm = FailureModel().rebuild(obs)
    # 规划 round 2（band r2-3，与观测不同 band）：本轮精确桶必空 → 跨轮同批边际接住 B1 史。
    rm = _pol._plate_risk_map(_plate_cfg(), fm, round_id=2)
    # 同 block Q0 象限、同非边缘、相邻奇偶格：奇格=B1（污染）应 > 偶格=B0（干净）。
    assert rm[well_id_of(2, 3)] > rm[well_id_of(2, 2)], "跨轮批次信号未进风险图（FM3 回归）"
    assert rm[well_id_of(1, 2)] > rm[well_id_of(1, 1)]


# ---------------------------------------------------------------- J-7：supersedes 记账字段（无消费者）

def test_supersedes_is_bookkeeping_only():
    """J-7 现状锁定：supersedes 是**记账字段，顶替语义未实现**（Backlog M13）。

    ① 审计可见：detour 动作确实把牵连旧 obs 记入 supersedes；
    ② 无行为消费者：两条仅 supersedes 不同、其余全等的动作，经 arbitrate + well_cost +
       actions_to_candidates 产出逐字段相同——证明没有任何仲裁路径读它来"顶替旧判/门控
       归因"。此测试防未来静默启用绕过信任语义（真要实现须走 M13 并同步契约）。
    """
    # ① detour 记账可见（collect 侧）
    it = collect_actions([_obs("obs_1")], [])[0]
    assert it.semantics == "detour" and it.supersedes == ("obs_1",)

    # ② 顶替语义无行为：构造两条只差 supersedes 的 REMEASURE 动作
    def _mk(supersedes):
        return ActionItem(
            item_uid="agent:dec_x", action=ActionType.REMEASURE, semantics="detour",
            target_cand_id="cand_1", target_obs_id="obs_1", params={},
            placement_hint=None, supersedes=supersedes, source="agent", priority=0.5,
        )
    with_sup = _mk(("obs_1", "obs_2"))   # 声称顶替两条旧判
    without_sup = _mk(())                # 不顶替任何旧判
    lookup = {"cand_1": {"x": 1.0}}

    # well_cost 不看 supersedes
    assert well_cost(with_sup, replicates=3) == well_cost(without_sup, replicates=3)
    # arbitrate 入选/溢出不看 supersedes
    a1, o1 = arbitrate([with_sup], n_wells_for_actions=3, replicates=3)
    a2, o2 = arbitrate([without_sup], n_wells_for_actions=3, replicates=3)
    assert len(a1) == len(a2) == 1 and len(o1) == len(o2) == 0
    # 物化候选：除 supersedes 外一切相同（supersedes 根本不进 Candidate）
    c1 = actions_to_candidates([with_sup], lookup)[0]
    c2 = actions_to_candidates([without_sup], lookup)[0]
    d1 = c1.model_dump(exclude={"cand_id"})  # cand_id 是随机 uuid，非 supersedes 派生
    assert d1 == c2.model_dump(exclude={"cand_id"})
    assert "supersedes" not in d1  # 顶替信息未流入任何下游产物
