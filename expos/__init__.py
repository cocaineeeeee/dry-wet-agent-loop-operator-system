"""expos —— 闭环物理材料实验操作层（experiment OS）。

内核只有两个持久科学对象（ExperimentObject / ObservationObject）+ 追加式事件日志
+ 轮次状态机 + 信任路由 + 检查点；其余一切（设计、执行、QC、归因、规划、agent）
都是可替换模块。权威蓝图见 docs/ARCHITECTURE.md。
"""

__version__ = "0.1.0"
