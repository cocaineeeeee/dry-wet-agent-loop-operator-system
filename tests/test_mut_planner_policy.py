"""变异语料击杀：planner/policy.py 风险图粗分层（MU 第一波 Q2 存活变异）。

Q2 [P1]：_plate_risk_map 把乐观伪影率 p 按 _RISK_TIER=0.25 粗分层（round 到最近层），
是 R1-2b 的抗饿死护栏——连续风险值令 LayoutPlanner._take 的区组均衡项永无平手、贪心抽干
低风险区组致跨区组无解。去分层（→ 原始 p）后护栏被删而无测试报警。钉：输出全为 _RISK_TIER
的整数倍。用 duck-typed 假失败模型直测该纯函数（不依赖训练/端到端）。
"""

from types import SimpleNamespace

from expos.planner.policy import _RISK_TIER, _plate_risk_map


class _FakeFM:
    """返回非分层对齐的连续伪影率——分层生效则 round 到 0.25 的整数倍。"""
    def p_artifact_optimistic(self, is_edge, block, solution_batch, round_id):
        return 0.37 if is_edge else 0.11  # 均非 0.25 的整数倍


def test_plate_risk_map_is_tiered_to_risk_tier_multiples():
    cfg = SimpleNamespace(plate=SimpleNamespace(rows=6, cols=8))
    rm = _plate_risk_map(cfg, _FakeFM(), round_id=0)
    assert rm, "风险图不应为空"
    for well, v in rm.items():
        q = v / _RISK_TIER
        assert abs(q - round(q)) < 1e-9, f"{well}={v} 非 _RISK_TIER({_RISK_TIER}) 整数倍（分层被去除）"
    # 分层保序：0.37→0.25、0.11→0.0，仍保留 >1 个不同层
    assert len(set(rm.values())) > 1
