# M24 — cell_free_expression_screen（第三域：生物）

> 用户裁决（2026-07-14）：现在接生物，但不是细胞培养+药物发现+机械手臂的巨大工程，
> 而是把生物当**第三个结构上真正不同的域**，检验 expos 是 scientific runtime 还是
> chemistry runtime。第一站钦定 = **cell-free protein expression / 基因构建筛选**。

## 两轴纪律（不可混淆）

```
轴一 科学域：  chemistry → biology       ← M24 走这条
轴二 执行方式：simulator → hardware      ← M24 不动，仍 simulator
```

M24 只证**生物知识闭环**（公开数据/生物 oracle → 跑通 → 生物 QC 与溯源）；
物理设备闭环是后续。**绝不**为接生物同时引入：细胞培养/显微镜/single-cell/
omics/药物筛选/真机械手臂——失败时才能定位是 domain/模型/仪器/编排哪一环。

## 为什么是 cell-free（而非酶活筛选）

酶活 = 条件→反应→yield，结构上仍是 catalyst 的孪生。cell-free 逼出新科学物件：
**construct（promoter/RBS/CDS variant/条件）→ 生物表达过程 → 表型读出**——
真正测试 kernel 是只懂"一组化学候选"还是能处理不同种类的科学设计物件。

## 最小落地版本（v1，先证闭环，勿铺开）

- 候选 constructs：8–16；rounds：2–3；replicates：4–8；
- observable：单一 fluorescence；
- controls：negative（无 promoter/CDS，期望 ~baseline）/ positive（已知强构建）/
  reference（中）；
- 判别面：strong positive / flipped / flat（同 K-D only-mu-differs 纪律）；
- **dry 端不上大型蛋白语言模型**——v1 用自足简单代理：GC content / codon
  adaptation index / RBS strength proxy / RNA folding ΔG proxy（每个都诚实标注
  为 biased proxy）。runtime 成立后再换 ESM / RNA foundation model。

闭环全环：`sequence proposal → dry prediction → wet assay simulation →
biological QC → evidence certification → claim update → next construct proposal`。

## 红线（架构纪律——本域是 domain abstraction 干净度的判官）

**kernel/planner/evidence-compiler/ledger 基本语义零改动**是硬门。
若为生物必须改这些，即证明 domain abstraction 还不够干净——那是**发现，如实报告**，
不是偷偷改。kernel 仍只需理解：candidate / observation / trust / evidence /
claim / knowledge / decision。

**属 domain/provider 层（新增合法）**：construct identity / sequence version /
parent–child lineage / promoter·RBS·CDS 组件 / plate·batch 标识 / biological
replicate 标识 / positive·negative·reference controls / assay readout units /
normalization policy。

## 架构风险清单（本域必须诚实回答的几问——这些答案才是 M24 的真交付）

1. **dry 腿不再是量子化学**：sequence-feature 预测器是**全新 dry adapter**（非
   PySCFDryAdapter 换几何表——catalyst 是那样，生物不是）。adapter 契约能否容纳
   一个非 PySCF、同步、轻量的 dry 腿？（DOMAIN2 线曾记 snar_flow 走 SimulatorBase
   同步 execute 径——生物 dry 腿可循此，本机秒级、无 sbatch。）
2. **construct 物件**：candidate.params 能否承载 sequence 组件+lineage 而 kernel
   不改？还是需要 domain schema 扩展（provider 层）？
3. **controls**：三类对照能否复用 sentinel 机制+域声明的对照构建+期望带？
4. **batch/plate 效应**：生物 replicate + 批次校正——现 replicate+interleave 基底
   处理板序混淆；批次偏移（板间 offset）可否复用 reader 服务端 calibration_drift
   artifact 机制（板级偏移≈标定漂移）？还是需要新机制？
5. **normalization**：读出相对对照归一——是 domain 层 QC 步，还是碰 evidence
   compiler？（碰即红旗。）

## domain 契约 v2 适配面（预判）

provider 五 hook：`dry_species`（生物无几何——此 hook 需重解为"构建→dry 输入"，
或该 hook 本就是 chemistry 特化的信号，记 v3 候选）/ `wet_coords`（construct →
公开设计坐标，truth 面读之）/ `truth_profiles`（expression_high/flipped/flat）/
`seed_claims`（"high-[feature] 构建表达更高"）/ `validate_yaml`。
**dry_species hook 是否 chemistry-leaky 是本域最尖锐的契约测试**——如实评估。

## 成功后对外主张（诚实门槛）

现在：Domain-swappable scientific runtime across **two chemistry** domains。
生物第三域跑通+生物 QC 成立后才可说：
**A cross-domain adaptive scientific runtime demonstrated across molecular,
catalytic, and biological design spaces.**（molecules / reactions / sequences /
phenotypes 都进同一条 Design→Execute→Observe→Certify→Update→Redesign。）

## 分工提案（A=adapters/domains/wet；B=kernel/planner/qc/mcl/schema）

- **A**：sequence-feature dry adapter（新，走同步 SimulatorBase 径）+ 四代理 +
  construct 物件映射 + wet expression 真值面（expression_high/flipped/flat）+
  domain provider + domains/cell_free_expression_screen.yaml + 判别测试。
- **B**：mcl bindings（construct 池/seed claims 中立化，若 domain contract 需扩展）+
  domain schema 生物字段（construct 组件/lineage/controls/normalization——若确需）+
  批次/归一若触 QC 的裁定。**kernel/evidence-compiler/ledger 零改动是双侧硬门。**
- 契约 v3 是否需要：以 A 侧 dry_species hook 适配实况为准，双侧合读裁。
