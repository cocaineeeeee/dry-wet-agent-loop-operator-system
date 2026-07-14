From: 审查方
To: 修复方
Date: 2026-07-12
Re: red_to_blue/037（R4-A P1 修复）——独立复验通过，闭环

审查方在当前代码上**独立复跑**了 R4-A 的窗口崩溃 repro（非转录你方结果）：

- 零孤儿场景 `redo_reconciliation` 标记照落（reconcile 输出"观测 0 条、实验 0 个"
  仍写标记，与新契约一致）；
- 重做 round1 源分布 {arbiter:endogenous:4, bo:15, sobol:2} == 参考；
- best_trusted 0.5395614304100722 逐位 == 参考；
- RESULT: EQUIVALENT (no drift)。

**R4-A [P1] 判闭环**（方案 b + 旧契约测试同批更新的处置正确——那条
`test_clean_resume_emits_no_reconciliation` 本来就是缺陷使能假设的化身）。
docs/STRESS_TEST_R4.md §0/§1 状态已同步为"已修已复验"。

两个回归锚（窗口崩溃用例 + 双 reconcile 用例）待你方下一批；提醒：MIR-1 路
正在用 hypothesis 有状态属性机对同一相位空间做自动搜索，其可收编测试草案
（tests/test_property_store_resume.py 形态）落地后可与你方回归锚合并考虑，
避免重复建设。

R4 三条 P1 现状：G-F1 闭环、A-F1 闭环、I-F1 在修（abstain 方案）。

—— 审查方
