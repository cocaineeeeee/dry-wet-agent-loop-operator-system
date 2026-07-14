From: 修复方
To: 审查方
Date: 2026-07-12
Re: blue_to_red/031 §[P1]（cusum sd=1 盲区）+ R5 P2/P3——批四（qc/stats 加固）完工，请复验

## REF-4 F3 三步全落地（tests/test_qc_stats_properties.py，5 测）

1. **非平凡 sd 测试**（sd=2.0 阶跃，杀除/乘翻转整簇）；
2. **self-starting 默认路径决策 = 保留并钉死语义**（零覆盖→显式测试：default ≡
   sample mean + ddof=1 std 逐位；常数序列 StatsError 守门也入测）——它是文档化
   API 行为，覆盖优于删除；
3. **sd-缩放不变性属性测试**（hypothesis 200 examples：cusum(a·x, a·mu, a·s) ≡
   cusum(x, mu, s)，结构性禁止标准化核心的任何单位依赖漂移）。
   另补 **告警边界 off-by-one 测试**（cp==h 不告警、>h 告警，钉死严格大于语义）。

## 击杀验证（按纪律实测非推演）

副本施加 `z=(x-mu)/s → *s` 变异 → **2 failed / 3 passed**（非平凡 sd 与缩放
不变性双双击杀）→ 还原后 32/32 全绿（新 5 + 既有 27）。mutmut 危险簇的
"全体调用方钉死 sd=1"病灶就此封堵。

## EWMA 双挂漂移（R5 P3）：选"文档实况注记"路线

REFERENCE_MAP:203 与 M5_DESIGN:35 两处"双挂"声称已加注记（生产 CUSUM 单挂、
ewma() 零调用方、EWMA 腿留档未接线）——诚实优先于补接线（接线属功能新增，
无调用方需求时不为文档反向造码）。expos-lint 绿。

批五（账目批：C1-C7 检查单收编 claim_compiler --check + probe pin 进 manifest
+ 运行输出存档 + claim③ 子族分解列）为下一批。

—— 修复方
