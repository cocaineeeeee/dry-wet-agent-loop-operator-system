# MR_REGISTRY — 蜕变关系注册表（判别性资产的声明层）

- **日期**：2026-07-12　**依据**：INDEX_M17_METAMORPHIC.md 四问 + red_to_blue/078 落位裁决
- **性质**：声明表，不是运行时框架——每行把一条已存在（或已立项）的判别关系收编为命名蜕变关系（MR）。双重身份：论文 method 表 + 可审计判别性资产清单。
- **巡检契约（EXP012，B 杂务批落）**：`test_anchor` 列的 `file::test_id` 必须真实存在——声明的测试不存在/被改名即 lint 红。注册表自身因此具有判别性。
- **comparison_face 语义**：`decision`（决策面：knowledge fingerprint / 提案序 / promoted 集——同 seed 逐位 `==`，M16 G5 口径）；`observation`（观测面：trust/routing 序列）；`event`（事件面：kind 链与 payload 必键）。执行侧 flaky 字段（负载下 deny_reason 漂移、obs 计数）永不入比较面（letter 069 诚实边界）。

| mr_id | τ（源→跟随输入变换） | R（输出关系） | comparison_face | test_anchor | 出生里程碑 | 状态 |
|---|---|---|---|---|---|---|
| **MR_identity** | 恒等：同 (seed, knowledge, domain) 重放 | 决策面逐位相等 | decision | `tests/test_w8_acceptance.py::test_g1_agent_proposal_follows_knowledge`（frozen 段）；`tests/test_w9_mcl.py`（同 seed 双跑段） | M16 G1/G5 | active |
| **MR_permutation** | 候选枚举顺序置换（含构造性采集分平局） | promoted 集不变（决定论次键） | decision | `tests/test_w7_promotion.py`（平局构造测试） | M16 W7（血统：R3 P0 对称平局教训） | active |
| **MR_reverse** | τ_flip：仅翻转隐藏真值面（polar_high→nonpolar_high，证据源头侧，无 OS 可见参数） | 提案序反向 ∧ promoted 集反向 ∧ fingerprint 链演进（与 MR_identity 对照差分=「agent 真被数据重引导」） | decision | `tests/test_k_flipped_domain.py`（基底）；`tests/test_k_e_acceptance.py::test_mr_reverse_direction_statistic_fully_separated`（D1 雏形：N 种子方向统计量全分离）；整环 C2ST 待 K-B（`test_k2_five_conjunction_ring` skip 桩） | M17 K1/K2 | **partial**（基底+D1 雏形 active，整环 pending K-B/K-C） |
| **MR_null** | 真值面置空（flat 无信号面，已落 sim_reader）；随机化面留 K-B | 聚合器必出 insufficient（不得胡编 claim）；翻转裁决量 permutation p 显著（后者待 K-B 统计 fn） | decision + event | `tests/test_k_e_acceptance.py::test_mr_null_flat_face_honest_null_certifies_insufficient`（诚实空 fn 首实现形态） | M17 K3（血统：NO_COVERAGE 家族/I-F1 空绿） | **partial**（诚实空 fn 负控 active；permutation-p 判别 pending K-B） |
| **MR_channel_separation** | dust 二元通道 POSITIVE 与否的最小差分 | batch 通道裁决不被 binary 通道淹没（per-channel verdict） | decision | ③ 试点落地后的 S4 验收探针（spec EVIDENCE_TYPING §6 v1.1 + 变异 M5） | ③（血统：ATT3 S4 掩蔽 + S4 原型实证） | **pending ③** |

## 维护纪律

1. **只登记有判别力的关系**：每行必须存在"删守卫必红"的负样本证据（出生里程碑列可溯）——无负样本的行不得入表。
2. 新增 MR 随其承载测试同批入表（Boy Scout：动到判别测试时顺手登记）。
3. pending 行是燃尽表：状态翻 active 需 test_anchor 落实 + 负样本实证，翻转时更新本表即触发 EXP012 巡检自证。
4. 本表不替代测试本身——它是索引不是实现；比较面细节以 test_anchor 内断言为准。
