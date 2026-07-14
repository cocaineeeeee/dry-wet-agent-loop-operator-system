"""M6+M7 联合端到端：归因→next_action→仲裁消费→消歧钉中心 的完整动作流。"""

from pathlib import Path

import pytest

from expos.kernel.objects import ActionType, TrustLevel
from expos.kernel.store import RunStore
from expos.loop import run_loop

ROOT = Path(__file__).resolve().parent.parent
CRYSTAL = ROOT / "domains" / "crystal.yaml"


@pytest.fixture(scope="module")
def os_run5(tmp_path_factory):
    """5 轮 crystal os：第 3 轮强边缘事件 → 第 4 轮应消费消歧动作。"""
    out = tmp_path_factory.mktemp("runs") / "m6m7_os"
    summary = run_loop(CRYSTAL, mode="os", rounds=5, seed=7, out_dir=out)
    return out, summary


def test_round3_suspects_are_attributed(os_run5):
    out, _ = os_run5
    store = RunStore(out, create=False)
    r3_suspects = [o for o in store.list_observations(trust=TrustLevel.SUSPECT)
                   if o.round_id == 3]
    assert len(r3_suspects) >= 10
    attributed = [o for o in r3_suspects if o.failure_attr is not None]
    assert len(attributed) >= 10
    # 饱和态限制（strength=0.5 污染 36/47 孔=77% 板面）："干净多数"假设崩溃，
    # 归因歧义是预期物理——检测在此幅度是送分题、归因有最优幅度窗口
    # （M9 归因精度-幅度曲线的动机）。此处断言安全关键不变量：
    causes = {o.failure_attr.top_cause for o in attributed}
    assert "edge_evaporation" in causes  # 明确 edge 归因存在
    # 所有归因的 remedy 落在合理动作集（无论归到哪个空间性假设，
    # 补救都是复测/跨批重复/消歧——不会产生危险动作）
    sane = {ActionType.DISAMBIGUATION_REPEAT, ActionType.REPEAT_CANDIDATE,
            ActionType.REMEASURE, ActionType.ADD_CONTROLS, ActionType.NONE}
    for o in attributed:
        if o.next_action is not None:
            assert o.next_action.action in sane
    # 安全底线：这 40 个嫌疑观测无一进入响应模型（在下方专项测试再断言全局版）
    # 归因事件落账
    attr_events = [e for e in store.read_events("attribution")
                   if e["payload"]["round_id"] == 3]
    assert len(attr_events) == len(r3_suspects)


def test_disambiguation_actions_generated_and_consumed(os_run5):
    """M6 产动作 → M7 仲裁消费：第 4 轮 provenance 记账 + 消歧候选钉中心。"""
    out, _ = os_run5
    store = RunStore(out, create=False)
    # 第 3 轮的边缘嫌疑观测带 DISAMBIGUATION next_action
    r3_actions = [o.next_action for o in store.list_observations(trust=TrustLevel.SUSPECT)
                  if o.round_id == 3 and o.next_action is not None]
    assert any(a.action == ActionType.DISAMBIGUATION_REPEAT for a in r3_actions)
    # 第 4 轮消费
    consumed = [e for e in store.read_events("action_consumed")
                if e["payload"]["round_id"] == 4]
    assert len(consumed) >= 1
    exp4 = [e for e in store.list_experiments() if e.round_id == 4][0]
    assert len(exp4.provenance.actions_consumed) == len(consumed)
    # 消歧候选带 center_only 且其孔位全为非边缘
    disamb = [c for c in exp4.candidates if c.placement_hint == "center_only"]
    assert len(disamb) >= 1
    for c in disamb:
        wells = [w for w in exp4.layout.wells if w.cand_id == c.cand_id]
        assert wells and all(not w.is_edge for w in wells)
    # 动作预算封顶 ≤30% 孔位
    action_cands = {c.cand_id for c in exp4.candidates
                    if c.parent_obs_id is not None or c.placement_hint is not None}
    action_wells = sum(1 for w in exp4.layout.wells if w.cand_id in action_cands)
    assert action_wells <= 0.30 * len(exp4.layout.wells) + 2  # +2 容 pair 取整


def test_stage_and_risk_map_active(os_run5):
    out, _ = os_run5
    store = RunStore(out, create=False)
    # 阶段 FSM 事件（sobol→gp 至少一次切换）
    stages = store.read_events("stage_changed")
    assert any(e["payload"].get("to") == "gp" for e in stages)
    # planner 状态入检查点
    ckpt = store.read_checkpoint()
    assert "planner" in ckpt and "stage" in ckpt["planner"]


def test_suspects_still_excluded_from_model(os_run5):
    out, summary = os_run5
    store = RunStore(out, create=False)
    n_train = store.read_events("model_updated")[-1]["payload"]["n_train"]
    assert n_train == summary["n_trusted"]
    assert summary["best_trusted"]["value"] <= 1.0  # 假最优依然被拒
