"""M2 设计层验收测试（BUILD_PLAN M2）：
单位立方往返 / 约束拒绝 / Sobol-BO 提议确定性与去重 / 哨兵固定位 / 副本跨区组 /
placement_hint / 风险避让 / 预算超支响亮失败 / 依赖隔离。"""

from pathlib import Path

import numpy as np
import pytest

from expos.design.budget import BudgetError, BudgetManager
from expos.design.layout import LayoutError, LayoutPlanner
from expos.design.sampler import propose_candidates, sobol_candidates
from expos.design.space import (
    DesignError,
    check_constraints,
    dim,
    from_unit,
    to_unit,
    validate_params,
)
from expos.kernel.objects import (
    Budget,
    Candidate,
    Constraint,
    Control,
    DesignSpace,
    LayoutAssignment,
    VariableDef,
)


def make_space() -> DesignSpace:
    return DesignSpace(
        name="crystal",
        variables=[
            VariableDef(name="supersaturation", low=1.05, high=1.6),
            VariableDef(name="additive_frac", low=1e-4, high=1e-2, transform="log"),
            VariableDef(name="cool_rate", low=0.1, high=2.0),
            VariableDef(name="seeded", kind="categorical", choices=[0, 1]),
        ],
    )


# ---------------------------------------------------------------- space

def test_unit_cube_roundtrip_linear_log_categorical():
    space = make_space()
    params = {"supersaturation": 1.3, "additive_frac": 3e-3, "cool_rate": 0.7, "seeded": 1}
    u = to_unit(space, params)
    assert u.shape == (dim(space),) and np.all((0 <= u) & (u <= 1))
    back = from_unit(space, u)
    assert back["supersaturation"] == pytest.approx(1.3)
    assert back["additive_frac"] == pytest.approx(3e-3, rel=1e-9)  # log 变换往返
    assert back["seeded"] == 1  # categorical snap
    # 端点
    assert to_unit(space, {**params, "additive_frac": 1e-4})[1] == pytest.approx(0.0)
    assert to_unit(space, {**params, "additive_frac": 1e-2})[1] == pytest.approx(1.0)


def test_invalid_log_lower_bound_rejected():
    with pytest.raises(ValueError):
        VariableDef(name="bad", low=0.0, high=1.0, transform="log")


def test_space_rejects_invalid_params():
    space = make_space()
    with pytest.raises(DesignError):  # 越界
        to_unit(space, {"supersaturation": 2.0, "additive_frac": 1e-3, "cool_rate": 0.5, "seeded": 0})
    with pytest.raises(DesignError):  # 缺失变量
        to_unit(space, {"supersaturation": 1.2})
    with pytest.raises(DesignError):  # 未知变量
        to_unit(space, {"supersaturation": 1.2, "additive_frac": 1e-3, "cool_rate": 0.5, "seeded": 0, "ghost": 1})
    with pytest.raises(DesignError):  # 非法类别值
        to_unit(space, {"supersaturation": 1.2, "additive_frac": 1e-3, "cool_rate": 0.5, "seeded": 7})


def test_constraints_reject_invalid_candidates():
    restrictions = [
        Constraint(name="cap", kind="range", params={"var": "supersaturation", "max": 1.4}),
        Constraint(name="mix", kind="sum_leq", params={"vars": ["additive_frac"], "max": 5e-3}),
        Constraint(name="no_seed_high", kind="forbidden_combo", params={"conditions": {"seeded": 1}}),
    ]
    ok = {"supersaturation": 1.2, "additive_frac": 1e-3, "cool_rate": 0.5, "seeded": 0}
    assert check_constraints(ok, restrictions)
    assert not check_constraints({**ok, "supersaturation": 1.5}, restrictions)
    assert not check_constraints({**ok, "additive_frac": 8e-3}, restrictions)
    assert not check_constraints({**ok, "seeded": 1}, restrictions)
    with pytest.raises(DesignError):
        validate_params(make_space(), {**ok, "supersaturation": 1.5}, restrictions)


# ---------------------------------------------------------------- sampler

def test_sampler_deterministic_under_seed():
    space = make_space()
    a = sobol_candidates(space, 8, seed=42)
    b = sobol_candidates(space, 8, seed=42)
    c = sobol_candidates(space, 8, seed=43)
    assert [x.params for x in a] == [y.params for y in b]
    assert [x.params for x in a] != [z.params for z in c]
    assert all(x.source == "sobol" for x in a)


def test_sampler_respects_constraints():
    space = make_space()
    restrictions = [Constraint(name="cap", kind="range", params={"var": "supersaturation", "max": 1.2})]
    cands = sobol_candidates(space, 16, seed=7, restrictions=restrictions)
    assert len(cands) == 16
    assert all(c.params["supersaturation"] <= 1.2 for c in cands)


def test_sampler_infeasible_constraints_fail_loudly():
    space = make_space()
    impossible = [Constraint(name="void", kind="range", params={"var": "supersaturation", "max": 0.5})]
    with pytest.raises(DesignError):
        sobol_candidates(space, 4, seed=7, restrictions=impossible)


def test_bo_placeholder_scores_and_min_dist():
    space = make_space()

    def score_fn(pool: np.ndarray) -> np.ndarray:
        return -np.abs(pool[:, 0] - 0.5)  # 偏好第一维靠近 0.5

    cands = propose_candidates(space, 6, seed=3, score_fn=score_fn, min_dist=0.15)
    assert len(cands) == 6 and all(c.source == "bo" for c in cands)
    us = np.stack([to_unit(space, c.params) for c in cands])
    d = np.linalg.norm(us[:, None, :] - us[None, :, :], axis=-1)
    np.fill_diagonal(d, np.inf)
    assert d.min() >= 0.15 - 1e-9  # 最小距离去重生效
    # 分数确实驱动选择：全体第一维都不会离 0.5 太远（对照随机 Sobol 前 6 点）
    assert np.abs(us[:, 0] - 0.5).max() < 0.45


# ---------------------------------------------------------------- layout

def sentinels(n=1):
    return [Control(kind="sentinel", params={"supersaturation": 1.1}) for _ in range(n)]


def test_sentinel_wells_fixed_at_corners_and_center():
    planner = LayoutPlanner(rows=6, cols=8, seed=7)
    ctrl = sentinels(5)
    layout = planner.assign([Candidate(params={"x": 1})], ctrl, n_replicates=2)
    sentinel_wells = {w.well_id for w in layout.wells if w.control_id is not None}
    assert {"A1", "A8", "F1", "F8", "D5"} == sentinel_wells  # 四角 + 中心 (rows//2=3→D, cols//2=4→5)


def test_replicates_cross_blocks_and_seed_recorded():
    planner = LayoutPlanner(rows=6, cols=8, seed=11)
    cands = [Candidate(params={"i": i}) for i in range(6)]
    layout = planner.assign(cands, sentinels(5), n_replicates=2)
    assert isinstance(layout, LayoutAssignment) and layout.seed == 11
    for c in cands:
        blocks = [w.block_id for w in layout.wells if w.cand_id == c.cand_id]
        assert len(blocks) == 2 and len(set(blocks)) == 2  # 跨区组
    # 无重复占孔
    ids = [w.well_id for w in layout.wells]
    assert len(ids) == len(set(ids))


def test_layout_deterministic():
    cands = [Candidate(cand_id=f"c{i}", params={"i": i}) for i in range(5)]
    ctrl = [Control(control_id="s0", kind="sentinel")]
    l1 = LayoutPlanner(6, 8, seed=5).assign(cands, ctrl, n_replicates=2)
    l2 = LayoutPlanner(6, 8, seed=5).assign(cands, ctrl, n_replicates=2)
    assert l1 == l2


def test_placement_hint_center_only_and_edge_center_pair():
    planner = LayoutPlanner(rows=6, cols=8, seed=13)
    center_c = Candidate(params={"i": 0}, placement_hint="center_only")
    pair_c = Candidate(params={"i": 1}, placement_hint="edge_center_pair")
    layout = planner.assign([center_c, pair_c], sentinels(1), n_replicates=3)
    center_wells = [w for w in layout.wells if w.cand_id == center_c.cand_id]
    assert len(center_wells) == 3 and all(not w.is_edge for w in center_wells)
    pair_wells = [w for w in layout.wells if w.cand_id == pair_c.cand_id]
    assert len(pair_wells) == 2  # 恰好一对
    assert sorted(w.is_edge for w in pair_wells) == [False, True]  # 1 边缘 + 1 中心


def test_risk_map_avoidance_with_fake_risk():
    # 伪造风险图：D 行中心区全部高风险 → center_only 候选应避开这些孔
    risky = {f"D{c}" for c in range(2, 8)}
    risk_map = {w: (1.0 if w in risky else 0.0) for w in risky}
    planner = LayoutPlanner(rows=6, cols=8, seed=17)
    cands = [Candidate(params={"i": i}, placement_hint="center_only") for i in range(3)]
    layout = planner.assign(cands, [], n_replicates=2, risk_map=risk_map)
    used = {w.well_id for w in layout.wells if w.cand_id is not None}
    assert used.isdisjoint(risky), f"高风险孔被占用: {used & risky}"


def test_layout_infeasible_raises_no_silent_fallback():
    planner = LayoutPlanner(rows=2, cols=2, seed=1)  # 4 孔全边缘、无中心
    with pytest.raises(LayoutError):  # 容量不足
        planner.assign([Candidate(params={"i": i}) for i in range(10)], [], n_replicates=2)
    with pytest.raises(LayoutError):  # center_only 无中心孔可用
        planner.assign([Candidate(params={"i": 0}, placement_hint="center_only")], [], n_replicates=1)
    with pytest.raises(LayoutError):  # edge_center_pair 凑不齐中心孔
        planner.assign([Candidate(params={"i": 0}, placement_hint="edge_center_pair")], [], n_replicates=2)


# ---------------------------------------------------------------- budget

def test_budget_tracking_and_overrun_fails_loudly():
    bm = BudgetManager(Budget(wells_total=48, rounds_total=2))
    assert bm.remaining_wells == 48 and bm.can_afford(48)
    bm.spend_wells(40, what="round0 layout")
    assert bm.remaining_wells == 8 and not bm.can_afford(9)
    with pytest.raises(BudgetError):
        bm.spend_wells(9, what="超支申请")
    assert bm.remaining_wells == 8  # 失败不记账
    assert bm.start_round() == 1 and bm.start_round() == 2
    with pytest.raises(BudgetError):
        bm.start_round()
    with pytest.raises(BudgetError):
        bm.spend_wells(-1)


def test_budget_charge_layout():
    planner = LayoutPlanner(rows=6, cols=8, seed=7)
    layout = planner.assign([Candidate(params={"i": i}) for i in range(4)], sentinels(5), n_replicates=2)
    bm = BudgetManager(Budget(wells_total=len(layout.wells), rounds_total=1))
    bm.charge_layout(layout)
    assert bm.remaining_wells == 0
    with pytest.raises(BudgetError):
        bm.charge_layout(layout)  # 再记一板必超支


# ---------------------------------------------------------------- 审查补测（对抗审查 + 合规比对盲区）

def test_constraint_referencing_missing_var_fails_loudly():
    ok = {"supersaturation": 1.2, "additive_frac": 1e-3, "cool_rate": 0.5, "seeded": 0}
    with pytest.raises(DesignError):  # range 引用拼错变量名
        check_constraints(ok, [Constraint(name="typo", kind="range", params={"var": "supersat", "max": 1.4})])
    with pytest.raises(DesignError):  # sum_leq 引用缺失变量
        check_constraints(ok, [Constraint(name="mix", kind="sum_leq", params={"vars": ["ghost"], "max": 1.0})])
    with pytest.raises(DesignError):  # forbidden_combo 条件键缺失
        check_constraints(ok, [Constraint(name="fc", kind="forbidden_combo", params={"conditions": {"ghost": 1}})])


def test_from_unit_clip_semantics():
    space = make_space()
    p = from_unit(space, np.array([-0.2, 1.3, 0.5, 2.0]))
    assert p["supersaturation"] == pytest.approx(1.05)  # clip 到下界
    assert p["additive_frac"] == pytest.approx(1e-2)    # clip 到上界（log 维）
    assert p["seeded"] == 1                              # 类别 clip 后 snap


def test_sobol_path_min_dist_dedup():
    space = make_space()
    cands = sobol_candidates(space, 8, seed=5, min_dist=0.2)
    us = np.stack([to_unit(space, c.params) for c in cands])
    d = np.linalg.norm(us[:, None, :] - us[None, :, :], axis=-1)
    np.fill_diagonal(d, np.inf)
    assert d.min() >= 0.2 - 1e-9


def test_bo_deterministic_same_seed_and_score_fn():
    space = make_space()

    def score_fn(pool):
        return pool[:, 2]

    a = propose_candidates(space, 5, seed=9, score_fn=score_fn, min_dist=0.1)
    b = propose_candidates(space, 5, seed=9, score_fn=score_fn, min_dist=0.1)
    assert [x.params for x in a] == [y.params for y in b]


def test_replicates_equal_blocks_and_round_robin_beyond():
    # k == 区组数(4)：强制 4 个不同 block
    layout4 = LayoutPlanner(6, 8, seed=3).assign(
        [Candidate(cand_id="c0", params={})], [], n_replicates=4
    )
    blocks4 = [w.block_id for w in layout4.wells if w.cand_id == "c0"]
    assert len(blocks4) == 4 and len(set(blocks4)) == 4
    # k > 区组数(5)：round-robin 路径，不报错、孔不重复
    layout5 = LayoutPlanner(6, 8, seed=3).assign(
        [Candidate(cand_id="c1", params={})], [], n_replicates=5
    )
    wells5 = [w for w in layout5.wells if w.cand_id == "c1"]
    assert len(wells5) == 5 and len({w.well_id for w in wells5}) == 5


def test_overflow_sentinels_spill_to_normal_wells():
    layout = LayoutPlanner(6, 8, seed=9).assign([], sentinels(7), n_replicates=2)
    ctrl_wells = [w for w in layout.wells if w.control_id is not None]
    assert len(ctrl_wells) == 7
    fixed = {"A1", "A8", "F1", "F8", "D5"}
    placed = {w.well_id for w in ctrl_wells}
    assert fixed <= placed and len(placed - fixed) == 2  # 溢出的 2 个进普通孔


def test_stratified_mix_edge_center_without_hint():
    layout = LayoutPlanner(6, 8, seed=21).assign(
        [Candidate(cand_id=f"c{i}", params={}) for i in range(6)], [], n_replicates=2
    )
    for i in range(6):
        strata = sorted(w.is_edge for w in layout.wells if w.cand_id == f"c{i}")
        assert strata == [False, True]  # 池充足时每候选 1 中心 + 1 边缘


def test_risk_map_unknown_well_id_rejected():
    with pytest.raises(LayoutError):
        LayoutPlanner(6, 8, seed=1).assign(
            [Candidate(params={})], [], n_replicates=2, risk_map={"Z99": 0.5}
        )


def test_per_block_exhaustion_fails_loudly():
    # 中心池 4×6=24 孔；7 个 center_only×4 副本需 28 个中心孔——总容量精检通过（28≤48），
    # 分配期第 7 个候选耗尽中心池 → 必须 LayoutError 而非静默降级
    cands = [Candidate(params={"i": i}, placement_hint="center_only") for i in range(7)]
    with pytest.raises(LayoutError):
        LayoutPlanner(6, 8, seed=2).assign(cands, [], n_replicates=4)


def test_import_graph_isolation():
    import subprocess
    import sys

    code = (
        "import sys; sys.path.insert(0, '.');"
        "import expos.design.space, expos.design.sampler, expos.design.layout, expos.design.budget;"
        "bad=[m for m in sys.modules if m.startswith(('expos.adapters','expos.qc','expos.planner','expos.agent','expos.models'))];"
        "assert not bad, bad"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"import 图污染: {result.stderr}"


# ---------------------------------------------------------------- 依赖隔离（M2 红线）

def test_design_layer_imports_no_forbidden_modules():
    design_dir = Path(__file__).resolve().parent.parent / "expos" / "design"
    forbidden = ("expos.adapters", "expos.qc", "expos.planner", "expos.agent", "expos.models")
    for src in design_dir.glob("*.py"):
        text = src.read_text(encoding="utf-8")
        hits = [f for f in forbidden if f in text]
        assert hits == [], f"{src.name} 引用了禁区模块: {hits}"
