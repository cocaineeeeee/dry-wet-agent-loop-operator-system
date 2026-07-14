"""预算管理（docs/ARCHITECTURE.md §5）：一切孔位消耗都要先申请，超支必须响亮失败。

规划器的所有动作（新候选/复测/重复/加哨兵）都经 BudgetManager 记账；
Budget 对象本身是 ExperimentObject 的字段，本类是它的记账包装。
"""

from __future__ import annotations

from expos.kernel.objects import Budget, LayoutAssignment


from expos.errors import ExposError


class BudgetError(ExposError):
    pass


class BudgetManager:
    def __init__(self, budget: Budget):
        self._b = budget.model_copy(deep=True)

    @property
    def budget(self) -> Budget:
        """当前记账状态（副本语义：外部拿到的是最新值对象）。"""
        return self._b.model_copy(deep=True)

    @property
    def remaining_wells(self) -> int:
        return self._b.wells_total - self._b.wells_used

    @property
    def remaining_rounds(self) -> int:
        return self._b.rounds_total - self._b.rounds_used

    def can_afford(self, wells: int) -> bool:
        return 0 <= wells <= self.remaining_wells

    def spend_wells(self, wells: int, what: str = "") -> None:
        if wells < 0:
            raise BudgetError("消耗孔数不能为负")
        if not self.can_afford(wells):
            raise BudgetError(
                f"预算超支: 申请 {wells} 孔（{what or '未注明'}），剩余 {self.remaining_wells}"
            )
        self._b.wells_used += wells

    def charge_layout(self, layout: LayoutAssignment) -> None:
        """整板记账：布局中每个已分配孔计 1。"""
        self.spend_wells(len(layout.wells), what="layout assignment")

    def start_round(self) -> int:
        if self.remaining_rounds <= 0:
            raise BudgetError(
                f"轮次预算耗尽: 已用 {self._b.rounds_used}/{self._b.rounds_total}"
            )
        self._b.rounds_used += 1
        return self._b.rounds_used
