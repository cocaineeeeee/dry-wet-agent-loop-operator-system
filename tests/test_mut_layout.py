"""变异语料击杀：design/layout.py 默认放置路径的风险主键与边界（MU2 Y1/Y4/Y5）。

- Y1 [P1]：默认放置路径（无 hint 的对照/溢出哨兵，走 _take 默认键）把 w.risk 作主键。
  变异把主键 risk→0 后，默认码路对 risk_map 视而不见（balance_first 强制路径另有 Y2
  护栏，但默认路径本体零测试）。钉：默认路径分配孔与高风险孔 isdisjoint。
- Y4 [P2]：跨区组强制边界 enforce = k <= n_blocks。改成 k < n_blocks 后，k==n_blocks
  时退回非强制路径，副本可落同一区组。钉：k==n_blocks 时 4 副本跨 4 区组。
- Y5 [P2]：容量预检 needed > capacity。改成 needed > capacity+1 后，needed==capacity+1
  绕过响亮预检、坠入分配期不同错误。钉：容量预检的专属报文。
"""

import pytest

from expos.design.layout import LayoutError, LayoutPlanner, well_id_of
from expos.kernel.objects import Candidate, Control


def _block_of(r: int, c: int, rows: int, cols: int) -> str:
    return f"Q{(2 if r >= rows / 2 else 0) + (1 if c >= cols / 2 else 0)}"


def test_default_path_consumes_risk_map(tmp_path):
    """默认放置路径（非哨兵对照，_take 默认键）避让高风险孔。
    留 10 个 risk=0 孔、其余 38 孔 risk=1，投 10 个 negative 对照——正确码必落
    在 10 个低风险孔上（与高风险孔 isdisjoint）；risk→0 变异令对照坠入高风险孔。"""
    rows, cols = 6, 8
    low_risk = {"F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8"}  # 末行 8 个
    low_risk |= {"E7", "E8"}  # 共 10 个低风险孔
    all_wells = {well_id_of(r, c) for r in range(rows) for c in range(cols)}
    risky = all_wells - low_risk
    risk_map = {w: 1.0 for w in risky}  # 低风险孔取默认 0.0

    controls = [Control(kind="negative") for _ in range(10)]
    planner = LayoutPlanner(rows, cols, seed=17)
    layout = planner.assign([], controls, risk_map=risk_map)
    used = {w.well_id for w in layout.wells if w.control_id is not None}
    assert len(used) == 10
    assert used.isdisjoint(risky), f"默认路径占用了高风险孔: {sorted(used & risky)}"
    assert used <= low_risk  # 恰好落在低风险孔


def test_cross_block_enforced_at_k_equals_n_blocks(tmp_path):
    """k == 区组数(4) 边界：4 个副本必须跨 4 个不同区组。
    用 risk_map 把 Q0 之外全部标高风险——正确码在强制路径下仍跨 4 区组；
    enforce 边界收紧成 k<n_blocks 后退回非强制路径，会把 4 副本全塞进低风险的 Q0。"""
    rows, cols = 6, 8
    q0 = {well_id_of(r, c) for r in range(rows) for c in range(cols)
          if _block_of(r, c, rows, cols) == "Q0"}
    all_wells = {well_id_of(r, c) for r in range(rows) for c in range(cols)}
    risk_map = {w: 1.0 for w in (all_wells - q0)}

    layout = LayoutPlanner(rows, cols, seed=3).assign(
        [Candidate(cand_id="c0", params={})], [], n_replicates=4, risk_map=risk_map
    )
    blocks = [w.block_id for w in layout.wells if w.cand_id == "c0"]
    assert len(blocks) == 4
    assert len(set(blocks)) == 4, f"副本未跨区组（区组={blocks}）"


def test_capacity_precheck_fires_at_capacity_plus_one():
    """needed == capacity+1：容量预检必须响亮抛"超过板容量"（先于任何分配）。
    off-by-one 变异 (needed > capacity+1) 会绕过预检，坠入分配期的其它错误报文。"""
    planner = LayoutPlanner(rows=2, cols=3, seed=1)  # 容量 6
    cands = [Candidate(cand_id=f"c{i}", params={}) for i in range(7)]  # 7 = 6+1
    with pytest.raises(LayoutError, match="超过板容量"):
        planner.assign(cands, [], n_replicates=1)
