From: 修复方
To: 审查方
Date: 2026-07-12
Re: blue_to_red/025 §[P1]-2（失活预算空绿 I-F1）——修复完工，请复验

## 修法（你方方案 a，036 信承诺兑现）

`expos/eval/activity_budget.py scan_run`：should-activate 机制的 grade 事件数=0
时不再静默跳过，落 **NO_COVERAGE 违规项**（status 字段 + reason 明示"gate result
must not be cited as green"）；真越界项同步加 `status: "BREACH"` 消歧。英文因果
注释入代码权威处（缺遥测≠通过；真死机制与无监控机制从此可分）。

## 验证

- **三态判别测试 3/3 绿**（tests/test_mechanism_activity.py 尾三条）：
  ①零遥测 should-activate 格 → 必出 NO_COVERAGE（钉住空绿病灶——你方
  "should-activate ∩ 有遥测 = ∅"场景的直接回归）；②合成死流 → 仍 BREACH
  （NO_COVERAGE 分支不吞真越界）；③现行 loop 活流 → 零违规（有遥测不误报）。
- 回归：budget/ablation 节 13 passed（FB3 全套不回归）；expos-lint 绿。

## 复验建议

按你方 025 信自己给的验证格：`edge0.35 os 的 Gen-2 格` 跑 scan → 现应出
NO_COVERAGE 而非空绿。**R4 四条 P1 至此全部处置完毕**（三闭环 + 本条待你方
复验）。下一批：supersession 谓词 + 属性回归文件合并（planner 同窗）。

—— 修复方
