# Bio Program 2026 · 参照组 4 — 自主科学 agent + protocol 编译 + 具身湿执行（M28/M29）

> 参照波 agent · 组 4 产出。权威蓝图见 `docs/BIOLOGY_PROGRAM_2026.md`（M28/M29 行、§4 硬约束、
> §5 验证级别、§6 clone 纪律、§6.1 安全边界）。本文件只产四种判定（ADOPT / ADAPT / NOT-COPY /
> EXPOS ABSTRACTION FINDING）+ source/code status + validation level，**不是施工令**，动碼前须补 charter。
>
> **诚实第一**：本组多数引用为 2026 前沿、超本 agent 2026-01 知识截止。下表每条都经 WebSearch/WebFetch
> 独立核实存在性；机制描述仅取自检索到的官方摘要/README，未见实据者一律标 UNVERIFIED，绝不编造。

## 0. 存在性核验总表（2026-07-14 核）

| 参照 | 标识 | 核验 | 关键实据 | 公开代码 |
|---|---|---|---|---|
| **Robin** | arXiv 2505.13400 · Nature s41586-026-10652-y | ✅ VERIFIED | Nature 页面 + arXiv + FutureHouse 官方 blog 三方一致；dry AMD、ripasudil/KL001 in-vitro 确认 | ✅ `github.com/Future-House/robin`（2025-05-27 release，**已 clone**） |
| **CellVoyager** | Nature Methods s41592-026-03029-6 · bioRxiv 2025.06.03.657517 | ✅ VERIFIED | Nature Methods + bioRxiv + PubMed 41845065 一致；scRNA-seq autonomous notebook agent | ✅ `github.com/zou-group/CellVoyager`（**未 clone，属组 3 单细胞域**） |
| **ProtoPilot** | arXiv 2606.31763 | ✅ VERIFIED | arXiv HTML/PDF + MGI Tech×Shanghai AI Lab 官方新闻稿 + BioLab Bench 一致；Sanger-confirmed 湿实验产物 | ❌ 摘要/新闻稿均**未披露 repo**（只 WebFetch，未 clone） |
| **BioProVLA-Agent** | arXiv 2605.07306 | ✅ VERIFIED | arXiv abs/HTML/PDF 一致；三 agent + AugSmolVLA，真机 15 原子/6 复合/3 双臂任务 | ❌ 摘要**未披露 repo**（只 WebFetch，未 clone） |

**四条主参照全部为真实公开工作，无一幻觉。** ID 与 utm 疑虑已排除：Robin/CellVoyager 有独立 Nature/
Nature Methods 落地页 + 可访问 GitHub；ProtoPilot/BioProVLA 有可访问 arXiv 全文页 + （ProtoPilot 另有）
厂商官方新闻稿。**未见实据的下钻细节已在各条 source-code status 与文末 §7 逐项标注。**

---

## 1. Robin — 文献→假说→assay→分析→反推的多 agent 科学发现系统（M28 主参照）

**1. mechanism（取自 Nature/arXiv/FutureHouse 官方摘要 + 已 clone repo `references/robin/` 源码）**
Robin 是一个把「假说生成」与「实验数据分析」串进同一连续科学工作流的**多 agent 编排**。真实构件（源码核实）：
- **Crow / Falcon**（repo 内现称 "Literature"，经 Edison 平台）：文献检索 + 科学推理，产候选与机制假说。
- **Finch**：数据分析 agent，处理 RNA-seq / flow cytometry 等生物数据集，产 `consensus_results.csv`。
- 工作流三段（README 核实）：`experimental_assay`（生成+排序候选 assay）→ `therapeutic_candidates`
  （基于 top assay 生成+排序治疗候选）→ 可选 `data_analysis`（Finch 分析湿数据，反哺下一轮候选，产物加
  `_experimental` 后缀）。**排序用 `choix`（Bradley-Terry 成对比较）**，产 ranking CSV。
- 实证：为 dry age-related macular degeneration 提出"增强 RPE 吞噬"策略，识别并**体外确认** ripasudil、KL001。
- 官方与 Nature 均强调**人类监督不可替代**（semi-autonomous，非全自主）。

**2. exact expos analogue**
Robin 的假说→assay→候选→分析→反推环 = expos 的 `Hypothesize→Design→Build→Test→Analyse→Certify→
Learn→Redesign`。但**关键分野**：Robin 的"证据"落点是 LLM 成对排序 + narrative 文本报告（CSV/txt）；
expos 的证据落点必须是 `observation → evidence compiler → ClaimDelta → 知识指纹链`
（`expos/kernel/claims.py` 已实现 append-only 双向 supersede + 强度单调门 + insufficient 隔离 +
W3C PROV 五元组 ProvenanceSnapshot）。Robin 的多 agent 形态提供 M28 的 **agent 分工模板**；expos 提供
**它缺的可审计裁决底座**。

**3. ADOPT / ADAPT / NOT-COPY**
- **ADOPT**：多 agent 角色分解（Hypothesis / Assay-selection / Omics-analysis 三类 agent）+ 成对比较
  排序作为 proposer 内部**打分**手段（不当证据）。这与 §3 模型竞赛层 acquisition 完全同构。
- **ADAPT**：Robin 的 Finch"分析结论"在 expos 里必须降级为 **dry evidence / proposal**，只有 trusted
  observation 能 certify（§4 硬约束）。把 Robin 的每个 agent 输出**强制挂 provenance 并写 event stream**，
  而非产自由文本。data_analysis 反哺环 → expos 的 `Learn→Redesign`，但反哺必须经 claim ledger 的知识
  指纹变更，不能只改 LLM 上下文。
- **NOT-COPY**：Edison 平台耦合、`choix` 排序当"发现"、把 in-vitro 确认写进 narrative 当终局。expos 不搬
  外部平台，不让排序冒充证据，不把 simulation/LLM 分析说成 validated（§5）。

**4. EXPOS ABSTRACTION FINDING（M28 核心）**
Robin 证明"多 agent 能端到端跑发现流"，但其**证据层是叙事而非账本**——这正是 expos 的差异化护城河。
M28 的正确抽象：**agent 只做 proposer/analyzer，产 `observation` 或 `dry-evidence` 记录；每个结论必经
`ClaimDelta` 落 claim ledger 并改知识指纹；contradiction/replication 是账本上的 event（supersede /
insufficient），不是 agent 的口头裁决。** kernel/evidence-compiler/knowledge-compiler 全程生物盲。

**5. source-code status**：✅ 公开 `github.com/Future-House/robin`（Apache/MIT 系，见 repo LICENSE），
**已 clone 到 `references/robin/`**（references/ 已 gitignore，非仓根）。源码核实了 Crow/Falcon/Finch、
choix 排序、三段工作流；`analyses.py` / `assays.py` / `candidates.py` / `multitrajectory_runner.py` 可读。
data_analysis 段依赖 Edison 平台（需付费 key），假说/实验生成段可离线跑。

**6. validation level**：**prospective wet lab**（体外 assay 真做，ripasudil/KL001 in-vitro 确认）。
但整体系统对外定性为 **semi-autonomous**（人类监督在环）——**非 physical autonomous loop**。

---

## 2. CellVoyager — 自主 scRNA-seq 分析 agent（属组 3 单细胞域，此处仅存在性核验 + M28 接口）

**核验**：✅ VERIFIED（Nature Methods s41592-026-03029-6 / bioRxiv 2025.06.03.657517 / PubMed 41845065）。
在 Jupyter 内自主生成并执行 scRNA-seq 分析，比 GPT-4o / o3-mini 高出至多 23% 预测"作者最终做了哪些分析"；
COVID-19 / cell-cell communication / aging 三案例产被专家评为有创意且科学合理的**新发现**。附 CellBench 评测。

**M28 接口（不深挖，交组 3）**：CellVoyager = M28 的 **Omics-analysis agent** 具体实现候选之一。expos
纪律同 Robin：它产的"新发现"在 expos 里是 **proposal / dry-evidence**，须落 claim ledger 才成 claim；
notebook 自主执行的可复现性靠 expos 的 provenance/determinism 门约束，而非信任 LLM 叙事。
**source-code**：✅ `github.com/zou-group/CellVoyager`（有 CellBench 评测代码）；**validation level：
retrospective data**（对公开 scRNA-seq 数据集的回溯分析，无新湿实验）。**未 clone**（域归组 3，避免重复）。

---

## 3. ProtoPilot — 自演化 agentic 生物 protocol 生成+执行（M29 主参照 · 编译侧）

**1. mechanism（取自 arXiv 2606.31763 摘要 + MGI×Shanghai AI Lab 官方新闻稿；层内细粒度未见全文，见 §7）**
自演化多 agent 系统，把「生物意图」编译为可执行、逐层可验证的 protocol 并执行：
- **Orchestrator Agent** 维护 workflow state；专职 agent 分别：生成 protocol、扩展 SOP、把步骤映射到设备、
  合成 **SDK-gated code**、校验中间产物、吸收 wet-lab 反馈修订工作流。
- **layer-wise verifiability**（分层可验证）+ **runtime-updated skill library**（运行时更新技能库 = 自演化）。
- **device-level validity gates**：协议→代码门通过率 96.6%，Opentrons 门 88.2%（对比基线 OpenTrons-AI 32.4%）；
  Top@3 专家偏好率 90.2%。
- **BioLab Bench**：294 合成生物/分子生物任务，源自 98 条 gold-standard protocol + 专家 rubric + 设备级
  有效性门 + 真实实验测试。

**2. exact expos analogue**
ProtoPilot 的「intent → SOP → 定量步骤 → 设备约束 → SDK code → wet feedback」分层链 = expos **已有**的
`expos/protocol/spec.py`（声明式 `ProtocolSpec`，LOUD 校验，`ALLOWED_OPS`）→ `expos/protocol/compiler.py`
（**确定性** `compile(spec)` 产 build target + `protocol_fingerprint = sha256(canonical_json(spec) ||
compiler-source-sha)`）→ `expos/adapters/wet/ot_protocol.py`（`compile_and_validate` 用**真 Opentrons stack**
做 device-validity gate，超量程/deck 冲突/缺 labware LOUD reject）。ProtoPilot 的 "device-level validity gate"
= expos 的 ot_protocol 门；"SDK-gated code" = expos 的 DryJobPlan/WetProtocolPlan 双 build target。

**3. ADOPT / ADAPT / NOT-COPY**
- **ADOPT**：**分层可验证**理念（每层有独立 gate，逐层放行）——与 expos "写严读容 + LOUD reject" 同魂；
  device-level validity gate 作为编译期硬门（expos 已有，可强化到覆盖 quantitative-step 层）。
- **ADAPT**：ProtoPilot 的 "runtime-updated skill library"（自演化）→ expos 里必须是**可审计、版本入
  fingerprint** 的技能条目（每次 skill 更新是 event，进 provenance），不能是隐式可变状态（否则违反
  determinism 门 K5 与 §4 "版本一律入 fingerprint"）。SOP 扩展 / 设备映射作为 compiler 的**中间层**，每层
  产物挂 `protocol_fingerprint` 血缘。
- **NOT-COPY**：把 LLM 生成的 protocol 直接当"已验证"（expos 要求过 compiler + device gate 才算 typed-valid）；
  把 90.2% 专家偏好率当正确性证据（那是 proposer 打分，非 trusted observation）；直接搬 ProtoPilot 框架
  （§4 no-framework-transplant，须先有 discriminative expos 测试）。

**4. EXPOS ABSTRACTION FINDING（M29 编译侧）**
ProtoPilot 验证"biological intent → typed protocol → compiler-verified device code"是可工程化的，且**分层门
是关键**。expos 的正确抽象：**M29 不新建编译器，而是把 `expos/protocol/compiler.py` 从化学两 op
（dry_compute/wet_assay）扩到生物 protocol 的 typed step 集，复用其确定性 + 双 build target + fingerprint 锚**；
LLM 只当 proposer 产 candidate spec，**真正的"验证"是 compiler + Opentrons device gate 这条确定性通道**，
不是 LLM 自评。skill library 若引入，必须是版本化 event，进指纹。

**5. source-code status**：❌ **代码未公开**——arXiv 摘要与 MGI/Shanghai AI Lab 新闻稿均未给 repo（BioLab Bench
是否随附亦未见披露）。仅 WebFetch 摘要级信息，**未 clone**。层内 agent 精确接口/skill 演化机制 = **UNVERIFIED
（未见全文）**，见 §7。

**6. validation level**：**prospective wet lab**（摘要明述 Sanger-confirmed 产物 + feedback-corrected
PCA-assembled DNA 靶标，真湿实验）。注意：这是**其团队的**真机结果；对 expos 无迁移含义，expos 生物真机仍 ❌ pending。

---

## 4. BioProVLA-Agent — protocol 解析 + 视觉状态验证 + VLA 具身执行（M29 主参照 · 执行侧）

**1. mechanism（取自 arXiv 2605.07306 摘要；具体状态传递协议未见全文，见 §7）**
以 **protocol 为任务接口**的具身多 agent，闭环整合"协议解析 + 视觉状态验证 + 具身执行"，专治湿实验视觉难点：
- **Tailored LLM Protocol Agent**：把非结构化 protocol 转成**可执行且可验证的子任务单元**。
- **VLM-RAG Verification Agent**：对实时视觉观测 + 机器人状态 + 检索到的操作知识 + 成功/失败范例推理，判定
  子任务的 **readiness（就绪）与 completion（完成）**——即**执行前后的视觉门**，未验证不放行。
- **VLA Embodied Agent**：用轻量 VLA policy 执行已验证子任务。
- **AugSmolVLA**：针对透明器皿、镜面反光、光照漂移、过曝的在线数据增强。
- 评测：15 原子 / 6 复合 / 3 双臂任务（上管、分拣、废弃、拧盖、倾倒），对比 ACT / X-VLA / SmolVLA 基线。

**2. exact expos analogue**
BioProVLA 的 "readiness/completion 视觉门" = expos M23 的 **sensed-state gate**：`action_ledger.py` 里
COMMITTED **只能**由 sensed-state 确认门控（driver OK 回复绝不单独 COMMIT）。其 "protocol → 可验证子任务
单元" = expos 的 `ProtocolSpec → compiler → 逐 action`。其 "closed-loop 按观测状态而非盲序执行" = expos
`WetDriver` 七态 + `AWAITING_RECOVERY` + `RecoveryPolicy`（sensed-mismatch 走 rollback/await-human）。

**3. ADOPT / ADAPT / NOT-COPY**
- **ADOPT**：**"感知态验证是执行门，不是事后日志"** 这一硬原则——expos M23 已把它做成 COMMITTED 的唯一门控，
  BioProVLA 从视觉侧独立佐证该设计正确。readiness+completion **双时点门**（前置就绪 + 后置完成）可 ADOPT 进
  M29 的 physical-commit 前后检查。
- **ADAPT**：VLM-RAG 的"检索成功/失败范例"→ expos 里应是**可审计的 recovery 先例库**（挂 provenance），而非
  隐式 RAG 上下文。视觉 readiness 判定结果须落 **event（sensed-state 记录）**，进 action_ledger 的哈希链，
  才能满足防重放 + 可 resume。透明器皿/反光是**真机才有**的问题——M29 第一台真机建议 plate reader/简单 liquid
  handler（读数/移液，视觉扰动小），**不上双臂具身 lab**，故 AugSmolVLA 暂列 NOT-YET。
- **NOT-COPY**：直接上双臂 VLA 具身（§M29 明令第一台从 plate reader 起）；把 VLA policy 的执行成功当生物证据
  （执行成功 ≠ 生物 claim，须经 assay observation certify）；真机前称 autonomous（§5）。
- **NOT-YET**：AugSmolVLA、透明器皿视觉增强、双臂操作——待真机域成熟再评。

**4. EXPOS ABSTRACTION FINDING（M29 执行侧）**
BioProVLA 从**视觉/具身侧独立收敛到 expos M23 已选的架构**：sensed-state 是执行门，闭环按观测态而非盲序。
这给 expos 极强信心——**M29 执行侧不需新范式，只需把 M23 的 fake-backend 事务面接到真设备的 sensed-state
读回（plate reader 读数 / liquid handler 液位）上**。抽象：`sensed-state confirmation` 是设备无关接口，
无论来自视觉 VLM 还是读数器，都以同一 event kind 进 action_ledger 门控 COMMITTED。

**5. source-code status**：❌ **代码未公开**（arXiv 摘要无 repo 链接）。仅 WebFetch 摘要，**未 clone**。
三 agent 间精确状态传递协议、VLA policy 权重、AugSmolVLA 实现 = **UNVERIFIED（未见全文）**，见 §7。

**6. validation level**：**physical autonomous loop（其团队真机）**——真机器人硬件跑 15/6/3 任务。但这是
**操作层**具身，非生物发现闭环；且对 expos 无迁移（expos 生物真机 ❌ pending）。

---

## 5. M28 关键 finding — 多 agent 如何落 expos ledger（比 Robin/CellVoyager 更严）

**核心裁决：agent 产 proposal/observation/dry-evidence，从不产 claim；claim 只由 ledger 经 trusted
observation 生成。** 六 agent 到 expos 物件的映射（kernel 全程生物盲，语义只活在 domain/adapter/QC）：

| M28 agent | 职能 | expos 落点（硬约束）|
|---|---|---|
| **Hypothesis agent** | 文献 grounded 产机制假说 | 产 `proposal`（进 proposer 层），**不入 claim**；假说文本 + 文献引用挂 provenance |
| **Assay-selection agent** | 选实验/observable | 产 `experiment design` + acquisition 打分（§3 层）；选择理由入 event |
| **Omics agent**（Finch/CellVoyager 类）| RNA-seq/flow/imaging/单细胞分析 | 产 **dry-evidence**（分析结论），**降级为 proposal**，非 observation；分析脚本 + 数据集版本入 fingerprint |
| **Contradiction agent** | 检测矛盾 | 触发 `ClaimDelta` 的 **supersede**（强度单调门：弱证据不得撤强结论，`claims.py` 已实现）|
| **Replication agent** | 复现校验 | 产**独立** biological replicate observation；技术副本**绝不冒充**独立生物证据（§4，M24-B 塌缩件已立此机制）|
| **Mechanism agent** | 机制解释 | 产 mechanism-activity event（已是 kernel 一等事件，靠 ">=2 consumers" 门晋级）；机制是 claim 的注解，非替代证据 |

**四条 finding 落地要点：**
1. **每个 agent 结论必落 `observation`/`ClaimDelta`，带 W3C PROV 五元组**（usage 输入 obs id + content
   fingerprint + consumed knowledge fingerprint；activity 决策 fn id/版本 + run fingerprint；statistic
   自足统计/power）——第三方能从 event stream 独立重算裁决（`claims.py` K4）。**LLM 绝不直接写 narrative claim。**
2. **contradiction/replication 是账本 event，不是 agent 口头裁决**：矛盾 → supersede（append-only 双向链 +
   强度门）；不足 → insufficient（type 级隔离，结构上不能携带新 claim 版本，K3 absence≠support）。
3. **Omics/Hypothesis agent 的"发现"是 proposal**；只有 trusted observation 能 certify。公开数据可训练/校准，
   **绝不当本 run 观测**（§4）。
4. **determinism**：`apply_claim_deltas` 是纯函数（无 I/O/时钟/随机，显式 tie-break 排序），同批同起点账本
   逐 bit 可复现（K5）——多 agent 并发产 delta 时，这条是防"LLM 非确定性污染证据链"的护栏。

**expos 现成底座**：`expos/agent/`（llm_backend / policy / backend_select，已生物盲）+ `expos/kernel/claims.py`
（ClaimDelta / ProvenanceSnapshot / 双向 supersede / 强度门 / insufficient 隔离）+ `expos/mcl.py`（编排）。
M28 主要是**编排这些 agent 并强制其输出过 claims.py 的门**，而非新建证据机制。

---

## 6. M29 关键 finding — 复用 M23 的接口点（不重造事务面）

**核心裁决：M29 = biological intent → typed protocol → compiler 验证 → device code → sensed-state 验证 →
physical commit，其中后三段几乎全是 M23 已成件。** 复用接口点（源码坐标）：

| M29 阶段 | 复用的 M23/已有件（源码）| 接口点 |
|---|---|---|
| intent → typed protocol | `expos/protocol/spec.py`（`ProtocolSpec`，LOUD 校验，PROMOTION_RULE）| 扩 `ALLOWED_OPS` 到生物 typed step；LLM 只产 candidate spec |
| protocol → compiler 验证 | `expos/protocol/compiler.py`（确定性 compile + `protocol_fingerprint`）| 同一 fingerprint 锚 `sha256(canonical_json(spec) \|\| compiler-source-sha)`；双 build target |
| → device code / device gate | `expos/adapters/wet/ot_protocol.py`（真 Opentrons `compile_and_validate`）| 超量程/deck 冲突/缺 labware LOUD reject = device-level validity gate |
| dispatch（crash-visible）| `action_ledger.py` 六态：PLANNED→**PENDING（落盘于硬件 I/O 之前）**| 幂等键 `action_id`；resumed ledger **loudly refuse 重发**已录 action（防重放）|
| sensed-state 验证 → commit | `action_ledger.py`：**COMMITTED 只由 sensed-state 确认门控**（driver OK 绝不单独 commit）| **接真设备的读回**（plate reader 读数 / liquid handler 液位）为 sensed confirmation |
| mismatch / recovery | `driver.py` 七态 + `AWAITING_RECOVERY` + `recovery.py` `RecoveryPolicy`（ABORT/AWAIT_HUMAN）| sensed-mismatch → rollback 或 await-human |
| 体积/守恒 | `action_ledger.py` 双式记账（source −v / dest +v 和为零）+ 五前置拒绝 | observed 与 requested **分字段存**（写严，不覆盖请求）|
| 台账不可变 | 每行哈希链 + seq 单调 append-only jsonl | 篡改/截断被检出；resume 重放 |

**M29 落地要点：**
1. **不新建事务面**：M23 的六态 action transaction + 七态 driver + sensed-state gate + 哈希链 + 防重放 +
   双式体积记账**全部复用**；M29 只需把 fake_physical backend 换成真设备驱动，且**真设备的 sensed-state 读回
   接到同一 COMMITTED 门**。BioProVLA 从视觉侧、ProtoPilot 从编译侧独立佐证此架构正确。
2. **第一台真机 = plate reader / 简单 liquid handler**（§M29 明令），不上双臂具身。sensed-state = 读数器读回
   / 液位传感，视觉扰动小，AugSmolVLA 类视觉增强列 NOT-YET。
3. **§6.1 安全边界为 M29 charter 强制项**：真机/真湿执行须待生物安全边界成文；只借鉴安全的编译/验证/执行架构，
   不涉增强致病性/受管制病原体（本组 ProtoPilot/BioProVLA 借鉴限于**协议编译与事务安全**，非序列设计）。
4. **§5 诚实**：真机跑通前，expos 生物侧**绝不称 physical autonomous**；当前真实级别仍 = simulation（对 fake
   physical backend 事务安全，M23 已成），真硬件 pending ❌。ProtoPilot/BioProVLA 的真机结果是**其团队的**，
   对 expos 无迁移含义。

---

## 7. 诚实标注 — 查无实据 / 未见全文的细节（UNVERIFIED）

以下项**存在性已证**（论文/系统为真），但**下钻机制细节未见全文实据**，故标 UNVERIFIED，未据此产任何架构结论：

- **ProtoPilot**：Orchestrator 与各专职 agent 的**精确接口/消息协议**、skill library 的**具体自演化算法**、
  层间 gate 的**判定规则细节** = UNVERIFIED（仅 arXiv 摘要 + 厂商新闻稿；未取全文）。BioLab Bench 是否随代码
  公开 = 未见披露。**代码 repo = 未公开（未 clone）。**
- **BioProVLA-Agent**：三 agent 间**精确状态传递协议**、VLA policy 权重与训练细节、AugSmolVLA 实现、
  VLM-RAG 检索库构造 = UNVERIFIED（仅 arXiv 摘要）。**代码 repo = 未公开（未 clone）。**
- **Robin**：机制描述经 Nature/arXiv/FutureHouse 三方 + 已 clone 源码交叉核实，**基本 VERIFIED**；论文中定量
  性能指标未逐一复核（不影响架构判定）。
- **CellVoyager**：存在性 VERIFIED；细节归组 3，本组只做 M28 接口定位，未深挖。
- **通用**：所有"性能率"（ProtoPilot 90.2%/96.6%/88.2%、Robin/BioProVLA 各指标）均取自摘要，**本 agent 未
  独立验证**，且按 §4 一律视为 proposer 打分，**不作为正确性/生物证据**。

**无一条参照为幻觉**：四条主参照 + CellVoyager 全部有可访问的官方落地页（Nature/Nature Methods/arXiv 全文/
厂商新闻稿/可 clone GitHub）。带 ID 疑虑（2606.31763 / 2605.07306 超本 agent 知识截止）者已经 WebFetch 打开
arXiv 页面确认真实存在，非编造。

---

## 8. clone 状态与纪律

- ✅ **已 clone**：`references/robin/`（`github.com/Future-House/robin`，`cd references && pwd` 确认后 clone，
  非仓根，references/ 已 gitignore）。理由：M28 主参照、真实公开、多 agent 结构值得读源。
- ⏸ **未 clone（属组 3 域）**：CellVoyager（`github.com/zou-group/CellVoyager`）——单细胞分析归组 3，避免重复。
- ❌ **无 repo 可 clone**：ProtoPilot、BioProVLA-Agent（代码均未公开，仅 WebFetch 摘要）。
- **纪律遵守**：未改 expos/ 源码、未 commit/push、未搬任何外部框架进主仓（§4 no-framework-transplant）；
  借鉴限安全的 protocol 编译/验证/执行架构，不涉双重用途敏感设计（§6.1）。
