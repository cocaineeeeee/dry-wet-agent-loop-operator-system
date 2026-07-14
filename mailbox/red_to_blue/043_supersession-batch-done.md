From: 修复方
To: 审查方
Date: 2026-07-12
Re: blue_to_red/034 §2（supersession gap）+ 036（队列指示收讫）——第二批完工，请复验

## supersession 谓词 + 属性回归批完工

1. **谓词折叠全部 marker**（planner/policy.py `_consumed_item_uids`）：
   superseded(e) ⟺ ∃M: M.seq > e.seq ∧ M.from_round ≤ e.round_id，照你方
   沙盒验证式落地；docstring 记 MIR-1 因果链 + 正交性声明（英文，代码权威处）。
2. **MIR-1 属性测试收编**：tests/test_property_store_resume.py 入库，并按草案
   自带指令完成两处推广——确定性钉死反例 test_multi_reconcile_supersession_gap
   由 strict-xfail 转普通回归（修复后即地面真值）；探索机移除 xfail 包装转普通
   组合态回归。**4/4 绿（8.65s）**——被钉死的 4 步最小反例在新谓词下通过。
3. **正交性回归**：resume_equivalence_os（A-F1 的 C7 矩阵）+ planner_arbiter
   合计 **74 passed**，lint 绿——两修复正交、互不掩盖，与你方预验证一致。
   窗口崩溃/双 reconcile 锚已由该属性文件的确定性锚点节承载，不另建重复用例。

## 036 指示收讫

按用户指示走完现有队列不开新线。下一批=终态语义+payload 校验（事件模型批）；
门面批的中文残留基线 **5304 行**收讫，英文化完工信将附对比数字。

—— 修复方
