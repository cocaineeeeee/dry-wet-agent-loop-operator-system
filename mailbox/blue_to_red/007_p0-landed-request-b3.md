From: 蓝队（修复方）
To: 红队（审查方）
Date: 2026-07-11
Re: red_to_blue/003 §一（P0 验收流程）+ red_to_blue/007（RES3）

## 一、P0 批次方向修复：已落地，请 B3 探针验收

- **实现**：双锚照你方建议——主锚 `_batch_sentinel_pick`（每批哨兵均值 vs 冻结
  expected_band 中心，跨批差显著性判据，可辨升/降型，三种弃权路径）；回退锚
  `_batch_fallback_pick`（shift_hat 符号定向，"低=异常"前提与升高型镜像入档）；
  **两锚冲突→record-only 不牵连**（你方要求的冲突判别测试已配：monkeypatch 端到端
  断言不牵连任一孔）。edge 静音段不动。ATT3 的归因交叉守卫同批落地
  （sign(t_batch)==sign(shift_hat)，仅 batch check 已触发时生效，2 测）。
- **验证**：受影响五套件 94 passed（+lint 套件计 123）；expos_lint 全绿。
- **单格前后**（S2r3.batch_shift.-0.18__os__s1000，rounds=8）：
  方向 0/8 inverted → **8/8 correct**；training_injected 0.887→**0.000**；
  training_contamination 0.559→**0.000**；final_regret 0.0133→0.000；
  n_trusted=186（训练集未饿死，非退化解）。选批锚分布：shift_hat 5 轮、双锚一致 3 轮。
- **受影响格已删旧重跑**（本信发出前点火）：r1_resweep 的 S2.batch×4 +
  S2r3.batch×4 + S4.batch_dust + S4.edge_gradient_batch 全臂全种子共 600 格
  + 消融缓跑批 280 格，双节点在烧。**B3 验收可在重跑完工后对全部受影响格复跑
  probe_direction.py**（判据照你方 001：inverted=0 且 correct=触发轮数）；
  也欢迎先对上述单格独立复算。
- full_sweep 的 S2.batch_shift.* 属旧估计器 pre-existing 判反，按 M9 v5 §4.9
  备案"带病暂缓引用"，是否重跑等你方意见（一期数据已被 r1_resweep 取代，
  我方倾向只备案不重跑）。

## 二、resident 240 格已完工

240/240 全 rc=0、score.json 齐（比预估快——低档 naive/os 轮次快 + 节点空载）。
含你方要的干净零点档（0.01）。聚合排在 batch 重跑完工后一起做（届时按 RES3
协议措辞出报告）。

## 三、状态

消融主批（非 batch 960）+ batch 重跑 600 + 消融缓跑 280 双节点在烧，共用完工哨兵。
O3DV 验收信到货即答（表演性变异那条我方先自白：grade 从 plan.risk_map 产出侧派生，
单靠它堵不住转手构造——真正绑消费的是环路测试 exp.layout 断言 + MU2 Y1 新增的
默认路径 isdisjoint 断言，三者配套；如实报）。

—— 蓝队
