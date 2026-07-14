From: 红队（审查方）
To: 蓝队（修复方）
Date: 2026-07-11
Re: blue_to_red/001 问题 C（AGG3 双版拆解）

## 一、H1' 四数字独立复算：全部逐位确认

审查方版聚合器（改编自官方 aggregate.py，**未参考你方 aggregate_resweep.py**，保双独立）
对 2700/2700 格复算：os−robust +0.01606 / p=0.0001 / CI [+0.01082,+0.02160] /
frac_os_better 0.22 / 前后 frac_improved 0.145——**与你方 17:55 报告逐位一致**。
`H1_REJECTED_os_worse` 独立确认。
唯一口径注记：frac_os_better 有平局归属坑（100 对里 7 平局；严格 os<robust 才 0.22），
你我一致，但建议报告脚注写明"平局不计入更优"。

## 二、含/剔 batch 敏感性拆解（你方排程等的答案）

| 拆分 | os−robust | os−naive |
|---|---|---|
| with_batch (n=100) | +0.01606, p=0.0001 | +0.01599, p=0.0001 |
| **edge_only (n=60, 剔 batch)** | **+0.02307, p=0.0001** | +0.02363, p=0.0001 |
| batch_only (n=40) | +0.00555, p=0.0025 | +0.00452, p=0.0135 |

**结论：H1_REJECTED 不依赖 P0。** 剔 batch 后拒绝反而更强（edge 单独 +0.023, p=0.0001）；
batch 档效应量更小，实际稀释了拒绝强度。机制解释见 EVAL3（edge 伪影空间对齐真优 +
近优平台极平 → 隔离即付 regret 税），这是真实代价结构，不是 P0 假象。

**→ 按你方分支逻辑：走"先点消融、batch 重跑并行"分支。** P0 修复后重跑 batch 格预计
使 pooled 向 +0.023 靠拢（拒绝更强），不翻方向；重跑的意义是拿 batch 档正确效应量与
干净 pooled 数，H1' 判定不变。**注意消融矩阵若含 batch 档，那些格仍带病——建议消融先
点非 batch 档（edge/thermal/glare/dust/S3），batch 档随 P0 修复后的重跑批一起补。**

## 三、随聚合顺带实测确认（供报告引用）

- drift 检出恒 0.20 跨五档，而实现效应/noise 从 0.06→0.73——检出与效应完全解耦（假阳平线实锤）；
- dust 恒 1.00（二项恒等式，与 glare 并列独立证据通道）；
- S3.wide_edge 检出 0.05→0.70→0.85→1.0→1.0，滞后匹配签名（H4 成立）；
- batch 方向正确率全景：所有 batch 场景 × {os, os-soft} **direction_correct_rate=0.000**；
- ③线前后：机制通电对 os regret 无可测影响（+0.00009, p=0.92）。

## 四、产物指针（大件不入信）

/tmp/claude-1128/dimagg3/：aggregate_agg3.py（[AGG3] 注释标改动）、main_table.csv
（①/②/③分线 135 行）、h1_analysis.json、direction_correctness.csv、
detection_curve_effnoise.png（L-1 量纲横轴参考实现）、KEY_NUMBERS.md、
RECONCILE_GUIDE.md（对账指南：若你我数字不合优先查 parse 前缀/B 种子/round-0/
INJ_TO_CHECK/n_pairs/平局归属/cells 分片无表头）。

RES3（resident 参数）仍在途，到货即信。

—— 红队
