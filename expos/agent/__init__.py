"""Agent Orchestrator 层（M8 实现）—— 建议权，无裁决权。

结构性边界（公理 7，docs/ARCHITECTURE.md §10）：
- 本包只接受 kernel/store.py 导出的 ReadOnlyRunView，永不持有 RunStore 写句柄；
- 一切产出为 DecisionRecord **返回值**，由 loop.py 调内核统一落盘；
- 本包不得定义/导出任何 save/append/write/delete/update 型 API（守门测试强制）。

M1 阶段本包为空占位，仅供守门测试锚定边界。
"""

__all__: list[str] = []
