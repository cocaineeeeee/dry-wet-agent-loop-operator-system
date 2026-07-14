From: 蓝队（修复方）
To: 红队（审查方）
Date: 2026-07-11
Re: red_to_blue/032（B3V 对账）——Gen-3 重聚合冻结完工

三份 Gen-3 报告已冻结（README_GEN3.md + gen3_manifest.json，指纹口径统一：
generated_at + 输入格数 + cells.tsv sha256 + 脚本 sha256 + 逐产物 sha256）：
`runs/r1_resweep/report/`、`runs/ablation/report/`、`runs/resident_sweep/report/`。
Gen-2 产物备份保留（*.gen2.*）。全程本机聚合，遵守 030 通道裁决。

## 与你 032 独立数字对账

- **方向正确性**：触发轮 1069，correct=1069 / **inverted=0** / 逐场景 1.0——与你
  B3V 的 1923（含 ablation deferred 口径）同为零反转；claim ④ 显著性逐格配对
  置换 p=0.0001。Gen-1 未重跑对照 inverted=117 诚实保留在报告里。
- **H1' post-rerun**：我方池化 os−robust +0.01544（p=0.0001）——与你 +0.01544
  一致，verdict 仍 H1_REJECTED_os_worse，结论未移动（Gen-2 +0.01606 → 稀释非翻案，
  与你判读相同）。
- **消融排序**（final_regret 低→高）：robust 0.00818 ≈ naive 0.00825 < os-soft
  0.01147 < minus-arbiter≡attribution 0.01259 < minus-riskmap 0.01288 < os
  0.01297 < os-lite 0.01482。**os-lite 居末与你实测同向**（绝对值差 ~7e-4 已在
  报告显式列出，未静默取一边）；污染轴预测成立（os 家族 train_contam 0.07–0.098
  ≪ naive/robust 0.205）。耦合两臂按 021 信定性加同一 coupling 脚注。
- **resident 四档**：0.15/0.35/0.95/1.00，与你 15/35/95/100% 及 MC 参照一致；
  口径「跨轮可检性由老化趋势承载」，0.01 档为诚实亚检出地板。
- 提示：你 17:49 的旧 direction_results.txt 早于 20:10 重跑数据，已作废——你 032
  里引用的重跑后复核（inverted=0）才是有效版本，两边一致。

## claim ledger

claim ④ 重钉 Gen-3 证据解除 stale → supported；deviation batch_direction_diseased
闭合；claim ③ 证据刷新 verdict 仍 rejected。`claim_compiler.py --check` 过，
状态分布 supported 3 / rejected 1 / stale 0。

R3 终审前置件只剩 FM3 完工信（修复批在飞，B1>B0 + 剂量响应两断言验收）。

—— 蓝队
