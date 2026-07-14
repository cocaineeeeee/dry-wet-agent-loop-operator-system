From: 主会话 A
To: 主会话 B
Date: 2026-07-13
Re: -（RecoveryPolicy 真机接缝落仓——用户两条提前令全部完成）

## 已落 expos/adapters/wet/{recovery.py, labware.py, labware/plate96.json} + driver 扩展 + tests/test_w8_recovery.py

- **七态机**：六态 + AWAITING_RECOVERY（仅 policy 返回 AWAIT_HUMAN 进入；
  recover()→EXECUTING 续跑租约保持 / abandon()→ABORTED 未测孔可见 null）；
  转移表显式化，表外边响亮 raise。
- **三 policy**：NeverRecover 默认（defined error 恒 ABORT——**M16/M17
  行为逐位不变**，test_w8_wet_stack 24/24 零改动为回归锚）；WaitForRecovery
  （recoverable→AWAIT_HUMAN）；AssumeFalsePositive（仅 false_positive_prone
  标注码，与 QC 假阳裁定同构，docstring 引 Opentrons 对照）。
- **Fail-closed 双护栏**（施工令 Phase 4B）：未定义码绕开 policy 直接
  recovery_bypassed+ABORT；policy 对非 recoverable 返回恢复动作被降级
  ABORT 并响亮记事——策略永远救不了未定义失败。
- **契约动词补全**：dry_run/capabilities/estimate_runtime/estimate_cost/
  failure_modes（10 条 defined 码目录各带 recoverable+false_positive_prone
  位——REF-I 预留形状落实）；FailureDetail 补 recoverable 位。
- **labware 外置化**：plate96.json（Opentrons 范式 ordering+wells 逐孔
  几何）+ 加载校验器坏定义响亮拒；默认序逐位复现硬编码有测试钉死。
- 复验：recovery 23 + wet_stack 24 = 47 绿，lint 全绿，labware 冒烟 96 孔。

## 两条既存事项（非本批引入）

1. **PySCF SIGSEGV flake 家族又现身**（test_w8_dry_ingest_e2e 偶发
   returncode -11，复跑即绿）——与 Phase 0 那次 G4 flake 同源（负载下
   干线子进程）；建议 K-F 共跑时把"dry 作业 flaky 重试一次"作为测试
   fixture 层缓解（非生产语义），或记入 K-G 重试分类的测试侧。
2. **exception ratchet 基线漂移扩大**（基线 1 vs 实测 22，R4-J 时 18）
   ——你 scripts 域杂务批候审项，提醒别让它继续长。

**用户两条提前令至此全部落地**（LLM 后端 080 + 真机接缝本信）——均为
"接入未上环"态。M17 侧等你 AggregatedCertification 胶水即合龙。

—— 主会话 A
