"""M5 三级 QC 检查器验收测试（docs/M5_DESIGN.md §2 / §5；REFERENCE_MAP §11.4 / §14）。

用真实模拟器（CrystalSim + 6×8 板）造伪影场景，逐一验证：
  * 边缘 / 批次 / glare 伪影板命中对应检查、逐孔归属正确（无全板连坐）；
  * 零伪影板 QC 税（suspicion≥0.3 的非控制观测比例）≤5%（20 种子平均）；
  * 干净板 PlateContext 数值 sane；确定性；lazy（hard 失败不阻断其余检查）；
  * 依赖隔离（不 import planner/agent/models、源码不含 truth 字样、import 图干净）。

只经 raw→observation 的 OS 可见面构板——绝不读仿真真值 sidecar（公理 2）。
"""

from pathlib import Path

import numpy as np
import pytest

from expos.adapters.ingest import raw_to_observations
from expos.adapters.sim_crystal import CrystalSim
from expos.qc.checks import (
    EDGE_FIRE,
    EDGE_FULL,
    CheckError,
    PlateContext,
    QCHistory,
    _EDGE_REFERENCE_SPAN,
    _batch_fallback_pick,
    _batch_select,
    _batch_sentinel_pick,
    run_qc,
)
from tests.test_adapters import make_experiment

ROOT = Path(__file__).resolve().parent.parent
FAST = 199  # 测试用较小置换数（Moran 仅作筛查，score 恒 0，不影响裁决）


def make_board(scenario, seed=0, noise=0.02, n_cands=6):
    """真实模拟器造板：ExperimentObject + 一批 PENDING 观测（OS 可见面）。

    ``n_cands`` 控制板密度：默认 6（17 孔玩具板，QC 税/PlateContext 用）；批次检查用近满板
    ``n_cands=20``（45 孔，与 M9 全扫描的 ~47 孔闭环板同量级——批次是乘性身份相关效应，只有在
    足够多跨批 identity 上才可靠估计，17 孔玩具板低于批次探测信息地板，见 checks.py 结构性教训）。
    """
    exp = make_experiment(seed=7, n_cands=n_cands)
    sim = CrystalSim({"noise_sd": noise, "artifact_scenario": scenario})
    result = sim.execute(exp, np.random.default_rng(seed))
    obs = raw_to_observations(exp, result.raw_results)
    return exp, obs


def _susp(reports, obs):
    return {o.layout_meta.well_id: reports[o.obs_id].suspicion for o in obs}


# ---------------------------------------------------------------- 伪影命中

def test_edge_artifact_flags_edge_not_center():
    """边缘蒸发 strength=0.5：边缘孔 suspicion≥0.6、中心哨兵 <0.3（逐孔归属，无连坐）。"""
    exp, obs = make_board([
        {"injector": "edge_evaporation", "params": {"strength": 0.5, "decay_wells": 1.0}}
    ])
    reports, plate = run_qc(exp, obs, moran_perm=FAST)
    susp = _susp(reports, obs)

    edge_wells = [w for w, d in plate.d_edge.items() if d == 0]
    center_ctrls = [o.layout_meta.well_id for o in obs
                    if o.is_control and plate.d_edge[o.layout_meta.well_id] >= 2]
    assert edge_wells and center_ctrls
    # 所有最外圈孔被判可疑
    assert all(susp[w] >= 0.6 for w in edge_wells), {w: susp[w] for w in edge_wells}
    # 中心哨兵不被连坐
    assert all(susp[w] < 0.3 for w in center_ctrls), {w: susp[w] for w in center_ctrls}
    # edge_effect 出现在边缘孔 flags、方向正确
    for w in edge_wells:
        assert "edge_effect" in reports[[o for o in obs
                if o.layout_meta.well_id == w][0].obs_id].flags
    assert plate.edge_paired_diff > 0.02  # 边缘抬升 → 正差


# ------------------------------------------------ scale-aware edge floor (M24-B, letter 147)
#
# The edge_effect structural check had an ABSOLUTE floor (0.018/0.045) implicitly calibrated to
# the chemistry measurement scale (metric_range span ~1.2 a.u., noise_sd~0.02). When a domain
# normalizes readouts to a different scale (biology percent-of-control, span 200) the same
# spatial noise is amplified ~167x and mis-fires the floor -> replicates collapse to SUSPECT ->
# certification power is killed. The fix expresses the floor as a fraction of the metric span so
# it tracks the scale (domain-neutral) WITHOUT changing chemistry behavior. edge is the sole
# absolute-metric-scale structural floor (batch shift_hat / gradient t / drift & replicate z /
# Moran are already dimensionless), so it is the only check retuned.

def _impose_edge_pattern(exp, obs, base, edge_delta, moran_perm=99):
    """Overwrite every observation value to base (+edge_delta on the outer ring, d_edge==0),
    giving a clean, scale-independent edge-vs-inner spatial pattern. Returns the d_edge map."""
    _, plate0 = run_qc(exp, obs, moran_perm=moran_perm)
    d_edge = plate0.d_edge
    for o in obs:
        o.result.value = base + (edge_delta if d_edge[o.layout_meta.well_id] == 0 else 0.0)
    return d_edge


def _edge_check(reports, obs, wid):
    o = [o for o in obs if o.layout_meta.well_id == wid][0]
    return [c for c in reports[o.obs_id].checks if c.name == "edge_effect"][0]


def test_edge_floor_byte_identical_on_chemistry_scale():
    """Byte-identity anchor: on the [0, 1.2] chemistry scale the effective edge floor equals the
    pre-fix ABSOLUTE value EXACTLY (fraction derived from the reference span -> factor 1.0)."""
    exp, obs = make_board([], seed=1)
    # default metric_range == chemistry (0, 1.2)
    reports, _ = run_qc(exp, obs, moran_perm=FAST)
    ec = reports[obs[0].obs_id].checks
    ev = [c for c in ec if c.name == "edge_effect"][0].evidence
    assert ev["fire"] == 0.018 == EDGE_FIRE          # exact, not approx
    assert ev["full"] == 0.045 == EDGE_FULL
    assert ev["metric_span"] == 1.2
    # explicit derivation: effective floor == fraction * span == historical absolute value
    assert EDGE_FIRE / _EDGE_REFERENCE_SPAN * 1.2 == 0.018


def test_edge_floor_scale_aware_bio_drift_ramp_does_not_fire():
    """Crux (letter 147): the SAME drift-amplified edge diff (~2.3 on a (0,200) percent scale,
    the ~+-3 spatial spread) that mis-fires under the chemistry-scale floor does NOT fire under
    the scale-aware biology floor. Identical data, opposite verdict by scale."""
    exp, obs = make_board([], seed=1)
    d_edge = _impose_edge_pattern(exp, obs, base=100.0, edge_delta=2.5)
    edge_wells = [w for w, d in d_edge.items() if d == 0]

    # chemistry-scale floor (0.018) -> the drift-amplified diff MIS-FIRES (the pre-fix bug)
    rep_chem, plate_chem = run_qc(exp, obs, moran_perm=99, metric_range=(0.0, 1.2))
    diff = abs(plate_chem.edge_paired_diff)
    assert diff > EDGE_FIRE                            # exceeds the old absolute floor
    assert _edge_check(rep_chem, obs, edge_wells[0]).evidence["fired"] is True

    # biology percent-of-control scale (0, 200): floor scales up ~167x -> NO mis-fire
    rep_bio, plate_bio = run_qc(exp, obs, moran_perm=99, metric_range=(0.0, 200.0))
    assert plate_bio.edge_paired_diff == plate_chem.edge_paired_diff  # scale doesn't move the stat
    bio_ec = _edge_check(rep_bio, obs, edge_wells[0])
    assert bio_ec.evidence["fire"] == pytest.approx(3.0)  # 0.018 * (200/1.2)
    assert diff < bio_ec.evidence["fire"]
    assert bio_ec.evidence["fired"] is False
    assert bio_ec.score == 0.0
    # every edge well stays clean on the edge axis (no edge-driven suspicion)
    edge_scores = {w: _edge_check(rep_bio, obs, w).score for w in edge_wells}
    assert all(s == 0.0 for s in edge_scores.values()), edge_scores


def test_edge_floor_scale_aware_not_blind_bio_real_artifact_fires():
    """Kill-comment: scale-AWARE, not scale-disabled. A genuinely large edge artifact on the
    (0,200) percent scale (proportionally large diff) STILL fires -> the check is not blinded."""
    exp, obs = make_board([], seed=1)
    d_edge = _impose_edge_pattern(exp, obs, base=100.0, edge_delta=20.0)
    edge_wells = [w for w, d in d_edge.items() if d == 0]
    rep, plate = run_qc(exp, obs, moran_perm=99, metric_range=(0.0, 200.0))
    bio_ec = _edge_check(rep, obs, edge_wells[0])
    assert abs(plate.edge_paired_diff) > bio_ec.evidence["full"]  # > 7.5 -> full score
    assert bio_ec.evidence["fired"] is True
    assert bio_ec.score == 1.0
    susp = _susp(rep, obs)
    assert all(susp[w] >= 0.6 for w in edge_wells), {w: susp[w] for w in edge_wells}


def _affine(obs, a, b):
    """Percent-of-control-style affine readout transform value -> a*value + b (the offset b is
    what dampens value-based estimators; residual-based statistics see it cancel)."""
    for o in obs:
        if o.result.value is not None:
            o.result.value = a * o.result.value + b


def test_edge_effect_genuine_artifact_caught_at_both_scales():
    """Acceptance (M24-B family audit): a genuine RELATIVE edge anomaly is still caught at BOTH
    the chemistry raw scale AND the biology percent-of-control scale — scale-AWARE, not
    scale-disabled. Under the affine transform (value -> a*value + b) the edge residual diff
    scales by `a` and the span-relative floor scales to match, so the verdict is preserved."""
    scen = [{"injector": "edge_evaporation", "params": {"strength": 0.5, "decay_wells": 1.0}}]
    exp_c, obs_c = make_board(scen, seed=2, n_cands=20)
    rep_c, _ = run_qc(exp_c, obs_c, moran_perm=FAST, metric_range=(0.0, 1.2))
    exp_b, obs_b = make_board(scen, seed=2, n_cands=20)
    _affine(obs_b, 125.0, -12.5)  # neg=0.1, pos=0.9 -> a=125, b=-12.5 (raw ~0.85 -> ~93)
    rep_b, _ = run_qc(exp_b, obs_b, moran_perm=FAST, metric_range=(0.0, 200.0))

    def edge_fired(rep, obs):
        return any(c.evidence.get("fired")
                   for o in obs for c in rep[o.obs_id].checks if c.name == "edge_effect")
    assert edge_fired(rep_c, obs_c)   # chemistry raw scale
    assert edge_fired(rep_b, obs_b)   # biology percent scale -- still caught (not blinded)


def test_dimensionless_siblings_are_scale_invariant():
    """Family-audit pin (letter 147 checklist): the sibling structural checks whose thresholds
    are DIMENSIONLESS -- row/col gradient t-statistic here -- produce the SAME statistic (up to
    floating-point noise from the affine multiply) and the SAME verdict under the affine
    percent-of-control transform. Their fire decision cannot shift with metric_range, so they
    need no scale-aware retune (edge is the sole unit-scale floor)."""
    scen = [{"injector": "thermal_gradient", "params": {"axis": "row", "magnitude": 0.6}}]
    exp_c, obs_c = make_board(scen, seed=5, n_cands=20)
    rep_c, _ = run_qc(exp_c, obs_c, moran_perm=FAST, metric_range=(0.0, 1.2))
    exp_b, obs_b = make_board(scen, seed=5, n_cands=20)
    _affine(obs_b, 125.0, -12.5)
    rep_b, _ = run_qc(exp_b, obs_b, moran_perm=FAST, metric_range=(0.0, 200.0))

    def grad_stat(rep, obs):
        for o in obs:
            for c in rep[o.obs_id].checks:
                if c.name == "row_col_gradient":
                    return (c.evidence.get("t_row"), c.evidence.get("t_col"),
                            c.evidence.get("fired"))
        return None
    t_row_c, t_col_c, fired_c = grad_stat(rep_c, obs_c)
    t_row_b, t_col_b, fired_b = grad_stat(rep_b, obs_b)
    assert fired_c == fired_b                              # verdict identical across scales
    assert t_row_b == pytest.approx(t_row_c, rel=1e-9)     # t-stat invariant (modulo FP)
    assert t_col_b == pytest.approx(t_col_c, rel=1e-9)


def test_batch_artifact_hits_one_batch():
    """批次位移 shift=-0.18（近满板 45 孔，与 M9 闭环板同量级）：身份无关 shift_hat 命中、
    只牵连单批（非全板连坐）。17 孔玩具板下棋盘格使去身份残差吸收批差、低于探测地板——见
    checks.py 结构性教训注释与本文件 make_board docstring。"""
    exp, obs = make_board([
        {"injector": "batch_shift", "params": {"batch_suffix": "B1", "shift": -0.18}}
    ], n_cands=20)
    reports, plate = run_qc(exp, obs, moran_perm=FAST)

    flagged = [o for o in obs if "batch_shift" in reports[o.obs_id].flags]
    assert flagged, "batch_shift 未命中"
    # 命中孔全部同属一个批次（逐批归属，不连坐另一批）
    batches = {o.material_meta.solution_batch for o in flagged}
    assert len(batches) == 1, f"批次归属连坐到多批: {batches}"
    # 方向正确（R3 §1.1 P0）：命中批必须是**被注入批**（batch_suffix=B1），非干净批
    assert batches == {"R0-B1"}, f"选批方向判反：命中 {batches}，注入批应为 R0-B1"
    # 命中孔 suspicion 抬升；非命中候选保持低
    assert max(reports[o.obs_id].suspicion for o in flagged) >= 0.3
    cand = [o for o in obs if not o.is_control]
    assert not all("batch_shift" in reports[o.obs_id].flags for o in cand)  # 非全板
    assert set(plate.batch_shifts) and all(np.isfinite(v) for v in plate.batch_shifts.values())
    # 身份无关估计器证据落板级：shift_hat 与 |shift| 同量级、方向为负
    bev = [c.evidence for c in reports[flagged[0].obs_id].checks if c.name == "batch_shift"][0]
    assert bev["shift_hat"] < -0.10 and abs(bev["z"]) >= 3.0


def test_batch_record_only_when_insufficient_cross_batch_pairs():
    """跨批 identity 对 <2（单批板）：batch_shift 不触发、record-only（诚实降级，不误判）。"""
    exp, obs = make_board([])
    for o in obs:                       # 全部塞进同一批 → 无跨批对
        o.material_meta.solution_batch = "R0-B0"
    reports, _ = run_qc(exp, obs, moran_perm=99)
    for o in obs:
        chk = [c for c in reports[o.obs_id].checks if c.name == "batch_shift"][0]
        assert chk.score == 0.0 and chk.passed
        assert chk.evidence.get("n_pairs", 0) < 2 and chk.evidence["fired"] is False


def test_batch_direction_selects_injected_not_clean_batch():
    """R3 §1.1 P0 回归（IEEE 平局）：两批棋盘格下 batch_shifts 两批精确相反数、abs 恒平局，
    旧 max(key=abs) 落插入序首个（永远选干净 B0）→ 100% 判反。修后必须选**被注入批 B1**。

    多种子对账：真跑 sim batch_shift（注入批 = batch_suffix=B1），断言凡触发即选中注入批、
    绝不选干净批（B0）。"""
    for seed in range(6):
        exp, obs = make_board([
            {"injector": "batch_shift", "params": {"batch_suffix": "B1", "shift": -0.18}}
        ], seed=seed, n_cands=20)
        reports, _ = run_qc(exp, obs, moran_perm=99, seed=seed)
        bev = next(c.evidence for o in obs
                   for c in reports[o.obs_id].checks if c.name == "batch_shift")
        if not bev.get("fired"):
            continue
        shifts = bev["shifts"]
        # 记录 IEEE 平局前提：两批 batch_shifts 精确相反数（|·| 恒平局）
        vals = sorted(shifts.values())
        assert len(vals) == 2 and abs(vals[0] + vals[1]) < 1e-9, f"seed{seed} 非相反数: {shifts}"
        flagged = {o.material_meta.solution_batch
                   for o in obs if "batch_shift" in reports[o.obs_id].flags}
        assert flagged == {"R0-B1"}, f"seed{seed} 选批判反：命中 {flagged}（注入批 R0-B1）"


def test_batch_direction_positive_shift_selects_injected():
    """双方向：升高型污染 shift=+0.18 亦选中被注入批 B1（回退锚 shift_hat>0 ⇒ 最高批）。"""
    exp, obs = make_board([
        {"injector": "batch_shift", "params": {"batch_suffix": "B1", "shift": 0.18}}
    ], n_cands=20)
    reports, _ = run_qc(exp, obs, moran_perm=99)
    bev = next(c.evidence for o in obs
               for c in reports[o.obs_id].checks if c.name == "batch_shift")
    assert bev.get("fired") and bev["shift_hat"] > 0.10
    flagged = {o.material_meta.solution_batch
               for o in obs if "batch_shift" in reports[o.obs_id].flags}
    assert flagged == {"R0-B1"}, f"升高型选批判反：命中 {flagged}"


def test_batch_fallback_anchor_shift_hat_sign():
    """回退锚单独路径：shift_hat 符号打破 IEEE 平局。两批精确相反数 shifts 下——
    shift_hat<0 ⇒ 选偏低批、shift_hat>0 ⇒ 选偏高批；shift_hat=0（无定向）⇒ None。"""
    shifts = {"B0": 0.0123, "B1": -0.0123}          # abs 平局（旧 max 会落 B0）
    assert _batch_fallback_pick(shifts, -0.18) == "B1"   # 降低型 → 偏低批
    assert _batch_fallback_pick(shifts, +0.18) == "B0"   # 该 shifts 下偏高批是 B0
    # 升高型注入（B1 偏高）
    assert _batch_fallback_pick({"B0": -0.0123, "B1": 0.0123}, +0.18) == "B1"
    assert _batch_fallback_pick(shifts, 0.0) is None     # 无方向 → 不猜
    assert _batch_fallback_pick({}, -0.18) is None


def test_batch_sentinel_anchor_primary_path():
    """主锚单独路径：哨兵绝对参考（deviation from 冻结 target=0，half_width=0.2）。
    清晰分离 → 命中偏离批；三种弃权（哨兵<2/批、target 出带、跨批差不显著）→ None。"""
    # B1 哨兵显著偏低、B0 在带内近 target → 选 B1
    pick, reason = _batch_sentinel_pick({"B0": [0.01, -0.02, 0.0],
                                         "B1": [-0.15, -0.14, -0.16]}, 0.2)
    assert pick == "B1" and reason == "sentinel"
    # 升高型：B1 哨兵显著偏高 → 主锚亦选 B1（不依赖"低=异常"前提）
    pick_up, _ = _batch_sentinel_pick({"B0": [0.0, 0.01], "B1": [0.16, 0.15]}, 0.2)
    assert pick_up == "B1"
    # 每批 <2 哨兵 → 弃权
    assert _batch_sentinel_pick({"B0": [0.0], "B1": [-0.15, -0.14]}, 0.2)[0] is None
    # target 不可信（干净参照批也出带）→ 弃权
    assert _batch_sentinel_pick({"B0": [0.5, 0.5], "B1": [0.6, 0.6]}, 0.2)[1] == "target_unreliable"
    # 跨批差不显著（哨兵噪声内）→ 弃权
    assert _batch_sentinel_pick({"B0": [0.0, 0.01, -0.01],
                                 "B1": [0.0, -0.01, 0.01]}, 0.2)[1] == "sentinel_not_significant"


def test_batch_two_anchor_conflict_record_only():
    """两锚冲突 → 响亮降级 record-only（不选批，诚实不猜）；一致/单锚 → 正常选批。"""
    sent = {"B0": [0.01, 0.0, -0.01], "B1": [-0.15, -0.14, -0.16]}   # 主锚 → B1
    # 回退锚指向 B0（shift_hat<0 且 B0 为偏低批）→ 与主锚冲突
    top, det = _batch_select(sent, 0.2, {"B0": -0.02, "B1": 0.02}, -0.18)
    assert top is None and det["select_anchor"] == "conflict_record_only"
    assert det["sentinel_pick"] == "B1" and det["fallback_pick"] == "B0"
    # 两锚一致 → 选 B1，标注 agree
    top2, det2 = _batch_select(sent, 0.2, {"B0": 0.02, "B1": -0.02}, -0.18)
    assert top2 == "B1" and det2["select_anchor"] == "sentinel_and_fallback_agree"
    # 主锚弃权、回退出批 → shift_hat_sign 锚
    top3, det3 = _batch_select({"B0": [0.0]}, 0.2, {"B0": 0.02, "B1": -0.02}, -0.18)
    assert top3 == "B1" and det3["select_anchor"] == "shift_hat_sign"


def test_batch_conflict_record_only_implicates_no_batch(monkeypatch):
    """判别性测试（红队 001）：主锚与回退锚指向不同批 → run_qc 必须走 record-only——
    检出仍 fired、但**不选任何批、不牵连任一孔**（batch_shift flag 全空），且证据显式标注。

    强制冲突：monkeypatch _batch_select 返回 (None, conflict_record_only)，验证下游接线
    确实不打分（而非静默把冲突当"选中某批"）。"""
    import expos.qc.checks as checks_mod

    exp, obs = make_board([
        {"injector": "batch_shift", "params": {"batch_suffix": "B1", "shift": -0.18}}
    ], n_cands=20)

    conflict_detail = {"select_anchor": "conflict_record_only", "sentinel_pick": "R0-B1",
                       "sentinel_reason": "sentinel", "fallback_pick": "R0-B0"}
    monkeypatch.setattr(checks_mod, "_batch_select",
                        lambda *a, **k: (None, conflict_detail))

    reports, _ = run_qc(exp, obs, moran_perm=99)
    bev = next(c.evidence for o in obs
               for c in reports[o.obs_id].checks if c.name == "batch_shift")
    # 检出仍成立（fired），但选批降级：无 fired_batch、无孔被牵连
    assert bev.get("fired") is True
    assert bev["select_anchor"] == "conflict_record_only"
    assert "fired_batch" not in bev
    flagged = [o for o in obs if "batch_shift" in reports[o.obs_id].flags]
    assert flagged == [], f"record-only 却牵连了孔: {[o.layout_meta.well_id for o in flagged]}"


def test_glare_channel_flags_all_wells():
    """glare prob=1：曝光通道命中全部孔（exposure>1.25，FPR≈0 首选通道）。"""
    exp, obs = make_board([
        {"injector": "glare", "params": {"prob": 1.0, "boost": 0.35}}
    ])
    reports, _ = run_qc(exp, obs, moran_perm=FAST)
    for o in obs:
        assert "glare_channel" in reports[o.obs_id].flags
        assert reports[o.obs_id].suspicion >= 0.6


def test_dust_channel_flags_anomalous_grain():
    """局部灰尘成核（partial）：颗粒数异常高的孔被 dust_channel 命中。"""
    exp, obs = make_board([
        {"injector": "dust_nucleation", "params": {"prob": 0.35, "drop": 0.4}}
    ], seed=1)
    reports, _ = run_qc(exp, obs, moran_perm=FAST)
    hit = [o for o in obs if "dust_channel" in reports[o.obs_id].flags]
    assert hit, "dust_channel 未命中任何孔"


# ---------------------------------------------------------------- QC 税（零伪影）

def test_clean_board_qc_tax_under_5pct():
    """零伪影板（noise 正常）：非控制观测 suspicion≥0.3 比例 ≤5%（20 种子平均）。"""
    taxes = []
    for sd in range(20):
        exp, obs = make_board([], seed=sd)
        reports, _ = run_qc(exp, obs, moran_perm=99, seed=0)
        cand = [o for o in obs if not o.is_control]
        taxes.append(sum(1 for o in cand if reports[o.obs_id].suspicion >= 0.3) / len(cand))
    mean_tax = float(np.mean(taxes))
    assert mean_tax <= 0.05, f"QC 税 {mean_tax:.3f} 超红线 5%"


# ---------------------------------------------------------------- PlateContext sane

def test_plate_context_sane_on_clean_board():
    exp, obs = make_board([])
    _, plate = run_qc(exp, obs, moran_perm=FAST)
    assert isinstance(plate, PlateContext)
    assert plate.round_id == exp.round_id
    assert plate.residual_grid.shape == (exp.layout.rows, exp.layout.cols)
    assert plate.row_effects.shape == (exp.layout.rows,)
    assert plate.col_effects.shape == (exp.layout.cols,)
    # 干净板：无明显边缘抬升、残差尺度为正、漂移相关有界
    assert abs(plate.edge_paired_diff) < 0.02
    assert np.isfinite(plate.gradient_slope_t)
    assert plate.resid_scale > 0
    assert -1.0 <= plate.drift_corr <= 1.0
    # Moran 跑出并落在合法区间
    assert 0.0 < plate.moran["p_sim"] <= 1.0
    # 每格填充孔有 d_edge、控制孔有稳健 z
    assert len(plate.d_edge) == len(exp.layout.wells)
    assert len(plate.sentinel_zscores) == sum(1 for o in obs if o.is_control)
    assert all(np.isfinite(z) for z in plate.sentinel_zscores.values())
    assert set(plate.batch_shifts) and all(np.isfinite(v) for v in plate.batch_shifts.values())


# ---------------------------------------------------------------- 确定性

def test_deterministic_same_seed():
    exp, obs = make_board([
        {"injector": "edge_evaporation", "params": {"strength": 0.4, "decay_wells": 1.0}}
    ])
    r1, p1 = run_qc(exp, obs, moran_perm=FAST, seed=3)
    r2, p2 = run_qc(exp, obs, moran_perm=FAST, seed=3)
    assert {k: v.suspicion for k, v in r1.items()} == {k: v.suspicion for k, v in r2.items()}
    assert {k: v.flags for k, v in r1.items()} == {k: v.flags for k, v in r2.items()}
    assert p1.moran == p2.moran  # 置换检验逐位复现
    assert p1.edge_paired_diff == p2.edge_paired_diff


# ---------------------------------------------------------------- lazy 收集器

def test_lazy_collector_hard_failure_does_not_block_others():
    """一个 hard 检查失败（NaN / 越量程 / 曝光越界）不阻止其余检查跑、其余观测出报告。"""
    exp, obs = make_board([
        {"injector": "edge_evaporation", "params": {"strength": 0.5, "decay_wells": 1.0}}
    ])
    # 污染一个观测：value=NaN；另一个：曝光越界
    obs[0].result.value = float("nan")
    obs[1].instrument_meta.exposure = 5.0
    reports, plate = run_qc(exp, obs, moran_perm=FAST)

    # 每个观测都拿到报告（绝不首错中断）
    assert len(reports) == len(obs)
    # 受污染观测的 hard 检查失败
    r0 = reports[obs[0].obs_id]
    assert "missing_nan" in r0.flags and r0.suspicion == 1.0
    r1 = reports[obs[1].obs_id]
    assert "exposure_illumination" in r1.flags
    # 其余结构检查仍对全板跑出（板级 edge 仍命中边缘孔）
    edge_wells = [w for w, d in plate.d_edge.items() if d == 0]
    susp = _susp(reports, obs)
    assert any(susp[w] >= 0.6 for w in edge_wells)
    # 每份报告都含完整三级检查套件（跑全部，不首错短路）
    names = {c.name for c in r0.checks}
    assert {"missing_nan", "out_of_range", "exposure_illumination",
            "edge_effect", "spatial_moran", "batch_shift"} <= names


def test_hard_out_of_range_and_exposure():
    exp, obs = make_board([])
    obs[0].result.value = 5.0            # 超 metric_range 上界
    obs[1].instrument_meta.illumination = 0.1  # 照度过低
    reports, _ = run_qc(exp, obs, moran_perm=99, metric_range=(0.0, 1.2))
    assert "out_of_range" in reports[obs[0].obs_id].flags
    assert reports[obs[0].obs_id].suspicion == 1.0
    assert "exposure_illumination" in reports[obs[1].obs_id].flags


# ---------------------------------------------------------------- 冷启动纪律

def test_sentinel_control_band_cold_start_record_only():
    """哨兵历史 <3 轮 → sentinel_control_band armed=False（写 evidence 不产 suspicion）。"""
    exp, obs = make_board([])
    hist2 = QCHistory()
    hist2.append_round([0.15, 0.16, 0.14, 0.15, 0.16])
    hist2.append_round([0.15, 0.15, 0.16, 0.14, 0.15])
    reports, _ = run_qc(exp, obs, history=hist2, moran_perm=99)
    for o in obs:
        if not o.is_control:
            continue
        chk = [c for c in reports[o.obs_id].checks if c.name == "sentinel_control_band"][0]
        assert chk.evidence["armed"] is False and chk.score == 0.0

    # ≥3 轮 → armed=True
    hist3 = QCHistory()
    for _ in range(3):
        hist3.append_round([0.15, 0.15, 0.16, 0.14, 0.15])
    reports3, _ = run_qc(exp, obs, history=hist3, moran_perm=99)
    ctrl = [o for o in obs if o.is_control][0]
    chk = [c for c in reports3[ctrl.obs_id].checks if c.name == "sentinel_control_band"][0]
    assert chk.evidence["armed"] is True


def test_history_accepts_observation_list():
    """policy 传的是既往观测列表（非 QCHistory）：run_qc 应容忍并抽取哨兵历史。"""
    exp, obs = make_board([])
    prior = []
    for rd in range(3):
        _, prev = make_board([], seed=rd)
        for o in prev:
            o.round_id = rd
        prior.extend([o for o in prev if o.is_control])
    reports, _ = run_qc(exp, obs, history=prior, moran_perm=99)
    ctrl = [o for o in obs if o.is_control][0]
    chk = [c for c in reports[ctrl.obs_id].checks if c.name == "sentinel_control_band"][0]
    assert chk.evidence["armed"] is True  # 3 轮观测 → armed


# ---------------------------------------------------------------- 跨轮时间漂移（压测 R1-2c 判别）

def _drift_round(rate, rid, seed):
    """一轮带 instrument_drift(linear, rate) 的板（rate=0 → 干净）。"""
    scen = [] if rate == 0.0 else [
        {"injector": "instrument_drift", "params": {"mode": "linear", "rate": rate}}
    ]
    exp = make_experiment(round_id=rid, n_cands=6, seed=7)
    sim = CrystalSim({"noise_sd": 0.02, "artifact_scenario": scen})
    result = sim.execute(exp, np.random.default_rng(seed))
    obs = raw_to_observations(exp, result.raw_results)
    return exp, obs


def _drift_check(reports, o):
    return [c for c in reports[o.obs_id].checks if c.name == "temporal_drift"][0]


def test_temporal_drift_cross_round_detects_aging_instrument():
    """判别（R1-2c）：仪器逐轮老化（instrument_drift linear，率逐轮加深）的多轮 run →
    armed 后的后期轮 temporal_drift score>0 且全轮牵连；<3 轮历史仍 record-only。

    诚实边界（留档，见 checks.py 漂移段注释）：本域执行面哨兵先采（capture 0..4）、
    副本相邻成对，轮内 capture 斜坡在去身份对比中被结构性抵消（"单轮不可辨识"）；
    注入器逐轮重置的恒参 AR(1) 常驻档轮均值可交换、低于跨轮探测信息地板（诚实漏检）。
    可判的是跨轮基线游走——此处用逐轮加深的 linear 档模拟仪器老化。"""
    hist = QCHistory()
    fired_rounds = []
    for r in range(7):
        exp, obs = _drift_round(-0.04 * r, r, seed=100 + r)
        reports, _ = run_qc(exp, obs, history=hist, moran_perm=49)
        chk = _drift_check(reports, obs[0])
        if r < 3:
            # 冷启动：历史 <3 轮 → record-only（诚实降级不变）
            assert chk.evidence["armed"] is False and chk.score == 0.0
        elif chk.score > 0:
            fired_rounds.append(r)
            assert chk.evidence["fired"] is True
            assert chk.evidence["cusum_stat"] > 6.0
            # 仪器级全局效应 → 全轮牵连（capped 弱嫌疑），且进 flags
            for o in obs:
                c = _drift_check(reports, o)
                assert 0.0 < c.score <= 0.40
                assert "temporal_drift" in reports[o.obs_id].flags
        hist.append_round([o.result.value for o in obs if o.is_control])
    assert fired_rounds, "老化漂移多轮 run 后期轮未检出"
    assert min(fired_rounds) >= 4  # 早期（漂移尚浅）不误报


def _resident_round(params, rid, seed):
    """一轮带 instrument_drift(resident, **params) 的板。"""
    scen = [{"injector": "instrument_drift", "params": dict(params, mode="resident")}]
    exp = make_experiment(round_id=rid, n_cands=6, seed=7)
    sim = CrystalSim({"noise_sd": 0.02, "artifact_scenario": scen})
    result = sim.execute(exp, np.random.default_rng(seed))
    return exp, raw_to_observations(exp, result.raw_results)


def test_temporal_drift_resident_aging_detected_late_rounds():
    """判别（R2 §1.1）：resident 跨轮持久老化漂移 → 冻结基线 CUSUM 在后期轮 score>0 检出。
    对照旧 ar1 逐轮重置（轮内 AR(1)、跨轮正交、CUSUM 恒 0）——resident 修的正是这个病灶。"""
    params = {"rate_per_round": -0.04, "phi": 0.95, "sigma": 0.01}
    hist = QCHistory()
    fired_rounds = []
    for r in range(7):
        exp, obs = _resident_round(params, r, seed=100 + r)
        reports, _ = run_qc(exp, obs, history=hist, moran_perm=49)
        chk = _drift_check(reports, obs[0])
        if r < 3:
            assert chk.evidence["armed"] is False and chk.score == 0.0
        elif chk.score > 0:
            fired_rounds.append(r)
            assert chk.evidence["fired"] is True
            for o in obs:  # 仪器级全局效应 → 全轮 capped 牵连
                assert 0.0 < _drift_check(reports, o).score <= 0.40
        hist.append_round([o.result.value for o in obs if o.is_control])
    assert fired_rounds, "resident 老化漂移多轮 run 后期轮未检出（跨轮持久应可判）"
    assert min(fired_rounds) >= 4  # 漂移尚浅的早期不误报


def test_temporal_drift_resident_pure_within_ar1_is_honest_blind_spot():
    """诚实盲区（留档，与 R2 §1.1 建议 b 一致）：resident 仅含轮内 AR(1) 分量
    （rate_per_round=0、sigma_between=0、无周期）时，轮均值跨轮可交换、低于跨轮探测
    信息地板 → temporal_drift 恒 0。这是与"注入器逐轮重置的恒参 AR(1)"同性质的**结构性
    漏检**，非 bug：跨轮哨兵只能抓持久基线游走，抓不到围绕固定基线的轮内自相关。"""
    params = {"rate_per_round": 0.0, "sigma_between": 0.0, "phi": 0.95, "sigma": 0.01}
    hist = QCHistory()
    for r in range(7):
        exp, obs = _resident_round(params, r, seed=100 + r)
        reports, _ = run_qc(exp, obs, history=hist, moran_perm=49)
        assert _drift_check(reports, obs[0]).score == 0.0  # 恒不检出（诚实盲区）
        hist.append_round([o.result.value for o in obs if o.is_control])


def test_temporal_drift_clean_multi_round_fpr_under_5pct():
    """20 种子干净多轮：armed 轮 temporal_drift 假警率 ≤5%（QC 税测法同款，保守标定）。"""
    fired = armed = 0
    for sd in range(20):
        hist = QCHistory()
        for r in range(7):
            exp, obs = _drift_round(0.0, r, seed=1000 * sd + r)
            reports, _ = run_qc(exp, obs, history=hist, moran_perm=49)
            chk = _drift_check(reports, obs[0])
            if chk.evidence.get("armed"):
                armed += 1
                if chk.score > 0:
                    fired += 1
            hist.append_round([o.result.value for o in obs if o.is_control])
    assert armed >= 60  # 20 种子 × 4 个 armed 轮
    assert fired / armed <= 0.05, f"跨轮漂移假警率 {fired}/{armed} 超 5%"


# ---------------------------------------------------------------- 响亮失败

def test_missing_layout_raises():
    exp, obs = make_board([])
    exp.layout = None
    with pytest.raises(CheckError):
        run_qc(exp, obs)


# ---------------------------------------------------------------- 依赖隔离（M5 红线）

def test_checks_source_has_no_forbidden_deps():
    src = (ROOT / "expos" / "qc" / "checks.py").read_text(encoding="utf-8")
    forbidden = ("expos.adapters", "expos.planner", "expos.agent", "expos.models", "truth")
    hits = [f for f in forbidden if f in src]
    assert hits == [], f"checks.py 触碰禁区: {hits}"


def test_checks_import_graph_clean():
    import subprocess
    import sys

    code = (
        "import sys; sys.path.insert(0, '.');"
        "import expos.qc.checks;"
        "bad=[m for m in sys.modules if m.startswith("
        "('expos.adapters','expos.planner','expos.agent','expos.models'))];"
        "assert not bad, bad"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True
    )
    assert result.returncode == 0, f"import 图污染: {result.stderr}"


# ---------------------------------------------------------------- 止血：单检查崩溃隔离
# 判别性测试（qc 止血批）：模块 docstring 曾自称"每检查 try/except"但 7 块无保护——
# 单检查内部异常会直穿崩掉整轮 QC。以下测试钉死修复：崩溃 → error-evidence 记录
# （passed=False + evidence.check_crashed=True + 进 flags + logging.error），score 保持
# 0.0（record-only，不劫持 max-fold suspicion）。ERROR kind (EVIDENCE_TYPING) 落地前的过渡语义。

def test_board_check_crash_becomes_error_evidence(monkeypatch, caplog):
    """monkeypatch 使 row_col_gradient 的板级计算内部 raise → run_qc 不崩、每孔该检查
    passed=False 且 evidence.check_crashed=True，其余检查照常产出，且崩溃不劫持裁决
    （score 0.0，clean 板全局 suspicion 仍为 0），日志有 error 记录。"""
    import logging as _logging

    import expos.qc.checks as checks_mod

    exp, obs = make_board([])  # 干净板：无真伪影，任何 suspicion>0 只可能来自崩溃劫持

    def _boom(_y):
        raise ValueError("injected slope failure")

    monkeypatch.setattr(checks_mod, "_slope_t", _boom)

    with caplog.at_level(_logging.ERROR):
        reports, _ = run_qc(exp, obs, moran_perm=FAST)  # 不得抛异常

    assert len(reports) == len(obs)                      # 整轮未崩、每观测出报告
    for o in obs:
        rep = reports[o.obs_id]
        grad = [c for c in rep.checks if c.name == "row_col_gradient"][0]
        assert grad.passed is False                      # 该孔该检查判 False
        assert grad.evidence.get("check_crashed") is True
        assert "error" in grad.evidence
        assert grad.score == 0.0                         # record-only，不劫持
        assert "row_col_gradient" in rep.flags           # 进 flags 可见
        # 其余检查照常产出（未被崩溃波及）
        names = {c.name for c in rep.checks}
        assert {"missing_nan", "out_of_range", "edge_effect", "batch_shift"} <= names
        assert [c for c in rep.checks if c.name == "missing_nan"][0].passed is True
        # 崩溃未劫持裁决：干净板全局 suspicion 仍为 0
        assert rep.suspicion == 0.0
    assert any("row_col_gradient" in r.message and r.levelno == _logging.ERROR
               for r in caplog.records), "崩溃须有 logging.error 记录（绝不静默吞错）"


def test_per_observation_aggregation_backstop(monkeypatch, caplog):
    """逐观测聚合循环本身受兜底保护：某观测聚合体崩溃 → 该孔得一份 qc_aggregation
    error 报告而非整轮崩溃。让 _report_for 内某 QCCheck 构造 raise（板级预计算只产
    score/evidence dict、从不构造 QCCheck，故此注入只命中聚合体）。"""
    import logging as _logging

    import expos.qc.checks as checks_mod

    exp, obs = make_board([])
    real_qccheck = checks_mod.QCCheck

    def _guard_qccheck(*args, **kwargs):
        if kwargs.get("name") == "exposure_illumination":
            raise RuntimeError("injected QCCheck construction failure")
        return real_qccheck(*args, **kwargs)

    monkeypatch.setattr(checks_mod, "QCCheck", _guard_qccheck)

    with caplog.at_level(_logging.ERROR):
        reports, _ = run_qc(exp, obs, moran_perm=FAST)  # 不得抛异常

    assert len(reports) == len(obs)
    for o in obs:
        rep = reports[o.obs_id]
        assert "qc_aggregation" in rep.flags
        agg = [c for c in rep.checks if c.name == "qc_aggregation"][0]
        assert agg.passed is False and agg.evidence.get("check_crashed") is True
        assert rep.suspicion == 0.0                      # 兜底不劫持裁决
    assert any("aggregation crashed" in r.message and r.levelno == _logging.ERROR
               for r in caplog.records)
