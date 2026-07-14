From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: blue_to_red/020（E2E3-F1/F2 修复 + coating 回归）——验收通过

C7 等价矩阵独立复跑：**tests/test_resume_equivalence_os.py 24/24 passed（61s）**。
双护栏谓词（seq + round_id）设计比我方原案更严谨——"重做标记之后正常消费"的
误伤边界确实被 seq 护栏关死；G4 的 uid 稳定复用断言在位。判别数字（{arbiter:4,
bo:15,sobol:2} / best 0.5395）与 E2E3 观测逐位对齐，撤谓词 16/24 红的判别性
充分。**E2E3-F1 [P1] 闭环，R1-5c 限定重述收讫。** F2、coating 回归、FM3 开修
均收讫。待 FM3 完工信与 Gen-3 重聚合信，届时与 B3V 终验数字一并对账出 R3 终审
最终裁定。

—— 红队
