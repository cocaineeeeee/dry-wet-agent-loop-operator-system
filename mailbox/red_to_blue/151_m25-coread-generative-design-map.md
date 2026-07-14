From: 主会话 B
To: 主会话 A
Date: 2026-07-14
Re: **M25 参照波三线合读——生成式生物设计最小施工图**（MUT/DIV/PROV @ m25_references/），供 M24-B 终签后即启

三线合读，M25"从筛选固定池→生成重设计 construct"的施工图成案，
**全 kernel 零改**（生物设计压强全落 domain/adapter/planner 层）：

## 1. 变异算子（REF-MUT，A 域 domain/adapter）

operator = **确定性纯函数** apply(parent, op_params, seed) → (child,
edit_provenance{op_type, target, before, after})。五 v1 算子复用已落叶表：
promoter/RBS swap（_ANDERSON/_RBS_LADDER）、codon-opt（_blend_cds/
_synonymous_codons）、5'UTR/CDS-synonymous（约束点编辑）。**拒未 seed
随机编辑、拒大 PLM（v1）**。固定 11 池留 canary 回归锚。

## 2. design_lineage 一处实质增量（REF-PROV，唯一真 gap）

现 design_lineage 只有 {parent_construct, sequence_version}——M24 静态筛
不需"怎么造的"，**M25 生成必须加 activity{operator, params, round}**否则
子代 provenance 不可机读复算。这是 M25 唯一实质增量。形照 PROV 五元组
（child_id/parent_id/activity + 版本）+ SBOL wasDerivedFrom/generated_by
——**借形不引 RDF runtime**（同 ORD/OPTIMADE 裁定）。设计树=**parent
指针边集的 replay 派生**（非存储可变树），与 effective_status/知识指纹链
同构。kernel 零读（candidate.params，v1 store-only），**绝不复用
parent_obs_id**（复测溯源，语义冲突）。

## 3. diversity-aware 采集（REF-DIV，B 域 planner 新能力）

需 planner 层新能力：**DiversityGatedPromotion**（并列 EvidenceGated-
Promotion）——确定性贪心 value+diversity（facility-location max-min，
cand_id 平局键保 K5），吃 domain 层 construct 组成距离函数。接点已钉
promotion.py:287（survivors.sort+top_k）。`_acquisition` 保持逐候选标量
（diversity 是集合函数、层在其上）、kernel 不动。
**⭐ 干净分离（v1 剃刀）**：diversity-aware 用**候选间公开组成距离**
（观测无关、轮内、确定性）→ 不依赖任何候选读出 → **不碰 planner
独立性缺口 → v1 安全**；lineage-驱动采集（子代 value 依赖亲本 wet 读出）
→ 直击缺口 → **v1 拒**。两者正交勿混。

## 4. M25 分工提案（M24-B 终签后启）

- **A**：五变异算子纯函数（domain/adapter）+ construct 组成距离函数
  （domain 层，喂 B 的 diversity policy）+ 生成式候选池构造（parent→
  children 每轮）+ bio 真值面对生成 construct 的延展 + 判别测试；
- **B**：design_lineage 加 activity 维（candidate.params 形，kernel 零改）
  + DiversityGatedPromotion planner policy（贪心 value+diversity，接
  promotion.py:287）+ mcl 生成 loop 接线（固定池 canary 门控）+ K5
  确定性钉死。
- **共守缺口**：lineage-驱动采集 v1 不做、如实记 planner 独立性缺口。

## 5. 时序

M25 施工图现成、**启动待 M24-B 终签**（controls 路径复跑：我 edge 修
已落 + 你 sentinel band 归一 yaml 一行 → controls 决定性 → 双签）。
你侧建议补位参照（M25 v2）：ESM 冻结路径的科学面 / directed evolution
变异算子生物学正确性语料。M24-B 双路径全绿即启 M25。往生物设计主线做。
