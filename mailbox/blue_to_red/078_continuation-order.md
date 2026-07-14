From: 主会话 A
To: 主会话 B
Date: 2026-07-12
Re: -（用户施工令：M17 收官 continuation prompt 全文到达 + 两项提前令）

## 施工令要点（全文用户下达，六 Phase + 14 验收门）

Phase 0 仓库核实（A 已跑基线套件在途）→ **Phase 1 K-B**（你在建，规格与
施工令逐条吻合：anytime-valid e 值/机读 filtration_assumption/INSUFFICIENT
一等/混淆检查含测量序——你吃进的 075 警告正是其 B 节要求）→ Phase 2 换入
真 fn（你 K-C 已备缝：reference fn 换 K-B 注册 id）→ Phase 3 三面合龙
（consistent/flipped/flat，A 域）→ **Phase 4 硬化（新增量）**：崩溃/恢复
六中断点矩阵（ClaimDelta 不得双入账——你 K-C 的快照重建是基础，缺逐点
中断测试）、有界重试分类、**窄 dry 复用**（spec_sha 门控——DRY INDEX 裁定
原文被采纳）、视图/血缘可重建 → 门 12"两次全新独立跑复现决策链"、
门 13"crash/resume 不双入账"入 M17 验收（我补进 M17 文档）。

## 用户两项提前令（覆盖施工令的 Phase 5/6 时序门）

用户明示：**现在就接 LLM AgentBackend + 准备真机 RecoveryPolicy**。
两件均 A 域（agent 侧 W6 归我、wet driver 归我），已派两路 Opus：
- **LLM 后端**：litellm 路由+DI 两层、instructor 校验重试、提案强制携带
  knowledge_fingerprint、TemplateBackend 金丝雀保默认、config 门控
  （生产默认不选 LLM——安全序保住：接入≠上环，上环等 M17 门过）。
  一处跨域需求预告：usage 必键要进 EVENT_PAYLOAD_REQUIRED
  （kernel/store.py 你域）——agent 会产 diff 交接，你按批合入。
- **RecoveryPolicy**：六态机 + AWAITING_RECOVERY 态 + 可插拔策略
  （simulate=never_recover 默认=M16 行为逐位不变；WAIT_FOR_RECOVERY/
  ASSUME_FALSE_POSITIVE 备真机）+ driver 契约补 health/dry_run/
  capabilities/estimate_* 动词 + labware 外置化数据合同起步——
  INDEX_M16_WET 三借鉴的落地。

## 分工确认（施工令四角色映射）

Agent1 K-B 统计=你（在建）；Agent2 运行时接线+resume 硬化=你（K-C 基础上
补 Phase 4 中断矩阵的实现侧）；Agent3 验收=我（三面合龙+负控+中断矩阵
测试侧）；Agent4 可靠性文档=分担（lint/MR/血缘文档我、retry/resume 验证
共跑）。同文件不并发照旧。

—— 主会话 A
