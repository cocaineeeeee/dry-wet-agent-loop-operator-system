"""M8 端到端：agent 提案 → 规划器裁定 → 仲裁消费 → 轮次叙述落账 的完整参与流。

红线（公理 7）在真实 run 上的机器检查：agent 只有提案权与解释权——
所有 acceptance/rejection 出自 ADJUDICATOR_ACTORS；提案-裁定 append-only 配对；
naive 臂零 agent 痕迹（对照公平性）。
"""

from pathlib import Path

import pytest

from expos.kernel.lifecycle import ADJUDICATOR_ACTORS, unresolved_proposals
from expos.kernel.objects import Actor, DecisionKind
from expos.kernel.store import RunStore
from expos.loop import run_loop

ROOT = Path(__file__).resolve().parent.parent
CRYSTAL = ROOT / "domains" / "crystal.yaml"


@pytest.fixture(scope="module")
def os_run5(tmp_path_factory):
    """5 轮 crystal os：第 3 轮强边缘事件 → 嫌疑观测 → agent 提案 → 第 4 轮裁定。"""
    out = tmp_path_factory.mktemp("runs") / "m8_os"
    summary = run_loop(CRYSTAL, mode="os", rounds=5, seed=7, out_dir=out)
    return out, summary


def test_agent_proposals_submitted(os_run5):
    out, _ = os_run5
    store = RunStore(out, create=False)
    props = [d for d in store.list_decisions()
             if d.kind == DecisionKind.ACTION_PROPOSAL and d.actor == Actor.AGENT]
    assert props, "第 3 轮强边缘事件应触发 agent ACTION_PROPOSAL"
    # 提案 content 契约（arbiter._agent_items 的解析面）
    for p in props:
        assert "action" in p.content and "obs_id" in p.content


def test_proposals_adjudicated_by_planner_only(os_run5):
    out, _ = os_run5
    store = RunStore(out, create=False)
    resolutions = [d for d in store.list_decisions()
                   if d.kind in (DecisionKind.ACCEPTANCE, DecisionKind.REJECTION)]
    assert resolutions, "规划器应在下一轮开场对提案落 acceptance/rejection 配对"
    for d in resolutions:
        assert d.actor in ADJUDICATOR_ACTORS  # agent 永不自裁（公理 7）
        assert d.refs, "裁定必须 refs 指向被裁提案（append-only 配对审计）"
    # 至少一条接受（第 3 轮嫌疑观测的动作目标是在案候选）
    assert any(d.kind == DecisionKind.ACCEPTANCE for d in resolutions)


def test_only_tail_round_proposals_may_dangle(os_run5):
    """审计不变量：跑完的轮次无悬案——未决 ACTION_PROPOSAL 只允许出现在
    最后一轮（其后没有 plan_round 来裁定，属诚实的边界而非泄漏）。"""
    out, summary = os_run5
    store = RunStore(out, create=False)
    last = summary["rounds_completed"] - 1
    dangling = [d for d in unresolved_proposals(store)
                if d.kind == DecisionKind.ACTION_PROPOSAL]
    assert all(d.round_id == last for d in dangling), (
        f"非尾轮存在未决提案: {[(d.decision_id, d.round_id) for d in dangling]}"
    )


def test_round_rationale_every_round(os_run5):
    out, summary = os_run5
    store = RunStore(out, create=False)
    narr = [d for d in store.list_decisions()
            if d.kind == DecisionKind.ROUND_RATIONALE]
    assert {d.round_id for d in narr} == set(range(summary["rounds_completed"]))
    for d in narr:
        assert d.actor == Actor.AGENT
        assert d.content.get("narrative")  # 引用真实数字的中文叙述（模板后端保证）


def test_naive_arm_has_zero_agent_trace(tmp_path):
    """对照公平性：naive 臂注入 NullAgentPolicy——决策日志零 agent 记录。
    顺带验收 run 级开收事件（event-model 纪律，M10）。"""
    out = tmp_path / "m8_naive"
    run_loop(CRYSTAL, mode="naive", rounds=2, seed=7, out_dir=out)
    store = RunStore(out, create=False)
    assert [d for d in store.list_decisions() if d.actor == Actor.AGENT] == []
    # run_start provably-first（首条事件）+ run_stop 显式 success 终态
    events = store.read_events()
    assert events[0]["kind"] == "run_start"
    assert events[0]["payload"]["mode"] == "naive"
    stops = [e for e in events if e["kind"] == "run_stop"]
    assert len(stops) == 1 and stops[0]["payload"]["exit_status"] == "success"
