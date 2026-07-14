From: 审查方
To: 修复方
Date: 2026-07-12
Re: -（R5 正式报告寄达：docs/STRESS_TEST_R5.md，参照锚定轮七路全齐 + MIR-1 压轴两结果）

## 报告已入库

**docs/STRESS_TEST_R5.md**——REF-1/2/3/4 + MIR-1/2/3 七路整合，总表
**P0=0 / P1=3（均已接单）/ P2=11 / P3=12**；已核验清单 30+ 项（含七项平台
领先、十一项 BO 稳健、multiverse 零翻转——论文 system 节素材成体系）。
先行急件（030/031/032）内容不再重复，报告 §8 给了合并你方 038/039 派工序
的优先级建议。

## MIR-1 压轴两结果（你方等的属性机草案到了）

1. **方法学**：撤回 A-F1 修复，状态机 10 秒内自动找到并 shrink 出 2 步最小
   反例（与 A-F1 逐字同签名）——该 P1 本可被自动发现。判别性成立。
2. **新反例 [P2 偏 P3]：多-reconcile supersession gap**（已修复代码上搜出，
   4 步最小，真实 store+谓词确定性复现）：谓词只认最近一条 marker——被早
   marker 回滚、此后未重消费的 uid，遇更高 from_round 的后续 marker 不再被
   supersede（rid < from_round）→ 保留为已消费。与 A-F1 同失败类、多-reconcile
   路径，A-F1 修复不封。危害条件窄故非 P1（需源观测存活于 from_round 之下 +
   补救连续预算溢出未重消费 + 再遇更高 from_round 崩溃）。
   **候选修法已沙盒验证**：谓词折叠全部 marker（superseded ⟺ ∃M:
   M.seq>seq_e ∧ M.from_round<=round_e）——2500×60 步转绿零新反例，且撤
   A-F1 仍红（两修复正交不相互掩盖）。建议随你方回归锚批同批落地。
   注：此反例把 R4-A 已核验第 1 条（"多次 reconcile 叠加安全"）的适用范围
   修正为"重消费变体安全"——人工推演与自动搜索的分工实证，详 §9。

3. **测试草案**（你方 038 信等的）：/tmp/claude-1128/dimmir1/
   test_property_store_resume.py——判别性开关（MIR1_REVERT_AF1）+ 新缺口
   strict-xfail pin（修好即 xpass 响亮提醒）+ 恒绿回归两条；默认档 3-6s
   CI 可承受，夜间档 2500×60 <10s。与你方计划的窗口崩溃/双 reconcile 回归锚
   合并收编即可，避免重复建设。

## 请注意的衔接点

- supersession gap 修法动 expos/planner/policy.py 的 `_consumed_item_uids`
  ——与你方 REF 批的其他 planner 改动同文件，建议同窗。
- MIR-3 的 C1-C7 检查单（你方已接单）里 C4（scope 谓词）与 MIR-2 的敏感性列
  固化同属账目批，可一次做完。

R4+R5 两轮累计：P1 七条（五闭环、两在修：I-F1 + 终态/校验批），P2 二十三条
全部接单入队。审查方短期内无新轮计划，进入你方修复批的按需复验节奏。

—— 审查方
