"""执行 adapter 层（M3 实现）：模拟器 + 伪影注入 + 台面 worklist。

公理 6：仿真真值 sidecar 只能由本层的 sim_* 生成；qc/models/planner/agent 禁读。
"""
