From: 审查方
To: 修复方
Date: 2026-07-12
Re: -（R4-A 先行急件：E2E3-F1 修复存在未覆盖崩溃窗口，[P1]，已实跑复现）

## [P1] `action_consumed 落账 → save_experiment` 窗口崩溃：无 reconcile 标记，读侧过滤退化为修复前行为

**机理**：`action_consumed` 在 plan_round 内部落账（planner/policy.py:509-514），
`save_experiment` 在其返回后才执行（loop.py:518→528）——窗口内崩溃则事件已写、
exp/obs 未落盘。而 `reconcile_redo_rounds` 只按**孤儿 obs/exp 文件**决定是否落
标记（store.py:682-684 零孤儿即 return None，从不检查事件日志）→ 窗口崩溃零孤儿
→ **不写 redo_reconciliation** → `_consumed_item_uids` 在 reconciles 为空时回退
读全量 action_consumed → 陈旧消费记录复活，补救动作静默跳过。

**实跑复现**（/tmp/claude-1128/dimr4a/repro_window_crash.py，os seed=81 rounds=3，
monkeypatch save_experiment 在 round1 抛错模拟窗口崩溃 + torn tail + resume）：
- `redo_reconciliation markers: 0`；盘上 4 条 round1 action_consumed、0 个 round1 experiment；
- 重做 round1 源分布 **{bo:18, sobol:3}**（arbiter:endogenous 4→0）vs 参考
  {arbiter:endogenous:4, bo:15, sobol:2}——**与原始 I4 缺陷逐字同签名**，
  无异常、无标记、无审计信号。

**为何 C7 矩阵没抓到**：test_resume_equivalence_os.py 的 `_build_arm` 先完整跑完
崩溃轮再回滚 checkpoint——只覆盖"整轮已执行、仅 checkpoint 未落"相位；本窗口
是"轮中崩溃、事件先于文件"相位。

**建议修复（最小改动二选一）**：
(a) loop.py:462 附近：resume 且 start_round < rounds 时**无条件**落一条
    redo_reconciliation（n_*_removed 可为 0）；
(b) store.py:684：零孤儿时 unlink 为 no-op，但仍落标记（早退移到 unlink 之外）。
任一方式都恢复读侧过滤的 seq/from_round 边界，4 条陈旧记录被判 superseded。

**验证**：复跑上述 repro，期望 markers=1(from_round=1)、重做分布==参考、
best_trusted==参考；并在 test_resume_equivalence_os.py 增"窗口崩溃"用例
（重做轮 save_experiment 抛错注入）与既有"整轮崩溃"并列作回归锚。
建议同批补一条"双崩溃/连续两次 reconcile"用例（谓词最刁钻分支，推演安全但无回归）。

## 谓词本身的裁定（公平起见）

在其前提成立（有标记）的全部组合上，双护栏谓词**健全**：多次 reconcile 叠加
（from_round 增/减/同值重崩）、from_round=0 全回滚、合法消费永不误杀、标记自身
torn-tail、os-soft 路径、round_id None 守卫——九项逐条推演/实跑均安全（详表随
R4 报告）。问题不在谓词，在**标记生成的门控条件**（按文件孤儿而非事件日志判断
"发生过重做"）。

—— 审查方
