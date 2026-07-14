"""变异语料击杀：design/sampler.py 最小距离去重（MU2 S1）。

S1 [P2]：_dedupe_min_dist 保留与已选点距离 ≥ min_dist 的点。谓词 `>= min_dist` 被架空成
`>= -min_dist`（距离恒 ≥ 0 ≥ −min_dist，永真）后近重复点全部被接纳。旧路径断言在自然
Sobol 池上距离本就够开，去重从不触发故漏检——直接对含近重复的构造池钉去重语义。
"""

import numpy as np

from expos.design.sampler import _dedupe_min_dist


def test_dedupe_drops_near_duplicates():
    """构造含近重复的池：点1 距点0 仅 0.01 ≪ min_dist=0.5，必须被丢弃。
    谓词架空成永真后 point1 被接纳（chosen=[0,1]）→ 断言必红。"""
    pool = np.array([[0.0, 0.0], [0.01, 0.0], [1.0, 1.0]])
    chosen = _dedupe_min_dist(pool, order=np.array([0, 1, 2]), n=2, min_dist=0.5)
    assert chosen == [0, 2], f"近重复点未被去重: {chosen}"
    # 入选点两两间距 ≥ min_dist
    a, b = pool[chosen[0]], pool[chosen[1]]
    assert np.linalg.norm(a - b) >= 0.5
