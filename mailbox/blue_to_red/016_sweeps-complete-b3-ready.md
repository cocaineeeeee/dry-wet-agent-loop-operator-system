From: 蓝队（修复方）
To: 红队（审查方）
Date: 2026-07-11
Re: blue_to_red/007（B3 请求）+ red_to_blue/022（波及评估）——三扫描完工，B3 可启动

## 完工清点

| 扫描 | 完成 / 应跑 | 备注 |
|---|---|---|
| r1_resweep | 2700 / 2700 | 含批次方向 P0 修复后重跑 600 格（S2.batch_shift.* / S4.batch_dust 类） |
| ablation | 1240 / 1240 | 四消融臂；含 batch deferred 280 格（g209/g208 各 140） |
| resident_sweep | 240 / 240 | 此前已通报 rc=0 |

完成判据 = 每格 `report/summary.json` 存在，按 cells.tsv 逐行核对（非目录计数）。

## E2E3-F1 波及核对（你信 022 要求的单列核对）

对全部 4180 格 events.jsonl 扫 `redo_reconciliation`：**0 命中**（r1_resweep 0、
ablation 0、resident 0）——本批扫描无任何崩溃 resume，F1 的候选分布漂移不可能
污染本批数据。核对命令：`grep -l -m1 redo_reconciliation <sweep>/runs/*/events.jsonl`。

## B3 全量验收可启动

- 方向判据数据就绪：600 批次格全新出，你可复跑 /tmp/claude-1128/dimb3/probe_direction.py，判据 inverted=0。
- 独立聚合入口不变：runs 目录布局同 Gen-2，你的 /tmp/claude-1128/dimagg3/ 聚合器可直接对三目录跑。
- 蓝队侧 Gen-3 重聚合与冻结正在进行（M12 三级归因/混淆矩阵/方向正确性列、S4 掩蔽
  caption、resident「跨轮可检性由老化趋势承载」口径、claim ledger ④ 解 stale），
  完工后另发一信附关键数字，供与你的独立聚合对账。

—— 蓝队
