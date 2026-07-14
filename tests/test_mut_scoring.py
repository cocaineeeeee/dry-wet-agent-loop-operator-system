"""变异语料击杀：eval/scoring.py 的方向/阈值守门（MU2 V4/V2/V3）。

红队 MU2 存活变异补测——这些是**测试缺口**（产品码正确），断言在正常码下全绿，
对台账 diff 施加 patch 后必转红。详见 tests/mutants/MANIFEST.tsv。

- V4：simple_regret = (sign·f* − best) 方向。翻转成 (best − sign·f*) 后 max(0,·) 恒 0，
  旧断言只查"≥0 且非增"（恒 0 皆过）——评测核心量无方向守门。
- V2：污染阈 τ = 3σ 常数。放宽到 5σ 后 (3σ,5σ) 区间的偏差不再计污染。
- V3：wrong_optimum_hit 的 3σ 门。放宽到 5σ 后同区间的假最优命中被漏判。
"""

from pathlib import Path

import pytest

from expos.eval.scoring import score_run
from expos.kernel.objects import (
    LayoutMeta,
    MeasuredResult,
    ObservationObject,
    Routing,
    TrustLevel,
)
from expos.kernel.store import RunStore

from tests.test_eval import CRYSTAL

_NOISE_SD = 0.02  # crystal.yaml simulator.noise_sd → 3σ=0.06, 5σ=0.10


def _build_run(tmp_path: Path, specs, mode: str = "os") -> Path:
    """specs: list of (well_id, col, result_value, trust, routing, true_value, measured_value).
    构造最小 run（单轮），供 score_run 消费。"""
    run_dir = tmp_path / f"run_{mode}"
    store = RunStore(run_dir)
    store.save_config({"mode": mode})
    truth_rows = []
    for i, (well, col, val, trust, routing, tv, mv) in enumerate(specs):
        o = ObservationObject(
            obs_id=f"o{i}", exp_id="exp_x", round_id=0, cand_id=f"c{i}",
            result=MeasuredResult(metric="quality", value=val),
            layout_meta=LayoutMeta(well_id=well, row=0, col=col),
        )
        o.trust, o.routing, o.trust_confidence = trust, routing, 1.0
        store.save_observation(o)
        truth_rows.append({"well_id": well, "true_value": tv,
                           "measured_value": mv, "artifacts": []})
    store.save_truth(0, truth_rows)
    return run_dir


# ------------------------------------------------------------------ V4：regret 方向
def test_simple_regret_is_positive_gap_not_flipped_zero(tmp_path):
    """best_true 明显低于 f* 的 run：simple_regret ≈ f*−best_true > 0（非恒 0）。
    钉方向：翻转成 best−f* 后 max(0,·) 恒 0，本断言必红。"""
    T = TrustLevel.TRUSTED
    R = Routing.TO_RESPONSE_MODEL
    # 两条 TRUSTED，最优真值 0.30 ≪ f*(≈0.497)；测量=真值（无污染）
    specs = [("A1", 0, 0.20, T, R, 0.20, 0.20),
             ("A2", 1, 0.30, T, R, 0.30, 0.30)]
    s = score_run(_build_run(tmp_path, specs), CRYSTAL, n_opt_scan=64)
    best_true = 0.30
    assert s["rounds"][-1]["best_true_so_far"] == pytest.approx(best_true)
    assert s["f_star"] > best_true  # 前提：真最优在观测最优之上
    assert s["final_regret"] == pytest.approx(s["f_star"] - best_true, abs=1e-9)
    assert s["final_regret"] > 1e-3  # 严格为正 → 恒 0 的翻转变异被击杀


# ------------------------------------------------------------------ V2：污染 τ=3σ
def test_contamination_tau_is_three_sigma_gate(tmp_path):
    """一条 |bias|=0.08 的 TRUSTED（落在 3σ=0.06 与 5σ=0.10 之间）+ 一条干净：
    τ=3σ 计其为污染（比例 0.5）；放宽到 5σ 则漏计（0.0）。钉 3σ 常数。"""
    T = TrustLevel.TRUSTED
    R = Routing.TO_RESPONSE_MODEL
    bias = 4.0 * _NOISE_SD  # 0.08 ∈ (3σ, 5σ)
    specs = [("A1", 0, 0.30, T, R, 0.30, 0.30),                 # 干净
             ("A2", 1, 0.30 + bias, T, R, 0.30, 0.30 + bias)]   # 污染（仅 3σ 门捕获）
    s = score_run(_build_run(tmp_path, specs), CRYSTAL, n_opt_scan=64)
    assert s["tau_bias"] == pytest.approx(3.0 * _NOISE_SD)
    assert s["contaminated_in_training"] == pytest.approx(0.5)


# ------------------------------------------------------------------ V3：wrong_opt 3σ 门
def test_wrong_optimum_hit_uses_three_sigma_gate(tmp_path):
    """best-trusted 的 measured 超真值有利侧 0.08（∈ (3σ,5σ)）：
    wrong_optimum_hit（3σ 门）=True 而 5σ 敏感性列=False。放宽 3σ→5σ 使前者转 False。"""
    T = TrustLevel.TRUSTED
    R = Routing.TO_RESPONSE_MODEL
    delta = 4.0 * _NOISE_SD  # 0.08 ∈ (3σ,5σ)
    # 唯一 TRUSTED：measured=0.38 为最优候选，真值 0.30 → delta_fav=0.08
    specs = [("A1", 0, 0.30 + delta, T, R, 0.30, 0.30 + delta)]
    s = score_run(_build_run(tmp_path, specs), CRYSTAL, n_opt_scan=64)
    last = s["rounds"][-1]
    assert last["wrong_optimum_hit"] is True          # 3σ 门捕获
    assert last["wrong_optimum_hit_5sigma"] is False  # 5σ 敏感性列不捕获
