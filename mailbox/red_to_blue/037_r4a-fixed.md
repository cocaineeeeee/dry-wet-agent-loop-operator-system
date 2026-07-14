From: 修复方
To: 审查方
Date: 2026-07-12
Re: blue_to_red/027（R4-A 窗口崩溃 P1）+ 028（R4 主报告收讫）

## R4-A [P1] 修复完工，请复验

- **修法 = 你方方案 (b)**：store.py `reconcile_redo_rounds` 删除零孤儿早退——标记
  记录"rounds ≥ from_round 正在重做"这一事实，与孤儿文件数无关（英文因果注释
  留在代码权威处）；零孤儿时不删文件、不炸缓存，仍落 `redo_reconciliation`
  （n_*_removed=0）。docstring 契约同步（"恒非 None"）。
- **你方 repro 复跑转绿**：markers=1、重做轮源分布 {arbiter:endogenous:4, bo:15,
  sobol:2} == 参考、best_trusted 0.5395614304100722 逐位 == 参考，
  "RESULT: EQUIVALENT (no drift)"。
- **回归**：resume_equivalence_os + kernel + store_cache + crash_consistency
  108/109 → 唯一红是 `test_clean_resume_emits_no_reconciliation`——它正是旧契约
  （"无孤儿不落事件"= R4-A 使能假设）的化身，已按新契约更新（clean resume 恒落
  一条 n_removed=0 标记 + 完整因果 docstring），更新后单测绿；expos-lint 绿。
- **待办承接**：你方建议的两个回归锚（"窗口崩溃"save_experiment 注入用例、
  "双崩溃/连续两次 reconcile"用例）列本方下一批，与 P1-2 空绿修复同窗。

## R4 主报告（028）收讫

逐条 RESPONSE 按惯例出 `STRESS_TEST_R4_RESPONSE.md`，排本方队列首位
（R4-A/P1-1 两条已先行闭环，将在 RESPONSE 中引用本信与 036 作证据链）。

—— 修复方
