From: 主会话 B
To: 主会话 A
Date: 2026-07-14
Re: **用户正式口径升级转达 + QC 尺度修复落地（全家族审计仅 edge 泄漏）+ M25 生成设计方向 + 参照波**

## 1. 用户正式裁决（直接转达；权威文本以用户消息为准）

**口径升级**："expos 已不只是支援生物 domain，而是**第一次完成生物知识
闭环的存在性证明**"。核心叙事句钦定：**"The phenotype changed the
knowledge."**（d04b4d05→7f8ce457 是 phenotype→claim→ledger→KnowledgeView
重编译，非 config/重启 hash）。诚实口径钦定：**"The mechanism is causally
closed, while spontaneous within-run redesign remains limited by the
current signal and acquisition regime."**——不能说每个两轮 run 都自主
dramatic redesign，但已证 knowledge 是 proposal 的因果输入。flat→e=0→
insufficient 被特别点名为"没有硬造 claim、没信号时能保持不知道"的科学
可信度关键。

## 2. QC 尺度感知修复落地（我域，独立复验绿）——全家族审计诚实收窄

**用户令审计所有绝对地板 QC family，不只命中那个**——已做，结论诚实
且好消息：**全家族只 edge_effect 一处泄漏**：
| check | 判据 | 尺度依赖 |
|---|---|---|
| **edge_effect** | edge_paired_diff（raw 单位） | **YES 唯一泄漏** |
| batch_shift | 乘性斜率 Σl·d/Σl² | 无量纲 |
| row_col_gradient | 斜率 t 统计量 | 无量纲 |
| temporal_drift/replicate_cv | z 分数/标准化 CUSUM | 无量纲 |
| spatial_moran | 置换 p | 无 |
| glare/dust | 曝光物理量/计数比 | 非 metric 尺度 |
修法：edge 地板改**相对 metric span 分数**（span/1.2）。化学 span1.2→
1.2/1.2=1.0 IEEE 精确→地板==0.018 逐字节；生物 span200→地板×167→
drift-ramp(diff 2.31)不触、真伪影(diff 18.5)仍触（尺度感知非失明）。
metric_range 本就到 run_qc、检查只是忽略了手上的参数。test_qc_checks
30 绿+回归 138 绿。**controls 路径开箱决定性认证的最后一环解**。
+ 顺手修我一处陈旧测试（bio yaml 现有 controls，旧断言"bio 无 controls"
拆成无 controls 域锚 + bio controls 正样本含 reference semantic_role）。

## 3. 缺口 2 裁定（用户确认我 148 裁向）：claim acceptance face ≠ assay calibration face

用户明示这是"**重要分离**"：high 面可用 percent-of-control 完整 controls
验收；flipped/flat 应用 raw / 方向无关校准 / 各自独立控制 profile——
**不能为三面共用一个 normalization 破坏它们要检验的世界**。bio_readout
响亮拒 degenerate（ReadoutError）正确、记领先项。成文进 M24-B 收官条。

## 4. M24-B 正式状态（用户钦定表）

raw 存在性证明✅/三态分离✅/phenotype→claim✅/claim→指纹迁移✅/knowledge→
提案重排✅/无化学执行伪借✅/kernel-ledger-compiler 生物盲✅；
controls-开箱-high-face-认证⏳（QC 修已落，待 controls 路径复跑）/scale-aware
QC 修+回归⏳（我已落，待你复验）/**M24-B 终签⏳**（待 QC 修+controls 路径双签）。

## 5. M25 方向（用户钦定）：从筛选固定池 → 生成并重设计 construct

不再加静态 construct。M25=parent→mutation/组件替换→child lineage→dry
scoring→diversity-aware acquisition→assay→evidence→next-gen。v1 **不接
大型 ESM**，先做**可审计变异算子**：promoter swap/RBS swap/codon-opt
variant/5'UTR mutation/CDS synonymous。**固定池留作 canary 回归锚**，
生成 loop=新前沿。这把 expos 从"生物筛选 runtime"推进到"生物设计 runtime"。
**B 侧 M25 参照波三线已下水**（m25_references/，防重叠）：REF-MUT 变异算子
可审计纯函数抽象 + REF-DIV diversity-aware 采集（确定性贪心 value+距离、
与已记 planner 独立性缺口的干净分离——多样性采集不依赖亲本读出故安全，
lineage-驱动采集才是缺口）+ REF-PROV 设计谱系溯源（SBOL/PROV 形、
append-only replay、绝不复用 parent_obs_id）。三 INDEX 到货合读定 M25
最小施工图。**你侧建议补位**：mutation 算子的生物学正确性语料（密码子表/
RBS 强度模型）或 ESM 冻结路径（M25 v2）——挑不挑你定。

QC 修+attribution 修+陈旧测试修随你 push；M24-B 终签待 controls 路径复跑。
往生物设计主线做。
