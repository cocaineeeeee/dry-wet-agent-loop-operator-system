"""变异语料击杀：agent/policy.py TemplateAgentPolicy.after_round（MU2 D1/D2/D3）。

after_round 全仓零测试（红队 0% 击杀）。这三条是 R1 修复的回归护栏：
- D1 [P1]：本轮 SUSPECT 过滤 `o.round_id == round_id`。翻转成 != 后只提交**跨轮旧
  嫌疑**，本轮嫌疑反被丢弃。钉：提交提案的 obs 全部属于本轮 SUSPECT 集合。
- D2 [P1]：先放开枚举（batch_size=len(obs)）再过滤后封顶。改回 batch_size=self.batch_size
  在全历史 SUSPECT 上截断——旧轮嫌疑排在前面会挤光本轮名额。钉：旧轮嫌疑多于
  batch_size 时本轮提案仍被提交。
- D3 [P2]：提交封顶 `submitted >= batch_size`。改成 > 后多放一条。钉：恰好 batch_size 条。
"""

from expos.agent.policy import TemplateAgentPolicy
from expos.kernel.lifecycle import unresolved_proposals
from expos.kernel.objects import (
    ActionType,
    Actor,
    DecisionKind,
    LayoutMeta,
    MeasuredResult,
    ObservationObject,
    RecommendedAction,
    Routing,
    TrustLevel,
)
from expos.kernel.store import RunStore


def _suspect(obs_id: str, well: str, col: int, round_id: int) -> ObservationObject:
    o = ObservationObject(
        obs_id=obs_id, exp_id="exp_x", round_id=round_id, cand_id="cand_" + obs_id,
        result=MeasuredResult(metric="quality", value=0.3),
        layout_meta=LayoutMeta(well_id=well, row=0, col=col),
        next_action=RecommendedAction(
            action=ActionType.DISAMBIGUATION_REPEAT,
            params={"placement_hint": "center_only"},
            reason=f"{obs_id} 嫌疑，建议中心复现消歧",
        ),
    )
    o.trust, o.routing, o.trust_confidence = TrustLevel.SUSPECT, Routing.TO_FAILURE_MODEL, 0.7
    return o


def _store_with(tmp_path, obs_list):
    store = RunStore(tmp_path / "run")
    for o in obs_list:
        store.save_observation(o)
    return store


def _submitted_obs_ids(store):
    return {(p.content or {}).get("obs_id") for p in unresolved_proposals(store)}


def _round_of(store, obs_id):
    return {o.obs_id: o.round_id for o in store.list_observations()}[obs_id]


# ------------------------------------------------------------------ D1
def test_after_round_only_submits_this_round_suspects(tmp_path):
    """混合旧轮(0)与本轮(1) SUSPECT，after_round(round_id=1) 只提交本轮嫌疑的提案。
    过滤翻转 (==→!=) 会改成只提交旧轮嫌疑 → 本断言必红。"""
    obs = [
        _suspect("old_a", "A1", 0, round_id=0),
        _suspect("old_b", "A2", 1, round_id=0),
        _suspect("cur_a", "B1", 0, round_id=1),
        _suspect("cur_b", "B2", 1, round_id=1),
    ]
    store = _store_with(tmp_path, obs)
    TemplateAgentPolicy(batch_size=5).after_round(store, None, round_id=1)
    submitted = _submitted_obs_ids(store)
    assert submitted, "本轮有 SUSPECT，应至少提交一条本轮提案"
    assert all(_round_of(store, oid) == 1 for oid in submitted), \
        f"提交了非本轮提案: {submitted}"


# ------------------------------------------------------------------ D2
def test_after_round_this_round_not_starved_by_old_suspects(tmp_path):
    """旧轮(0)嫌疑多于 batch_size 且排在枚举前列——本轮(1)提案仍必须被提交。
    若把枚举截断改回 batch_size（先截后过滤），旧轮嫌疑会挤光名额 → 本轮 0 提交。"""
    obs = [_suspect(f"old_{i}", f"A{i + 1}", i, round_id=0) for i in range(5)]  # 5 > batch_size
    obs += [_suspect("cur_a", "B1", 0, round_id=1),
            _suspect("cur_b", "B2", 1, round_id=1)]
    store = _store_with(tmp_path, obs)
    TemplateAgentPolicy(batch_size=3).after_round(store, None, round_id=1)
    submitted = _submitted_obs_ids(store)
    assert {"cur_a", "cur_b"} <= submitted, \
        f"本轮提案被旧轮嫌疑截断挤光: {submitted}"


# ------------------------------------------------------------------ D3
def test_after_round_submission_cap_is_exact(tmp_path):
    """本轮 SUSPECT 多于 batch_size：恰好提交 batch_size 条（不多放一条）。
    封顶 >= → > 会多提交一条。"""
    obs = [_suspect(f"cur_{i}", f"C{i + 1}", i, round_id=0) for i in range(5)]
    store = _store_with(tmp_path, obs)
    TemplateAgentPolicy(batch_size=3).after_round(store, None, round_id=0)
    assert len(unresolved_proposals(store)) == 3


# ------------------------------------------------------------------ narrative gate
# NARR3 red-team fix (mailbox/red_to_blue/029, [P2]): the round narrative must bind
# its reported submission count to the *actual* number of submit_proposal calls made
# this round, not to the count of candidate/identified actions (which batch_size can
# cap well below). This test recomputes the ground truth from real ACTION_PROPOSAL
# decisions rather than trusting the policy's internal counter, so a regression that
# reintroduces "submitted == identified" (or any other derived-from-candidates number)
# fails it.
def test_narrative_submitted_count_matches_actual_submit_proposal_calls(tmp_path):
    obs = [_suspect(f"cur_{i}", f"C{i + 1}", i, round_id=0) for i in range(5)]
    store = _store_with(tmp_path, obs)
    TemplateAgentPolicy(batch_size=3).after_round(store, None, round_id=0)

    actual_submitted = len([
        d for d in store.list_decisions()
        if d.kind == DecisionKind.ACTION_PROPOSAL
        and d.actor == Actor.AGENT
        and d.round_id == 0
    ])
    assert actual_submitted == 3  # batch_size cap, strictly below the 5 identified

    rationale = next(
        d for d in store.list_decisions()
        if d.kind == DecisionKind.ROUND_RATIONALE and d.round_id == 0
    )
    assert rationale.content["n_submitted"] == actual_submitted
    assert rationale.content["n_queued_actions"] == 5  # identified count, unchanged
    assert f"submitted {actual_submitted}" in rationale.content["narrative"]
