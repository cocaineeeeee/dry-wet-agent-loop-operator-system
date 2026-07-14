"""M5 纯统计原语验收测试（REFERENCE_MAP §11.4 / §13.5）。

覆盖：queen 权重邻居数/行标准化/剔除同步/全剔除失败；Moran 合成梯度显著 vs 噪声不显著/
EI/确定性；median polish 行列效应恢复 + NaN 收敛；MAD 稳健 z 命中；SBB 公式值/边界；
CUSUM/EWMA 跳变告警；placebo/subsample 反驳器双向判据；依赖隔离红线。
"""

from pathlib import Path

import numpy as np
import pytest

from expos.qc.stats import (
    StatsError,
    cohens_d,
    cusum,
    ewma,
    mad_z,
    median_polish,
    moran_permutation,
    morans_i,
    queen_w,
    refute_placebo,
    refute_subsample,
    sbb_suspicion,
)


# ---------------------------------------------------------------- queen 权重

def test_queen_neighbor_counts():
    W, keep = queen_w(6, 8)
    assert keep == list(range(48))
    deg = (W > 0).sum(axis=1)
    # 角 (0,0)=3, 边 (0,3)=5, 心 (3,4)=8
    assert deg[0 * 8 + 0] == 3
    assert deg[0 * 8 + 3] == 5
    assert deg[3 * 8 + 4] == 8


def test_queen_row_standardized_sums_to_one():
    W, _ = queen_w(6, 8)
    assert np.allclose(W.sum(axis=1), 1.0)  # 6×8 全格皆有邻居


def test_queen_drop_syncs_rows_and_index():
    # 剔除 (0,0) 与线性下标 10 → keep 少两格、维度同步收缩
    W, keep = queen_w(6, 8, drop={(0, 0), 10})
    assert 0 not in keep and 10 not in keep
    assert len(keep) == 46
    assert W.shape == (46, 46)
    # 被剔除格位不再是任何行的邻居
    W_full, _ = queen_w(6, 8)
    assert (0 * 8 + 1) in keep  # 邻居仍在
    # (0,1) 原本邻居含 (0,0)，剔除后其行和仍为 1（对剩余邻居重标准化）
    i = keep.index(0 * 8 + 1)
    assert np.isclose(W[i].sum(), 1.0)


def test_queen_island_row_is_zero():
    # 把某格所有邻居都剔除 → 该格成岛屿，行全 0（lag=0，不除零）
    # (0,0) 的 queen 邻居 = {(0,1),(1,0),(1,1)}
    drop = {(0, 1), (1, 0), (1, 1)}
    W, keep = queen_w(6, 8, drop=drop)
    i = keep.index(0)
    assert np.allclose(W[i], 0.0)


def test_queen_drop_all_raises():
    with pytest.raises(StatsError):
        queen_w(2, 2, drop={0, 1, 2, 3})


# ---------------------------------------------------------------- Moran's I

def _row_gradient(rows=6, cols=8):
    """行梯度场：值随行号线性增大（强正空间自相关）。"""
    g = np.zeros((rows, cols))
    for r in range(rows):
        g[r, :] = r
    return g.ravel()


def test_moran_gradient_significant():
    W, _ = queen_w(6, 8)
    y = _row_gradient()
    res = moran_permutation(y, W, n_perm=999, seed=0)
    assert res["I"] > 0.5
    assert res["p_sim"] < 0.01


def test_moran_noise_not_significant():
    W, _ = queen_w(6, 8)
    rng = np.random.default_rng(123)
    y = rng.normal(size=48)
    res = moran_permutation(y, W, n_perm=999, seed=0)
    assert res["p_sim"] > 0.1


def test_moran_ei_and_determinism():
    W, _ = queen_w(6, 8)
    y = _row_gradient() + np.random.default_rng(1).normal(scale=0.1, size=48)
    r1 = moran_permutation(y, W, n_perm=999, seed=7)
    r2 = moran_permutation(y, W, n_perm=999, seed=7)
    assert r1 == r2  # 同 seed 确定性
    assert np.isclose(r1["EI"], -1.0 / (48 - 1))


def test_morans_i_length_mismatch_raises():
    W, _ = queen_w(6, 8)
    with pytest.raises(StatsError):
        morans_i(np.zeros(10), W)


# ---------------------------------------------------------------- median polish

def test_median_polish_recovers_row_col_effects():
    rng = np.random.default_rng(0)
    nr, nc = 6, 8
    true_row = np.array([-2.0, -1.0, 0.0, 1.0, 2.0, 3.0])
    true_col = np.linspace(-3, 3, nc)
    overall = 5.0
    grid = overall + true_row[:, None] + true_col[None, :] + rng.normal(scale=0.05, size=(nr, nc))
    out = median_polish(grid)
    # 恢复的行/列效应与真值高相关
    assert np.corrcoef(out["row"], true_row)[0, 1] > 0.9
    assert np.corrcoef(out["col"], true_col)[0, 1] > 0.9
    # 残差应远小于原始信号
    assert np.nanmax(np.abs(out["residuals"])) < 1.0


def test_median_polish_nan_tolerant():
    rng = np.random.default_rng(2)
    true_row = np.arange(6, dtype=float)
    true_col = np.arange(8, dtype=float)
    grid = 3.0 + true_row[:, None] + true_col[None, :] + rng.normal(scale=0.05, size=(6, 8))
    grid[1, 3] = np.nan
    grid[4, 6] = np.nan
    out = median_polish(grid)  # 不应抛异常、能收敛
    assert np.isnan(out["residuals"][1, 3])  # NaN 透传
    assert np.corrcoef(out["row"], true_row)[0, 1] > 0.9


# ---------------------------------------------------------------- MAD 稳健 z

def test_mad_z_flags_isolated_outlier():
    x = np.array([1.0, 1.1, 0.9, 1.05, 0.95, 1.0, 5.0])  # 末位离群
    z = mad_z(x)
    assert abs(z[-1]) > 3.5
    assert np.all(np.abs(z[:-1]) < 3.5)


def test_mad_z_zero_mad_raises():
    with pytest.raises(StatsError):
        mad_z(np.array([2.0, 2.0, 2.0, 2.0, 2.0]))


# ---------------------------------------------------------------- SBB 校准

def test_sbb_formula_value():
    assert abs(sbb_suspicion(0.05) - 0.2893) < 1e-3


def test_sbb_trends_to_zero_near_one():
    assert sbb_suspicion(0.999) < 0.01
    assert sbb_suspicion(1.0) == 0.0


def test_sbb_peak_at_inverse_e():
    assert abs(sbb_suspicion(1.0 / np.e) - 0.5) < 1e-6


def test_sbb_illegal_raises():
    for bad in (0.0, -0.1, 1.5):
        with pytest.raises(StatsError):
            sbb_suspicion(bad)


# ---------------------------------------------------------------- Cohen's d

def test_cohens_d_basic_and_guard():
    a = np.array([1.0, 1.2, 0.9, 1.1])
    b = np.array([2.0, 2.1, 1.9, 2.2])
    assert cohens_d(a, b) < -2.0  # 大效应、方向为负
    with pytest.raises(StatsError):
        cohens_d(np.array([1.0]), b)


# ---------------------------------------------------------------- CUSUM / EWMA

def test_cusum_alarms_after_shift():
    rng = np.random.default_rng(0)
    base = rng.normal(0.0, 1.0, size=30)
    shift = rng.normal(4.0, 1.0, size=30)  # 第 30 点起均值跳变
    x = np.concatenate([base, shift])
    out = cusum(x, k=0.5, h=5.0, target=0.0, sd=1.0)
    assert out["alarm_idx"], "应当告警"
    assert min(out["alarm_idx"]) >= 30  # 告警在跳变之后


def test_cusum_clean_no_alarm():
    rng = np.random.default_rng(1)
    x = rng.normal(0.0, 1.0, size=60)
    out = cusum(x, k=0.5, h=5.0, target=0.0, sd=1.0)
    assert out["alarm_idx"] == []


def test_ewma_tracks_shift():
    x = np.concatenate([np.zeros(20), np.full(20, 3.0)])
    z = ewma(x, lam=0.2)
    assert z[19] < 0.5   # 跳变前贴近基线
    assert z[-1] > 2.0   # 跳变后爬升逼近新均值


# ---------------------------------------------------------------- 反驳器

def _make_edge_center_data(rows=6, cols=8, effect=1.0, noise=0.05, seed=0):
    """构造 (value, is_edge) 行数据：边缘格位系统性偏高 effect。"""
    rng = np.random.default_rng(seed)
    rows_list = []
    for r in range(rows):
        for c in range(cols):
            is_edge = 1.0 if (r in (0, rows - 1) or c in (0, cols - 1)) else 0.0
            val = effect * is_edge + rng.normal(scale=noise)
            rows_list.append((val, is_edge))
    return np.array(rows_list)


def _edge_minus_center(data):
    is_edge = data[:, 1]
    edge = data[is_edge == 1.0, 0]
    center = data[is_edge == 0.0, 0]
    if edge.size == 0 or center.size == 0:
        return 0.0
    return float(edge.mean() - center.mean())


def test_refute_placebo_pass_on_real_effect():
    data = _make_edge_center_data(effect=1.0, seed=1)
    values = data[:, 0]
    labels = data[:, 1]

    def stat(lbl):
        edge = values[lbl == 1.0]
        center = values[lbl == 0.0]
        return float(edge.mean() - center.mean())

    res = refute_placebo(stat, labels, n=499, seed=0)
    assert res["observed"] > 0.5
    assert res["passed"]  # 打乱标签 → 效应塌零


def test_refute_placebo_fail_on_null():
    data = _make_edge_center_data(effect=0.0, noise=0.3, seed=2)
    values = data[:, 0]
    labels = data[:, 1]

    def stat(lbl):
        edge = values[lbl == 1.0]
        center = values[lbl == 0.0]
        return float(edge.mean() - center.mean())

    res = refute_placebo(stat, labels, n=499, seed=0)
    assert not res["passed"]  # 无真实效应 → 相对阈值塌零判据不满足


def test_refute_subsample_pass_on_real_effect():
    data = _make_edge_center_data(effect=1.0, seed=3)
    res = refute_subsample(_edge_minus_center, data, frac=0.8, n=100, seed=0)
    assert res["observed"] > 0.5
    assert res["passed"]  # 子样下效应稳定


def test_refute_determinism():
    data = _make_edge_center_data(effect=1.0, seed=3)
    r1 = refute_subsample(_edge_minus_center, data, frac=0.8, n=100, seed=42)
    r2 = refute_subsample(_edge_minus_center, data, frac=0.8, n=100, seed=42)
    assert r1 == r2


# ---------------------------------------------------------------- 依赖隔离（M5 红线）

def test_stats_source_has_no_forbidden_deps():
    src = (Path(__file__).resolve().parent.parent / "expos" / "qc" / "stats.py").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "expos.adapters",
        "expos.planner",
        "expos.agent",
        "expos.models",
        "truth",
    )
    hits = [f for f in forbidden if f in src]
    assert hits == [], f"stats.py 触碰禁区: {hits}"


def test_stats_import_graph_clean():
    import subprocess
    import sys

    code = (
        "import sys; sys.path.insert(0, '.');"
        "import expos.qc.stats;"
        "bad=[m for m in sys.modules if m.startswith("
        "('expos.adapters','expos.planner','expos.agent','expos.models'))];"
        "assert not bad, bad"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"import 图污染: {result.stderr}"
