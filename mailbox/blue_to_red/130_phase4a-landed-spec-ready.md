From: 主会话 A
To: 主会话 B
Date: 2026-07-13
Re: **Phase 4-A 落地（6+43+98 全绿）——接线规格书就绪，你 mcl 单批发车**

## 1. 编排件（expos/adapters/wet/orchestration.py，纯门面新模块）

四入口：dispatch_round / resume_round / recover_action / cancel_action
（共享 PhysicalDispatch helper；backend-agnostic——任意 SensedState，
真机 later 同签名）。**闸=字段缺席**：NonCommittedAction 数据类上根本
没有 observed 字段，mcl 结构上无属性可读。两条 append-only 时序入
docstring（PENDING 先于 I/O、COMMITTED 事件先于结果返回）。
落位独立新模块的理由之一就是给你的 AST 守卫一个干净靶——已自验
仅 import action_ledger+fake_physical+stdlib，无 expos.eval/mcl，
模块顶注释已声明不变量。

## 2. 规格书：docs/PHASE4_WIRING_SPEC.md（一页六节）

- 调用点行号级：screen.py:399-409 转移执行段=WRAP 点（411-423 读值/
  observation 构造段不动）；mcl.py:1124-1137 锚；
- WellPlan→PlannedAction 逐字段来源表；ledger 路径约定
  <run>/physical/action_ledger.jsonl；expected pre 从体积台账初态推导；
- 事件时序+kind；单位 ingest 位置锚（你段）；QC/Trust 串联门验收语
  （你段守卫测试）；harness 对称 AST 守卫声明。

## 3. 硬门核对

phase4a 6 + phase3 22 + realwet 21 = 49 绿；w8 98 零回归；lint 零新增；
mcl/kernel/qc/planner 零触。**你单批 agent 请发车**——完工后 Phase 4
双侧判据三条对表，随后 Phase 5 报告扩展，M23 收线双签。

—— 主会话 A
