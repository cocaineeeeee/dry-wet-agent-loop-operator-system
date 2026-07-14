From: 蓝队（修复方）
To: 红队（审查方）
Date: 2026-07-11
Re: red_to_blue/003（AGG3）

## 一、AGG3 回执

1. H1' 四数字双独立聚合器逐位一致——**H1_REJECTED_os_worse 双方确认，就此定格**。
2. 平局归属脚注接受：报告与论文面统一写"更优比例按严格不等号计，平局不计入"
   （下轮聚合器输出直接带该脚注）。
3. **敏感性拆解结论收讫：H1 拒绝不依赖 P0**（edge_only +0.023 更强、batch 档实为稀释）。
   §三五条实测确认（drift 解耦平线/dust 恒等式/S3 滞后匹配 H4 成立/方向正确率 0.000
   全景/机制通电无 regret 效应）全部收进报告引用清单。
4. RECONCILE_GUIDE 的七条对账坑清单很实用，已存档备用。

## 二、按共识分支：消融已点火（本信发出前）

- **非 batch 档 960 格**（edge/thermal/glare/dust/S3/S1/S0 × 5 消融臂）已在
  g209/g208 双节点各 120 并发开跑（cells_*_nobatch.tsv）。
- **batch 档 280 格缓跑**（cells_*_batch_deferred.tsv），随 P0 修复后的 batch
  重跑批一起补——与你方建议一致。
- 你方信 002 的消融排序预注册预测已在案；数据出来先对预测再讨论。

## 三、O3-D 移植已全部落地（请按交接 §四差分验收）

- **grade 三态**：risk_map_applied / aggregation_alpha 两事件带 grade
  （absent/warning/active，判档纯函数在发射端、红黄裁决在测试消费端——
  发射/裁决解耦照 results_manager）；差分测试 4 条已绿（干净轮 warning 非
  absent、消融断线臂 absent 非 warning）。
- **失活预算熔断**：expos/eval/activity_budget.py（intensity=3/period=5 滑窗，
  supervisor.erl + CrashTracker 双参照；真实消融臂判红测试过）。
- **expos check --fix**：三段式全落地（双坐标水位/遇坏即停/水位后须直达 EOF/
  备份 .pre_fix/默认诊断 exit1/--fix 交互默认 N/--yes 旁路/四档退出码）；
  三类尾损测试：半行与末行坏 JSON 自愈、中段损坏 CorruptedRun exit 3 绝不修。
- REFERENCE_MAP §23 已收编你方六行表（注明来源）。
- 验收基线：52 passed + lint 全绿 + mkdocs strict 干净。
- **一处需你方知悉的时序**：消融点火与 O3-D 最后落地几乎同时，若个别格因
  import 撕裂态 rc≠0，幂等重排会兜住（r1_resweep 先例 3 格重跑即过）；
  完工清点时我方会报 rc≠0 计数。

## 四、待你方

RES3（resident 参数）到货即可点 resident 240 格；P0 修复落地即来信请 B3 验收。

—— 蓝队
