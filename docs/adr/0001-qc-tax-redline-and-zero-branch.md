# ADR 0001：QC 税红线 ≤5% 与裁决策略零分支

- **Status**: Accepted (2026-07-10)
- **Context**: 三臂对比的公平性依赖"两臂仅差策略对象"；过度隔离好数据的系统会在含伪影基准上虚假获胜（Deming tampering，REFERENCE_MAP §15）。
- **Decision**: ① 零伪影场景假阳性 SUSPECT 率 ≤5% 为 M5 硬验收线；② 裁决/聚合/规划抽为三策略注入点，`_policies_for_mode` 是 loop 唯一 mode 判定点，主体零分支（测试断言强制）。
- **Consequences**: M5 实测闭环级 0%、板级 2.5%；对比公平性主张可在方法节一句话陈述；新增模式（robust/compare）只加策略对象不碰主体。
