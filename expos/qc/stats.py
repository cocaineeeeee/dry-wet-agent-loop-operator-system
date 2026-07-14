"""M5 纯统计原语（小样本 QC 三级检查 + DoWhy 式反驳器）。

依据 docs/REFERENCE_MAP.md §11.4（三级 QC 配置表）与 §13.5（esda / DoWhy 逐行配方）。
本层是**纯函数库**：只依赖 numpy，不 import expos 的 adapters/planner/agent/models，
也不接触任何真值——QC 只能看观测量，不能看模拟器内部真相（公理 2）。

设计取舍（§13.5 明列的坑，逐一规避）：
  * Moran 置换用**不折叠单尾 greater**：p=(#{sim≥I}+1)/(P+1)（esda 默认折叠双尾
    "uniformly too small, not advised"，不抄）；EI=−1/(n−1) 非零，判显著要看它。
  * NaN 孔剔除须**同步删 W 行列**；岛屿单元（无邻接）lag=0（不做行标准化，权重全 0）。
  * 反驳器两判据**方向相反**：placebo 判"效应塌零"、subsample 判"效应稳定"，勿混。
  * 反驳器 p 值统一走**经验分位**（n<100 时 DoWhy 切正态近似，40 观测不稳，不抄）。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

import numpy as np

_E = float(np.e)


class StatsError(Exception):
    """统计原语的响亮失败：非法输入、退化样本、全剔除等一律抛此异常而非静默返回。"""


# ==================================================================== 空间权重

def _norm_cell(cell: Any, cols: int) -> int:
    """把格位标识归一化为行主序线性下标：支持 int（线性）或 (row, col) 元组。"""
    if isinstance(cell, (tuple, list)):
        r, c = int(cell[0]), int(cell[1])
        return r * cols + c
    return int(cell)


def queen_w(
    rows: int, cols: int, drop: Iterable[Any] = frozenset()
) -> tuple[np.ndarray, list[int]]:
    """queen 邻接（网格 Chebyshev 距离 1，8 邻域）→ 行标准化权重矩阵 W 与索引映射。

    依据 REFERENCE_MAP §13.5："queen 邻接=网格 Chebyshev 距离 1"、"NaN 孔剔除须同步删
    W 行列"、"岛屿单元 lag=0"。

    参数
      rows, cols : 网格尺寸。
      drop       : 需剔除的格位集合（如 NaN 孔），元素为线性下标 int 或 (row, col) 元组。

    返回
      (W, keep)：
        W    — 形状 (m, m) 的行标准化权重（m=有效格位数）；某行有邻居则该行和为 1，
               岛屿行（被剔除邻居后无邻接）全 0（lag=0，稀释而非除零）。
        keep — 列表，keep[i] = 矩阵第 i 行对应格位的**线性下标**（升序）；调用方须按此
               顺序对齐 y。剔除与 y 的对齐即靠这份映射（§13.5"剔除须同步"）。

    全部格位被剔除时**响亮失败**（StatsError）——空网格上算 Moran 无意义。
    """
    if rows <= 0 or cols <= 0:
        raise StatsError(f"网格尺寸非法: rows={rows}, cols={cols}")
    n = rows * cols
    dropped = {_norm_cell(d, cols) for d in drop}
    keep = [idx for idx in range(n) if idx not in dropped]
    if not keep:
        raise StatsError("所有格位均被剔除，无法构建权重矩阵")
    pos = {idx: i for i, idx in enumerate(keep)}  # 线性下标 → 矩阵行
    m = len(keep)
    A = np.zeros((m, m), dtype=float)
    for idx in keep:
        r, c = divmod(idx, cols)
        i = pos[idx]
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                nr, nc = r + dr, c + dc
                if 0 <= nr < rows and 0 <= nc < cols:
                    nidx = nr * cols + nc
                    j = pos.get(nidx)
                    if j is not None:
                        A[i, j] = 1.0
    deg = A.sum(axis=1)
    W = np.zeros_like(A)
    nz = deg > 0
    W[nz] = A[nz] / deg[nz, None]  # 岛屿行（deg=0）保持全 0
    return W, keep


# ==================================================================== Moran's I

def morans_i(y: np.ndarray, W: np.ndarray) -> float:
    """行标准化 W 下的全局 Moran's I = (zᵀWz)/(zᵀz)，z=y−ȳ（仅去均值）。

    依据 REFERENCE_MAP §13.5 精确式。W 已行标准化时 S0=n，n/S0=1，故无需再乘归一化因子。
    y 长度必须与 W 维度一致，否则响亮失败。zᵀz=0（常数场）时 I 无定义 → StatsError。
    """
    y = np.asarray(y, dtype=float).ravel()
    W = np.asarray(W, dtype=float)
    if W.ndim != 2 or W.shape[0] != W.shape[1]:
        raise StatsError(f"W 不是方阵: {W.shape}")
    if y.shape[0] != W.shape[0]:
        raise StatsError(f"y 长度 {y.shape[0]} 与 W 维度 {W.shape[0]} 不符（剔除未同步？）")
    z = y - y.mean()
    denom = float(z @ z)
    if denom == 0.0:
        raise StatsError("zᵀz=0（常数场），Moran's I 无定义")
    return float((z @ (W @ z)) / denom)


def moran_permutation(
    y: np.ndarray, W: np.ndarray, n_perm: int = 9999, seed: int = 0
) -> dict[str, float]:
    """Moran's I 条件置换检验（不折叠单尾 greater）。

    依据 REFERENCE_MAP §11.4/§13.5：置换 P 次、p=(#{sim≥I}+1)/(P+1)（9999 次 → p 地板
    1e-4）；EI=−1/(n−1) 解析期望（非零，n=40 时≈−0.026）。参考分布由整体置换 y 生成。

    返回 {"I", "EI", "p_sim", "z_sim"}：z_sim 用置换分布的均值/标准差标准化观测 I。
    """
    y = np.asarray(y, dtype=float).ravel()
    n = y.shape[0]
    if n < 3:
        raise StatsError(f"样本量 {n} 过小，置换检验无意义")
    if n_perm < 1:
        raise StatsError(f"n_perm={n_perm} 非法")
    I = morans_i(y, W)
    EI = -1.0 / (n - 1)
    rng = np.random.default_rng(seed)
    sims = np.empty(n_perm, dtype=float)
    for k in range(n_perm):
        sims[k] = morans_i(rng.permutation(y), W)
    ge = int(np.sum(sims >= I))
    p_sim = (ge + 1) / (n_perm + 1)
    sd = float(sims.std())
    z_sim = float((I - sims.mean()) / sd) if sd > 0 else 0.0
    return {"I": float(I), "EI": float(EI), "p_sim": float(p_sim), "z_sim": z_sim}


# ==================================================================== median polish

def median_polish(
    grid: np.ndarray, max_iter: int = 10, eps: float = 0.01
) -> dict[str, Any]:
    """Tukey median polish（NaN 容忍、L1 收敛准则）。

    依据 REFERENCE_MAP §11.4："na.rm、eps=0.01、maxiter=10"；用于剥离行/列梯度与边缘效应，
    Moran 检查在其残差上跑（先去趋势，§13.5）。中位数一律用 nanmedian，收敛看残差 L1 范数
    （nansum|residual|）的相对变化 ≤ eps。

    返回 {"overall", "row", "col", "residuals"}：
      grid ≈ overall + row[:,None] + col[None,:] + residuals。
    """
    R = np.asarray(grid, dtype=float).copy()
    if R.ndim != 2:
        raise StatsError(f"median_polish 需要 2D 网格，得到 {R.ndim}D")
    nr, nc = R.shape
    overall = 0.0
    row = np.zeros(nr)
    col = np.zeros(nc)
    prev_l1 = np.inf
    for _ in range(max_iter):
        # 行扫：取每行中位数并回收进 row，再把 row 的中位数并入 overall
        rmed = np.nanmedian(R, axis=1)
        rmed = np.where(np.isnan(rmed), 0.0, rmed)
        R -= rmed[:, None]
        row += rmed
        rdelta = float(np.nanmedian(row))
        row -= rdelta
        overall += rdelta
        # 列扫：对称处理
        cmed = np.nanmedian(R, axis=0)
        cmed = np.where(np.isnan(cmed), 0.0, cmed)
        R -= cmed[None, :]
        col += cmed
        cdelta = float(np.nanmedian(col))
        col -= cdelta
        overall += cdelta
        l1 = float(np.nansum(np.abs(R)))
        if prev_l1 < np.inf and abs(prev_l1 - l1) <= eps * (abs(prev_l1) + 1e-12):
            break
        prev_l1 = l1
    return {"overall": float(overall), "row": row, "col": col, "residuals": R}


# ==================================================================== 稳健 z

def mad_z(x: np.ndarray) -> np.ndarray:
    """MAD 稳健 z 分数：0.6745·(x−median)/MAD，MAD=median|x−median|。

    依据 REFERENCE_MAP §11.4："median polish 残差的 MAD 稳健 z，|z|>3.5" 判孤立离群。
    0.6745 使 MAD 在正态下无偏估计 σ。MAD=0（半数以上取值相同、无离散度）时**响亮失败**
    ——此时"离群"无稳健标度可言，静默返回会掩盖退化输入（房规：响亮失败优先）。NaN 透传。
    """
    x = np.asarray(x, dtype=float)
    med = np.nanmedian(x)
    mad = np.nanmedian(np.abs(x - med))
    if mad == 0.0:
        raise StatsError("MAD=0（无稳健离散度），无法计算稳健 z——输入退化")
    return 0.6745 * (x - med) / mad


# ==================================================================== SBB 校准

def sbb_suspicion(p: float) -> float:
    """Sellke-Bayarri-Berger 校准：α(p)=[1+(−e·p·ln p)⁻¹]⁻¹。

    依据 REFERENCE_MAP §11.4：把检查 p 值映射为校准嫌疑分。−e·p·ln p 是零假设后验概率下界
    的贝叶斯因子项，在 p=1/e 处取极大 1（α=0.5，此即"基线"上限），向两侧衰减：p→0⁺ 与 p→1
    时 α→0。故 p>1/e 落入弱证据尾区，仍按公式（自然衰减到基线 0），不另设常数夹断——否则
    p→1 无法趋 0（本模块判断取舍，见 §11.4 "p>1/e→截到基线"与 p→1 趋 0 两约束的调和）。

    校验 p∈(0,1]，越界响亮失败。样例：p=0.05→α≈0.289。
    """
    p = float(p)
    if not (0.0 < p <= 1.0):
        raise StatsError(f"p={p} 非法，SBB 校准要求 p∈(0,1]")
    if p == 1.0:
        return 0.0  # −e·p·ln p = 0 → 分母无穷 → α=0
    bf = -_E * p * np.log(p)  # >0（ln p<0）
    return float(1.0 / (1.0 + 1.0 / bf))


# ==================================================================== 配对置换检验

def _clean_finite(values) -> np.ndarray:
    """Drop None entries; fail loudly on non-finite values (nan/inf)."""
    arr = np.asarray([v for v in values if v is not None], dtype=float)
    if arr.size and not np.isfinite(arr).all():
        raise StatsError("置换检验输入含非有限值（nan/inf）")
    return arr


def paired_permutation_test(
    diffs, n_permutations: int = 9999, seed: int = 0
) -> dict[str, Any]:
    """Paired sign-flip permutation test (two-sided).

    Relocated VERBATIM from the eval package's ``stats_tests`` for M17 K-B: the in-loop
    round aggregator (qc layer) must reuse this exact machinery, but the eval
    package is the post-hoc leaf no expos module may import (tests/test_eval.py
    red line), so the shared primitive now lives here and
    eval's ``stats_tests`` re-exports it unchanged. Logic, defaults and
    output keys are bit-identical to the original (protocol docs/M9_PROTOCOL.md
    §3 / §7 H1).

    ``diffs`` = per-pair differences (e.g. same-seed regret_A - regret_B; None
    entries are dropped). Null hypothesis = the two arms are exchangeable, so
    each pair's difference flips sign with probability 1/2. Test statistic =
    mean paired difference. p = (1 + #{|mean(perm)| >= |mean(obs)|}) /
    (n_permutations + 1) (+1 smoothing: a Monte Carlo p is never 0). All-zero
    diffs -> p = 1.0 (no signal).

    Returns {"p_value", "observed_mean_diff", "n", "n_permutations"}.
    """
    d = _clean_finite(diffs)
    if d.size == 0:
        raise StatsError("置换检验需要至少 1 对配对差（输入全为 None 或为空）")
    observed = float(np.mean(d))
    out = {
        "observed_mean_diff": observed,
        "n": int(d.size),
        "n_permutations": int(n_permutations),
    }
    if np.allclose(d, 0.0):
        out["p_value"] = 1.0  # all-zero diffs: every flip yields 0 -> two-sided p = 1
        return out
    rng = np.random.default_rng(seed)
    signs = rng.choice(np.array([-1.0, 1.0]), size=(int(n_permutations), d.size))
    perm_means = (signs * d).mean(axis=1)
    # float tolerance: permutations with |perm| numerically equal to |obs| must
    # count (conservative)
    n_ge = int(np.sum(np.abs(perm_means) >= abs(observed) - 1e-12))
    out["p_value"] = float((1 + n_ge) / (n_permutations + 1))
    return out


# ==================================================================== Cohen's d

def cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    """Cohen's d（池化标准差）：(mean(a)−mean(b))/s_pool。

    依据 REFERENCE_MAP §11.4：n=2–3 副本不做单轮显著性，改报效应量 d（0.2/0.5/0.8 档）。
    s_pool=√[((nₐ−1)sₐ²+(n_b−1)s_b²)/(nₐ+n_b−2)]（ddof=1）。任一样本 <2 或池化标准差为 0
    （两组皆常数）时**响亮失败**。
    """
    a = np.asarray(a, dtype=float).ravel()
    b = np.asarray(b, dtype=float).ravel()
    na, nb = a.shape[0], b.shape[0]
    if na < 2 or nb < 2:
        raise StatsError(f"Cohen's d 需每组样本 ≥2，得到 nₐ={na}, n_b={nb}")
    sp2 = ((na - 1) * a.var(ddof=1) + (nb - 1) * b.var(ddof=1)) / (na + nb - 2)
    if sp2 <= 0.0:
        raise StatsError("池化方差为 0（两组皆常数），Cohen's d 无定义")
    return float((a.mean() - b.mean()) / np.sqrt(sp2))


# ==================================================================== EWMA / CUSUM

def ewma(x: np.ndarray, lam: float = 0.2) -> np.ndarray:
    """指数加权移动平均：z_t=λ·x_t+(1−λ)·z_{t−1}，z_0=x_0。

    依据 REFERENCE_MAP §11.4：批次位移/时间漂移用 EWMA(λ=0.2, L=3) 挂哨。λ∈(0,1]。
    """
    x = np.asarray(x, dtype=float).ravel()
    if not (0.0 < lam <= 1.0):
        raise StatsError(f"λ={lam} 非法，需 ∈(0,1]")
    if x.shape[0] == 0:
        raise StatsError("空序列，无法计算 EWMA")
    z = np.empty_like(x)
    z[0] = x[0]
    for t in range(1, x.shape[0]):
        z[t] = lam * x[t] + (1.0 - lam) * z[t - 1]
    return z


def cusum(
    x: np.ndarray,
    k: float = 0.5,
    h: float = 5.0,
    target: float | None = None,
    sd: float | None = None,
) -> dict[str, Any]:
    """标准 tabular CUSUM（双侧）。

    依据 REFERENCE_MAP §11.4：批次位移/时间漂移用 CUSUM(k=0.5, h=5)。标准化 z=(x−target)/sd
    （target 缺省=均值、sd 缺省=样本标准差，即 self-starting 的简化）；
      C⁺_t=max(0, C⁺_{t−1}+z_t−k)，C⁻_t=max(0, C⁻_{t−1}−z_t−k)；
    任一累积超过 h 即告警。k、h 均以标准化单位计。

    返回 {"pos", "neg", "alarm_idx"}：pos/neg 为累积序列，alarm_idx 为所有越限下标（升序）。
    """
    x = np.asarray(x, dtype=float).ravel()
    n = x.shape[0]
    if n == 0:
        raise StatsError("空序列，无法计算 CUSUM")
    mu = float(np.mean(x)) if target is None else float(target)
    s = float(np.std(x, ddof=1)) if sd is None else float(sd)
    if s <= 0.0:
        raise StatsError("标准差为 0（常数序列），CUSUM 无法标准化——请传入 sd")
    z = (x - mu) / s
    pos = np.zeros(n)
    neg = np.zeros(n)
    alarm_idx: list[int] = []
    cp = cn = 0.0
    for t in range(n):
        cp = max(0.0, cp + z[t] - k)
        cn = max(0.0, cn - z[t] - k)
        pos[t] = cp
        neg[t] = cn
        if cp > h or cn > h:
            alarm_idx.append(t)
    return {"pos": pos, "neg": neg, "alarm_idx": alarm_idx}


# ==================================================================== 反驳器（DoWhy 式）

def refute_placebo(
    statistic_fn: Callable[[np.ndarray], float],
    labels: np.ndarray,
    n: int = 999,
    seed: int = 0,
) -> dict[str, Any]:
    """Placebo 反驳器：打乱标签重估，判"效应塌零"。

    依据 REFERENCE_MAP §13.5：placebo=洗 treatment 列 ×n（默认 999），检验"0 是否落在 placebo
    分布内"。PASS ⇔ p_zero>0.05 **且** |placebo 均值|<0.1·|观测|（效应随标签打乱而塌到零）。

    statistic_fn 是闭包（内部持数据、只吃 labels）。p 值走**经验分位**（不用正态近似，§13.5）：
    p_zero=(#{|sim−μ_sim|≥|0−μ_sim|}+1)/(n+1)——0 越接近 placebo 分布中心，越多样本比它更偏离，
    p_zero 越大 → 越支持"塌零"。真实效应打乱后 placebo≈0（PASS）；无效应假信号时 |观测|≈0，
    相对阈值 0.1|观测| 近乎 0，placebo 均值难以低于它 → FAIL。

    返回 {"observed", "placebo_mean", "p_zero", "passed"}。
    """
    labels = np.asarray(labels)
    if n < 1:
        raise StatsError(f"n={n} 非法")
    observed = float(statistic_fn(labels))
    rng = np.random.default_rng(seed)
    sims = np.array([float(statistic_fn(rng.permutation(labels))) for _ in range(n)])
    placebo_mean = float(sims.mean())
    farther = int(np.sum(np.abs(sims - placebo_mean) >= abs(0.0 - placebo_mean)))
    p_zero = (farther + 1) / (n + 1)
    passed = bool(p_zero > 0.05 and abs(placebo_mean) < 0.1 * abs(observed))
    return {
        "observed": observed,
        "placebo_mean": placebo_mean,
        "p_zero": float(p_zero),
        "passed": passed,
    }


def refute_subsample(
    statistic_fn: Callable[[np.ndarray], float],
    data: np.ndarray,
    frac: float = 0.8,
    n: int = 100,
    seed: int = 0,
) -> dict[str, Any]:
    """Subsample 反驳器：抽 frac 子样重估，判"效应稳定"。

    依据 REFERENCE_MAP §13.5：subset_fraction=0.8、n=100。**判据与 placebo 方向相反**——
    PASS ⇔ p_in>0.05 **且** std<0.5·|观测|（效应在子样下不散架）。

    statistic_fn 吃数据子集（按 axis 0 抽行，不放回）。p 值走经验分位：
    p_in=(#{|sim−μ_sim|≥|观测−μ_sim|}+1)/(n+1)——观测越靠近子样分布中心，p_in 越大 → 越稳。

    返回 {"observed", "subset_mean", "subset_std", "p_in", "passed"}。
    """
    data = np.asarray(data)
    if not (0.0 < frac <= 1.0):
        raise StatsError(f"frac={frac} 非法，需 ∈(0,1]")
    if n < 1:
        raise StatsError(f"n={n} 非法")
    N = data.shape[0]
    m = max(1, int(round(frac * N)))
    if m < 1 or m > N:
        raise StatsError(f"子样容量 {m} 非法（N={N}）")
    observed = float(statistic_fn(data))
    rng = np.random.default_rng(seed)
    sims = np.empty(n, dtype=float)
    for i in range(n):
        idx = rng.choice(N, size=m, replace=False)
        sims[i] = float(statistic_fn(data[idx]))
    subset_mean = float(sims.mean())
    subset_std = float(sims.std())
    farther = int(np.sum(np.abs(sims - subset_mean) >= abs(observed - subset_mean)))
    p_in = (farther + 1) / (n + 1)
    passed = bool(p_in > 0.05 and subset_std < 0.5 * abs(observed))
    return {
        "observed": observed,
        "subset_mean": subset_mean,
        "subset_std": subset_std,
        "p_in": float(p_in),
        "passed": passed,
    }
