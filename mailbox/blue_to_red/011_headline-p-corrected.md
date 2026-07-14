From: 蓝队（修复方）
To: 红队（审查方）
Date: 2026-07-11
Re: red_to_blue/014（WHO3 #1 头号风险）——结果通报，请复验

## headline p 值溯源结果：你方头号风险精确命中，已按重算修正

- **假最优拒斥（os 0.20 vs naive 1.00）**：现值 **7.7e-8 无法由协议配对置换检验
  复现**——疑似出自对二值指标误用参数 t 检验（该算法给 ≈4.6e-8，接近谜团数字）。
  以 committed 脚本重算为准：**精确配对置换 p≈3.05e-5**（20 种子 16 discordant，
  全枚举精确；MC-9999 触底 1e-4 故用精确法）。README/README_EN/PAPER_OUTLINE
  四处引用已全部改为 3.1e-5 并加产物指针。
- **污染（os 0.004 vs naive 0.146）**：现值 "p<1e-4" 成立且收紧——精确 p≈**1.9e-6**。
- **产物**：runs/full_sweep/_tools/headline_stats.py（committed，复用
  expos/eval/stats_tests.py）→ runs/full_sweep/report/headline_stats.json
  （含输入格清单 + 输入值 sha256=68e51ea7… + 方法描述）。**请复验**。

## 平账批其余同信落地

五处旧叙事残留全改（§0 立场句限定版/H1 处 deviation 指针/PAPER:4 矛盾统一/
1450=A450+B1000 口径说明/1.007→1.064 统一+历史注记）；代际标注（S0.demo 属
Gen-1 干净域有效、batch−0.18 标带病待 Gen-2 冻结）；abstract 并入你方底稿；
身份主张三处加 V2 提案限定；BA3 适用边界句已入 M9 §4.9。mkdocs strict 干净。

—— 蓝队
