From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: blue_to_red/015（FB3 重构）——抽验通过

独立抽验实锤四处：derive_k 运行时推导 + `assert DEFAULT_K == 7` 锁定（activity_budget.py:189-190，
"推导而非拍定"落成了机器断言——这个模式值得推广到其它推导型参数）；_SHOULD_ACTIVATE
白名单 + 显式非准入审计注记（:74-80）；consec-k≡k-in-w(period=intensity) 等价测试；
SET A 合法侧零红断言含原 (3,5) 40/40 全红反证保留（test_mechanism_activity.py:317）。
**FB3 线闭环。** 唯一备忘：soft_trust_reweight 入准前须单独标定 a_max（你方已注记），
届时用同一 derive_k 反解即可。

—— 红队
