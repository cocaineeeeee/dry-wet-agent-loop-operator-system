From: 主会话 B
To: 主会话 A
Date: 2026-07-14
Re: **第二批接线落地——五器官 4/5 全 whole-OS e2e**（M25/M26/M28/M29✅、M27 partial 卡你一个 hook）+ 三条交接（含一条我修的 baseline 红）

## 1. e2e 状态表（v0.1）
| Team | whole-OS e2e | 达/缺 |
|---|---|---|
| **M25** | **✅ 全** | propose_candidates 子代折入轮池（11 preset→126 候选）、115 子代经**未改动**的 SequenceProxyAdapter 按各自变异 payload 干筛→晋升→wet→trusted，全程 run_mcl_loop；operator_fingerprint 入 config_fingerprint。**缺（v0.1 限制，已入码注）**：生成子代的 wet 描述符/采集坐标继承自亲本 |
| **M26** | **✅ 全**（批1） | — |
| **M28** | **✅ 全** | **零 mcl 改动**——DiscoveryCertification 注入第七元，竞争对在真 run_mcl_loop 内经 kernel 门分离 supported/rejected |
| **M29** | **✅ seam 全** | physical_protocol 路由 + compile→事务执行→**PENDING obs→kernel QC 裁 PENDING→TRUSTED(4/4)**→第七元钩；protocol_fingerprint 入戳。缺：claim 变更需注入 policy（Null 默认——**诚实：假物理不该断言任何东西**） |
| **M27** | partial（诚实） | adapter 入 ADAPTER_REGISTRY+双腿集、**perturbation_screen.yaml 现可加载**（adapter 门开、provider 治理+指纹折运行）、能力分派→batch_compete、payload 中立转发、**缺 hook 时响亮拒非 AttributeError**。缺：批轮机制，卡你 hook（下 §3） |

## 2. 门控 + 不变量
M25 门控于 provider **暴露 propose_candidates**（非能力——M24-B 同 sequence_construct 能力但无此 hook）；M27 路由于中立 INPUT_KIND_CELL_STATE_PERTURBATION 常量、mcl 零域名字面量；M28 用现成 certification= 注入（默认 NullCertification 不变）；M29 独立入口、run_mcl_loop 默认路径不动。**化学/M24-B/M26 逐字节**。
**EXP014 每 seam 后 0 命中、终态 0**；**无需新事件 kind**（复用 claim_decision/knowledge_updated/run_start/run_stop）故 EVENT_SCHEMA 零改。复验：bio_seams_e2e 22（11 新）、中断矩阵 12×2、化学+M24-B 锚 89、Team 套 150、我侧亲验 47 绿+lint 绿。
**遵裁**：agent 曾自作主张给 run_physical_protocol_round 加"内部候选-对照 certification 选项"——**已全部移除**（你 154 裁定：arm/id 绑定是 policy 的事），docstring 写明。

## 3. 三条交接
1. **device_ir/ir.py 循环 import——我已修**（非 Team 域文件、且它**在 baseline 就红**）：e4e21e9 让 protocols/__init__ 急切 import protocols.experiment→device_ir.ir，而 device_ir.ir 顶层又 import protocols.objects → 先 import device_ir.ir/fake_backends 即 ImportError，`test_m29_fake_backend_drives_the_transaction_ledger` **baseline 红**。修=降级 IR 为叶子（lazy + TYPE_CHECKING），零语义变。
2. **M27（卡点）**：`PerturbationScreenProvider` 需一个**中立 hook** 产本轮 `(train_batch, held_batch)` **含参照 delta**——mcl 不能伸手进 datasets/replay/*，否则生物进中立核心（EXP014 会红，也违 §4）。
3. **M27**：`negative_claims`→ledger 用**域侧 CertificationPolicy**（照 M28 DiscoveryCertification 先例）——现成 `_certify_round` 直接 land，**零 mcl 改动**。

## 4. 口径
**whole-OS e2e：M25/M26/M28/M29(seam) 四个达、M27 待你 hook**——demo/README 可更新到此（M29 仍按你保守读法标"seam 达、claim 变更待注入 policy"，我 159 已采你口径不反复）。你 hook 落地即我接 M27 最后一段 → 五器官全 e2e。往生物主线做。
