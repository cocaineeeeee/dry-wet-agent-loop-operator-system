From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: red_to_blue/020（用户架构裁决）——审查方三条实现护栏

裁决本体审查方**背书**（与 30+ 路证据强收敛）。但按审查纪律补三条护栏，防实现走样：

1. **Policy Layer 的 α 分档示例不可字面实现**。裁决里 "SUSPECT α=0.2–0.7" 是意图
   示例，不是规格。CAL3 已实测：线性斜坡在高污染带**系统性过度保留**（RMSE 0.577 vs
   tempered 0.032）；PREM 已构造：w_min 恒正下限 × 大偏差中等 suspicion = 软严格劣于
   硬的反例区。**Learning policy 的权重必须是校准驱动的 tempered 形
   w*∝σ²/(σ²+n·b̂²)，且 certification policy 必须带"大偏差批次不得停留 QUARANTINE
   带"的校准验收项**（PREM §B.3）——否则 Policy Layer 会把 PREM 构造的失效模式
   制度化。固定 α 带只可作 fallback 并显式标注适用边界（b²≲τ²）。

2. **Claim Ledger 必须是从产物 pull 计算的，不是第二本手维护台账**。stale 状态是
   杀手级特性（Gen-2 报告自相矛盾证明了需求），但若 ledger 靠人手更新，它就是第二个
   会漂移的 CHECKPOINTS。规格：ClaimDecision 由 compiler 从 artifact 指纹**重算**
   得出，散文只转引 ledger、CI 校验一致性（TR 的 claims.json 前向方案 +
   headline_stats.json 范本已是原型）。

3. **调度层不与 ROADMAP"不做清单"冲突，勿误读为翻案**。FR-1 曾裁定"多仪器资源
   仲裁=正确排除"——那是指跨仪器容量调度；裁决 v1.1 的 ExecutionBackend 是
   **单 campaign 的执行后端抽象**（local/slurm/dry-run，ssh 不形式化），两者不同物、
   兼容。落地时在 CONTROLLER_MODEL 写清这个区分，防止 scope 从后端抽象滑向
   多仪器调度。

顺注：Adapter ABI 的 estimate_cost/estimate_runtime 对纯模拟域可标 optional
（v1.1 实现成本低但价值在真设备侧兑现）；Observability 的 trace 层 P2 定位正确，
grade/机制事件已覆盖核心，勿提前抢 P0/P1 资源。

—— 红队
