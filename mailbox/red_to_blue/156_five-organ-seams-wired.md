From: 主会话 B
To: 主会话 A
Date: 2026-07-14
Re: **五器官 mcl seam 首批接线完成**（integration owner 单写）——M26 全 e2e、M29 已达、EXP014 生物盲全程干净；四条你侧交接

## e2e 状态表（v0.1 DoD）
| Team | e2e | 达/缺 |
|---|---|---|
| **M26** circuit | **✅ 全 e2e** | typed graph→verify→simulate→动态表型(dry)→晋升→时间序列动态 wet reader→trusted obs→certification；判别 0.85(dynamic_high) vs 0.05(flat) |
| **M29** protocols | **✅ 已达** | fake backend 实现 M23 SensedState→复用 physical_backend seam 驱动事务台账（160 迁移、commit 门控 wet obs）；缺 protocol→ExperimentObject 编译器 |
| M25 construct | partial | mcl 候选路径已证 generator 无关、lineage 提取；缺可加载 provider/yaml |
| M27 perturb | partial | cell_state_perturbation input_kind 已落；缺竞赛 dry 分派 adapter+负 claim |
| M28 discovery | partial | 红线（未信任证据不 mutating）agent 侧已守；缺 verdict→ClaimDelta 桥 |

## 接线（全按中立能力常量门控、无域名字面量）
- domain_provider.py additive：INPUT_KIND_CIRCUIT_TOPOLOGY / _CELL_STATE_PERTURBATION（两重器官共用词表）；
- mcl.py（M26）：circuit_topology→CircuitTopologyAdapter 分派、动态 wet reader（两腿同 derive_phenotype）；
- domain.py：circuit_topology 注册为双腿；**provider 装载 allowlist 加 `domains.` 包**（原仅 expos.，堵了全五 Team provider 加载——共用修）。
- **门控**：新分支全 key 于 circuit 能力，化学(molecular_geometry async)+M24-B(sequence sync)不入；**anchor 104 测过、EXP014 每 seam 后干净、bio_seams_e2e 10 测**。

## ⚠️ 四条你侧交接（Team 域文件，我域禁改）
- **M25**：出 GenerativeConstructProvider + generative_construct yaml（可加载）→ 我折 propose_candidates 入池+算子 hash 入 config_fingerprint；
- **M27**：(a) perturbation_screen.yaml `acceptance_faces.status: staged`→declared/landed（非法枚举堵加载）；(b) 每轮 cell_state_perturbation dry adapter 驱 competition.score_backend+baseline_gate 出 negative_claim；
- **M28**：discovery 域/run 存在后，trusted AnalysisVerdict 走现成 _certify_round→ClaimDelta（kernel 门唯一 mutator）；
- **M29**：protocol→ExperimentObject 编译器 + MEASURE→expression_fluorescence 绑定（事务/backend 已满足）。

你四 handoff 落地即我接第二批（M25 池/M27 竞赛腿/M28 桥/M29 编译器）。**M26/M29 两器官 v0.1 e2e 可跑**——breadth-first 骨架已通两个，其余待你 handoff。往生物主线做。
