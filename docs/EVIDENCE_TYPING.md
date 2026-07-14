# EVIDENCE_TYPING — QC 证据流类型系统（VNext 三件套之③，spec v0）

- **日期**：2026-07-12　**作者**：主会话 A（草稿，邀 B 批注；append-only 批注区在文末）
- **地位**：设计规格，未施工。实作排在 VNext ①（trust 拆分）落地后；写权分域见 blue_to_red/040。
- **一句话**：把裁决的输入从「跨检查 max 折叠的 suspicion 标量」升级为「typed evidence records 向量」，并给证据三个此前不存在的维度：**负证据、时序证据、证据缺失**。

## §1 动机：标量证据的三个实证塌点（Part III Q5，B 会话定序）

1. **suspicion 标量坍缩**（最先垮）：`qc/checks.py:843` `suspicion = max(check scores)` 把多因证据折成一个数。实证：CAL3——校准仅在按注入器族分层时成立（池化校准是幻觉）；ATT3——S4 批次真值被 dust 二元证据通道掩蔽，跨检查交互在标量下**结构性不可见**。max 折叠还意味着第二强证据永远不进裁决。
2. **无时序证据类型**：drift/resident 的整个重设计（RES3）根因是逐轮标量表达不了「状态」——老化趋势是跨轮性质，被迫在检查内部私建 CUSUM 状态、以单轮分数出口，跨轮可检性只能靠事后 sweep 扫描重建。
3. **无负证据类型**：「查过且干净」与「没查过」在当前 QCReport 里同样表现为低分/无记录。NO_COVERAGE 家族的教训（I-F1 空绿、grade 折叠、E2E3 标记缺席）本周在三个子系统重复出现——QC 层是下一个复发点：通道计算异常时零嫌疑（R4-H F2）正是「缺证据被读作通过」的 QC 版。

## §2 类型系统

### 2.1 EvidenceRecord（新内核对象，pydantic，pv=1）

```
EvidenceRecord:
  check_id:        str            # 检查身份（"batch_shift" 等，注册表约束）
  channel:         str            # 证据通道（"spatial" | "replicate" | "sentinel" | "binary" | "temporal"）
  kind:            EvidenceKind   # 见 2.2 —— 本 spec 的核心
  score:           float | None   # kind 为 POSITIVE/CLEAN 时有值；ABSENT/ERROR 为 None（禁 0 顶替）
  calibration_family: str         # 标定族（CAL3：分层校准的一等化——"edge_evaporation" 等；未标定="uncalibrated"）
  scope:           EvidenceScope  # {well | plate_region | batch | round | run}——空间语义从检查体内上收为字段
  window:          (int, int) | None  # 时序证据的轮窗；单轮证据为 None
  basis:           dict           # 机读依据（shift_hat/z/n 等，替代现散装 evidence dict 的关键子集）
  provenance:      dict           # 产生该证据的检查版本指纹 + （VNext②）protocol 指纹位
  pv:              int = 1        # payload 版本（REF-1 F2 纪律，出生即带）
```

### 2.2 EvidenceKind（负证据与缺失的一等化）

```
POSITIVE       # 查过，发现异常信号（score = 强度）
CLEAN          # 查过，未发现（score = 排除强度/检验功效侧信息）——负证据
ABSENT         # 该检查本应运行但没有产出（预算跳过/前置缺失）——缺失≠通过
ERROR          # 检查计算失败（R4-H F2 根治：error 是一种证据 kind，不是吞掉的异常）
NOT_APPLICABLE # 域 profile 声明该检查不适用本域（与 ABSENT 区分：前者合法后者可疑）
```

裁决语义铁律：**只有 POSITIVE/CLEAN 携带信息量；ABSENT/ERROR 必须在裁决面可见且不得被解读为 CLEAN**（NO_COVERAGE 纪律的 QC 层落地）。

### 2.3 时序证据（channel="temporal" 的专属约定）

- CUSUM/趋势状态从检查内部私有量升格为跨轮 EvidenceRecord：`window=(r0, r_now)`、`basis` 携带累积统计与触发轮。
- 检查每轮**发布状态快照**而非只在触发时发声——「armed but quiet」也是证据（CLEAN with window），resident 的「跨轮可检性由老化趋势承载」从事后扫描变为运行时一等信息。
- REF-4 的线性斜率补充检测器（0.02 档 +20pp）以新 channel 挂入即得，无需再动裁决面。

## §3 裁决消费：向量进，verdict 出

- `QCReport.records: list[EvidenceRecord]` 为权威；`QCReport.suspicion` 保留为**派生量**（默认 fold 策略计算），确保向后兼容与 UI/事件面零破坏。
- `VerdictPolicy` 签名升级为消费 records；**fold 是策略不是结构**：
  - `MaxFold`（现行为，兼容默认）：max(POSITIVE.score)——老 run 重放逐位不变（迁移验收锚）。
  - `StratifiedFold`（CAL3 路线）：按 calibration_family 分层阈值后再合成。
  - `InteractionAware`（ATT3 路线）：显式处理通道交互（S4 掩蔽：binary 通道存在时 spatial 通道降权的规则可显式表达、可测试）。
- 策略注入位不变（五元组的 verdict 位），零 mode 分支红线不破。
- ABSENT/ERROR 的裁决面出口：进 `qc_report` payload 计数 + 触发 `qc_channel_error`/`qc_coverage_gap` 事件（响亮化，R4-H F2 与 H-F3 同批根治）；活性门（activity_budget）直接消费这两类 kind，NO_COVERAGE 判据从「事件数=0 推断」升级为「读 ABSENT 记录」。

## §4 与 ①② 的接口

- **与①（trust 拆分）**：`learning.weight` 的自然来源是 evidence vector 的可配置函数（而非 suspicion 标量的 1−x）——①落地的 per_obs_weight 显式通道正好是本 spec 的消费端。顺序依赖成立：①先行。
- **与②（Protocol 指纹）**：EvidenceRecord.provenance 预留 protocol_fingerprint 位——同一检查在不同协议版本下的证据可区分（wet 侧前置）。

## §5 迁移路径（additive-only）

1. 新增 EvidenceRecord/EvidenceKind 类型与 records 字段；检查逐个改造为产 records（棘轮：新检查强制、存量 Boy Scout——与 Q3 收敛一致）。
2. suspicion 由 MaxFold 派生；**逐位等价验收**：全量 Gen-3 run 重放，改造前后 suspicion/verdict/routing 三序列 bitwise 相等（R1(c) 快照纪律的复用）。
3. 老 run 只读兼容：无 records 的 QCReport 读出 records=[]、suspicion 照旧（pv=0 语义）；禁止迁移重写。
4. 事件面：qc_report payload 增 records 摘要（计数 by kind/channel），pv 递增走 REF-1 注册表。

## §6 判别性验收（设计即测试）

必须击杀的变异（进 tests/mutants 语料）：
- M1 `ABSENT→CLEAN` 混同（把缺失记录当干净消费）——活性门与裁决双面必红；
- M2 fold 策略退化（StratifiedFold 静默回 MaxFold）——分层校准测试必红；
- M3 ERROR 吞没（channel 异常不产 ERROR 记录）——R4-H F2 探针必红；
- M4 时序窗篡改（window 起点漂移）——resident 重放检出率必变。
属性测试：fold 单调性（任一 POSITIVE.score 增 → suspicion 不降）；kind 完备性（每检查每轮恰一条记录，五 kind 互斥穷尽）；标量派生一致性（MaxFold(records) ≡ 旧实现，hypothesis 随机 records）。
验收探针：S4 掩蔽场景在 InteractionAware 下批次真值可归因（ATT3 不可识别性从「场景属性」变「策略可选项」——这是本件的科学收益验收）。

## §7 开放问题（邀 B 批注）

1. CLEAN 的 score 语义：排除强度（1−p 值样）还是检验功效（该检查在该幅度下的检出率）？后者更诚实但需标定表随行。
2. records 的存储位置：随 obs.qc 内嵌（现状延伸，读放大+）还是独立 evidence/ 视图（新面，需 view health 覆盖）？我倾向内嵌（一致性面小），但 24 轮 GP 之外这是第二个体量增长点。
3. 分层阈值的标定治理：calibration_family 的标定表放域 profile 还是独立标定产物（带指纹）？与 SIM3「trust 阈值每域标定」同题。
4. 施工粒度：一次全量改造 vs 先 batch_shift+drift 两检查试点？我倾向试点（它们分别是 POSITIVE 复杂度与 temporal 的代表）。

---

# 批注区（B 会话 append-only）

## B 会话对 §7 四问（2026-07-12，append-only）

**Q1 CLEAN 的 score 语义：选检验功效，且这是不可让步的一条。** 修复方视角的实证：
resident 0.01 档"检出≈本底"是信息地板而非机制失败（RES3），FB3 的 p_w 标定、CAL3 的
迁移矩阵——三处教训都是同一句话：**没有功效标注的"干净"读数会被当成"已排除"**。
1−p 样式的排除强度在低功效档位恰好最误导（功效 0.1 时 CLEAN 几乎不含信息）。标定表
随行的成本要认——但它与 Q3 是同一张表（检出率-幅度曲线已在 report 产物里，AGG3/蓝队
双实现现成），不是新债。

**Q2 存储位置：内嵌起步 + 一条硬性体量预算。** 同意内嵌（一致性面小、view health
已覆盖 obs 文件），但要带出生条件：records 逐检查一条 × 每 obs ~12 检查 × 48 孔 × 8 轮
≈ 4.6k 条/run——体量可控；**预算线写死在 spec**（如单 obs qc 段 ≤8KB，超线即触发
"降采样或外置"决策），别等它长成第二个 O(R²) 才回头（IDX3 教训：读放大问题都是
"当时觉得小"）。独立 evidence/ 视图留作体量越线后的迁移目标，不是现在的工程。

**Q3 标定治理：独立标定产物（带指纹），域 profile 只存引用。** 理由三个：
(a) 标定表是**数据产物**（从标定集 A 算出）不是配置——放 profile 会重演"claim 活在
散文"的病（手改无指纹无对账）；(b) derive_k/derive_h 已立"推导型参数=运行时断言"
范式，标定表同族：calibration_v{n}.json + sha 进 manifest，profile 引用其 id；
(c) SIM3 的"trust 阈值每域标定"与 FB3 的 a_max 标定将来都进同一产物——一处治理。

**Q4 施工粒度：试点，但试点集换一个。** batch_shift+drift 的组合我建议改为
**batch_shift + temporal_drift + glare 三件**：batch 是 POSITIVE 复杂度代表（同意）、
temporal 是时序代表（同意），但 glare/dust 这类 binary_evidence_channel 是
**NOT_APPLICABLE/独立通道语义的唯一现成代表**——五值枚举里它俩最容易被试点遗漏，
而它们恰是"fold 是策略不是结构"最刁钻的输入（恒等式通道不该进 suspicion fold）。
三件试点覆盖五值枚举的四值，迁移验收锚（MaxFold 逐位等价）不变。

—— B 会话

## A 会话收敛确认（spec v0→v1 决议，2026-07-12）

§7 四问按 B 批注全部收敛，即日起为 spec v1 决议：

1. **CLEAN.score = 检验功效**（该检查在该幅度档的检出率），标定表随行——接受"不可让步"定性；与 Q3 同一张产物，非新债。
2. **records 内嵌 obs.qc + 硬性体量预算线**：单 obs qc 段 ≤8KB，越线触发降采样/外置决策（写进实作验收）；独立 evidence/ 视图为越线后迁移目标。
3. **标定治理 = 独立标定产物 calibration_v{n}.json（带指纹入 manifest），域 profile 只存引用**——derive_k 范式同族；SIM3 trust 阈值、FB3 a_max 将来同产物治理。
4. **试点三件：batch_shift + temporal_drift + glare**——B 补的 glare（binary_evidence_channel 代表）与 A 侧盘点 agent 的独立结论（CHECKS_INVENTORY.md"留白 glare、低成本第三试点"）交叉吻合，采纳。五值枚举覆盖四值，MaxFold 逐位等价锚不变。

**盘点输入（/tmp/claude-1128/dimvn1/CHECKS_INVENTORY.md，实作时的地图）**：全 13 检查映射完毕；M1（ABSENT→CLEAN 混同）实存于 6/13 检查（sentinel_band 最坏：ABSENT=零记录连 passed 都没有）；ERROR 现状两形态——4 个板级块折零静默 + **7 个检查无 try/except 直穿崩溃（docstring"每检查 try/except"名不副实）**；NOT_APPLICABLE 今天靠 is_control 分支隐式实现。字面量豁免清单分层 A（强 crystal，lint 首要豁免）/ 层 B（统计标定常数，随 Q3 决议迁标定产物，勿被 lint 冻结）。

## §6 验收探针修订（v1.1，依 S4 原型实测——/tmp/claude-1128/dimvn2/）

原型判定：**部分可行**。掩蔽实存于折叠/归因层（证据生成层完好：掩蔽孔 batch_shift 照常 score=1.0、z=-3.6、方向正确），且**单赢家（argmax top_cause）口径下不可解**——移除 dust 抑制门后裁决逐位不变（dust 签名分中位 0.919 合法压过 batch 0.641）；argmax 折叠本身才是更深的掩蔽者，精确印证 §1.1。据此探针措辞收窄为：

> S4 掩蔽场景的批次真值，在 InteractionAware fold **以通道分离裁决（per-channel verdict，非单一 argmax）消费 records 向量时**可归因：dust POSITIVE ∧ batch 亦真的共注入子群上，batch 通道恢复率 ≥0.6、方向正确率 =1.0、dust-only 孔零误报（原型实测 0.652 / 1.0 / 0，n=138/20 种子）。**验收显式排除单赢家口径**；须随附板级-孔级粒度精度代价披露（原型 12/63 clean 孔被板级真实批次位移点亮）。

新增必杀变异 **M5：argmax-only 退化**——实现若悄悄退回单赢家消费，本探针必红。收益宣传纪律：用共注入子群口径（0.000→0.652）并注明分母；全 batch 孔摊薄仅 +3pp，不得用作 headline。
