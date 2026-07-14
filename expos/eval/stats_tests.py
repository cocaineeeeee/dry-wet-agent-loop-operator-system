"""预注册统计检验（docs/M9_PROTOCOL.md §3 末段 / §7 H1）——R1-3(d) 零实现修复。

协议原文（docs/M9_PROTOCOL.md L120-121）：
    "统计：N≥20 种子，报效应量 + bootstrap 95%CI + 跨臂**置换检验**（§11.4，
    小样本体制不做单轮显著性）。"
§7 H1（L221-222）要求"置换检验 p<0.05，跨 N 种子"。

实现三件套：

- ``paired_permutation_test``：两臂同种子配对差的**符号翻转置换检验**
  （同 base seed 跨臂同伪影实现 → 配对可比，§4.3；零假设=两臂可交换 →
  每对差符号随机翻转；默认 9999 次 Monte Carlo，双侧 p，含 +1 平滑）。
  M17 K-B 起实现本体迁至 ``expos.qc.stats``（轮内聚合器在 qc 层须复用同一
  机器，而 eval 是禁止入向 import 的事后叶子），此处原样 re-export——逻辑、
  默认参数、输出键逐位不变，调用面零变化。
- ``percentile_bootstrap_ci``：**种子级重采样** percentile bootstrap
  （默认 10000 次，95% CI）。选 percentile 而非 BCa：N=20 量级下 BCa 的
  加速常数估计不稳，percentile 更保守可复核。
- ``compare_arms_paired``：便捷封装——按共同种子配对两臂标量指标（如
  final_regret），输出效应量（配对差均值）+ p + CI + ``low_n`` 标记
  （n_pairs < 20 时置位；协议 §3 N≥20 下限）。

确定性：全部随机性走 ``numpy.random.default_rng(seed)``，同输入同输出。
依赖红线：expos.eval 是叶子（无入向 import）——本模块仅依赖 numpy +
expos.errors + expos.qc.stats（纯 numpy 统计原语库，无真值面），不触内核。
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from expos.errors import ExposError
from expos.qc.stats import paired_permutation_test

__all__ = [
    "LOW_N_THRESHOLD",
    "StatsError",
    "compare_arms_paired",
    "paired_permutation_test",
    "percentile_bootstrap_ci",
]

#: 协议 §3（L120）"N≥20 种子"——低于此的场景在输出里带 low_n 标记
LOW_N_THRESHOLD = 20


class StatsError(ExposError):
    """统计检验输入不合法（空样本等）——响亮失败，不静默给 NaN。"""

    user_facing = False


def _clean(values) -> np.ndarray:
    arr = np.asarray([v for v in values if v is not None], dtype=float)
    if arr.size and not np.isfinite(arr).all():
        raise StatsError("统计检验输入含非有限值（nan/inf）")
    return arr


# ``paired_permutation_test`` 本体见 expos/qc/stats.py（M17 K-B 迁移，见模块
# docstring）；上方 import + __all__ 完成原位 re-export。


def percentile_bootstrap_ci(
    values, n_boot: int = 10000, confidence: float = 0.95, seed: int = 0
) -> dict[str, Any]:
    """种子级重采样 percentile bootstrap CI（统计量=均值）。

    返回 {"mean", "ci_low", "ci_high", "n", "n_boot", "confidence"}。
    单点样本退化为点估计（ci_low=ci_high=该点）。
    """
    v = _clean(values)
    if v.size == 0:
        raise StatsError("bootstrap CI 需要至少 1 个样本（输入全为 None 或为空）")
    mean = float(np.mean(v))
    if v.size == 1:
        lo = hi = float(v[0])
    else:
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, v.size, size=(int(n_boot), v.size))
        boot_means = v[idx].mean(axis=1)
        alpha = (1.0 - confidence) / 2.0
        lo, hi = np.quantile(boot_means, [alpha, 1.0 - alpha])
    return {
        "mean": mean,
        "ci_low": float(lo),
        "ci_high": float(hi),
        "n": int(v.size),
        "n_boot": int(n_boot),
        "confidence": float(confidence),
    }


def compare_arms_paired(
    a_by_seed: Mapping[int, float | None],
    b_by_seed: Mapping[int, float | None],
    n_permutations: int = 9999,
    n_boot: int = 10000,
    seed: int = 0,
) -> dict[str, Any]:
    """两臂同种子配对比较（协议 §4.3：同 base 同伪影实现 → 配对可比）。

    输入 = 两臂 {seed: 指标值}；只用**共同且双方非 None** 的种子。
    输出 = 配对差（A−B）的均值/置换 p/95% bootstrap CI + ``low_n``
    （n_pairs < LOW_N_THRESHOLD=20，协议 §3 N≥20 下限）。
    """
    common = sorted(
        s
        for s in set(a_by_seed) & set(b_by_seed)
        if a_by_seed[s] is not None and b_by_seed[s] is not None
    )
    if not common:
        raise StatsError("两臂无共同非空种子，无法配对比较")
    diffs = [float(a_by_seed[s]) - float(b_by_seed[s]) for s in common]
    perm = paired_permutation_test(diffs, n_permutations=n_permutations, seed=seed)
    boot = percentile_bootstrap_ci(diffs, n_boot=n_boot, seed=seed)
    return {
        "n_pairs": len(common),
        "seeds": common,
        "mean_diff": perm["observed_mean_diff"],
        "p_value": perm["p_value"],
        "ci95_low": boot["ci_low"],
        "ci95_high": boot["ci_high"],
        "n_permutations": perm["n_permutations"],
        "n_boot": boot["n_boot"],
        "low_n": len(common) < LOW_N_THRESHOLD,
    }
