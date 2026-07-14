"""变异语料击杀：qc/stats.py 稳健统计的标定常数（MU2 T1/T3）。

- T1 [P2]：mad_z 的 0.6745 正态一致性常数（使 MAD 无偏估计 σ）。去掉（→1.0）后稳健 z
  整体缩放错位。已知答案钉常数：这是统计定义常数，非可调门限，钉之不僵化标定。
- T3 [P2]：EWMA 凸加权 z_t = λ·x_t + (1−λ)·z_{t−1}。λ 与 1−λ 对调后权重装反。已知答案钉。
"""

import numpy as np
import pytest

from expos.qc.stats import ewma, mad_z


def test_mad_z_uses_0_6745_consistency_constant():
    """已知答案：x=[0,1,2,3,4] → med=2, MAD=1 → z = 0.6745·(x−2)。
    丢弃 0.6745（→1.0）后整体缩放变化 → 断言必红。"""
    x = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    z = mad_z(x)
    expected = 0.6745 * np.array([-2.0, -1.0, 0.0, 1.0, 2.0])
    assert np.allclose(z, expected)
    assert z[4] == pytest.approx(0.6745 * 2.0)  # 显式钉常数


def test_ewma_convex_weights_lambda_on_current():
    """已知答案：x=[0,10,10], λ=0.2 → z=[0, 2.0, 3.6]（λ 加在当前值）。
    λ↔(1−λ) 对调 → z[1]=8.0 → 断言必红。"""
    z = ewma(np.array([0.0, 10.0, 10.0]), lam=0.2)
    assert z[0] == pytest.approx(0.0)
    assert z[1] == pytest.approx(2.0)   # 0.2·10 + 0.8·0
    assert z[2] == pytest.approx(3.6)   # 0.2·10 + 0.8·2.0
