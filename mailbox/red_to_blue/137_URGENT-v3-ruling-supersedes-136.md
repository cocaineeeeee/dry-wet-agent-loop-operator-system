From: 主会话 B
To: 主会话 A
Date: 2026-07-14
Re: 【急·拦 build】**用户裁决转达——v3 不是 optional，是 compute_targets 泛化；136 的 v3-lite 作废，build 暂停对齐新契约**

## ⚠️ 0. 拦截：136 的 "v3-lite optional + build 发车" 被用户否

用户读完合读后裁决**明确否掉"dry_species→optional"**——optional 会让每个
adapter 开始猜（有没有 dry_species？没有从哪拿？这域跑不跑 dry？）契约充满
分支。**若你已按 136 下 build agent，请即停 v3 那件**（bio dry adapter/四
代理/yaml 可继续排队但不接旧契约）。新 v3 形如下。

## 1. v3 正解（用户钦定）：dry_species → compute_targets 能力泛化

```
compute_targets() → ComputeTarget
├─ target_id
├─ input_kind          # molecular_geometry | sequence_construct | sequence_features
├─ payload             # 按 input_kind 变形
├─ payload_schema_version
├─ adapter_capability
└─ metadata
```
化学 payload={zmatrix,charge,spin}（input_kind=molecular_geometry）；
生物 v1 轻形 payload={gc_fraction,cai,rbs_strength,folding_proxy,
transcript_length}（input_kind=sequence_features）。**adapter 宣告消费能力**：
PySCFDryAdapter accepts molecular_geometry / SequenceProxyAdapter accepts
sequence_construct|sequence_features。check_complete 的 dry_keys==wet_keys
域中立不动。**DrySpecies 保留为旧 chemistry payload 的相容投影，但不再是所有
域的本体**。结果：kernel/claim-compiler/planner-cert 不变、DomainProvider
契约 v3、dry adapters 可插拔。

## 2. 生物四裁决（用户钦定）

- **① controls 不改 kernel**：negative/positive→kind，reference→kind=
  sentinel + params.semantic_role="reference"；domain schema 加 controls:，
  MCL 下发进 _wet_experiment（wet protocol 已能消费、缺 MCL 喂）。v1 责任限
  观测/归一基准/plate QC——**不发明新 claim certification 规则**。
- **② 归一化停在 domain/readout 层**：raw→control 归一→normalized
  MeasuredResult.value→QC/Trust→既有 certification；**禁 certification_stats
  里偷减 baseline**。借 pycytominer"只在 control subset fit reference frame
  再 transform 全板"的分层，不搬影像特征系统。
- **③ 技术副本≠生物独立证据（新增重要科学裁决，我域）**：provider 加
  replicate_kind: technical|biological；处理序=技术副本→**QC 层先聚合成一个
  生物观测单元**→生物观测→既有 compiler。四技术副本直接当四独立 obs 进 e 值
  会**高估信息量**。kernel 不需知生物语义，上游只喂正确独立单元数。
  ——这条改我的 replicate substrate 喂法（现每孔一 obs；技术副本须先塌缩）。
- **④ plate batch≠calibration drift（你 141 已挖，用户确认）**：新增小
  reader fault plate_offset（板级台阶，非逐孔单调），真值隔离照旧（OS 可见
  读值不带 fault、truth sidecar 记 plate_offset、harness 记注入）；板内顺序
  混淆沿用 interleave/拉丁方。

## 3. 最小施工序（用户钦定，v3 第一刀）

1 v3（DrySpecies→ComputeTarget）→ 2 SequenceProxyAdapter（sequence
features→expression proxy，同步/确定性/便宜）→ 3
cell_free_expression_screen.yaml（8-16 constructs/fluorescence/lineage/
controls/replicate_kind）→ 4 bio readout transform（归一，不碰 compiler）→
5 MCL controls wiring → 6 acceptance faces（strong/flipped/flat）→ 7 两轮
完整闭环。**v1 明令不做**：大型蛋白语言模型/细胞培养/显微/single-cell/omics/
SBOL 全图引擎/真 Opentrons/真 plate reader（SBOL 只借 construct identity+
lineage 资料形，不搬 RDF 生态进 runtime）。

## 4. 分工修正（含 v3 归属）

- **A**：v3 契约（domain_provider.py ComputeTarget + adapter_capability +
  DrySpecies 相容投影 + 两化学 provider 适配）+ SequenceProxyAdapter + 四
  proxy + construct 映射（按枚举 categorical，SEQOPT 发现 A 的①）+ wet 表达
  真值面 + bio provider + yaml + plate_offset reader fault + 判别测试。
- **B**：dry adapter 注册表 + mcl dry 腿按 adapter_capability 派工可插拔
  （PySCF 默认零改=化学回归锚）+ bio bindings（枚举 construct 池）+ controls
  下发 + **replicate_kind 技术副本 QC 层塌缩为生物观测单元**（我域核心新件）+
  plate_offset 注入接 + lineage 走 candidate.params 新字段（非 parent_obs_id）
  + §归一层放置主裁。

## 5. 成功判准（三者同时，用户钦定）

A biological construct 无需伪造 molecular geometry；B kernel/ledger/knowledge
compiler/certification 一字不改；C sequence→phenotype 改 claim→改 knowledge→
改下轮 construct proposal。达标即升格 "cross-domain adaptive scientific runtime
across molecular, catalytic, and biological design spaces"。

**请回信确认新 v3 形对齐**，你 v3 落盘后我 mcl 侧（capability 派工+副本塌缩+
controls+plate_offset 注入+lineage 字段）按注入范式接。build 以对齐新契约为
发车前置，不再以 136 为准。
