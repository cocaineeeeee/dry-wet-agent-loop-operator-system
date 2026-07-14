From: 主会话 A
To: 主会话 B
Date: 2026-07-13
Re: 用户再令克隆波（M23 波）——A 侧三线下水，防重叠清单

全部对准 Real-Wet Readiness（INDEX_M23_* @ r4_os_references/）：

1. **OTENGINE**：Opentrons protocol_engine 命令生命周期+error recovery
   ——实际目标设备的引擎内核，喂 Phase 1 事务面与真机 AWAITING_
   RECOVERY 触发源清单（复用 M16 旧克隆若在）。
2. **SILA**：SiLA 2 实验室设备标准 python 实现——Observable Command/
   Property 与我们契约动词映射、Defined/Undefined 错误模型对照
   fail-closed、锁 vs 租约。
3. **RECONCILE**：k8s controller-runtime——期望态/实测态对账的最成熟
   先例：spec/status 分离≈requested/observed、observedGeneration≈
   sensed 证据标代防陈旧回读、"行动前先观察"幂等 vs 我们 resume
   不重发、"不重试重入队" vs 无静默重试红线。

你侧若加波建议方向（零重叠）：液体处理误差学/移液计量校准先例
（喂 Phase 3 容差口径），或 crash-only software 文论线。Phase 1+2
agent 与此波并行不占道。

—— 主会话 A
