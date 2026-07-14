"""预算守恒 stateful 属性测试（族5 报告建议·台2）。

expos/design/budget.py 的 BudgetManager 记账语义（docs/ARCHITECTURE.md §5：
一切孔位消耗先申请、超支响亮失败）在随机穿插下的守恒性质，并复刻 loop.py 的
charge 语义（每轮 start_round → charge_layout → 回填 exp.budget）与断点续跑的
Budget round-trip（loop 用 Budget(**ckpt["budget"]) 重建）。

已核实 budget.py 语义：spend_wells 在 not can_afford / wells<0 时抛 BudgetError；
start_round 在 remaining_rounds<=0 时抛 BudgetError。invariant ④ 直接测此既有守卫，
未发明新语义。

rules（3 条）：
  · start_round        —— 复刻 loop 每轮起手；轮次耗尽必 BudgetError，否则 rounds_used+1；
  · charge_layout      —— 随机小布局整板记账；超预算/负数必响亮 BudgetError 且账不变；
  · checkpoint_roundtrip—— Budget 快照（model_dump）→ 重建 BudgetManager，续跑等价替换。

invariants（4 条）：
  ① 已花 ≤ 总预算（wells_used、rounds_used 均在界内且非负）；
  ② round 计数与成功 start_round 次数一致（rounds_used == 影子计数）；
  ③ 快照-恢复后逐字段相等：Budget(**bm.budget.model_dump()) == bm.budget（resume 等价的预算面）；
  ④ 超预算守卫的边界自洽：can_afford(remaining) 真、can_afford(remaining+1) 假（响亮抛的前置）。
"""

from __future__ import annotations

import importlib.util

import pytest

if importlib.util.find_spec("hypothesis") is None:  # dev extra 未装时整模块优雅跳过（照 test_ui_smoke find_spec 范本）
    pytest.skip("hypothesis 未安装（pip install -e '.[dev]'）", allow_module_level=True)

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from expos.design.budget import BudgetError, BudgetManager
from expos.kernel.objects import Budget, LayoutAssignment, WellAssignment

WELLS_TOTAL = 24
ROUNDS_TOTAL = 5


def _layout(n: int) -> LayoutAssignment:
    """n 个已分配孔的最小合法布局（每孔 cand_id 占位，满足 WellAssignment 的 XOR 校验）。"""
    wells = [
        WellAssignment(well_id=f"W{i}", row=0, col=i, cand_id="c")
        for i in range(n)
    ]
    return LayoutAssignment(rows=1, cols=max(n, 1), seed=0, wells=wells)


class BudgetStateMachine(RuleBasedStateMachine):
    @initialize()
    def _seed(self) -> None:
        self.bm = BudgetManager(Budget(wells_total=WELLS_TOTAL, rounds_total=ROUNDS_TOTAL))
        # 影子账本：独立累加成功的花费/轮次，与记账器交叉验证。
        self.wells_used = 0
        self.rounds_used = 0

    # ------------------------------------------------------------ start_round
    @rule()
    def start_round(self) -> None:
        if self.bm.remaining_rounds <= 0:
            with pytest.raises(BudgetError):
                self.bm.start_round()
            assert self.bm.budget.rounds_used == self.rounds_used  # 抛后不动账
        else:
            n = self.bm.start_round()
            self.rounds_used += 1
            assert n == self.rounds_used
            assert self.bm.budget.rounds_used == self.rounds_used

    # ------------------------------------------------------------ charge_layout
    @rule(n=st.integers(min_value=0, max_value=WELLS_TOTAL + 4))
    def charge_layout(self, n: int) -> None:
        layout = _layout(n)
        if self.bm.can_afford(n):
            self.bm.charge_layout(layout)
            self.wells_used += n
            assert self.bm.budget.wells_used == self.wells_used
        else:
            # 超预算：响亮 BudgetError，账面分毫不动（既有守卫，非新语义）。
            before = self.bm.budget.wells_used
            with pytest.raises(BudgetError):
                self.bm.charge_layout(layout)
            assert self.bm.budget.wells_used == before == self.wells_used

    # ------------------------------------------------------------ 负数消耗守卫
    @rule(neg=st.integers(min_value=-6, max_value=-1))
    def charge_negative(self, neg: int) -> None:
        before = self.bm.budget.wells_used
        with pytest.raises(BudgetError):
            self.bm.spend_wells(neg, what="negative")
        assert self.bm.budget.wells_used == before == self.wells_used

    # ------------------------------------------------------------ checkpoint round-trip
    @rule()
    def checkpoint_roundtrip(self) -> None:
        """复刻 loop：write_checkpoint 存 bm.budget.model_dump()，resume 时
        Budget(**ckpt["budget"]) 重建。续跑等价 = 重建的记账器逐字段相等。"""
        snap = self.bm.budget.model_dump()
        resumed = BudgetManager(Budget(**snap))
        assert resumed.budget == self.bm.budget
        assert resumed.budget.model_dump() == snap
        assert resumed.remaining_wells == self.bm.remaining_wells
        assert resumed.remaining_rounds == self.bm.remaining_rounds
        # 用等价副本替换，证明"从快照续跑"后续步骤与原地推进无差别。
        self.bm = resumed

    # ------------------------------------------------------------ 不变量
    @invariant()
    def inv_within_budget(self) -> None:
        """① 已花 ≤ 总预算，且均非负。"""
        if not hasattr(self, "bm"):
            return
        b = self.bm.budget
        assert 0 <= b.wells_used <= b.wells_total
        assert 0 <= b.rounds_used <= b.rounds_total

    @invariant()
    def inv_round_count(self) -> None:
        """② round 计数与成功 start_round 次数一致；孔位账与影子一致。"""
        if not hasattr(self, "bm"):
            return
        assert self.bm.budget.rounds_used == self.rounds_used
        assert self.bm.budget.wells_used == self.wells_used

    @invariant()
    def inv_snapshot_roundtrip(self) -> None:
        """③ 快照-恢复逐字段相等（resume 等价的预算面），任意步后都成立。"""
        if not hasattr(self, "bm"):
            return
        rebuilt = Budget(**self.bm.budget.model_dump())
        assert rebuilt == self.bm.budget
        assert rebuilt.model_dump() == self.bm.budget.model_dump()

    @invariant()
    def inv_guard_boundary(self) -> None:
        """④ 超预算守卫边界自洽：恰好花光可负担、再多一孔不可负担（响亮抛的前置）。"""
        if not hasattr(self, "bm"):
            return
        rem = self.bm.remaining_wells
        assert rem >= 0
        assert self.bm.can_afford(rem) is True
        assert self.bm.can_afford(rem + 1) is False


BudgetStateMachine.TestCase.settings = settings(
    max_examples=40,
    stateful_step_count=25,
    deadline=None,
)

TestBudgetStateMachine = BudgetStateMachine.TestCase
