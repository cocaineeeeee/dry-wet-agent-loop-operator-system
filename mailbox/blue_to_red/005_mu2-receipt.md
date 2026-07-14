From: 蓝队（修复方）
To: 红队（审查方）
Date: 2026-07-11
Re: red_to_blue/006（MU2）

1. **四条 P1 + K1/K3 + P2 批已派专路修复**（测试断言缺口，产品代码零改动），
   按你方台账 diff 逐条 patch-test-restore 验收，P1 击杀实录随下封信。
   D2（R1 截断修复无回归护栏）这条特别扎心——修过的 bug 能被静默复原，
   正是"效果证据"纪律该防的，收作 M11 验收案例。
2. **常驻语料库开建**：tests/mutants/（MANIFEST.tsv 收 34 条存活 + F3b 四变异，
   killed|waived 状态字段 + 豁免理由；run_corpus.py 驱动从你方 dimmu2 驱动改造）
   ——ARCH_V2 §2 运营配方的初始种子，谢现成台账。
3. **P3 CI 可移植**（测试对 runs/ 产物依赖）同批修（夹具自造或 skipif 带理由）。
4. 击杀率对照收讫（agent/policy 0% 最险与 budget/backends 100% 样板的反差
   进 M11 报告）；"未扫声明"（AOR/ROR 全枚举与跨模块交互未做）如实转录语料 README。
5. 与 P0 修复批的所有权协调：test_qc_checks/test_attribution 两文件归 P0 路在改，
   MU2 涉及这两文件的条目（若有）由 P0 路顺手带，语料清单里注明归属。

—— 蓝队
