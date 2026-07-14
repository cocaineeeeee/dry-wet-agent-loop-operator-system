"""M6 归因引擎验收（docs/M6_DESIGN.md v2；真实模拟器造板 + checks.run_qc 产 PlateContext）。

覆盖：六注入场景 top_cause 正确（关键场景 edge0.5/batch-0.18/glare prob=1 全对）；
edge vs gradient 竞争判别；干净板 inconclusive；反驳器拦截偶然效应；propose_action 的
semantics/supersedes/placement_hint；确定性；依赖/真值红线。
"""

from pathlib import Path

import numpy as np
import pytest

from expos.adapters.ingest import raw_to_observations
from expos.adapters.sim_crystal import CrystalSim
from expos.design.layout import LayoutPlanner
from expos.design.sampler import sobol_candidates
from expos.domain import load_domain
from expos.kernel.objects import (
    ActionType,
    Budget,
    Control,
    DesignProvenance,
    ExecutionReq,
    ExperimentObject,
    FailureAttribution,
    LayoutMeta,
    MeasuredResult,
    ObservationObject,
)
from expos.qc.attribution import SIG_Z, AttributionError, attribute, propose_action
from expos.qc.checks import run_qc

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- 造板工具

def _make_exp(scenario, n_cands=18, round_id=0, seed=7, n_batches=3):
    cfg = load_domain(ROOT / "domains" / "crystal.yaml")
    cands = sobol_candidates(cfg.design_space, n_cands, seed=seed, restrictions=cfg.restrictions)
    controls = [Control(kind="sentinel", params=cfg.sentinel.params,
                        expected_band=cfg.sentinel.expected_band)
                for _ in range(cfg.sentinel.n)]
    layout = LayoutPlanner(cfg.plate.rows, cfg.plate.cols, seed=seed).assign(
        cands, controls, n_replicates=cfg.replicates
    )
    return ExperimentObject(
        round_id=round_id, domain=cfg.name, objective=cfg.objective,
        design_space=cfg.design_space,
        active_vars=[v.name for v in cfg.design_space.variables],
        restrictions=cfg.restrictions, candidates=cands, controls=controls,
        layout=layout, budget=Budget(**cfg.budget.model_dump()),
        execution_req=ExecutionReq(adapter=cfg.adapter, n_solution_batches=n_batches),
        provenance=DesignProvenance(generator="sobol"),
    )


def _build(scenario, *, n_cands=18, seed=7, exec_seed=0, round_id=0, n_batches=3):
    """真实模拟器造板 → ingest → run_qc。返回 (exp, obs_list, reports, plate)。"""
    exp = _make_exp(scenario, n_cands=n_cands, round_id=round_id, seed=seed, n_batches=n_batches)
    sim = CrystalSim({"noise_sd": 0.02, "artifact_scenario": scenario})
    result = sim.execute(exp, np.random.default_rng(exec_seed))
    obs = raw_to_observations(exp, result.raw_results)
    reports, plate = run_qc(exp, obs, seed=0)
    return exp, obs, reports, plate


def _attr(o, reports, plate, exp):
    return attribute(o, reports[o.obs_id], plate, exp, seed=0)


def _d_edge(o, exp):
    r, c = o.layout_meta.row, o.layout_meta.col
    return min(r, c, exp.layout.rows - 1 - r, exp.layout.cols - 1 - c)


def _cands(obs):
    return [o for o in obs if not o.is_control]


# ================================================================ 六注入场景

def test_edge_evaporation_all_implicated_correct():
    """关键场景 edge strength=0.5：被牵连边缘观测 top_cause 全对。"""
    scen = [{"injector": "edge_evaporation", "params": {"strength": 0.5, "decay_wells": 1.0}}]
    exp, obs, reports, plate = _build(scen)
    implicated = [o for o in _cands(obs)
                  if _d_edge(o, exp) <= 1
                  and reports[o.obs_id].checks[0] and o.result.value is not None]
    # 边缘且残差被抬高（正）的候选孔 = 被牵连
    flagged = [o for o in implicated
               if _attr(o, reports, plate, exp).hypotheses]
    tops = [_attr(o, reports, plate, exp).top_cause for o in flagged]
    edge_wells = [o for o in _cands(obs) if _d_edge(o, exp) <= 1]
    correct = [o for o in edge_wells
               if _attr(o, reports, plate, exp).top_cause == "edge_evaporation"]
    assert len(correct) >= 1
    # 全对：所有产生非空 top 的边缘牵连孔都指向 edge
    non_none = [o for o in edge_wells
                if _attr(o, reports, plate, exp).top_cause is not None]
    assert non_none, "edge 场景应至少牵连一个边缘孔"
    assert all(_attr(o, reports, plate, exp).top_cause == "edge_evaporation" for o in non_none)


def test_batch_effect_key_scenario_correct():
    """关键场景 batch shift=-0.18：B1 批被牵连孔 top_cause=batch_effect。"""
    scen = [{"injector": "batch_shift", "params": {"batch_suffix": "B1", "shift": -0.18}}]
    exp, obs, reports, plate = _build(scen)
    b1 = [o for o in _cands(obs) if o.material_meta.solution_batch.endswith("B1")]
    tops = {o.obs_id: _attr(o, reports, plate, exp).top_cause for o in b1}
    non_none = [c for c in tops.values() if c is not None]
    assert non_none, "batch 场景应牵连 B1 孔"
    assert all(c == "batch_effect" for c in non_none)


def test_batch_guard_intercepts_inverted_clean_batch():
    """归因侧交叉守卫（ATT3 P1，第二道防线）：2 批棋盘格 batch shift=-0.18 触发后，干净批 B0
    孔即便 t_batch 显著（|t|≥SIG_Z）也**不得**获 batch_effect——干净批 t_batch 系统为正、与
    定向 shift_hat(<0) 反号，sign 一致性门拦截。这正是"选批判反"在归因侧的兜底。"""
    scen = [{"injector": "batch_shift", "params": {"batch_suffix": "B1", "shift": -0.18}}]
    exp, obs, reports, plate = _build(scen, n_cands=20, n_batches=2)
    bev = next(c.evidence for o in obs for c in reports[o.obs_id].checks
               if c.name == "batch_shift" and c.evidence.get("fired"))
    assert bev["shift_hat"] < 0, "前提：shift_hat 定向为负（异常读低）"
    clean = [o for o in _cands(obs) if not o.material_meta.solution_batch.endswith("B1")]
    intercepted = []
    for o in clean:
        a = _attr(o, reports, plate, exp)
        be = next(h for h in a.hypotheses if h.cause == "batch_effect")
        assert a.top_cause != "batch_effect", \
            f"干净批 {o.material_meta.solution_batch} 被误归 batch_effect"
        if abs(be.evidence["batch_t"]) >= SIG_Z:      # 显著但被守卫拦截
            assert be.evidence["shift_hat_sign_ok"] is False
            assert be.score == 0.0
            intercepted.append(o)
    assert intercepted, "应至少有一个 t_batch 显著的干净批孔被守卫拦截（否则未覆盖守卫路径）"


def test_batch_guard_does_not_hurt_injected_batch():
    """守卫不误伤：注入批 B1 孔（t_batch 与 shift_hat 同号）仍正常获 batch_effect。"""
    scen = [{"injector": "batch_shift", "params": {"batch_suffix": "B1", "shift": -0.18}}]
    exp, obs, reports, plate = _build(scen, n_cands=20, n_batches=2)
    b1 = [o for o in _cands(obs) if o.material_meta.solution_batch.endswith("B1")]
    scored = [o for o in b1 if _attr(o, reports, plate, exp).top_cause == "batch_effect"]
    assert scored, "注入批 B1 应有孔获 batch_effect（守卫不误伤同号侧）"
    for o in scored:
        be = next(h for h in _attr(o, reports, plate, exp).hypotheses
                  if h.cause == "batch_effect")
        assert be.evidence["shift_hat_sign_ok"] is True


def test_glare_prob1_all_correct():
    """关键场景 glare prob=1：全部候选孔 top_cause=glare。"""
    scen = [{"injector": "glare", "params": {"prob": 1.0, "boost": 0.35}}]
    exp, obs, reports, plate = _build(scen)
    tops = [_attr(o, reports, plate, exp).top_cause for o in _cands(obs)]
    assert tops and all(t == "glare" for t in tops)


def test_thermal_gradient_correct():
    scen = [{"injector": "thermal_gradient", "params": {"axis": "row", "magnitude": 0.4}}]
    exp, obs, reports, plate = _build(scen)
    rows = exp.layout.rows
    extreme = [o for o in _cands(obs)
               if o.layout_meta.row in (0, rows - 1)]
    tops = [_attr(o, reports, plate, exp).top_cause for o in extreme]
    non_none = [t for t in tops if t is not None]
    assert non_none, "gradient 场景应牵连极端行孔"
    assert all(t == "thermal_gradient" for t in non_none)


def test_dust_contamination_correct():
    scen = [{"injector": "dust_nucleation", "params": {"prob": 0.5, "drop": 0.4}}]
    exp, obs, reports, plate = _build(scen, exec_seed=3)
    dusted = [o for o in _cands(obs)
              if _check_score(reports[o.obs_id], "dust_channel") > 0]
    assert dusted, "dust 场景应至少牵连一个孔（副本分裂）"
    tops = [_attr(o, reports, plate, exp).top_cause for o in dusted]
    non_none = [t for t in tops if t is not None]
    # 被牵连（读数下降）孔全部指向 dust；读数未降者 inconclusive，绝不误指他因
    assert non_none and all(t == "dust_contamination" for t in non_none)
    assert len(non_none) >= 0.7 * len(dusted)


def test_instrument_drift_single_round_inconclusive():
    """instrument_drift 单轮不可辨识（§2.2/§6：候选去身份残差抵消漂移、哨兵传感器信噪不足，
    record-only；须跨轮累积）→ 不强行归因，落 inconclusive（top_cause=None），不误指他因。"""
    scen = [{"injector": "instrument_drift", "params": {"mode": "linear", "rate": -0.008}}]
    exp, obs, reports, plate = _build(scen)
    late = sorted(_cands(obs), key=lambda o: o.instrument_meta.capture_index)[-6:]
    tops = [_attr(o, reports, plate, exp).top_cause for o in late]
    # 单轮不误指具体他因（不冒充 edge/batch/glare/gradient）
    assert all(t is None for t in tops), f"单轮 drift 应 inconclusive，得到 {tops}"


def _check_score(report, name):
    for c in report.checks:
        if c.name == name:
            return c.score
    return 0.0


# ================================================================ 竞争判别

def test_edge_vs_gradient_discrimination():
    """同尺度对照：edge 板判 edge、gradient 板判 gradient（ΔR² + 对边符号，§2.6）。"""
    edge_scen = [{"injector": "edge_evaporation", "params": {"strength": 0.5, "decay_wells": 1.0}}]
    grad_scen = [{"injector": "thermal_gradient", "params": {"axis": "row", "magnitude": 0.4}}]

    e_exp, e_obs, e_rep, e_plate = _build(edge_scen)
    g_exp, g_obs, g_rep, g_plate = _build(grad_scen)

    e_edge = [o for o in _cands(e_obs) if _d_edge(o, e_exp) <= 1]
    e_tops = [_attr(o, e_rep, e_plate, e_exp).top_cause for o in e_edge]
    assert any(t == "edge_evaporation" for t in e_tops)
    assert not any(t == "thermal_gradient" for t in e_tops)

    rows = g_exp.layout.rows
    g_ext = [o for o in _cands(g_obs) if o.layout_meta.row in (0, rows - 1)]
    g_tops = [_attr(o, g_rep, g_plate, g_exp).top_cause for o in g_ext]
    assert any(t == "thermal_gradient" for t in g_tops)
    assert not any(t == "edge_evaporation" for t in g_tops)


# ================================================================ 干净板

def test_clean_board_inconclusive():
    """干净板（无注入）：不产生高置信归因（top_cause=None）。"""
    exp, obs, reports, plate = _build([], exec_seed=1)
    for o in _cands(obs):
        attr = _attr(o, reports, plate, exp)
        assert attr.top_cause is None, f"干净板不应归因，得到 {attr.top_cause}"


# ================================================================ 反驳器拦截

def test_clean_board_fpr_and_single_hypothesis_bounded():
    """校准佐证（R1 P3 tasks 1&2）：多种子干净板家族误归因率有界 + 每孔至多 1 个正分假设。

    - maxc≤1 是 ΔR² 无 Bonferroni 的**影响界依据**：硬门（board_sig∧footprint）使假设互斥
      选择，非并行多检验，选择族有效大小=1。
    - FPR 有界是 subsample 0.5 系数取舍的**实测佐证**：composite 门在干净板不滥归因
      （权威口径 20 种子默认板 = 2.4%；此处用缩板 4 种子做轻量回归守门）。"""
    fp = tot = maxc = 0
    for s in range(4):
        exp, obs, reports, plate = _build([], n_cands=12, exec_seed=s)
        for o in _cands(obs):
            if o.result.value is None:
                continue
            a = _attr(o, reports, plate, exp)
            tot += 1
            maxc = max(maxc, sum(1 for h in a.hypotheses if h.score > 0))
            if a.top_cause is not None:
                fp += 1
    assert maxc <= 1, f"每孔应至多 1 个正分假设（Bonferroni 影响界），得 {maxc}"
    assert fp / tot <= 0.12, f"干净板家族误归因率过高：{fp}/{tot}={fp / tot:.3f}"


def test_refuter_intercepts_coincidental_signal():
    """构造假信号（小板 + 单孔偶然强残差）：edge 伪后验越过 FLOOR，但 DoWhy subsample 反驳器
    判其经不起抽样（单孔驱动、不稳）→ 降级、top_cause=None。这是 FLOOR 之外、反驳器专有的拦截。"""
    exp, obs, reports, plate = _build([], n_cands=4, exec_seed=2)  # 小板 → 效应高杠杆、易被抽样击穿
    edge_c = [o for o in _cands(obs) if _d_edge(o, exp) <= 1]
    target = edge_c[0]
    for o in obs:
        if o.obs_id == target.obs_id:
            o.result.value = float(o.result.value) + 0.4  # 单孔偶然抬升（无真实板级机制）
    reports2, plate2 = run_qc(exp, obs, seed=0)
    attr = attribute(target, reports2[target.obs_id], plate2, exp, seed=0)
    lead = attr.hypotheses[0]
    assert lead.cause == "edge_evaporation"
    assert attr.confidence >= 0.45, "伪后验应越过 FLOOR（证明是反驳器而非 FLOOR 拦截）"
    ref = lead.evidence.get("refuter")
    assert isinstance(ref, dict) and ref.get("passed") is False, "反驳器应判领先假设不稳"
    assert attr.top_cause is None


# ================================================================ propose_action

def _dummy_obs():
    return ObservationObject(
        exp_id="exp_x", round_id=0, cand_id="cand_x",
        result=MeasuredResult(metric="q", value=0.5),
        layout_meta=LayoutMeta(well_id="A1", row=0, col=0),
    )


@pytest.mark.parametrize("cause,action,semantics,detour,hint", [
    ("edge_evaporation", ActionType.DISAMBIGUATION_REPEAT, "detour", True, "center_only"),
    ("glare", ActionType.REMEASURE, "detour", True, None),
    ("instrument_drift", ActionType.REMEASURE, "detour", True, None),
    ("thermal_gradient", ActionType.ADD_CONTROLS, "addition", False, None),
    ("dust_contamination", ActionType.REPEAT_CANDIDATE, "addition", False, None),
    ("batch_effect", ActionType.REPEAT_CANDIDATE, "addition", False, None),
])
def test_propose_action_semantics(cause, action, semantics, detour, hint):
    o = _dummy_obs()
    attr = FailureAttribution(hypotheses=[], top_cause=cause, confidence=0.8)
    ra = propose_action(o, attr)
    assert ra.action == action
    assert ra.params["semantics"] == semantics
    assert ra.params["target_obs"] == o.obs_id
    assert ra.params["target_cand"] == o.cand_id
    assert ra.params["created_by_action_id"] is None
    if detour:
        assert ra.params["supersedes"] == [o.obs_id]
    else:
        assert ra.params["supersedes"] == []
    if hint is not None:
        assert ra.params["placement_hint"] == hint


def test_propose_action_inconclusive():
    o = _dummy_obs()
    attr = FailureAttribution(hypotheses=[], top_cause=None, confidence=0.3)
    ra = propose_action(o, attr)
    assert ra.action == ActionType.DISAMBIGUATION_REPEAT
    assert ra.params["semantics"] == "detour"
    assert ra.params["placement_hint"] == "center_only"
    assert ra.params["supersedes"] == [o.obs_id]


def test_propose_action_trusted_none():
    assert propose_action(_dummy_obs(), None) is None


# ================================================================ 确定性 & 隔离

def test_determinism():
    scen = [{"injector": "edge_evaporation", "params": {"strength": 0.5}}]
    exp, obs, reports, plate = _build(scen)
    o = [x for x in _cands(obs) if _d_edge(x, exp) <= 1][0]
    a1 = attribute(o, reports[o.obs_id], plate, exp, seed=0)
    a2 = attribute(o, reports[o.obs_id], plate, exp, seed=0)
    assert a1.model_dump() == a2.model_dump()


def test_attribute_requires_layout():
    o = _dummy_obs()
    exp = _make_exp([])
    exp.layout = None
    _, obs, reports, plate = _build([])
    with pytest.raises(AttributionError):
        attribute(o, reports[list(reports)[0]], plate, exp, seed=0)


def test_signature_weights_no_phantom_aux():
    """签名权重如实三通道（R1 P3）：声明与实现一致，无独立辅助信号。

    历史声称四档（QC 0.4 / 主 0.3 / 锚点 0.2 / 辅助 0.1），但 _emit 里辅助恒等于锚点
    （aux≡anchor）——并非独立第四通道，四档声明与实现不符。修正为三通道
    QC 0.4 / 主 0.3 / 锚点 0.3（= 旧 0.2+0.1，score 逐位不变），杜绝虚假声明。"""
    import expos.qc.attribution as mod

    assert not hasattr(mod, "W_AUX"), "四档虚假声明的 W_AUX 应已删除"
    assert (mod.W_QC, mod.W_MAIN, mod.W_ANCHOR) == (0.4, 0.3, 0.3)
    assert mod.W_QC + mod.W_MAIN + mod.W_ANCHOR == pytest.approx(1.0)
    assert "W_AUX" not in Path(mod.__file__).read_text(encoding="utf-8")


def test_dependency_and_truth_isolation():
    """Dependency + truth-isolation red line at IDENTIFIER/IMPORT level, not raw
    substring: a lint COMMENT mentioning ``adapters/sim_base.py`` (the batch-
    formula cross-reference) is documentation, not a layering violation (letter
    146: the blanket substring false-flagged that comment). Mirrors the EXP001 /
    test_adapters AST discipline."""
    import ast as _ast
    import expos.qc.attribution as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = _ast.parse(src, filename=mod.__file__)
    # (1) module-dependency red line: no import of adapters / agent / planner.
    forbidden_mods = ("expos.adapters", "adapters", "expos.agent", "expos.planner", "planner")
    imported: list[str] = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, _ast.Import):
            imported.extend(a.name for a in node.names)
    hits = [f for f in forbidden_mods for m in imported if m == f or m.startswith(f + ".")]
    assert hits == [], f"attribution.py import 了禁区模块: {sorted(set(hits))}"
    # (2) truth-isolation: no `truth` IDENTIFIER (docstrings/comments exempt — the
    # AST carries no comment text). Same identifier-level semantics as EXP001.
    truth_idents = [
        n for n in _ast.walk(tree)
        if (isinstance(n, _ast.Name) and "truth" in n.id.lower())
        or (isinstance(n, _ast.Attribute) and "truth" in n.attr.lower())
        or (isinstance(n, _ast.arg) and "truth" in n.arg.lower())
    ]
    assert truth_idents == [], "attribution.py 不得含 truth 标识符（真值隔离红线）"


def test_board_frame_batch_matches_simulator_labels():
    """缝隙守卫（对抗审查实锤）：归因证据帧的批次分组必须与 sim_base 发出的
    solution_batch 逐孔一致（棋盘格 (row+col)%n）。若错用 capture 序 idx%n，
    观测常被排除出自身批次组 → 真批次效应被稀释、t_batch 失真。"""
    from expos.qc.attribution import _board_frame

    exp, obs, reports, plate = _build([], n_batches=3)
    frame = _board_frame(plate, exp)
    by_rc = {(o.layout_meta.row, o.layout_meta.col): o.material_meta.solution_batch
             for o in obs}
    assert len(frame["batch"]) > 0
    for r, c, b in zip(frame["row"], frame["col"], frame["batch"]):
        assert b == by_rc[(int(r), int(c))], (
            f"({int(r)},{int(c)}) 归因帧批次 {b} ≠ 模拟器标签 {by_rc[(int(r), int(c))]}"
        )
