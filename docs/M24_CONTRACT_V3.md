# M24 前置 · Domain Contract v3（用户裁决 2026-07-14，权威——覆盖 v3-lite）

> 用户明令：**不要把 dry_species 变 optional**（optional 让每 adapter 开始猜"有没有/
> 从哪拿/跑不跑 dry"，contract 长满分支）。改判为把它**泛化为 compute_targets()**。
> 第一刀是修 dry_species chemistry leak，**不是填 cell_free yaml**。这是第三域存在的
> 价值：逼 expos 证明自己真不是 chemistry runtime。

## v3 契约形（A 域 domain_provider.py，权威）

```
compute_targets() -> Mapping[level, ComputeTarget]     # 取代 dry_species()

ComputeTarget:
  target_id: str
  input_kind: str            # "molecular_geometry" | "sequence_construct" | "sequence_features"
  payload: dict              # 按 input_kind 变形
  payload_schema_version: str
  adapter_capability: str    # 声明本 target 需要哪种 adapter 能力
  metadata: dict = {}
```

payload 按 input_kind 变形（固定字面量，两 agent 共用勿各自定义）：
- `molecular_geometry`：`{zmatrix, charge, spin}`（chemistry）
- `sequence_construct`（生物 v1 首选，dry 腿真算）：`{sequence, promoter, rbs, cds,
  parent_construct, sequence_version}`
- `sequence_features`（更轻备选，特征预算好）：`{gc_fraction, cai, rbs_strength,
  folding_proxy, transcript_length}`

**adapter 声明消费能力**：PySCFDryAdapter accepts `molecular_geometry`；
SequenceProxyAdapter accepts `sequence_construct | sequence_features`。

`DrySpecies` **保留为 chemistry payload 的相容投影**（一个 helper 构造
molecular_geometry ComputeTarget），**不再是所有域的本体**。check_complete 保
`compute_target keys == wet_coords keys`（域中立，不动）。

## 分层不变量（成功判准 B）

```
kernel / claim·evidence compiler / planner certification / ledger / knowledge   零改
DomainProvider contract          → v3（ComputeTarget）
dry adapters                     → 可插拔（按 adapter_capability 选）
```

## 最小施工顺序（用户钦定七步）

1. **Contract v3**：DrySpecies → ComputeTarget（A，本 doc 权威；两现有 provider
   迁移为声明 molecular_geometry ComputeTarget；solvent+catalyst 逐字节回归锚硬门）。
2. **SequenceProxyAdapter**：sequence features → expression proxy，同步/确定性/便宜（A）。
3. **cell_free_expression_screen.yaml**：8-16 constructs / fluorescence unit /
   construct lineage / controls / replicate_kind（A yaml + B schema 字段）。
4. **Bio readout transform**：negative baseline + positive/reference 归一——
   **不碰 evidence compiler**（domain/readout 层，进 QC/certification 前完成）。
5. **MCL controls wiring**：domain controls 真正进 wet experiment（B mcl）。
6. **Acceptance faces**：strong / flipped / flat（A sim_reader + 判别测试）。
7. **两轮完整闭环**：proposal→dry proxy→sim plate assay→normalization→QC/trust→
   certification→claim update→knowledge fingerprint change（联合，末步）。

## 生物域四裁决（用户钦定）

1. **Controls 不改 kernel**：negative→kind=negative / positive→kind=positive /
   reference→kind=sentinel + `params.semantic_role="reference"`；domain schema 加
   多项 `controls:`，_wet_experiment 下发（wet protocol 已能消费，缺的是 mcl 没喂）。
   v1 controls 责任限：观测 / 归一化基准 / plate QC——**不发明新 claim certification 规则**。
2. **归一化停在 domain/readout 层**：raw fluorescence → 阴阳/参照对照归一 →
   normalized MeasuredResult.value → QC/Trust → 既有 certification。借 pycytominer
   "只在 control subset fit reference frame 再 transform 全板"的分层，不借影像特征系统。
   certification 只读 TRUSTED 观测公开值 → 归一在它之前完成即统计内核完全域中立。
3. **技术副本 ≠ 生物独立证据**：domain 增 `replicate_kind: technical|biological`；
   处理序 = 技术副本 → QC 层先聚合成一个生物观测单元 → biological observations →
   既有 evidence compiler。kernel 不知 promoter/cell-free/technical replicate 语义，
   上游只喂正确的独立单元数（4 技术副本直入 e 值聚合会高估信息量）。
4. **Plate batch ≠ calibration drift**：新增极小 reader-side fault `plate_offset`
   （板级常量加性：plateA +0.08/plateB −0.04…，形不同于逐孔单调 drift）；真值隔离
   照旧（OS 读无 fault truth / truth sidecar 记 plate_offset / eval harness 记注入）；
   板内序混淆沿用现 interleaving/Latin-square。

## 成功判准（三件同时成立才可升级对外主张）

- **A**：biological construct 无需伪造 molecular geometry；
- **B**：kernel/ledger/knowledge compiler/certification semantics 一字不改；
- **C**：sequence→phenotype 的数据改 claim → claim 改 knowledge → knowledge 改
  下一轮 construct proposal。
三者成立 → 可升级："A cross-domain adaptive scientific runtime demonstrated
across **molecular, catalytic, and biological** design spaces."

## v1 明令不做

大型 protein LM / 细胞培养 / 显微镜 / single-cell / omics / 真 SBOL 全图引擎 /
真 Opentrons / 真 plate reader。SBOL v1 只借 construct identity+lineage 数据形，
不搬 RDF 生态进 runtime。

## 记账（架构债，M24 落地随收官条入 CHECKPOINTS）

1. **provider hook 运行期脱节**：五 hook 现仅出生治理消费、run 直读叶子表——
   M24 生物循 catalyst 法造 bio 叶子表 run 直读+provider 包；"让运行期消费 hook"
   重构另立批。
2. **lineage 驱动 acquisition 的 planner 独立候选假设缺口**：construct 设计谱系
   走 candidate.params 新字段（**绝不复用 parent_obs_id**——其语义是复测溯源非
   设计谱系，套用即双义污染反向账）；v1 只做谱系标识不驱动提案，缺口如实记档。
