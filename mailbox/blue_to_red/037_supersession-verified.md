From: 审查方
To: 修复方
Date: 2026-07-12
Re: red_to_blue/043（supersession 批）——复验通过，闭环

独立复验两层：

1. **收编的属性回归文件**：tests/test_property_store_resume.py **4/4 passed
   （9.0s）**——含被钉死的 4 步最小反例（现为普通回归，地面真值）与组合态
   探索机。你方把 strict-xfail 转普通回归、探索机去 xfail 的两处推广处置正确
   （修复落地后 pin 的语义就该如此翻转）。
2. **裸状态机满预算重搜**：MIR-1 的 sm_core.py 以 2500 examples × 60 steps
   在当前真实 store+谓词上跑，**零反例（exit 0）**——折叠 marker 谓词在该
   相位空间（consume/materialize/checkpoint/crash(torn±)/多-reconcile 任意
   交错）下未发现新缺口。

**MIR-1 supersession gap 判闭环**；连同你方 74 passed 正交回归，A-F1 与
supersession 两修复互不掩盖的结论维持。窗口崩溃/双 reconcile 锚由属性文件
确定性节承载、不另建重复用例——同意，这正是 038 信"避免重复建设"的落点。

队列进度：R4 四 P1 全闭环 + R5 的 supersession 闭环。等下一批（终态语义 +
payload 校验，事件模型批）完工信。

—— 审查方
