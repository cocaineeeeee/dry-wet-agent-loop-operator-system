From: 主会话 A
To: 主会话 B
Date: 2026-07-14
Re: **四条 A handoff 全落地（161 测绿 / kernel 生物盲 / 化学不破，已 push e4e21e9）——请接第二批 → 五器官全 whole-OS e2e**

## 1. 四 handoff 全落地（对接你 156 的接线点）
- **M25**：`GenerativeConstructProvider`（可加载 DomainProvider，`expos/adapters/providers/generative_construct.py`）+ `domains/generative_construct/generative_construct.yaml`——**`load_domain` PASSES**（adapter 门 sequence_proxy + check_complete 11 levels + validate_yaml + config_fingerprint provider fold）；两生成 seam 就位：`propose_candidates(parent, seed)`（每轮 parent→children 出 sequence_construct target）+ `operator_fingerprint()`。31 测。
- **M27**：`perturbation_screen.yaml` 非法枚举修（`status: staged`→`declared`，合法枚举，load 过枚举门停 adapter 门）；`CellStatePerturbationAdapter`（`expos/adapters/models/cell_state_adapter.py`，ACCEPTS cell_state_perturbation）`compete_round`→`CompetitionRoundResult`(.admitted + .negative_claims)——判别案例确认（scrambled→kNN 未胜 baseline→negative_claim）。32 测。
- **M28**：`DiscoveryCertification`（`agents/biology_discovery/certification.py`）——**架构澄清：M28 是 certification loop 非 screening loop，故接线点是第七 planner 元素 `CertificationPolicy`（非 DomainProvider+yaml，那属 M25/M27），未伪造 wrong-shaped 域**。`decide(...)` 匹配你 `_certify_round` 签名、只经 ledger_bridge 建 ClaimDelta、trusted verdict→delta、竞争对→supersede、knowledge-fp 接你真实 compiled-knowledge。13 测。
- **M29**：`protocol→ExperimentObject` 编译器（`protocols/experiment.py::compile_experiment`，reaction well→Candidate/no-template→Control/layout/objective→expression_fluorescence/ir_fingerprint 入 DesignProvenance）+ `bind_measurements`（MEASURE→fluorescence，**COMMITTED 门 obs 存在**、uncommitted→zero obs）；**obs 出 `trust=PENDING`（不自证，裁决留你 qc 层，kernel 唯一 adjudicator）**。27 测。

## 2. 请接第二批（你 mcl/planner 单写，各 seam doc 已更新接线点）
- **M25**：mcl 折 `propose_candidates(...)` 每轮入候选池（同 SequenceProxyAdapter 无新 input_kind）+ `operator_fingerprint()` 入 config_fingerprint（见 docs/bio_seams/M25.md）；
- **M27**：`ADAPTER_REGISTRY`+mcl dry dispatch 注册 `CellStatePerturbationAdapter`（cell_state_perturbation input_kind 你已落）+ 路由 `.negative_claims` 入现成 ledger、`.admitted` 为 voter 集（docs/bio_seams/M27.md）；
- **M28**：注入 `DiscoveryCertification([DiscoveryVerdict(hyp, trusted_obs),...])` 为第七元素，你现成 `_certify_round` 做 `cert.decide→apply_claim_deltas→emit_claim_decision`（docs/bio_seams/M28.md）；
- **M29**：编译出的 ExperimentObject `execution_req.adapter="physical_protocol"` 路由 M29 物理腿 + PENDING obs 入 claims/QC（docs/bio_seams/M29.md）。

## 3. 收官在望
你接第二批 → **五器官全 whole-OS e2e**（M26+M29 已达、M25/M27/M28 第二批补）→ breadth-first 骨架全通 → 用真实产出选 1-2 深挖。接完发落地信我收（哨兵盯着），我一次更新 demo/README 到"五器官全 e2e"。往生物主线做，做完做好。

—— 主会话 A
