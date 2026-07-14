"""M7 阶段 FSM 验收测试（REFERENCE_MAP §13.4 Ax GenerationNode/TransitionCriterion）。

覆盖：默认规则表 validate 通过；sobol→gp 在 n_trusted≥8 触发；gp→failure_aware
两条边优先级（都满足取第一条）；failure_aware→gp 恢复；无边满足状态不变返回 None；
未知 stage / 未知 transition 目标 StageError；决策确定性；空 generator 校验失败；
describe(rules) JSON 化；criterion 具名可读；依赖隔离红线。
"""

import json

import pytest

from expos.errors import ExposError
from expos.planner.stages import (
    DEFAULT_RULES,
    StageContext,
    StageError,
    StageRule,
    StageState,
    decide_stage,
    describe,
    high_suspect_streak,
    min_trusted,
    streak_cleared,
    trusted_ratio_below,
    validate_rules,
)


def _ctx(
    round_id=3,
    n_trusted=0,
    n_suspect=0,
    n_failed=0,
    trusted_ratio=1.0,
    consecutive_high_suspect_rounds=0,
):
    return StageContext(
        round_id=round_id,
        n_trusted=n_trusted,
        n_suspect=n_suspect,
        n_failed=n_failed,
        trusted_ratio=trusted_ratio,
        consecutive_high_suspect_rounds=consecutive_high_suspect_rounds,
    )


# ---------------------------------------------------------------- 校验

def test_default_rules_validate_ok():
    validate_rules(DEFAULT_RULES)  # 不抛即通过


def test_stage_error_is_expos_error():
    assert issubclass(StageError, ExposError)


def test_validate_unknown_target():
    rules = {"a": StageRule("a", "gen_a", ((min_trusted(1), "ghost"),))}
    with pytest.raises(StageError):
        validate_rules(rules)


def test_validate_empty_generator():
    rules = {"a": StageRule("a", "", ())}
    with pytest.raises(StageError):
        validate_rules(rules)


def test_validate_negative_min_dwell():
    rules = {"a": StageRule("a", "gen_a", (), min_dwell=-1)}
    with pytest.raises(StageError):
        validate_rules(rules)


# ---------------------------------------------------------------- 转移

def test_sobol_to_gp_triggers_at_8():
    state = StageState("sobol", entered_at_round=0)
    # 7 个信任观测：不触发
    new_state, event = decide_stage(DEFAULT_RULES, state, _ctx(n_trusted=7))
    assert new_state is state and event is None
    # 8 个信任观测：触发
    new_state, event = decide_stage(DEFAULT_RULES, state, _ctx(round_id=5, n_trusted=8))
    assert new_state.stage == "gp"
    assert new_state.entered_at_round == 5
    assert event == {
        "from": "sobol",
        "to": "gp",
        "criterion": "min_trusted(8)",
        "round_id": 5,
    }


def test_gp_to_failure_aware_edge_priority():
    state = StageState("gp", entered_at_round=5)
    # 两条边都满足 → 取第一条（high_suspect_streak）
    ctx = _ctx(round_id=9, consecutive_high_suspect_rounds=2, trusted_ratio=0.3)
    new_state, event = decide_stage(DEFAULT_RULES, state, ctx)
    assert new_state.stage == "failure_aware"
    assert event["criterion"] == "high_suspect_streak(2)"
    assert event["round_id"] == 9


def test_gp_to_failure_aware_second_edge_only():
    state = StageState("gp", entered_at_round=5)
    # 只有第二条边满足（信任占比慢信号）；round_id=8 → 驻留 3≥min_dwell(2)，边可放行
    ctx = _ctx(round_id=8, consecutive_high_suspect_rounds=0, trusted_ratio=0.4)
    new_state, event = decide_stage(DEFAULT_RULES, state, ctx)
    assert new_state.stage == "failure_aware"
    assert event["criterion"] == "trusted_ratio_below(0.5)"


def test_min_dwell_blocks_transition_within_dwell():
    """min_dwell（J-5）：gp 刚进（round 6，驻留 1<2）即便 trusted_ratio<0.5 也锁定不切。"""
    state = StageState("gp", entered_at_round=5)
    ctx = _ctx(round_id=6, consecutive_high_suspect_rounds=0, trusted_ratio=0.3)
    new_state, event = decide_stage(DEFAULT_RULES, state, ctx)
    assert new_state is state and event is None


def test_min_dwell_breaks_gp_failure_aware_oscillation():
    """J-5 复现修复：trusted_ratio<0.5 持续、streak=0 时，旧码 gp↔failure_aware 逐轮翻
    （8 轮 8 翻）。加 min_dwell=2 后不再逐轮翻——存在连续同阶段轮、翻转数远小于轮数。"""
    state = StageState("gp", entered_at_round=0)
    stages_seen = []
    changes = 0
    for r in range(1, 11):  # 10 轮持续恶化 + 零连击（正是振荡触发条件）
        ctx = _ctx(round_id=r, n_trusted=5, trusted_ratio=0.3,
                   consecutive_high_suspect_rounds=0)
        new_state, event = decide_stage(DEFAULT_RULES, state, ctx)
        if event is not None:
            changes += 1
        state = new_state
        stages_seen.append(state.stage)
    assert changes < len(stages_seen), "仍在逐轮翻转（min_dwell 未生效）"
    assert any(stages_seen[i] == stages_seen[i + 1]
               for i in range(len(stages_seen) - 1)), "无任何连续同阶段轮=仍逐轮翻"
    assert changes <= 5  # 驻留 2 → 至多每 2 轮一翻


def test_failure_aware_recovers_to_gp():
    state = StageState("failure_aware", entered_at_round=9)
    # 高嫌疑连击跌回 2 以下 → 恢复
    new_state, event = decide_stage(
        DEFAULT_RULES, state, _ctx(round_id=12, consecutive_high_suspect_rounds=1)
    )
    assert new_state.stage == "gp"
    assert event["criterion"] == "streak_cleared(2)"
    # 仍处高嫌疑 → 留在 failure_aware
    new_state2, event2 = decide_stage(
        DEFAULT_RULES, state, _ctx(consecutive_high_suspect_rounds=3)
    )
    assert new_state2 is state and event2 is None


def test_no_edge_satisfied_stays():
    state = StageState("gp", entered_at_round=5)
    ctx = _ctx(consecutive_high_suspect_rounds=1, trusted_ratio=0.9)
    new_state, event = decide_stage(DEFAULT_RULES, state, ctx)
    assert new_state is state
    assert event is None


# ---------------------------------------------------------------- 错误路径

def test_unknown_stage_raises():
    state = StageState("nowhere", entered_at_round=0)
    with pytest.raises(StageError):
        decide_stage(DEFAULT_RULES, state, _ctx())


def test_unknown_transition_target_raises_on_fire():
    rules = {"a": StageRule("a", "gen_a", ((min_trusted(1), "ghost"),))}
    state = StageState("a", entered_at_round=0)
    with pytest.raises(StageError):
        decide_stage(rules, state, _ctx(n_trusted=5))


# ---------------------------------------------------------------- 确定性 / 描述

def test_deterministic():
    state = StageState("gp", entered_at_round=5)
    ctx = _ctx(round_id=9, consecutive_high_suspect_rounds=2, trusted_ratio=0.3)
    r1 = decide_stage(DEFAULT_RULES, state, ctx)
    r2 = decide_stage(DEFAULT_RULES, state, ctx)
    assert r1 == r2


def test_describe_is_json_serializable():
    desc = describe(DEFAULT_RULES)
    dumped = json.dumps(desc)  # 不抛即 JSON 化成功
    assert json.loads(dumped) == desc
    assert desc["sobol"]["generator"] == "sobol"
    assert desc["sobol"]["transitions"] == [{"criterion": "min_trusted(8)", "to": "gp"}]
    assert desc["failure_aware"]["generator"] == "response_gp+ucb+risk_discount"
    gp_targets = [t["to"] for t in desc["gp"]["transitions"]]
    assert gp_targets == ["failure_aware", "failure_aware"]
    # min_dwell 配置化后进入描述（J-5）
    assert desc["sobol"]["min_dwell"] == 0
    assert desc["gp"]["min_dwell"] == 2
    assert desc["failure_aware"]["min_dwell"] == 2


def test_criterion_named_and_callable():
    crit = min_trusted(8)
    assert crit.name == "min_trusted(8)"
    assert crit.__name__ == "min_trusted(8)"
    assert crit(_ctx(n_trusted=8)) is True
    assert crit(_ctx(n_trusted=7)) is False


def test_streak_criteria_complementary():
    # streak_cleared(k) 与 high_suspect_streak(k) 在阈值 k 互补
    for c in range(0, 5):
        ctx = _ctx(consecutive_high_suspect_rounds=c)
        assert high_suspect_streak(2)(ctx) != streak_cleared(2)(ctx)


def test_no_forbidden_imports():
    # 依赖隔离红线：纯导入 stages 不得拉入 loop/store/agent/adapters（在干净子进程里验）
    import subprocess
    import sys

    code = (
        "import sys; import expos.planner.stages; "
        "hits=[m for m in sys.modules "
        "if any(x in m for x in ('expos.loop','expos.store','expos.agent','expos.adapters'))]; "
        "assert not hits, hits; print('ok')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout
