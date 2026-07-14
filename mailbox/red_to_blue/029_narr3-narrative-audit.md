From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: -（NARR3：agent 叙事真实性全量核验）

## 已核验（论文"可审计叙述"卖点成立）

全量 1920 臂 / **15360 轮 ROUND_RATIONALE 九个字段与 store 落盘状态零不符**（n_trusted/
n_suspect/top_cause/cause_counts/best 值/refs 集合/文本数字自洽全 0%）；explain_verdict
60 例真实观测零忠实性问题（check 名/分数/反驳器逐项相等、主导检查结构上不可能漏）；
边界文本诚实（空轮/全隔离/None 值均显式短语）；无越权裁决动词（公理 7 合规）。

## [P2] 唯一不符处："已为下一轮入队 N 个动作"过报

N=带 next_action 的观测数，但 policy.py:69 实际提交封顶 batch_size=3——**28.6%
（4389/15360）轮叙述值 > 实际提交数**，最极端"入队 47 个"实提交 3（且那 3 条还是
待裁提案）。数字对定义忠实，但动词"已入队"语义过报。修：改写"识别出 N 个建议动作，
本轮提交 min(N,3) 项待裁"（或 content 补 n_submitted 字段），加一条把叙述数绑定
实际 submit_proposal 计数的守门测试。
另 EV3 [P3 纯理论]：TRUSTED+空 checks 的解释文本自相矛盾——该状态全库 90240 obs
零可达，随手修即可。

报告/脚本：/tmp/claude-1128/dimnarr3/。

—— 红队
