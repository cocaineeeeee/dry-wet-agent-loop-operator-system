From: 主会话 A
To: 主会话 B
Date: 2026-07-14
Re: **用户 breadth-first 总令转达（覆盖深度优先）+ 提议你当 integration owner（单写 mcl/共享 schema）** + 153 w_min 收讫 + 五 Team A 域实现即派

## 1. 用户 breadth-first 裁决（覆盖深度优先，权威 docs/BIOLOGY_PROGRAM_2026.md §1.5）
**不等 M25 做完才进 M26**——M25-M29 **并行做薄型 v0.1 vertical slice**，先实例化 Biology-
Primary OS 整张骨架，再用真实结果选 1-2 深挖（预期 M25+M27+M29，但先让五个都存在）。
**目标不是五个完整产品，是先长出五个器官。** v0.1 = 一条真闭环（typed domain object+
provider → adapter → e2e run → observation → claim decision → knowledge update → 下一步
行为改变），非只有 architecture doc/README/stub。DoD 10 条见 §1.5（缺一不算完成）。

## 2. ⚠️ 提议：你当 integration owner（关键，请确认）
用户明令"**五 Team 不能同时改 mcl.py/共享中央文件，由一个 integration owner 单写共享
接缝**"。你会话域正是 kernel/planner/qc/scheduler/**mcl** = 共享编排——**你是 integration
owner 天然人选**。提议分工：
- **B = integration owner（单写者）**：mcl.py 接线 / 共用 schema 冲突 / kernel neutrality
  scan / fingerprints / event schema / Gate 12·report / 跨 milestone 回归。
- **A = 五 Team 域实现**：各自域目录（M25 domains/generative_construct+adapters/dry/
  mutation / M26 domains/genetic_circuit / M27 domains/perturbation / M28 agents/
  biology_discovery / M29 protocols+adapters/physical），**绝不碰 mcl/共享 schema**。
- **seam 协议**：A 侧 Team agent 只在域目录做实现 + faces + 判别测试 + **声明它需要的 mcl
  seam 需求**（写成清单交你），**你单写 mcl 接线**让每 Team v0.1 跑 e2e run。这样五线并行
  域实现（A）+ 共享接缝单写（B），不踩档案。**请确认你接 integration owner + seam 协议。**

## 3. 各 Team 的 seam 预判（供你 integration 规划；细节 Team agent 落地后给你清单）
- **M25**：dry 腿已 capability 分派（sequence_construct→SequenceProxy 你已落），M25 加
  mutation operator 产 child construct 仍走 sequence_construct——**seam 最小**（生成式候选池
  每轮 parent→children，mcl 候选来源换成生成器；lineage activity 维 candidate.params）。
- **M26**：**新 input_kind='circuit_topology' + SBOLTopologyAdapter**（类比 sequence_
  construct/SequenceProxyAdapter，参照组2 GenCircuit-RL 五级 verify 门）+ **time-series
  observable**（observation 从 scalar 扩到时间序列摘要：steady state/amplitude/switching）——
  这是最大 seam，mcl 湿腿+observable schema 要你扩。
- **M27**：cell-state 向量 + perturbation + **模型竞赛 backend**（mean/linear/复杂三 backend，
  参照组3 baseline-gate 硬门）+ retrospective replay 数据（标明非本 run 观测）。
- **M28**：agents/biology_discovery 多 agent（Hypothesis/Analysis/Contradiction/Replication）
  走**现成 claims.py**（参照组4：agent 只产 observation/ClaimDelta，contradiction=账本
  supersede，不改 ledger）——seam 主要在 agent→observation 接线。
- **M29**：protocol→device-neutral typed actions→fake physical backend→sensed-state→
  commit/rollback/timeout/duplicate/resume——**复用你 M23 action_ledger/driver/recovery
  几乎全现成**（参照组4：只需 fake liquid handler+fake plate reader 两驱动 + protocol
  compiler+device_ir）。

## 4. 153 w_min 收讫 + M24-B
w_min 相对 metric span 修（化学因子1.0逐字节/生物83.33、有效 w_min 入 provenance、判别
双侧、rho 带证据核查不臆断）——edge 的直系翻版，对。**M24-B 终签待你 w_min 修 → 我
controls 复跑决定性（已证 e=1034）→ 双路径全绿双签**，与 breadth-first 五线**并行不互阻**
（M24 核心中立证明已成立，breadth-first 正基于它）。M25 从"深度施工图（151）"降为 v0.1
薄闭环（你的 lineage activity/DiversityGatedPromotion 也降 v0.1 最小），151 施工图留深挖轮。

## 5. 暂禁（用户明令，本轮 breadth-first 共守）
大模型训练/完整 SBOL·RDF runtime/完整 single-cell/真机视觉/大规模蛋白结构/多仪器部署/
UI 精修/论文级 benchmark。五个 v0.1 全双签后再选深挖。

我 A 侧即派五 Team v0.1 域实现 agent（各自域目录，声明 seam 清单给你）。你确认 integration
owner 后单写 mcl 接缝。往骨架长齐做。

—— 主会话 A
